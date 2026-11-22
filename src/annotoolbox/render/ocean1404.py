"""Anno 1404's sea as its projected grid manager draws it: each frame ``waterprojgrid.fx`` writes, into a float map
of the grid's 230 x 280 points, the world position of every point over the trapezoid the camera sees of the sea,
damped by the terrain-wide water depth map, and its foam; ``waterdisp.fx`` then draws the grid from that map,
refracting the scene below with caustics, reflecting the mirrored scene and the sky cube.

``COcean1404(ctx, stages, dummies)`` is the draw (``draw``); ``depth_map`` builds the water depth map from the heights
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
    SSun1404,
    behind_values,
    caustic_offsets,
    light_values,
    number_in,
    profile_block,
    scattering_values,
)
from .fxcdraw import NO_DEPTH, CFxcDraw
from .gamedraw import CMesh, CProgram
from .nativepass import draw_pass
from .ocean1701 import CGridSurfaces, SBounds, screen_triangle, trapezoid
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
TRAPEZOID = SBounds(left=-1.14, right=1.14, near_row=-1.28, far_row=1.14, near_depth=-0.15, near_height=0.1)
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
class SWaves1404:
    """A set's ``WaterDescription``: the great and medium waves' length, height and the two speeds, the choppiness,
    the depth the waves calm over (negative: below the water). The defaults are the manager's own."""

    length: tuple[float, float] = (0.01, 0.11)
    height: tuple[float, float] = (2.0, 0.25)
    speed0: tuple[float, float] = (1e-4, 0.0045)
    speed1: tuple[float, float] = (1.2e-4, 0.006)
    choppiness: float = 2.1
    depth: float = -3.5


def read_waves(text: str, name: str) -> SWaves1404:
    """The ``WaterDescription`` of set ``name`` in ``ambientmap.xml`` (a value it lacks keeps the default)."""
    body, base = profile_block(profile_block(text, name), "WaterDescription"), SWaves1404()

    def pair(tag: str, fallback: tuple) -> tuple:
        found = re.search(rf"<{tag}>\s*<x>{NUMBER}</x>\s*<y>{NUMBER}</y>", body)
        return (float(found.group(1)), float(found.group(2))) if found else fallback

    return SWaves1404(pair("WaveLength", base.length), pair("WaveHeight", base.height),
                     pair("WaveSpeed0", base.speed0), pair("WaveSpeed1", base.speed1),
                     number_in(body, "Choppiness", base.choppiness), number_in(body, "MaxWaterDepth", base.depth))  # fmt: skip


def _ring(ring_radius: int) -> list[tuple[int, int]]:
    """The (dx, dz) of the square ring ``ring_radius`` round a texel in the order the engine walks it."""
    out = [(offset_x, ring_radius) for offset_x in range(-ring_radius, ring_radius + 1)] + [
        (ring_radius, offset_z) for offset_z in range(ring_radius - 1, -ring_radius - 1, -1)
    ]
    return (
        out
        + [(offset_x, -ring_radius) for offset_x in range(ring_radius - 1, -ring_radius, -1)]
        + [(-ring_radius, offset_z) for offset_z in range(-ring_radius, ring_radius)]
    )


def _shifted(source: np.ndarray, offset_x: int, offset_z: int) -> tuple[np.ndarray, np.ndarray]:
    """``source`` read ``(dx, dz)`` texels on, and where that lies on the map."""
    height, width = source.shape
    out, inside = np.zeros_like(source), np.zeros(source.shape, bool)
    if abs(offset_x) >= width or abs(offset_z) >= height:  # off the map everywhere
        return out, inside
    z_slice, x_slice = (
        slice(max(0, -offset_z), min(height, height - offset_z)),
        slice(max(0, -offset_x), min(width, width - offset_x)),
    )
    out[z_slice, x_slice] = source[
        z_slice.start + offset_z : z_slice.stop + offset_z, x_slice.start + offset_x : x_slice.stop + offset_x
    ]
    inside[z_slice, x_slice] = True
    return out, inside


