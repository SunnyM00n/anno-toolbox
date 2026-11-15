"""Anno 2070's sea as its water manager draws it, for the viewer's 2070 look and a 2070 island's sea: each frame the
wave maps by ``watergrid.fx`` (two wave height textures into a displacement map, its normals and foam), then the ocean
by ``waterocean.fx`` on instanced square chunks of 16 units in rings of coarser grids, displaced by that map,
refracting the lit scene's copy, reflecting the sky cube; ``info_map`` the island's water info map as the engine
builds it.

``Ocean2070(ctx, stages, dummies, water)`` is the entry (``frame2070.ocean_pass`` draws it): ``waves`` records the
wave passes, ``draws`` gives the ocean's draws for ``chunk_rings``. docs/rendering/shader-compile.md section 10.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .fxcdraw import FxcDraw
from .gamedraw import Mesh, Program
from .nativepass import draw_pass
from .sea2205 import grid_mesh
from .sky2070 import CUBE_TECHNIQUE, SkyCube
from .sky2070 import FX as SKY_FX

F = QRhiTexture.Format
FX = "data/system/shaders/fx/waterocean.fx"
GRID_FX = "data/system/shaders/fx/watergrid.fx"
TECHNIQUE = "OceanSurfaceOverWaterStandardShadow"  # shadow method 0, as the frame's
# the water options of the owner's configuration a view can feed: no planar reflection pass and no coast (dynamic
# waves) in a model viewer [ours]
DEFINES = (("WATER_DISPLACEMENT_MAP_ENABLED", ""), ("WATER_CAUSTICS_ENABLED", ""))
STAGES = {"position": (GRID_FX, (), "WavePosition"), "normal": (GRID_FX, (), "WaveNormal"),
          "ocean": (FX, DEFINES, TECHNIQUE), "cube": (SKY_FX, (), CUBE_TECHNIQUE)}  # fmt: skip
# an island's sea has a coast and the view a mirrored scene: the owner's whole water configuration
ISLAND_DEFINES = (*DEFINES, ("WATER_DYNAMIC_WAVES_ENABLED", ""), ("WATER_REFLECTION_ENABLED", ""))
WATER = "data/graphics/effects/water/"
WAVES = {"cTexture0": WATER + "waves01.dds", "cTexture1": WATER + "waves02.dds"}  # great and medium waves (A8)
RIPPLES, CAUSTICS = WATER + "ripples.dds", WATER + "caustics.dds"
MAP_SIZE = 384  # the wave maps' side
TILE = 48.0  # world units one wave map spans (cWaterConfig.z = 1/48)
CHUNK = 16.0
RINGS = (48.0, 72.0, 96.0, 128.0, 10000.0)  # a chunk's level by its distance from the camera less 8
GRIDS = (64, 32, 16, 8, 4)  # quads a side of a chunk per level
MORPH = 10.0, 0.1  # the last units of a ring morph towards the next coarser grid, at this rate
PER_DRAW = 200  # cWaterChunkData's length
MIN_REACH = 128.0  # the sea's half-width round the model at least [ours]
DEEP = (0, 0, 0, 255)  # the water info map's stand-in: deep water everywhere (its depth factor in alpha) [ours]
# what the ocean reads without the island's maps: deep water, nothing mirrored (alpha 0), flat coast waves
FALLBACKS = {"cTexture3": DEEP, "cTexture4": (0, 0, 0, 0), "cTexture8": (0, 0, 0, 0), "cTexture9": (0, 0, 0, 0)}
SWELL = 4.0  # how far the waves reach up and down, for the sun's shadow depth over the sea [ours]


INFO_TEXEL = 2.0  # world units a texel of the water info map spans (8 a terrain chunk of 16)
INFO_CHUNK = 8  # info texels a terrain chunk side
INFO_REACH = 3  # the shallow spread's and the blur's half-window, texels (7 x 7)
INFO_LAND_CHUNK = -4.0  # a chunk whose highest point is above this gets the spread and the blur
INFO_DEPTH = (-0.125, 0.25)  # the first map: clamp(a - h * b, 1/255, 1)
INFO_LIFT = 0xA0  # the depth factor: 160 + its map * 95/255, as a byte
INFO_DEEP = -12.0  # from this depth down the coast distance is 1 without looking for land
INFO_COAST_REACH = 8  # units each way the coast distance looks for land
INFO_COAST_SCALE = 0.0883883  # 1 / (8 sqrt 2): the farthest land it can find is 1


@dataclass(frozen=True)
class Sea:
    """Where a frame draws the sea: round ``centre`` (x, z in the game's axes) at least ``reach`` units, with the
    engine's fixed maps (``WAVES``, ``RIPPLES``, ``CAUSTICS``) by path as loaded; ``info`` the water info map (a
    texture; None: the deep-water stand-in ``DEEP``) and ``texel`` its ``cWaterConfig.xy`` (0.5 / its texels a side,
    its corner at the origin); ``coast`` the coast waves (``coast2070.CoastWaves``) and ``reflection`` the planar
    reflection's map, for an ocean compiled with ``ISLAND_DEFINES``."""

    centre: tuple[float, float]
    reach: float
    maps: dict
    info: object = None
    texel: tuple[float, float] = (0.5, 0.5)
    coast: object = None
    reflection: object = None


