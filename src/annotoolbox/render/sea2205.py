"""Anno 2205's sea by its own shaders: the ordinary waves (``water_ordinary_waves.fx``: the setting's three wave
layers into a height and choppiness map, then its normals and foam) and the sea surface (``water_ocean.fx``
``Water``: chunks of a grid around the eye, lit, refracting the frame's copy of the scene and reflecting its sky).

``compile_stages(source)``, ``read_water(text)`` and ``read_maps(files)`` run in the background; ``Sea2205(ctx,
stages, water, maps, dummies)`` is the device side: ``set_session(infos, flow, size)``, then per frame ``run(ctx,
seconds)`` (the waves) and ``model(eye)`` (the frame's blended draws). docs/rendering/island-1800-2205-view.md 15.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from ..mod.modxml import close_tags
from . import frameforward, gamedraw
from . import gameshader as gs
from .fxcdraw import FxcDraw, ScreenChain
from .gamedraw import Program
from .nativepass import draw_pass
from .objects1800 import named_maps, plain_texture

WATER_FX = "data/shaders/fx/water_ocean.fx"
WAVES_FX = "data/shaders/fx/water_ordinary_waves.fx"
WATER_DIR = "data/graphics/effects/water/"
# the maps the shaders read by their names: the files of the install's water folder [I: by name, the engine's
# assignment is not read]
MAPS = {
    "cWavesMap0": "waves_001_0.dds", "cWavesMap1": "waves_002_0.dds", "cWavesMap2": "waves_003_0.dds",
    "cWaterFoamMap": "foam_0.dds", "cWaterFoamRampMap": "foam_ramp_0.dds", "cWaterFoamNoiseMap": "foam_noise_0.dds",
    "cWaterCausticsMap": "caustics.dds", "cNoiseTex": "noise.dds", "cWaterRipplesMap": "ripples.dds",
}  # fmt: skip
# what the view has none of: no planar reflection (the sky's alone), still water, no coast distance [ours]
FALLBACKS = {
    **frameforward.FALLBACKS, "cReflectionColorMap": (0, 0, 0, 0), "cReflectionDepthMap": (0, 0, 0, 0),
    "cWaterFlowMap": (127, 127, 255, 0), "cWaterDistanceMap": (0, 0, 255, 0),
}  # fmt: skip
WAVES_SIDE = 256  # the ordinary waves map's side in texels [E]
WAVES_SPAN = 64.0  # world units one repeat of it covers [E]
GRID = 32  # cells a chunk side (cWaterGridSize) [ours]
CHUNKS = 128  # chunks a draw (constants_water.fxh NUM_WATER_CHUNK_CONSTANTS)
NEAR_CHUNK = 16.0  # the smallest chunk's side in world units [ours]
SPLIT = 2.5  # a chunk is split while the eye is nearer than this many of its sides [ours]
DRIFT = 0.01  # the caustics' and foam's drift in their maps a second [E]


@dataclass(frozen=True)
class Water:
    """A setting's ``Water`` block: the three wave layers, the depths, colours and foam."""

    amplitudes: tuple = (0.24, 0.07, 0.04)
    velocities: tuple = ((5.28, 5.0, -4.02, -4.0), (3.06, 3.06, -3.0, -3.0), (5.0, 5.14, -5.13, -5.13))
    frequencies: tuple = (1.0, 1.0, 2.0)
    choppiness: tuple = (10.0, 11.0, 13.0)
    view_depth: float = 12.7
    view_depth_exponent: float = 0.1
    light_depth: float = 7.8
    color: tuple = (0.094118, 0.176471, 0.254902)
    caustic_color: tuple = (2.16, 3.02, 3.53)
    foam: tuple = (0.18, 0.3, 0.42)  # creation threshold, strength, streaking


class WaterBlock:
    """A local ambient setting's ``Water`` block as both games' readers take it: ``num(tag, default)`` and
    ``vec(tag, lanes, default)``, the default where the block or the value is missing; ``found``: it has one."""

    def __init__(self, text: str) -> None:
        self.block = ET.fromstring(close_tags(text.lstrip(chr(0xFEFF)))).find(".//Water")
        self.found = self.block is not None

    def num(self, tag: str, default: float) -> float:
        return float(self.block.findtext(tag) or default) if self.found else default

    def vec(self, tag: str, lanes: str, default: tuple) -> tuple:
        el = self.block.find(tag) if self.found else None
        return default if el is None else tuple(float(el.findtext(c) or d) for c, d in zip(lanes, default, strict=True))


