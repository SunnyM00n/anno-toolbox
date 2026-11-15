"""One draw of shaders a game compiled with FXC (DXBC) on QRhi's Direct3D 11 backend: the binding layout from the
shaders' reflection, constant buffers written by member path, textures and samplers bound by name (classic slots),
the mesh's vertices as stored, a pipeline per target.

``FxcDraw(ctx, program, mesh, state, dummies)`` is the draw (``program``: ``gamedraw.Program`` of two
``models.fxstage.Stage``s); ``prepare(rp, signature, batch, textures, fill)`` before a pass, ``draw(cb, size)`` in it.
``Dummies`` makes a material's missing maps; ``ScreenChain`` is the base of the games' post chains (full-screen
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

from ..models.fxstage import Sampler, State
from . import gameshader as gs
from . import mipchain
from .gamedraw import (
    BLOCK_FORMATS,
    CULL_MODES,
    Mesh,
    Program,
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

B = QRhiShaderResourceBinding
F = QRhiTexture.Format
NO_DEPTH = State(cull="none", depth_test=False, depth_write=False)  # a full-screen pass
_DEPTH = {"less": QRhiGraphicsPipeline.CompareOp.Less, "less_equal": QRhiGraphicsPipeline.CompareOp.LessOrEqual,
          "greater": QRhiGraphicsPipeline.CompareOp.Greater, "greater_equal": QRhiGraphicsPipeline.CompareOp.GreaterOrEqual,
          "always": QRhiGraphicsPipeline.CompareOp.Always, "equal": QRhiGraphicsPipeline.CompareOp.Equal}  # fmt: skip


_TYPED = re.compile(r"Texture(Cube|3D|2DArray|2D)(?:<\s*(float|uint|int)(\d?)\s*>)?")
_INTEGER = {("uint", ""): F.R32UI, ("uint", "1"): F.R32UI, ("uint", "2"): F.RG32UI, ("uint", "4"): F.RGBA32UI,
            ("int", ""): F.R32SI, ("int", "1"): F.R32SI, ("int", "2"): F.RG32SI, ("int", "4"): F.RGBA32SI}  # fmt: skip
_TEXEL_BYTES = {F.R32UI: 4, F.R32SI: 4, F.RG32UI: 8, F.RG32SI: 8, F.RGBA32UI: 16, F.RGBA32SI: 16, F.RGBA8: 4}


def dummy_format(kind: str) -> tuple[str, QRhiTexture.Format]:
    """(dimension "2d", "3d", "cube" or "array", the QRhi format) of the stand-in for a declared texture type
    (``Texture3D<uint2>``, ``Texture2D<uint>``, ``TextureCube<float4>``, ``Texture2DArray``): a view whose dimension
    or element type differs from the shader's declaration reads undefined values."""
    m = _TYPED.match(kind)
    if m is None:
        return "2d", F.RGBA8
    dimension = {"Cube": "cube", "3D": "3d", "2DArray": "array", "2D": "2d"}[m.group(1)]
    return dimension, _INTEGER.get((m.group(2) or "", m.group(3) or ""), F.RGBA8)


