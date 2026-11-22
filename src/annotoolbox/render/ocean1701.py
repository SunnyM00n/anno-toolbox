"""Anno 1701's sea as its terrain draws it ("vertex water"): a projected grid. Each frame ``waterdisp.fx`` writes,
into a float map of 175 x 256 texels, the world position of every grid point over the trapezoid the camera sees of
the sea, raised by three pairs of wave lookups and damped by the water depth; ``water.fx`` then draws the grid from
that map (a vertex texture), refracting the scene below, reflecting the mirrored scene and the sky cube.

``COcean1701(ctx, stages, dummies)`` is the draw: ``draw(fbo, size_px, cameras, light, maps, seconds, ocean)`` runs both passes;
``trapezoid`` is the engine's cut of the camera's view on the sea, ``disp_values`` / ``water_values`` the constants by
name. Positions are the game's world units (``TILE`` a tile). docs/rendering/shader-compile.md section 13, "The sea".
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from PySide6.QtGui import QRhiTexture

from ..models.fxstage import SSampler
from . import gameshader as gs
from .frame1404 import colour_in, profile_block
from .frame1701 import SLight1701, light_values
from .frameforward import TEXTURE
from .fxcdraw import NO_DEPTH, CFxcDraw
from .gamedraw import CMesh, CProgram
from .nativepass import draw_pass

DISP_FX = "data/system/fxshaders/various/waterdisp.fx"
WATER_FX = "data/system/fxshaders/various/water.fx"
TECHNIQUE = "ProjGrid"
# the water options of the highest setting: the reflection of the scene; with the sun's shadow the soft one on an
# R32F map, as the models'; the coast and wake waves (VW_DYNAMICWAVES) where a wave texture is drawn
# (``coast1701``, the island view; shader-compile.md 13)
DEFINES = (("VW_REFLECTION", "1"),)
SHADOW_DEFINES = (("VERTEXWATER_SHADOW", "1"), ("VERTEXWATER_SOFTSHADOW", "1"))
STAGES = {"disp": (DISP_FX, DEFINES, TECHNIQUE), "water": (WATER_FX, DEFINES, TECHNIQUE),
          "shadowed": (WATER_FX, DEFINES + SHADOW_DEFINES, TECHNIQUE)}  # fmt: skip
WAVE_STAGES = {"disp_waves": (DISP_FX, DEFINES + (("VW_DYNAMICWAVES", "1"),), TECHNIQUE)}
NO_RAIN = (0, 0, 0, 0)  # the rain's drops without a storm: none [I]
DYNAMIC_WAVES = "cDynamicWaves"  # ours: the terrain's wave texture among a draw's maps (pcTex1 of the position pass)
SHADOW_MAP = "cSamplerShadowmap"
WATER = "data/graphics/textures/water/vertexwater/"
WAVES = WATER + "waves_0.dds"  # the engine's Waves.tga at the highest texture quality
SKY_CUBE = WATER + "skycubemap.dds"
GRID = (175, 256)  # the grid's points across and away from the camera (the water object's constructor)


@dataclass(frozen=True)
class SBounds:
    """How an engine widens the screen into its sea's trapezoid: the screen's x at the left and right, its y of the
    near (bottom) and far (top) edge, the depth just before the near plane, the height the near edge is cut at."""

    left: float = -1.2
    right: float = 1.3
    near_row: float = -1.25
    far_row: float = 1.05
    near_depth: float = -0.08
    near_height: float = 15.0


TRAPEZOID = SBounds()  # 1701's: the far edge cut at the water, the near edge 15 units above it
SPEEDS = (0.03, 0.02, 0.01, 0.005)  # the four clocks of pcUsrConstant2 (water: 0), per second
DEPTH_RANGE = 125.0  # the terrain's water depth (terrain_pervertex.cg): saturate(-y / 125)
DEEP = (255, 255, 255, 255)  # the depth map's stand-in without ground below: deep water [ours]
POINT = SSampler("MIN_MAG_MIP_POINT", "CLAMP", "CLAMP", "CLAMP")  # a vertex texture fetch (Direct3D 9's: point)
OCEAN = (0.2, 0.447, 0.8, 0.0)  # a profile's ocean colour where the file has none **[I]**


def ocean_color(ambient: str, name: str) -> tuple[float, float, float, float]:
    """A profile's ``OceanWaterColor`` of ``ambientmap.xml`` (rgba as stored; the engine keeps it as ARGB bytes)."""
    found = colour_in(profile_block(ambient, name), "OceanWaterColor")
    return found if found is not None and len(found) == 4 else OCEAN


