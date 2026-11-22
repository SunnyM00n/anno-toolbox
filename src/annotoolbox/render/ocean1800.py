"""Anno 1800's ocean by its own shaders: the surface (``water_ocean.fx`` ``WaterFFT_TriList``: patches of a grid
around the eye, displaced by the engine's small-wave volumes and the large waves of the setting's spectrum, lit,
refracting the frame's copy of the scene) with its small-wave gradient map (``waves1800``) and its large waves' FFT
(``oceanfft1800``); the patches' own values in one buffer a frame (``objects1800.CFrameRows``).

``compile_stages(source)``, ``read_water(text)`` and ``read_maps(files)`` run in the background; ``COcean1800(ctx,
stages, water, maps, volumes, dummies)`` is the device side: ``set_session(infos, flow, size)``, then per frame
``run(ctx, seconds)`` and ``models(eye, sun)``. docs/rendering/island-1800-2205-view.md 16, 17.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models.shaderforward import CNoPass
from . import frameforward, gamedraw, objects1800, oceanfft1800, sea2205, waves1800
from . import gameshader as gs
from .fxcdraw import CSharedBuffers, layout_key
from .gamedraw import CProgram

FX = waves1800.EFFECT
TECHNIQUE = "WaterFFT_TriList"
EFFECTS = "data/graphics/effects/"
# the maps the surface reads by their names [I: by name, the engine's assignment is not read]
MAPS = {
    "cPerlinNoiseTex": "noise/perlin_noise.dds", "cNoiseTex3D": "noise/noise_3d.dds", "cNoiseTex2": "water/noise.dds",
    "cBlueNoiseTex": "noise/blue_noise_64_r8g8_0.dds", "cWaterFoamMap": "water/foam_0.dds",
    "cWaterRippleBumpMap": "water/ripples.dds",
}  # fmt: skip
# what the view has none of: no planar reflection, no underwater map, no buildings' footprint; flat large waves
# where their FFT does not compile [ours]
FALLBACKS = {
    **sea2205.FALLBACKS, "cLargeWaveDisplacementMap": (0, 0, 0, 0), "cLargeWaveGradientMap": (0, 0, 0, 0),
    "cUnderwaterHeightMap": (0, 0, 0, 0), "cPropBlurredVisibilityTex": (0, 0, 0, 0), "cUndiscoveredMask": (0, 0, 0, 0),
    "cWaterInfoMap": (0, 0, 0, 255), "cSmallWaveGradientMap": (0, 0, 0, 0),
}  # fmt: skip
# cbOceanFFT as the engine uploads it from its ocean simulator's start values (taken from how the game behaves)
PATCH = 4096.0  # the simulator's first value: the FFT patch in the engine's units
DISPLACEMENT = 1 / 256  # cDisplacementScale
OCEAN = {
    "cPerlinAmplitude": [35.0, 39.0, 46.0], "cTexelLengthX2": PATCH / 512 * 2, "cPerlinOctave": [0.45, 0.23, 1.09],
    "cUVScale": 1 / (PATCH * DISPLACEMENT), "cPerlinGradient": [2.0, 2.2, 2.6], "cPerlinSize": 2.6,
    "cUVOffset": 1 / 1024, "cGridLen": 512 / PATCH, "cDisplacementScale": DISPLACEMENT, "cWaterInfoDebugMode": -1.0,
}  # fmt: skip
SPAN = 5  # the patches cover the island's square and twice as much again on every side [ours]
NEAR_PATCH, SPLIT = 32.0, 1.5  # the smallest patch's side, split while the eye is nearer than 1.5 sides [ours]
PATCH_BUFFER = "cbOceanFFTPerCall"  # a patch's own values (a root descriptor in 1800): its slot of the frame's rows
OCEAN_BUFFERS = ("cbOceanFFT", "cbWater")  # the ocean's values, the same for every patch of a frame


@dataclass(frozen=True)
class SWater:
    """A setting's ``SWater`` block: the large waves' spectrum (``oceanfft1800.init_values``) and the colours."""

    amplitude: float = 0.25
    choppiness: float = 1.6
    size: float = 800.0
    damping: float = 0.5
    seed: int = 13
    deep: tuple = (0.054902, 0.160784, 0.270588)
    shallow: tuple = (0.0, 0.77, 0.82)
    view_depth: float = 12.0
    light_depth: float = 10.0
    absorption: float = 3.5
    caustics: float = 1.0


def read_water(text: str) -> SWater:
    """The ``SWater`` block of a local ambient setting (the record's defaults where it has none: ``CWaterBlock``)."""
    block, water = sea2205.CWaterBlock(text), SWater()
    num = block.num
    return SWater(num("WaveAmplitude", water.amplitude), num("WaveChoppiness", water.choppiness), num("WaveSize", water.size),
                 num("WindDamping", water.damping), int(num("WaveSeed", water.seed)), block.vec("WaterColorDeep", "xyz", water.deep),
                 block.vec("WaterColorShallow", "xyz", water.shallow), num("ViewDepth", water.view_depth),
                 num("LightDepth", water.light_depth), num("Absorption", water.absorption),
                 num("CausticsIntensity", water.caustics))  # fmt: skip


def frame_values(water: SWater) -> dict:
    """The setting's water in the frame's ``cbPerFrame`` (``constants.fxh``), the water clean (``cWaterQuality``, the
    island's pollution: at 0 the sea takes its polluted olive) [ours]."""
    return {"cWaterViewDepth": water.view_depth, "cWaterLightDepth": water.light_depth,
            "cAbsorption": water.absorption, "cCausticsIntensity": water.caustics, "cWaterQuality": 1.0}  # fmt: skip


def compile_stages(source) -> dict:
    """In the background: the surface's technique (stages, states), the small waves' gradient pass and the large
    waves' passes (None where they do not compile: flat), with the engine's defines of the ocean's effect
    (``waves1800.DEFINES``)."""
    try:
        large = oceanfft1800.compile_stages(source)
    except (CNoPass, RuntimeError):
        large = None
    return {"water": (source.effect_pass(FX, waves1800.DEFINES, TECHNIQUE),
                      source.technique_state(FX, waves1800.DEFINES, TECHNIQUE)),
            "gradients": waves1800.compile_stage(source), "large": large}  # fmt: skip


def read_maps(files) -> dict:
    """In the background: the surface's maps by the shaders' names (``MAPS``, ``objects1800.named_maps``)."""
    return objects1800.named_maps(files, MAPS, EFFECTS)


def patch_values(x: float, z: float, side: float, eye) -> dict:
    """``cbOceanFFTPerCall`` of a patch of ``side`` at (x, z): the grid's 0..1 corners scaled and placed in the local
    space, which is the world's here (the waves' coordinates run on across the patches), the eye in it [ours]."""
    local = np.identity(4)
    local[0, 0] = local[2, 2] = side
    local[3, 0], local[3, 2] = x, z  # row vectors: mul(position, M)
    return {"cMtxOceanLocal": local, "cMtxOceanWorld": np.identity(4), "cUVBase": [0.0, 0.0],
            "cPerlinMovement": [0.0, 0.0], "cLocalEye": [float(component) for component in eye]}  # fmt: skip


def stitched_indices(cell_count: int, mask: int) -> np.ndarray:
    """``sea2205.grid_mesh``'s triangles with the odd vertices of the edges in ``mask`` (bits: x 0, x 1, z 0, z 1)
    folded onto their even neighbours, the triangles that fold flat left out: that edge then meets a patch of
    twice the side vertex for vertex, with no crack where the displaced heights differ (island-1800-2205-view.md 19)
    [ours: the engine's patch mesh is not read]."""
    remap, vertex_side = np.arange((cell_count + 1) ** 2), cell_count + 1
    odd = np.arange(1, cell_count, 2)
    edges = (
        odd * vertex_side,
        odd * vertex_side + cell_count,
        odd,
        cell_count * vertex_side + odd,
    )  # the odd vertices of each edge (row z, column x)
    steps = (vertex_side, vertex_side, 1, 1)  # back to the even one before it
    for bit, (vertices, step) in enumerate(zip(edges, steps, strict=True)):
        if mask >> bit & 1:
            remap[vertices] = vertices - step
    tri = remap[sea2205.grid_mesh(cell_count).m_indices].reshape(-1, 3)
    keep = (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])
    return tri[keep].ravel().astype(np.uint32)