class Dummies:
    """The stand-in textures of one device: a solid colour as a 2D texture, a cube, a 3D texture or an array of the
    declared element type, made on first use (the upload goes into the batch of the pass that first binds it)."""

    def __init__(self, rhi) -> None:
        self.rhi = rhi
        self._made: dict[tuple, object] = {}
        self.keep: list = []

    def get(self, kind: str, rgba: tuple[int, int, int, int], batch):
        key = (kind, tuple(rgba))
        if key in self._made:
            return self._made[key]
        dimension, fmt = dummy_format(kind)
        px = bytes(rgba) * 4 if fmt == F.RGBA8 else bytes(4 * _TEXEL_BYTES[fmt])  # an integer texture reads zeros
        if dimension == "cube":
            tex = self.rhi.newTexture(fmt, QSize(2, 2), 1, QRhiTexture.Flag.CubeMap)
            layers = 6
        elif dimension == "3d":
            tex = self.rhi.newTexture(fmt, 2, 2, 2, 1, QRhiTexture.Flag.ThreeDimensional)
            layers = 2
        elif dimension == "array":
            tex = self.rhi.newTextureArray(fmt, 2, QSize(2, 2), 1, QRhiTexture.Flag(0))
            layers = 2
        else:
            tex = self.rhi.newTexture(fmt, QSize(2, 2))
            layers = 1
        entries = [QRhiTextureUploadEntry(k, 0, QRhiTextureSubresourceUploadDescription(px)) for k in range(layers)]
        tex.create()
        desc = QRhiTextureUploadDescription()
        desc.setEntries(entries)
        batch.uploadTexture(tex, desc)
        self._made[key] = tex
        self.keep.append(tex)
        return tex

    def release(self, ctx) -> None:
        for tex in self.keep:
            ctx.later(tex)
        self._made, self.keep = {}, []


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
    rows = [(word >> np.uint64(row_bits * r)) & np.uint64((1 << row_bits) - 1) for r in range(4)]
    word = sum(_reverse_fields(row, bits, used) << np.uint64(row_bits * r) for r, row in enumerate(rows))
    blocks[:, start : start + count] = np.stack([(word >> np.uint64(8 * k)) & np.uint64(255) for k in range(count)], 1)


# per block kind: (block bytes, its index runs as (first byte, bytes, bits per texel)); the rest are end points
_MIRRORED = {"DXT1": (8, [(4, 4, 2)]), "DXT3": (16, [(0, 8, 4), (12, 4, 2)]), "DXT5": (16, [(2, 6, 3), (12, 4, 2)]),
             "ATI1": (8, [(2, 6, 3)]), "BC4U": (8, [(2, 6, 3)]), "ATI2": (16, [(2, 6, 3), (10, 6, 3)]),
             "BC5U": (16, [(2, 6, 3), (10, 6, 3)])}  # fmt: skip


