"""One mesh drawn by Anno 117's own shaders on QRhi (Direct3D 12): the binding layout from the compiled shaders'
reflection, the engine's buffers filled by name, the material's maps in texture arrays as they are stored (BC blocks
uploaded unchanged), a pipeline per target.

The maps' arrays are in ``gametextures`` and the pipelines (``CPipelineUser``, ``new_pipeline``) in ``gamepipeline``.
``CProgram(vertex_stage, pixel_stage, geometry_stage=None)`` takes the compiled ``SStage``s; ``CGameModel(rhi, program, mesh, material)`` is the draw:
``prepare(render_pass, signature, batch, camera)`` before a pass, ``draw(command_buffer, size_px)`` inside it (``gameframe`` runs both).
The rules: docs/tool-suite.md section 21, "The game-shader path".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhiBuffer,
    QRhiSampler,
    QRhiShaderResourceBinding,
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
    QRhiVertexInputAttribute,
    QRhiViewport,
    QShader,
    QShaderCode,
    QShaderKey,
    QShaderVersion,
)

from ..models.fxstage import SSampler, sampler_named
from . import gamepool, gameshader, mipchain
from .gamepipeline import CPipelineUser
from .gametextures import BLOCK_FORMATS, CTextureTable, stored_levels
from .renderstates import GBUFFER_STATE, SPassState

Binding = QRhiShaderResourceBinding
TextureFormat = QRhiTexture.Format
# an effect's address mode words -> QRhi's (the engine's fixed samplers make the same record: ``sampler_named``)
_ADDRESS = {"WRAP": QRhiSampler.AddressMode.Repeat, "CLAMP": QRhiSampler.AddressMode.ClampToEdge,
            "MIRROR": QRhiSampler.AddressMode.Mirror, "BORDER": QRhiSampler.AddressMode.ClampToEdge}  # fmt: skip


# 117's depth is reversed (1 near, 0 far), so a comparison sampler passes where the reference is the greater
REVERSED_COMPARE = QRhiSampler.CompareOp.GreaterOrEqual


def sampler_of(rhi, sampler_state: SSampler, compare=QRhiSampler.CompareOp.LessOrEqual) -> QRhiSampler:
    """A QRhi sampler for a sampler state (QRhi has no anisotropy: linear with mips stands in). Point filtering
    keeps a linear mip filter unless the state says ``MIP_POINT``; ``compare`` is the test of a comparison sampler."""
    point = "POINT" in sampler_state.filter and "ANISOTROPIC" not in sampler_state.filter
    filter_mode = QRhiSampler.Filter.Nearest if point else QRhiSampler.Filter.Linear
    mip = QRhiSampler.Filter.Nearest if "MIP_POINT" in sampler_state.filter else QRhiSampler.Filter.Linear
    modes = [
        _ADDRESS.get(address, _ADDRESS["WRAP"])
        for address in (sampler_state.address_u, sampler_state.address_v, sampler_state.address_w)
    ]
    sampler = rhi.newSampler(filter_mode, filter_mode, mip, modes[0], modes[1])
    sampler.setAddressW(modes[2])
    if sampler_state.compare:
        sampler.setTextureCompareOp(compare)
    sampler.create()
    return sampler


def fixed_sampler(rhi, name: str, default: str = "WRAP") -> QRhiSampler:
    """One of 117's fixed samplers by the words of its name (``sampler_named``), its depth comparison reversed."""
    return sampler_of(rhi, sampler_named(name, default), REVERSED_COMPARE)


@dataclass
class SStage:
    """A compiled shader: its DXIL, entry point and reflection (``infra``'s ``Reflection``)."""

    blob: bytes
    entry: str
    reflection: object
    inputs: list = field(default_factory=list)  # the vertex stage's ``gameshader.SInput``s
    types: dict = field(default_factory=dict)  # a texture's declared type by name (``gameshader.declared_types``)
    state: dict = field(default_factory=dict)  # its pass's render states (``fxeffect.SEffect.state_of``)


def is_dxil(blob: bytes) -> bool:
    """Whether a compiled shader is DXIL (DXC, Direct3D 12) rather than DXBC (FXC, Direct3D 10 and 11): both come in
    a DXBC container, DXIL has a part of that name."""
    return blob[:4] == b"DXBC" and b"DXIL" in blob[32 : 32 + 4 * 64 + 4096]


def native_shader(stage: QShader.Stage, blob: bytes, entry: str, binding_map: dict[int, tuple[int, int]]) -> QShader:
    """A ``QShader`` holding a game's compiled shader as it is - DXIL for the Direct3D 12 backend, DXBC for the
    Direct3D 11 one - with the QRhi binding -> register map of its resources."""
    shader = QShader()
    shader.setStage(stage)
    dxil = is_dxil(blob)
    source = QShader.Source.DxilShader if dxil else QShader.Source.DxbcShader
    for version in (60, 62, 66, 67) if dxil else (40, 41, 50, 51):  # the backends look a shader up by model
        key = QShaderKey(source, QShaderVersion(version))
        shader.setShader(key, QShaderCode(blob, entry.encode()))
        shader.setResourceBindingMap(key, binding_map)
    return shader


