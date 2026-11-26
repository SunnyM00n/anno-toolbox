"""Anno 1800's objects by its own model shaders, many instances a draw as its engine draws a batch (instance words
pointing at matrices, the casters' words per cascade), each draw a model of ``frameforward.CForwardFrame``.

``CDraws(ctx, program, mesh, state, dummies, binding)`` holds one mesh's draws in one pass (``CObjectDraw``s: the
further ones through the first; ``SBinding``: the frame's buffers ``CSharedBuffers``, the frame's rows ``CFrameRows``);
``CRun`` is one draw of instances as the frame's model; ``CMaps2D`` the maps once per file, ``plain_texture`` and
``named_maps`` the maps of the 1800 and 2205 looks' other passes; ``CObjectSource`` the compile; ``distance_levels`` the
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
from . import cull, frameforward, gamedraw, gamemaps, gameshader, gametextures, mipchain
from .fxcdraw import CFxcDraw, CSharedBuffers, layout_key, same_objects

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
TextureFormat = QRhiTexture.Format


class CObjectSource(shaderforward.CForwardSource):
    """1800's model effects as the viewer compiles them (``shaderforward``), the casters' words declared at
    ``CASTER_ROWS``; ``GAME``, its models' caster technique ``CASTER`` and its prop shaders ``PROPS`` (2205's:
    ``objects2205.CObjectSource2205``)."""

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
        """A prop's effect file, its defines (the .prp's switches the effect tests) and its ``PROPS`` row; ``CNoPass``
        for a type no prop shader of this view draws."""
        kind = self.PROPS.get(str(values.get("Type", "")))
        if kind is None:
            raise shaderforward.CNoPass(
                f"the prop type {values.get('Type')!r} is not one of the game's prop shaders here"
            )
        fx = f"{shaderforward.FX_DIR}{kind[0]}.fx"
        with self._m_lock:
            if fx not in self._m_switches:
                self._m_switches[fx] = shaderforward.preprocessor_switches(fxeffect.expand_includes(self._read, fx))
        return fx, tuple(shaderforward.material_defines(values, kind[0], self.m_game, self._m_switches[fx])), kind


def lod_tables(text: str) -> list[tuple[float, ...]]:
    """Per quality level of an ``enginesettings`` file its ``LODDistances`` (LOD0 first): LOD k from that distance on."""
    out = []
    for k in range(8):
        match = re.search(rf"<QualityLevel{k}>(.*?)</QualityLevel{k}>", text, re.S)
        if match is None:
            break
        table = re.search(r"<LODDistances>(.*?)</LODDistances>", match.group(1), re.S)
        out.append(
            tuple(float(distance) for distance in re.findall(r"<LOD\d>([-\d.]+)</LOD\d>", table.group(1)))
            if table
            else ()
        )
    return out


def distance_levels(centres: np.ndarray, radii: np.ndarray, view: np.ndarray, projection: np.ndarray,
                    size_px: tuple[int, int], rules: cull.SLodRules, table: tuple, levels: int) -> np.ndarray:  # fmt: skip
    """Each instance's level by 1800's rule: the last LOD whose distance it is at or past (``table``), 0 without a
    table; -1 for one too small or too far by 117's rule (``cull.lod_levels``, its own levels left out)."""
    shown = cull.lod_levels(centres, radii, view, projection, size_px, rules, 1)  # one level: 0 shown, -1 not
    if not table:
        return shown
    homogeneous = np.c_[centres, np.ones(len(centres))] @ np.asarray(view, float).T
    distance = np.linalg.norm(homogeneous[:, :3], axis=1)
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
    cascade's world -> clip xy (row vectors, ``gameshadow.SCascade.matrix``) [k, 2]; what the engine's GPU culling
    hands the casters [ours: by the spheres]."""
    hom = np.c_[np.asarray(centres, float).reshape(-1, 3), np.ones(len(centres))]
    out = [np.zeros((0, 2), np.int64)]
    for i, matrix in enumerate(matrices):
        matrix = np.asarray(matrix, float)
        projected = hom @ matrix
        projected = projected[:, :2] / np.where(np.abs(projected[:, 3:4]) > 1e-9, projected[:, 3:4], 1.0)
        reach = np.asarray(radii, float)[:, None] * np.linalg.norm(matrix[:3, :2], axis=0)
        visible = np.flatnonzero(np.all(np.abs(projected) <= 1.0 + reach, axis=1))
        out.append(np.stack([visible, np.full(len(visible), i)], axis=1))
    return np.concatenate(out).astype(np.int64)


def pair_words(count: int, bones: int, pairs: np.ndarray) -> np.ndarray:
    """The casters' words of ``pairs`` (``cascade_pairs``): the instance's word with its cascade in bits 30 and 31."""
    out = words(count, bones)[pairs[:, 0]]
    out[:, 0] |= pairs[:, 1].astype(np.uint32) << np.uint32(30)
    return out


def _quaternions(rotations: np.ndarray) -> np.ndarray:
    """The unit quaternions (x, y, z, w) of rotations ``m`` ([n, 3, 3], column vectors), by the largest of the four
    (Shepperd's way: no sign is guessed where w is near 0)."""
    m00, m11, m22 = rotations[:, 0, 0], rotations[:, 1, 1], rotations[:, 2, 2]
    diagonals = np.stack([1 + m00 + m11 + m22, 1 + m00 - m11 - m22, 1 - m00 + m11 - m22, 1 - m00 - m11 + m22], axis=1)
    fourfold = np.sqrt(np.maximum(diagonals, 1e-12)) * 2  # 4 x the chosen component
    diff_x, diff_y, diff_z = (
        rotations[:, 2, 1] - rotations[:, 1, 2],
        rotations[:, 0, 2] - rotations[:, 2, 0],
        rotations[:, 1, 0] - rotations[:, 0, 1],
    )
    sum_xy, sum_xz, sum_yz = (
        rotations[:, 0, 1] + rotations[:, 1, 0],
        rotations[:, 0, 2] + rotations[:, 2, 0],
        rotations[:, 1, 2] + rotations[:, 2, 1],
    )
    cases = np.stack(
        [
            np.stack(
                [diff_x / fourfold[:, 0], diff_y / fourfold[:, 0], diff_z / fourfold[:, 0], fourfold[:, 0] / 4], axis=1
            ),
            np.stack(
                [fourfold[:, 1] / 4, sum_xy / fourfold[:, 1], sum_xz / fourfold[:, 1], diff_x / fourfold[:, 1]], axis=1
            ),
            np.stack(
                [sum_xy / fourfold[:, 2], fourfold[:, 2] / 4, sum_yz / fourfold[:, 2], diff_y / fourfold[:, 2]], axis=1
            ),
            np.stack(
                [sum_xz / fourfold[:, 3], sum_yz / fourfold[:, 3], fourfold[:, 3] / 4, diff_z / fourfold[:, 3]], axis=1
            ),
        ],
        axis=1,
    )
    quaternions = cases[np.arange(len(rotations)), np.argmax(diagonals, axis=1)]
    return quaternions / np.linalg.norm(quaternions, axis=1, keepdims=True)


def prop_words(colours: np.ndarray) -> np.ndarray:
    """The colour words of the island's stored prop colours ([n, 4] floats, white 1): ``UnpackPropColor``'s bytes,
    alpha over 255, red, green and blue over 127.5 (rendering/island-1800-2205-view.md 15) [I: the engine's
    packing not read; the island's white is the shader's 1]."""
    stored = np.asarray(colours, np.float64).reshape(-1, 4)
    rgb = np.clip(np.rint(stored[:, :3] * 127.5), 0, 255).astype(np.uint32)
    alpha = np.clip(np.rint(stored[:, 3] * 255.0), 0, 255).astype(np.uint32)
    return (alpha << 24) | (rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2]


def prop_rows(worlds: np.ndarray, flags: int = NO_VISIBILITY_CHECK, colour_word=PROP_WHITE) -> np.ndarray:
    """The props' instance data ([n, 4, 4] float32: ``GetPropInstanceData``'s four float4 each) of ``worlds`` ([n, 4,
    4], column vectors): the place and fade (shown), the turn as a quaternion, the scale and the flags word, the colour
    word, the height in its own space, the shadow's fade. The view's mirror is carried by a negative x scale: the
    shaders turn ``position x scale`` and divide the normal by the scale [ours]."""
    world_matrices = np.asarray(worlds, np.float64).reshape(-1, 4, 4)
    linear = world_matrices[:, :3, :3]
    scale = np.linalg.norm(linear, axis=1)  # the columns' lengths
    scale[:, 0] *= np.where(np.linalg.det(linear) < 0, -1.0, 1.0)
    turn = _quaternions(linear / np.where(np.abs(scale) > 1e-12, scale, 1.0)[:, None, :])
    out = np.zeros((len(world_matrices), 4, 4), np.float32)
    out[:, 0, :3], out[:, 0, 3] = world_matrices[:, :3, 3], 1.0
    out[:, 1], out[:, 2, :3] = turn, scale
    word_view = out.view(np.uint32)
    word_view[:, 2, 3], word_view[:, 3, 0] = flags, colour_word
    out[:, 3, 2] = 1.0
    return out


def prop_caster_rows(props: np.ndarray, pairs: np.ndarray) -> np.ndarray:
    """The props' casters' rows of the (instance, cascade) ``pairs`` of the props' ``prop_rows``: the instance's data
    with its cascade in the colour word's bits 0 and 1, as the engine's culling hands them
    (``CalculatePropShadowCullClipDistances``)."""
    rows = np.asarray(props, np.float32).reshape(-1, 4, 4)[pairs[:, 0]]
    word_view = rows.view(np.uint32)
    word_view[:, 3, 0] = (word_view[:, 3, 0] & np.uint32(0xFFFFFFFC)) | pairs[:, 1].astype(np.uint32)
    return rows


def chunk_size(bones: int) -> int:
    """The instances one draw holds: its words, and the matrix buffer's elements over the matrices each takes."""
    return max(1, min(CHUNK, MATRICES // max(1, bones), CASTER_ROWS // 4))


def group(name: str, own: tuple = OWN) -> str:
    """Whose a constant buffer is: the draw's (``own``: an object draw's instances), the part's (its material) or
    the frame's."""
    return "draw" if name in own else "part" if name.startswith(MATERIAL) else "frame"


def _aligned(size_bytes: int) -> int:
    """``size`` bytes rounded up to a dynamic offset's alignment (256)."""
    return (int(size_bytes) + 255) // 256 * 256


class CFrameRows:
    """A frame's instance data of every object draw (the 117 view's ``gameinstances.CFrameInstances`` for 1800's
    constant buffers): per buffer name one buffer for all draws, a draw binding its part by a dynamic offset (1800
    binds its instance buffers as root descriptors into one upload buffer), and the buffers whose content repeats from
    draw to draw made once (``fixed``: a run's words, the casters' fade). ``begin`` a frame, ``place`` a draw's bytes
    while the frame's draws are made, ``upload`` once before the passes (a still frame sends nothing)."""

    def __init__(self, ctx) -> None:
        self.m_ctx, self.m_rhi = ctx, ctx.m_rhi
        self.m_buffers: dict[str, object] = {}
        self.m_made: dict[tuple, object] = {}  # (layout, content key) -> a fixed buffer
        self.m_key: tuple | None = None  # what the frame's rows were placed for (``place_runs``)
        # a draw's slot by a dynamic offset on Direct3D 12; on 11 that ended the process (2026-11-14), so there each
        # draw binds its slot at a fixed offset, a binding set per offset (island-1800-2205-view.md 16)
        self.m_is_dynamic = ctx.m_rhi.backend() == QRhi.Implementation.D3D12
        self.begin()

    def begin(self) -> None:
        self._m_parts: dict[str, list] = {}
        self._m_ends: dict[str, int] = {}
        self._m_reach: dict[str, int] = {}
        self._m_sent = False

    def place(self, name: str, raw: np.ndarray, reach: int) -> int:
        """A draw's bytes (uint8) into the buffer ``name``, its binding ``reach`` bytes long: the byte offset."""
        return int(self.place_many(name, np.asarray(raw, np.uint8)[None], reach)[0])

    def place_many(self, name: str, rows: np.ndarray, reach: int) -> np.ndarray:
        """Draws' bytes of one length at once (uint8 [n, k], a row a draw) into the buffer ``name``, each binding
        ``reach`` bytes long: their byte offsets."""
        rows = np.asarray(rows, np.uint8)
        row_count, stride = len(rows), _aligned(rows.shape[1])
        offset_bytes = self._m_ends.get(name, 0)
        block = np.zeros((row_count, stride), np.uint8)
        block[:, : rows.shape[1]] = rows
        self._m_parts.setdefault(name, []).append((offset_bytes, block.ravel()))
        self._m_ends[name] = offset_bytes + row_count * stride
        self._m_reach[name] = max(self._m_reach.get(name, 0), _aligned(reach))
        return offset_bytes + np.arange(row_count) * stride

    def buffer(self, name: str):
        """The buffer ``name`` with room for this frame's rows and the last binding's reach past them (made anew,
        twice that, when it grows: a binding set of the old one is made again)."""
        need = self._m_ends.get(name, 0) + max(self._m_reach.get(name, 0), 256)
        buffer = self.m_buffers.get(name)
        if buffer is None or buffer.size() < need:
            if buffer is not None:
                self.m_ctx.later(buffer)
                need *= 2
            buffer = self.m_buffers[name] = gamedraw.uniform_buffer(self.m_rhi, need)
        return buffer

    def fixed(self, batch, reflection, key, fill) -> object:
        """A buffer of ``reflection``'s layout whose content is ``fill(Constants)``'s, made once per ``key``."""
        cache_key = (layout_key(reflection), key)
        buffer = self.m_made.get(cache_key)
        if buffer is None:
            constants = gameshader.CConstants(reflection)
            fill(constants)
            buffer = self.m_made[cache_key] = gamedraw.uniform_buffer(self.m_rhi, len(constants.m_data))
            batch.updateDynamicBuffer(buffer, 0, bytes(constants.m_data))
        return buffer

    def upload(self, batch) -> None:
        """The frame's rows into their buffers (once a frame; grown first where needed)."""
        if self._m_sent:
            return
        self._m_sent = True
        for name, parts in self._m_parts.items():
            data = np.zeros(self._m_ends[name], np.uint8)
            for offset_bytes, raw in parts:
                data[offset_bytes : offset_bytes + len(raw)] = raw
            batch.updateDynamicBuffer(self.buffer(name), 0, data.tobytes())

    def release(self) -> None:
        for buffer in (*self.m_buffers.values(), *self.m_made.values()):
            self.m_ctx.later(buffer)
        self.m_buffers, self.m_made, self.m_key = {}, {}, None
        self.begin()


@dataclass(frozen=True)
class SBinding:
    """How a mesh's draws in one pass bind (``CObjectDraw``, ``CDraws``): the pass's frame buffers (``shared``), the
    names of a draw's own buffers (``own``, ``group``), the frame's instance rows (None: each draw its own buffers),
    the mirrored winding and the stand-ins of the textures no pass hands."""

    shared: CSharedBuffers | None = None
    own: tuple = OWN
    rows: CFrameRows | None = None
    is_mirrored: bool = False
    fallbacks: dict = field(default_factory=dict)


class CObjectDraw(CFxcDraw):
    """A draw of an object mesh's instances whose constant buffers are filled by group (``group``): the frame's from
    ``shared``, the part's written once, the draw's own every frame; with a ``lead`` (the mesh's first draw in the
    pass, prepared before it and let go with it: ``CDraws``) a further draw of more instances, everything but its
    own instance buffers the lead's (its pipeline, vertices, samplers, the frame's and the part's buffers). With
    ``rows`` (``CFrameRows``) its own buffers are the frame's: ``slot`` bound by its offset, ``fixed`` made once, the
    rest ``own_rows`` (``place_runs``); a further draw with no rest of its own draws by the lead's binding set."""

    def __init__(self, ctx, program: gamedraw.CProgram, mesh: gamedraw.CMesh, state, dummies,
                 binding: SBinding | None = None, lead: CObjectDraw | None = None) -> None:  # fmt: skip
        super().__init__(ctx, program, mesh, state, dummies)
        resolved = binding or SBinding()
        self.m_lead, self.m_shared, self.m_own, self.m_rows = lead, resolved.shared, resolved.own, resolved.rows
        self.m_is_mirrored, self.m_fallbacks = resolved.is_mirrored, dict(resolved.fallbacks)
        if lead is not None:  # the lead's, as they are now
            self.m_own, self.m_rows, self.m_is_mirrored, self.m_fallbacks = (
                lead.m_own,
                lead.m_rows,
                lead.m_is_mirrored,
                lead.m_fallbacks,
            )
        self._m_fresh: set[str] = set()  # the part's buffers not written yet
        self._m_bound_buffers: list = []
        self.m_slot: tuple[str, int] | None = None  # rows: (the buffer bound by its dynamic offset, the offset)
        self.m_fixed: dict[str, tuple] = {}  # rows: own buffers of repeating content, name -> (key, fill)
        self.m_own_rows: dict[str, bytes] = {}  # rows: the rest of its own buffers' bytes this frame
        self._m_sent_rows: dict[str, bytes] = {}

    def _framed(self, name: str) -> bool:
        """Whether an own buffer is the frame's (``CFrameRows``): its slot or a fixed one."""
        return self.m_rows is not None and (
            (self.m_slot is not None and name == self.m_slot[0]) or name in self.m_fixed
        )

    def _own_buffers(self) -> None:
        for name, cbuffer in self.m_program.m_cbuffers.items():
            kind = group(name, self.m_own)
            if (kind == "draw" and not self._framed(name)) or (self.m_lead is None and kind == "part"):
                self.m_constants[name] = gameshader.CConstants(cbuffer)
                self.m_buffers[name] = gamedraw.uniform_buffer(self.m_rhi, len(self.m_constants[name].m_data))
                self.m_keep.append(self.m_buffers[name])
        self._m_fresh = {part_name for part_name in self.m_constants if group(part_name, self.m_own) == "part"}

    def _borrowed(self, batch) -> dict:
        """The buffers it binds but does not own (the lead's, the frame's rows, or the shared ones), those first bound
        this frame by name (``CConstants``)."""
        due = {}
        for name, cbuffer in self.m_program.m_cbuffers.items():
            if name in self.m_constants:
                continue
            if self.m_lead is not None:
                self.m_buffers[name] = self.m_lead.m_buffers[name]
            elif self._framed(name):
                fixed = self.m_fixed.get(name)
                rows = self.m_rows
                self.m_buffers[name] = rows.fixed(batch, cbuffer, *fixed) if fixed is not None else rows.buffer(name)
            else:
                constants, buffer, first = self.m_shared.take(cbuffer)
                self.m_buffers[name] = buffer
                if first:
                    due[name] = constants
        return due

    def _cbuffer(self, name: str, number: int, stages):
        if self.m_slot is None or name != self.m_slot[0]:
            return super()._cbuffer(name, number, stages)
        reach = _aligned(self.m_program.m_cbuffers[name].size)
        if self.m_rows.m_is_dynamic:
            return gamedraw.Binding.uniformBufferWithDynamicOffset(number, stages, self.m_buffers[name], reach)
        return gamedraw.Binding.uniformBuffer(number, stages, self.m_buffers[name], self.m_slot[1], reach)

    def prepare(self, render_pass, signature, batch, textures: dict, fill) -> None:
        lead = self.m_lead
        if self.m_rows is not None:
            self.m_rows.upload(batch)
        if lead is not None:
            self.m_pipeline = lead.m_pipeline
            if lead.m_pipeline is None:  # the lead not made yet (a blended draw sorted before it): drawn next frame
                return
            self.m_vbufs, self.m_ibuf, self._m_samplers = lead.m_vbufs, lead.m_ibuf, lead._m_samplers
            self.m_attrs, self.m_fill_stride = lead.m_attrs, lead.m_fill_stride
            if (
                self.m_rows is not None and self.m_rows.m_is_dynamic and not self.m_own_rows
            ):  # only its slot: the lead's set
                self.m_srb = lead.m_srb
                rows = self.m_rows.m_buffers.get(self.m_slot[0]) if self.m_slot is not None else None
                if self.m_slot is not None and all(rows is not buffer for buffer in lead._m_bound_buffers):
                    # the lead's set binds a buffer that grew since (sorted before it): next frame
                    self.m_pipeline = None
                return
            if self.m_srb is lead.m_srb:  # its own set from now on: the lead's is not its to let go
                self.m_srb = None
        elif not self.m_keep:
            self._vertex_resources(batch)
        if not self.m_constants:
            self._own_buffers()
        due = self._borrowed(batch)
        bound = [self.m_buffers[buffer_name] for buffer_name in self.m_program.m_cbuffers]
        if self.m_slot is not None and not self.m_rows.m_is_dynamic:  # bound at its offset: a set per offset
            bound.append(self.m_slot)
        if self.m_srb is None or not same_objects(bound, self._m_bound_buffers) or self._maps_changed(textures):
            self._make_srb(textures, batch)
            self._m_bound_buffers = bound
        if lead is None and self.m_pipeline is None and not self.m_error:
            self._make_pipeline(render_pass, signature)
        part = {buffer_name: self.m_constants[buffer_name] for buffer_name in self._m_fresh}
        mine = (
            {}
            if self.m_rows is not None
            else {
                buffer_name: buffer_constants
                for buffer_name, buffer_constants in self.m_constants.items()
                if group(buffer_name, self.m_own) == "draw"
            }
        )
        for constants in (due, part, mine):
            if constants:
                fill(constants)
                for name, buffer_constants in constants.items():
                    batch.updateDynamicBuffer(self.m_buffers[name], 0, bytes(buffer_constants.m_data))
        self._m_fresh = set()
        for name, raw in self.m_own_rows.items():  # sent only when they changed (a still frame sends nothing)
            if self._m_sent_rows.get(name) != raw:
                batch.updateDynamicBuffer(self.m_buffers[name], 0, raw)
                self._m_sent_rows[name] = raw

    def draw(self, command_buffer, size_px) -> None:
        if self.m_pipeline is None:
            return
        dynamic_offset = None
        if self.m_slot is not None and self.m_rows.m_is_dynamic:
            dynamic_offset = (self.m_program.m_numbers[(self.m_slot[0], "cbuffer")], self.m_slot[1])
        self.m_mesh.draw(
            command_buffer,
            self.m_pipeline,
            self.m_srb,
            size_px,
            (self.m_vbufs, self.m_ibuf),
            self.m_instances,
            dynamic_offset,
        )

    def release(self) -> None:
        if self.m_lead is not None and self.m_srb is self.m_lead.m_srb:  # borrowed: the lead lets it go
            self.m_srb = None
        super().release()
        self._m_fresh, self._m_bound_buffers, self._m_sent_rows = set(), [], {}


class CDraws:
    """One mesh's draws in one pass, kept from frame to frame: the first (``lead``) makes the pipeline and binds the
    shared buffers, the further ones draw more instances through it; ``take()`` hands this frame's next. With the
    binding's ``rows`` their instance data is the frame's (``CFrameRows``); without its stand-ins the forward
    frame's (``frameforward.FALLBACKS``)."""

    def __init__(
        self, ctx, program: gamedraw.CProgram, mesh: gamedraw.CMesh, state, dummies, binding: SBinding
    ) -> None:
        binding = binding if binding.fallbacks else replace(binding, fallbacks=dict(frameforward.FALLBACKS))
        self.m_lead = CObjectDraw(ctx, program, mesh, state, dummies, binding)
        self.m_chunks: list[CObjectDraw] = []
        self.m_used = 0

    def begin(self) -> None:
        self.m_used = 0

    def take(self) -> CObjectDraw:
        k, self.m_used = self.m_used, self.m_used + 1
        if k == 0:
            return self.m_lead
        while len(self.m_chunks) < k:
            lead = self.m_lead
            self.m_chunks.append(
                CObjectDraw(lead.m_ctx, lead.m_program, lead.m_mesh, lead.m_state, lead.m_dummies, lead=lead)
            )
        return self.m_chunks[k - 1]

    def taken(self) -> list[CObjectDraw]:
        """This frame's draws so far, the lead first."""
        return [self.m_lead, *self.m_chunks][: self.m_used]

    def release(self) -> None:
        for object_draw in (*self.m_chunks, self.m_lead):  # the further draws first: they may bind the lead's set
            object_draw.release()
        self.m_chunks, self.m_used = [], 0


class CCast:
    """A run's draw into the sun's cascades: the frame sets ``instances`` to the cascades it reads, the draw makes a
    copy per instance and cascade; the part's maps are taken here (the shadow pass runs before the colour pass). The
    frame's shadow values go into the frame's buffers only: its fill wrote all of them into each draw's own too."""

    def __init__(self, draw: CFxcDraw, run: CRun) -> None:
        self.m_draw_call, self.m_run, self.m_instances = draw, run, 1
        # the (instance, cascade) pairs drawn (``cull_casts``); None: all of them
        self.m_pairs: np.ndarray | None = None

    def fresh(self) -> bool:
        """Whether its draw's resources are not made yet (``nativepass.prepared`` spreads the first prepares)."""
        return not self.m_draw_call.m_keep

    def count(self) -> int:
        """The copies one draw makes: the pairs, or every instance into each cascade the frame reads."""
        if self.m_pairs is not None:
            return int(np.count_nonzero(self.m_pairs[:, 1] < max(1, self.m_instances)))
        return len(self.m_run.m_worlds) * max(1, self.m_instances)

    def drawn(self, used: int) -> np.ndarray:
        """The pairs drawn when the frame reads ``used`` cascades (without a cull: every instance into each, by
        cascade as ``cascade_pairs`` orders them; kept as the pairs)."""
        if self.m_pairs is None:
            instance_count = len(self.m_run.m_worlds)
            self.m_pairs = np.stack(
                [np.tile(np.arange(instance_count), used), np.repeat(np.arange(used), instance_count)], axis=1
            )
        return self.m_pairs[self.m_pairs[:, 1] < max(1, used)]

    def prepare(self, render_pass, signature, batch, textures: dict, fill) -> None:
        self.m_draw_call.m_instances = self.count()

        def own(constants: dict) -> None:
            (fill if group(next(iter(constants))) == "frame" else self.m_run.fill_caster)(constants)

        self.m_draw_call.prepare(render_pass, signature, batch, {**textures, **self.m_run.m_part.textures(batch)}, own)

    def draw(self, command_buffer, size_px) -> None:
        if self.m_draw_call.m_instances:
            self.m_draw_call.draw(command_buffer, size_px)


class CRun:
    """Up to ``chunk_size`` instances of a mesh as the forward frame's model (``frameforward.CForwardModel``'s
    shape): its draw, its caster (``CCast``, None for none), its ``part`` (``fields``, ``transparent``,
    ``textures(batch)``), the instances' ``worlds``, their spheres and the matrices each takes (``bones``)."""

    m_water = False

    def __init__(self, draw: CFxcDraw, cast: CFxcDraw | None, part, worlds: np.ndarray, spheres: tuple,
                 bones: int = 1) -> None:  # fmt: skip
        self.m_draw_call, self.m_part, self.m_worlds, self.m_bones = draw, part, worlds, max(1, bones)
        self.m_draw_call.m_instances = len(worlds)
        self.m_shadow = CCast(cast, self) if cast is not None else None
        self.m_blended, self.m_later = part.m_is_transparent, []
        self.m_world = np.asarray(worlds[0], float) if len(worlds) else np.identity(4)
        self.m_centres, self.m_radii = spheres
        self.m_prepass: CFxcDraw | None = None  # its draw in the frame's normals-and-depth pre-pass (``with_prepass``)

    def fresh(self) -> bool:
        """Whether its draw's resources are not made yet (``nativepass.prepared`` spreads the first prepares)."""
        return not self.m_draw_call.m_keep

    def with_prepass(self, draw: CFxcDraw | None) -> None:
        """Its draw of the same instances in the frame's pre-pass (an opaque part's; None: none)."""
        self.m_prepass = draw
        if draw is not None:
            draw.m_instances = len(self.m_worlds)

    def textures(self) -> dict:
        return self.m_part.textures(None)

    def prepare(self, render_pass, signature, batch, camera: gameshader.SCamera, light: dict, extra: dict | None = None,
                draw: CFxcDraw | None = None) -> None:  # fmt: skip
        """This frame's constants: the frame's and the material's into the mesh's first draw, the instances into
        each."""
        target_draw = draw or self.m_draw_call

        def fill(constants: dict) -> None:  # the frame's and the part's buffers (its own are the frame's rows)
            if group(next(iter(constants))) == "frame":
                frameforward.fill_frame(constants, camera, light)
            else:
                frameforward.fill_fields(constants, self.m_part.fields)

        target_draw.prepare(render_pass, signature, batch, {**self.m_part.textures(batch), **(extra or {})}, fill)

    def fill_caster(self, constants: dict) -> None:
        """The cascaded pass's part buffers (beside the frame's shadow values): the material."""
        if group(next(iter(constants))) == "part":
            frameforward.fill_fields(constants, self.m_part.fields)

    fill_model = fill_caster  # 2205's single map fills a caster by this name; its words are the frame's rows too

    def place(self, rows: CFrameRows, used: int) -> None:
        """Its draws' instance data into the frame's ``rows``: the matrices in one slot its colour, pre-pass and
        caster draws share, the words fixed per ``bones`` (each draw's start at 0), the casters' words of their
        (instance, cascade) pairs below ``used`` cascades the caster's own."""
        program = self.m_draw_call.m_program
        slot = place_slot(rows, program, frameforward.MATRIX_BUFFER, "cModelMatrixBuffer",
                     matrix_elements(self.m_worlds, self.m_bones))  # fmt: skip
        bones = self.m_bones
        fixed = {"cbModel": (("words", bones), lambda constants: constants.set_elements("cModelInstanceData",
                                                                        words(chunk_size(bones), bones)))}  # fmt: skip
        hand_slots([self.m_draw_call, self.m_prepass], slot, fixed)
        if self.m_shadow is not None:
            own = {CASTERS: pair_words(len(self.m_worlds), bones, self.m_shadow.drawn(used)).tobytes()}
            hand_slots([self.m_shadow.m_draw_call], slot, fixed, own)

    def draw(self, command_buffer, size_px) -> None:
        self.m_draw_call.draw(command_buffer, size_px)

    def corners(self) -> np.ndarray:
        """The box of the instances' spheres (the shadow's fit)."""
        min_corner = (self.m_centres - self.m_radii[:, None]).min(0)
        max_corner = (self.m_centres + self.m_radii[:, None]).max(0)
        return np.array(
            [
                [x, y, z]
                for x in (min_corner[0], max_corner[0])
                for y in (min_corner[1], max_corner[1])
                for z in (min_corner[2], max_corner[2])
            ]
        )

    def release(self) -> None:
        """Nothing: its draws are its part's (``CDraws``)."""


class CPropRun(CRun):
    """A run of props (``PROP_CHUNK`` at most): ``CRun`` with the props' instance data (``prop_rows``, made for the
    frame's props at once: ``place_runs``) in place of the models' words and matrices, its casters' rows per
    (instance, cascade); ``colours`` its props' colour words (``prop_words``; None: white)."""

    m_colours: np.ndarray | None = None

    def words(self) -> np.ndarray:
        """Its props' colour words, white where it was handed none."""
        return np.full(len(self.m_worlds), PROP_WHITE, np.uint32) if self.m_colours is None else self.m_colours

    def place(self, rows: CFrameRows, used: int, data: np.ndarray | None = None) -> None:
        """Its draws' instance data into the frame's ``rows``: its props' rows (``data``, else made here) with the fade
        in its colour draw's slot, the casters' rows of their pairs below ``used`` cascades in the caster's, the
        caster's fade fixed."""
        data = prop_rows(self.m_worlds, colour_word=self.words()) if data is None else data
        program = self.m_draw_call.m_program
        slot = place_slot(rows, program, "cbProp", "cPropInstanceData", data.reshape(-1, 4), shown_fade)
        hand_slots([self.m_draw_call, self.m_prepass], slot, {})
        if self.m_shadow is not None:
            cast = self.m_shadow.m_draw_call
            pairs = self.m_shadow.drawn(used)
            slot = place_slot(rows, cast.m_program, PROP_CASTERS, "cPropGPUCulledInstanceData",
                         prop_caster_rows(data, pairs).reshape(len(pairs), 16))  # fmt: skip
            hand_slots([cast], slot, {"cbProp": ("shown", shown_fade)})


def shown_fade(constants) -> None:
    """A prop buffer's fade: shown, not fading."""
    constants.set("cPropFadeType", 1.0)


_g_members: dict[tuple, tuple] = {}  # (layout, member) -> (offset, element stride, the buffer's bytes)
_g_templates: dict[tuple, np.ndarray] = {}  # (layout, fill) -> a buffer's bytes as ``fill`` leaves them


def _member(reflection, path: str) -> tuple[int, int]:
    """(offset, element stride) of an array member of a constant buffer, looked up once per layout."""
    key = (layout_key(reflection), path)
    got = _g_members.get(key)
    if got is None:
        constants = gameshader.CConstants(reflection)
        first, second = constants.find(f"{path}[0]"), constants.find(f"{path}[1]")
        got = _g_members[key] = (first[0], second[0] - first[0] if second is not None else reflection.size)
    return got


def slot_bytes(reflection, path: str, rows: np.ndarray, fill=None) -> np.ndarray:
    """A draw's bytes of a constant buffer whose array ``path`` holds ``rows`` (one per element, from element 0):
    up to the last row, or (``fill``) the whole buffer as ``fill(Constants)`` leaves it with the rows in it."""
    offset, stride = _member(reflection, path)
    rows = np.ascontiguousarray(rows)
    raw = rows.reshape(len(rows), int(np.prod(rows.shape[1:], dtype=np.int64))).view(np.uint8)
    row_count = len(raw)
    if fill is None:
        out = np.zeros(offset + row_count * stride, np.uint8)
    else:
        key = (layout_key(reflection), fill)
        if key not in _g_templates:
            constants = gameshader.CConstants(reflection)
            fill(constants)
            _g_templates[key] = np.frombuffer(bytes(constants.m_data), np.uint8)
        out = _g_templates[key].copy()
    out[offset : offset + row_count * stride].reshape(row_count, stride)[:, : raw.shape[1]] = raw
    return out


def place_slot(rows: CFrameRows, program, name: str, path: str, data: np.ndarray, fill=None) -> tuple | None:
    """``data`` placed into the frame's buffer ``name`` as ``program`` binds it: (name, offset), None where it does
    not bind it."""
    cbuffer = program.m_cbuffers.get(name)
    if cbuffer is None:
        return None
    return name, rows.place(name, slot_bytes(cbuffer, path, data, fill), cbuffer.size)


def hand_slots(draws: list, slot: tuple | None, fixed: dict, own: dict | None = None) -> None:
    """A run's draws (None: none) take this frame's slot, fixed buffers and own rows where their program binds
    them."""
    for draw in draws:
        if draw is None:
            continue
        names = draw.m_program.m_cbuffers
        draw.m_slot = slot if slot is not None and slot[0] in names else None
        draw.m_fixed = {name: value for name, value in fixed.items() if name in names}
        draw.m_own_rows = {name: value for name, value in (own or {}).items() if name in names}


def place_runs(rows: CFrameRows, runs: list, used: int) -> None:
    """The frame's instance data of every run at once (``CFrameRows``): the props' rows made in one go, each run's
    slots placed; ``used``: the cascades the frame reads."""
    rows.begin()
    props = [run for run in runs if isinstance(run, CPropRun)]
    if props:
        every = prop_rows(
            np.concatenate([run.m_worlds for run in props]), colour_word=np.concatenate([run.words() for run in props])
        )
        ends = np.cumsum([len(run.m_worlds) for run in props])
        for run, data in zip(props, np.split(every, ends[:-1]), strict=True):
            run.place(rows, used, data)
    for run in runs:
        if not isinstance(run, CPropRun):
            run.place(rows, used)


def cull_casts(casts: list, matrices: list) -> None:
    """Each cast's (instance, cascade) pairs, only those whose sphere reaches into the cascade (``cascade_pairs``),
    for all of them at once."""
    if not casts:
        return
    sizes = np.array([len(cast.m_run.m_centres) for cast in casts])
    pairs = cascade_pairs(np.concatenate([cast.m_run.m_centres for cast in casts]),
                          np.concatenate([cast.m_run.m_radii for cast in casts]), matrices)  # fmt: skip
    starts = np.cumsum(sizes) - sizes
    owner = np.searchsorted(np.cumsum(sizes), pairs[:, 0], side="right")
    order = np.argsort(owner, kind="stable")  # each cast's pairs kept by cascade, as its own call gave them
    pairs, owner = pairs[order], owner[order]
    bounds = np.searchsorted(owner, np.arange(len(casts) + 1))
    for k, cast in enumerate(casts):
        got = pairs[bounds[k] : bounds[k + 1]].copy()
        got[:, 0] -= starts[k]
        cast.m_pairs = got


def plain_texture(rhi, batch, dds):
    """A map as one 2D texture (the model shaders read ``Texture2D``): its blocks as stored with every level (the
    levels a file stops short of made: ``mipchain``), or its first level as RGBA; never sRGB (the shaders turn colour
    into linear light themselves)."""
    stored, _key = gametextures.map_kind(dds)
    dds_info = dds.m_info
    if stored:
        flags = QRhiTexture.Flag.MipMapped if dds_info.mips > 1 else QRhiTexture.Flag(0)
        texture = rhi.newTexture(
            gametextures.BLOCK_FORMATS[dds_info.format], QSize(dds_info.width_px, dds_info.height_px), 1, flags
        )
        levels = [dds.stored(level) for level in range(dds_info.mips)]
        levels = mipchain.full_levels(dds_info, levels) if dds_info.mips > 1 else levels
    else:
        rgba = np.ascontiguousarray(dds.level(0), np.uint8)
        texture = rhi.newTexture(TextureFormat.RGBA8, QSize(rgba.shape[1], rgba.shape[0]))
        levels = [rgba.tobytes()]
    texture.create()
    upload = QRhiTextureUploadDescription()
    upload.setEntries([QRhiTextureUploadEntry(0, level, QRhiTextureSubresourceUploadDescription(raw))
                     for level, raw in enumerate(levels)])  # fmt: skip
    batch.uploadTexture(texture, upload)
    return texture


def named_maps(files, names: dict, folder: str = "") -> dict:
    """In the background: the maps a pass reads by the shaders' names (``names``: name -> the file under
    ``folder``) as ``CDdsFile``s for ``plain_texture``; one that does not read is left to its stand-in."""
    from ..models import ddsdecode

    out = {}
    for name, path in names.items():
        try:
            out[name] = ddsdecode.CDdsFile(files.read(folder + path))
        except (OSError, KeyError, ValueError):
            continue
    return out


class CMaps2D(gamemaps.CMapPool):
    """117's pool of maps (``gamemaps.CMapPool``: once per file, counted by the parts that name it, coarser levels past
    the quality's largest side) with each map one plain 2D texture (``plain_texture``)."""

    def take(self, batch, path: str) -> object:
        texture = self.m_arrays.get(path)
        if texture is None:
            dds = self.m_pending.pop(path)
            dds = dds if isinstance(dds, gamemaps._CKept) else gamemaps.smaller(dds, self.m_max_size)
            texture = self.m_arrays[path] = plain_texture(self.m_rhi, batch, dds)
        return texture