def read_water(text: str) -> Water:
    """The ``Water`` block of a local ambient setting (the record's defaults where it has none)."""
    w = WaterBlock(text)
    if not w.found:
        return Water()
    num, vec, d = w.num, w.vec, Water()
    return Water(
        tuple(num(f"WaveAmplitudes{i}", d.amplitudes[i]) for i in range(3)),
        tuple(vec(f"WaveVelocities{i}", "xyzw", d.velocities[i]) for i in range(3)),
        tuple(num(f"WaveFrequencies{i}", d.frequencies[i]) for i in range(3)),
        tuple(num(f"WaveChoppiness{i}", d.choppiness[i]) for i in range(3)),
        num("ViewDepth", d.view_depth),
        num("ViewDepthExponent", d.view_depth_exponent),
        num("LightDepth", d.light_depth),
        vec("WaterColor", "xyz", d.color),
        vec("CausticColor", "xyz", d.caustic_color),
        tuple(
            num(t, v)
            for t, v in zip(("FoamCreationThreshold", "FoamCreationStrength", "FoamStreaking"), d.foam, strict=True)
        ),  # fmt: skip
    )


def compile_stages(source) -> dict:
    """In the background: the sea's technique (stages, states) and the ordinary waves' two passes (2205's
    ``ForwardSource``)."""
    return {
        "water": (source.effect_pass(WATER_FX, (), "Water"), source.technique_state(WATER_FX, (), "Water")),
        "position": source.effect_pass(WAVES_FX, (), "tOrdinaryWavesPosition"),
        "normals": source.effect_pass(WAVES_FX, (), "tOrdinaryWavesNormals"),
    }


def read_maps(files) -> dict:
    """In the background: the water maps by the shaders' names (``MAPS``, ``objects1800.named_maps``)."""
    return named_maps(files, MAPS, WATER_DIR)


