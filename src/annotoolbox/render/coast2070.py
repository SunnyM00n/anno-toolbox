"""Anno 2070's coast waves as its water renderer runs them: a height field of 512 x 512 texels over 128 units round
what the camera sees, stepped at most 30 times a second by ``waterwaves.fx``'s ``DynamicWaves`` pass (the wave
equation, waves born along every coast and where objects cross the water) and ``ChoppyWaves`` pass (the sideways push
and the foam), which the ocean reads with ``WATER_DYNAMIC_WAVES_ENABLED``.

``CoastWaves(ctx, stages, dummies, coast, volume)`` is the entry: ``step(seconds, camera, maps, fill)`` moves its area
as the engine does (``area``) and runs the passes when a step is due; ``ocean_maps`` and
``ocean_values`` are what the ocean binds. docs/rendering/shader-compile.md section 10, "The coast waves".
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .fxcdraw import FxcDraw
from .gamedraw import Program, screen_triangle
from .nativepass import draw_pass

FX = "data/system/shaders/fx/waterwaves.fx"
STAGES = {"waves": (FX, (), "DynamicWaves"), "choppy": (FX, (), "ChoppyWaves")}
COAST = "data/graphics/effects/water/coast.dds"  # the coast noise (cTexture6)
MAP_SIZE = 512  # RT_REAL_DYNAMICWAVE_MAP0/1 and RT_CHOPPY_MAP: 512 x 512, four half floats
TEXEL = 0.25  # world units a texel spans: the area is a quarter of the map's side
STEP = 1 / 30  # the simulation steps at most this often (seconds of the game's clock)
TIMER_STEP = 0.1333333  # the waves' own timer advances at most this much a step
TIMER_WRAP = 3600.0
LOW_EYE, FLAT_VIEW = 5.0, -0.26  # an eye this low, or a top ray this flat: the area lies ahead of the camera
AHEAD = 0.45  # ... this share of its side ahead
BAND = 5.0  # else round the rays' footprint between 5 below and 5 above the water
FAR_MOVE = 48.0  # a camera that jumped farther restarts the waves (cWaterDynamicWave2.y)
# the G-buffer channels whose signs mark an object that makes strong waves (a moving ship): none in the island view
NO_STRONG_WAVES = (128, 129, 129, 255)


def snap(value: float) -> float:
    """``value`` down to the map's texel grid, as the engine floors the area's corner."""
    return math.floor(value / TEXEL) * TEXEL


def area(eye, ahead, view_proj: np.ndarray, size: float = MAP_SIZE * TEXEL) -> tuple[float, float]:
    """The corner (x, z) of the area the waves are simulated over, by the engine's rule: with the eye at most
    ``LOW_EYE`` high or the screen's top rays flatter than ``FLAT_VIEW``, a square ``AHEAD`` of its side ahead of the
    eye; else centred on where the four corner rays cross the band of ``BAND`` round the water. ``view_proj``: ours
    (row vectors, depth 0 near and 1 far); ``ahead`` the camera's forward direction."""
    eye, ahead = np.asarray(eye, float), np.asarray(ahead, float)
    inv = np.linalg.inv(np.asarray(view_proj, float))
    far = [np.array([x, y, 1.0, 1.0]) @ inv for x in (-1.0, 1.0) for y in (-1.0, 1.0)]
    rays = [p[:3] / p[3] - eye for p in far]
    top = max(r[1] / max(np.linalg.norm(r), 1e-9) for r in rays)
    if eye[1] <= LOW_EYE or top >= FLAT_VIEW:
        return snap(eye[0] + ahead[0] * size * AHEAD - size / 2), snap(eye[2] + ahead[2] * size * AHEAD - size / 2)
    hits = [eye + r * ((y - eye[1]) / r[1]) for r in rays if r[1] != 0.0 for y in (-BAND, BAND)]
    xs, zs = [h[0] for h in hits], [h[2] for h in hits]
    return snap((min(xs) + max(xs)) / 2 - size / 2), snap((min(zs) + max(zs)) / 2 - size / 2)


def recovery(strength: float, moved: float, frame_ms: float) -> float:
    """The waves' strength after a step (``cWaterDynamicWave2.x``): it drops while the camera moves (``moved`` units
    since the last step) and grows back with the frame's milliseconds."""
    s = min(strength, max(1.0 - min(moved * (2 / 3), 1.0), 0.1))
    return min(s + (frame_ms * 1e-4) ** (1.0 - s * 0.75), 1.0)


