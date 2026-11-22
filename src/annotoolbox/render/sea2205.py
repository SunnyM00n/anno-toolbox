"""Anno 2205's sea by its own shaders: the ordinary waves (``water_ordinary_waves.fx``: the setting's three wave
layers into a height and choppiness map, then its normals and foam) and the sea surface (``water_ocean.fx``
``SWater``: chunks of a grid around the eye, lit, refracting the frame's copy of the scene and reflecting its sky).

``compile_stages(source)``, ``read_water(text)`` and ``read_maps(files)`` run in the background; ``CSea2205(ctx,
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
from .fxcdraw import CFxcDraw, CScreenChain
from .gamedraw import CProgram
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
class SWater:
    """A setting's ``SWater`` block: the three wave layers, the depths, colours and foam."""

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


class CWaterBlock:
    """A local ambient setting's ``SWater`` block as both games' readers take it: ``num(tag, default)`` and
    ``vec(tag, lanes, default)``, the default where the block or the value is missing; ``found``: it has one."""

    def __init__(self, text: str) -> None:
        self.m_block = ET.fromstring(close_tags(text.lstrip(chr(0xFEFF)))).find(".//Water")
        self.m_found = self.m_block is not None

    def num(self, tag: str, default: float) -> float:
        return float(self.m_block.findtext(tag) or default) if self.m_found else default

    def vec(self, tag: str, lanes: str, default: tuple) -> tuple:
        element = self.m_block.find(tag) if self.m_found else None
        return (
            default
            if element is None
            else tuple(
                float(element.findtext(lane) or lane_default) for lane, lane_default in zip(lanes, default, strict=True)
            )
        )


def read_water(text: str) -> SWater:
    """The ``SWater`` block of a local ambient setting (the record's defaults where it has none)."""
    block = CWaterBlock(text)
    if not block.m_found:
        return SWater()
    num, vec, defaults = block.num, block.vec, SWater()
    return SWater(
        tuple(num(f"WaveAmplitudes{i}", defaults.amplitudes[i]) for i in range(3)),
        tuple(vec(f"WaveVelocities{i}", "xyzw", defaults.velocities[i]) for i in range(3)),
        tuple(num(f"WaveFrequencies{i}", defaults.frequencies[i]) for i in range(3)),
        tuple(num(f"WaveChoppiness{i}", defaults.choppiness[i]) for i in range(3)),
        num("ViewDepth", defaults.view_depth),
        num("ViewDepthExponent", defaults.view_depth_exponent),
        num("LightDepth", defaults.light_depth),
        vec("WaterColor", "xyz", defaults.color),
        vec("CausticColor", "xyz", defaults.caustic_color),
        tuple(
            num(tag, default)
            for tag, default in zip(
                ("FoamCreationThreshold", "FoamCreationStrength", "FoamStreaking"), defaults.foam, strict=True
            )
        ),  # fmt: skip
    )


def compile_stages(source) -> dict:
    """In the background: the sea's technique (stages, states) and the ordinary waves' two passes (2205's
    ``CForwardSource``)."""
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


def chunks(eye, size_tiles: float, near: float = NEAR_CHUNK, split: float = SPLIT) -> np.ndarray:
    """The sea's chunks over the session's square (``cWaterChunks``: x, z, side, the distance its coarser
    neighbour begins at less its side, where its grid fades to every second vertex): a quadtree split while the eye
    is nearer than ``split`` sides [ours]."""
    ex, ey, ez = (float(component) for component in eye)
    out, todo = [], [(0.0, 0.0, float(size_tiles))]
    while todo:
        x, z, side = todo.pop()
        dx, dz = max(x - ex, 0.0, ex - x - side), max(z - ez, 0.0, ez - z - side)
        near_eye = (dx * dx + dz * dz + ey * ey) ** 0.5 < split * side
        if side > near and near_eye:
            half = side / 2
            todo += [(x, z, half), (x + half, z, half), (x, z + half, half), (x + half, z + half, half)]
        else:
            out.append((x, z, side, split * 2 * side - side if side < size_tiles else 1e9))
    return np.array(out, np.float32).reshape(-1, 4)


def wave_values(water: SWater, seconds: float, step: float, sides=(512, 1024, 1024)) -> dict:
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
        out[f"cWaveOffsets[{i}]"] = [velocity * seconds / sides[i] for velocity in water.velocities[i]]
    return out