def _unproject(inverse: np.ndarray, x: float, y: float, z: float) -> np.ndarray:
    point = np.array([x, y, z, 1.0]) @ inverse
    return point[:3] / point[3]


def _cut(start: np.ndarray, end: np.ndarray, height_m: float, bounded: bool = True) -> np.ndarray | None:
    """Where the segment ``start`` -> ``end`` crosses the height ``height`` (``bounded``: strictly between them)."""
    rise = end[1] - start[1]
    if rise == 0.0:
        return None
    fraction = (height_m - start[1]) / rise
    if bounded and not 0.0 < fraction < 1.0:
        return None
    return start + (end - start) * fraction


def trapezoid(view_proj: np.ndarray, bounds: SBounds = TRAPEZOID) -> tuple | None:
    """The sea's trapezoid for the camera's view-projection (ours: row vectors, depth 0 near, the game's units and
    axes), as the engine cuts it: (bottom left, bottom right, top left, top right) as (x, z), or None when either
    near corner misses (the engine then draws no water). A far corner is where the screen's top edge ray crosses the
    water, else where the far plane's side edge does; a near one where the bottom edge ray crosses the near height.
    ``bounds``: 1701's by default (1404's grid cuts its own, ``ocean1404``)."""
    limits = bounds
    inv = np.linalg.inv(np.asarray(view_proj, float))
    out = []
    for x in (limits.left, limits.right):
        near, far = _unproject(inv, x, limits.near_row, limits.near_depth), _unproject(inv, x, limits.near_row, 1.0)
        out.append(_cut(near, far, limits.near_height))
    if out[0] is None or out[1] is None:
        return None
    for x in (limits.left, limits.right):
        near, far = _unproject(inv, x, limits.far_row, limits.near_depth), _unproject(inv, x, limits.far_row, 1.0)
        top = _cut(near, far, 0.0)
        if top is None:  # the ray passes over the water: along the far plane's side
            top = _cut(far, _unproject(inv, x, limits.near_row, 1.0), 0.0, bounded=False)
        out.append(top if top is not None else out[len(out) - 2])
    return tuple((float(point[0]), float(point[2])) for point in out)


def grid_mesh(grid: tuple[int, int] = GRID) -> CMesh:
    """The grid's points (u, v) and the engine's triangles. The engine stores ``i / w``, which falls on a texel's
    edge of the position map; the points here sit half a texel on, at its middle, so that the point fetch takes that
    texel on any hardware (the position is the texel's, the stored value is not read otherwise) **[I]**."""
    width, height = grid
    coord_u, coord_v = np.meshgrid((np.arange(width) + 0.5) / width, (np.arange(height) + 0.5) / height)
    data = np.stack([coord_u.ravel(), coord_v.ravel()], 1).astype("<f4")
    k = (np.arange(height - 1)[:, None] * width + np.arange(width - 1)[None, :]).ravel()
    index = np.stack([k + 1, k, k + width, k + 1, k + width, k + width + 1], 1).ravel().astype(np.uint32)
    return CMesh(data.tobytes(), 8, ((0, 7, 2, 0),), index, width * height)


def screen_triangle(grid: tuple[int, int] | None = None) -> CMesh:
    """A triangle over the screen with the engine's quad's coordinates: u from 0 at the left to 1 at the right, v
    from 0 at the top to 1 at the bottom, as Direct3D reads a texture (1404's position pass). With ``grid`` (a map's
    texels a side) half a texel on, so that texel (i, j) gets (i / w, j / h): Direct3D 9 put its pixel centres on
    whole coordinates (1701's position pass)."""
    du, dv = (0.5 / grid[0], 0.5 / grid[1]) if grid else (0.0, 0.0)
    vertices = np.array([[-1, -1, 0, -du, 1 - dv], [3, -1, 0, 2 - du, 1 - dv], [-1, 3, 0, -du, -1 - dv]], "<f4")
    return CMesh(vertices.tobytes(), 20, ((0, 7, 3, 0), (4, 7, 2, 12)), np.array([0, 1, 2], np.uint32), 3)