class CoastWaves:
    """The waves' maps and their two passes (module docstring). ``index`` is the map the ocean reads (the engine's
    ping-pong comes back to it each step); ``origin`` the area's corner, None before the first step."""

    def __init__(self, ctx, stages: dict, dummies, coast, volume) -> None:
        self.ctx, self.coast, self.volume = ctx, coast, volume
        tri = screen_triangle()
        self.draws = {}
        for name in ("waves", "choppy"):
            s = stages[name]
            self.draws[name] = FxcDraw(ctx, Program(s.vs, s.ps), tri, s.state, dummies)
            self.draws[name].fallbacks = {"cTexture2": NO_STRONG_WAVES}
        self.maps: list = []
        self.index = 0
        self.origin: tuple[float, float] | None = None
        self.size = MAP_SIZE * TEXEL
        self.timer, self.strength = 0.0, 1.0
        self.last: float | None = None  # the clock of the last step
        self.eye: np.ndarray | None = None  # the camera's eye at the last step

    def _targets(self) -> None:
        if self.maps:
            return
        ctx, size, f = self.ctx, (MAP_SIZE, MAP_SIZE), QRhiTexture.Format.RGBA16F
        waves = [ctx.native_texture(size, f) for _ in range(2)]
        choppy = ctx.native_texture(size, f)
        # a step's simulation writes the other map and the foam, its choppy pass the foam's map and this one back
        fbos = {("waves", k): ctx.framebuffer([waves[1 - k], choppy]) for k in (0, 1)}
        fbos |= {("choppy", k): ctx.framebuffer([choppy, waves[k]]) for k in (0, 1)}
        self.maps = [waves, choppy, fbos]
        self._clear()

    def _clear(self) -> None:
        """The three maps flat (the two simulation passes' targets hold all three)."""
        for k in (0, 1):
            draw_pass(self.ctx, self.maps[2][("waves", k)], lambda rp, sig, batch: None, lambda cb: None, targets=2)

    def due(self, seconds: float) -> bool:
        return self.last is None or seconds - self.last > STEP

    def restart(self) -> None:
        """Flat water again, as a world starts."""
        self.last, self.origin, self.eye = None, None, None
        self.timer, self.strength = 0.0, 1.0
        if self.maps:
            self._clear()

    def step(self, seconds: float, camera: tuple, maps: dict, fill) -> None:
        """One step at the clock ``seconds`` if due: the area moved for ``camera`` (eye, forward direction and our
        view-projection: ``area``; the old map shifted under the new one), then the two passes (``maps``: the depth
        copy as ``cTexture1``, the info map ``cTexture3``, the wave position map ``cTexture4``; ``fill(c)``: the
        camera's and the ocean's values)."""
        if not self.due(seconds):
            return
        elapsed = STEP if self.last is None else seconds - self.last
        self.last = seconds
        self._targets()
        waves, choppy, fbos = self.maps
        eye = np.asarray(camera[0], float)
        corner = area(eye, camera[1], camera[2], self.size)
        old = self.origin or corner
        shift = ((corner[0] - old[0]) / self.size, (corner[1] - old[1]) / self.size)
        moved = float(np.linalg.norm(eye - self.eye)) if self.eye is not None else 0.0
        self.origin, self.eye = corner, eye
        self.timer = (self.timer + min(elapsed, TIMER_STEP)) % TIMER_WRAP
        self.strength = recovery(self.strength, moved, elapsed * 1000.0)
        values = {"cWaterDynamicWave0": [*corner, self.size, self.size],
                  "cWaterDynamicWave1": [*shift, 1 / MAP_SIZE, 1 / MAP_SIZE],
                  "cWaterDynamicWave2": [self.strength, 1.0 if moved > FAR_MOVE else 0.0, self.timer, 0.0]}  # fmt: skip

        def fill_all(c: dict) -> None:
            fill(c)
            gs.set_values(c.values(), values)

        k = self.index
        shared = {"cTexture5": self.volume, "cTexture6": self.coast, **maps}
        for name, source in (("waves", waves[k]), ("choppy", waves[1 - k])):
            draw = self.draws[name]
            textures = {**shared, "cTexture0": source.native}
            draw_pass(self.ctx, fbos[(name, k)],
                      lambda rp, sig, batch, d=draw, t=textures: d.prepare(rp, sig, batch, t, fill_all),
                      lambda cb, d=draw: d.draw(cb, (MAP_SIZE, MAP_SIZE)), targets=2, clear=False)  # fmt: skip

    def ocean_maps(self) -> dict:
        """The maps the ocean reads: the height field with its push (``cTexture8``) and the normal and foam
        (``cTexture9``); none before the first step."""
        if not self.maps:
            return {}
        waves, choppy, _ = self.maps
        return {"cTexture8": waves[self.index].native, "cTexture9": choppy.native}

    def ocean_values(self) -> dict:
        """``cUserConstant0``: the area's corner and the inverse of its side."""
        x, z = self.origin or (0.0, 0.0)
        return {"cUserConstant0": [x, z, 1 / self.size, 1 / self.size]}

    def release(self) -> None:
        for d in self.draws.values():
            d.release()
        if self.maps:
            waves, choppy, fbos = self.maps
            for t in (*fbos.values(), *waves, choppy):
                t.release()
        self.maps = []