def sea_values(water: SWater, size_tiles: float, seconds: float, sun) -> dict:
    """The sea draw's ``cbWater`` but its chunks: the waves map over the world, the session's maps over its square,
    the setting's depths and colours, the drift of the caustics and foam."""
    drift = seconds * DRIFT
    return {"cOrdinaryWaves": [1 / WAVES_SPAN, 1 / WAVES_SPAN, 0.5 / WAVES_SIDE, 0.5 / WAVES_SIDE],
            "cWaterInfoCoord": [1 / size_tiles, 1 / size_tiles], "cWaterViewDepth": water.view_depth,
            "cWaterViewDepthExponent": water.view_depth_exponent, "cWaterLightDepth": water.light_depth,
            "cWaterColor": list(water.color), "cWaterCausticColor": list(water.caustic_color),
            "cWaterLightDirection": list(sun), "cWaterGridSize": float(GRID),
            "cWaterCausticsOffset": [drift, drift * 0.7, -drift * 0.8, drift * 0.6],
            "cWaterFoamOffset": [drift * 0.5, -drift * 0.4, drift * 0.3, drift * 0.45]}  # fmt: skip


def grid_mesh(quad_count: int = GRID) -> gamedraw.CMesh:
    """A chunk's grid: (n + 1)^2 float2 corners over 0..1, two triangles a cell turning clockwise with x right and z
    up the picture: the faces the technique's culling keeps, seen from above in the view's axes (tried on the
    device)."""
    corner_coords = np.arange(quad_count + 1, dtype=np.float32) / quad_count
    xs, zs = np.meshgrid(corner_coords, corner_coords)
    vertices = np.stack([xs.ravel(), zs.ravel()], axis=1).astype("<f4")
    i = np.arange(quad_count)
    corner = (i[:, None] * (quad_count + 1) + i[None, :]).ravel()  # row z, column x
    right, below, diagonal = corner + 1, corner + quad_count + 1, corner + quad_count + 2
    indices = np.stack([corner, below, right, right, below, diagonal], axis=1).ravel().astype(np.uint32)
    return gamedraw.CMesh(vertices.tobytes(), 8, ((0, 7, 2, 0),), indices, len(vertices))


class CSeaDraw:
    """Up to ``CHUNKS`` chunks of the sea as a model of the forward frame's blended pass (it reads the scene's
    copies: ``water``), its ``cbWater`` written each frame."""

    m_blended, m_water, m_shadow, m_prepass, m_ssr = True, True, None, None, None

    def __init__(self, draw: CFxcDraw, sea: CSea2205) -> None:
        self.m_draw_call, self.m_sea, self.m_later = draw, sea, []
        self.m_rows = np.zeros((0, 4), np.float32)
        self.m_world = np.identity(4)

    def prepare(
        self, render_pass, signature, batch, camera: gs.SCamera, light: dict, extra: dict | None = None
    ) -> None:
        sea, rows = self.m_sea, self.m_rows

        def fill(buffer: dict) -> None:
            frameforward.fill_frame(buffer, camera, light)
            water = buffer.get("cbWater")
            if water is not None:
                gs.set_values([water], sea.m_values)
                water.set_elements("cWaterChunks", rows)

        self.m_draw_call.m_instances = len(rows)
        self.m_draw_call.prepare(render_pass, signature, batch, {**sea.m_textures, **(extra or {})}, fill)

    def draw(self, command_buffer, size_px) -> None:
        if len(self.m_rows):
            self.m_draw_call.draw(command_buffer, size_px)

    def corners(self) -> np.ndarray:
        lo, hi = self.m_rows[:, :2].min(0), self.m_rows[:, :2].max(0) + self.m_rows[:, 2].max()
        return np.array([[x, 0.0, z] for x in (lo[0], hi[0]) for z in (lo[1], hi[1])])

    def release(self) -> None:
        """Nothing: its draw is the sea's."""