def ring_min(source: np.ndarray, reach: int) -> np.ndarray:
    """The engine's first blur: per texel the least of the rings 1 to ``reach`` round it (the texel itself left
    out, the texels off the map too) as bytes, 1 where none lies on the map; truncated to a byte."""
    low = np.ones(source.shape, np.float32)
    for ring_radius in range(1, reach + 1):
        for offset_x, offset_z in _ring(ring_radius):
            shifted, inside = _shifted(source, offset_x, offset_z)
            low = np.where(inside, np.minimum(low, shifted.astype(np.float32) * INV255), low)
    return (low * np.float32(255)).astype(np.uint8)


def ring_mean(source: np.ndarray, reach: int) -> np.ndarray:
    """The engine's second blur: the mean of the rings 1 to ``reach`` (the texel left out) over the texels on the
    map, summed in its order, truncated to a byte."""
    total, count = np.zeros(source.shape, np.float32), np.zeros(source.shape, np.int32)
    for ring_radius in range(1, reach + 1):
        for offset_x, offset_z in _ring(ring_radius):
            shifted, inside = _shifted(source, offset_x, offset_z)
            total = np.where(inside, total + shifted.astype(np.float32) * INV255, total)
            count += inside
    return (total / np.maximum(count, 1).astype(np.float32) * np.float32(255)).astype(np.uint8)


def depth_map(heights: np.ndarray, step_tiles: float, depth: float = SWaves1404.depth) -> np.ndarray:
    """The water depth map as the projected grid manager builds it once per world (shader-compile.md 12, "The sea"):
    a texel per unit, its height ``sampled`` at the texel's corner (``heights``: [z, x] at ``step`` units, bilinear [ours]);
    red the depth ``(h + 0.5) / depth`` clamped to 1/255..1 as a byte, blurred by the 9 x 9 minimum and the 11 x 11
    mean; green that depth + 0.5 (at most 1); blue twice it, clamped; alpha 255 where the texel and its eight
    neighbours two texels off lie above ``LAND_ABOVE`` (the water clipped there). RGBA bytes [z, x]."""
    side = max(1, int(round((heights.shape[0] - 1) * step_tiles)))
    sampled = sample_grid(np.asarray(heights, np.float64), np.arange(side) / step_tiles).astype(np.float32)
    lift = sampled + np.float32(0.5)
    one = np.clip(lift * np.float32(1.0 / depth), INV255, np.float32(1.0))
    two = np.clip(lift * np.float32(2.0 / depth), INV255, np.float32(1.0))
    land = np.zeros((side, side), bool)
    if side > 4:
        inner = np.ones((side - 4, side - 4), bool)
        for offset_z in (-2, 0, 2):
            for offset_x in (-2, 0, 2):
                inner &= sampled[2 + offset_z : side - 2 + offset_z, 2 + offset_x : side - 2 + offset_x] > LAND_ABOVE
        land[2:-2, 2:-2] = inner
    out = np.zeros((side, side, 4), np.uint8)
    out[..., 0] = ring_mean(ring_min((one * np.float32(255)).astype(np.uint8), 4), 5)
    out[..., 1] = (np.minimum(one + np.float32(0.5), np.float32(1.0)) * np.float32(255)).astype(np.uint8)
    out[..., 2] = (two * np.float32(255)).astype(np.uint8)
    out[..., 3] = np.where(land, 255, 0)
    return out


def grid_mesh(grid: tuple[int, int] = GRID) -> CMesh:
    """The grid's points (i / (w - 1), j / (h - 1)) and the engine's triangles (k, k + w, k + 1), (k + 1, k + w,
    k + w + 1); the surface reads the position map there, between its texels (linear, as the engine's Direct3D 10
    path does)."""
    width, height = grid
    coord_u, coord_v = np.meshgrid(np.arange(width) / (width - 1), np.arange(height) / (height - 1))
    data = np.stack([coord_u.ravel(), coord_v.ravel()], 1).astype("<f4")
    k = (np.arange(height - 1)[:, None] * width + np.arange(width - 1)[None, :]).ravel()
    index = np.stack([k, k + width, k + 1, k + 1, k + width, k + width + 1], 1).ravel().astype(np.uint32)
    return CMesh(data.tobytes(), 8, ((0, 7, 2, 0),), index, width * height)