def view_map(raw: bytes) -> tuple[int, bytes] | None:
    """A square RGBA map of the session (``WaterInfos``, ``WaterFlow``: rows along z) in the view's axes (mirrored in
    x, as the terrain's pages: island-1800-2205-view.md 3); None for an empty or odd one."""
    side = int(round((len(raw) // 4) ** 0.5))
    if not side or side * side * 4 != len(raw):
        return None
    return side, np.ascontiguousarray(np.frombuffer(raw, np.uint8).reshape(side, side, 4)[:, ::-1]).tobytes()


def chunks(eye, size: float, near: float = NEAR_CHUNK, split: float = SPLIT) -> np.ndarray:
    """The sea's chunks over the session's square (``cWaterChunks``: x, z, side, the distance its coarser
    neighbour begins at less its side, where its grid fades to every second vertex): a quadtree split while the eye
    is nearer than ``split`` sides [ours]."""
    ex, ey, ez = (float(v) for v in eye)
    out, todo = [], [(0.0, 0.0, float(size))]
    while todo:
        x, z, s = todo.pop()
        dx, dz = max(x - ex, 0.0, ex - x - s), max(z - ez, 0.0, ez - z - s)
        near_eye = (dx * dx + dz * dz + ey * ey) ** 0.5 < split * s
        if s > near and near_eye:
            h = s / 2
            todo += [(x, z, h), (x + h, z, h), (x, z + h, h), (x + h, z + h, h)]
        else:
            out.append((x, z, s, split * 2 * s - s if s < size else 1e9))
    return np.array(out, np.float32).reshape(-1, 4)


def wave_values(water: Water, seconds: float, step: float, sides=(512, 1024, 1024)) -> dict:
    """The ordinary waves' constants at ``seconds`` (``step``: the seconds since the last run): the setting's layers,
    each moved by its velocities in texels of its map a second [E: the engine's offsets not read]."""
    out: dict = {"cTexelSizeTS": [1 / WAVES_SIDE, 0.0, 0.0, 1 / WAVES_SIDE],
                 "cTexelSizeWS": [WAVES_SPAN / WAVES_SIDE] * 2, "cTime": max(1e-3, step),
                 "cFoamCreationThreshold": water.foam[0], "cFoamCreationStrength": water.foam[1],
                 "cFoamStreaking": water.foam[2]}  # fmt: skip
    for i in range(3):
        out[f"cWaveAmplitudes[{i}]"] = water.amplitudes[i]
        out[f"cWaveFrequencies[{i}]"] = water.frequencies[i]
        out[f"cWaveChoppiness[{i}]"] = water.choppiness[i]
        out[f"cWaveVelocities[{i}]"] = list(water.velocities[i])
        out[f"cWaveOffsets[{i}]"] = [v * seconds / sides[i] for v in water.velocities[i]]
    return out


def sea_values(water: Water, size: float, seconds: float, sun) -> dict:
    """The sea draw's ``cbWater`` but its chunks: the waves map over the world, the session's maps over its square,
    the setting's depths and colours, the drift of the caustics and foam."""
    drift = seconds * DRIFT
    return {"cOrdinaryWaves": [1 / WAVES_SPAN, 1 / WAVES_SPAN, 0.5 / WAVES_SIDE, 0.5 / WAVES_SIDE],
            "cWaterInfoCoord": [1 / size, 1 / size], "cWaterViewDepth": water.view_depth,
            "cWaterViewDepthExponent": water.view_depth_exponent, "cWaterLightDepth": water.light_depth,
            "cWaterColor": list(water.color), "cWaterCausticColor": list(water.caustic_color),
            "cWaterLightDirection": list(sun), "cWaterGridSize": float(GRID),
            "cWaterCausticsOffset": [drift, drift * 0.7, -drift * 0.8, drift * 0.6],
            "cWaterFoamOffset": [drift * 0.5, -drift * 0.4, drift * 0.3, drift * 0.45]}  # fmt: skip


def grid_mesh(n: int = GRID) -> gamedraw.Mesh:
    """A chunk's grid: (n + 1)^2 float2 corners over 0..1, two triangles a cell turning clockwise with x right and z
    up the picture: the faces the technique's culling keeps, seen from above in the view's axes (tried on the
    device)."""
    k = np.arange(n + 1, dtype=np.float32) / n
    xs, zs = np.meshgrid(k, k)
    vertices = np.stack([xs.ravel(), zs.ravel()], axis=1).astype("<f4")
    i = np.arange(n)
    a = (i[:, None] * (n + 1) + i[None, :]).ravel()  # row z, column x
    b, c, d = a + 1, a + n + 1, a + n + 2
    indices = np.stack([a, c, b, b, c, d], axis=1).ravel().astype(np.uint32)
    return gamedraw.Mesh(vertices.tobytes(), 8, ((0, 7, 2, 0),), indices, len(vertices))


class SeaDraw:
    """Up to ``CHUNKS`` chunks of the sea as a model of the forward frame's blended pass (it reads the scene's
    copies: ``water``), its ``cbWater`` written each frame."""

    blended, water, shadow, prepass, ssr = True, True, None, None, None

    def __init__(self, draw: FxcDraw, sea: Sea2205) -> None:
        self.draw_call, self.sea, self.later = draw, sea, []
        self.rows = np.zeros((0, 4), np.float32)
        self.world = np.identity(4)

    def prepare(self, rp, signature, batch, cam: gs.Camera, light: dict, extra: dict | None = None) -> None:
        sea, rows = self.sea, self.rows

        def fill(c: dict) -> None:
            frameforward.fill_frame(c, cam, light)
            water = c.get("cbWater")
            if water is not None:
                gs.set_values([water], sea.values)
                water.set_elements("cWaterChunks", rows)

        self.draw_call.instances = len(rows)
        self.draw_call.prepare(rp, signature, batch, {**sea.textures, **(extra or {})}, fill)

    def draw(self, cb, size) -> None:
        if len(self.rows):
            self.draw_call.draw(cb, size)

    def corners(self) -> np.ndarray:
        lo, hi = self.rows[:, :2].min(0), self.rows[:, :2].max(0) + self.rows[:, 2].max()
        return np.array([[x, 0.0, z] for x in (lo[0], hi[0]) for z in (lo[1], hi[1])])

    def release(self) -> None:
        """Nothing: its draw is the sea's."""


class Sea2205:
    """The sea on the device (module docstring); ``shared``: the frame's buffers of the colour pass (the look's)."""

    def __init__(self, ctx, stages: dict, water: Water, maps: dict, dummies, shared=None) -> None:
        self.ctx, self.water, self.dummies, self.maps = ctx, water, dummies, maps
        self.waves = ScreenChain(ctx, {"even": Program(*stages["position"]), "odd": Program(*stages["position"]),
                                       "normals": Program(*stages["normals"])}, dummies)  # fmt: skip
        self.waves.ensure_targets((WAVES_SIDE, WAVES_SIDE), {"even": 1, "odd": 1, "normals": 1})
        stages_, state = stages["water"]
        self.program, self.state, self.mesh = Program(*stages_), state, grid_mesh()
        self.draws: list[SeaDraw] = []
        self.shared = shared
        self.textures: dict = {}  # the maps, the session's and the waves' by the shaders' names
        self.values: dict = {}
        self.size = 0.0
        self.session: tuple | None = None  # (infos, flow) as handed, uploaded with the next run
        self.turn, self.last = 0, None  # the waves' ping-pong and the last run's seconds
        self.made: list = []

    def set_session(self, infos: bytes, flow: bytes, size: float) -> None:
        """The session's ``WaterInfos`` and ``WaterFlow`` maps (raw RGBA, its axes) over its ``size`` world units."""
        self.session, self.size = (infos, flow), float(size)

    def _uploads(self, batch) -> None:
        for name, dds in self.maps.items():
            self.textures[name] = tex = plain_texture(self.ctx.rhi, batch, dds)
            self.made.append(tex)
        self.maps = {}
        for name, raw in zip(("cWaterInfoMap", "cWaterFlowMap"), self.session or (), strict=False):
            found = view_map(raw)
            if found is None:
                continue
            side, data = found
            tex = self.ctx.rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(side, side))
            tex.create()
            gamedraw.upload_level(batch, tex, data)
            self.textures[name] = tex
            self.made.append(tex)
        self.session = None

    def run(self, ctx, seconds: float) -> None:
        """The maps onto the device once, then the ordinary waves at ``seconds``: the heights from the last frame's
        (their foam and speed), then their normals."""
        if self.maps or self.session is not None:  # a batch no pass takes is dropped: handed on
            ctx.native_pass(lambda cb, batch: (self._uploads(batch), cb.resourceUpdate(batch)))
        step = 1 / 30 if self.last is None else max(1e-3, seconds - self.last)
        self.last = seconds
        values = wave_values(self.water, seconds, step)
        new, old = ("even", "odd") if self.turn % 2 == 0 else ("odd", "even")
        if self.turn == 0:  # still water to start from: the target holds what its memory held, and the foam runs on
            draw_pass(ctx, self.waves.targets[old][1], lambda rp, signature, batch: None, lambda cb: None)
        self.turn += 1
        maps = {k: self.textures[k] for k in ("cWavesMap0", "cWavesMap1", "cWavesMap2") if k in self.textures}
        heights = self.waves.run(new, {**maps, "cOrdinaryWavesMap": self.waves.targets[old][0].native}, values).native
        normals = self.waves.run("normals", {"cWavesMap3": heights}, values).native
        self.textures["cOrdinaryWavesMap0"], self.textures["cOrdinaryWavesMap1"] = heights, normals
        self.values = sea_values(self.water, self.size or 1.0, seconds, (0.0, -1.0, 0.0))

    def frame_values(self) -> dict:
        """What the sea puts into the frame's own constants: nothing (2205's water values are its ``cbWater``)."""
        return {}

    def models(self, eye, sun=None) -> list[SeaDraw]:
        """This frame's draws of the chunks around ``eye`` (the view's axes), ``CHUNKS`` a draw."""
        if sun is not None:
            self.values["cWaterLightDirection"] = list(sun)
        rows = chunks(eye, self.size or 1.0)
        out = []
        for k, a in enumerate(range(0, len(rows), CHUNKS)):
            while len(self.draws) <= k:
                draw = FxcDraw(self.ctx, self.program, self.mesh, self.state, self.dummies)
                draw.mirrored, draw.fallbacks, draw.shared = False, dict(FALLBACKS), self.shared
                self.draws.append(SeaDraw(draw, self))
            d = self.draws[k]
            d.rows = rows[a : a + CHUNKS]
            d.world[:3, 3] = (self.size / 2, 0.0, self.size / 2)
            out.append(d)
        return out

    def release(self) -> None:
        for d in self.draws:
            d.draw_call.release()
        self.waves.release()
        for tex in self.made:
            self.ctx.later(tex)
        self.draws, self.made, self.textures = [], [], {}
