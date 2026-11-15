"""Anno 1404's sea as its projected grid manager draws it: each frame ``waterprojgrid.fx`` writes, into a float map
of the grid's 230 x 280 points, the world position of every point over the trapezoid the camera sees of the sea,
damped by the terrain-wide water depth map, and its foam; ``waterdisp.fx`` then draws the grid from that map,
refracting the scene below with caustics, reflecting the mirrored scene and the sky cube.

``Ocean1404(ctx, stages, dummies)`` is the draw (``draw``); ``depth_map`` builds the water depth map from the heights
as the engine does once per world, ``read_waves`` a set's ``WaterDescription``, ``grid_values`` / ``water_values``
the constants by name. docs/rendering/shader-compile.md section 12, "The sea".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .frame1404 import (
    NUMBER,
    Sun1404,
    behind_values,
    caustic_offsets,
    light_values,
    number_in,
    profile_block,
    scattering_values,
)
from .fxcdraw import NO_DEPTH, FxcDraw
from .gamedraw import Mesh, Program
from .nativepass import draw_pass
from .ocean1701 import Bounds, GridSurfaces, screen_triangle, trapezoid
from .ocean2070 import sample_grid

GRID_FX = "data/system/shaders/fx/waterprojgrid.fx"
WATER_FX = "data/system/shaders/fx/waterdisp.fx"
# the water manager's highest setting: the scene behind the sea from the frame's copy with its caustics, the mirrored
# scene; the force manager's dynamic waves (ships, storms) are left out: an island view has none [ours]
DEFINES = (("WATER_REFLECTION_ENABLED", "1"), ("WATER_BACKBUFFER_REFRACTION", "1"), ("WATER_CAUSTICS_ENABLED", "1"))
# the surface without and with the sun's shadow (StandardShadow: shadow method 0, one map)
STAGES = {"grid": (GRID_FX, (), "Object"), "water": (WATER_FX, DEFINES, "WithoutShadow"),
          "shadowed": (WATER_FX, DEFINES, "StandardShadow")}  # fmt: skip
WAVES = "data/graphics/effects/water/waves_height_0.dds"  # the engine's waves_height.png at the highest quality
RIPPLES = "data/graphics/effects/water/waves_norm_0.dds"  # waves_norm.png: the surface's ripples and foam detail
CAUSTICS = "data/graphics/effects/water/caustics_0.dds"
GRID = (230, 280)  # the grid's points across and away from the camera (the water manager makes the grid so)
TRAPEZOID = Bounds(left=-1.14, right=1.14, near_row=-1.28, far_row=1.14, near_depth=-0.15, near_height=0.1)
# the medium waves fade with the distance from the eye to the trapezoid's near and far edge: 1 - (d - 18 / k) /
# (36 / k), at least 0.2; k a value of the camera's not identified, 1 taken [ours]
FADE_START, FADE_RANGE, FADE_FLOOR, FADE_CAMERA = 18.0, 36.0, 0.2, 1.0
SPECULAR = (2.5, 2.0, 1.0)  # cWaterSpecular: (2.5, 2, 1) times 1 less a weather value, 0 (clear) taken [ours]
LAND_ABOVE = 0.1  # the depth map's mask: a texel and its eight neighbours two texels off all above this height
INV255 = np.float32(0.003921568859368563)  # the engine's 1/255 (0x3b808081)
NO_FOAM = (0, 0, 0, 0)  # the dynamic wave map's stand-in (its green: foam)
FLAT = (128, 128, 128, 128)  # the rain ripples' stand-in: no rain
# the depth map's stand-in without ground (a model viewer's sea): deep everywhere, nowhere land [ours]
OPEN_SEA = (255, 255, 255, 0)


@dataclass(frozen=True)
class Waves1404:
    """A set's ``WaterDescription``: the great and medium waves' length, height and the two speeds, the choppiness,
    the depth the waves calm over (negative: below the water). The defaults are the manager's own."""

    length: tuple[float, float] = (0.01, 0.11)
    height: tuple[float, float] = (2.0, 0.25)
    speed0: tuple[float, float] = (1e-4, 0.0045)
    speed1: tuple[float, float] = (1.2e-4, 0.006)
    choppiness: float = 2.1
    depth: float = -3.5


