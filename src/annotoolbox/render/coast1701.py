"""Anno 1701's coast waves as its terrain draws them: each frame the island's coast lines are drawn as strips into
the terrain's wave texture (screen space, cleared to grey) by ``coastline.fx``'s flat and steep dynamic-wave
techniques, a scrolling wave height the sea's position pass (``VW_DYNAMICWAVES``) adds to the water.

``CoastWaves1701(ctx, stages, dummies)`` is the draw: ``lines(strips(...))`` takes the strips the engine builds
from the coast lines, ``draw(cam, seconds, maps)`` fills the wave texture and returns it. World units throughout;
docs/rendering/shader-compile.md section 13, "The coast and wake waves".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .fxcdraw import FxcDraw
from .gamedraw import Mesh, Program
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
        return [_frac(s * seconds) for s in FLAT_SPEEDS]
    rate, amount = STEEP_SWAY
    return [*(_frac(s * seconds) for s in STEEP_SPEEDS), _frac(amount * math.sin(rate * seconds))]


def offsets(points: np.ndarray, widths: np.ndarray) -> np.ndarray:
    """Each point's step across the strip, ``width x (-dz, 0, dx) / |d|``: ``d`` the segment at an end, the sum of
    the two unit segments (in 3D) at a point between; a point without a direction gets none."""
    p = np.asarray(points, float)
    seg = p[1:] - p[:-1]
    d = np.empty((len(p), 3))
    d[0], d[-1] = seg[0], seg[-1]
    if len(p) > 2:
        lengths = np.linalg.norm(seg, axis=1, keepdims=True)
        unit = seg / np.where(lengths > 0, lengths, 1.0)
        d[1:-1] = unit[1:] + unit[:-1]
    across = np.hypot(d[:, 0], d[:, 2])
    scale = np.where(across > 0, np.asarray(widths, float) / np.where(across > 0, across, 1.0), 0.0)
    return np.stack([-d[:, 2] * scale, np.zeros(len(p)), d[:, 0] * scale], 1)


def strip(points, widths) -> np.ndarray:
    """The engine's strip of a coast line (points (n, 3) and widths in world units): per point the vertex off the
    line (``point - offset``, v 0) and the one on it (v 1), both lifted ``LIFT``; u the running length times
    ``U_SCALE``, each step the mean of the two edges as the engine measures them (its off-line edge from the last
    point plus its offset, the height difference plus the lift); alpha 0 at both ends of a line of three points or
    more. Rows (x, y, z, alpha, u, v)."""
    p = np.asarray(points, float)
    off = offsets(p, widths)
    lift = np.array([0.0, LIFT, 0.0])
    a, b = p - off + lift, p + lift
    u = np.zeros(len(p))
    if len(p) > 1:
        edge_a = np.linalg.norm(a[1:] - (p[:-1] + off[:-1]), axis=1)
        edge_b = np.linalg.norm(b[1:] - p[:-1], axis=1)
        u[1:] = np.cumsum(0.5 * edge_a + 0.5 * edge_b) * U_SCALE
    alpha = np.ones(len(p))
    if len(p) > 2:
        alpha[[0, -1]] = 0.0
    rows = np.empty((2 * len(p), 6))
    rows[0::2, :3], rows[1::2, :3] = a, b
    rows[:, 3] = np.repeat(alpha, 2)
    rows[:, 4] = np.repeat(u, 2)
    rows[0::2, 5], rows[1::2, 5] = 0.0, 1.0
    return rows


@dataclass(frozen=True)
class CoastLine:
    """A coast line in world units: its points (n, 3), each point's width and its ``m_Variation``."""

    points: np.ndarray
    widths: np.ndarray
    variation: int = 0


def strip_mesh(lines: list[CoastLine]) -> Mesh | None:
    """The strips of ``lines`` as one triangle list (the engine draws each line as a strip); None without a line."""
    rows, index, base = [], [], 0
    for line in lines:
        if len(line.points) < 2:
            continue
        r = strip(line.points, line.widths)
        n = len(r)
        k = np.arange(n - 2)
        index.append(np.stack([base + k, base + k + 1, base + k + 2], 1))
        rows.append(r)
        base += n
    if not rows:
        return None
    v = np.concatenate(rows)
    data = np.zeros(len(v), [("p", "<f4", 3), ("c", "u1", 4), ("t", "<f4", 2)])
    data["p"] = v[:, :3]
    data["c"] = np.stack([np.full(len(v), 255), np.full(len(v), 255), np.full(len(v), 255),
                          np.round(v[:, 3] * 255)], 1).astype(np.uint8)  # fmt: skip
    data["t"] = v[:, 4:6]
    elements = ((0, 7, 3, 0), (5, 5, 1, 12), (4, 7, 2, 16))
    return Mesh(data.tobytes(), STRIDE, elements, np.concatenate(index).ravel().astype(np.uint32), len(v))


def strips(lines: list[CoastLine]) -> dict[str, Mesh | None]:
    """The flat and the steep coasts' meshes (``strip_mesh``)."""
    steep = [line for line in lines if line.variation == STEEP]
    flat = [line for line in lines if line.variation != STEEP]
    return {"flat": strip_mesh(flat), "steep": strip_mesh(steep)}


class CoastWaves1701:
    """The wave texture and the two techniques' draws (module docstring)."""

    def __init__(self, ctx, stages: dict, dummies) -> None:
        self.ctx, self.stages, self.dummies = ctx, stages, dummies
        self.draws: dict[str, FxcDraw] = {}
        self.texture = self.fbo = None

    def lines(self, meshes: dict[str, Mesh | None]) -> None:
        """New strips (the coast lines changed): the draws made again."""
        for d in self.draws.values():
            d.release()
        self.draws = {}
        for kind, mesh in meshes.items():
            stage = self.stages.get(kind)
            if mesh is not None and stage is not None:
                self.draws[kind] = FxcDraw(self.ctx, Program(stage.vs, stage.ps), mesh, stage.state, self.dummies)

    def _target(self):
        if self.texture is None:
            self.texture = self.ctx.native_texture((SIDE, SIDE), QRhiTexture.Format.RGBA8)
            self.fbo = self.ctx.framebuffer([self.texture])
        return self.texture

    def draw(self, cam: gs.Camera, seconds: float, maps: dict):
        """The wave texture for the camera ``cam`` (as the sea's position pass reads it) at the clock ``seconds``;
        ``maps``: the coast textures by ``TEXTURES``' keys."""
        target = self._target()
        view_proj = cam.view @ cam.proj
        values = {k: {"vcMtxViewProj": view_proj, "vcUsrConstant0": clocks(seconds, k == "steep")} for k in self.draws}
        textures = {k: {s: maps[m] for s, m in BINDINGS[k].items() if m in maps} for k in self.draws}

        def prepare(rp, sig, batch) -> None:
            for k, d in self.draws.items():
                d.prepare(rp, sig, batch, textures[k], lambda c, k=k: gs.set_values(c.values(), values[k]))

        def record(cb) -> None:
            for k in ("flat", "steep"):
                if k in self.draws:
                    self.draws[k].draw(cb, (SIDE, SIDE))

        draw_pass(self.ctx, self.fbo, prepare, record, color=(GREY, GREY, GREY, GREY))
        return target

    def release(self) -> None:
        for d in self.draws.values():
            d.release()
        self.draws = {}
        for t in (self.fbo, self.texture):
            if t is not None:
                t.release()
        self.texture = self.fbo = None