class CGridMesh(gamedraw.CMesh):
    """The patches' grid with its sixteen stitched variants in one index buffer (``stitched_indices``, by mask);
    ``span`` (first index, count) is the variant the next draw takes (set by ``COceanDraw.draw``)."""

    m_spans: tuple = ()
    m_span: tuple | None = None

    @classmethod
    def made(cls, cell_count: int = sea2205.GRID) -> CGridMesh:
        plain = sea2205.grid_mesh(cell_count)
        parts = [stitched_indices(cell_count, mask) for mask in range(16)]
        firsts = np.cumsum([0, *(len(part) for part in parts[:-1])])
        mesh = cls(plain.m_data, plain.m_stride, plain.m_elements, np.concatenate(parts), plain.m_count)
        mesh.m_spans = tuple((int(first), len(part)) for first, part in zip(firsts, parts, strict=True))
        return mesh

    def draw(self, command_buffer, *args, **kwargs) -> None:
        super().draw(command_buffer if self.m_span is None else _CRanged(command_buffer, *self.m_span), *args, **kwargs)


class _CRanged:
    """A command buffer whose indexed draw takes a range of the index buffer (``CGridMesh.span``)."""

    def __init__(self, command_buffer, first: int, count: int) -> None:
        self.m_command_buffer, self.m_first, self.m_count = command_buffer, first, count

    def __getattr__(self, name: str):
        return getattr(self.m_command_buffer, name)

    def drawIndexed(self, _count: int, instances: int = 1) -> None:
        self.m_command_buffer.drawIndexed(self.m_count, instances, self.m_first)


