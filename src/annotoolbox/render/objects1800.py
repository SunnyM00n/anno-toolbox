"""Anno 1800's objects by its own model shaders, many instances a draw as its engine draws a batch (instance words
pointing at matrices, the casters' words per cascade), each draw a model of ``frameforward.ForwardFrame``.

``Draws(ctx, program, mesh, state, dummies, binding)`` holds one mesh's draws in one pass (``ObjectDraw``s: the
further ones through the first; ``Binding``: the frame's buffers ``SharedBuffers``, the frame's rows ``FrameRows``);
``Run`` is one draw of instances as the frame's model; ``Maps2D`` the maps once per file, ``plain_texture`` and
``named_maps`` the maps of the 1800 and 2205 looks' other passes; ``ObjectSource`` the compile; ``distance_levels`` the
level of detail. docs/rendering/island-1800-2205-view.md sections 12 and 16.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhi,
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
)

from ..formats import fxeffect
from ..models import shaderforward
from . import cull, frameforward, gamedraw, gamemaps, mipchain
from . import gameshader as gs
from .fxcdraw import FxcDraw, SharedBuffers, layout_key, same_objects

CHUNK = 280  # instance words a draw (constants_model.fxh NUM_MODEL_CONSTANTS)
MATRICES = shaderforward.STRUCTURED_LENGTHS["float4x3"]  # cModelMatrixBuffer as the viewer reads it
CASTER_ROWS = 4096  # the casters' words a draw: 1024 instances of four cascades [ours: the length]
WHITE = 0x0FFFFFFF  # the word's x: object and dye colour white, opaque
NO_TERRAIN_ADAPT = frameforward.NO_TERRAIN_ADAPT  # the word's y: the view places the objects at their own heights
CASTERS = "cb_cModelGPUCulledInstanceData"
PROP_CASTERS = "cb_cPropGPUCulledInstanceData"
OWN = ("cbModel", frameforward.MATRIX_BUFFER, CASTERS, "cbProp", PROP_CASTERS)  # a draw's own buffers: its instances
MATERIAL = ("cbMaterial", "cbModelConfigData", "cbPhongEx")  # a part's buffers: its material, its config's (2205's)
# the casters' words as the engine's GPU culling hands them: a structured buffer the viewer reads as constants, at
# 16 elements there; declared here at the most a constant buffer holds
_CULLED_RE = re.compile(rb"(?m)^[^\n;]*StructuredBuffer\s*<\s*uint4\s*>\s*cModelGPUCulledInstanceData[^;]*;")
_CULLED = f"shared cbuffer {CASTERS} {{ uint4 cModelGPUCulledInstanceData[{CASTER_ROWS}]; }};".encode()
# the props' casters the same way: four float4 an instance and cascade (constants_prop.fxh ProGPUCulledInstData)
PROP_CASTER_ROWS = 1024
_PROP_CULLED_RE = re.compile(
    rb"(?m)^[^\n;]*StructuredBuffer\s*<\s*ProGPUCulledInstData\s*>\s*cPropGPUCulledInstanceData[^;]*;"
)
_PROP_CULLED = (
    f"shared cbuffer {PROP_CASTERS} {{ ProGPUCulledInstData cPropGPUCulledInstanceData[{PROP_CASTER_ROWS}]; }};"
).encode()
# 1800's prop shaders by the .prp's Type: the effect, its colour technique, the state block it draws with without
# the depth pre-pass (the .prp's ForceZPrePass 0), its caster, its normals-and-depth pass for the occlusion
# (island-1800-2205-view.md 15, 16)
PROPS = {
    "Plant": ("prop_plant", "PlantDefault", 1, "PlantShadow", "PlantNormalsDepth"),
    "Terrain": ("prop_terrain", "PropDefault", 1, "PropShadow", "PropNormalsDepth"),
    "SimplePBR": ("prop_simple_pbr", "PropDefault", 0, "PropShadow", "PropNormalsDepth"),
}
PROP_CHUNK = 256  # instances a prop draw: cPropInstanceData holds 512, the casters' rows 256 in four cascades
NO_VISIBILITY_CHECK = 0x8  # functions_prop.fxh PRPF_NO_VISIBILITY_CHECK: the view has no prop visibility map
PROP_WHITE = 0xFF808080  # UnpackPropColor's word of the island's white (rgb / 127.5), opaque
F = QRhiTexture.Format


class ObjectSource(shaderforward.ForwardSource):
    """1800's model effects as the viewer compiles them (``shaderforward``), the casters' words declared at
    ``CASTER_ROWS``; ``GAME``, its models' caster technique ``CASTER`` and its prop shaders ``PROPS`` (2205's:
    ``objects2205.ObjectSource2205``)."""

    GAME, CASTER, PROPS = "anno1800", "ModelObj2Shadow", PROPS

    def __init__(self, files, compiler) -> None:
        super().__init__(files, compiler, self.GAME)

    def _read(self, path: str) -> bytes | None:
        raw = super()._read(path)
        if raw is not None and b"cModelGPUCulledInstanceData" in raw:
            raw = _CULLED_RE.sub(_CULLED, raw)
        if raw is not None and b"cPropGPUCulledInstanceData" in raw:
            raw = _PROP_CULLED_RE.sub(_PROP_CULLED, raw)
        return raw

    def prop_material(self, values: dict) -> tuple[str, tuple, tuple]:
        """A prop's effect file, its defines (the .prp's switches the effect tests) and its ``PROPS`` row; ``NoPass``
        for a type no prop shader of this view draws."""
        kind = self.PROPS.get(str(values.get("Type", "")))
        if kind is None:
            raise shaderforward.NoPass(
                f"the prop type {values.get('Type')!r} is not one of the game's prop shaders here"
            )
        fx = f"{shaderforward.FX_DIR}{kind[0]}.fx"
        with self._lock:
            if fx not in self._switches:
                self._switches[fx] = shaderforward.preprocessor_switches(fxeffect.expand_includes(self._read, fx))
        return fx, tuple(shaderforward.material_defines(values, kind[0], self.game, self._switches[fx])), kind


def lod_tables(text: str) -> list[tuple[float, ...]]:
    """Per quality level of an ``enginesettings`` file its ``LODDistances`` (LOD0 first): LOD k from that distance on."""
    out = []
    for k in range(8):
        m = re.search(rf"<QualityLevel{k}>(.*?)</QualityLevel{k}>", text, re.S)
        if m is None:
            break
        table = re.search(r"<LODDistances>(.*?)</LODDistances>", m.group(1), re.S)
        out.append(tuple(float(v) for v in re.findall(r"<LOD\d>([-\d.]+)</LOD\d>", table.group(1))) if table else ())
    return out


def distance_levels(centres: np.ndarray, radii: np.ndarray, view: np.ndarray, proj: np.ndarray,
                    size: tuple[int, int], rules: cull.LodRules, table: tuple, levels: int) -> np.ndarray:  # fmt: skip
    """Each instance's level by 1800's rule: the last LOD whose distance it is at or past (``table``), 0 without a
    table; -1 for one too small or too far by 117's rule (``cull.lod_levels``, its own levels left out)."""
    shown = cull.lod_levels(centres, radii, view, proj, size, rules, 1)  # one level: 0 shown, -1 not
    if not table:
        return shown
    hom = np.c_[centres, np.ones(len(centres))] @ np.asarray(view, float).T
    distance = np.linalg.norm(hom[:, :3], axis=1)
    found = np.searchsorted(np.asarray(table, float), distance, side="right") - 1
    return np.where(shown < 0, -1, np.clip(found, 0, levels - 1)).astype(np.int32)