def uniform_buffer(rhi, size_bytes: int) -> QRhiBuffer:
    """A dynamic uniform buffer for a constant buffer of ``size`` bytes, rounded up to the 256 the shaders bind at."""
    buffer = rhi.newBuffer(
        QRhiBuffer.Type.Dynamic, QRhiBuffer.UsageFlag.UniformBuffer, (max(16, size_bytes) + 255) // 256 * 256
    )
    buffer.create()
    return buffer


def stand_in(rhi, kind: str, declared: str = "", batch=None, value: float = 0.0):
    """A resource for a binding no pass of the frame makes: a raw buffer, a sampler, or a 1 x 1 texture of the
    declared type (``Texture2D``, ``TextureCube``, ``Texture2DArray``, ``Texture3D``; ``<uint>`` integer; ``RW...`` a
    storage texture). With ``batch`` a buffer is zeroed and a sampled texture filled with ``value`` (an integer
    texture with 0); without it the contents are whatever the backend gives."""
    if kind in ("byteaddress", "uav_rwbyteaddress"):
        resource = rhi.newBuffer(QRhiBuffer.Type.Static, QRhiBuffer.UsageFlag.StorageBuffer, 4096)
        layers, data = 0, bytes(4096)
    elif kind == "sampler":
        linear, clamp = QRhiSampler.Filter.Linear, QRhiSampler.AddressMode.ClampToEdge
        resource = rhi.newSampler(linear, linear, linear, clamp, clamp)
        layers, data = 0, b""
    else:
        flags = QRhiTexture.Flag.UsedWithLoadStore if kind != "texture" else QRhiTexture.Flag(0)
        integer = "<uint" in declared or "<int" in declared
        texture_format = TextureFormat.R32UI if integer else TextureFormat.RGBA16F
        data = bytes(4) if integer else np.full(4, value, np.float16).tobytes()
        bare = declared.removeprefix("RW")
        layers = 1
        if bare.startswith("TextureCube"):
            resource, layers = rhi.newTexture(texture_format, QSize(1, 1), 1, flags | QRhiTexture.Flag.CubeMap), 6
        elif bare.startswith("Texture2DArray"):
            resource, layers = rhi.newTextureArray(texture_format, 2, QSize(1, 1), 1, flags), 2
        elif bare.startswith("Texture3D"):
            resource = rhi.newTexture(texture_format, 1, 1, 1, 1, flags | QRhiTexture.Flag.ThreeDimensional)
        else:
            resource = rhi.newTexture(texture_format, QSize(1, 1), 1, flags)
        if kind != "texture":
            layers = 0  # a storage texture is written by the shader
    resource.create()
    if batch is not None and kind in ("byteaddress", "uav_rwbyteaddress"):
        batch.uploadStaticBuffer(resource, data)
    elif batch is not None and layers:
        subresource = QRhiTextureSubresourceUploadDescription(data)
        upload = QRhiTextureUploadDescription()
        upload.setEntries([QRhiTextureUploadEntry(k, 0, subresource) for k in range(layers)])
        batch.uploadTexture(resource, upload)
    return resource


def _binding_key(binding, buffers: dict[str, str]) -> tuple[str, str]:
    """A stage's binding as ``CProgram`` numbers it: (name, kind), a constant buffer under its key in ``buffers``."""
    name = buffers.get(binding.name, binding.name) if binding.kind == "cbuffer" else binding.name
    return name, binding.kind


