"""One mesh drawn by Anno 117's own shaders on QRhi (Direct3D 12): the binding layout from the compiled shaders'
reflection, the engine's buffers filled by name, the material's maps in texture arrays as they are stored (BC blocks
uploaded unchanged), a pipeline per target.

``Program(vs, ps, gs=None)`` takes the compiled ``Stage``s; ``GameModel(rhi, program, mesh, material)`` is the draw:
``prepare(rp, signature, batch, camera)`` before a pass, ``draw(cb, size)`` inside it (``gameframe`` runs both).
The rules: docs/tool-suite.md section 21, "The game-shader path".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhiBuffer,
    QRhiGraphicsPipeline,
    QRhiSampler,
    QRhiShaderResourceBinding,
    QRhiShaderStage,
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
    QRhiVertexInputAttribute,
    QRhiVertexInputBinding,
    QRhiVertexInputLayout,
    QRhiViewport,
    QShader,
    QShaderCode,
    QShaderKey,
    QShaderVersion,
)

from ..models.fxstage import Sampler, sampler_named
from . import gameshader as gs
from . import mipchain
from .renderstates import GBUFFER_STATE, PassState, stencil_state

B = QRhiShaderResourceBinding
F = QRhiTexture.Format
# a DDS's block kind -> the QRhi format it uploads as
BLOCK_FORMATS = {"DXT1": F.BC1, "DXT3": F.BC2, "DXT5": F.BC3, "ATI1": F.BC4, "ATI2": F.BC5, "BC6H": F.BC6H,
                 "BC7": F.BC7}  # fmt: skip
SRGB_BLOCKS = ("DXT1", "DXT3", "DXT5", "BC7")
# an effect's address mode words -> QRhi's (the engine's fixed samplers make the same record: ``sampler_named``)
_ADDRESS = {"WRAP": QRhiSampler.AddressMode.Repeat, "CLAMP": QRhiSampler.AddressMode.ClampToEdge,
            "MIRROR": QRhiSampler.AddressMode.Mirror, "BORDER": QRhiSampler.AddressMode.ClampToEdge}  # fmt: skip


# 117's depth is reversed (1 near, 0 far), so a comparison sampler passes where the reference is the greater
REVERSED_COMPARE = QRhiSampler.CompareOp.GreaterOrEqual


def sampler_of(rhi, desc: Sampler, compare=QRhiSampler.CompareOp.LessOrEqual) -> QRhiSampler:
    """A QRhi sampler for a sampler state (QRhi has no anisotropy: linear with mips stands in). Point filtering
    keeps a linear mip filter unless the state says ``MIP_POINT``; ``compare`` is the test of a comparison sampler."""
    point = "POINT" in desc.filter and "ANISOTROPIC" not in desc.filter
    f = QRhiSampler.Filter.Nearest if point else QRhiSampler.Filter.Linear
    mip = QRhiSampler.Filter.Nearest if "MIP_POINT" in desc.filter else QRhiSampler.Filter.Linear
    modes = [_ADDRESS.get(a, _ADDRESS["WRAP"]) for a in (desc.address_u, desc.address_v, desc.address_w)]
    s = rhi.newSampler(f, f, mip, modes[0], modes[1])
    s.setAddressW(modes[2])
    if desc.compare:
        s.setTextureCompareOp(compare)
    s.create()
    return s


def fixed_sampler(rhi, name: str, default: str = "WRAP") -> QRhiSampler:
    """One of 117's fixed samplers by the words of its name (``sampler_named``), its depth comparison reversed."""
    return sampler_of(rhi, sampler_named(name, default), REVERSED_COMPARE)


@dataclass
class Stage:
    """A compiled shader: its DXIL, entry point and reflection (``infra``'s ``Reflection``)."""

    blob: bytes
    entry: str
    reflection: object
    inputs: list = field(default_factory=list)  # the vertex stage's ``gameshader.Input``s
    types: dict = field(default_factory=dict)  # a texture's declared type by name (``gameshader.declared_types``)
    state: dict = field(default_factory=dict)  # its pass's render states (``fxeffect.Effect.state_of``)


def is_dxil(blob: bytes) -> bool:
    """Whether a compiled shader is DXIL (DXC, Direct3D 12) rather than DXBC (FXC, Direct3D 10 and 11): both come in
    a DXBC container, DXIL has a part of that name."""
    return blob[:4] == b"DXBC" and b"DXIL" in blob[32 : 32 + 4 * 64 + 4096]


def native_shader(stage: QShader.Stage, blob: bytes, entry: str, binding_map: dict[int, tuple[int, int]]) -> QShader:
    """A ``QShader`` holding a game's compiled shader as it is - DXIL for the Direct3D 12 backend, DXBC for the
    Direct3D 11 one - with the QRhi binding -> register map of its resources."""
    s = QShader()
    s.setStage(stage)
    dxil = is_dxil(blob)
    source = QShader.Source.DxilShader if dxil else QShader.Source.DxbcShader
    for version in (60, 62, 66, 67) if dxil else (40, 41, 50, 51):  # the backends look a shader up by model
        key = QShaderKey(source, QShaderVersion(version))
        s.setShader(key, QShaderCode(blob, entry.encode()))
        s.setResourceBindingMap(key, binding_map)
    return s


