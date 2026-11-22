"""Anno 2070's coast waves as its water renderer runs them: a height field of 512 x 512 texels over 128 units round
what the camera sees, stepped at most 30 times a second by ``waterwaves.fx``'s ``DynamicWaves`` pass (the wave
equation, waves born along every coast and where objects cross the water) and ``ChoppyWaves`` pass (the sideways push
and the foam), which the ocean reads with ``WATER_DYNAMIC_WAVES_ENABLED``.

``CCoastWaves(ctx, stages, dummies, coast, volume)`` is the entry: ``step(seconds, camera, maps, fill)`` moves its area
as the engine does (``area``) and runs the passes when a step is due; ``ocean_maps`` and
``ocean_values`` are what the ocean binds. docs/rendering/shader-compile.md section 10, "The coast waves".
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .fxcdraw import CFxcDraw
from .gamedraw import CProgram, screen_triangle
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


def area(eye, ahead, view_proj: np.ndarray, size_m: float = MAP_SIZE * TEXEL) -> tuple[float, float]:
    """The corner (x, z) of the area the waves are simulated over, by the engine's rule: with the eye at most
    ``LOW_EYE`` high or the screen's top rays flatter than ``FLAT_VIEW``, a square ``AHEAD`` of its side ahead of the
    eye; else centred on where the four corner rays cross the band of ``BAND`` round the water. ``view_proj``: ours
    (row vectors, depth 0 near and 1 far); ``ahead`` the camera's forward direction."""
    eye, ahead = np.asarray(eye, float), np.asarray(ahead, float)
    inv = np.linalg.inv(np.asarray(view_proj, float))
    far = [np.array([x, y, 1.0, 1.0]) @ inv for x in (-1.0, 1.0) for y in (-1.0, 1.0)]
    rays = [corner[:3] / corner[3] - eye for corner in far]
    top = max(ray[1] / max(np.linalg.norm(ray), 1e-9) for ray in rays)
    if eye[1] <= LOW_EYE or top >= FLAT_VIEW:
        return snap(eye[0] + ahead[0] * size_m * AHEAD - size_m / 2), snap(
            eye[2] + ahead[2] * size_m * AHEAD - size_m / 2
        )
    hits = [eye + ray * ((y - eye[1]) / ray[1]) for ray in rays if ray[1] != 0.0 for y in (-BAND, BAND)]
    hits_x, hits_z = [hit[0] for hit in hits], [hit[2] for hit in hits]
    return snap((min(hits_x) + max(hits_x)) / 2 - size_m / 2), snap((min(hits_z) + max(hits_z)) / 2 - size_m / 2)


def recovery(strength: float, moved: float, frame_ms: float) -> float:
    """The waves' strength after a step (``cWaterDynamicWave2.x``): it drops while the camera moves (``moved`` units
    since the last step) and grows back with the frame's milliseconds."""
    recovered = min(strength, max(1.0 - min(moved * (2 / 3), 1.0), 0.1))
    return min(recovered + (frame_ms * 1e-4) ** (1.0 - recovered * 0.75), 1.0)


