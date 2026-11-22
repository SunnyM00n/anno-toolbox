"""One draw of shaders a game compiled with FXC (DXBC) on QRhi's Direct3D 11 backend: the binding layout from the
shaders' reflection, constant buffers written by member path, textures and samplers bound by name (classic slots),
the mesh's vertices as stored, a pipeline per target.

``CFxcDraw(ctx, program, mesh, state, dummies)`` is the draw (``program``: ``gamedraw.CProgram`` of two
``models.fxstage.SStage``s); ``prepare(render_pass, signature, batch, textures, fill)`` before a pass, ``draw(command_buffer, size_px)`` in it.
``CDummies`` makes a material's missing maps; ``CScreenChain`` is the base of the games' post chains (full-screen
passes into targets of their own, each a ``screen_pass``). docs/rendering/shader-compile.md section 10.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhiBuffer,
    QRhiGraphicsPipeline,
    QRhiShaderResourceBinding,
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
)

from ..models.fxstage import SSampler, SState
from . import gameshader, mipchain
from .gamedraw import (
    BLOCK_FORMATS,
    CULL_MODES,
    CMesh,
    CProgram,
    immutable_buffer,
    new_pipeline,
    sampler_of,
    screen_triangle,
    target_blends,
    uniform_buffer,
    vertex_input,
    vertex_layout,
)
from .nativepass import draw_pass
from .renderstates import blend_factor

Binding = QRhiShaderResourceBinding
Format = QRhiTexture.Format
NO_DEPTH = SState(cull="none", depth_test=False, depth_write=False)  # a full-screen pass
_DEPTH = {"less": QRhiGraphicsPipeline.CompareOp.Less, "less_equal": QRhiGraphicsPipeline.CompareOp.LessOrEqual,
          "greater": QRhiGraphicsPipeline.CompareOp.Greater, "greater_equal": QRhiGraphicsPipeline.CompareOp.GreaterOrEqual,
          "always": QRhiGraphicsPipeline.CompareOp.Always, "equal": QRhiGraphicsPipeline.CompareOp.Equal}  # fmt: skip


_TYPED = re.compile(r"Texture(Cube|3D|2DArray|2D)(?:<\s*(float|uint|int)(\d?)\s*>)?")
_INTEGER = {("uint", ""): Format.R32UI, ("uint", "1"): Format.R32UI, ("uint", "2"): Format.RG32UI, ("uint", "4"): Format.RGBA32UI,
            ("int", ""): Format.R32SI, ("int", "1"): Format.R32SI, ("int", "2"): Format.RG32SI, ("int", "4"): Format.RGBA32SI}  # fmt: skip
_TEXEL_BYTES = {
    Format.R32UI: 4,
    Format.R32SI: 4,
    Format.RG32UI: 8,
    Format.RG32SI: 8,
    Format.RGBA32UI: 16,
    Format.RGBA32SI: 16,
    Format.RGBA8: 4,
}


def dummy_format(kind: str) -> tuple[str, QRhiTexture.Format]:
    """(dimension "2d", "3d", "cube" or "array", the QRhi format) of the stand-in for a declared texture type
    (``Texture3D<uint2>``, ``Texture2D<uint>``, ``TextureCube<float4>``, ``Texture2DArray``): a view whose dimension
    or element type differs from the shader's declaration reads undefined values."""
    match = _TYPED.match(kind)
    if match is None:
        return "2d", Format.RGBA8
    dimension = {"Cube": "cube", "3D": "3d", "2DArray": "array", "2D": "2d"}[match.group(1)]
    return dimension, _INTEGER.get((match.group(2) or "", match.group(3) or ""), Format.RGBA8)