def matrix_elements(worlds: np.ndarray, bones: int = 1) -> np.ndarray:
    """``cModelMatrixBuffer``'s elements of ``worlds`` ([n, 4, 4], column vectors), each ``bones`` times (a skin's
    matrices at rest): a ``float4x3`` stored by columns holds the world's first three rows [n x bones, 12]."""
    rows = np.ascontiguousarray(np.asarray(worlds, np.float32)[:, :3, :4]).reshape(len(worlds), 12)
    return np.repeat(rows, max(1, bones), axis=0)


def words(count: int, bones: int = 1, cascades: int = 0) -> np.ndarray:
    """The instance words of ``count`` instances (uint32 [n, 4]): white, no terrain adaption, its first matrix; with
    ``cascades`` a word per instance and cascade, the cascade in bits 30 and 31 (the casters')."""
    first = np.arange(count, dtype=np.uint32) * np.uint32(max(1, bones))
    out = np.zeros((count, 4), np.uint32)
    out[:, 0], out[:, 1], out[:, 3] = WHITE, NO_TERRAIN_ADAPT, first
    if not cascades:
        return out
    out = np.repeat(out, cascades, axis=0)
    out[:, 0] |= np.tile(np.arange(cascades, dtype=np.uint32), count) << np.uint32(30)
    return out


def cascade_pairs(centres: np.ndarray, radii: np.ndarray, matrices: list) -> np.ndarray:
    """The (instance, cascade) pairs whose sphere reaches into the cascade's part of the map: ``matrices`` each
    cascade's world -> clip xy (row vectors, ``gameshadow.Cascade.matrix``) [k, 2]; what the engine's GPU culling
    hands the casters [ours: by the spheres]."""
    hom = np.c_[np.asarray(centres, float).reshape(-1, 3), np.ones(len(centres))]
    out = [np.zeros((0, 2), np.int64)]
    for i, m in enumerate(matrices):
        m = np.asarray(m, float)
        p = hom @ m
        p = p[:, :2] / np.where(np.abs(p[:, 3:4]) > 1e-9, p[:, 3:4], 1.0)
        reach = np.asarray(radii, float)[:, None] * np.linalg.norm(m[:3, :2], axis=0)
        k = np.flatnonzero(np.all(np.abs(p) <= 1.0 + reach, axis=1))
        out.append(np.stack([k, np.full(len(k), i)], axis=1))
    return np.concatenate(out).astype(np.int64)


def pair_words(count: int, bones: int, pairs: np.ndarray) -> np.ndarray:
    """The casters' words of ``pairs`` (``cascade_pairs``): the instance's word with its cascade in bits 30 and 31."""
    out = words(count, bones)[pairs[:, 0]]
    out[:, 0] |= pairs[:, 1].astype(np.uint32) << np.uint32(30)
    return out