def read_waves(text: str, name: str) -> Waves1404:
    """The ``WaterDescription`` of set ``name`` in ``ambientmap.xml`` (a value it lacks keeps the default)."""
    body, base = profile_block(profile_block(text, name), "WaterDescription"), Waves1404()

    def pair(tag: str, fallback: tuple) -> tuple:
        found = re.search(rf"<{tag}>\s*<x>{NUMBER}</x>\s*<y>{NUMBER}</y>", body)
        return (float(found.group(1)), float(found.group(2))) if found else fallback

    return Waves1404(pair("WaveLength", base.length), pair("WaveHeight", base.height),
                     pair("WaveSpeed0", base.speed0), pair("WaveSpeed1", base.speed1),
                     number_in(body, "Choppiness", base.choppiness), number_in(body, "MaxWaterDepth", base.depth))  # fmt: skip


def _ring(r: int) -> list[tuple[int, int]]:
    """The (dx, dz) of the square ring ``r`` round a texel in the order the engine walks it."""
    out = [(dx, r) for dx in range(-r, r + 1)] + [(r, dz) for dz in range(r - 1, -r - 1, -1)]
    return out + [(dx, -r) for dx in range(r - 1, -r, -1)] + [(-r, dz) for dz in range(-r, r)]


def _shifted(src: np.ndarray, dx: int, dz: int) -> tuple[np.ndarray, np.ndarray]:
    """``src`` read ``(dx, dz)`` texels on, and where that lies on the map."""
    h, w = src.shape
    out, inside = np.zeros_like(src), np.zeros(src.shape, bool)
    if abs(dx) >= w or abs(dz) >= h:  # off the map everywhere
        return out, inside
    zs, xs = slice(max(0, -dz), min(h, h - dz)), slice(max(0, -dx), min(w, w - dx))
    out[zs, xs] = src[zs.start + dz : zs.stop + dz, xs.start + dx : xs.stop + dx]
    inside[zs, xs] = True
    return out, inside


def ring_min(src: np.ndarray, reach: int) -> np.ndarray:
    """The engine's first blur: per texel the least of the rings 1 to ``reach`` round it (the texel itself left
    out, the texels off the map too) as bytes, 1 where none lies on the map; truncated to a byte."""
    low = np.ones(src.shape, np.float32)
    for r in range(1, reach + 1):
        for dx, dz in _ring(r):
            v, inside = _shifted(src, dx, dz)
            low = np.where(inside, np.minimum(low, v.astype(np.float32) * INV255), low)
    return (low * np.float32(255)).astype(np.uint8)


def ring_mean(src: np.ndarray, reach: int) -> np.ndarray:
    """The engine's second blur: the mean of the rings 1 to ``reach`` (the texel left out) over the texels on the
    map, summed in its order, truncated to a byte."""
    total, count = np.zeros(src.shape, np.float32), np.zeros(src.shape, np.int32)
    for r in range(1, reach + 1):
        for dx, dz in _ring(r):
            v, inside = _shifted(src, dx, dz)
            total = np.where(inside, total + v.astype(np.float32) * INV255, total)
            count += inside
    return (total / np.maximum(count, 1).astype(np.float32) * np.float32(255)).astype(np.uint8)


def depth_map(heights: np.ndarray, step: float, depth: float = Waves1404.depth) -> np.ndarray:
    """The water depth map as the projected grid manager builds it once per world (shader-compile.md 12, "The sea"):
    a texel per unit, its height ``h`` at the texel's corner (``heights``: [z, x] at ``step`` units, bilinear [ours]);
    red the depth ``(h + 0.5) / depth`` clamped to 1/255..1 as a byte, blurred by the 9 x 9 minimum and the 11 x 11
    mean; green that depth + 0.5 (at most 1); blue twice it, clamped; alpha 255 where the texel and its eight
    neighbours two texels off lie above ``LAND_ABOVE`` (the water clipped there). RGBA bytes [z, x]."""
    n = max(1, int(round((heights.shape[0] - 1) * step)))
    h = sample_grid(np.asarray(heights, np.float64), np.arange(n) / step).astype(np.float32)
    lift = h + np.float32(0.5)
    one = np.clip(lift * np.float32(1.0 / depth), INV255, np.float32(1.0))
    two = np.clip(lift * np.float32(2.0 / depth), INV255, np.float32(1.0))
    land = np.zeros((n, n), bool)
    if n > 4:
        inner = np.ones((n - 4, n - 4), bool)
        for dz in (-2, 0, 2):
            for dx in (-2, 0, 2):
                inner &= h[2 + dz : n - 2 + dz, 2 + dx : n - 2 + dx] > LAND_ABOVE
        land[2:-2, 2:-2] = inner
    out = np.zeros((n, n, 4), np.uint8)
    out[..., 0] = ring_mean(ring_min((one * np.float32(255)).astype(np.uint8), 4), 5)
    out[..., 1] = (np.minimum(one + np.float32(0.5), np.float32(1.0)) * np.float32(255)).astype(np.uint8)
    out[..., 2] = (two * np.float32(255)).astype(np.uint8)
    out[..., 3] = np.where(land, 255, 0)
    return out