class CProgram:
    """A vertex and pixel stage (and an optional geometry stage, or a hull and domain stage
    the terrain's quad patches) with one QRhi binding number per resource name, shared by the stages."""

    def __init__(self, vertex_stage: SStage, pixel_stage: SStage, geometry_stage: SStage | None = None, hull_stage: SStage | None = None,
                 domain_stage: SStage | None = None) -> None:  # fmt: skip
        self.m_vertex_stage, self.m_pixel_stage, self.m_geometry_stage, self.m_hull_stage, self.m_domain_stage = (
            vertex_stage,
            pixel_stage,
            geometry_stage,
            hull_stage,
            domain_stage,
        )
        self.m_numbers: dict[tuple[str, str], int] = {}
        self.m_stages: dict[tuple[str, str], Binding.StageFlag] = {}
        staged = [(Binding.StageFlag.VertexStage, vertex_stage), (Binding.StageFlag.FragmentStage, pixel_stage)]
        staged += [(Binding.StageFlag.GeometryStage, geometry_stage)] if geometry_stage is not None else []
        if hull_stage is not None and domain_stage is not None:
            staged += [
                (Binding.StageFlag.TessellationControlStage, hull_stage),
                (Binding.StageFlag.TessellationEvaluationStage, domain_stage),
            ]
        names = self._buffer_names([shader_stage for _flag, shader_stage in staged])
        for (flag, stage), own in zip(staged, names, strict=True):
            for binding in stage.reflection.bindings:
                key = _binding_key(binding, own)
                self.m_numbers.setdefault(key, len(self.m_numbers))
                self.m_stages[key] = self.m_stages.get(key, Binding.StageFlag(0)) | flag
        maps = [{self.m_numbers[_binding_key(binding, own)]: (binding.register, -1) for binding in shader_stage.reflection.bindings}
                for (_flag, shader_stage), own in zip(staged, names, strict=True)]  # fmt: skip
        if not is_dxil(vertex_stage.blob):
            self._pad_samplers(maps)
        self.m_shaders = (native_shader(QShader.Stage.VertexStage, vertex_stage.blob, vertex_stage.entry, maps[0]),
                        native_shader(QShader.Stage.FragmentStage, pixel_stage.blob, pixel_stage.entry, maps[1]))  # fmt: skip
        self.m_geometry = (
            native_shader(QShader.Stage.GeometryStage, geometry_stage.blob, geometry_stage.entry, maps[2])
            if geometry_stage
            else None
        )
        self.m_patches = None  # the hull and domain shader of a tessellated program
        if hull_stage is not None and domain_stage is not None:
            self.m_patches = (native_shader(QShader.Stage.TessellationControlStage, hull_stage.blob, hull_stage.entry, maps[-2]),
                            native_shader(QShader.Stage.TessellationEvaluationStage, domain_stage.blob, domain_stage.entry, maps[-1]))  # fmt: skip
        self.m_cbuffers = {
            own[cbuffer.name]: cbuffer
            for (_flag, shader_stage), own in zip(staged, names, strict=True)
            for cbuffer in shader_stage.reflection.cbuffers
        }
        self.m_pipelines: dict[tuple, list] = {}  # its pipelines by pass and layout: [pipeline, models using it]

    def release(self) -> None:
        """Destroy the pipelines its models did not give back: an owner that lets its models go by their resources
        alone, a frame later, counts no user down (the terrain bake's)."""
        for pipeline, _users in self.m_pipelines.values():
            pipeline.destroy()
        self.m_pipelines = {}

    @staticmethod
    def _buffer_names(stages: list) -> list[dict[str, str]]:
        """Per stage its constant buffers' keys: the name, or ``name@<stage>`` for a buffer an earlier stage names
        with another layout (each Direct3D 9 effect's stage has its own ``$Globals``: 1701's)."""
        seen: dict[str, object] = {}
        out = []
        for k, stage in enumerate(stages):
            own = {}
            for cbuffer in stage.reflection.cbuffers:
                layout = (cbuffer.size, [(variable.name, variable.offset) for variable in cbuffer.variables])
                key = cbuffer.name if seen.get(cbuffer.name, layout) == layout else f"{cbuffer.name}@{k}"
                seen.setdefault(cbuffer.name, layout)
                own[cbuffer.name] = key
            out.append(own)
        return out

    def _pad_samplers(self, maps: list[dict]) -> None:
        """A stand-in sampler (register 0) for a DXBC stage that reads textures without a sampler (``Load``, as
        2205's vertex shader reads its matrices): QRhi's Direct3D 11 backend binds a stage's textures only when it
        also binds a sampler there (seen with PySide6 6.12; shader-compile.md section 11)."""
        for k, (flag, stage) in enumerate(
            (
                (Binding.StageFlag.VertexStage, self.m_vertex_stage),
                (Binding.StageFlag.FragmentStage, self.m_pixel_stage),
            )
        ):
            kinds = {binding.kind for binding in stage.reflection.bindings}
            if "texture" in kinds and "sampler" not in kinds:
                key = (f"<{'vertex' if k == 0 else 'pixel'} stage sampler>", "sampler")
                self.m_numbers[key] = len(self.m_numbers)
                self.m_stages[key] = flag
                maps[k][self.m_numbers[key]] = (0, -1)


# a vertex input's semantic -> the mesh element it reads (the rdm's usages PNGBTCWI, docs/formats/rdm.md)
SEMANTICS = {"SV_POSITION": 0, "POSITION": 0, "NORMAL": 1, "TANGENT": 2, "BINORMAL": 3, "TEXCOORD": 4, "COLOR": 5,
             "BLENDWEIGHT": 6, "BLENDINDICES": 7}  # fmt: skip
_AttributeFormat = QRhiVertexInputAttribute.Format
# an element's (type, count) -> how QRhi feeds it: halves, floats, a packed four-byte vector as unsigned bytes,
# four u16 as integers (the terrain's NuggetsVertex position, ``terraindraw``), four u32 (2070's cloth tangents,
# ``cloth2070``)
ATTRIBUTES = {
    (6, 2): _AttributeFormat.Half2,
    (6, 3): _AttributeFormat.Half3,
    (6, 4): _AttributeFormat.Half4,
    (7, 1): _AttributeFormat.Float,
    (7, 2): _AttributeFormat.Float2,
    (7, 3): _AttributeFormat.Float3,
    (7, 4): _AttributeFormat.Float4,
    (5, 1): _AttributeFormat.UNormByte4,
    (2, 4): _AttributeFormat.UShort4,
    (4, 1): _AttributeFormat.UInt,
    (4, 4): _AttributeFormat.UInt4,
}