def _quaternions(m: np.ndarray) -> np.ndarray:
    """The unit quaternions (x, y, z, w) of rotations ``m`` ([n, 3, 3], column vectors), by the largest of the four
    (Shepperd's way: no sign is guessed where w is near 0)."""
    m00, m11, m22 = m[:, 0, 0], m[:, 1, 1], m[:, 2, 2]
    d = np.stack([1 + m00 + m11 + m22, 1 + m00 - m11 - m22, 1 - m00 + m11 - m22, 1 - m00 - m11 + m22], axis=1)
    s = np.sqrt(np.maximum(d, 1e-12)) * 2  # 4 x the chosen component
    a, b, c = m[:, 2, 1] - m[:, 1, 2], m[:, 0, 2] - m[:, 2, 0], m[:, 1, 0] - m[:, 0, 1]
    e, f, g = m[:, 0, 1] + m[:, 1, 0], m[:, 0, 2] + m[:, 2, 0], m[:, 1, 2] + m[:, 2, 1]
    cases = np.stack(
        [
            np.stack([a / s[:, 0], b / s[:, 0], c / s[:, 0], s[:, 0] / 4], axis=1),
            np.stack([s[:, 1] / 4, e / s[:, 1], f / s[:, 1], a / s[:, 1]], axis=1),
            np.stack([e / s[:, 2], s[:, 2] / 4, g / s[:, 2], b / s[:, 2]], axis=1),
            np.stack([f / s[:, 3], g / s[:, 3], s[:, 3] / 4, c / s[:, 3]], axis=1),
        ],
        axis=1,
    )
    q = cases[np.arange(len(m)), np.argmax(d, axis=1)]
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def prop_words(colours: np.ndarray) -> np.ndarray:
    """The colour words of the island's stored prop colours ([n, 4] floats, white 1): ``UnpackPropColor``'s bytes,
    alpha over 255, red, green and blue over 127.5 (rendering/island-1800-2205-view.md 15) [I: the engine's
    packing not read; the island's white is the shader's 1]."""
    c = np.asarray(colours, np.float64).reshape(-1, 4)
    rgb = np.clip(np.rint(c[:, :3] * 127.5), 0, 255).astype(np.uint32)
    alpha = np.clip(np.rint(c[:, 3] * 255.0), 0, 255).astype(np.uint32)
    return (alpha << 24) | (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]


def prop_rows(worlds: np.ndarray, flags: int = NO_VISIBILITY_CHECK, color=PROP_WHITE) -> np.ndarray:
    """The props' instance data ([n, 4, 4] float32: ``GetPropInstanceData``'s four float4 each) of ``worlds`` ([n, 4,
    4], column vectors): the place and fade (shown), the turn as a quaternion, the scale and the flags word, the colour
    word, the height in its own space, the shadow's fade. The view's mirror is carried by a negative x scale: the
    shaders turn ``position x scale`` and divide the normal by the scale [ours]."""
    w = np.asarray(worlds, np.float64).reshape(-1, 4, 4)
    a = w[:, :3, :3]
    scale = np.linalg.norm(a, axis=1)  # the columns' lengths
    scale[:, 0] *= np.where(np.linalg.det(a) < 0, -1.0, 1.0)
    turn = _quaternions(a / np.where(np.abs(scale) > 1e-12, scale, 1.0)[:, None, :])
    out = np.zeros((len(w), 4, 4), np.float32)
    out[:, 0, :3], out[:, 0, 3] = w[:, :3, 3], 1.0
    out[:, 1], out[:, 2, :3] = turn, scale
    words = out.view(np.uint32)
    words[:, 2, 3], words[:, 3, 0] = flags, color
    out[:, 3, 2] = 1.0
    return out


def prop_caster_rows(props: np.ndarray, pairs: np.ndarray) -> np.ndarray:
    """The props' casters' rows of the (instance, cascade) ``pairs`` of the props' ``prop_rows``: the instance's data
    with its cascade in the colour word's bits 0 and 1, as the engine's culling hands them
    (``CalculatePropShadowCullClipDistances``)."""
    rows = np.asarray(props, np.float32).reshape(-1, 4, 4)[pairs[:, 0]]
    words = rows.view(np.uint32)
    words[:, 3, 0] = (words[:, 3, 0] & np.uint32(0xFFFFFFFC)) | pairs[:, 1].astype(np.uint32)
    return rows