def grid_mesh(grid: tuple[int, int] = GRID) -> Mesh:
    """The grid's points (i / (w - 1), j / (h - 1)) and the engine's triangles (k, k + w, k + 1), (k + 1, k + w,
    k + w + 1); the surface reads the position map there, between its texels (linear, as the engine's Direct3D 10
    path does)."""
    w, h = grid
    u, v = np.meshgrid(np.arange(w) / (w - 1), np.arange(h) / (h - 1))
    data = np.stack([u.ravel(), v.ravel()], 1).astype("<f4")
    k = (np.arange(h - 1)[:, None] * w + np.arange(w - 1)[None, :]).ravel()
    index = np.stack([k, k + w, k + 1, k + 1, k + w, k + w + 1], 1).ravel().astype(np.uint32)
    return Mesh(data.tobytes(), 8, ((0, 7, 2, 0),), index, w * h)


def fades(eye, corners: tuple) -> tuple[float, float]:
    """The medium waves' strength at the near and the far edge: by the eye's distance to each edge's middle on the
    water (``FADE_START``)."""
    bl, br, tl, tr = (np.asarray(c, float) for c in corners)
    out = []
    for a, b in ((bl, br), (tl, tr)):
        mid = (a + b) / 2
        d = float(np.linalg.norm(np.asarray(eye, float) - (mid[0], 0.0, mid[1])))
        t = np.clip((d - FADE_START / FADE_CAMERA) / (FADE_RANGE / FADE_CAMERA), 0.0, 1.0)
        out.append(max(FADE_FLOOR, 1.0 - float(t)))
    return out[0], out[1]


def grid_values(cam: gs.Camera, corners: tuple, waves: Waves1404, seconds: float, texels: int) -> dict:
    """The position pass's constants: the trapezoid as (bottom left, bottom right - bottom left) and (top left -
    bottom left, top right - bottom right), the fades, the depth map's scale (``texels`` a side), the waves and their
    scrolls ``frac(t speed / length)`` (t the render clock in seconds)."""
    bl, br, tl, tr = (np.asarray(c, float) for c in corners)
    near, far = fades(cam.eye, corners)
    (l0, l1), (h0, h1), (s0x, s0y), (s1x, s1y) = waves.length, waves.height, waves.speed0, waves.speed1
    t = float(seconds)
    return {"cProjGridTrapeze0": [*bl, *(br - bl)], "cProjGridTrapeze1": [*(tl - bl), *(tr - br)],
            "cOversampling": [2.0 * (1.0 - float(cam.view[1, 2])) + 1.0, near, far, 0.0],
            "cReciprocalTerrainSize": [1.0 / texels] * 4, "cWaveLength": [l0, l1, 0.8 * l0, 0.8 * l1],
            "cWaveHeight": [h0, h1], "cWaveChoppiness": [waves.choppiness, h0 * 0.5, 0.0, 0.0],
            "cWaveSpeed0": [(t * s0x / l0) % 1.0, 0.0, (t * s1x / l0) % 1.0, 0.0],
            "cWaveSpeed1": [(t * s0y / l1) % 1.0, 0.0, (t * s1y / l1) % 1.0, 0.0]}  # fmt: skip


def water_values(sun: Sun1404, cam: gs.Camera, waves: Waves1404) -> dict:
    """The surface pass's constants: camera, light and haze as 1404's models take them, the ground behind
    (``frame1404.behind_values``), the water manager's colours (the set's ocean colour, lit by ambient to sun by the
    sun's height **[I]**: which light fields the manager blends was inferred), the light's direction, the specular,
    the depth range, the caustics' scroll and the grid's neighbour step 1.5 / its points."""
    toward = np.asarray(sun.direction, float)
    toward = toward / max(1e-9, float(np.linalg.norm(toward)))
    k = abs(float(toward[1]))
    lit = np.asarray(sun.ambient, float) * (1 - k) + np.asarray(sun.diffuse, float) * k
    d, (w, h), s = waves.depth, GRID, sun.seconds
    return {**gs.camera_values(cam), **light_values(sun), **scattering_values(sun, cam), **behind_values(cam),
            "cTimer": [s, 0.0, s, s], "cWaterColor": [*(np.asarray(sun.ocean) * lit)],
            "cWaterColorUnlighted": list(sun.ocean), "cWaterLightDirection": list(toward),
            "cWaterSpecular": list(SPECULAR), "cWaterDepth": [d, -d, 1.0 / d, -1.0 / d],
            "cWaterCaustic": caustic_offsets(s), "cReciprocalGridSize": [1.5 / w, 0.0, 0.0, 1.5 / h]}  # fmt: skip


