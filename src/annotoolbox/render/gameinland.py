"""Anno 117's rivers and lakes by its own inland water shader (``water_inland_water.fx``): a river a ribbon of the
engine's ``InlandWaterVertex`` drawn by ``RiverDefault``, a lake its flat outline by ``LakeDefault``, each with its own
look (``cbInlandWater``), the rivers reading their flow map (``water_cluster_flow.fx``) and their wave gradients.

``CInlandWater(rhi, stages, flow, maps, waves, water)`` is the entry (``compile_stages``); ``set_bodies(bodies, size)``
hands it the island's (``SBody``), ``run(ctx, seconds, heights)`` makes the flow map when they changed and the
gradients each frame, ``models()`` are the frame's draws (before the sea). docs/rendering/island-117-view.md "Phase
6", step 4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from ..models import ddsdecode
from ..sources.anno117 import ASSETS_XML_PATH
from . import gamedraw, gameshader, renderstates
from .clothsim import unit_bytes
from .gamecompute import CComputeChain, CComputeStage, copy_into, groups, record
from .gamesea import CSeaModel, read_water
from .gamesky import block, block_number, block_vector
from .nativepass import draw_models

EFFECT = "data/shaders/fx/water_inland_water.fx"
FLOW_EFFECT = "data/shaders/fx/water_cluster_flow.fx"
STAGES = {"river.vs": (EFFECT, "RiverDefault", "vsRiver"), "river.ps": (EFFECT, "RiverDefault", "psRiver"),
          "lake.vs": (EFFECT, "LakeDefault", "vsLake"), "lake.ps": (EFFECT, "LakeDefault", "psLake")}  # fmt: skip
GRADIENTS = (EFFECT, "ComputeRiverGradients", "csComputeRiverGradients")
# RiverPreventOcean: the rivers mark the sea's cut-out bit before it draws, past their faded ends (Egypt only)
# (its vertex shader is RiverDefault's ``vsRiver``, the same function: the effect lists the call once)
PREVENT_OCEAN = {"prevent.ps": (EFFECT, "RiverPreventOcean", "psRiverPreventOcean")}
FLOW = {"vs": (FLOW_EFFECT, "ClusterFlow", "vsClusterFlow"), "ps": (FLOW_EFFECT, "ClusterFlow", "psClusterFlow"),
        "expand": (FLOW_EFFECT, "ExpandFlow", "csExpandFlow"), "dilate": (FLOW_EFFECT, "Dilate", "csDilateFlow")}  # fmt: skip
EGYPT = "EGYPT_INLAND_WATER"  # the second shipped variant: Egypt's three wave sizes
ENGINE = "data/base/graphics/engine/"
# the maps no pass hands, chosen by their names: which files the engine binds is not written anywhere read [I]
MAPS = {"cWaterDuckweedMap": f"{ENGINE}water/duckweed_0.dds", "cWaterDuckweedNormMap": f"{ENGINE}water/duckweed_norm_0.dds",
        "cPerlinNoiseTex": f"{ENGINE}noise/perlin_noise_and_gradients.dds"}  # fmt: skip
WAVES = f"{ENGINE}water/waves_small_y.dds"  # cWavesElevation: a volume of 48 slices, a 20 s loop
# black where nothing is handed: no rain ripples, all discovered, still air, no coarse displacement or vegetation;
# the sea's maps (water infos, its moving foam and waves) when it does not draw: open sea
BLACK = ("cWaterRippleBumpMap", "cUndiscoveredMask", "cWindTex", "cTerrainCoarseDisplTex", "cTerrainVegetateMask",
         "cWaterInfoMapRG", "cWaterInfoMapBA", "cOceanFoamMap", "cPhysicalWavesMap0")  # fmt: skip
RIPPLES = "cWaterRippleBumpMap"  # of them the one volume
# InlandWaterVertex as the view feeds it: position, normal, tangent, bitangent (bytes read .bgra), the coordinate,
# the three flow speeds, the flags (its stored layout is the engine's [E])
VERTEX = np.dtype([("pos", "<f4", 3), ("n", "u1", 4), ("t", "u1", 4), ("b", "u1", 4), ("uv", "<f4", 2),
                   ("speed", "<f4", 3), ("flags", "<u4")])  # fmt: skip
ELEMENTS = ((0, 7, 3, 0), (4, 5, 1, 12), (4, 5, 1, 16), (4, 5, 1, 20), (4, 7, 2, 24), (4, 7, 3, 32), (4, 4, 1, 44))
RIGHT_SHORE, START, END = 1, 2, 4  # IVF_IS_RIGHT_SHORE, IVF_IS_START, IVF_IS_END
MEAN_SPEEDS = (1.65, 2.96, 1.77)  # vanilla rivers' mean u0, for a point without (water.md 1.1) [ours]
WIDTH = 4.0  # a river point's width when the line has none (as the view's own ribbon)
FLOW_TEXELS, FLOW_MAX = 2.0, 2048  # the flow map's texels a world unit and its largest side [ours]
# the look's fields of a River or Lake block -> cbInlandWater; the vanilla template's values (water.md 1.10)
LOOK = {"ColorShallow": "cIWColorShallow", "ColorDeep": "cIWColorDeep", "ScatterColor": "cIWScatterColor",
        "ScatterStrength": "cIWScatterStrength", "WaveIntensity": "cIWWaveIntensity", "ShallowDepth": "cIWShallowDepth",
        "AbsorptionShallow": "cIWAbsorptionShallow", "ViewDepth": "cIWViewDepth",
        "AbsorptionSmoothness": "cIWAbsorptionSmoothness", "MaxFoamDepth": "cIWMaxFoamDepth"}  # fmt: skip
TEMPLATE = {"ColorShallow": (0.847, 0.839, 0.796), "ColorDeep": (0.365, 0.522, 0.831),
            "ScatterColor": (0.278, 1.0, 0.941), "ScatterStrength": 0.18, "WaveIntensity": 1.2, "ShallowDepth": 0.2,
            "AbsorptionShallow": 0.9, "ViewDepth": 8.0, "AbsorptionSmoothness": 0.6, "MaxFoamDepth": 0.1}  # fmt: skip
LAKE_TEMPLATE = {**TEMPLATE, "ShallowDepth": 0.4, "ViewDepth": 4.0}
# what no file holds: the engine's values for a body without the season's look (wave size, speed, the physical waves'
# share 1, Egypt's weights 0 / 1 / 0, no gust); no height offset or flag [ours] (island-117-view.md "Phase 6", step 4)
FIXED = {"cIWWaveSize": 1.0, "cIWWaveSpeed": 1.0, "cIWPhysicalWaveIntensity": 1.0, "cIWLargeWaveWeight": 0.0,
         "cIWMediumWaveWeight": 1.0, "cIWSmallWaveWeight": 0.0, "cIWSmallWaveWindGustBoost": 0.0,
         "cIWHeightOffset": 0.0, "cIWShaderFlags": 0}  # fmt: skip
# a River block's RenderFlags is the shader's cIWFlags (the component's default 3: both ends blend); with
# IWF_USE_GLOBAL_SHADING (Egypt's Nile rivers) an Egypt session takes globals.xml's season look instead of the
# block's (island-117-view.md "Round 12")
RENDER_FLAGS, GLOBAL_SHADING = 3, 0x20
GLOBALS = "data/base/config/engine/globals.xml"
SEASON = "DLC03_InlandWater_{}Season"
# the season record's fields (AmbientInlandWater) and their cbInlandWater names; the colours are vectors
SEASON_FIELDS = {"WaveSize": "cIWWaveSize", "WaveSpeed": "cIWWaveSpeed", "WaveIntensity": "cIWWaveIntensity",
                 "ShallowDepth": "cIWShallowDepth", "AbsorptionShallow": "cIWAbsorptionShallow",
                 "ColorShallow": "cIWColorShallow", "ColorDeep": "cIWColorDeep", "ScatterColor": "cIWScatterColor",
                 "ScatterStrength": "cIWScatterStrength", "ViewDepth": "cIWViewDepth",
                 "AbsorptionSmoothness": "cIWAbsorptionSmoothness", "PhysicalWaveIntensity": "cIWPhysicalWaveIntensity",
                 "LargeWaveWeight": "cIWLargeWaveWeight", "MediumWaveWeight": "cIWMediumWaveWeight",
                 "SmallWaveWeight": "cIWSmallWaveWeight"}  # fmt: skip
COLOURS = ("ColorShallow", "ColorDeep", "ScatterColor")
DRY = 0.0  # the season look's factor: 0 the dry season, 1 the flood (``SSeasonCycle`` moves it)
TextureFormat = QRhiTexture.Format


@dataclass
class SBody:
    """A river or lake of the island: ``kind`` ("River" or "Lake"), its points (the island's axes, world units) and
    widths, per point its flow speeds (left, centre, right shore; None: ``MEAN_SPEEDS``), its look (``LOOK``'s fields
    of its block), its draw order, a lake's triangles (corner indices)."""

    kind: str
    points: list
    widths: list = field(default_factory=list)
    speeds: list = field(default_factory=list)
    look: dict = field(default_factory=dict)
    order: int = 0
    triangles: list = field(default_factory=list)
    flags: int = RENDER_FLAGS  # its block's RenderFlags: cIWFlags


def bodies(lines: list, found: list, triangulate) -> list[SBody]:
    """The ``SBody`` of each river and lake line (the session's: kind, points, widths; they follow the edits), its
    flow speeds and look from ``found`` (``islands.islandview.inland_water`` of the file's, in the same order) while
    its kind and point count are the file's; ``triangulate(outline)`` gives a lake's triangles (x, z)."""
    result = []
    for k, line in enumerate(lines):
        stored = found[k] if k < len(found) else None
        is_same = stored is not None and stored["kind"] == line["kind"] and len(stored["speeds"]) == len(line["points"])
        look = dict(stored["look"]) if is_same else {}
        body = SBody(line["kind"], [list(point) for point in line["points"]], list(line.get("widths", [])),
                    list(stored["speeds"]) if is_same else [], look, int(look.pop("RenderOrder", 0)),
                    flags=int(look.pop("RenderFlags", RENDER_FLAGS)))  # fmt: skip
        if body.kind == "Lake":
            body.triangles = list(triangulate([(point[0], point[2]) for point in body.points]))
        result.append(body)
    return result


def compile_stages(source, is_egypt: bool = False) -> tuple:
    """(the river's and lake's shaders, the flow map's, the maps' DDS files, the waves' volume, the ambient's water
    values, Egypt's season looks or None): the plain variant, Egypt's with ``egypt``."""
    switches = (
        *[switch for switch in source.shipped_switches(EFFECT) if switch[0] != EGYPT],
        *([(EGYPT, "")] if is_egypt else []),
    )
    stages = {
        key: source.pass_stages(EFFECT, switches, f"{technique}/{function}")[0]
        for key, (_e, technique, function) in STAGES.items()
    }
    stages["gradients"] = source.pass_stages(EFFECT, switches, f"{GRADIENTS[1]}/{GRADIENTS[2]}")[0]
    if is_egypt:  # only an Egypt session's rivers keep the sea from under them (island-117-view.md "Round 13")
        stages.update({key: source.pass_stages(EFFECT, switches, f"{technique}/{function}")[0] for key, (_e, technique, function) in
                       PREVENT_OCEAN.items()})  # fmt: skip
    maps = {name: ddsdecode.CDdsFile(source.m_files.read(path)) for name, path in MAPS.items()}
    waves = ddsdecode.CDdsFile(source.m_files.read(WAVES))
    seasons = read_seasons(source.m_files.read(GLOBALS).decode("utf-8", "replace")) if is_egypt else None
    if seasons is not None:
        seasons["cycle"] = read_cycle(source.m_files.read(ASSETS_XML_PATH).decode("utf-8", "replace"))
    return stages, source.stage_set(FLOW), maps, waves, read_water(source.ambient_text()), seasons


@dataclass(frozen=True)
class SSeason:
    """One season of the asset file (template ``SSeason``): flood or not, its length, the rivers' height it brings
    and how long they take to reach it (ms), and whether it moves them at all (``IsRiverHeightAdjusted``)."""

    is_flood: bool
    duration: float
    river_height: float
    change: float
    is_adjusted: bool = True


@dataclass(frozen=True)
class SSeasonCycle:
    """Egypt's seasons as the session runs them (the ``SeasonPool`` of the ``SeasonFeature``): the pool's seasons in
    order from ``start`` (its first one lasting ``first`` ms), again from the first after the last (``is_looped``)."""

    seasons: tuple
    start: int
    first: float
    is_looped: bool = True

    def at(self, elapsed_ms: float) -> tuple[float, float]:
        """(the season look's factor, the rivers' height offset) ``ms`` into the session, by the season manager's
        rule (island-117-view.md "Round 14"): around the switch to a season that moves the rivers, over the half of
        its change time before it to the mean of both heights, over the half after to its own; the factor is the
        height over the pool's highest, within 0 and 1."""
        elapsed_ms, season_count = max(0.0, elapsed_ms), len(self.seasons)
        index, begin, end = self.start, 0.0, self.first
        height = self.seasons[index].river_height  # the session starts at its first season's height [I]
        while sum(season.duration for season in self.seasons) > 0:
            is_last = not self.is_looped and index == season_count - 1
            next_season = self.seasons[(index + 1) % season_count]
            if is_last or not next_season.is_adjusted:
                if is_last or elapsed_ms < end:
                    break
            else:
                half = next_season.change / 2
                start = max(begin, end - half)  # the first half starts once the season has that little left
                mid = (self.seasons[index].river_height + next_season.river_height) / 2
                if elapsed_ms < start:
                    break
                if elapsed_ms < end:
                    height = _towards(height, mid, elapsed_ms - start, half)
                    break
                height = _towards(height, mid, end - start, half)
                if elapsed_ms < end + half:
                    height = _towards(height, next_season.river_height, elapsed_ms - end, half)
                    break
                height = next_season.river_height
            index, begin, end = (index + 1) % season_count, end, end + next_season.duration
        top = max((season.river_height for season in self.seasons if season.is_adjusted), default=0.0)
        top = top if abs(top) >= 1e-6 else 0.0  # the engine sends no factor then [I: 0 here]
        return (min(1.0, max(0.0, height / top)) if top else 0.0), height


def _towards(start: float, target: float, elapsed_ms: float, length_ms: float) -> float:
    """From ``a`` to ``b`` over ``length`` ms, ``elapsed`` into it."""
    return start + (target - start) * (min(1.0, elapsed_ms / length_ms) if length_ms > 0 else 1.0)


def read_cycle(assets: str) -> SSeasonCycle | None:
    """The season pool of the assets' ``SeasonFeature`` that adjusts river heights (Egypt's flooding), None without
    one."""
    feature = re.search(r"<DefaultSeasonPool>(\d+)</DefaultSeasonPool>\s*<IsAdjustingRiverHeights>1<", assets)
    pool = _asset(assets, feature.group(1)) if feature else ""
    order = [int(season_guid) for season_guid in re.findall(r"<Season>(\d+)</Season>", pool)]
    if not order:
        return None
    seasons = []
    for guid in order:
        body = _asset(assets, str(guid))
        numbers = {
            tag: float(text)
            for tag, text in re.findall(r"<(SeasonDuration|RiverHeight|RiverChangeDuration)>([^<]+)<", body)
        }
        seasons.append(SSeason("<SeasonType>FloodSeason<" in body, numbers.get("SeasonDuration", 0.0),
                              numbers.get("RiverHeight", 0.0), numbers.get("RiverChangeDuration", 0.0),
                              "<IsRiverHeightAdjusted>1<" in body))  # fmt: skip
    start = re.search(r"<StartSeason>(\d+)<", pool)
    first = re.search(r"<StartSeasonDuration>([^<]+)<", pool)
    start_index = order.index(int(start.group(1))) if start and int(start.group(1)) in order else 0
    return SSeasonCycle(tuple(seasons), start_index, float(first.group(1)) if first else seasons[start_index].duration,
                       "<LoopAfterLastSeason>1<" in pool)  # fmt: skip


def _asset(assets: str, guid: str) -> str:
    """The text of the asset ``guid`` ("" when there is none)."""
    position = assets.find(f"<GUID>{guid}</GUID>")
    return assets[assets.rfind("<Asset>", 0, position) : assets.find("</Asset>", position)] if position >= 0 else ""


def read_seasons(text: str) -> dict | None:
    """globals.xml's season looks of Egypt's rivers: "Dry" and "Flood" (field -> number or vector), the physical
    waves' share between them and the small waves' gust boost; None without them."""
    seasons: dict = {}
    for name in ("Dry", "Flood"):
        found = block(text, SEASON.format(name))
        if not found:
            return None
        seasons[name] = {
            field_name: block_vector(found, field_name) if field_name in COLOURS else block_number(found, field_name)
            for field_name in SEASON_FIELDS
        }
    seasons["transition"] = block_number(text, "DLC03_InlandWater_TransitionPhysWaveIntensity", 1.0)
    seasons["gust"] = block_number(text, "DLC03_InlandWater_SmallWaveWindGustBoost", 0.0)
    return seasons


def season_values(seasons: dict, factor: float = DRY) -> dict:
    """``cbInlandWater``'s look of a river with the season's look at season ``factor`` (0 dry, 1 flood), as the engine
    blends it: wave size and speed the dry season's, the rest from dry to flood, the physical waves' share through the
    transition's at the middle."""
    dry, flood = seasons["Dry"], seasons["Flood"]
    values = {}
    for key, name in SEASON_FIELDS.items():
        dry_value, flood_value = np.asarray(dry[key], float), np.asarray(flood[key], float)
        value = dry_value if key in ("WaveSize", "WaveSpeed") else dry_value + (flood_value - dry_value) * factor
        values[name] = value.tolist() if value.ndim else float(value)
    edge = abs(2 * factor - 1)
    side = dry if factor < 0.5 else flood
    values["cIWPhysicalWaveIntensity"] = (1 - edge) * seasons["transition"] + edge * side["PhysicalWaveIntensity"]
    return values


def _mirrored(vectors: np.ndarray) -> np.ndarray:
    mirrored = np.array(vectors, float)
    mirrored[..., 0] = -mirrored[..., 0]
    return mirrored


def _vectors(rows: np.ndarray) -> np.ndarray:
    """Unit vectors as the shader reads them back (``UnBias``, then ``.bgra``): x in byte 2, z in byte 0."""
    byte_rows = unit_bytes(rows)
    return np.stack([byte_rows[:, 2], byte_rows[:, 1], byte_rows[:, 0], np.full(len(byte_rows), 255, np.uint8)], -1)


def river_mesh(body: SBody, size_tiles: float) -> gamedraw.CMesh | None:
    """A river's ribbon (two vertices a point: left shore, right shore) in the view's axes (x turned over); None for
    fewer than two points."""
    points = np.asarray(body.points, float).reshape(-1, 3)
    point_count = len(points)
    if point_count < 2:
        return None
    along = np.gradient(points, axis=0)
    flat = along[:, [0, 2]] / np.maximum(np.linalg.norm(along[:, [0, 2]], axis=1, keepdims=True), 1e-6)
    right = np.stack(
        [flat[:, 1], np.zeros(point_count), -flat[:, 0]], -1
    )  # the right hand facing the flow (y up, z ahead)
    widths = [
        body.widths[k] if k < len(body.widths) else (body.widths[-1] if body.widths else WIDTH)
        for k in range(point_count)
    ]
    half = np.asarray(widths, float)[:, None] / 2
    tangent = along / np.maximum(np.linalg.norm(along, axis=1, keepdims=True), 1e-6)
    normal = np.cross(tangent, right)
    normal *= np.where(normal[:, 1:2] < 0, -1.0, 1.0)
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-6)
    distances = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    vertices = np.zeros(2 * point_count, VERTEX)
    for shore, sign in ((0, -1.0), (1, 1.0)):
        shore_slice = slice(shore, None, 2)
        positions = points + sign * right * half
        positions[:, 0] = size_tiles - positions[:, 0]
        vertices["pos"][shore_slice] = positions
        vertices["n"][shore_slice], vertices["t"][shore_slice], vertices["b"][shore_slice] = (
            _vectors(_mirrored(x)) for x in (normal, tangent, right)
        )
        vertices["uv"][shore_slice] = np.stack([distances, np.full(point_count, float(shore))], -1)
        vertices["speed"][shore_slice] = [
            body.speeds[k] if k < len(body.speeds) and body.speeds[k] else MEAN_SPEEDS for k in range(point_count)
        ]
        vertices["flags"][shore_slice] = shore * RIGHT_SHORE
    vertices["flags"][:2] |= START
    vertices["flags"][-2:] |= END
    base = np.arange(point_count - 1) * 2
    indices = np.stack([base, base + 1, base + 2, base + 1, base + 3, base + 2], -1).astype(np.uint32).ravel()
    return gamedraw.CMesh(vertices.tobytes(), VERTEX.itemsize, ELEMENTS, indices, len(vertices))