def chunk_size(bones: int) -> int:
    """The instances one draw holds: its words, and the matrix buffer's elements over the matrices each takes."""
    return max(1, min(CHUNK, MATRICES // max(1, bones), CASTER_ROWS // 4))


def group(name: str, own: tuple = OWN) -> str:
    """Whose a constant buffer is: the draw's (``own``: an object draw's instances), the part's (its material) or
    the frame's."""
    return "draw" if name in own else "part" if name.startswith(MATERIAL) else "frame"


def _aligned(size: int) -> int:
    """``size`` bytes rounded up to a dynamic offset's alignment (256)."""
    return (int(size) + 255) // 256 * 256


class FrameRows:
    """A frame's instance data of every object draw (the 117 view's ``gameinstances.FrameInstances`` for 1800's
    constant buffers): per buffer name one buffer for all draws, a draw binding its part by a dynamic offset (1800
    binds its instance buffers as root descriptors into one upload buffer), and the buffers whose content repeats from
    draw to draw made once (``fixed``: a run's words, the casters' fade). ``begin`` a frame, ``place`` a draw's bytes
    while the frame's draws are made, ``upload`` once before the passes (a still frame sends nothing)."""

    def __init__(self, ctx) -> None:
        self.ctx, self.rhi = ctx, ctx.rhi
        self.buffers: dict[str, object] = {}
        self.made: dict[tuple, object] = {}  # (layout, content key) -> a fixed buffer
        self.key: tuple | None = None  # what the frame's rows were placed for (``place_runs``)
        # a draw's slot by a dynamic offset on Direct3D 12; on 11 that ended the process (2026-11-14), so there each
        # draw binds its slot at a fixed offset, a binding set per offset (island-1800-2205-view.md 16)
        self.dynamic = ctx.rhi.backend() == QRhi.Implementation.D3D12
        self.begin()

    def begin(self) -> None:
        self._parts: dict[str, list] = {}
        self._ends: dict[str, int] = {}
        self._reach: dict[str, int] = {}
        self._sent = False

    def place(self, name: str, raw: np.ndarray, reach: int) -> int:
        """A draw's bytes (uint8) into the buffer ``name``, its binding ``reach`` bytes long: the byte offset."""
        return int(self.place_many(name, np.asarray(raw, np.uint8)[None], reach)[0])

    def place_many(self, name: str, rows: np.ndarray, reach: int) -> np.ndarray:
        """Draws' bytes of one length at once (uint8 [n, k], a row a draw) into the buffer ``name``, each binding
        ``reach`` bytes long: their byte offsets."""
        rows = np.asarray(rows, np.uint8)
        n, stride = len(rows), _aligned(rows.shape[1])
        at = self._ends.get(name, 0)
        block = np.zeros((n, stride), np.uint8)
        block[:, : rows.shape[1]] = rows
        self._parts.setdefault(name, []).append((at, block.ravel()))
        self._ends[name] = at + n * stride
        self._reach[name] = max(self._reach.get(name, 0), _aligned(reach))
        return at + np.arange(n) * stride

    def buffer(self, name: str):
        """The buffer ``name`` with room for this frame's rows and the last binding's reach past them (made anew,
        twice that, when it grows: a binding set of the old one is made again)."""
        need = self._ends.get(name, 0) + max(self._reach.get(name, 0), 256)
        buf = self.buffers.get(name)
        if buf is None or buf.size() < need:
            if buf is not None:
                self.ctx.later(buf)
                need *= 2
            buf = self.buffers[name] = gamedraw.uniform_buffer(self.rhi, need)
        return buf

    def fixed(self, batch, reflection, key, fill) -> object:
        """A buffer of ``reflection``'s layout whose content is ``fill(Constants)``'s, made once per ``key``."""
        k = (layout_key(reflection), key)
        buf = self.made.get(k)
        if buf is None:
            c = gs.Constants(reflection)
            fill(c)
            buf = self.made[k] = gamedraw.uniform_buffer(self.rhi, len(c.data))
            batch.updateDynamicBuffer(buf, 0, bytes(c.data))
        return buf

    def upload(self, batch) -> None:
        """The frame's rows into their buffers (once a frame; grown first where needed)."""
        if self._sent:
            return
        self._sent = True
        for name, parts in self._parts.items():
            data = np.zeros(self._ends[name], np.uint8)
            for at, raw in parts:
                data[at : at + len(raw)] = raw
            batch.updateDynamicBuffer(self.buffer(name), 0, data.tobytes())

    def release(self) -> None:
        for buf in (*self.buffers.values(), *self.made.values()):
            self.ctx.later(buf)
        self.buffers, self.made, self.key = {}, {}, None
        self.begin()


@dataclass(frozen=True)
class Binding:
    """How a mesh's draws in one pass bind (``ObjectDraw``, ``Draws``): the pass's frame buffers (``shared``), the
    names of a draw's own buffers (``own``, ``group``), the frame's instance rows (None: each draw its own buffers),
    the mirrored winding and the stand-ins of the textures no pass hands."""

    shared: SharedBuffers | None = None
    own: tuple = OWN
    rows: FrameRows | None = None
    mirrored: bool = False
    fallbacks: dict = field(default_factory=dict)


class ObjectDraw(FxcDraw):
    """A draw of an object mesh's instances whose constant buffers are filled by group (``group``): the frame's from
    ``shared``, the part's written once, the draw's own every frame; with a ``lead`` (the mesh's first draw in the
    pass, prepared before it and let go with it: ``Draws``) a further draw of more instances, everything but its
    own instance buffers the lead's (its pipeline, vertices, samplers, the frame's and the part's buffers). With
    ``rows`` (``FrameRows``) its own buffers are the frame's: ``slot`` bound by its offset, ``fixed`` made once, the
    rest ``own_rows`` (``place_runs``); a further draw with no rest of its own draws by the lead's binding set."""

    def __init__(self, ctx, program: gamedraw.Program, mesh: gamedraw.Mesh, state, dummies,
                 binding: Binding | None = None, lead: ObjectDraw | None = None) -> None:  # fmt: skip
        super().__init__(ctx, program, mesh, state, dummies)
        b = binding or Binding()
        self.lead, self.shared, self.own, self.rows = lead, b.shared, b.own, b.rows
        self.mirrored, self.fallbacks = b.mirrored, dict(b.fallbacks)
        if lead is not None:  # the lead's, as they are now
            self.own, self.rows, self.mirrored, self.fallbacks = lead.own, lead.rows, lead.mirrored, lead.fallbacks
        self._fresh: set[str] = set()  # the part's buffers not written yet
        self._bound_buffers: list = []
        self.slot: tuple[str, int] | None = None  # rows: (the buffer bound by its dynamic offset, the offset)
        self.fixed: dict[str, tuple] = {}  # rows: own buffers of repeating content, name -> (key, fill)
        self.own_rows: dict[str, bytes] = {}  # rows: the rest of its own buffers' bytes this frame
        self._sent_rows: dict[str, bytes] = {}

    def _framed(self, name: str) -> bool:
        """Whether an own buffer is the frame's (``FrameRows``): its slot or a fixed one."""
        return self.rows is not None and ((self.slot is not None and name == self.slot[0]) or name in self.fixed)

    def _own_buffers(self) -> None:
        for name, cb in self.program.cbuffers.items():
            kind = group(name, self.own)
            if (kind == "draw" and not self._framed(name)) or (self.lead is None and kind == "part"):
                self.constants[name] = gs.Constants(cb)
                self.buffers[name] = gamedraw.uniform_buffer(self.rhi, len(self.constants[name].data))
                self.keep.append(self.buffers[name])
        self._fresh = {n for n in self.constants if group(n, self.own) == "part"}

    def _borrowed(self, batch) -> dict:
        """The buffers it binds but does not own (the lead's, the frame's rows, or the shared ones), those first bound
        this frame by name (``Constants``)."""
        due = {}
        for name, cb in self.program.cbuffers.items():
            if name in self.constants:
                continue
            if self.lead is not None:
                self.buffers[name] = self.lead.buffers[name]
            elif self._framed(name):
                fixed = self.fixed.get(name)
                rows = self.rows
                self.buffers[name] = rows.fixed(batch, cb, *fixed) if fixed is not None else rows.buffer(name)
            else:
                c, buf, first = self.shared.take(cb)
                self.buffers[name] = buf
                if first:
                    due[name] = c
        return due

    def _cbuffer(self, name: str, number: int, stages):
        if self.slot is None or name != self.slot[0]:
            return super()._cbuffer(name, number, stages)
        reach = _aligned(self.program.cbuffers[name].size)
        if self.rows.dynamic:
            return gamedraw.B.uniformBufferWithDynamicOffset(number, stages, self.buffers[name], reach)
        return gamedraw.B.uniformBuffer(number, stages, self.buffers[name], self.slot[1], reach)

    def prepare(self, rp, signature, batch, textures: dict, fill) -> None:
        lead = self.lead
        if self.rows is not None:
            self.rows.upload(batch)
        if lead is not None:
            self.pipeline = lead.pipeline
            if lead.pipeline is None:  # the lead not made yet (a blended draw sorted before it): drawn next frame
                return
            self.vbufs, self.ibuf, self._samplers = lead.vbufs, lead.ibuf, lead._samplers
            self.attrs, self.fill_stride = lead.attrs, lead.fill_stride
            if self.rows is not None and self.rows.dynamic and not self.own_rows:  # only its slot: the lead's set
                self.srb = lead.srb
                rows = self.rows.buffers.get(self.slot[0]) if self.slot is not None else None
                if self.slot is not None and all(rows is not b for b in lead._bound_buffers):
                    self.pipeline = None  # the lead's set binds a buffer that grew since (sorted before it): next frame
                return
            if self.srb is lead.srb:  # its own set from now on: the lead's is not its to let go
                self.srb = None
        elif not self.keep:
            self._vertex_resources(batch)
        if not self.constants:
            self._own_buffers()
        due = self._borrowed(batch)
        bound = [self.buffers[n] for n in self.program.cbuffers]
        if self.slot is not None and not self.rows.dynamic:  # bound at its offset: a set per offset
            bound.append(self.slot)
        if self.srb is None or not same_objects(bound, self._bound_buffers) or self._maps_changed(textures):
            self._make_srb(textures, batch)
            self._bound_buffers = bound
        if lead is None and self.pipeline is None and not self.error:
            self._make_pipeline(rp, signature)
        part = {n: self.constants[n] for n in self._fresh}
        mine = (
            {} if self.rows is not None else {n: c for n, c in self.constants.items() if group(n, self.own) == "draw"}
        )
        for constants in (due, part, mine):
            if constants:
                fill(constants)
                for name, c in constants.items():
                    batch.updateDynamicBuffer(self.buffers[name], 0, bytes(c.data))
        self._fresh = set()
        for name, raw in self.own_rows.items():  # sent only when they changed (a still frame sends nothing)
            if self._sent_rows.get(name) != raw:
                batch.updateDynamicBuffer(self.buffers[name], 0, raw)
                self._sent_rows[name] = raw

    def draw(self, cb, size) -> None:
        if self.pipeline is None:
            return
        offset = None
        if self.slot is not None and self.rows.dynamic:
            offset = (self.program.numbers[(self.slot[0], "cbuffer")], self.slot[1])
        self.mesh.draw(cb, self.pipeline, self.srb, size, (self.vbufs, self.ibuf), self.instances, offset)

    def release(self) -> None:
        if self.lead is not None and self.srb is self.lead.srb:  # borrowed: the lead lets it go
            self.srb = None
        super().release()
        self._fresh, self._bound_buffers, self._sent_rows = set(), [], {}


class Draws:
    """One mesh's draws in one pass, kept from frame to frame: the first (``lead``) makes the pipeline and binds the
    shared buffers, the further ones draw more instances through it; ``take()`` hands this frame's next. With the
    binding's ``rows`` their instance data is the frame's (``FrameRows``); without its stand-ins the forward
    frame's (``frameforward.FALLBACKS``)."""

    def __init__(self, ctx, program: gamedraw.Program, mesh: gamedraw.Mesh, state, dummies, binding: Binding) -> None:
        binding = binding if binding.fallbacks else replace(binding, fallbacks=dict(frameforward.FALLBACKS))
        self.lead = ObjectDraw(ctx, program, mesh, state, dummies, binding)
        self.chunks: list[ObjectDraw] = []
        self.used = 0

    def begin(self) -> None:
        self.used = 0

    def take(self) -> ObjectDraw:
        k, self.used = self.used, self.used + 1
        if k == 0:
            return self.lead
        while len(self.chunks) < k:
            lead = self.lead
            self.chunks.append(ObjectDraw(lead.ctx, lead.program, lead.mesh, lead.state, lead.dummies, lead=lead))
        return self.chunks[k - 1]

    def taken(self) -> list[ObjectDraw]:
        """This frame's draws so far, the lead first."""
        return [self.lead, *self.chunks][: self.used]

    def release(self) -> None:
        for d in (*self.chunks, self.lead):  # the further draws first: they may bind the lead's set
            d.release()
        self.chunks, self.used = [], 0


class Cast:
    """A run's draw into the sun's cascades: the frame sets ``instances`` to the cascades it reads, the draw makes a
    copy per instance and cascade; the part's maps are taken here (the shadow pass runs before the colour pass). The
    frame's shadow values go into the frame's buffers only: its fill wrote all of them into each draw's own too."""

    def __init__(self, draw: FxcDraw, run: Run) -> None:
        self.draw_call, self.run, self.instances = draw, run, 1
        self.pairs: np.ndarray | None = None  # the (instance, cascade) pairs drawn (``cull_casts``); None: all of them

    def count(self) -> int:
        """The copies one draw makes: the pairs, or every instance into each cascade the frame reads."""
        if self.pairs is not None:
            return int(np.count_nonzero(self.pairs[:, 1] < max(1, self.instances)))
        return len(self.run.worlds) * max(1, self.instances)

    def drawn(self, used: int) -> np.ndarray:
        """The pairs drawn when the frame reads ``used`` cascades (without a cull: every instance into each, by
        cascade as ``cascade_pairs`` orders them; kept as the pairs)."""
        if self.pairs is None:
            n = len(self.run.worlds)
            self.pairs = np.stack([np.tile(np.arange(n), used), np.repeat(np.arange(used), n)], axis=1)
        return self.pairs[self.pairs[:, 1] < max(1, used)]

    def prepare(self, rp, signature, batch, textures: dict, fill) -> None:
        self.draw_call.instances = self.count()

        def own(c: dict) -> None:
            (fill if group(next(iter(c))) == "frame" else self.run.fill_caster)(c)

        self.draw_call.prepare(rp, signature, batch, {**textures, **self.run.part.textures(batch)}, own)

    def draw(self, cb, size) -> None:
        if self.draw_call.instances:
            self.draw_call.draw(cb, size)


class Run:
    """Up to ``chunk_size`` instances of a mesh as the forward frame's model (``frameforward.ForwardModel``'s
    shape): its draw, its caster (``Cast``, None for none), its ``part`` (``fields``, ``transparent``,
    ``textures(batch)``), the instances' ``worlds``, their spheres and the matrices each takes (``bones``)."""

    water = False

    def __init__(self, draw: FxcDraw, cast: FxcDraw | None, part, worlds: np.ndarray, spheres: tuple,
                 bones: int = 1) -> None:  # fmt: skip
        self.draw_call, self.part, self.worlds, self.bones = draw, part, worlds, max(1, bones)
        self.draw_call.instances = len(worlds)
        self.shadow = Cast(cast, self) if cast is not None else None
        self.blended, self.later = part.transparent, []
        self.world = np.asarray(worlds[0], float) if len(worlds) else np.identity(4)
        self.centres, self.radii = spheres
        self.prepass: FxcDraw | None = None  # its draw in the frame's normals-and-depth pre-pass (``with_prepass``)

    def with_prepass(self, draw: FxcDraw | None) -> None:
        """Its draw of the same instances in the frame's pre-pass (an opaque part's; None: none)."""
        self.prepass = draw
        if draw is not None:
            draw.instances = len(self.worlds)

    def textures(self) -> dict:
        return self.part.textures(None)

    def prepare(self, rp, signature, batch, cam: gs.Camera, light: dict, extra: dict | None = None,
                draw: FxcDraw | None = None) -> None:  # fmt: skip
        """This frame's constants: the frame's and the material's into the mesh's first draw, the instances into
        each."""
        d = draw or self.draw_call

        def fill(c: dict) -> None:  # the frame's and the part's buffers (its own are the frame's rows)
            if group(next(iter(c))) == "frame":
                frameforward.fill_frame(c, cam, light)
            else:
                frameforward.fill_fields(c, self.part.fields)

        d.prepare(rp, signature, batch, {**self.part.textures(batch), **(extra or {})}, fill)

    def fill_caster(self, c: dict) -> None:
        """The cascaded pass's part buffers (beside the frame's shadow values): the material."""
        if group(next(iter(c))) == "part":
            frameforward.fill_fields(c, self.part.fields)

    fill_model = fill_caster  # 2205's single map fills a caster by this name; its words are the frame's rows too

    def place(self, rows: FrameRows, used: int) -> None:
        """Its draws' instance data into the frame's ``rows``: the matrices in one slot its colour, pre-pass and
        caster draws share, the words fixed per ``bones`` (each draw's start at 0), the casters' words of their
        (instance, cascade) pairs below ``used`` cascades the caster's own."""
        program = self.draw_call.program
        slot = place_slot(rows, program, frameforward.MATRIX_BUFFER, "cModelMatrixBuffer",
                     matrix_elements(self.worlds, self.bones))  # fmt: skip
        bones = self.bones
        fixed = {"cbModel": (("words", bones), lambda c: c.set_elements("cModelInstanceData",
                                                                        words(chunk_size(bones), bones)))}  # fmt: skip
        hand_slots([self.draw_call, self.prepass], slot, fixed)
        if self.shadow is not None:
            own = {CASTERS: pair_words(len(self.worlds), bones, self.shadow.drawn(used)).tobytes()}
            hand_slots([self.shadow.draw_call], slot, fixed, own)

    def draw(self, cb, size) -> None:
        self.draw_call.draw(cb, size)

    def corners(self) -> np.ndarray:
        """The box of the instances' spheres (the shadow's fit)."""
        lo = (self.centres - self.radii[:, None]).min(0)
        hi = (self.centres + self.radii[:, None]).max(0)
        return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])

    def release(self) -> None:
        """Nothing: its draws are its part's (``Draws``)."""