class Ocean1404(GridSurfaces):
    """The position and foam maps and the two passes (module docstring); ``mirrored``: ``GridSurfaces``'."""

    def __init__(self, ctx, stages: dict, dummies) -> None:
        self.ctx = ctx
        grid = stages["grid"]
        self.grid = FxcDraw(ctx, Program(grid.vs, grid.ps), screen_triangle(), NO_DEPTH, dummies)
        self.grid.fallbacks = {"cTexture1": OPEN_SEA}
        self.water, self.shadowed = (self._surface(ctx, stages.get(k), dummies) for k in ("water", "shadowed"))
        self.maps: dict = {}

    @staticmethod
    def _surface(ctx, water, dummies) -> FxcDraw | None:
        if water is None:
            return None
        draw = FxcDraw(ctx, Program(water.vs, water.ps), grid_mesh(), water.state, dummies)
        draw.fallbacks = {"cTexture1": FLAT, "cTexture3": NO_FOAM, "cTexture7": (0, 0, 0, 0)}
        return draw

    def _maps(self) -> dict:
        if not self.maps:
            position = self.ctx.native_texture(GRID, QRhiTexture.Format.RGBA32F)
            foam = self.ctx.native_texture(GRID, QRhiTexture.Format.RGBA8)
            self.maps = {"position": position, "foam": foam, "fbo": self.ctx.framebuffer([position, foam])}
        return self.maps

    def draw(self, fbo, size: tuple[int, int], cams: tuple, sun: Sun1404, waves: Waves1404, maps: dict,
             shadow: tuple | None = None) -> bool:  # fmt: skip
        """The position pass, then the surface into ``fbo`` (its colour and depth kept); ``cams``: the camera as the
        engine sees it (the trapezoid's) and as the target stands (the draws'); ``maps``: the waves (``waves``), the
        depth map (``depth``: its texture and texels a side; without it ``OPEN_SEA``) and the surface's maps by the
        shader's names (the sky cube, ripples, refraction, reflection, depth copy, caustics); ``shadow``: the sun's
        (``cbShadow``'s values and the map by name), None for none. False when the engine draws no water."""
        game, cam = cams
        corners = trapezoid(game.view @ game.proj, TRAPEZOID)
        if corners is None:
            return False
        own = self._maps()
        depth, texels = maps.get("depth") or (None, 1)
        values = grid_values(game, corners, waves, sun.seconds, texels)
        inputs = {"cTexture0": maps["waves"], "cTexture1": depth}
        draw_pass(self.ctx, own["fbo"],
                  lambda rp, sig, batch: self.grid.prepare(rp, sig, batch, inputs,
                                                           lambda c: gs.set_values(c.values(), values)),
                  lambda cb: self.grid.draw(cb, GRID), targets=2)  # fmt: skip
        surface = water_values(sun, cam, waves)
        textures = {k: v for k, v in maps.items() if k.startswith("cTexture")}
        textures.update({"cTexture4": own["position"].native, "cTexture5": own["foam"].native})
        pas = self.water
        if shadow is not None and self.shadowed is not None:
            pas, surface, textures = self.shadowed, {**surface, **shadow[0]}, {**textures, **shadow[1]}
        draw_pass(self.ctx, fbo,
                  lambda rp, sig, batch: pas.prepare(rp, sig, batch, textures,
                                                     lambda c: gs.set_values(c.values(), surface)),
                  lambda cb: pas.draw(cb, size), clear=False)  # fmt: skip
        return True

    def release(self) -> None:
        for d in (self.grid, self.water, self.shadowed):
            if d is not None:
                d.release()
        for key in ("fbo", "position", "foam"):
            if key in self.maps:
                self.maps[key].release()
        self.maps = {}
