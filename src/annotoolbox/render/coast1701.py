"""Anno 1701's coast waves as its terrain draws them: each frame the island's coast lines are drawn as strips into
the terrain's wave texture (screen space, cleared to grey) by ``coastline.fx``'s flat and steep dynamic-wave
techniques, a scrolling wave height the sea's position pass (``VW_DYNAMICWAVES``) adds to the water.

``CCoastWaves1701(ctx, stages, dummies)`` is the draw: ``lines(strips(...))`` takes the strips the engine builds
from the coast lines, ``draw(camera, seconds, maps)`` fills the wave texture and returns it. World units throughout;
docs/rendering/shader-compile.md section 13, "The coast and wake waves".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .fxcdraw import CFxcDraw
from .gamedraw import CMesh, CProgram
from .nativepass import draw_pass

FX = "data/system/fxshaders/terrain/coastline.fx"
DEFINES = (("USE_DYNAMIC_WAVES_VERTEX_WATER", "1"),)  # the engine's, with vertex water
# a coast line's m_Variation: 1 a steep coast (the engine's second list, the spray's), any other a flat one
STAGES = {"flat": (FX, DEFINES, "CoastlineDynamicWave"), "steep": (FX, DEFINES, "CoastlineDynamicWaveSteep")}
STEEP = 1
WATER = "data/graphics/textures/water/"
# the engine's .tga names at the highest texture quality
TEXTURES = {"noise": WATER + "coastwavenoise_0.dds", "blend": WATER + "waveblend_0.dds",
            "flat": WATER + "coastwavenormalmap_0.dds", "steep": WATER + "coaststeepwavenormalmap_0.dds"}  # fmt: skip
# the textures each technique binds (pcTex0, 1, 2 by the shaders' sampler names)
BINDINGS = {"flat": {"samplerTex0": "noise", "samplerTex1": "blend", "samplerTex2": "flat"},
            "steep": {"samplerTex0": "blend", "samplerTex1": "steep"}}  # fmt: skip
SIDE = 2048  # the wave texture: the refraction's size, 512 x 4 at the highest quality
GREY = 127 / 255  # its clear, 0x7f7f7f7f: a flat sea reads (127/255 - 0.5) x 20 below the water
LIFT = 1.0  # the strips lie a unit above the coast line
U_SCALE = 0.0039  # u: the length along the strip in world units, times this
# the four clocks of vcUsrConstant0 per second (the coast object's update); the steep one's last is a sine
FLAT_SPEEDS = (0.045, 0.001, 0.05, 0.025)
STEEP_SPEEDS = (0.04, 0.003, 0.1)
STEEP_SWAY = (1.25, 0.075)  # the steep clock's w: 0.075 sin(1.25 t)
STRIDE = 24  # position, colour (four bytes), uv


def _frac(x: float) -> float:
    """``x`` less its integer part, as the engine truncates (a negative value stays negative)."""
    return x - math.trunc(x)


def clocks(seconds: float, steep: bool) -> list[float]:
    """``vcUsrConstant0`` of a technique at the coast object's clock ``seconds``."""
    if not steep:
        return [_frac(speed * seconds) for speed in FLAT_SPEEDS]
    rate, amount = STEEP_SWAY
    return [*(_frac(speed * seconds) for speed in STEEP_SPEEDS), _frac(amount * math.sin(rate * seconds))]


def offsets(points: np.ndarray, widths: np.ndarray) -> np.ndarray:
    """Each point's step across the strip, ``width x (-dz, 0, dx) / |d|``: ``d`` the segment at an end, the sum of
    the two unit segments (in 3D) at a point between; a point without a direction gets none."""
    path_points = np.asarray(points, float)
    if len(path_points) < 2:  # no segment: no direction
        return np.zeros((len(path_points), 3))
    seg = path_points[1:] - path_points[:-1]
    directions = np.empty((len(path_points), 3))
    directions[0], directions[-1] = seg[0], seg[-1]
    if len(path_points) > 2:
        lengths = np.linalg.norm(seg, axis=1, keepdims=True)
        unit = seg / np.where(lengths > 0, lengths, 1.0)
        directions[1:-1] = unit[1:] + unit[:-1]
    across = np.hypot(directions[:, 0], directions[:, 2])
    scale = np.where(across > 0, np.asarray(widths, float) / np.where(across > 0, across, 1.0), 0.0)
    return np.stack([-directions[:, 2] * scale, np.zeros(len(path_points)), directions[:, 0] * scale], 1)


def strip(points, widths) -> np.ndarray:
    """The engine's strip of a coast line (points (n, 3) and widths in world units): per point the vertex off the
    line (``point - offset``, v 0) and the one on it (v 1), both lifted ``LIFT``; u the running length times
    ``U_SCALE``, each step the mean of the two edges as the engine measures them (its off-line edge from the last
    point plus its offset, the height difference plus the lift); alpha 0 at both ends of a line of three points or
    more. Rows (x, y, z, alpha, u, v)."""
    path_points = np.asarray(points, float)
    offset = offsets(path_points, widths)
    lift = np.array([0.0, LIFT, 0.0])
    near_edge, far_edge = path_points - offset + lift, path_points + lift
    along = np.zeros(len(path_points))
    if len(path_points) > 1:
        edge_a = np.linalg.norm(near_edge[1:] - (path_points[:-1] + offset[:-1]), axis=1)
        edge_b = np.linalg.norm(far_edge[1:] - path_points[:-1], axis=1)
        along[1:] = np.cumsum(0.5 * edge_a + 0.5 * edge_b) * U_SCALE
    alpha = np.ones(len(path_points))
    if len(path_points) > 2:
        alpha[[0, -1]] = 0.0
    rows = np.empty((2 * len(path_points), 6))
    rows[0::2, :3], rows[1::2, :3] = near_edge, far_edge
    rows[:, 3] = np.repeat(alpha, 2)
    rows[:, 4] = np.repeat(along, 2)
    rows[0::2, 5], rows[1::2, 5] = 0.0, 1.0
    return rows