def info_depth(heights: np.ndarray, step: float) -> np.ndarray:
    """The water info map's depth factor (its alpha, a byte a texel) as the water manager builds it once per world
    from the terrain: per ``INFO_TEXEL`` square the terrain's height at its corner into ``INFO_DEPTH``'s ramp (deep
    water 255, the coast 1); around every chunk of land (``INFO_LAND_CHUNK``) the 7 x 7 minimum, then the 7 x 7 mean of
    that; then 160 + 95/255 of it. ``heights``: [z, x] at ``step`` units from the origin, the game's axes."""
    n = int(math.ceil((heights.shape[0] - 1) * step / INFO_TEXEL))
    at = np.arange(n) * INFO_TEXEL / step
    h = sample_grid(heights, at)
    a, b = INFO_DEPTH
    first = (np.clip(a - h * b, 1 / 255, 1.0) * 255).astype(np.int32)
    spread = np.full_like(first, 255)
    land = _land_chunks(heights, step, n)
    for x0, x1, z0, z1 in land:
        spread[z0:z1, x0:x1] = _window(first, (x0, x1, z0, z1), np.nanmin)
    for x0, x1, z0, z1 in land:
        first[z0:z1, x0:x1] = _window(spread, (x0, x1, z0, z1), _mean)
    return ((INFO_LIFT + (first * 0.37254902).astype(np.int32)) & 255).astype(np.uint8)


def info_map(heights: np.ndarray, step: float) -> np.ndarray:
    """The water info map as the ocean and the coast waves read it: RGBA bytes [z, x], the land height, the coast
    distance and the steep coast of ``info_coast`` and the depth factor ``info_depth`` in alpha."""
    depth = info_depth(heights, step)
    out = np.zeros((*depth.shape, 4), np.uint8)
    out[..., :3] = info_coast(heights, step)[: depth.shape[0], : depth.shape[1]]
    out[..., 3] = depth
    return out


def info_coast(heights: np.ndarray, step: float) -> np.ndarray:
    """The water info map's red, green and blue (bytes [z, x, 3]) as the water manager builds them, each texel at its
    middle: red the land height clamped to 0..1; green the coast distance, 0 on land, 1 from 12 below the water, else
    the nearest land within 8 units each way / (8 sqrt 2) (1 with none), past 0.4 stretched by the engine's noise;
    blue the steep coast, 255 unless the coast is farther than 0.2 and the water there shallower than 8 times it
    (the ground textures' ``ShallowCoast`` flag would clear it near land: no 2070 texture sets it). Rivers are not
    read (the engine clears blue and green near one)."""
    n = int(math.ceil((heights.shape[0] - 1) * step / INFO_TEXEL))
    mid = (np.arange(n) * INFO_TEXEL + INFO_TEXEL / 2) / step
    h = sample_grid(heights, mid)
    units = int(round((heights.shape[0] - 1) * step))
    land = sample_grid(heights, np.arange(units + 1) / step) >= 0.0
    reach = INFO_COAST_REACH
    pad = np.pad(land, reach, constant_values=False)
    centre = (np.arange(n) * 2 + 1 + reach).clip(0, units + 2 * reach)
    near = np.ones((n, n))
    for i in range(-reach, reach + 1):
        for j in range(-reach, reach + 1):
            found = pad[np.ix_(centre + i, centre + j)]
            near = np.where(found, np.minimum(near, math.hypot(i, j) * INFO_COAST_SCALE), near)
    shore = (h > INFO_DEEP) & (h < 0.0)
    g = np.where(h >= 0.0, 0.0, np.where(shore, near, 1.0))
    steep = np.where(shore & (g > 0.2) & (h / np.maximum(g, 1e-6) >= -8.0), 0, 255)
    x, z = np.meshgrid(np.arange(n), np.arange(n))
    noise = ((np.sin(1.6 * x) + np.cos(1.6 * z)) * 0.38 + 0.76) ** 3
    g = np.where((h < 0.0) & (g > 0.4), np.minimum(g + (g - 0.4) * (5 / 3) * noise, 1.0), g)
    red = np.clip(h, 0.0, 1.0)
    return np.stack([red * 255, g * 255, steep], -1).astype(np.uint8)