def deep_color(light: SLight1701, ocean) -> np.ndarray:
    """The colour ``water.fx`` turns the refraction to over depth: the ocean's lit by ``saturate(ambient + diffuse)``,
    times 0.85 (gamma values)."""
    lit = np.clip(np.asarray(light.ambient, float) + np.asarray(light.diffuse, float), 0.0, 1.0)
    return np.asarray(ocean, float)[:3] * lit * 0.85


def clocks(seconds: float) -> list[float]:
    """The four clocks (the fractions of ``SPEEDS`` times the render clock in seconds)."""
    return [(speed * seconds) % 1.0 for speed in SPEEDS]


def disp_values(camera: gs.SCamera, corners: tuple, seconds: float, ahead) -> dict:
    """The position pass's constants: the view-projection into texture space (the depth map's lookup), the
    trapezoid as the bottom left corner and its three edges, the clocks, the oversampling ``2 + ahead.y`` (the
    camera's forward direction **[I]**: more rows near the camera the flatter it looks)."""
    bl, br, tl, tr = (np.asarray(corner, float) for corner in corners)
    return {"vcMtxTexture0": camera.view @ camera.projection @ TEXTURE, "pcUsrConstant0": [*bl, *(br - bl)],
            "pcUsrConstant1": [*(tl - bl), *(tr - br)], "pcUsrConstant2": clocks(seconds),
            "vcUsrConstant0": [2.0 + float(ahead[1]), 0.0, 0.0, 0.0]}  # fmt: skip


def water_values(light: SLight1701, camera: gs.SCamera, ocean, seconds: float) -> dict:
    """The surface pass's constants: the camera, the light and the depth fog of the profile in world units, the
    clocks, the ocean's colour and the fog's colour."""
    values = light_values(light, camera, np.identity(4))
    fog = [light.fog_start, light.fog_end, 1.0, 1.0 / max(1e-6, light.fog_end - light.fog_start)]
    width, height = GRID
    return {**values, "vcGlbFogSEDC": fog, "vcFog.vcGlbFogSEDC": fog, "vcUsrConstant0": [width, height, 1.5 / width, 1.5 / height],
            "pcUsrConstant0": clocks(seconds), "pcUsrConstant1": list(ocean), "pcUsrConstant2": [*light.fog, 1.0]}  # fmt: skip


def shadow_inputs(shadow: tuple, units: float) -> tuple[dict, dict]:
    """A frame's sun shadow (``frame1701.shadow_values``' receiving values and the map by the models' name), drawn in
    a view whose unit is ``units`` world units, as the sea reads it in world units: the map's matrix and the depth
    plane scaled, the map under the sea's sampler name."""
    values, maps = shadow
    scale = np.diag([1.0 / units] * 3 + [1.0])
    plane = np.asarray(values["vcShdConstants"], float)
    return ({**values, "vcMtxShadowBias": scale @ np.asarray(values["vcMtxShadowBias"], float),
             "vcShdConstants": [*(plane[:3] / units), plane[3]]}, {SHADOW_MAP: next(iter(maps.values()))})  # fmt: skip


class CGridSurfaces:
    """What 1404's and 1701's projected grids share: the surface's draws without and with the sun's shadow
    (``water``, ``shadowed``: None where it did not compile) and ``mirrored``: the camera's projection mirrors (the
    game's axes) or turns the picture over, so the grid's faces swap."""

    m_water: CFxcDraw | None = None
    m_shadowed: CFxcDraw | None = None

    @property
    def m_is_mirrored(self) -> bool:
        return self.m_water.m_is_mirrored

    @m_is_mirrored.setter
    def m_is_mirrored(self, on: bool) -> None:
        for draw in (self.m_water, self.m_shadowed):
            if draw is not None and on != draw.m_is_mirrored:
                draw.release()  # the pipeline keeps its culling
                draw.m_is_mirrored = on