def lake_mesh(body: SBody, size_tiles: float) -> gamedraw.CMesh | None:
    """A lake's outline triangulated (``body.triangles``) flat at its first point's height, in the view's axes; None
    without triangles."""
    if not body.triangles:
        return None
    points = np.asarray(body.points, float).reshape(-1, 3)
    vertices = np.zeros(len(points), VERTEX)
    vertices["pos"] = np.stack([size_tiles - points[:, 0], np.full(len(points), points[0, 1]), points[:, 2]], -1)
    up, x, z = (
        np.tile(direction, (len(points), 1)) for direction in ([0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0])
    )
    vertices["n"], vertices["t"], vertices["b"] = _vectors(up), _vectors(x), _vectors(z)
    vertices["uv"] = points[:, [0, 2]]
    return gamedraw.CMesh(
        vertices.tobytes(), VERTEX.itemsize, ELEMENTS, np.asarray(body.triangles, np.uint32).ravel(), len(vertices)
    )


def look_values(body: SBody, seasons: dict | None = None, season: tuple = (DRY, 0.0)) -> dict:
    """``cbInlandWater``'s values of a body but the flow map's place: its block over the template's, then ``FIXED``,
    its flags; in an Egypt session (``seasons``: ``read_seasons``) the season's look where its flags ask for it, the
    small waves' gust boost on every body, and the river height the season cycle has reached (``season``: its
    factor and height, ``SSeasonCycle.at``) on every body as the season manager sends it."""
    look = {**(LAKE_TEMPLATE if body.kind == "Lake" else TEMPLATE), **body.look}
    values = {
        **{
            LOOK[field_name]: list(field_value) if isinstance(field_value, tuple) else field_value
            for field_name, field_value in look.items()
            if field_name in LOOK
        },
        **FIXED,
    }
    values["cIWFlags"] = body.flags
    if seasons is not None:
        values["cIWSmallWaveWindGustBoost"] = seasons["gust"]
        if seasons.get("cycle") is not None:
            values["cIWHeightOffset"] = float(season[1])
        if body.flags & GLOBAL_SHADING:
            values.update(season_values(seasons, season[0]))
    return values


