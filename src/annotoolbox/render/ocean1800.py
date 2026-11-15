"""Anno 1800's ocean by its own shaders: the surface (``water_ocean.fx`` ``WaterFFT_TriList``: patches of a grid
around the eye, displaced by the engine's small-wave volumes and the large waves of the setting's spectrum, lit,
refracting the frame's copy of the scene) with its small-wave gradient map (``waves1800``) and its large waves' FFT
(``oceanfft1800``); the patches' own values in one buffer a frame (``objects1800.FrameRows``).

``compile_stages(source)``, ``read_water(text)`` and ``read_maps(files)`` run in the background; ``Ocean1800(ctx,
stages, water, maps, volumes, dummies)`` is the device side: ``set_session(infos, flow, size)``, then per frame
``run(ctx, seconds)`` and ``models(eye, sun)``. docs/rendering/island-1800-2205-view.md 16, 17.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models.shaderforward import NoPass
from . import frameforward, gamedraw, objects1800, oceanfft1800, sea2205, waves1800
from . import gameshader as gs
from .fxcdraw import SharedBuffers, layout_key
from .gamedraw import Program

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
# cbOceanFFT as the engine uploads it from its ocean simulator's start values (read in the executable)
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
class Water:
    """A setting's ``Water`` block: the large waves' spectrum (``oceanfft1800.init_values``) and the colours."""

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


def read_water(text: str) -> Water:
    """The ``Water`` block of a local ambient setting (the record's defaults where it has none: ``WaterBlock``)."""
    w, d = sea2205.WaterBlock(text), Water()
    num = w.num
    return Water(num("WaveAmplitude", d.amplitude), num("WaveChoppiness", d.choppiness), num("WaveSize", d.size),
                 num("WindDamping", d.damping), int(num("WaveSeed", d.seed)), w.vec("WaterColorDeep", "xyz", d.deep),
                 w.vec("WaterColorShallow", "xyz", d.shallow), num("ViewDepth", d.view_depth),
                 num("LightDepth", d.light_depth), num("Absorption", d.absorption),
                 num("CausticsIntensity", d.caustics))  # fmt: skip


def frame_values(water: Water) -> dict:
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
    except (NoPass, RuntimeError):
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
            "cPerlinMovement": [0.0, 0.0], "cLocalEye": [float(v) for v in eye]}  # fmt: skip


def stitched_indices(n: int, mask: int) -> np.ndarray:
    """``sea2205.grid_mesh``'s triangles with the odd vertices of the edges in ``mask`` (bits: x 0, x 1, z 0, z 1)
    folded onto their even neighbours, the triangles that fold flat left out: that edge then meets a patch of
    twice the side vertex for vertex, with no crack where the displaced heights differ (island-1800-2205-view.md 19)
    [ours: the engine's patch mesh is not read]."""
    remap, w = np.arange((n + 1) ** 2), n + 1
    odd = np.arange(1, n, 2)
    edges = (odd * w, odd * w + n, odd, n * w + odd)  # the odd vertices of each edge (row z, column x)
    steps = (w, w, 1, 1)  # back to the even one before it
    for bit, (vertices, step) in enumerate(zip(edges, steps, strict=True)):
        if mask >> bit & 1:
            remap[vertices] = vertices - step
    tri = remap[sea2205.grid_mesh(n).indices].reshape(-1, 3)
    keep = (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])
    return tri[keep].ravel().astype(np.uint32)


class GridMesh(gamedraw.Mesh):
    """The patches' grid with its sixteen stitched variants in one index buffer (``stitched_indices``, by mask);
    ``span`` (first index, count) is the variant the next draw takes (set by ``OceanDraw.draw``)."""

    spans: tuple = ()
    span: tuple | None = None

    @classmethod
    def made(cls, n: int = sea2205.GRID) -> GridMesh:
        plain = sea2205.grid_mesh(n)
        parts = [stitched_indices(n, mask) for mask in range(16)]
        firsts = np.cumsum([0, *(len(p) for p in parts[:-1])])
        mesh = cls(plain.data, plain.stride, plain.elements, np.concatenate(parts), plain.count)
        mesh.spans = tuple((int(f), len(p)) for f, p in zip(firsts, parts, strict=True))
        return mesh

    def draw(self, cb, *args, **kwargs) -> None:
        super().draw(cb if self.span is None else _Ranged(cb, *self.span), *args, **kwargs)


class _Ranged:
    """A command buffer whose indexed draw takes a range of the index buffer (``GridMesh.span``)."""

    def __init__(self, cb, first: int, count: int) -> None:
        self.cb, self.first, self.count = cb, first, count

    def __getattr__(self, name: str):
        return getattr(self.cb, name)

    def drawIndexed(self, _count: int, instances: int = 1) -> None:
        self.cb.drawIndexed(self.count, instances, self.first)