def edge_masks(cells: np.ndarray) -> np.ndarray:
    """Per patch (x, z, side rows of ``sea2205.chunks``) the mask of its edges a patch of twice its side lies
    beyond (``stitched_indices``' bits), for every patch at once; the split rule keeps neighbours within twice of
    each other."""
    x, z, cell_size = (cells[:, k].astype(np.float64) for k in range(3))
    sides = np.unique(np.round(cell_size, 3))

    def code(side, column, row) -> np.ndarray:  # one integer per (side, column, row); -1 for a side no patch has
        side = np.round(side, 3)
        k = np.minimum(np.searchsorted(sides, side), len(sides) - 1)
        known = sides[k] == side
        return np.where(known, (k * 2**21 + column.astype(np.int64) + 1) * 2**21 + row.astype(np.int64) + 1, -1)

    keys = np.unique(code(cell_size, np.round(x / cell_size), np.round(z / cell_size)))
    big, epsilon = 2 * cell_size, cell_size * 1e-3
    probes = (
        (x - epsilon, z + cell_size / 2),
        (x + cell_size + epsilon, z + cell_size / 2),
        (x + cell_size / 2, z - epsilon),
        (x + cell_size / 2, z + cell_size + epsilon),
    )
    out = np.zeros(len(cells), np.int32)
    for bit, (probe_x, probe_z) in enumerate(probes):
        found = code(big, np.floor(probe_x / big), np.floor(probe_z / big))
        out |= np.where((found >= 0) & np.isin(found, keys), 1 << bit, 0).astype(np.int32)
    return out


def surface_values(water: SWater, size_tiles: float, seconds: float) -> dict:
    """``cbOceanFFT`` and ``cbWater`` but the patch: the engine's ocean values, the wave time, the setting's colours,
    the island's maps over its square, the caustics' drift [E]."""
    drift = seconds * sea2205.DRIFT
    return {**OCEAN, "cWaveTime": waves1800.wave_time(seconds), "cChoppyScale": water.choppiness,
            "cWaterColorDeep": list(water.deep), "cWaterColorShallow": list(water.shallow),
            "cWaterInfoCoord": [1 / size_tiles, 1 / size_tiles],
            "cWaterCausticsOffset": [drift, drift * 0.7, -drift * 0.8, drift * 0.6]}  # fmt: skip


_g_patch_words: dict[tuple, tuple] = {}  # a layout -> where x, z and the side go (``patch_rows``)