def uniform_buffer(rhi, size: int) -> QRhiBuffer:
    """A dynamic uniform buffer for a constant buffer of ``size`` bytes, rounded up to the 256 the shaders bind at."""
    buf = rhi.newBuffer(QRhiBuffer.Type.Dynamic, QRhiBuffer.UsageFlag.UniformBuffer, (max(16, size) + 255) // 256 * 256)
    buf.create()
    return buf


def stand_in(rhi, kind: str, declared: str = "", batch=None, value: float = 0.0):
    """A resource for a binding no pass of the frame makes: a raw buffer, a sampler, or a 1 x 1 texture of the
    declared type (``Texture2D``, ``TextureCube``, ``Texture2DArray``, ``Texture3D``; ``<uint>`` integer; ``RW...`` a
    storage texture). With ``batch`` a buffer is zeroed and a sampled texture filled with ``value`` (an integer
    texture with 0); without it the contents are whatever the backend gives."""
    if kind in ("byteaddress", "uav_rwbyteaddress"):
        res = rhi.newBuffer(QRhiBuffer.Type.Static, QRhiBuffer.UsageFlag.StorageBuffer, 4096)
        layers, data = 0, bytes(4096)
    elif kind == "sampler":
        lin, clamp = QRhiSampler.Filter.Linear, QRhiSampler.AddressMode.ClampToEdge
        res = rhi.newSampler(lin, lin, lin, clamp, clamp)
        layers, data = 0, b""
    else:
        flags = QRhiTexture.Flag.UsedWithLoadStore if kind != "texture" else QRhiTexture.Flag(0)
        integer = "<uint" in declared or "<int" in declared
        fmt = F.R32UI if integer else F.RGBA16F
        data = bytes(4) if integer else np.full(4, value, np.float16).tobytes()
        bare = declared.removeprefix("RW")
        layers = 1
        if bare.startswith("TextureCube"):
            res, layers = rhi.newTexture(fmt, QSize(1, 1), 1, flags | QRhiTexture.Flag.CubeMap), 6
        elif bare.startswith("Texture2DArray"):
            res, layers = rhi.newTextureArray(fmt, 2, QSize(1, 1), 1, flags), 2
        elif bare.startswith("Texture3D"):
            res = rhi.newTexture(fmt, 1, 1, 1, 1, flags | QRhiTexture.Flag.ThreeDimensional)
        else:
            res = rhi.newTexture(fmt, QSize(1, 1), 1, flags)
        if kind != "texture":
            layers = 0  # a storage texture is written by the shader
    res.create()
    if batch is not None and kind in ("byteaddress", "uav_rwbyteaddress"):
        batch.uploadStaticBuffer(res, data)
    elif batch is not None and layers:
        sub = QRhiTextureSubresourceUploadDescription(data)
        desc = QRhiTextureUploadDescription()
        desc.setEntries([QRhiTextureUploadEntry(k, 0, sub) for k in range(layers)])
        batch.uploadTexture(res, desc)
    return res


def _binding_key(binding, buffers: dict[str, str]) -> tuple[str, str]:
    """A stage's binding as ``Program`` numbers it: (name, kind), a constant buffer under its key in ``buffers``."""
    name = buffers.get(binding.name, binding.name) if binding.kind == "cbuffer" else binding.name
    return name, binding.kind


class Program:
    """A vertex and pixel shader (and an optional geometry shader, ``gs``, or a hull and domain shader, ``hs`` and
    ``ds``: the terrain's quad patches) with one QRhi binding number per resource name, shared by the stages."""

    def __init__(self, vs: Stage, ps: Stage, gs: Stage | None = None, hs: Stage | None = None,
                 ds: Stage | None = None) -> None:  # fmt: skip
        self.vs, self.ps, self.gs, self.hs, self.ds = vs, ps, gs, hs, ds
        self.numbers: dict[tuple[str, str], int] = {}
        self.stages: dict[tuple[str, str], B.StageFlag] = {}
        staged = [(B.StageFlag.VertexStage, vs), (B.StageFlag.FragmentStage, ps)]
        staged += [(B.StageFlag.GeometryStage, gs)] if gs is not None else []
        if hs is not None and ds is not None:
            staged += [(B.StageFlag.TessellationControlStage, hs), (B.StageFlag.TessellationEvaluationStage, ds)]
        names = self._buffer_names([s for _f, s in staged])
        for (flag, stage), own in zip(staged, names, strict=True):
            for b in stage.reflection.bindings:
                key = _binding_key(b, own)
                self.numbers.setdefault(key, len(self.numbers))
                self.stages[key] = self.stages.get(key, B.StageFlag(0)) | flag
        maps = [{self.numbers[_binding_key(b, own)]: (b.register, -1) for b in s.reflection.bindings}
                for (_f, s), own in zip(staged, names, strict=True)]  # fmt: skip
        if not is_dxil(vs.blob):
            self._pad_samplers(maps)
        self.shaders = (native_shader(QShader.Stage.VertexStage, vs.blob, vs.entry, maps[0]),
                        native_shader(QShader.Stage.FragmentStage, ps.blob, ps.entry, maps[1]))  # fmt: skip
        self.geometry = native_shader(QShader.Stage.GeometryStage, gs.blob, gs.entry, maps[2]) if gs else None
        self.patches = None  # the hull and domain shader of a tessellated program
        if hs is not None and ds is not None:
            self.patches = (native_shader(QShader.Stage.TessellationControlStage, hs.blob, hs.entry, maps[-2]),
                            native_shader(QShader.Stage.TessellationEvaluationStage, ds.blob, ds.entry, maps[-1]))  # fmt: skip
        self.cbuffers = {
            own[c.name]: c for (_f, s), own in zip(staged, names, strict=True) for c in s.reflection.cbuffers
        }
        self.pipelines: dict[tuple, list] = {}  # its pipelines by pass and layout: [pipeline, models using it]

    def release(self) -> None:
        """Destroy the pipelines its models did not give back: an owner that lets its models go by their resources
        alone, a frame later, counts no user down (the terrain bake's)."""
        for pipeline, _users in self.pipelines.values():
            pipeline.destroy()
        self.pipelines = {}

    @staticmethod
    def _buffer_names(stages: list) -> list[dict[str, str]]:
        """Per stage its constant buffers' keys: the name, or ``name@<stage>`` for a buffer an earlier stage names
        with another layout (each Direct3D 9 effect's stage has its own ``$Globals``: 1701's)."""
        seen: dict[str, object] = {}
        out = []
        for k, stage in enumerate(stages):
            own = {}
            for c in stage.reflection.cbuffers:
                layout = (c.size, [(v.name, v.offset) for v in c.variables])
                key = c.name if seen.get(c.name, layout) == layout else f"{c.name}@{k}"
                seen.setdefault(c.name, layout)
                own[c.name] = key
            out.append(own)
        return out

    def _pad_samplers(self, maps: list[dict]) -> None:
        """A stand-in sampler (register 0) for a DXBC stage that reads textures without a sampler (``Load``, as
        2205's vertex shader reads its matrices): QRhi's Direct3D 11 backend binds a stage's textures only when it
        also binds a sampler there (seen with PySide6 6.12; shader-compile.md section 11)."""
        for k, (flag, stage) in enumerate(((B.StageFlag.VertexStage, self.vs), (B.StageFlag.FragmentStage, self.ps))):
            kinds = {b.kind for b in stage.reflection.bindings}
            if "texture" in kinds and "sampler" not in kinds:
                key = (f"<{'vertex' if k == 0 else 'pixel'} stage sampler>", "sampler")
                self.numbers[key] = len(self.numbers)
                self.stages[key] = flag
                maps[k][self.numbers[key]] = (0, -1)


@dataclass
class _Class:
    """One texture array of the bindless stand-in: maps of one size, format and level count."""

    key: tuple
    maps: list = field(default_factory=list)  # (dds or rgba array) per layer


class TextureTable:
    """A material's maps spread over ``gameshader.CLASSES`` texture arrays; ``slots`` gives each parameter its
    bindless index (0 is the engine's null texture, white), ``words`` the table the stand-in reads."""

    def __init__(self, maps: dict) -> None:
        self.classes: list[_Class] = [_Class(("null",), [np.full((4, 4, 4), 255, np.uint8)])]
        self.slots: dict[str, int] = {}
        self.words = np.zeros(gs.SLOTS, np.uint32)
        for name, dds in maps.items():
            self.add(name, dds)

    def add(self, name: str, dds) -> int:
        """One more map under ``name``, before the arrays are made (a cloth's player logo, ``render.clothlogo``);
        its bindless index, 0 (the null texture) when every array is taken."""
        stored, key = map_kind(dds)
        found = next((c for c in self.classes if c.key == key), None)
        if found is None:
            if len(self.classes) == gs.CLASSES:
                return 0  # more kinds of map than arrays: the parameter reads the null texture
            found = _Class(key)
            self.classes.append(found)
        found.maps.append(dds if stored else dds.level(0))
        slot = len(self.slots) + 1
        self.slots[name] = slot
        self.words[slot] = (self.classes.index(found) << 16) | (len(found.maps) - 1)
        return slot

    def drop_pixels(self) -> None:
        """Forget the maps' pixels once their arrays are made (``slots`` and ``words`` stay): an owner that makes
        the arrays once for all its draws (``gameinstances.PartPass``) keeps no second copy."""
        self.classes = []

    def create(self, rhi, batch) -> list:
        """The texture arrays (one per class, a white layer for the unused), uploaded in ``batch``."""
        if not self.classes:
            raise RuntimeError("the maps' pixels were dropped: their arrays are made already")
        null = self.classes[0].maps
        return [class_array(rhi, batch, self.classes[k].maps if k < len(self.classes) else null)
                for k in range(gs.CLASSES)]  # fmt: skip


def class_array(rhi, batch, maps: list):
    """One texture array of maps alike (``models.ddsdecode.DdsFile``s of one size, format and level count, stored
    as they are; or RGBA arrays of one size), uploaded in ``batch``."""
    first = maps[0]
    if isinstance(first, np.ndarray):
        h, w = first.shape[:2]
        tex = rhi.newTextureArray(F.RGBA8, len(maps), QSize(w, h))
        levels = [[np.ascontiguousarray(m).tobytes()] for m in maps]
    else:
        info = first.info
        flags = QRhiTexture.Flag.MipMapped if info.mips > 1 else QRhiTexture.Flag(0)
        if info.srgb and info.format in SRGB_BLOCKS:
            flags |= QRhiTexture.Flag.sRGB
        tex = rhi.newTextureArray(BLOCK_FORMATS[info.format], len(maps), QSize(info.width, info.height), 1, flags)
        levels = [stored_levels(m) for m in maps]
    tex.create()
    entries = [QRhiTextureUploadEntry(layer, level, QRhiTextureSubresourceUploadDescription(raw))
               for layer, per in enumerate(levels) for level, raw in enumerate(per)]  # fmt: skip
    desc = QRhiTextureUploadDescription()
    desc.setEntries(entries)
    batch.uploadTexture(tex, desc)
    return tex


def map_kind(dds):
    """How a map goes into a texture array: as stored (BC blocks, one face) or as its first level decoded to RGBA,
    and the key of the maps an array of it can hold."""
    info = dds.info
    stored = info.format in BLOCK_FORMATS and info.faces == 1
    key = (info.width, info.height, info.mips, info.format, info.srgb) if stored else (info.width, info.height)
    return stored, key


# a vertex input's semantic -> the mesh element it reads (the rdm's usages PNGBTCWI, docs/formats/rdm.md)
SEMANTICS = {"SV_POSITION": 0, "POSITION": 0, "NORMAL": 1, "TANGENT": 2, "BINORMAL": 3, "TEXCOORD": 4, "COLOR": 5,
             "BLENDWEIGHT": 6, "BLENDINDICES": 7}  # fmt: skip
_A = QRhiVertexInputAttribute.Format
# an element's (type, count) -> how QRhi feeds it: halves, floats, a packed four-byte vector as unsigned bytes,
# four u16 as integers (the terrain's NuggetsVertex position, ``terraindraw``), four u32 (2070's cloth tangents,
# ``cloth2070``)
ATTRIBUTES = {(6, 2): _A.Half2, (6, 3): _A.Half3, (6, 4): _A.Half4, (7, 1): _A.Float, (7, 2): _A.Float2,
              (7, 3): _A.Float3, (7, 4): _A.Float4, (5, 1): _A.UNormByte4, (2, 4): _A.UShort4,
              (4, 1): _A.UInt, (4, 4): _A.UInt4}  # fmt: skip


@dataclass
class Mesh:
    """A mesh's vertices as the file stores them (``data`` at ``stride``; ``elements`` (usage, type, count, offset)
    in order) and the triangles one draw takes; the engine binds the stored vertices the same way."""

    data: bytes
    stride: int
    elements: tuple
    indices: np.ndarray
    count: int
    version: int = 0  # which vertices it holds (a moving cloth's frame): a device copy of another uploads again
    rest: bytes = b""  # the vertices as first made, once they changed

    def set_vertices(self, data: bytes, version: int) -> None:
        """New vertices in the same layout (``render.clothmesh``), known as ``version``."""
        if not self.rest:
            self.rest = self.data
        self.data, self.version = data, version

    @classmethod
    def of_rdm(cls, mesh, indices) -> Mesh:
        """An ``formats.rdmread.Mesh`` with the indices of one draw (a submesh group)."""
        at, out = 0, []
        for e in mesh.elements:
            out.append((e.usage, e.type, e.count, at))
            at += e.size
        return cls(mesh.vertices, mesh.stride, tuple(out), np.asarray(indices, np.uint32), mesh.vertex_count)

    def positions(self) -> np.ndarray:
        """[n, 3] floats of the first position element."""
        usage, kind, count, at = next(e for e in self.elements if e[0] == 0)
        size = count * (2 if kind == 6 else 4)
        raw = np.frombuffer(self.data, np.uint8).reshape(self.count, self.stride)[:, at : at + size]
        return np.ascontiguousarray(raw).view("<f2" if kind == 6 else "<f4")[:, :3].astype(np.float32)

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        """The corners (min, max) of the box the positions span."""
        v = self.positions()
        return v.min(axis=0), v.max(axis=0)

    def draw(self, cb, pipeline, srb, area: tuple, buffers: tuple, instances: int = 1,
             offset: tuple[int, int] | None = None, state: PassState | None = None) -> None:  # fmt: skip
        """Its triangles drawn by ``pipeline`` with ``srb`` (``buffers``: the vertex buffers - the stored vertices
        and the per-vertex constants - and the index buffer) over the viewport ``area`` (the target's width and
        height, or a part of it: ``share_rect``); ``offset``: (binding, bytes) of the binding set's one buffer bound
        with a dynamic offset; ``state``: the pipeline's, for its stencil reference."""
        vbufs, ibuf = buffers
        cb.setGraphicsPipeline(pipeline)
        if state is not None and state.stencil is not None:
            cb.setStencilRef(state.stencil.ref)
        cb.setViewport(QRhiViewport(*area) if len(area) == 4 else QRhiViewport(0, 0, *area))
        if offset is None:
            cb.setShaderResources(srb)
        else:
            cb.setShaderResources(srb, 1, offset)
        cb.setVertexInput(0, [(b, 0) for b in vbufs], ibuf, 0, cb.IndexFormat.IndexUInt32)
        cb.drawIndexed(len(self.indices), instances)

    def bone_count(self, per_vertex: int) -> int:
        """How many bone matrices the vertices reach (the highest index + 1; 0 for a mesh without a skin)."""
        found = [e for e in self.elements if e[0] == 7]
        if not per_vertex or not found or not self.count:
            return 0
        raw = np.frombuffer(self.data, np.uint8).reshape(self.count, self.stride)
        at = found[0][3]
        return int(raw[:, at : at + (4 if per_vertex > 1 else 1)].max()) + 1


def share_rect(size: tuple[int, int], share: tuple) -> tuple:
    """A part of a target of ``size`` as a viewport (x, y, width, height; QRhi's starts at the bottom left);
    ``share``: x and y from its top left, width and height, as shares of the target."""
    x, y, w, h = share
    return (x * size[0], (1 - y - h) * size[1], w * size[0], h * size[1])


def vertex_input(mesh: Mesh, inputs: list[gs.Input]) -> tuple[bytes, list]:
    """The QRhi attributes of the vertex inputs by their semantics: binding 0 the mesh as stored, binding 1 a
    constant per vertex for an input the mesh lacks (a colour white, the rest zero), returned as its bytes."""
    fill, attrs = [], []
    for inp in inputs:
        m = re.fullmatch(r"([A-Za-z_]+?)(\d*)", inp.semantic)
        usage = SEMANTICS.get(m.group(1).upper()) if m else None
        found = [e for e in mesh.elements if e[0] == usage]
        nth = int(m.group(2) or 0) if m else 0
        e = found[nth] if nth < len(found) else None
        fmt = ATTRIBUTES.get((e[1], e[2])) if e is not None else None
        if fmt is not None:
            attrs.append(QRhiVertexInputAttribute(0, inp.location, fmt, e[3]))
        else:
            attrs.append(QRhiVertexInputAttribute(1, inp.location, _A.UNormByte4, 4 * len(fill)))
            fill.append(255 if usage == 5 else 0)
    per_vertex = bytes(v for v in fill for _ in range(4))
    return per_vertex * mesh.count, attrs


def screen_triangle() -> Mesh:
    """A triangle over the screen (``PostEffectVertex``: float3 position, float2 coordinate): the coordinate as
    Direct3D reads a texture (v down, row 0 at the top)."""
    v = np.array([[-1, -1, 0, 0, 1], [3, -1, 0, 2, 1], [-1, 3, 0, 0, -1]], "<f4")
    return Mesh(v.tobytes(), 20, ((0, 7, 3, 0), (4, 7, 2, 12)), np.array([0, 1, 2], np.uint32), 3)


def white_texture(rhi, batch) -> QRhiTexture:
    """A white 4 x 4 texture (what a plain 2D texture no pass hands reads), uploaded in ``batch``."""
    white = rhi.newTexture(F.RGBA8, QSize(4, 4))
    white.create()
    upload_level(batch, white, bytes([255]) * 64)
    return white


def stored_levels(dds) -> list[bytes]:
    """A flat map's levels as stored, and those its file stops short of down to 1 x 1 (``mipchain``: a mipmapped
    QRhi texture has every level, and one never written reads what the memory held, coloured speckles)."""
    levels = [dds.stored(level) for level in range(dds.info.mips)]
    return mipchain.full_levels(dds.info, levels) if dds.info.mips > 1 else levels


def upload_level(batch, tex, data: bytes, layer: int = 0, level: int = 0) -> None:
    """``data`` into one layer and level of ``tex`` (a map written again in place keeps the binding sets on it)."""
    sub = QRhiTextureSubresourceUploadDescription(data)
    batch.uploadTexture(tex, QRhiTextureUploadDescription(QRhiTextureUploadEntry(layer, level, sub)))


def dds_texture(rhi, dds) -> tuple[QRhiTexture, QRhiTextureUploadDescription]:
    """A texture of a DDS file's stored levels (BC blocks as stored, a volume's slices) and the upload of them, for
    the caller's batch; a plain format other than RGBA16F uploads as RGBA8 (its bytes as stored). A volume whose
    file stops short of the full chain takes its first level only (no level is left unwritten: ``mipchain``)."""
    info = dds.info
    fmt = BLOCK_FORMATS.get(info.format, F.RGBA16F if info.format == "RGBA16F" else F.RGBA8)
    mips = info.mips
    if info.depth > 1 and mips < mipchain.chain_length(info.width, info.height, info.depth):
        mips = 1
    flags = QRhiTexture.Flag.MipMapped if mips > 1 else QRhiTexture.Flag(0)
    if info.depth > 1:
        flags |= QRhiTexture.Flag.ThreeDimensional
        tex = rhi.newTexture(fmt, info.width, info.height, info.depth, 1, flags)
    else:  # the flat call: made by the call with a depth, the clouds' weather map drew no cloud (audit, round 12)
        tex = rhi.newTexture(fmt, QSize(info.width, info.height), 1, flags)
    tex.create()
    if info.depth > 1:
        entries = [QRhiTextureUploadEntry(z, mip, QRhiTextureSubresourceUploadDescription(dds.stored(mip, 0, z)))
                   for mip in range(mips) for z in range(dds.slices(mip))]  # fmt: skip
    else:
        entries = [QRhiTextureUploadEntry(0, mip, QRhiTextureSubresourceUploadDescription(raw))
                   for mip, raw in enumerate(stored_levels(dds))]  # fmt: skip
    desc = QRhiTextureUploadDescription()
    desc.setEntries(entries)
    return tex, desc


def immutable_buffer(rhi, batch, usage, data: bytes) -> QRhiBuffer:
    """A buffer of ``data`` (vertices, indices) uploaded once through ``batch`` (16 bytes at least: none is empty)."""
    buf = rhi.newBuffer(QRhiBuffer.Type.Immutable, usage, max(16, len(data)))
    buf.create()
    if data:
        batch.uploadStaticBuffer(buf, data)
    return buf


def vertex_layout(strides: list[int], attrs: list) -> QRhiVertexInputLayout:
    """A vertex input layout of one binding per stride (a mesh: its stored vertices, the per-vertex constants)."""
    layout = QRhiVertexInputLayout()
    layout.setBindings([QRhiVertexInputBinding(s) for s in strides])
    layout.setAttributes(attrs)
    return layout


def target_blends(blends: tuple, targets: int) -> list:
    """QRhi's blend of each of ``targets`` colour targets: ``blends[i]`` (colour source and destination, alpha source
    and destination factors; None or missing: off)."""
    out = []
    for i in range(targets):
        tb = QRhiGraphicsPipeline.TargetBlend()
        found = blends[i] if i < len(blends) else None
        if found is not None:
            tb.enable = True
            tb.srcColor, tb.dstColor, tb.srcAlpha, tb.dstAlpha = found
        out.append(tb)
    return out


CULL_MODES = {"back": QRhiGraphicsPipeline.CullMode.Back, "front": QRhiGraphicsPipeline.CullMode.Front,
              "none": QRhiGraphicsPipeline.CullMode.None_}  # fmt: skip


def _pass_format(rp) -> tuple:
    """What a pipeline made for the pass ``rp`` depends on of it (its targets' formats; the object where QRhi says
    nothing)."""
    try:
        return tuple(rp.serializedFormat())
    except (AttributeError, TypeError):
        return (id(rp),)


def new_pipeline(rhi, program: Program, layout, srb, rp, samples: int, blends: list):
    """A graphics pipeline of ``program``'s stages (its geometry shader, or its hull and domain shader on patches of
    four points, too) for the pass ``rp``: its vertex layout, binding set, sample count and target blends set; the
    caller sets depth and culling (and the topology of an untessellated one), then ``create``s."""
    ps = rhi.newGraphicsPipeline()
    vs_shader, ps_shader = program.shaders
    stages = [QRhiShaderStage(QRhiShaderStage.Type.Vertex, vs_shader)]
    if program.geometry is not None:  # interior mapping: face normals from its geometry shader
        stages.append(QRhiShaderStage(QRhiShaderStage.Type.Geometry, program.geometry))
    if program.patches is not None:  # the terrain's detailed nodes: quad patches
        hull, domain = program.patches
        stages += [QRhiShaderStage(QRhiShaderStage.Type.TessellationControl, hull),
                   QRhiShaderStage(QRhiShaderStage.Type.TessellationEvaluation, domain)]  # fmt: skip
        ps.setTopology(QRhiGraphicsPipeline.Topology.Patches)
        ps.setPatchControlPointCount(4)
    ps.setShaderStages([*stages, QRhiShaderStage(QRhiShaderStage.Type.Fragment, ps_shader)])
    ps.setVertexInputLayout(layout)
    ps.setShaderResourceBindings(srb)
    ps.setRenderPassDescriptor(rp)
    ps.setSampleCount(max(1, samples))
    ps.setTargetBlends(blends)
    return ps


@dataclass
class Material:
    """A model config's material as the shaders read it: the structures' values and the maps by parameter."""

    fields: dict  # structure -> field -> value
    maps: dict  # parameter -> ``models.ddsdecode.DdsFile``
    paths: dict = field(default_factory=dict)  # parameter -> the map's file (maps shared by file: ``gamemaps``)


class GameModel:
    """One mesh with its material drawn by ``program`` into targets of ``signature`` (``gameframe``); ``world`` is
    its place (4 x 4, column vectors) and ``palette`` a skin's matrices per joint (None: rigid), set per frame.
    ``share``: another pass's model of the same group, whose mesh and maps this one draws with (a shadow caster);
    ``state`` the technique's states."""

    def __init__(self, rhi, program: Program, mesh: Mesh, material: Material, share: GameModel | None = None,
                 state: PassState = GBUFFER_STATE, table: TextureTable | None = None) -> None:  # fmt: skip
        self.rhi, self.program, self.mesh, self.material = rhi, program, mesh, material
        self.share, self.state = share, state
        self.world = np.identity(4)
        self.palette: np.ndarray | None = None
        self.table = table or (share.table if share is not None else TextureTable(material.maps))
        self.arrays: list = []  # the bindless stand-in's texture arrays, made with the resources
        self.given_arrays: list | None = None  # the owner's instead, shared by its draws (the terrain bake's maps)
        self.keep: list = []
        self.pipeline = None
        self._pipeline_key: tuple | None = None  # its pipeline's key in ``program.pipelines`` (shared)
        self.constants: dict[str, gs.Constants] = {}
        self.buffers: dict[str, object] = {}
        self.error = ""
        self._sent: dict[str, bytes] = {}  # the bytes each constant buffer was last sent
        self._uploaded: int | None = None  # the mesh version its own vertex buffer holds (None: a shared one)
        self.values: dict[str, object] = {}  # member path -> value its owner sets each frame (a cloth's cbCloth)
        self.raw: dict[str, bytes] = {}  # constant buffer -> the bytes it starts with (a cloth's simulated vertices)

    def fresh(self) -> bool:
        """Whether its resources are not made yet: its first ``prepare`` is the costly one (``nativepass.prepared``
        spreads those over frames)."""
        return not self.keep

    def _buffer(self, batch, usage, data: bytes):
        buf = immutable_buffer(self.rhi, batch, usage, data)
        self.keep.append(buf)
        return buf

    def _resources(self, batch, shared: dict) -> None:
        rhi = self.rhi
        self._sent, self.constants, self.buffers = {}, {}, {}
        fill, self.attrs = vertex_input(self.mesh, self.program.vs.inputs)
        self.fill_stride = len(fill) // max(1, self.mesh.count)
        vertex = QRhiBuffer.UsageFlag.VertexBuffer
        self._uploaded = self.mesh.version if self.share is None or not self.share.keep else None
        if self._uploaded is not None:  # its own copy of the vertices
            self.mesh_buf = self._buffer(batch, vertex, self.mesh.data)
            self.ibuf = self._buffer(batch, QRhiBuffer.UsageFlag.IndexBuffer, self.mesh.indices.astype("<u4").tobytes())
            if self.given_arrays is not None:
                self.arrays = self.given_arrays
            else:
                self.arrays = self.table.create(rhi, batch)
                self.keep += self.arrays
        else:  # the other pass's buffers and maps, made once
            self.mesh_buf, self.ibuf, self.arrays = self.share.mesh_buf, self.share.ibuf, self.share.arrays
        fill_buf, white = self._inputs(batch, fill)
        self.vbufs, self.white = [self.mesh_buf, fill_buf], white
        self.srb = self._bindings(shared, white)

    def _bindings(self, shared: dict, white):
        """The binding set of every resource the program names (made and kept): the constant buffers
        (``_cbuffer``), the samplers, the material's arrays, the textures and raw buffers the frame hands by name."""
        rhi = self.rhi
        bindings = []
        for (name, kind), number in self.program.numbers.items():
            st = self.program.stages[(name, kind)]
            if kind == "cbuffer":
                bindings.append(self._cbuffer(name, number, st))
            elif kind == "sampler":
                bindings.append(B.sampler(number, st, self._sampler(name)))
            elif kind == "texture":
                k = int(name[len("cBindless") :]) if name.startswith("cBindless") and name[9:].isdigit() else -1
                bindings.append(B.texture(number, st, self.arrays[k] if k >= 0 else self._texture(name, shared, white)))
            elif name in shared:  # a raw buffer the frame fills (the cascades' information, the lights)
                bindings.append(B.bufferLoadStore(number, st, shared[name]))
            else:  # a buffer no pass fills (the texture feedback, compiled out): a scratch one
                bindings.append(B.bufferLoadStore(number, st, self._scratch(name)))
        srb = rhi.newShaderResourceBindings()
        srb.setBindings(bindings)
        srb.create()
        self.keep.append(srb)
        return srb

    def _cbuffer(self, name: str, number: int, stages):
        """The binding of the constant buffer ``name``: its values (the material's) and its uniform buffer, made once."""
        if name not in self.constants:
            c = self.constants[name] = gs.Constants(self.program.cbuffers[name])
            gs.fill_material(c, self.material.fields, self.table.slots)
            c.set("cBindlessSlots", self.table.words.reshape(-1, 4))
            self.buffers[name] = self._uniform(name, len(c.data))
        return B.uniformBuffer(number, stages, self.buffers[name])

    def _inputs(self, batch, fill: bytes) -> tuple:
        """The vertex buffer of the inputs the mesh lacks (``fill``) and the white texture of the plain 2D textures
        no pass hands: the model's own, released with it."""
        return self._buffer(batch, QRhiBuffer.UsageFlag.VertexBuffer, fill), self._white(batch)

    def _white(self, batch):
        """A white 4 x 4 texture (the model's own, released with it), uploaded in ``batch``."""
        white = white_texture(self.rhi, batch)
        self.keep.append(white)
        return white

    def _uniform(self, name: str, size: int):
        """The uniform buffer of the constant buffer ``name`` (the model's own, released with it)."""
        buf = uniform_buffer(self.rhi, size)
        self.keep.append(buf)
        return buf

    def _texture(self, name: str, shared: dict, white):
        """A texture the frame hands by name (the shading's tables, the shadow map), else white for a plain 2D
        texture, a zeroed stand-in of another declared kind (a cube, an array, an integer texture)."""
        if name in shared:
            return shared[name]
        p = self.program
        declared = next((s.types[name] for s in (p.vs, p.ps, p.hs, p.ds) if s and name in s.types), "Texture2D")
        if declared in ("Texture2D", "Texture2D<float4>"):
            return white
        tex = stand_in(self.rhi, "texture", declared)
        self.keep.append(tex)
        return tex

    def _sampler(self, name: str):
        s = fixed_sampler(self.rhi, name)
        self.keep.append(s)
        return s

    def _scratch(self, name: str):
        """A raw buffer no pass fills (the model's own, released with it)."""
        buf = stand_in(self.rhi, "uav_rwbyteaddress")
        self.keep.append(buf)
        return buf

    def _make_pipeline(self, rp, signature) -> None:
        """The model's pipeline: one per program, pass and vertex layout, shared by every model that draws alike (its
        own binding set is layout-compatible): a pipeline of the game's shaders holds megabytes of the driver's
        memory, and a continental island's nodes and parts made thousands of them (island-117-view.md "Speed")."""
        self.pipeline, self._pipeline_key = self._shared_pipeline(rp, signature, self.attrs, self.mesh.stride,
                                                                  self.fill_stride)  # fmt: skip

    def _shared_pipeline(self, rp, signature, attrs: list, stride: int, fill_stride: int) -> tuple:
        """(pipeline, its key in ``program.pipelines``) for a vertex layout: the program's shared one, made when
        new (None and no key when the backend refused it: ``error`` says so)."""
        targets, samples = signature
        layout_key = tuple((a.binding(), a.location(), a.format(), a.offset()) for a in attrs)
        key = (_pass_format(rp), targets, samples, stride, max(4, fill_stride), layout_key, self.state)
        shared = self.program.pipelines.get(key)
        if shared is not None:
            shared[1] += 1
            return shared[0], key
        ps = self._new_pipeline(rp, signature, vertex_layout([stride, max(4, fill_stride)], attrs))
        if ps is None:
            return None, None
        self.program.pipelines[key] = [ps, 1]  # the program's: destroyed with its last user
        return ps, key

    def _new_pipeline(self, rp, signature, layout):
        targets, samples = signature
        blends = target_blends(self.state.blends, targets)
        for k, tb in enumerate(blends):
            mask = self.state.writes[k] if k < len(self.state.writes) else None
            if not self.state.colour or mask is not None:  # QRhi's mask bits are D3D's
                tb.colorWrite = QRhiGraphicsPipeline.ColorMaskComponent(0 if not self.state.colour else mask)
        ps = new_pipeline(self.rhi, self.program, layout, self.srb, rp, samples, blends)
        ps.setDepthTest(self.state.depth_test)
        ps.setDepthWrite(self.state.depth_write)
        op = QRhiGraphicsPipeline.CompareOp
        depth_op = op.Always if self.state.depth_always else op.Equal if self.state.depth_equal else op.GreaterOrEqual
        ps.setDepthOp(depth_op)  # the engine's depth: 1 near, 0 far
        ps.setCullMode(CULL_MODES[self.state.cull])
        st = self.state.stencil
        if st is not None:  # the reference is set per draw (``Mesh.draw``)
            ps.setStencilTest(True)
            ps.setStencilFront(stencil_state(st))
            ps.setStencilBack(stencil_state(st))
            ps.setStencilReadMask(st.mask)
            ps.setStencilWriteMask(st.write_mask)
            ps.setFlags(ps.flags() | QRhiGraphicsPipeline.Flag.UsesStencilRef)
        if not ps.create():
            self.error = "the backend refused the game shader's pipeline"
            ps.destroy()
            return None
        return ps

    def _give_back(self, key) -> None:
        """One user fewer of the program's pipeline ``key``; the last destroys it."""
        shared = self.program.pipelines.get(key) if key is not None else None
        if shared is not None:
            shared[1] -= 1
            if shared[1] <= 0:
                shared[0].destroy()
                del self.program.pipelines[key]

    def prepare(self, rp, signature, batch, camera: gs.Camera, fill=None, shared: dict | None = None) -> None:
        """Make what is missing and write this frame's inputs into ``batch`` (before the pass begins); ``fill(c)``
        writes a pass's own constants into each buffer (the shadow's cascades, the lights); ``shared``: raw buffers
        the frame owns, by name (bound when the model's resources are made)."""
        if not self.keep:
            self._resources(batch, shared or {})
        elif self._uploaded is not None and self._uploaded != self.mesh.version:  # a cloth moved
            batch.uploadStaticBuffer(self.mesh_buf, self.mesh.data)
            self._uploaded = self.mesh.version
        if self.pipeline is None and not self.error:
            self._make_pipeline(rp, signature)
        for name, c in self.constants.items():
            self._write(batch, name, c, camera, fill)

    def _write(self, batch, name: str, c: gs.Constants, camera: gs.Camera, fill) -> None:
        """This frame's values of the constant buffer ``name`` (the camera, the place, the pass's, the owner's), sent."""
        gs.fill_frame(c, camera)
        gs.fill_instance(c, self.world, self.palette)
        if fill is not None:
            fill(c)
        for path, value in self.values.items():
            c.set(path, value)
        raw = self.raw.get(name)
        if raw is not None:
            n = min(len(raw), len(c.data))
            c.data[:n] = raw[:n]
        self._send(batch, name, bytes(c.data))

    def _send(self, batch, name: str, data: bytes) -> None:
        """``data`` into the buffer of ``name`` when the bytes this model last sent there differ."""
        if self._sent.get(name) != data:
            batch.updateDynamicBuffer(self.buffers[name], 0, data)
            self._sent[name] = data

    def draw(self, cb, size: tuple[int, int], instances: int = 1) -> None:
        if self.pipeline is not None:
            self.mesh.draw(cb, self.pipeline, self.srb, size, (self.vbufs, self.ibuf), instances, state=self.state)

    def release(self) -> None:
        for r in self.keep:
            if r is not None:
                r.destroy()
        self._give_back(self._pipeline_key)
        self.keep, self.pipeline, self._pipeline_key = [], None, None

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        return self.mesh.box()