@dataclass
class CMesh:
    """A mesh's vertices as the file stores them (``data`` at ``stride``; ``elements`` (usage, type, count, offset)
    in order) and the triangles one draw takes; the engine binds the stored vertices the same way."""

    m_data: bytes
    m_stride: int
    m_elements: tuple
    m_indices: np.ndarray
    m_count: int
    m_version: int = 0  # which vertices it holds (a moving cloth's frame): a device copy of another uploads again
    m_rest: bytes = b""  # the vertices as first made, once they changed
    m_box: tuple | None = None  # ``box``, kept: a native part lets its vertices' bytes go once they are on the device

    def set_vertices(self, data: bytes, version: int) -> None:
        """New vertices in the same layout (``render.clothmesh``), known as ``version``."""
        if not self.m_rest:
            self.m_rest = self.m_data
        self.m_data, self.m_version, self.m_box = data, version, None

    @classmethod
    def of_rdm(cls, mesh, indices) -> CMesh:
        """An ``formats.rdmread.SMesh`` with the indices of one draw (a submesh group)."""
        offset_bytes, out = 0, []
        for element in mesh.elements:
            out.append((element.usage, element.type, element.count, offset_bytes))
            offset_bytes += element.size
        return cls(mesh.vertices, mesh.stride, tuple(out), np.asarray(indices, np.uint32), mesh.vertex_count)

    def positions(self) -> np.ndarray:
        """[n, 3] floats of the first position element."""
        usage, kind, count, offset_bytes = next(element for element in self.m_elements if element[0] == 0)
        size = count * (2 if kind == 6 else 4)
        raw = np.frombuffer(self.m_data, np.uint8).reshape(self.m_count, self.m_stride)[
            :, offset_bytes : offset_bytes + size
        ]
        return np.ascontiguousarray(raw).view("<f2" if kind == 6 else "<f4")[:, :3].astype(np.float32)

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        """The corners (min, max) of the box the positions span (worked out once while the vertices are here)."""
        if self.m_box is None:
            positions = self.positions()
            self.m_box = (positions.min(axis=0), positions.max(axis=0))
        return self.m_box

    def draw(self, command_buffer, pipeline, resource_bindings, area: tuple, buffers: tuple, instances: int = 1,
             dynamic_offset: tuple[int, int] | None = None, state: SPassState | None = None) -> None:  # fmt: skip
        """Its triangles drawn by ``pipeline`` with ``srb`` (``buffers``: the vertex buffers - the stored vertices
        and the per-vertex constants - and the index buffer) over the viewport ``area`` (the target's width and
        height, or a part of it: ``share_rect``); ``dynamic_offset``: (binding, bytes) of the binding set's one buffer bound
        with a dynamic offset; ``state``: the pipeline's, for its stencil reference."""
        vbufs, ibuf = buffers
        command_buffer.setGraphicsPipeline(pipeline)
        if state is not None and state.stencil is not None:
            command_buffer.setStencilRef(state.stencil.ref)
        command_buffer.setViewport(QRhiViewport(*area) if len(area) == 4 else QRhiViewport(0, 0, *area))
        if dynamic_offset is None:
            command_buffer.setShaderResources(resource_bindings)
        else:
            command_buffer.setShaderResources(resource_bindings, 1, dynamic_offset)
        command_buffer.setVertexInput(
            0, [(vertex_buffer, 0) for vertex_buffer in vbufs], ibuf, 0, command_buffer.IndexFormat.IndexUInt32
        )
        command_buffer.drawIndexed(len(self.m_indices), instances)

    def bone_count(self, per_vertex: int) -> int:
        """How many bone matrices the vertices reach (the highest index + 1; 0 for a mesh without a skin)."""
        found = [element for element in self.m_elements if element[0] == 7]
        if not per_vertex or not found or not self.m_count:
            return 0
        raw = np.frombuffer(self.m_data, np.uint8).reshape(self.m_count, self.m_stride)
        offset_bytes = found[0][3]
        return int(raw[:, offset_bytes : offset_bytes + (4 if per_vertex > 1 else 1)].max()) + 1


def share_rect(size_px: tuple[int, int], share: tuple) -> tuple:
    """A part of a target of ``size`` as a viewport (x, y, width, height; QRhi's starts at the bottom left);
    ``share``: x and y from its top left, width and height, as shares of the target."""
    x, y, share_width, share_height = share
    return (x * size_px[0], (1 - y - share_height) * size_px[1], share_width * size_px[0], share_height * size_px[1])