def fades(eye, corners: tuple) -> tuple[float, float]:
    """The medium waves' strength at the near and the far edge: by the eye's distance to each edge's middle on the
    water (``FADE_START``)."""
    bl, br, tl, tr = (np.asarray(corner, float) for corner in corners)
    out = []
    for first, second in ((bl, br), (tl, tr)):
        midpoint = (first + second) / 2
        distance = float(np.linalg.norm(np.asarray(eye, float) - (midpoint[0], 0.0, midpoint[1])))
        fade = np.clip((distance - FADE_START / FADE_CAMERA) / (FADE_RANGE / FADE_CAMERA), 0.0, 1.0)
        out.append(max(FADE_FLOOR, 1.0 - float(fade)))
    return out[0], out[1]


def grid_values(camera: gs.SCamera, corners: tuple, waves: SWaves1404, seconds: float, texels: int) -> dict:
    """The position pass's constants: the trapezoid as (bottom left, bottom right - bottom left) and (top left -
    bottom left, top right - bottom right), the fades, the depth map's scale (``texels`` a side), the waves and their
    scrolls ``frac(t speed / length)`` (t the render clock in seconds)."""
    bl, br, tl, tr = (np.asarray(corner, float) for corner in corners)
    near, far = fades(camera.eye, corners)
    (l0, l1), (h0, h1), (s0x, s0y), (s1x, s1y) = waves.length, waves.height, waves.speed0, waves.speed1
    time_s = float(seconds)
    return {"cProjGridTrapeze0": [*bl, *(br - bl)], "cProjGridTrapeze1": [*(tl - bl), *(tr - br)],
            "cOversampling": [2.0 * (1.0 - float(camera.view[1, 2])) + 1.0, near, far, 0.0],
            "cReciprocalTerrainSize": [1.0 / texels] * 4, "cWaveLength": [l0, l1, 0.8 * l0, 0.8 * l1],
            "cWaveHeight": [h0, h1], "cWaveChoppiness": [waves.choppiness, h0 * 0.5, 0.0, 0.0],
            "cWaveSpeed0": [(time_s * s0x / l0) % 1.0, 0.0, (time_s * s1x / l0) % 1.0, 0.0],
            "cWaveSpeed1": [(time_s * s0y / l1) % 1.0, 0.0, (time_s * s1y / l1) % 1.0, 0.0]}  # fmt: skip


def water_values(sun: SSun1404, camera: gs.SCamera, waves: SWaves1404) -> dict:
    """The surface pass's constants: camera, light and haze as 1404's models take them, the ground behind
    (``frame1404.behind_values``), the water manager's colours (the set's ocean colour, lit by ambient to sun by the
    sun's height **[I]**: which light fields the manager blends was inferred), the light's direction, the specular,
    the depth range, the caustics' scroll and the grid's neighbour step 1.5 / its points."""
    toward = np.asarray(sun.direction, float)
    toward = toward / max(1e-9, float(np.linalg.norm(toward)))
    k = abs(float(toward[1]))
    lit = np.asarray(sun.ambient, float) * (1 - k) + np.asarray(sun.diffuse, float) * k
    depth, (width, height), seconds = waves.depth, GRID, sun.seconds
    return {**gs.camera_values(camera), **light_values(sun), **scattering_values(sun, camera), **behind_values(camera),
            "cTimer": [seconds, 0.0, seconds, seconds], "cWaterColor": [*(np.asarray(sun.ocean) * lit)],
            "cWaterColorUnlighted": list(sun.ocean), "cWaterLightDirection": list(toward),
            "cWaterSpecular": list(SPECULAR), "cWaterDepth": [depth, -depth, 1.0 / depth, -1.0 / depth],
            "cWaterCaustic": caustic_offsets(seconds), "cReciprocalGridSize": [1.5 / width, 0.0, 0.0, 1.5 / height]}  # fmt: skip