class PropRun(Run):
    """A run of props (``PROP_CHUNK`` at most): ``Run`` with the props' instance data (``prop_rows``, made for the
    frame's props at once: ``place_runs``) in place of the models' words and matrices, its casters' rows per
    (instance, cascade); ``colours`` its props' colour words (``prop_words``; None: white)."""

    colours: np.ndarray | None = None

    def words(self) -> np.ndarray:
        """Its props' colour words, white where it was handed none."""
        return np.full(len(self.worlds), PROP_WHITE, np.uint32) if self.colours is None else self.colours

    def place(self, rows: FrameRows, used: int, data: np.ndarray | None = None) -> None:
        """Its draws' instance data into the frame's ``rows``: its props' rows (``data``, else made here) with the fade
        in its colour draw's slot, the casters' rows of their pairs below ``used`` cascades in the caster's, the
        caster's fade fixed."""
        data = prop_rows(self.worlds, color=self.words()) if data is None else data
        program = self.draw_call.program
        slot = place_slot(rows, program, "cbProp", "cPropInstanceData", data.reshape(-1, 4), shown_fade)
        hand_slots([self.draw_call, self.prepass], slot, {})
        if self.shadow is not None:
            cast = self.shadow.draw_call
            pairs = self.shadow.drawn(used)
            slot = place_slot(rows, cast.program, PROP_CASTERS, "cPropGPUCulledInstanceData",
                         prop_caster_rows(data, pairs).reshape(len(pairs), 16))  # fmt: skip
            hand_slots([cast], slot, {"cbProp": ("shown", shown_fade)})