def sample_grid(heights: np.ndarray, at: np.ndarray) -> np.ndarray:
    """``heights`` at the sample positions ``at`` along both axes (bilinear [inferred]: the engine reads its terrain's
    triangles), clamped to the grid."""
    last = heights.shape[0] - 1
    at = np.clip(at, 0, last)
    i = np.minimum(at.astype(np.int64), last - 1 if last > 0 else 0)
    f = at - i
    j = np.minimum(i + 1, last)
    rows = heights[i] * (1 - f)[:, None] + heights[j] * f[:, None]
    return rows[:, i] * (1 - f)[None, :] + rows[:, j] * f[None, :]


def _land_chunks(heights: np.ndarray, step: float, n: int) -> list[tuple[int, int, int, int]]:
    """Per terrain chunk whose highest point is above ``INFO_LAND_CHUNK``: its texels widened by ``INFO_REACH``
    (x0, x1, z0, z1), clipped to the map."""
    per = int(round(INFO_CHUNK * INFO_TEXEL / step))
    out = []
    for cz in range(0, heights.shape[0] - 1, per):
        for cx in range(0, heights.shape[1] - 1, per):
            if float(heights[cz : cz + per + 1, cx : cx + per + 1].max()) > INFO_LAND_CHUNK:
                tx, tz, r = cx // per * INFO_CHUNK, cz // per * INFO_CHUNK, INFO_REACH
                out.append((max(tx - r, 0), min(tx + INFO_CHUNK + r, n), max(tz - r, 0), min(tz + INFO_CHUNK + r, n)))
    return out


def _window(src: np.ndarray, box: tuple[int, int, int, int], op) -> np.ndarray:
    """``op(window, axis)`` over the 7 x 7 window of each texel of ``box`` in ``src`` (the texels off the map left
    out)."""
    x0, x1, z0, z1 = box
    r = INFO_REACH
    pad = np.pad(src.astype(np.float64), r, constant_values=np.nan)
    rows = np.stack([pad[z0 + dz : z1 + dz, x0 : x1 + 2 * r] for dz in range(2 * r + 1)], -1)
    win = np.stack([rows[:, dx : dx + (x1 - x0)] for dx in range(2 * r + 1)], -1).reshape(z1 - z0, x1 - x0, -1)
    return op(win, -1).astype(np.int32)


def _mean(win: np.ndarray, axis: int) -> np.ndarray:
    """The integer mean of the texels on the map, as the engine divides its sum by their count."""
    return np.nansum(win, axis) // np.maximum(np.sum(~np.isnan(win), axis), 1)