class CSea2205:
    """The sea on the device (module docstring); ``shared``: the frame's buffers of the colour pass (the look's)."""

    def __init__(self, ctx, stages: dict, water: SWater, maps: dict, dummies, shared=None) -> None:
        self.m_ctx, self.m_water, self.m_dummies, self.m_maps = ctx, water, dummies, maps
        self.m_waves = CScreenChain(ctx, {"even": CProgram(*stages["position"]), "odd": CProgram(*stages["position"]),
                                       "normals": CProgram(*stages["normals"])}, dummies)  # fmt: skip
        self.m_waves.ensure_targets((WAVES_SIDE, WAVES_SIDE), {"even": 1, "odd": 1, "normals": 1})
        stages_, state = stages["water"]
        self.m_program, self.m_state, self.m_mesh = CProgram(*stages_), state, grid_mesh()
        self.m_draws: list[CSeaDraw] = []
        self.m_shared = shared
        self.m_textures: dict = {}  # the maps, the session's and the waves' by the shaders' names
        self.m_values: dict = {}
        self.m_size_tiles = 0.0
        self.m_session: tuple | None = None  # (infos, flow) as handed, uploaded with the next run
        self.m_turn, self.m_last = 0, None  # the waves' ping-pong and the last run's seconds
        self.m_made: list = []

    def set_session(self, infos: bytes, flow: bytes, size_tiles: float) -> None:
        """The session's ``WaterInfos`` and ``WaterFlow`` maps (raw RGBA, its axes) over its ``size_tiles`` world units."""
        self.m_session, self.m_size_tiles = (infos, flow), float(size_tiles)

    def _uploads(self, batch) -> None:
        for name, dds in self.m_maps.items():
            self.m_textures[name] = texture = plain_texture(self.m_ctx.m_rhi, batch, dds)
            self.m_made.append(texture)
        self.m_maps = {}
        for name, raw in zip(("cWaterInfoMap", "cWaterFlowMap"), self.m_session or (), strict=False):
            found = view_map(raw)
            if found is None:
                continue
            side, data = found
            texture = self.m_ctx.m_rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(side, side))
            texture.create()
            gamedraw.upload_level(batch, texture, data)
            self.m_textures[name] = texture
            self.m_made.append(texture)
        self.m_session = None

    def run(self, ctx, seconds: float) -> None:
        """The maps onto the device once, then the ordinary waves at ``seconds``: the heights from the last frame's
        (their foam and speed), then their normals."""
        if self.m_maps or self.m_session is not None:  # a batch no pass takes is dropped: handed on
            ctx.native_pass(lambda command_buffer, batch: (self._uploads(batch), command_buffer.resourceUpdate(batch)))
        step = 1 / 30 if self.m_last is None else max(1e-3, seconds - self.m_last)
        self.m_last = seconds
        values = wave_values(self.m_water, seconds, step)
        new, old = ("even", "odd") if self.m_turn % 2 == 0 else ("odd", "even")
        if self.m_turn == 0:  # still water to start from: the target holds what its memory held, and the foam runs on
            draw_pass(
                ctx,
                self.m_waves.m_targets[old][1],
                lambda render_pass, signature, batch: None,
                lambda command_buffer: None,
            )
        self.m_turn += 1
        maps = {
            map_name: self.m_textures[map_name]
            for map_name in ("cWavesMap0", "cWavesMap1", "cWavesMap2")
            if map_name in self.m_textures
        }
        heights = self.m_waves.run(
            new, {**maps, "cOrdinaryWavesMap": self.m_waves.m_targets[old][0].native}, values
        ).native
        normals = self.m_waves.run("normals", {"cWavesMap3": heights}, values).native
        self.m_textures["cOrdinaryWavesMap0"], self.m_textures["cOrdinaryWavesMap1"] = heights, normals
        self.m_values = sea_values(self.m_water, self.m_size_tiles or 1.0, seconds, (0.0, -1.0, 0.0))

    def frame_values(self) -> dict:
        """What the sea puts into the frame's own constants: nothing (2205's water values are its ``cbWater``)."""
        return {}

    def models(self, eye, sun=None) -> list[CSeaDraw]:
        """This frame's draws of the chunks around ``eye`` (the view's axes), ``CHUNKS`` a draw."""
        if sun is not None:
            self.m_values["cWaterLightDirection"] = list(sun)
        rows = chunks(eye, self.m_size_tiles or 1.0)
        out = []
        for k, start in enumerate(range(0, len(rows), CHUNKS)):
            while len(self.m_draws) <= k:
                draw = CFxcDraw(self.m_ctx, self.m_program, self.m_mesh, self.m_state, self.m_dummies)
                draw.m_is_mirrored, draw.m_fallbacks, draw.m_shared = False, dict(FALLBACKS), self.m_shared
                self.m_draws.append(CSeaDraw(draw, self))
            sea_draw = self.m_draws[k]
            sea_draw.m_rows = rows[start : start + CHUNKS]
            sea_draw.m_world[:3, 3] = (self.m_size_tiles / 2, 0.0, self.m_size_tiles / 2)
            out.append(sea_draw)
        return out

    def release(self) -> None:
        for sea_draw in self.m_draws:
            sea_draw.m_draw_call.release()
        self.m_waves.release()
        for texture in self.m_made:
            self.m_ctx.later(texture)
        self.m_draws, self.m_made, self.m_textures = [], [], {}