def vertex_input(mesh: CMesh, inputs: list[gameshader.SInput]) -> tuple[bytes, list]:
    """The QRhi attributes of the vertex inputs by their semantics: binding 0 the mesh as stored, binding 1 a
    constant per vertex for an input the mesh lacks (a colour white, the rest zero), returned as its bytes."""
    fill, attributes = [], []
    for shader_input in inputs:
        match = re.fullmatch(r"([A-Za-z_]+?)(\d*)", shader_input.semantic)
        usage = SEMANTICS.get(match.group(1).upper()) if match else None
        found = [element for element in mesh.m_elements if element[0] == usage]
        nth = int(match.group(2) or 0) if match else 0
        element = found[nth] if nth < len(found) else None
        attribute_format = ATTRIBUTES.get((element[1], element[2])) if element is not None else None
        if attribute_format is not None:
            attributes.append(QRhiVertexInputAttribute(0, shader_input.location, attribute_format, element[3]))
        else:
            attributes.append(
                QRhiVertexInputAttribute(1, shader_input.location, _AttributeFormat.UNormByte4, 4 * len(fill))
            )
            fill.append(255 if usage == 5 else 0)
    per_vertex = bytes(value for value in fill for _ in range(4))
    return per_vertex * mesh.m_count, attributes


def screen_triangle() -> CMesh:
    """A triangle over the screen (``PostEffectVertex``: float3 position, float2 coordinate): the coordinate as
    Direct3D reads a texture (v down, row 0 at the top)."""
    vertices = np.array([[-1, -1, 0, 0, 1], [3, -1, 0, 2, 1], [-1, 3, 0, 0, -1]], "<f4")
    return CMesh(vertices.tobytes(), 20, ((0, 7, 3, 0), (4, 7, 2, 12)), np.array([0, 1, 2], np.uint32), 3)


def white_texture(rhi, batch) -> QRhiTexture:
    """A white 4 x 4 texture (what a plain 2D texture no pass hands reads), uploaded in ``batch``."""
    white = rhi.newTexture(TextureFormat.RGBA8, QSize(4, 4))
    white.create()
    upload_level(batch, white, bytes([255]) * 64)
    return white


def upload_level(batch, texture, data: bytes, layer: int = 0, level: int = 0) -> None:
    """``data`` into one layer and level of ``tex`` (a map written again in place keeps the binding sets on it)."""
    subresource = QRhiTextureSubresourceUploadDescription(data)
    batch.uploadTexture(texture, QRhiTextureUploadDescription(QRhiTextureUploadEntry(layer, level, subresource)))


def dds_texture(rhi, dds) -> tuple[QRhiTexture, QRhiTextureUploadDescription]:
    """A texture of a DDS file's stored levels (BC blocks as stored, a volume's slices) and the upload of them, for
    the caller's batch; a plain format other than RGBA16F uploads as RGBA8 (its bytes as stored). A volume whose
    file stops short of the full chain takes its first level only (no level is left unwritten: ``mipchain``)."""
    dds_info = dds.m_info
    texture_format = BLOCK_FORMATS.get(
        dds_info.format, TextureFormat.RGBA16F if dds_info.format == "RGBA16F" else TextureFormat.RGBA8
    )
    mips = dds_info.mips
    if dds_info.depth > 1 and mips < mipchain.chain_length(dds_info.width_px, dds_info.height_px, dds_info.depth):
        mips = 1
    flags = QRhiTexture.Flag.MipMapped if mips > 1 else QRhiTexture.Flag(0)
    if dds_info.depth > 1:
        flags |= QRhiTexture.Flag.ThreeDimensional
        texture = rhi.newTexture(texture_format, dds_info.width_px, dds_info.height_px, dds_info.depth, 1, flags)
    else:  # the flat call: made by the call with a depth, the clouds' weather map drew no cloud (audit, round 12)
        texture = rhi.newTexture(texture_format, QSize(dds_info.width_px, dds_info.height_px), 1, flags)
    texture.create()
    if dds_info.depth > 1:
        entries = [QRhiTextureUploadEntry(z, mip, QRhiTextureSubresourceUploadDescription(dds.stored(mip, 0, z)))
                   for mip in range(mips) for z in range(dds.slices(mip))]  # fmt: skip
    else:
        entries = [QRhiTextureUploadEntry(0, mip, QRhiTextureSubresourceUploadDescription(raw))
                   for mip, raw in enumerate(stored_levels(dds))]  # fmt: skip
    upload = QRhiTextureUploadDescription()
    upload.setEntries(entries)
    return texture, upload


def immutable_buffer(rhi, batch, usage, data: bytes) -> QRhiBuffer:
    """A buffer of ``data`` (vertices, indices) uploaded once through ``batch`` (16 bytes at least: none is empty)."""
    buffer = rhi.newBuffer(QRhiBuffer.Type.Immutable, usage, max(16, len(data)))
    buffer.create()
    if data:
        batch.uploadStaticBuffer(buffer, data)
    return buffer