class CDummies:
    """The stand-in textures of one device: a solid colour as a 2D texture, a cube, a 3D texture or an array of the
    declared element type, made on first use (the upload goes into the batch of the pass that first binds it)."""

    def __init__(self, rhi) -> None:
        self.m_rhi = rhi
        self._m_made: dict[tuple, object] = {}
        self.m_keep: list = []

    def get(self, kind: str, rgba: tuple[int, int, int, int], batch):
        key = (kind, tuple(rgba))
        if key in self._m_made:
            return self._m_made[key]
        dimension, texture_format = dummy_format(kind)
        texel = (
            bytes(rgba) * 4 if texture_format == Format.RGBA8 else bytes(4 * _TEXEL_BYTES[texture_format])
        )  # an integer texture reads zeros
        if dimension == "cube":
            texture = self.m_rhi.newTexture(texture_format, QSize(2, 2), 1, QRhiTexture.Flag.CubeMap)
            layers = 6
        elif dimension == "3d":
            texture = self.m_rhi.newTexture(texture_format, 2, 2, 2, 1, QRhiTexture.Flag.ThreeDimensional)
            layers = 2
        elif dimension == "array":
            texture = self.m_rhi.newTextureArray(texture_format, 2, QSize(2, 2), 1, QRhiTexture.Flag(0))
            layers = 2
        else:
            texture = self.m_rhi.newTexture(texture_format, QSize(2, 2))
            layers = 1
        entries = [QRhiTextureUploadEntry(k, 0, QRhiTextureSubresourceUploadDescription(texel)) for k in range(layers)]
        texture.create()
        upload = QRhiTextureUploadDescription()
        upload.setEntries(entries)
        batch.uploadTexture(texture, upload)
        self._m_made[key] = texture
        self.m_keep.append(texture)
        return texture

    def release(self, ctx) -> None:
        for texture in self.m_keep:
            ctx.later(texture)
        self._m_made, self.m_keep = {}, []


def _reverse_fields(rows: np.ndarray, bits: int, used: int) -> np.ndarray:
    """Rows of packed texel fields (``bits`` each, the first texel lowest) with their first ``used`` fields reversed."""
    rows = rows.astype(np.uint64)
    mask = np.uint64((1 << bits) - 1)
    out = rows & ~np.uint64((1 << bits * used) - 1)
    for i in range(used):
        out |= ((rows >> np.uint64(bits * i)) & mask) << np.uint64(bits * (used - 1 - i))
    return out


def _rows_of(blocks: np.ndarray, start: int, count: int, bits: int, used: int) -> None:
    """The texel rows of ``count`` bytes from ``start`` in each block (four rows of ``count * 2`` bits) mirrored."""
    raw = blocks[:, start : start + count].astype(np.uint64)
    word = sum(raw[:, k] << np.uint64(8 * k) for k in range(count))
    row_bits = count * 2
    rows = [(word >> np.uint64(row_bits * row_index)) & np.uint64((1 << row_bits) - 1) for row_index in range(4)]
    word = sum(
        _reverse_fields(row, bits, used) << np.uint64(row_bits * row_index) for row_index, row in enumerate(rows)
    )
    blocks[:, start : start + count] = np.stack([(word >> np.uint64(8 * k)) & np.uint64(255) for k in range(count)], 1)


# per block kind: (block bytes, its index runs as (first byte, bytes, bits per texel)); the rest are end points
_MIRRORED = {"DXT1": (8, [(4, 4, 2)]), "DXT3": (16, [(0, 8, 4), (12, 4, 2)]), "DXT5": (16, [(2, 6, 3), (12, 4, 2)]),
             "ATI1": (8, [(2, 6, 3)]), "BC4U": (8, [(2, 6, 3)]), "ATI2": (16, [(2, 6, 3), (10, 6, 3)]),
             "BC5U": (16, [(2, 6, 3), (10, 6, 3)])}  # fmt: skip