def wave_values(water, seconds: float) -> dict:
    """The wave constants after ``seconds`` (``water``: the setting's ``Water``): frequency, amplitude, choppiness
    (and half the great amplitude) and the two scrolls, each advanced by velocity / frequency a second and kept in 0..1
    (the second sample's v offset -0.5 from the start)."""
    (fx, fy), (ax, ay) = water.wave_frequency[:2], water.wave_amplitude[:2]
    (v0x, v0y), (v1x, v1y) = (v[:2] for v in water.wave_velocity)
    t = float(seconds)
    return {"cWaterFrequency": [fx, fy, 0.0, 0.0], "cWaterAmplitude": [ax, ay, 0.0, 0.0],
            "cWaterChoppiness": [water.choppiness, ax * 0.5, 0.0, 0.0],
            "cWaterVelocity0": [(t * v0x / fx) % 1.0, 0.0, (-t * v1x / fx) % 1.0, -0.5],
            "cWaterVelocity1": [(t * v0y / fy) % 1.0, 0.0, (-t * v1y / fy) % 1.0, -0.5]}  # fmt: skip


def ocean_values(water, seconds: float, toward_sun, view_dir, texel=(0.5, 0.5)) -> dict:
    """The ocean's own constants: the config (the info map's ``texel``, ``Sea.texel``, and 1/``TILE``), the ripples'
    and caustics' scroll by the game's milliseconds, the setting's above-water light, the glint's sun and the fake
    double refraction by the camera's tilt (``view_dir``: the camera's forward direction) [inferred]."""
    ms = float(seconds) * 1000.0
    tilt = 0.75 * (1.0 - abs(float(view_dir[1]))) + 0.2
    return {"cWaterConfig": [*texel, 1.0 / TILE, 0.0],
            "cWaterRipplesOffset": [(2e-5 * ms) % 1.0, 0.0, (0.5 - 2e-5 * ms) % 1.0, 0.4],
            "cWaterCausticsOffset": [(5e-5 * ms) % 1.0, 0.0, (4e-5 * ms + 0.5) % 1.0, 0.4],
            "cWaterLightAbsorptionExp": [*water.above_absorption[:3], 0.0],
            "cWaterLightDepth": [water.above_light_depth, 0.0, 0.0, 0.0],
            "cWaterViewDepth": [water.above_view_depth, water.above_view_exponent, 0.0, 0.0],
            "cWaterScattering": [*water.above_scattering[:3], 0.0],
            "cWaterLightDirection": [*toward_sun[:3], 0.0], "cUserConstant1": [tilt, 0.0, 0.0, 0.0]}  # fmt: skip


def corners(sea: Sea, height: float = SWELL) -> np.ndarray:
    """The eight corners of the box the sea's chunks and waves fill (``chunk_rings``' square, ``height`` up and down)."""
    half = max(float(sea.reach), MIN_REACH)
    (x, z), h = sea.centre, height
    return np.array([[x + a * half, b * h, z + c * half] for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)], float)


def chunk_rings(centre, reach: float, eye) -> dict[int, list[tuple[float, float]]]:
    """The chunks' corners per level over a square of ``max(reach, MIN_REACH)`` round ``centre`` (x, z), each chunk's
    level by its centre's distance from ``eye`` less 8 against ``RINGS`` (beyond the last: none)."""
    half = max(float(reach), MIN_REACH)
    eye = np.asarray(eye, float)
    lo = [math.floor((float(c) - half) / CHUNK) for c in centre]
    hi = [math.ceil((float(c) + half) / CHUNK) for c in centre]
    out: dict[int, list] = {}
    for i in range(lo[0], hi[0]):
        for k in range(lo[1], hi[1]):
            x, z = i * CHUNK, k * CHUNK
            d = float(np.linalg.norm(eye - (x + CHUNK / 2, 0.0, z + CHUNK / 2))) - CHUNK / 2
            level = next((n for n, ring in enumerate(RINGS) if d < ring), None)
            if level is not None:
                out.setdefault(level, []).append((x, z))
    return out


def level_values(level: int) -> dict:
    """``cWaterChunkConfig0`` and ``1`` of a level: 16 units a chunk, one row a chunk; the morph towards the next
    coarser grid (the last level's own) over the ring's last ``MORPH`` units."""
    r = GRIDS[level] // 2 if level < len(GRIDS) - 1 else GRIDS[level]
    start, rate = MORPH
    return {"cWaterChunkConfig0": [0.0, 1.0, CHUNK, 0.0],
            "cWaterChunkConfig1": [r / CHUNK, CHUNK / r, RINGS[level] - start, rate]}  # fmt: skip