@dataclass
class SMaterial:
    """A model config's material as the shaders read it: the structures' values and the maps by parameter."""

    fields: dict  # structure -> field -> value
    maps: dict  # parameter -> ``models.ddsdecode.CDdsFile``
    paths: dict = field(default_factory=dict)  # parameter -> the map's file (maps shared by file: ``gamemaps``)


class CGameModel(CPipelineUser):
    """One mesh with its material drawn by ``program`` into targets of ``signature`` (``gameframe``); ``world`` is
    its place (4 x 4, column vectors) and ``palette`` a skin's matrices per joint (None: rigid), set per frame.
    ``share``: another pass's model of the same group, whose mesh and maps this one draws with (a shadow caster);
    ``state`` the technique's states."""

    def __init__(self, rhi, program: CProgram, mesh: CMesh, material: SMaterial, share: CGameModel | None = None,
                 state: SPassState = GBUFFER_STATE, table: CTextureTable | None = None) -> None:  # fmt: skip
        self.m_rhi, self.m_program, self.m_mesh, self.m_material = rhi, program, mesh, material
        self.m_share, self.m_state = share, state
        self.m_world = np.identity(4)
        self.m_palette: np.ndarray | None = None
        self.m_table = table or (share.m_table if share is not None else CTextureTable(material.maps))
        self.m_arrays: list = []  # the bindless stand-in's texture arrays, made with the resources
        self.m_given_arrays: list | None = None  # the owner's instead, shared by its draws (the terrain bake's maps)
        self.m_keep: list = []
        self.m_pipeline = None
        self._m_pipeline_key: tuple | None = None  # its pipeline's key in ``program.pipelines`` (shared)
        self.m_constants: dict[str, gameshader.CConstants] = {}
        self.m_buffers: dict[str, object] = {}
        self.m_ranges: dict[str, tuple[int, int]] = {}  # a constant buffer in the model's pool -> (offset, size)
        self._m_pool: gamepool.CUniformPool | None = None
        self._m_tokens: dict[tuple, tuple | None] = {}  # the device's shared stand-ins this model uses
        self.m_error = ""
        self._m_sent: dict[str, bytes] = {}  # the bytes each constant buffer was last sent
        self._m_filled: dict[str, tuple] = {}  # a constant buffer -> (its constants, the stamp it was filled for)
        self._m_layouts: dict[str, tuple] = {}  # a constant buffer -> (its layout, which inputs it reads: ``_stamp``)
        self._m_inputs: tuple | None = None  # this prepare's inputs (``_inputs_of``; None: every buffer is filled)
        self._m_uploaded: int | None = None  # the mesh version its own vertex buffer holds (None: a shared one)
        self.m_values: dict[str, object] = {}  # member path -> value its owner sets each frame (a cloth's cbCloth)
        self.m_raw: dict[str, bytes] = {}  # constant buffer -> the bytes it starts with (a cloth's simulated vertices)

    def fresh(self) -> bool:
        """Whether its resources are not made yet: its first ``prepare`` is the costly one (``nativepass.prepared``
        spreads those over frames)."""
        return not self.m_keep

    def _buffer(self, batch, usage, data: bytes):
        buffer = immutable_buffer(self.m_rhi, batch, usage, data)
        self.m_keep.append(buffer)
        return buffer

    def _resources(self, batch, shared: dict) -> None:
        rhi = self.m_rhi
        self._m_sent, self.m_constants, self.m_buffers, self._m_filled = {}, {}, {}, {}
        self.m_ranges, self._m_pool = {}, None
        fill, self.m_attrs = vertex_input(self.m_mesh, self.m_program.m_vertex_stage.inputs)
        self.m_fill_stride = len(fill) // max(1, self.m_mesh.m_count)
        vertex = QRhiBuffer.UsageFlag.VertexBuffer
        self._m_uploaded = self.m_mesh.m_version if self.m_share is None or not self.m_share.m_keep else None
        if self._m_uploaded is not None:  # its own copy of the vertices
            self.m_mesh_buf = self._buffer(batch, vertex, self.m_mesh.m_data)
            self.m_ibuf = self._buffer(
                batch, QRhiBuffer.UsageFlag.IndexBuffer, self.m_mesh.m_indices.astype("<u4").tobytes()
            )
            if self.m_given_arrays is not None:
                self.m_arrays = self.m_given_arrays
            else:
                self.m_arrays = self.m_table.create(rhi, batch)
                self.m_keep += self.m_arrays
        else:  # the other pass's buffers and maps, made once
            self.m_mesh_buf, self.m_ibuf, self.m_arrays = (
                self.m_share.m_mesh_buf,
                self.m_share.m_ibuf,
                self.m_share.m_arrays,
            )
        fill_buf, white = self._inputs(batch, fill)
        self.m_vbufs, self.m_white = [self.m_mesh_buf, fill_buf], white
        self.m_srb = self._bindings(shared, white)

    def _bindings(self, shared: dict, white):
        """The binding set of every resource the program names (made and kept): the constant buffers
        (``_cbuffer``), the samplers, the material's arrays, the textures and raw buffers the frame hands by name."""
        rhi = self.m_rhi
        bindings = []
        for (name, kind), number in self.m_program.m_numbers.items():
            stage_flags = self.m_program.m_stages[(name, kind)]
            if kind == "cbuffer":
                bindings.append(self._cbuffer(name, number, stage_flags))
            elif kind == "sampler":
                bindings.append(Binding.sampler(number, stage_flags, self._sampler(name)))
            elif kind == "texture":
                array_index = (
                    int(name[len("cBindless") :]) if name.startswith("cBindless") and name[9:].isdigit() else -1
                )
                bindings.append(
                    Binding.texture(
                        number,
                        stage_flags,
                        self.m_arrays[array_index] if array_index >= 0 else self._texture(name, shared, white),
                    )
                )
            elif name in shared:  # a raw buffer the frame fills (the cascades' information, the lights)
                bindings.append(Binding.bufferLoadStore(number, stage_flags, shared[name]))
            else:  # a buffer no pass fills (the texture feedback, compiled out): a scratch one
                bindings.append(Binding.bufferLoadStore(number, stage_flags, self._scratch(name)))
        srb = rhi.newShaderResourceBindings()
        srb.setBindings(bindings)
        srb.create()
        self.m_keep.append(srb)
        return srb

    def _cbuffer(self, name: str, number: int, stages):
        """The binding of the constant buffer ``name``: its values (the material's) and its uniform buffer, made once."""
        if name not in self.m_constants:
            constants = self.m_constants[name] = gameshader.CConstants(self.m_program.m_cbuffers[name])
            gameshader.fill_material(constants, self.m_material.fields, self.m_table.m_slots)
            constants.set("cBindlessSlots", self.m_table.m_words.reshape(-1, 4))
            self.m_buffers[name] = self._uniform(name, len(constants.m_data))
        if name in self.m_ranges:
            return Binding.uniformBuffer(number, stages, self.m_buffers[name], *self.m_ranges[name])
        return Binding.uniformBuffer(number, stages, self.m_buffers[name])

    def _shared(self, key: tuple, make):
        """The device's stand-in for ``key`` (``gamepool.shared``), counted once for this model."""
        resource, token = gamepool.shared(self.m_rhi, key, make)
        if key in self._m_tokens:
            gamepool.give_back(token)  # this model counts already
        else:
            self._m_tokens[key] = token
        return resource

    def _inputs(self, batch, fill: bytes) -> tuple:
        """The vertex buffer of the inputs the mesh lacks (``fill``: the device's empty one when it lacks none)
        and the white texture of the plain 2D textures no pass hands."""
        if fill:
            return self._buffer(batch, QRhiBuffer.UsageFlag.VertexBuffer, fill), self._white(batch)
        vertex = QRhiBuffer.UsageFlag.VertexBuffer
        empty = self._shared(("model", "empty"), lambda: immutable_buffer(self.m_rhi, batch, vertex, b""))
        return empty, self._white(batch)

    def _white(self, batch):
        """A white 4 x 4 texture, the device's (uploaded in ``batch`` by the first model that asks)."""
        return self._shared(("model", "white"), lambda: white_texture(self.m_rhi, batch))

    def _uniform(self, name: str, size_bytes: int):
        """The uniform buffer of the constant buffer ``name``: a range of the model's one buffer (``m_ranges``)."""
        if self._m_pool is None:
            rhi = self.m_rhi
            room = sum(rhi.ubufAligned(max(16, layout.size)) for layout in self.m_program.m_cbuffers.values())
            self._m_pool = gamepool.CUniformPool(rhi, self.m_keep, room)
        buffer, offset, size = self._m_pool.take(size_bytes)
        self.m_ranges[name] = (offset, size)
        return buffer

    def _texture(self, name: str, shared: dict, white):
        """A texture the frame hands by name (the shading's tables, the shadow map), else white for a plain 2D
        texture, a zeroed stand-in of another declared kind (a cube, an array, an integer texture)."""
        if name in shared:
            return shared[name]
        program = self.m_program
        declared = next(
            (
                stage.types[name]
                for stage in (
                    program.m_vertex_stage,
                    program.m_pixel_stage,
                    program.m_hull_stage,
                    program.m_domain_stage,
                )
                if stage and name in stage.types
            ),
            "Texture2D",
        )
        if declared in ("Texture2D", "Texture2D<float4>"):
            return white
        return self._shared(("model", "texture", declared), lambda: stand_in(self.m_rhi, "texture", declared))

    def _sampler(self, name: str):
        return self._shared(("model", "sampler", name), lambda: fixed_sampler(self.m_rhi, name))

    def _scratch(self, name: str):
        """A raw buffer no pass fills (the device's, shared by the models)."""
        return self._shared(("model", "scratch"), lambda: stand_in(self.m_rhi, "uav_rwbyteaddress"))

    def prepare(
        self, render_pass, signature, batch, camera: gameshader.SCamera, fill=None, shared: dict | None = None
    ) -> None:
        """Make what is missing and write this frame's inputs into ``batch`` (before the pass begins); ``fill(c)``
        writes a pass's own constants into each buffer (the shadow's cascades, the lights); ``shared``: raw buffers
        the frame owns, by name (bound when the model's resources are made)."""
        if not self.m_keep:
            self._resources(batch, shared or {})
        elif self._m_uploaded is not None and self._m_uploaded != self.m_mesh.m_version:  # a cloth moved
            batch.uploadStaticBuffer(self.m_mesh_buf, self.m_mesh.m_data)
            self._m_uploaded = self.m_mesh.m_version
        if self.m_pipeline is None and not self.m_error:
            self._make_pipeline(render_pass, signature)
        self._m_inputs = self._inputs_of(camera, fill)
        for name, constants in self.m_constants.items():
            self._write(batch, name, constants, camera, fill)

    def _inputs_of(self, camera: gameshader.SCamera, fill) -> tuple | None:
        """What this prepare fills the buffers from: the camera, the place and pose, the pass's fill (its ``key``,
        and ``names``, the members it writes: None for any), the owner's values; None for a fill without a key."""
        fill_key, fill_names = None, frozenset()
        if fill is not None:
            fill_key = getattr(fill, "key", None)
            if fill_key is None:
                return None
            fill_names = getattr(fill, "names", None)
        key = gameshader.value_key
        place = (key(np.asarray(self.m_world)), None if self.m_palette is None else key(np.asarray(self.m_palette)))
        values = tuple((path, key(value)) for path, value in self.m_values.items())
        value_names = frozenset(path.partition(".")[0].partition("[")[0] for path in self.m_values)
        return gameshader.camera_key(camera), place, fill_key, fill_names, values, value_names

    def _stamp(self, name: str, constants: gameshader.CConstants) -> tuple | None:
        """What the buffer ``name`` is filled from this frame, as far as its members read it: the camera where it
        holds frame members, the place where it holds instance members, the fill and the values where they write a
        member of it, its raw bytes (None: filled every time)."""
        if self._m_inputs is None:
            return None
        reflection = constants.m_reflection
        layout = self._m_layouts.get(name)
        if layout is None or layout[0] is not reflection:
            names = frozenset(variable.name for variable in reflection.variables)
            layout = self._m_layouts[name] = (reflection, names, not names.isdisjoint(gameshader.FRAME_NAMES),
                                              not names.isdisjoint(gameshader.INSTANCE_NAMES))  # fmt: skip
        _reflection, names, has_frame, has_instance = layout
        camera_key, place, fill_key, fill_names, values, value_names = self._m_inputs
        raw = self.m_raw.get(name)
        return (camera_key if has_frame else None, place if has_instance else None,
                fill_key if fill_names is None or not names.isdisjoint(fill_names) else None,
                values if not names.isdisjoint(value_names) else None, None if raw is None else bytes(raw))  # fmt: skip

    def _write(self, batch, name: str, constants: gameshader.CConstants, camera: gameshader.SCamera, fill) -> None:
        """This frame's values of the constant buffer ``name`` (the camera, the place, the pass's, the owner's), sent;
        nothing when the inputs it reads stand as it was last filled (``_stamp``)."""
        stamp = self._stamp(name, constants)
        filled = self._m_filled.get(name)
        if stamp is not None and filled is not None and filled[0] is constants and filled[1] == stamp:
            return
        gameshader.fill_frame(constants, camera)
        gameshader.fill_instance(constants, self.m_world, self.m_palette)
        if fill is not None:
            fill(constants)
        for path, value in self.m_values.items():
            constants.set(path, value)
        raw = self.m_raw.get(name)
        if raw is not None:
            length = min(len(raw), len(constants.m_data))
            constants.m_data[:length] = raw[:length]
        self._send(batch, name, bytes(constants.m_data))
        self._m_filled[name] = (constants, stamp)

    def _send(self, batch, name: str, data: bytes) -> None:
        """``data`` into the buffer of ``name`` when the bytes this model last sent there differ."""
        if self._m_sent.get(name) != data:
            batch.updateDynamicBuffer(self.m_buffers[name], self.m_ranges.get(name, (0, 0))[0], data)
            self._m_sent[name] = data

    def draw(self, command_buffer, size_px: tuple[int, int], instances: int = 1) -> None:
        if self.m_pipeline is not None:
            self.m_mesh.draw(
                command_buffer,
                self.m_pipeline,
                self.m_srb,
                size_px,
                (self.m_vbufs, self.m_ibuf),
                instances,
                state=self.m_state,
            )

    def release(self) -> None:
        for resource in self.m_keep:
            if resource is not None:
                resource.destroy()
        self._give_back(self._m_pipeline_key)
        for token in self._m_tokens.values():
            gamepool.give_back(token)
        self.m_keep, self.m_pipeline, self._m_pipeline_key = [], None, None
        self.m_ranges, self._m_pool, self._m_tokens = {}, None, {}

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        return self.m_mesh.box()