def mirrored_x(raw: bytes, kind: str, width_px: int, height_px: int) -> bytes:
    """One level of one face as stored, flipped left to right (``kind`` a ``DdsFile`` format: DXT1/3/5, BC4/5 or a
    plain layout of whole texels): the blocks reversed in their rows, each block's texel indices reversed in its rows
    (a level narrower than a block: only its texels)."""
    if kind in _MIRRORED:
        block_bytes, runs = _MIRRORED[kind]
        across = max(1, (width_px + 3) // 4)
        blocks = np.frombuffer(raw, np.uint8).reshape(-1, across, block_bytes)[:, ::-1].reshape(-1, block_bytes).copy()
        for start, count, bits in runs:
            _rows_of(blocks, start, count, bits, min(width_px, 4))
        return blocks.tobytes()
    texels = np.frombuffer(raw, np.uint8).reshape(height_px, width_px, -1)
    return np.ascontiguousarray(texels[:, ::-1]).tobytes()


@dataclass(frozen=True)
class SCubeFaces:
    """A cube's texels ready for the device: its format (None: RGBA8), size in pixels, level count and per face and
    level the bytes (``cube_faces``)."""

    texture_format: object
    size_px: tuple[int, int]
    mip_count: int
    levels: tuple  # ((face, level, bytes), ...)


def cube_faces(dds, mirrored: bool = False) -> SCubeFaces:
    """The faces of a DDS of six faces (``models.ddsdecode.CDdsFile``), every level (in the background): block-compressed
    and half-float faces as they are stored (an HDR sky cube keeps its range), the rest as RGBA8. ``mirrored``: as
    seen in the view's axes, the game's mirrored in x (the +X and -X faces swapped, every face flipped:
    shader-compile.md 11 "Sky cubes"); a block format without a flip of its own is decoded to RGBA8."""
    header = dds.m_info
    texture_format = BLOCK_FORMATS.get(header.format) or (Format.RGBA16F if header.format == "RGBA16F" else None)
    if mirrored and texture_format is not None and header.format != "RGBA16F" and header.format not in _MIRRORED:
        texture_format = None
    levels = []
    for face in range(6):
        source = (1, 0, 2, 3, 4, 5)[face] if mirrored else face
        own_levels = []
        for level in range(header.mips):
            raw = (
                dds.stored(level, source)
                if texture_format
                else np.ascontiguousarray(dds.level(level, source)).tobytes()
            )
            if mirrored:
                raw = mirrored_x(raw, header.format if texture_format else "RGBA8", *dds.size_of(level))
            own_levels.append(raw)
        levels += [(face, level, raw) for level, raw in enumerate(full_chain(header, texture_format, own_levels))]
    return SCubeFaces(texture_format, (header.width_px, header.height_px), header.mips, tuple(levels))


def full_chain(header, texture_format, levels: list[bytes]) -> list[bytes]:
    """One face's ``levels`` (``texture_format``: its QRhi format, None for RGBA8) and those its file stops short of, down to
    1 x 1 (``mipchain``); a plain face is RGBA here whatever its file's order of channels was."""
    if header.mips <= 1:
        return levels
    if texture_format is not None and texture_format != Format.RGBA16F:
        return mipchain.full_levels(header, levels)
    # an HDR sky cube keeps its range
    return mipchain.plain_levels(
        levels, (header.width_px, header.height_px), "<f2" if texture_format == Format.RGBA16F else "u1"
    )


def mirrored_turn(turn) -> np.ndarray:
    """The game's ``turn`` (3x3 or 4x4) of a lookup into a map uploaded mirrored (``cube_faces(..., True)``, 2070's
    noise volume) as the view's axes need it: ``M T M``, ``M`` the mirror in x, right for ``mul(T, v)`` and
    ``mul(v, T)`` alike (shader-compile.md 11 "Sky cubes")."""
    turn_matrix = np.asarray(turn, float)
    mirror = np.diag([-1.0, 1.0, 1.0, 1.0][: len(turn_matrix)])
    return mirror @ turn_matrix @ mirror


def cube_of(ctx, faces: SCubeFaces) -> QRhiTexture:
    """A cube map of ``cube_faces``' texels, uploaded with the next pass."""
    flags = QRhiTexture.Flag.CubeMap | (QRhiTexture.Flag.MipMapped if faces.mip_count > 1 else QRhiTexture.Flag(0))
    texture = ctx.m_rhi.newTexture(faces.texture_format or Format.RGBA8, QSize(*faces.size_px), 1, flags)
    texture.create()
    entries = [QRhiTextureUploadEntry(face, level, QRhiTextureSubresourceUploadDescription(raw))
               for face, level, raw in faces.levels]  # fmt: skip
    upload = QRhiTextureUploadDescription()
    upload.setEntries(entries)
    ctx._upload(texture, lambda batch: batch.uploadTexture(texture, upload))
    return texture


def volume_of(ctx, slices: list[np.ndarray], texture_format=Format.RGBA8) -> QRhiTexture:
    """A 3D texture of ``slices`` (each ``(height, width, ...)`` texels already in ``fmt``'s layout: RGBA bytes,
    half floats), one level, uploaded with the next pass."""
    height_px, width_px = slices[0].shape[:2]
    texture = ctx.m_rhi.newTexture(
        texture_format, width_px, height_px, len(slices), 1, QRhiTexture.Flag.ThreeDimensional
    )
    texture.create()
    entries = [QRhiTextureUploadEntry(z, 0, QRhiTextureSubresourceUploadDescription(np.ascontiguousarray(texture_slice).tobytes()))
               for z, texture_slice in enumerate(slices)]  # fmt: skip
    upload = QRhiTextureUploadDescription()
    upload.setEntries(entries)
    ctx._upload(texture, lambda batch: batch.uploadTexture(texture, upload))
    return texture


def switched(holder, name: str, is_on: bool, make: Callable[[], object] | None = None):
    """A frame part kept as ``holder.<name>`` by View > Rendering's switch: made by ``make`` once it is on (``make``
    None: not compiled yet), released and None while it is off; the part, or None."""
    part = getattr(holder, name)
    if is_on and part is None and make is not None:
        part = make()
        setattr(holder, name, part)
    elif not is_on and part is not None:
        part.release()
        part = None
        setattr(holder, name, None)
    return part


Fill = Callable[[dict], None]  # writes this frame's values into the draw's constant buffers by name


# the forward games' constant buffers that hold the frame's values alone (the camera, the sun, the shadow, the
# lights), the same for every draw of a pass; the models' and materials' stay each draw's own
FRAME_BUFFERS = frozenset({"cbPerFrame", "cbCamera", "cbScattering", "cbShadow", "cbShadowPartition", "cbPointLights",
                           "cbSpotLights", "cbPerContext", "cbAmbientOcclusion", "cbPerRenderTarget"})  # fmt: skip


def layout_key(reflection) -> tuple:
    """What a constant buffer shared by several programs must agree on: its name, size and members' places."""
    return (
        reflection.name,
        reflection.size,
        tuple((variable.name, variable.offset) for variable in reflection.variables),
    )


def same_objects(first: list, second: list) -> bool:
    """Whether two lists hold the same objects in order (a tuple by its value: a buffer's slot). What a binding set
    was made with is kept and compared so: the id of a buffer or texture let go is given again to the next one made."""
    return len(first) == len(second) and all(
        x is y or (isinstance(x, tuple) and x == y) for x, y in zip(first, second, strict=True)
    )


class CSharedBuffers:
    """One pass's frame buffers (``FRAME_BUFFERS``), shared by every draw that binds one of the same layout: filled
    once a frame (``begin``), by the first draw that binds it (each draw wrote the frame's values into its own copy:
    most of a frame's time with many draws)."""

    def __init__(self, ctx) -> None:
        self.m_ctx, self.m_frame, self.m_entries = ctx, 0, {}
        self._m_keys: dict[int, tuple] = {}  # id of a reflection -> (it, its ``layout_key``), worked out once

    def begin(self) -> None:
        """Start a frame: the first draw that binds a buffer fills it."""
        self.m_frame += 1

    def take(self, reflection) -> tuple:
        """(its ``CConstants``, its buffer, whether this draw is the first to bind it this frame)."""
        kept = self._m_keys.get(id(reflection))  # pattern: ok (the entry holds the reflection: its id stays its own)
        if kept is None or kept[0] is not reflection:
            kept = self._m_keys[id(reflection)] = (reflection, layout_key(reflection))
        entry = self.m_entries.get(kept[1])
        if entry is None:
            constants = gameshader.CConstants(reflection)
            entry = self.m_entries[kept[1]] = [constants, uniform_buffer(self.m_ctx.m_rhi, len(constants.m_data)), 0]
        due, entry[2] = entry[2] != self.m_frame, self.m_frame
        return entry[0], entry[1], due

    def release(self) -> None:
        for _constants, buffer, _frame in self.m_entries.values():
            self.m_ctx.later(buffer)
        self.m_entries, self._m_keys = {}, {}


class CFxcDraw:
    """The shaders of ``program`` drawing ``mesh`` in the states ``state`` (module docstring). With ``shared`` (a
    pass's ``CSharedBuffers``) its frame buffers are the pass's, written by the first draw of a frame."""

    def __init__(self, ctx, program: CProgram, mesh: CMesh, state: SState, dummies: CDummies) -> None:
        self.m_ctx, self.m_rhi, self.m_program, self.m_mesh, self.m_state = ctx, ctx.m_rhi, program, mesh, state
        self.m_dummies = dummies
        self.m_shared: CSharedBuffers | None = None
        self._m_bound_buffers: list = []  # the buffers its bindings were made with
        self.m_is_mirrored = False  # the meshes are drawn mirrored (the viewer's x flip): front and back faces swap
        self.m_fallbacks: dict[str, tuple] = {}  # a texture's stand-in colour by name (white without one)
        self.m_instances = 1  # how many copies one draw makes (particles: one per particle of the range)
        self.m_keep: list = []
        self.m_pipeline = self.m_srb = None
        self.m_constants: dict[str, gameshader.CConstants] = {}
        self.m_buffers: dict[str, object] = {}
        self._m_bound: dict[str, object] = {}  # texture name -> the caller's texture its binding set holds
        self._m_samplers: dict[str, object] = {}
        self.m_uploaded = 0  # the mesh version its vertex buffer holds
        self.m_error = ""

    def _vertex_resources(self, batch) -> None:
        """Make the vertex and index buffers of the mesh and keep them."""
        fill, self.m_attrs = vertex_input(self.m_mesh, self.m_program.m_vertex_stage.inputs)
        self.m_fill_stride = len(fill) // max(1, self.m_mesh.m_count)
        vertex = QRhiBuffer.UsageFlag.VertexBuffer
        self.m_vbufs = [immutable_buffer(self.m_rhi, batch, vertex, data) for data in (self.m_mesh.m_data, fill)]
        self.m_uploaded = self.m_mesh.m_version
        indices = self.m_mesh.m_indices.astype("<u4").tobytes()
        self.m_ibuf = immutable_buffer(self.m_rhi, batch, QRhiBuffer.UsageFlag.IndexBuffer, indices)
        self.m_keep += [*self.m_vbufs, self.m_ibuf]

    def _constant_buffers(self) -> None:
        """Make a constant buffer for each of the program's buffers that no shared pass buffer stands in for."""
        for name, reflection in self.m_program.m_cbuffers.items():
            if self.m_shared is not None and name in FRAME_BUFFERS:
                continue
            self.m_constants[name] = gameshader.CConstants(reflection)
            buffer = self.m_buffers[name] = uniform_buffer(self.m_rhi, len(self.m_constants[name].m_data))
            self.m_keep.append(buffer)

    def _sampler(self, name: str):
        """The sampler of that name, made once from the shader's declaration."""
        if name not in self._m_samplers:
            desc = (
                self.m_program.m_vertex_stage.samplers.get(name)
                or self.m_program.m_pixel_stage.samplers.get(name)
                or SSampler()
            )
            self._m_samplers[name] = sampler_of(self.m_rhi, desc)
            self.m_keep.append(self._m_samplers[name])
        return self._m_samplers[name]

    def _texture(self, name: str, textures: dict, batch):
        """The texture bound to ``name``: the caller's, else a stand-in of its declared kind (a map the material
        lacks reads as white)."""
        texture = textures.get(name)
        if texture is not None:
            return texture
        kind = (
            self.m_program.m_pixel_stage.types.get(name) or self.m_program.m_vertex_stage.types.get(name) or "Texture2D"
        )
        return self.m_dummies.get(kind, self.m_fallbacks.get(name, (255, 255, 255, 255)), batch)

    def _bindings(self, textures: dict, batch) -> list:
        """The shader resource bindings of the program: its constant buffers, samplers and textures."""
        out = []
        for (name, kind), number in self.m_program.m_numbers.items():
            stages = self.m_program.m_stages[(name, kind)]
            if kind == "cbuffer":
                out.append(self._cbuffer(name, number, stages))
            elif kind == "sampler":
                out.append(Binding.sampler(number, stages, self._sampler(name)))
            elif kind == "texture":
                texture = self._texture(name, textures, batch)
                self._m_bound[name] = textures.get(name)  # the caller's (None: a stand-in), so dropping one rebinds
                out.append(Binding.texture(number, stages, texture))
        return out

    def _cbuffer(self, name: str, number: int, stages):
        """A constant buffer's binding (a subclass binds one by a dynamic offset: ``objects1800.CObjectDraw``)."""
        return Binding.uniformBuffer(number, stages, self.m_buffers[name])

    def _make_srb(self, textures: dict, batch) -> None:
        srb = self.m_rhi.newShaderResourceBindings()
        srb.setBindings(self._bindings(textures, batch))
        srb.create()
        self.m_ctx.later(self.m_srb)  # the frame in flight may still bind it
        self.m_srb = srb

    def _make_pipeline(self, render_pass, signature) -> None:
        targets, samples = signature
        layout = vertex_layout([self.m_mesh.m_stride, max(4, self.m_fill_stride)], self.m_attrs)
        pipeline_object = new_pipeline(
            self.m_rhi, self.m_program, layout, self.m_srb, render_pass, samples, self._blends(targets)
        )
        pipeline_object.setDepthTest(self.m_state.depth_test)
        pipeline_object.setDepthWrite(self.m_state.depth_write)
        pipeline_object.setDepthOp(_DEPTH.get(self.m_state.depth_func, QRhiGraphicsPipeline.CompareOp.LessOrEqual))
        cull = (
            {"back": "front", "front": "back"}.get(self.m_state.cull, self.m_state.cull)
            if self.m_is_mirrored
            else self.m_state.cull
        )
        pipeline_object.setCullMode(CULL_MODES.get(cull, QRhiGraphicsPipeline.CullMode.Back))
        pipeline_object.setDepthBias(self.m_state.depth_bias)
        pipeline_object.setSlopeScaledDepthBias(self.m_state.slope_bias)
        if not pipeline_object.create():
            self.m_error = "the backend refused the game shader's pipeline"
            pipeline_object = None
        self.m_pipeline = pipeline_object
        self.m_keep.append(pipeline_object)

    def _blends(self, targets: int) -> list:
        """The state's blend, the same for every colour target, and each target's write mask."""
        state = self.m_state
        one = (blend_factor(state.source, "ONE"), blend_factor(state.dest, "ZERO"),
               blend_factor(state.source_alpha or state.source, "ONE"), blend_factor(state.dest_alpha or state.dest, "ZERO"))  # fmt: skip
        out = target_blends((one,) * targets if state.blend else (), targets)
        for target_blend, mask in zip(out, state.writes, strict=False):
            if mask is not None:  # QRhi's mask bits are D3D's
                target_blend.colorWrite = QRhiGraphicsPipeline.ColorMaskComponent(mask)
        return out

    def prepare(self, render_pass, signature, batch, textures: dict, fill: Fill) -> None:
        """Make what is missing and write this frame's constants into ``batch`` (before the pass begins);
        ``textures``: name -> ``QRhiTexture``; ``fill`` sets the buffers' values by name."""
        if not self.m_keep:
            self._vertex_resources(batch)
            self._constant_buffers()
        elif self.m_uploaded != self.m_mesh.m_version:  # a cloth moved (``render.clothmesh``)
            batch.uploadStaticBuffer(self.m_vbufs[0], self.m_mesh.m_data)
            self.m_uploaded = self.m_mesh.m_version
        due = self._shared_due()
        bound = [self.m_buffers.get(cbuffer_name) for cbuffer_name in self.m_program.m_cbuffers]
        if self.m_srb is None or not same_objects(bound, self._m_bound_buffers) or self._maps_changed(textures):
            self._make_srb(textures, batch)
            self._m_bound_buffers = bound
        if self.m_pipeline is None and not self.m_error:
            self._make_pipeline(render_pass, signature)
        written = {**due, **self.m_constants}
        fill(written)
        for name, constants in written.items():
            batch.updateDynamicBuffer(self.m_buffers[name], 0, bytes(constants.m_data))

    def _maps_changed(self, textures: dict) -> bool:
        """Whether a texture the binding set holds is no longer the caller's of that name."""
        return any(
            self._m_bound.get(name) is not textures.get(name)
            for (name, kind) in self.m_program.m_numbers
            if kind == "texture"
        )

    def _shared_due(self) -> dict:
        """The pass's frame buffers bound into ``buffers`` (a draw without ``shared``: its own, made now), those this
        draw is the first to bind this frame by name (``CConstants``)."""
        due = {}
        for name, reflection in self.m_program.m_cbuffers.items():
            if name in self.m_constants:
                continue
            if self.m_shared is None or name not in FRAME_BUFFERS:
                self.m_constants[name] = gameshader.CConstants(reflection)
                self.m_buffers[name] = uniform_buffer(self.m_rhi, len(self.m_constants[name].m_data))
                self.m_keep.append(self.m_buffers[name])
                continue
            constants, self.m_buffers[name], first = self.m_shared.take(reflection)
            if first:
                due[name] = constants
        return due

    def draw(self, command_buffer, size_px: tuple[int, int]) -> None:
        """Record the draw into the command buffer ``command_buffer`` of a pass over a target of ``size_px`` pixels."""
        if self.m_pipeline is not None:
            self.m_mesh.draw(
                command_buffer, self.m_pipeline, self.m_srb, size_px, (self.m_vbufs, self.m_ibuf), self.m_instances
            )

    def release(self) -> None:
        """Hand the resources over for destruction one frame later (the frame being recorded may still use them);
        a later ``prepare`` makes all of them anew, the samplers and the constant buffers too."""
        for resource in [*self.m_keep, self.m_srb]:
            self.m_ctx.later(resource)
        self.m_keep, self.m_pipeline, self.m_srb = [], None, None
        self.m_constants, self.m_buffers, self._m_bound, self._m_samplers = {}, {}, {}, {}
        self._m_bound_buffers = []

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        """The mesh's bounding box."""
        return self.m_mesh.box()


def screen_pass(ctx, draw: CFxcDraw, framebuffer, maps: dict, values: dict, size_px: tuple[int, int]) -> None:
    """One full-screen ``draw`` into ``framebuffer`` (cleared first), its textures and constants by name."""

    def prepare(render_pass, signature, batch) -> None:
        draw.prepare(
            render_pass, signature, batch, maps, lambda constants: gameshader.set_values(constants.values(), values)
        )

    draw_pass(ctx, framebuffer, prepare, lambda command_buffer: draw.draw(command_buffer, size_px))


class CScreenChain:
    """Full-screen passes of one game's post effects (``programs``: pass name -> ``CProgram``), each drawn over a
    screen triangle into a half-float target of its own: what 2205's and 1800's chain (``postforward``) and 2070's
    and 1404's sequence (``post2070``) share; they say the order, the inputs and the values."""

    def __init__(self, ctx, programs: dict, dummies: CDummies) -> None:
        self.m_ctx = ctx
        mesh = screen_triangle()
        self.m_draws = {name: CFxcDraw(ctx, program, mesh, NO_DEPTH, dummies) for name, program in programs.items()}
        self.m_size_px = None
        self.m_targets: dict = {}  # name -> (texture, framebuffer, its size)

    def ensure_targets(self, size_px: tuple[int, int], divisors: dict) -> None:
        """A target per name of ``divisors`` at the picture's ``size`` divided by its divisor, made again when the
        picture's size changes."""
        if self.m_size_px == size_px:
            return
        self.release_targets()
        for name, divisor in divisors.items():
            width_px, height_px = max(1, size_px[0] // divisor), max(1, size_px[1] // divisor)
            texture = self.m_ctx.native_texture((width_px, height_px), QRhiTexture.Format.RGBA16F)
            self.m_targets[name] = (texture, self.m_ctx.framebuffer([texture]), (width_px, height_px))
        self.m_size_px = size_px

    def target_size(self, name: str) -> tuple[int, int]:
        """The size in pixels of the target of the pass ``name``."""
        return self.m_targets[name][2]

    def run(self, name: str, maps: dict, values: dict):
        """The pass ``name`` into its target, its textures and constants by name; returns the target."""
        texture, framebuffer, size_px = self.m_targets[name]
        screen_pass(self.m_ctx, self.m_draws[name], framebuffer, maps, values, size_px)
        return texture

    def release_targets(self) -> None:
        """Hand the targets over for destruction and forget them."""
        for texture, framebuffer, _size in self.m_targets.values():
            framebuffer.release()
            texture.release()
        self.m_targets, self.m_size_px = {}, None

    def release(self) -> None:
        self.release_targets()
        for draw in self.m_draws.values():
            draw.release()