def _patch_raw(reflection, x: float, z: float, side: float, eye) -> np.ndarray:
    constants = gs.CConstants(reflection)
    gs.set_values([constants], patch_values(x, z, side, eye))
    return np.frombuffer(bytes(constants.m_data), np.uint8)


def patch_rows(reflection, cells: np.ndarray, eye) -> np.ndarray:
    """Every patch's ``cbOceanFFTPerCall`` (``patch_values``) of ``cells`` (x, z, side rows) at once as bytes of
    ``reflection``'s layout ([n, size] uint8): the values all patches share written once, the three that differ
    put where the layout holds them (found once per layout)."""
    base = _patch_raw(reflection, 0.0, 0.0, 0.0, eye)
    key = layout_key(reflection)
    if key not in _g_patch_words:
        plain = base.view(np.float32)
        _g_patch_words[key] = tuple(np.flatnonzero(_patch_raw(reflection, *probe, eye).view(np.float32) != plain)
                                  for probe in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))  # fmt: skip
    out = np.repeat(base[None], len(cells), axis=0)
    words = out.view(np.float32)
    for column, at in enumerate(_g_patch_words[key]):
        words[:, at] = np.asarray(cells, np.float32)[:, column : column + 1]
    return out


class COceanDraw:
    """One patch of the ocean as a model of the forward frame's blended pass (it reads the scene's copies:
    ``water``): a draw of the ocean's ``objects1800.CDraws``, its own values its slot of the frame's rows, the ocean's
    values in the frame's buffers (written by the frame's first patch)."""

    m_blended, m_water, m_shadow, m_prepass, m_ssr = True, True, None, None, None

    def __init__(self, ocean: COcean1800) -> None:
        self.m_ocean, self.m_later = ocean, []
        self.m_draw_call: objects1800.CObjectDraw | None = None
        self.m_cell, self.m_eye = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)  # its patch (x, z, side) and the eye, world axes
        self.m_world = np.identity(4)
        self.m_box = (np.zeros(3), np.zeros(3))
        self.m_span: tuple | None = None  # its grid's stitched variant (``CGridMesh.spans``)

    @property
    def patch(self) -> dict:
        """Its ``cbOceanFFTPerCall`` values (``patch_values``; the frame writes them for all patches at once)."""
        return patch_values(*self.m_cell, self.m_eye)

    def prepare(
        self, render_pass, signature, batch, camera: gs.SCamera, light: dict, extra: dict | None = None
    ) -> None:
        values = self.m_ocean.m_values

        def fill(constants: dict) -> None:
            rest = {name: value for name, value in constants.items() if name not in OCEAN_BUFFERS}
            if rest:
                frameforward.fill_frame(rest, camera, light)
            gs.set_values([value for name, value in constants.items() if name in OCEAN_BUFFERS], values)

        self.m_draw_call.prepare(render_pass, signature, batch, {**self.m_ocean.m_textures, **(extra or {})}, fill)

    def draw(self, command_buffer, size_px) -> None:
        self.m_ocean.m_mesh.m_span = self.m_span
        self.m_draw_call.draw(command_buffer, size_px)
        self.m_ocean.m_mesh.m_span = None

    def corners(self) -> np.ndarray:
        low, high = self.m_box
        return np.array([[x, 0.0, z] for x in (low[0], high[0]) for z in (low[2], high[2])])

    def release(self) -> None:
        """Nothing: its draw is the ocean's."""