def shown_fade(c) -> None:
    """A prop buffer's fade: shown, not fading."""
    c.set("cPropFadeType", 1.0)


_MEMBERS: dict[tuple, tuple] = {}  # (layout, member) -> (offset, element stride, the buffer's bytes)
_TEMPLATES: dict[tuple, np.ndarray] = {}  # (layout, fill) -> a buffer's bytes as ``fill`` leaves them


def _member(reflection, path: str) -> tuple[int, int]:
    """(offset, element stride) of an array member of a constant buffer, looked up once per layout."""
    key = (layout_key(reflection), path)
    got = _MEMBERS.get(key)
    if got is None:
        c = gs.Constants(reflection)
        first, second = c.find(f"{path}[0]"), c.find(f"{path}[1]")
        got = _MEMBERS[key] = (first[0], second[0] - first[0] if second is not None else reflection.size)
    return got


def slot_bytes(reflection, path: str, rows: np.ndarray, fill=None) -> np.ndarray:
    """A draw's bytes of a constant buffer whose array ``path`` holds ``rows`` (one per element, from element 0):
    up to the last row, or (``fill``) the whole buffer as ``fill(Constants)`` leaves it with the rows in it."""
    offset, stride = _member(reflection, path)
    rows = np.ascontiguousarray(rows)
    raw = rows.reshape(len(rows), int(np.prod(rows.shape[1:], dtype=np.int64))).view(np.uint8)
    n = len(raw)
    if fill is None:
        out = np.zeros(offset + n * stride, np.uint8)
    else:
        key = (layout_key(reflection), fill)
        if key not in _TEMPLATES:
            c = gs.Constants(reflection)
            fill(c)
            _TEMPLATES[key] = np.frombuffer(bytes(c.data), np.uint8)
        out = _TEMPLATES[key].copy()
    out[offset : offset + n * stride].reshape(n, stride)[:, : raw.shape[1]] = raw
    return out