def mirrored_x(raw: bytes, kind: str, width: int, height: int) -> bytes:
    """One level of one face as stored, flipped left to right (``kind`` a ``DdsFile`` format: DXT1/3/5, BC4/5 or a
    plain layout of whole texels): the blocks reversed in their rows, each block's texel indices reversed in its rows
    (a level narrower than a block: only its texels)."""
    if kind in _MIRRORED:
        size, runs = _MIRRORED[kind]
        across = max(1, (width + 3) // 4)
        blocks = np.frombuffer(raw, np.uint8).reshape(-1, across, size)[:, ::-1].reshape(-1, size).copy()
        for start, count, bits in runs:
            _rows_of(blocks, start, count, bits, min(width, 4))
        return blocks.tobytes()
    texels = np.frombuffer(raw, np.uint8).reshape(height, width, -1)
    return np.ascontiguousarray(texels[:, ::-1]).tobytes()


@dataclass(frozen=True)
class CubeFaces:
    """A cube's texels ready for the device: its format (None: RGBA8), size, level count and per face and level the
    bytes (``cube_faces``)."""

    format: object
    size: tuple[int, int]
    mips: int
    levels: tuple  # ((face, level, bytes), ...)


def cube_faces(dds, mirrored: bool = False) -> CubeFaces:
    """The faces of a DDS of six faces (``models.ddsdecode.DdsFile``), every level (in the background): block-compressed
    and half-float faces as they are stored (an HDR sky cube keeps its range), the rest as RGBA8. ``mirrored``: as
    seen in the view's axes, the game's mirrored in x (the +X and -X faces swapped, every face flipped:
    shader-compile.md 11 "Sky cubes"); a block format without a flip of its own is decoded to RGBA8."""
    info = dds.info
    fmt = BLOCK_FORMATS.get(info.format) or (F.RGBA16F if info.format == "RGBA16F" else None)
    if mirrored and fmt is not None and info.format != "RGBA16F" and info.format not in _MIRRORED:
        fmt = None
    levels = []
    for face in range(6):
        source = (1, 0, 2, 3, 4, 5)[face] if mirrored else face
        own = []
        for level in range(info.mips):
            raw = dds.stored(level, source) if fmt else np.ascontiguousarray(dds.level(level, source)).tobytes()
            if mirrored:
                raw = mirrored_x(raw, info.format if fmt else "RGBA8", *dds.size_of(level))
            own.append(raw)
        levels += [(face, level, raw) for level, raw in enumerate(full_chain(info, fmt, own))]
    return CubeFaces(fmt, (info.width, info.height), info.mips, tuple(levels))


def full_chain(info, fmt, levels: list[bytes]) -> list[bytes]:
    """One face's ``levels`` (``fmt``: its QRhi format, None for RGBA8) and those its file stops short of, down to
    1 x 1 (``mipchain``); a plain face is RGBA here whatever its file's order of channels was."""
    if info.mips <= 1:
        return levels
    if fmt is not None and fmt != F.RGBA16F:
        return mipchain.full_levels(info, levels)
    # an HDR sky cube keeps its range
    return mipchain.plain_levels(levels, (info.width, info.height), "<f2" if fmt == F.RGBA16F else "u1")


def mirrored_turn(turn) -> np.ndarray:
    """The game's ``turn`` (3x3 or 4x4) of a lookup into a map uploaded mirrored (``cube_faces(..., True)``, 2070's
    noise volume) as the view's axes need it: ``M T M``, ``M`` the mirror in x, right for ``mul(T, v)`` and
    ``mul(v, T)`` alike (shader-compile.md 11 "Sky cubes")."""
    t = np.asarray(turn, float)
    m = np.diag([-1.0, 1.0, 1.0, 1.0][: len(t)])
    return m @ t @ m


def cube_of(ctx, faces: CubeFaces) -> QRhiTexture:
    """A cube map of ``cube_faces``' texels, uploaded with the next pass."""
    flags = QRhiTexture.Flag.CubeMap | (QRhiTexture.Flag.MipMapped if faces.mips > 1 else QRhiTexture.Flag(0))
    tex = ctx.rhi.newTexture(faces.format or F.RGBA8, QSize(*faces.size), 1, flags)
    tex.create()
    entries = [QRhiTextureUploadEntry(face, level, QRhiTextureSubresourceUploadDescription(raw))
               for face, level, raw in faces.levels]  # fmt: skip
    desc = QRhiTextureUploadDescription()
    desc.setEntries(entries)
    ctx._upload(tex, lambda u: u.uploadTexture(tex, desc))
    return tex


def volume_of(ctx, slices: list[np.ndarray], fmt=F.RGBA8) -> QRhiTexture:
    """A 3D texture of ``slices`` (each ``(height, width, ...)`` texels already in ``fmt``'s layout: RGBA bytes,
    half floats), one level, uploaded with the next pass."""
    h, w = slices[0].shape[:2]
    tex = ctx.rhi.newTexture(fmt, w, h, len(slices), 1, QRhiTexture.Flag.ThreeDimensional)
    tex.create()
    entries = [QRhiTextureUploadEntry(z, 0, QRhiTextureSubresourceUploadDescription(np.ascontiguousarray(s).tobytes()))
               for z, s in enumerate(slices)]  # fmt: skip
    desc = QRhiTextureUploadDescription()
    desc.setEntries(entries)
    ctx._upload(tex, lambda u: u.uploadTexture(tex, desc))
    return tex


def switched(holder, name: str, on: bool, make: Callable[[], object] | None = None):
    """A frame part kept as ``holder.<name>`` by View > Rendering's switch: made by ``make`` once it is on (``make``
    None: not compiled yet), released and None while it is off; the part, or None."""
    part = getattr(holder, name)
    if on and part is None and make is not None:
        part = make()
        setattr(holder, name, part)
    elif not on and part is not None:
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
    return reflection.name, reflection.size, tuple((v.name, v.offset) for v in reflection.variables)


class SharedBuffers:
    """One pass's frame buffers (``FRAME_BUFFERS``), shared by every draw that binds one of the same layout: filled
    once a frame (``begin``), by the first draw that binds it (each draw wrote the frame's values into its own copy:
    most of a frame's time with many draws)."""

    def __init__(self, ctx) -> None:
        self.ctx, self.frame, self.entries = ctx, 0, {}
        self._keys: dict[int, tuple] = {}  # id of a reflection -> (it, its ``layout_key``), worked out once

    def begin(self) -> None:
        self.frame += 1

    def take(self, reflection) -> tuple:
        """(its ``Constants``, its buffer, whether this draw is the first to bind it this frame)."""
        kept = self._keys.get(id(reflection))
        if kept is None or kept[0] is not reflection:
            kept = self._keys[id(reflection)] = (reflection, layout_key(reflection))
        entry = self.entries.get(kept[1])
        if entry is None:
            c = gs.Constants(reflection)
            entry = self.entries[kept[1]] = [c, uniform_buffer(self.ctx.rhi, len(c.data)), 0]
        due, entry[2] = entry[2] != self.frame, self.frame
        return entry[0], entry[1], due

    def release(self) -> None:
        for _c, buf, _frame in self.entries.values():
            self.ctx.later(buf)
        self.entries, self._keys = {}, {}


class FxcDraw:
    """The shaders of ``program`` drawing ``mesh`` in the states ``state`` (module docstring). With ``shared`` (a
    pass's ``SharedBuffers``) its frame buffers are the pass's, written by the first draw of a frame."""

    def __init__(self, ctx, program: Program, mesh: Mesh, state: State, dummies: Dummies) -> None:
        self.ctx, self.rhi, self.program, self.mesh, self.state = ctx, ctx.rhi, program, mesh, state
        self.dummies = dummies
        self.shared: SharedBuffers | None = None
        self._bound_buffers: list = []  # the buffers its bindings were made with
        self.mirrored = False  # the meshes are drawn mirrored (the viewer's x flip): front and back faces swap
        self.fallbacks: dict[str, tuple] = {}  # a texture's stand-in colour by name (white without one)
        self.instances = 1  # how many copies one draw makes (particles: one per particle of the range)
        self.keep: list = []
        self.pipeline = self.srb = None
        self.constants: dict[str, gs.Constants] = {}
        self.buffers: dict[str, object] = {}
        self._bound: dict[str, int] = {}
        self._samplers: dict[str, object] = {}
        self.uploaded = 0  # the mesh version its vertex buffer holds
        self.error = ""

    def _vertex_resources(self, batch) -> None:
        fill, self.attrs = vertex_input(self.mesh, self.program.vs.inputs)
        self.fill_stride = len(fill) // max(1, self.mesh.count)
        vertex = QRhiBuffer.UsageFlag.VertexBuffer
        self.vbufs = [immutable_buffer(self.rhi, batch, vertex, data) for data in (self.mesh.data, fill)]
        self.uploaded = self.mesh.version
        indices = self.mesh.indices.astype("<u4").tobytes()
        self.ibuf = immutable_buffer(self.rhi, batch, QRhiBuffer.UsageFlag.IndexBuffer, indices)
        self.keep += [*self.vbufs, self.ibuf]

    def _constant_buffers(self) -> None:
        for name, cb in self.program.cbuffers.items():
            if self.shared is not None and name in FRAME_BUFFERS:
                continue
            self.constants[name] = gs.Constants(cb)
            buf = self.buffers[name] = uniform_buffer(self.rhi, len(self.constants[name].data))
            self.keep.append(buf)

    def _sampler(self, name: str):
        if name not in self._samplers:
            desc = self.program.vs.samplers.get(name) or self.program.ps.samplers.get(name) or Sampler()
            self._samplers[name] = sampler_of(self.rhi, desc)
            self.keep.append(self._samplers[name])
        return self._samplers[name]

    def _texture(self, name: str, textures: dict, batch):
        """The texture bound to ``name``: the caller's, else a stand-in of its declared kind (a map the material
        lacks reads as white)."""
        tex = textures.get(name)
        if tex is not None:
            return tex
        kind = self.program.ps.types.get(name) or self.program.vs.types.get(name) or "Texture2D"
        return self.dummies.get(kind, self.fallbacks.get(name, (255, 255, 255, 255)), batch)

    def _bindings(self, textures: dict, batch) -> list:
        out = []
        for (name, kind), number in self.program.numbers.items():
            st = self.program.stages[(name, kind)]
            if kind == "cbuffer":
                out.append(self._cbuffer(name, number, st))
            elif kind == "sampler":
                out.append(B.sampler(number, st, self._sampler(name)))
            elif kind == "texture":
                tex = self._texture(name, textures, batch)
                self._bound[name] = id(textures.get(name))  # the caller's (None: a stand-in), so dropping one rebinds
                out.append(B.texture(number, st, tex))
        return out

    def _cbuffer(self, name: str, number: int, stages):
        """A constant buffer's binding (a subclass binds one by a dynamic offset: ``objects1800.ObjectDraw``)."""
        return B.uniformBuffer(number, stages, self.buffers[name])

    def _make_srb(self, textures: dict, batch) -> None:
        srb = self.rhi.newShaderResourceBindings()
        srb.setBindings(self._bindings(textures, batch))
        srb.create()
        self.ctx.later(self.srb)  # the frame in flight may still bind it
        self.srb = srb

    def _make_pipeline(self, rp, signature) -> None:
        targets, samples = signature
        layout = vertex_layout([self.mesh.stride, max(4, self.fill_stride)], self.attrs)
        ps = new_pipeline(self.rhi, self.program, layout, self.srb, rp, samples, self._blends(targets))
        ps.setDepthTest(self.state.depth_test)
        ps.setDepthWrite(self.state.depth_write)
        ps.setDepthOp(_DEPTH.get(self.state.depth_func, QRhiGraphicsPipeline.CompareOp.LessOrEqual))
        cull = (
            {"back": "front", "front": "back"}.get(self.state.cull, self.state.cull)
            if self.mirrored
            else self.state.cull
        )
        ps.setCullMode(CULL_MODES.get(cull, QRhiGraphicsPipeline.CullMode.Back))
        ps.setDepthBias(self.state.depth_bias)
        ps.setSlopeScaledDepthBias(self.state.slope_bias)
        if not ps.create():
            self.error = "the backend refused the game shader's pipeline"
            ps = None
        self.pipeline = ps
        self.keep.append(ps)

    def _blends(self, targets: int) -> list:
        """The state's blend, the same for every colour target, and each target's write mask."""
        s = self.state
        one = (blend_factor(s.source, "ONE"), blend_factor(s.dest, "ZERO"),
               blend_factor(s.source_alpha or s.source, "ONE"), blend_factor(s.dest_alpha or s.dest, "ZERO"))  # fmt: skip
        out = target_blends((one,) * targets if s.blend else (), targets)
        for tb, mask in zip(out, s.writes, strict=False):
            if mask is not None:  # QRhi's mask bits are D3D's
                tb.colorWrite = QRhiGraphicsPipeline.ColorMaskComponent(mask)
        return out

    def prepare(self, rp, signature, batch, textures: dict, fill: Fill) -> None:
        """Make what is missing and write this frame's constants into ``batch`` (before the pass begins);
        ``textures``: name -> ``QRhiTexture``; ``fill`` sets the buffers' values by name."""
        if not self.keep:
            self._vertex_resources(batch)
            self._constant_buffers()
        elif self.uploaded != self.mesh.version:  # a cloth moved (``render.clothmesh``)
            batch.uploadStaticBuffer(self.vbufs[0], self.mesh.data)
            self.uploaded = self.mesh.version
        due = self._shared_due()
        wanted = {n: id(textures.get(n)) for (n, k) in self.program.numbers if k == "texture"}
        bound = [id(self.buffers.get(n)) for n in self.program.cbuffers]
        if self.srb is None or bound != self._bound_buffers or any(self._bound.get(n) != i for n, i in wanted.items()):
            self._make_srb(textures, batch)
            self._bound_buffers = bound
        if self.pipeline is None and not self.error:
            self._make_pipeline(rp, signature)
        written = {**due, **self.constants}
        fill(written)
        for name, c in written.items():
            batch.updateDynamicBuffer(self.buffers[name], 0, bytes(c.data))

    def _shared_due(self) -> dict:
        """The pass's frame buffers bound into ``buffers`` (a draw without ``shared``: its own, made now), those this
        draw is the first to bind this frame by name (``Constants``)."""
        due = {}
        for name, cb in self.program.cbuffers.items():
            if name in self.constants:
                continue
            if self.shared is None or name not in FRAME_BUFFERS:
                self.constants[name] = gs.Constants(cb)
                self.buffers[name] = uniform_buffer(self.rhi, len(self.constants[name].data))
                self.keep.append(self.buffers[name])
                continue
            c, self.buffers[name], first = self.shared.take(cb)
            if first:
                due[name] = c
        return due

    def draw(self, cb, size: tuple[int, int]) -> None:
        if self.pipeline is not None:
            self.mesh.draw(cb, self.pipeline, self.srb, size, (self.vbufs, self.ibuf), self.instances)

    def release(self) -> None:
        """Hand the resources over for destruction one frame later (the frame being recorded may still use them);
        a later ``prepare`` makes all of them anew, the samplers and the constant buffers too."""
        for r in [*self.keep, self.srb]:
            self.ctx.later(r)
        self.keep, self.pipeline, self.srb = [], None, None
        self.constants, self.buffers, self._bound, self._samplers = {}, {}, {}, {}
        self._bound_buffers = []

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        return self.mesh.box()


def screen_pass(ctx, draw: FxcDraw, fbo, maps: dict, values: dict, size: tuple[int, int]) -> None:
    """One full-screen ``draw`` into ``fbo`` (cleared first), its textures and constants by name."""

    def prepare(rp, signature, batch) -> None:
        draw.prepare(rp, signature, batch, maps, lambda c: gs.set_values(c.values(), values))

    draw_pass(ctx, fbo, prepare, lambda cb: draw.draw(cb, size))


class ScreenChain:
    """Full-screen passes of one game's post effects (``programs``: pass name -> ``Program``), each drawn over a
    screen triangle into a half-float target of its own: what 2205's and 1800's chain (``postforward``) and 2070's
    and 1404's sequence (``post2070``) share; they say the order, the inputs and the values."""

    def __init__(self, ctx, programs: dict, dummies: Dummies) -> None:
        self.ctx = ctx
        mesh = screen_triangle()
        self.draws = {name: FxcDraw(ctx, program, mesh, NO_DEPTH, dummies) for name, program in programs.items()}
        self.size = None
        self.targets: dict = {}  # name -> (texture, framebuffer, its size)

    def ensure_targets(self, size: tuple[int, int], divisors: dict) -> None:
        """A target per name of ``divisors`` at the picture's ``size`` divided by its divisor, made again when the
        picture's size changes."""
        if self.size == size:
            return
        self.release_targets()
        for name, div in divisors.items():
            w, h = max(1, size[0] // div), max(1, size[1] // div)
            tex = self.ctx.native_texture((w, h), QRhiTexture.Format.RGBA16F)
            self.targets[name] = (tex, self.ctx.framebuffer([tex]), (w, h))
        self.size = size

    def target_size(self, name: str) -> tuple[int, int]:
        return self.targets[name][2]

    def run(self, name: str, maps: dict, values: dict):
        """The pass ``name`` into its target, its textures and constants by name; returns the target."""
        tex, fbo, size = self.targets[name]
        screen_pass(self.ctx, self.draws[name], fbo, maps, values, size)
        return tex

    def release_targets(self) -> None:
        for tex, fbo, _size in self.targets.values():
            fbo.release()
            tex.release()
        self.targets, self.size = {}, None

    def release(self) -> None:
        self.release_targets()
        for draw in self.draws.values():
            draw.release()