class COcean1800(sea2205.CSea2205):
    """The ocean on the device (module docstring): 2205's sea's maps, session and draws with 1800's surface, its
    small and large waves; ``shared``: the frame's buffers of the colour pass (the look's; without them its own)."""

    def __init__(self, ctx, stages: dict, water: SWater, maps: dict, volumes: dict, dummies, shared=None) -> None:
        self.m_ctx, self.m_water, self.m_dummies, self.m_maps = ctx, water, dummies, maps
        self.m_own_shared = shared is None  # begun by the ocean each frame, not by a look
        self.m_shared = CSharedBuffers(ctx) if shared is None else shared
        stages_, self.m_state = stages["water"]
        self.m_program, self.m_mesh = CProgram(*stages_), CGridMesh.made()
        self.m_small = waves1800.CSmallWaves1800(ctx, stages["gradients"], volumes)
        large = stages.get("large")
        self.m_large = oceanfft1800.CLargeWaves1800(ctx, large, water) if large else None
        self.m_rows = objects1800.CFrameRows(ctx)
        binding = objects1800.SBinding(self.m_shared, (PATCH_BUFFER,), self.m_rows, fallbacks=dict(FALLBACKS))
        self.m_patches = objects1800.CDraws(ctx, self.m_program, self.m_mesh, self.m_state, dummies, binding)
        self.m_draws: list[COceanDraw] = []
        self.m_textures: dict = dict(self.m_small.m_volumes)
        self.m_values: dict = {}
        self.m_size_tiles, self.m_session, self.m_made = 0.0, None, []

    def run(self, ctx, seconds: float) -> None:
        """The maps onto the device once, then the small waves' gradients and the large waves at ``seconds``."""
        if self.m_maps or self.m_session is not None:  # a batch no pass takes is dropped: handed on
            ctx.native_pass(lambda command_buffer, batch: (self._uploads(batch), command_buffer.resourceUpdate(batch)))
        self.m_textures[waves1800.MAP] = self.m_small.run(ctx, seconds)
        if self.m_large is not None and not self.m_large.m_error:
            maps = self.m_large.run(ctx, seconds)
            self.m_textures.update(zip(("cLargeWaveDisplacementMap", "cLargeWaveGradientMap"), maps, strict=True))
        self.m_values = surface_values(self.m_water, self.m_size_tiles or 1.0, seconds)

    def frame_values(self) -> dict:
        return frame_values(self.m_water)

    def models(self, eye, sun=None, origin=(0.0, 0.0)) -> list[COceanDraw]:
        """This frame's draws of the patches around ``eye`` (the view's axes, relative to the square's corner
        ``origin``: the viewer's sea lies under its model): 2205's quadtree over ``SPAN`` x the island's square around
        it, a draw a patch, their own values made and placed in the frame's rows at once (``patch_rows``)."""
        size = self.m_size_tiles or 1.0
        shift = (SPAN - 1) / 2 * size
        found = sea2205.chunks((eye[0] + shift, eye[1], eye[2] + shift), SPAN * size, NEAR_PATCH, SPLIT)
        masks = edge_masks(found).tolist()
        if self.m_own_shared:
            self.m_shared.begin()
        self.m_rows.begin()
        self.m_patches.begin()
        origin_x, origin_z = (float(component) for component in origin)
        eye = (float(eye[0]) + origin_x, float(eye[1]), float(eye[2]) + origin_z)
        cells = found[:, :3].astype(np.float64) - (shift - origin_x, shift - origin_z, 0.0)
        reflection = self.m_program.m_cbuffers.get(PATCH_BUFFER)
        slots = [None] * len(cells)
        if reflection is not None:
            raws = patch_rows(reflection, cells, eye)
            slots = [(PATCH_BUFFER, int(at)) for at in self.m_rows.place_many(PATCH_BUFFER, raws, raws.shape[1])]
        while len(self.m_draws) < len(cells):
            self.m_draws.append(COceanDraw(self))
        out = self.m_draws[: len(cells)]
        for draw, (x, z, side), slot, mask in zip(out, cells.tolist(), slots, masks, strict=True):
            draw.m_draw_call = self.m_patches.take()
            draw.m_draw_call.m_slot = slot
            draw.m_cell, draw.m_eye, draw.m_span = (x, z, side), eye, self.m_mesh.m_spans[mask]
            draw.m_world[:3, 3] = (x + side / 2, 0.0, z + side / 2)
            draw.m_box = (np.array([x, 0.0, z]), np.array([x + side, 0.0, z + side]))
        return out

    def release(self) -> None:
        self.m_patches.release()
        self.m_rows.release()
        if self.m_own_shared:
            self.m_shared.release()
        self.m_small.release()
        if self.m_large is not None:
            self.m_large.release()
        for texture in self.m_made:
            self.m_ctx.later(texture)
        self.m_draws, self.m_made, self.m_textures = [], [], {}