def place_slot(rows: FrameRows, program, name: str, path: str, data: np.ndarray, fill=None) -> tuple | None:
    """``data`` placed into the frame's buffer ``name`` as ``program`` binds it: (name, offset), None where it does
    not bind it."""
    cb = program.cbuffers.get(name)
    if cb is None:
        return None
    return name, rows.place(name, slot_bytes(cb, path, data, fill), cb.size)


def hand_slots(draws: list, slot: tuple | None, fixed: dict, own: dict | None = None) -> None:
    """A run's draws (None: none) take this frame's slot, fixed buffers and own rows where their program binds
    them."""
    for d in draws:
        if d is None:
            continue
        names = d.program.cbuffers
        d.slot = slot if slot is not None and slot[0] in names else None
        d.fixed = {n: v for n, v in fixed.items() if n in names}
        d.own_rows = {n: v for n, v in (own or {}).items() if n in names}


def place_runs(rows: FrameRows, runs: list, used: int) -> None:
    """The frame's instance data of every run at once (``FrameRows``): the props' rows made in one go, each run's
    slots placed; ``used``: the cascades the frame reads."""
    rows.begin()
    props = [r for r in runs if isinstance(r, PropRun)]
    if props:
        every = prop_rows(np.concatenate([r.worlds for r in props]), color=np.concatenate([r.words() for r in props]))
        ends = np.cumsum([len(r.worlds) for r in props])
        for r, data in zip(props, np.split(every, ends[:-1]), strict=True):
            r.place(rows, used, data)
    for r in runs:
        if not isinstance(r, PropRun):
            r.place(rows, used)