class CCoastWaves:
    """The waves' maps and their two passes (module docstring). ``index`` is the map the ocean reads (the engine's
    ping-pong comes back to it each step); ``origin`` the area's corner, None before the first step."""

    def __init__(self, ctx, stages: dict, dummies, coast, volume) -> None:
        self.m_ctx, self.m_coast, self.m_volume = ctx, coast, volume
        triangle = screen_triangle()
        self.m_draws = {}
        for name in ("waves", "choppy"):
            stage = stages[name]
            self.m_draws[name] = CFxcDraw(
                ctx, CProgram(stage.vertex_stage, stage.pixel_stage), triangle, stage.state, dummies
            )
            self.m_draws[name].fallbacks = {"cTexture2": NO_STRONG_WAVES}
        self.m_maps: list = []
        self.m_index = 0
        self.m_origin: tuple[float, float] | None = None
        self.m_size_m = MAP_SIZE * TEXEL
        self.m_timer, self.m_strength = 0.0, 1.0
        self.m_last: float | None = None  # the clock of the last step
        self.m_eye: np.ndarray | None = None  # the camera's eye at the last step

    def _targets(self) -> None:
        if self.m_maps:
            return
        ctx, size, texture_format = self.m_ctx, (MAP_SIZE, MAP_SIZE), QRhiTexture.Format.RGBA16F
        waves = [ctx.native_texture(size, texture_format) for _ in range(2)]
        choppy = ctx.native_texture(size, texture_format)
        # a step's simulation writes the other map and the foam, its choppy pass the foam's map and this one back
        fbos = {("waves", k): ctx.framebuffer([waves[1 - k], choppy]) for k in (0, 1)}
        fbos |= {("choppy", k): ctx.framebuffer([choppy, waves[k]]) for k in (0, 1)}
        self.m_maps = [waves, choppy, fbos]
        self._clear()

    def _clear(self) -> None:
        """The three maps flat (the two simulation passes' targets hold all three)."""
        for k in (0, 1):
            draw_pass(
                self.m_ctx,
                self.m_maps[2][("waves", k)],
                lambda render_pass, signature, batch: None,
                lambda command_buffer: None,
                targets=2,
            )

    def due(self, seconds: float) -> bool:
        return self.m_last is None or seconds - self.m_last > STEP

    def restart(self) -> None:
        """Flat water again, as a world starts."""
        self.m_last, self.m_origin, self.m_eye = None, None, None
        self.m_timer, self.m_strength = 0.0, 1.0
        if self.m_maps:
            self._clear()

    def step(self, seconds: float, camera: tuple, maps: dict, fill) -> None:
        """One step at the clock ``seconds`` if due: the area moved for ``camera`` (eye, forward direction and our
        view-projection: ``area``; the old map shifted under the new one), then the two passes (``maps``: the depth
        copy as ``cTexture1``, the info map ``cTexture3``, the wave position map ``cTexture4``; ``fill(c)``: the
        camera's and the ocean's values)."""
        if not self.due(seconds):
            return
        elapsed = STEP if self.m_last is None else seconds - self.m_last
        self.m_last = seconds
        self._targets()
        waves, choppy, fbos = self.m_maps
        eye = np.asarray(camera[0], float)
        corner = area(eye, camera[1], camera[2], self.m_size_m)
        old = self.m_origin or corner
        shift = ((corner[0] - old[0]) / self.m_size_m, (corner[1] - old[1]) / self.m_size_m)
        moved = float(np.linalg.norm(eye - self.m_eye)) if self.m_eye is not None else 0.0
        self.m_origin, self.m_eye = corner, eye
        self.m_timer = (self.m_timer + min(elapsed, TIMER_STEP)) % TIMER_WRAP
        self.m_strength = recovery(self.m_strength, moved, elapsed * 1000.0)
        values = {"cWaterDynamicWave0": [*corner, self.m_size_m, self.m_size_m],
                  "cWaterDynamicWave1": [*shift, 1 / MAP_SIZE, 1 / MAP_SIZE],
                  "cWaterDynamicWave2": [self.m_strength, 1.0 if moved > FAR_MOVE else 0.0, self.m_timer, 0.0]}  # fmt: skip

        def fill_all(constants: dict) -> None:
            fill(constants)
            gs.set_values(constants.values(), values)

        k = self.m_index
        shared = {"cTexture5": self.m_volume, "cTexture6": self.m_coast, **maps}
        for name, source in (("waves", waves[k]), ("choppy", waves[1 - k])):
            draw = self.m_draws[name]
            textures = {**shared, "cTexture0": source.native}
            draw_pass(
                self.m_ctx,
                fbos[(name, k)],
                lambda render_pass, signature, batch, draw_call=draw, frame_textures=textures: draw_call.prepare(
                    render_pass, signature, batch, frame_textures, fill_all
                ),
                lambda command_buffer, draw_call=draw: draw_call.draw(command_buffer, (MAP_SIZE, MAP_SIZE)),
                targets=2,
                clear=False,
            )

    def ocean_maps(self) -> dict:
        """The maps the ocean reads: the height field with its push (``cTexture8``) and the normal and foam
        (``cTexture9``); none before the first step."""
        if not self.m_maps:
            return {}
        waves, choppy, _ = self.m_maps
        return {"cTexture8": waves[self.m_index].native, "cTexture9": choppy.native}

    def ocean_values(self) -> dict:
        """``cUserConstant0``: the area's corner and the inverse of its side."""
        x, z = self.m_origin or (0.0, 0.0)
        return {"cUserConstant0": [x, z, 1 / self.m_size_m, 1 / self.m_size_m]}

    def release(self) -> None:
        for draw in self.m_draws.values():
            draw.release()
        if self.m_maps:
            waves, choppy, fbos = self.m_maps
            for target_texture in (*fbos.values(), *waves, choppy):
                target_texture.release()
        self.m_maps = []