class COcean1404(CGridSurfaces):
    """The position and foam maps and the two passes (module docstring); ``is_mirrored``: ``CGridSurfaces``'."""

    def __init__(self, ctx, stages: dict, dummies) -> None:
        self.m_ctx = ctx
        grid = stages["grid"]
        self.m_grid = CFxcDraw(ctx, CProgram(grid.vertex_stage, grid.pixel_stage), screen_triangle(), NO_DEPTH, dummies)
        self.m_grid.m_fallbacks = {"cTexture1": OPEN_SEA}
        self.m_water, self.m_shadowed = (self._surface(ctx, stages.get(k), dummies) for k in ("water", "shadowed"))
        self.m_maps: dict = {}

    @staticmethod
    def _surface(ctx, water, dummies) -> CFxcDraw | None:
        if water is None:
            return None
        draw = CFxcDraw(ctx, CProgram(water.vertex_stage, water.pixel_stage), grid_mesh(), water.state, dummies)
        draw.m_fallbacks = {"cTexture1": FLAT, "cTexture3": NO_FOAM, "cTexture7": (0, 0, 0, 0)}
        return draw

    def _maps(self) -> dict:
        if not self.m_maps:
            position = self.m_ctx.native_texture(GRID, QRhiTexture.Format.RGBA32F)
            foam = self.m_ctx.native_texture(GRID, QRhiTexture.Format.RGBA8)
            self.m_maps = {"position": position, "foam": foam, "fbo": self.m_ctx.framebuffer([position, foam])}
        return self.m_maps

    def draw(self, framebuffer, size_px: tuple[int, int], cameras: tuple, sun: SSun1404, waves: SWaves1404, maps: dict,
             shadow: tuple | None = None) -> bool:  # fmt: skip
        """The position pass, then the surface into ``framebuffer`` (its colour and depth kept); ``cameras``: the camera as the
        engine sees it (the trapezoid's) and as the target stands (the draws'); ``maps``: the waves (``waves``), the
        depth map (``depth``: its texture and texels a side; without it ``OPEN_SEA``) and the surface's maps by the
        shader's names (the sky cube, ripples, refraction, reflection, depth copy, caustics); ``shadow``: the sun's
        (``cbShadow``'s values and the map by name), None for none. False when the engine draws no water."""
        game, camera = cameras
        corners = trapezoid(game.view @ game.projection, TRAPEZOID)
        if corners is None:
            return False
        own = self._maps()
        depth, texels = maps.get("depth") or (None, 1)
        values = grid_values(game, corners, waves, sun.seconds, texels)
        inputs = {"cTexture0": maps["waves"], "cTexture1": depth}
        draw_pass(self.m_ctx, own["fbo"],
                  lambda render_pass, signature, batch: self.m_grid.prepare(render_pass, signature, batch, inputs,
                                                           lambda constants: gs.set_values(constants.values(), values)),
                  lambda command_buffer: self.m_grid.draw(command_buffer, GRID), targets=2)  # fmt: skip
        surface = water_values(sun, camera, waves)
        textures = {k: value for k, value in maps.items() if k.startswith("cTexture")}
        textures.update({"cTexture4": own["position"].native, "cTexture5": own["foam"].native})
        pas = self.m_water
        if shadow is not None and self.m_shadowed is not None:
            pas, surface, textures = self.m_shadowed, {**surface, **shadow[0]}, {**textures, **shadow[1]}
        draw_pass(self.m_ctx, framebuffer,
                  lambda render_pass, signature, batch: pas.prepare(render_pass, signature, batch, textures,
                                                     lambda constants: gs.set_values(constants.values(), surface)),
                  lambda command_buffer: pas.draw(command_buffer, size_px), clear=False)  # fmt: skip
        return True

    def release(self) -> None:
        for draw in (self.m_grid, self.m_water, self.m_shadowed):
            if draw is not None:
                draw.release()
        for key in ("fbo", "position", "foam"):
            if key in self.m_maps:
                self.m_maps[key].release()
        self.m_maps = {}