def cull_casts(casts: list, matrices: list) -> None:
    """Each cast's (instance, cascade) pairs, only those whose sphere reaches into the cascade (``cascade_pairs``),
    for all of them at once."""
    if not casts:
        return
    sizes = np.array([len(c.run.centres) for c in casts])
    pairs = cascade_pairs(np.concatenate([c.run.centres for c in casts]),
                          np.concatenate([c.run.radii for c in casts]), matrices)  # fmt: skip
    starts = np.cumsum(sizes) - sizes
    owner = np.searchsorted(np.cumsum(sizes), pairs[:, 0], side="right")
    order = np.argsort(owner, kind="stable")  # each cast's pairs kept by cascade, as its own call gave them
    pairs, owner = pairs[order], owner[order]
    bounds = np.searchsorted(owner, np.arange(len(casts) + 1))
    for k, c in enumerate(casts):
        got = pairs[bounds[k] : bounds[k + 1]].copy()
        got[:, 0] -= starts[k]
        c.pairs = got


def plain_texture(rhi, batch, dds):
    """A map as one 2D texture (the model shaders read ``Texture2D``): its blocks as stored with every level (the
    levels a file stops short of made: ``mipchain``), or its first level as RGBA; never sRGB (the shaders turn colour
    into linear light themselves)."""
    stored, _key = gamedraw.map_kind(dds)
    info = dds.info
    if stored:
        flags = QRhiTexture.Flag.MipMapped if info.mips > 1 else QRhiTexture.Flag(0)
        tex = rhi.newTexture(gamedraw.BLOCK_FORMATS[info.format], QSize(info.width, info.height), 1, flags)
        levels = [dds.stored(level) for level in range(info.mips)]
        levels = mipchain.full_levels(info, levels) if info.mips > 1 else levels
    else:
        rgba = np.ascontiguousarray(dds.level(0), np.uint8)
        tex = rhi.newTexture(F.RGBA8, QSize(rgba.shape[1], rgba.shape[0]))
        levels = [rgba.tobytes()]
    tex.create()
    desc = QRhiTextureUploadDescription()
    desc.setEntries([QRhiTextureUploadEntry(0, level, QRhiTextureSubresourceUploadDescription(raw))
                     for level, raw in enumerate(levels)])  # fmt: skip
    batch.uploadTexture(tex, desc)
    return tex


def named_maps(files, names: dict, folder: str = "") -> dict:
    """In the background: the maps a pass reads by the shaders' names (``names``: name -> the file under
    ``folder``) as ``DdsFile``s for ``plain_texture``; one that does not read is left to its stand-in."""
    from ..models import ddsdecode

    out = {}
    for name, path in names.items():
        try:
            out[name] = ddsdecode.DdsFile(files.read(folder + path))
        except (OSError, KeyError, ValueError):
            continue
    return out


class Maps2D(gamemaps.MapPool):
    """117's pool of maps (``gamemaps.MapPool``: once per file, counted by the parts that name it, coarser levels past
    the quality's largest side) with each map one plain 2D texture (``plain_texture``)."""

    def take(self, batch, path: str) -> object:
        tex = self.arrays.get(path)
        if tex is None:
            dds = self.pending.pop(path)
            dds = dds if isinstance(dds, gamemaps._Kept) else gamemaps.smaller(dds, self.max_size)
            tex = self.arrays[path] = plain_texture(self.rhi, batch, dds)
        return tex