def edge_masks(cells: np.ndarray) -> np.ndarray:
    """Per patch (x, z, side rows of ``sea2205.chunks``) the mask of its edges a patch of twice its side lies
    beyond (``stitched_indices``' bits), for every patch at once; the split rule keeps neighbours within twice of
    each other."""
    x, z, s = (cells[:, k].astype(np.float64) for k in range(3))
    sides = np.unique(np.round(s, 3))

    def code(side, column, row) -> np.ndarray:  # one integer per (side, column, row); -1 for a side no patch has
        side = np.round(side, 3)
        k = np.minimum(np.searchsorted(sides, side), len(sides) - 1)
        known = sides[k] == side
        return np.where(known, (k * 2**21 + column.astype(np.int64) + 1) * 2**21 + row.astype(np.int64) + 1, -1)

    keys = np.unique(code(s, np.round(x / s), np.round(z / s)))
    big, eps = 2 * s, s * 1e-3
    probes = ((x - eps, z + s / 2), (x + s + eps, z + s / 2), (x + s / 2, z - eps), (x + s / 2, z + s + eps))
    out = np.zeros(len(cells), np.int32)
    for bit, (px, pz) in enumerate(probes):
        found = code(big, np.floor(px / big), np.floor(pz / big))
        out |= np.where((found >= 0) & np.isin(found, keys), 1 << bit, 0).astype(np.int32)
    return out


def surface_values(water: Water, size: float, seconds: float) -> dict:
    """``cbOceanFFT`` and ``cbWater`` but the patch: the engine's ocean values, the wave time, the setting's colours,
    the island's maps over its square, the caustics' drift [E]."""
    drift = seconds * sea2205.DRIFT
    return {**OCEAN, "cWaveTime": waves1800.wave_time(seconds), "cChoppyScale": water.choppiness,
            "cWaterColorDeep": list(water.deep), "cWaterColorShallow": list(water.shallow),
            "cWaterInfoCoord": [1 / size, 1 / size],
            "cWaterCausticsOffset": [drift, drift * 0.7, -drift * 0.8, drift * 0.6]}  # fmt: skip


_PATCH_WORDS: dict[tuple, tuple] = {}  # a layout -> where x, z and the side go (``patch_rows``)


def _patch_raw(reflection, x: float, z: float, side: float, eye) -> np.ndarray:
    c = gs.Constants(reflection)
    gs.set_values([c], patch_values(x, z, side, eye))
    return np.frombuffer(bytes(c.data), np.uint8)


def patch_rows(reflection, cells: np.ndarray, eye) -> np.ndarray:
    """Every patch's ``cbOceanFFTPerCall`` (``patch_values``) of ``cells`` (x, z, side rows) at once as bytes of
    ``reflection``'s layout ([n, size] uint8): the values all patches share written once, the three that differ
    put where the layout holds them (found once per layout)."""
    base = _patch_raw(reflection, 0.0, 0.0, 0.0, eye)
    key = layout_key(reflection)
    if key not in _PATCH_WORDS:
        plain = base.view(np.float32)
        _PATCH_WORDS[key] = tuple(np.flatnonzero(_patch_raw(reflection, *probe, eye).view(np.float32) != plain)
                                  for probe in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))  # fmt: skip
    out = np.repeat(base[None], len(cells), axis=0)
    words = out.view(np.float32)
    for column, at in enumerate(_PATCH_WORDS[key]):
        words[:, at] = np.asarray(cells, np.float32)[:, column : column + 1]
    return out


class OceanDraw:
    """One patch of the ocean as a model of the forward frame's blended pass (it reads the scene's copies:
    ``water``): a draw of the ocean's ``objects1800.Draws``, its own values its slot of the frame's rows, the ocean's
    values in the frame's buffers (written by the frame's first patch)."""

    blended, water, shadow, prepass, ssr = True, True, None, None, None

    def __init__(self, ocean: Ocean1800) -> None:
        self.ocean, self.later = ocean, []
        self.draw_call: objects1800.ObjectDraw | None = None
        self.cell, self.eye = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)  # its patch (x, z, side) and the eye, world axes
        self.world = np.identity(4)
        self.box = (np.zeros(3), np.zeros(3))
        self.span: tuple | None = None  # its grid's stitched variant (``GridMesh.spans``)

    @property
    def patch(self) -> dict:
        """Its ``cbOceanFFTPerCall`` values (``patch_values``; the frame writes them for all patches at once)."""
        return patch_values(*self.cell, self.eye)

    def prepare(self, rp, signature, batch, cam: gs.Camera, light: dict, extra: dict | None = None) -> None:
        values = self.ocean.values

        def fill(c: dict) -> None:
            rest = {n: v for n, v in c.items() if n not in OCEAN_BUFFERS}
            if rest:
                frameforward.fill_frame(rest, cam, light)
            gs.set_values([v for n, v in c.items() if n in OCEAN_BUFFERS], values)

        self.draw_call.prepare(rp, signature, batch, {**self.ocean.textures, **(extra or {})}, fill)

    def draw(self, cb, size) -> None:
        self.ocean.mesh.span = self.span
        self.draw_call.draw(cb, size)
        self.ocean.mesh.span = None

    def corners(self) -> np.ndarray:
        lo, hi = self.box
        return np.array([[x, 0.0, z] for x in (lo[0], hi[0]) for z in (lo[2], hi[2])])

    def release(self) -> None:
        """Nothing: its draw is the ocean's."""