@dataclass(frozen=True)
class SCoastLine:
    """A coast line in world units: its points (n, 3), each point's width and its ``m_Variation``."""

    points: np.ndarray
    widths: np.ndarray
    variation: int = 0


def strip_mesh(lines: list[SCoastLine]) -> CMesh | None:
    """The strips of ``lines`` as one triangle list (the engine draws each line as a strip); None without a line."""
    rows, index, base = [], [], 0
    for line in lines:
        if len(line.points) < 2:
            continue
        ribbon = strip(line.points, line.widths)
        row_count = len(ribbon)
        k = np.arange(row_count - 2)
        index.append(np.stack([base + k, base + k + 1, base + k + 2], 1))
        rows.append(ribbon)
        base += row_count
    if not rows:
        return None
    vertices = np.concatenate(rows)
    data = np.zeros(len(vertices), [("p", "<f4", 3), ("c", "u1", 4), ("t", "<f4", 2)])
    data["p"] = vertices[:, :3]
    data["c"] = np.stack([np.full(len(vertices), 255), np.full(len(vertices), 255), np.full(len(vertices), 255),
                          np.round(vertices[:, 3] * 255)], 1).astype(np.uint8)  # fmt: skip
    data["t"] = vertices[:, 4:6]
    elements = ((0, 7, 3, 0), (5, 5, 1, 12), (4, 7, 2, 16))
    return CMesh(data.tobytes(), STRIDE, elements, np.concatenate(index).ravel().astype(np.uint32), len(vertices))


def strips(lines: list[SCoastLine]) -> dict[str, CMesh | None]:
    """The flat and the steep coasts' meshes (``strip_mesh``)."""
    steep = [line for line in lines if line.variation == STEEP]
    flat = [line for line in lines if line.variation != STEEP]
    return {"flat": strip_mesh(flat), "steep": strip_mesh(steep)}


class CCoastWaves1701:
    """The wave texture and the two techniques' draws (module docstring)."""

    def __init__(self, ctx, stages: dict, dummies) -> None:
        self.m_ctx, self.m_stages, self.m_dummies = ctx, stages, dummies
        self.m_draws: dict[str, CFxcDraw] = {}
        self.m_texture = self.m_fbo = None

    def lines(self, meshes: dict[str, CMesh | None]) -> None:
        """New strips (the coast lines changed): the draws made again."""
        for fxc_draw in self.m_draws.values():
            fxc_draw.release()
        self.m_draws = {}
        for kind, mesh in meshes.items():
            stage = self.m_stages.get(kind)
            if mesh is not None and stage is not None:
                self.m_draws[kind] = CFxcDraw(
                    self.m_ctx, CProgram(stage.vertex_stage, stage.pixel_stage), mesh, stage.state, self.m_dummies
                )

    def _target(self):
        if self.m_texture is None:
            self.m_texture = self.m_ctx.native_texture((SIDE, SIDE), QRhiTexture.Format.RGBA8)
            self.m_fbo = self.m_ctx.framebuffer([self.m_texture])
        return self.m_texture

    def draw(self, camera: gs.SCamera, seconds: float, maps: dict):
        """The wave texture for the camera ``camera`` (as the sea's position pass reads it) at the clock ``seconds``;
        ``maps``: the coast textures by ``TEXTURES``' keys."""
        target = self._target()
        view_proj = camera.view @ camera.projection
        values = {
            k: {"vcMtxViewProj": view_proj, "vcUsrConstant0": clocks(seconds, k == "steep")} for k in self.m_draws
        }
        textures = {
            k: {slot: maps[map_key] for slot, map_key in BINDINGS[k].items() if map_key in maps} for k in self.m_draws
        }

        def prepare(render_pass, signature, batch) -> None:
            for k, fxc_draw in self.m_draws.items():
                fxc_draw.prepare(
                    render_pass,
                    signature,
                    batch,
                    textures[k],
                    lambda constants, k=k: gs.set_values(constants.values(), values[k]),
                )

        def record(command_buffer) -> None:
            for k in ("flat", "steep"):
                if k in self.m_draws:
                    self.m_draws[k].draw(command_buffer, (SIDE, SIDE))

        draw_pass(self.m_ctx, self.m_fbo, prepare, record, color=(GREY, GREY, GREY, GREY))
        return target

    def release(self) -> None:
        for fxc_draw in self.m_draws.values():
            fxc_draw.release()
        self.m_draws = {}
        for texture in (self.m_fbo, self.m_texture):
            if texture is not None:
                texture.release()
        self.m_texture = self.m_fbo = None