def flow_side(size_tiles: float) -> int:
    """The flow map's texels a side for an island of ``size`` world units."""
    return int(min(FLOW_MAX, max(16, round(size_tiles * FLOW_TEXELS))))


def flow_values(size_tiles: float, side: int) -> dict:
    """Where the flow map lies (the island's square from 0) for the cluster's and the rivers' buffers."""
    return {"cClusterFlowOriginWS": [0, 0], "cClusterFlowRecSizeWS": [1 / size_tiles] * 2,
            "cClusterFlowTextureSize": [side, side, 1 / side, 1 / side], "cClusterFlowRecTerrainSize": [1 / size_tiles] * 2}  # fmt: skip


class CInlandModel(CSeaModel):
    """A river's or lake's draw: what the frame hands by name (the sea's maps among them) first, then its own maps."""

    def lookup(self, name: str, shared: dict):
        return shared[name] if name in shared else super().lookup(name, shared)


class CInlandWater(CComputeChain):
    """The island's rivers and lakes (module docstring); ``error`` says why they do not draw."""

    def __init__(self, rhi, stages: dict, flow: dict, maps: dict, waves, water: dict, seasons=None) -> None:
        self.m_rhi, self.m_water, self.m_maps_files, self.m_waves_file = rhi, water, maps, waves
        self.m_seasons = seasons  # Egypt's season looks (``read_seasons``; None: not an Egypt session)
        self.m_river = gamedraw.CProgram(stages["river.vs"], stages["river.ps"])
        self.m_lake = gamedraw.CProgram(stages["lake.vs"], stages["lake.ps"])
        self.m_raster = gamedraw.CProgram(flow["vs"], flow["ps"])
        self.m_states = {"river": renderstates.pass_state(stages["river.ps"].state),
                       "lake": renderstates.pass_state(stages["lake.ps"].state),
                       "raster": replace(renderstates.pass_state(flow["ps"].state, 1), depth_test=False,
                                         depth_write=False)}  # fmt: skip
        self.m_stages = {"gradients": CComputeStage(rhi, stages["gradients"]),
                       "expand": CComputeStage(rhi, flow["expand"]), "dilate": CComputeStage(rhi, flow["dilate"])}  # fmt: skip
        self.m_prevent = None  # RiverPreventOcean's program (an Egypt session's) and its state
        if "prevent.ps" in stages:
            pixel_stage = stages[
                "prevent.ps"
            ]  # sbInlandWaterPreventOcean: the depth test always passes, nothing but the bit
            state = replace(renderstates.pass_state(pixel_stage.state), depth_always=True, depth_write=False, colour=False,
                            blends=(), stencil=renderstates.stencil_of(pixel_stage.state))  # fmt: skip
            self.m_prevent = (gamedraw.CProgram(stages["river.vs"], pixel_stage), state)
        self.m_bodies: list[SBody] = []
        self.m_size_tiles, self.m_seconds = 0.0, 0.0
        self.m_session_ms: float | None = None  # the season cycle's time (None: the view's clock from its start) [ours]
        self._m_season = (DRY, 0.0)  # the season factor and river height the looks hold
        self.m_own: dict = {}  # the maps every draw binds: the flow map, the gradients, the engine's, black
        self._m_targets: list = []  # the flow map's raster target, its expansion, the map, their framebuffer
        self._m_made: list = []  # the textures of ``own`` made here (the targets release themselves)
        self._m_raster: list = []  # the flow map's draws, one a river
        self._m_models: list = []
        self._m_flow_due = False
        self._m_flow_native = False  # the last flow map was made natively (made again if they failed)
        self._m_error = ""
        self.m_native = None  # inlandnative.CNativeInland: the compute passes by the shipped shaders (None: ours)

    @property
    def m_error(self) -> str:
        return self._m_error or super().m_error

    def season(self) -> tuple[float, float]:
        """Egypt's season look factor and river height now (``SSeasonCycle.at``); the dry look without a cycle."""
        cycle = (self.m_seasons or {}).get("cycle")
        if cycle is None:
            return self._m_season
        return cycle.at(self.m_session_ms if self.m_session_ms is not None else self.m_seconds * 1000.0)

    def set_bodies(self, bodies: list[SBody], size_tiles: float) -> None:
        """The island's rivers and lakes (drawn and their flow map made from the next frame)."""
        self.m_bodies, self.m_size_tiles, self._m_flow_due = list(bodies), float(size_tiles), True
        self._drop_models()

    def _maps(self, ctx) -> None:
        """The maps made once: the engine's files, black stand-ins, the gradients' target, the flow map's three;
        their pixels go up in a pass of their own, ahead of the passes that read them."""
        rhi, uploads = self.m_rhi, []
        for name, dds in [*self.m_maps_files.items(), ("cWavesElevation", self.m_waves_file)]:
            self.m_own[name], description = gamedraw.dds_texture(rhi, dds)
            uploads.append((self.m_own[name], description))
        self.m_maps_files, self.m_waves_file = {}, None  # on the device once that pass ran
        # 2D maps but the ripples' volume: the overload with a depth makes a 3D texture, which bound 3D views where
        # the shaders read 2D ones (the debug layer, 2026-11-19)
        black = rhi.newTexture(TextureFormat.RGBA8, QSize(4, 4), 1, QRhiTexture.Flag(0))
        volume = rhi.newTexture(TextureFormat.RGBA8, 4, 4, 1, 1, QRhiTexture.Flag(0))
        for texture in (black, volume):
            texture.create()
        self.m_own.update({**dict.fromkeys(BLACK, black), RIPPLES: volume})
        size_px = self.m_own["cWavesElevation"].pixelSize()
        gradients = rhi.newTexture(TextureFormat.RGBA16F, size_px, 1, QRhiTexture.Flag.UsedWithLoadStore)
        gradients.create()
        self.m_own["cRiverGradientsRO"] = gradients
        self._m_made = [*(texture for texture, _description in uploads), black, volume, gradients]
        side = flow_side(self.m_size_tiles)
        raster = ctx.native_texture((side, side), TextureFormat.RGBA8)
        expanded, flow = (ctx.native_texture((side, side), TextureFormat.RGBA8, storage=True) for _ in range(2))
        self._m_targets = [raster, expanded, flow, ctx.framebuffer([raster])]
        self.m_own["cInlandWaterClusterFlowTex"] = flow.native

        def upload(
            command_buffer, batch
        ) -> None:  # an empty compute pass hands the batch on (one no pass takes is dropped)
            for texture, description in uploads:
                batch.uploadTexture(texture, description)
            for texture in (black, volume):
                gamedraw.upload_level(batch, texture, bytes(64))
            command_buffer.beginComputePass(batch)
            command_buffer.endComputePass()

        ctx.native_pass(upload)

    def run(self, ctx, seconds: float, heights=None) -> None:
        """Before the frame's passes: the maps once, the flow map when the bodies changed (``heights``: the
        session's coarse height texture, None: none), the gradients at ``seconds``."""
        self.m_seconds = seconds
        if not self.m_size_tiles or self.m_error:
            return
        if not self.m_own:
            self._maps(ctx)
        side = flow_side(self.m_size_tiles)
        if self._m_flow_native and self.m_native is not None and self.m_native.m_error:  # it failed: ours, made again
            self._m_flow_due, self._m_flow_native = True, False
        if self._m_flow_due:
            self._m_flow_due = False
            self._make_flow(ctx, side, heights)
        if self._native() is not None:
            self.m_native.gradients(ctx, self.m_own, seconds)
            return
        size_px = self.m_own["cRiverGradientsRO"].pixelSize()
        timer = [seconds, 0.0, seconds, seconds]
        record(ctx, self.m_stages["gradients"], {name: self.m_own[name] for name in ("cWavesElevation", "cRiverGradientsRO")},
               lambda constants: constants.set("cTimer", timer), (groups(size_px.width()), groups(size_px.height()), 1))  # fmt: skip

    def _make_flow(self, ctx, side: int, heights) -> None:
        """The rivers drawn from above into the raster target by ``ClusterFlow``, expanded, copied into the flow map
        and dilated there (the dilation writes only texels without water)."""
        raster, expanded, flow, framebuffer = self._m_targets
        values = flow_values(self.m_size_tiles, side)
        # no session heights: black, read as far below every river (its whole ribbon is water)
        named = {"cFlowTerrainHeightTex": heights if heights is not None else self.m_own["cWaterInfoMapRG"]}
        for draw in self._m_raster:  # the last flow map's draws (their pass has run)
            draw.release()
        self._m_raster = []
        for body in self.m_bodies:
            mesh = river_mesh(body, self.m_size_tiles) if body.kind == "River" else None
            if mesh is not None:
                draw = self._model(self.m_raster, mesh, self.m_states["raster"])
                draw.m_values = values
                self._m_raster.append(draw)
        camera = gameshader.SCamera(
            np.zeros(3), np.identity(4), np.identity(4), 0.1, 1.0, (side, side)
        )  # placed by itself
        if self._native() is not None:  # the shipped ClusterFlow's
            self._m_flow_native = True
            self.m_native.raster(ctx, framebuffer, self._m_raster, named, camera, side)
            self.m_native.flow(ctx, raster, expanded, flow, side)
            return
        self._m_flow_native = False
        draw_models(
            ctx,
            framebuffer,
            self._m_raster,
            lambda draw, render_pass, signature, batch: draw.prepare(
                render_pass, signature, batch, camera, None, named
            ),
            lambda draw, command_buffer: draw.draw(command_buffer, (side, side)),
            targets=1,
            depth=0.0,
            defer=False,
        )
        group_count = groups(side, 16)
        record(ctx, self.m_stages["expand"], {"cExpandFlowSrc": raster.native, "cExpandFlowDst": expanded.native},
               None, (group_count, group_count, 1), lambda: copy_into(self.m_rhi, flow.native, expanded.native, (side, side)))  # fmt: skip
        record(ctx, self.m_stages["dilate"], {"cExpandFlowSrc": expanded.native, "cExpandFlowDst": flow.native}, None,
               (group_count, group_count, 1))  # fmt: skip

    def _native(self):
        """The native compute passes while they work (``inlandnative``), else None."""
        native = self.m_native
        return native if native is not None and not native.m_error else None

    def models(self) -> list:
        """This frame's draws, rivers by their order (each its colour, then its depth), then the lakes; [] before the
        maps are made or when they failed."""
        if self.m_error or not self.m_own or not self.m_size_tiles:
            return []
        if not self._m_models:
            self._m_models = self._make_models()
        season = self.season()
        if season != self._m_season:  # the season cycle moved on: the looks and the height again
            self._m_season = season
            for model in self._m_models:
                model.look = {**model.look, **look_values(model.body, self.m_seasons, season)}
        seconds = self.m_seconds
        frame = {"cTimer": [seconds, 0.0, seconds, seconds], "cWaveTime": seconds, "cTerrainSize": [self.m_size_tiles, self.m_size_tiles, 1 / self.m_size_tiles,
                 1 / self.m_size_tiles], "cWaterInfoCoord": [1 / self.m_size_tiles] * 2, **self.m_water}  # fmt: skip
        for model in self._m_models:
            model.m_values = {**model.look, **frame}
        return list(self._m_models)

    def _make_models(self) -> list:
        flow, ordered, marks = flow_values(self.m_size_tiles, flow_side(self.m_size_tiles)), [], []
        rivers = sorted(
            (candidate for candidate in self.m_bodies if candidate.kind == "River"),
            key=lambda candidate: candidate.order,
        )
        for body in [*rivers, *(candidate for candidate in self.m_bodies if candidate.kind != "River")]:
            is_river = body.kind == "River"
            mesh = river_mesh(body, self.m_size_tiles) if is_river else lake_mesh(body, self.m_size_tiles)
            if mesh is None:
                continue
            state = self.m_states["river" if is_river else "lake"]
            model = self._model(self.m_river if is_river else self.m_lake, mesh, state)
            model.look, model.body = {**look_values(body, self.m_seasons, self._m_season), **flow}, body
            ordered.append(model)
            if is_river:  # its depth after its colour (p1: ``psRiverDepthOnly`` writes nothing but the depth)
                depth = self._model(
                    self.m_river, mesh, replace(state, depth_write=True, colour=False, blends=()), model
                )
                depth.look, depth.body = model.look, body
                ordered.append(depth)
            if is_river and self.m_prevent is not None:  # the sea's bit marked before anything of the water draws
                mark = self._model(self.m_prevent[0], mesh, self.m_prevent[1], model)
                mark.look, mark.body = model.look, body
                marks.append(mark)
        return marks + ordered

    def _model(self, program, mesh, state, share=None) -> CInlandModel:
        model = CInlandModel(self.m_rhi, program, mesh, gamedraw.SMaterial({}, {}), share, state)
        model.m_own, model.m_pending, model.m_lent = self.m_own, {}, {}
        model.m_switches = ((EGYPT, ""),) if self.m_prevent is not None else ()  # Egypt's variant has the sea's mark
        return model

    def take_failure(self, failed: list) -> str:
        """Why one of its draws failed in the last frame (taken out of ``failed``, the device's (draw, reason) list;
        a pipeline refused counts too; the flow map's draws among them), "" when none did: then none draws again."""
        mine = [entry for entry in failed if any(entry[0] is draw for draw in (*self._m_models, *self._m_raster))]
        failed[:] = [entry for entry in failed if all(entry is not other_entry for other_entry in mine)]
        why = mine[0][1] if mine else next((draw.m_error for draw in self._m_models if draw.m_error), "")
        if why:
            self._m_error = why
            self._drop_models()
        return why

    def _drop_models(self) -> None:
        for model in self._m_models:
            model.release()
        self._m_models = []

    def release(self) -> None:
        self._drop_models()
        for model in self._m_raster:
            model.release()
        super().release()
        for texture in self._m_made:
            texture.destroy()
        for target in self._m_targets:
            target.release()
        self.m_own, self._m_targets, self._m_made, self._m_raster = {}, [], [], []