def screen_p2() -> Mesh:
    """A triangle over the screen with float2 positions (the wave passes' ``p2``)."""
    v = np.array([[-1, -1], [3, -1], [-1, 3]], "<f4")
    return Mesh(v.tobytes(), 8, ((0, 7, 2, 0),), np.array([0, 1, 2], np.uint32), 3)


class Ocean2070:
    """The wave passes, their two maps and the ocean's draws (module docstring); the draws per level and batch are
    kept and filled anew each frame."""

    def __init__(self, ctx, stages: dict, dummies, water) -> None:
        self.ctx, self.dummies, self.water = ctx, dummies, water
        tri = screen_p2()
        flat = {"cull": "none", "depth_test": False, "depth_write": False, "blend": False}
        self.position, self.normal = (
            FxcDraw(ctx, Program(s.vs, s.ps), tri, replace(s.state, **flat), dummies)
            for s in (stages["position"], stages["normal"])
        )
        self.stages = stages["ocean"]
        self.grids = [grid_mesh(q) for q in GRIDS]  # 2205's chunk grid: the same p2 over 0..1, drawn unculled
        self.draw_calls: dict[tuple[int, int], FxcDraw] = {}
        self.maps: list = []  # the position and normal maps and their framebuffers, made on first use
        self.cube = SkyCube(ctx, stages["cube"], dummies)  # the sky it reflects (set_volume: its clouds)

    def _targets(self) -> None:
        if self.maps:
            return
        size = (MAP_SIZE, MAP_SIZE)
        position = self.ctx.native_texture(size, F.RGBA16F)
        normal = self.ctx.native_texture(size, F.RGBA8)
        self.maps = [position, self.ctx.framebuffer([position]), normal, self.ctx.framebuffer([normal])]

    def waves(self, seconds: float, textures: dict) -> dict:
        """The two wave passes after ``seconds`` (``textures``: ``WAVES`` loaded); the maps by the ocean's names."""
        self._targets()
        position, position_fbo, normal, normal_fbo = self.maps
        values = wave_values(self.water, seconds)

        def fill(c: dict) -> None:
            gs.set_values(c.values(), values)

        size = (MAP_SIZE, MAP_SIZE)
        for draw, fbo, maps in ((self.position, position_fbo, textures),
                                (self.normal, normal_fbo, {"cTexture2": position.native})):  # fmt: skip
            draw_pass(self.ctx, fbo, lambda rp, sig, batch, d=draw, m=maps: d.prepare(rp, sig, batch, m, fill),
                      lambda cb, d=draw: d.draw(cb, size))  # fmt: skip
        return {"cTexture0": position.native, "cTexture1": normal.native}

    def sky_cube(self, fill):
        """The sky the sea reflects, rendered for this frame (``sky2070.SkyCube``; ``fill``: the sky's values)."""
        return self.cube.render(fill)

    def draws(self, rings: dict) -> list[tuple[FxcDraw, dict]]:
        """The ocean's draws for ``chunk_rings``: per level batches of at most ``PER_DRAW`` chunks, each draw with its
        instance count set and the constants it adds (the level's config and the chunks' corners)."""
        out = []
        for level, corners in sorted(rings.items()):
            for k in range(0, len(corners), PER_DRAW):
                batch = corners[k : k + PER_DRAW]
                key = (level, k // PER_DRAW)
                if key not in self.draw_calls:
                    s = self.stages
                    d = FxcDraw(self.ctx, Program(s.vs, s.ps), self.grids[level], s.state, self.dummies)
                    d.fallbacks = dict(FALLBACKS)
                    self.draw_calls[key] = d
                draw = self.draw_calls[key]
                draw.instances = len(batch)
                rows = [[x, z, 0.0, 0.0] for x, z in batch] + [[0.0] * 4] * (PER_DRAW - len(batch))
                out.append((draw, {**level_values(level), "cWaterChunkData": rows}))
        return out

    def release(self) -> None:
        for d in (self.position, self.normal, *self.draw_calls.values()):
            d.release()
        self.draw_calls = {}
        for t in (self.maps[3], self.maps[2], self.maps[1], self.maps[0]) if self.maps else ():
            t.release()
        self.maps = []
        self.cube.release()