class COcean1701(CGridSurfaces):
    """The position map and the two passes (module docstring), the surface with the sun's shadow too where its stage
    is given (``shadowed``); ``mirrored``: ``CGridSurfaces``'."""

    def __init__(self, ctx, stages: dict, dummies) -> None:
        self.m_ctx = ctx
        self.m_disp, self.m_disp_waves = (self._position(ctx, stages.get(k), dummies) for k in ("disp", "disp_waves"))
        self.m_water, self.m_shadowed = (self._surface(ctx, stages.get(k), dummies) for k in ("water", "shadowed"))
        self.m_map = self.m_map_fbo = None

    @staticmethod
    def _position(ctx, displacement, dummies) -> CFxcDraw | None:
        if displacement is None:
            return None
        draw = CFxcDraw(
            ctx, CProgram(displacement.vertex_stage, displacement.pixel_stage), screen_triangle(GRID), NO_DEPTH, dummies
        )
        draw.m_fallbacks = {"samplerTex2": DEEP, "samplerTex3": NO_RAIN}
        return draw

    @staticmethod
    def _surface(ctx, water, dummies) -> CFxcDraw | None:
        if water is None:
            return None
        vs = replace(water.vertex_stage, samplers=dict.fromkeys(water.vertex_stage.samplers, POINT))
        draw = CFxcDraw(ctx, CProgram(vs, water.pixel_stage), grid_mesh(), water.state, dummies)
        draw.m_fallbacks = {"cSamplerWaterDepth": DEEP, "cSamplerReflec": (0, 0, 0, 0)}
        return draw

    def _map(self):
        if self.m_map is None:
            self.m_map = self.m_ctx.native_texture(GRID, QRhiTexture.Format.RGBA32F)
            self.m_map_fbo = self.m_ctx.framebuffer([self.m_map])
        return self.m_map

    def draw(self, framebuffer, size_px: tuple[int, int], cameras: tuple, light: SLight1701, maps: dict, seconds: float,
             ocean=OCEAN, shadow: tuple | None = None) -> bool:  # fmt: skip
        """The position pass, then the surface into ``framebuffer`` (its colour and depth kept), depth tested and blended;
        ``cameras``: the camera as the engine sees it (the trapezoid's) and as the target stands (the draws'); ``maps``:
        the waves (``cSamplerWave``), the sky cube, the refraction, the reflection and the depth map by the
        shaders' sampler names, and the coast waves' texture (``DYNAMIC_WAVES``, ``coast1701``: the position pass
        with ``VW_DYNAMICWAVES``); ``shadow``: the sun's (``shadow_inputs``), None for none. False when the near
        corners miss the sea (the engine draws none)."""
        game, camera = cameras
        corners = trapezoid(game.view @ game.projection)
        if corners is None:
            return False
        ahead = -np.asarray(game.view, float)[:3, 2]
        disp = disp_values(camera, corners, seconds, ahead)
        position = self._map()
        disp_maps = {"samplerTex0": maps.get("cSamplerWave"), "samplerTex2": maps.get("cSamplerWaterDepth"),
                     "samplerTex1": maps.get(DYNAMIC_WAVES)}  # fmt: skip
        disp_maps = {k: value for k, value in disp_maps.items() if value is not None}
        pas = self.m_disp_waves if "samplerTex1" in disp_maps and self.m_disp_waves is not None else self.m_disp
        draw_pass(self.m_ctx, self.m_map_fbo,
                  lambda render_pass, signature, batch: pas.prepare(render_pass, signature, batch, disp_maps,
                                                     lambda constants: gs.set_values(constants.values(), disp)),
                  lambda command_buffer: pas.draw(command_buffer, GRID))  # fmt: skip
        values = water_values(light, camera, ocean, seconds)
        textures = {**maps, "VertexSamplerTex0": position.native}
        surface = self.m_water
        if shadow is not None and self.m_shadowed is not None:
            surface, values, textures = self.m_shadowed, {**values, **shadow[0]}, {**textures, **shadow[1]}
        draw_pass(self.m_ctx, framebuffer,
                  lambda render_pass, signature, batch: surface.prepare(render_pass, signature, batch, textures,
                                                         lambda constants: gs.set_values(constants.values(), values)),
                  lambda command_buffer: surface.draw(command_buffer, size_px), clear=False)  # fmt: skip
        return True

    def release(self) -> None:
        for draw in (self.m_disp, self.m_disp_waves, self.m_water, self.m_shadowed):
            if draw is not None:
                draw.release()
        for target in (self.m_map_fbo, self.m_map):
            if target is not None:
                target.release()
        self.m_map = self.m_map_fbo = None