class Ocean1800(sea2205.Sea2205):
    """The ocean on the device (module docstring): 2205's sea's maps, session and draws with 1800's surface, its
    small and large waves; ``shared``: the frame's buffers of the colour pass (the look's; without them its own)."""

    def __init__(self, ctx, stages: dict, water: Water, maps: dict, volumes: dict, dummies, shared=None) -> None:
        self.ctx, self.water, self.dummies, self.maps = ctx, water, dummies, maps
        self.own_shared = shared is None  # begun by the ocean each frame, not by a look
        self.shared = SharedBuffers(ctx) if shared is None else shared
        stages_, self.state = stages["water"]
        self.program, self.mesh = Program(*stages_), GridMesh.made()
        self.small = waves1800.SmallWaves1800(ctx, stages["gradients"], volumes)
        large = stages.get("large")
        self.large = oceanfft1800.LargeWaves1800(ctx, large, water) if large else None
        self.rows = objects1800.FrameRows(ctx)
        binding = objects1800.Binding(self.shared, (PATCH_BUFFER,), self.rows, fallbacks=dict(FALLBACKS))
        self.patches = objects1800.Draws(ctx, self.program, self.mesh, self.state, dummies, binding)
        self.draws: list[OceanDraw] = []
        self.textures: dict = dict(self.small.volumes)
        self.values: dict = {}
        self.size, self.session, self.made = 0.0, None, []

    def run(self, ctx, seconds: float) -> None:
        """The maps onto the device once, then the small waves' gradients and the large waves at ``seconds``."""
        if self.maps or self.session is not None:  # a batch no pass takes is dropped: handed on
            ctx.native_pass(lambda cb, batch: (self._uploads(batch), cb.resourceUpdate(batch)))
        self.textures[waves1800.MAP] = self.small.run(ctx, seconds)
        if self.large is not None and not self.large.error:
            maps = self.large.run(ctx, seconds)
            self.textures.update(zip(("cLargeWaveDisplacementMap", "cLargeWaveGradientMap"), maps, strict=True))
        self.values = surface_values(self.water, self.size or 1.0, seconds)

    def frame_values(self) -> dict:
        return frame_values(self.water)

    def models(self, eye, sun=None, origin=(0.0, 0.0)) -> list[OceanDraw]:
        """This frame's draws of the patches around ``eye`` (the view's axes, relative to the square's corner
        ``origin``: the viewer's sea lies under its model): 2205's quadtree over ``SPAN`` x the island's square around
        it, a draw a patch, their own values made and placed in the frame's rows at once (``patch_rows``)."""
        size = self.size or 1.0
        shift = (SPAN - 1) / 2 * size
        found = sea2205.chunks((eye[0] + shift, eye[1], eye[2] + shift), SPAN * size, NEAR_PATCH, SPLIT)
        masks = edge_masks(found).tolist()
        if self.own_shared:
            self.shared.begin()
        self.rows.begin()
        self.patches.begin()
        ox, oz = (float(v) for v in origin)
        eye = (float(eye[0]) + ox, float(eye[1]), float(eye[2]) + oz)
        cells = found[:, :3].astype(np.float64) - (shift - ox, shift - oz, 0.0)
        reflection = self.program.cbuffers.get(PATCH_BUFFER)
        slots = [None] * len(cells)
        if reflection is not None:
            raws = patch_rows(reflection, cells, eye)
            slots = [(PATCH_BUFFER, int(at)) for at in self.rows.place_many(PATCH_BUFFER, raws, raws.shape[1])]
        while len(self.draws) < len(cells):
            self.draws.append(OceanDraw(self))
        out = self.draws[: len(cells)]
        for d, (x, z, side), slot, mask in zip(out, cells.tolist(), slots, masks, strict=True):
            d.draw_call = self.patches.take()
            d.draw_call.slot = slot
            d.cell, d.eye, d.span = (x, z, side), eye, self.mesh.spans[mask]
            d.world[:3, 3] = (x + side / 2, 0.0, z + side / 2)
            d.box = (np.array([x, 0.0, z]), np.array([x + side, 0.0, z + side]))
        return out

    def release(self) -> None:
        self.patches.release()
        self.rows.release()
        if self.own_shared:
            self.shared.release()
        self.small.release()
        if self.large is not None:
            self.large.release()
        for tex in self.made:
            self.ctx.later(tex)
        self.draws, self.made, self.textures = [], [], {}
