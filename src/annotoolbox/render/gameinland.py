"""Anno 117's rivers and lakes by its own inland water shader (``water_inland_water.fx``): a river a ribbon of the
engine's ``InlandWaterVertex`` drawn by ``RiverDefault``, a lake its flat outline by ``LakeDefault``, each with its own
look (``cbInlandWater``), the rivers reading their flow map (``water_cluster_flow.fx``) and their wave gradients.

``InlandWater(rhi, stages, flow, maps, waves, water)`` is the entry (``compile_stages``); ``set_bodies(bodies, size)``
hands it the island's (``Body``), ``run(ctx, seconds, heights)`` makes the flow map when they changed and the
gradients each frame, ``models()`` are the frame's draws (before the sea). docs/rendering/island-117-view.md "Phase
6", step 4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import gamedraw, renderstates
from . import gameshader as gs
from .clothsim import unit_bytes
from .gamecompute import ComputeChain, ComputeStage, copy_into, groups, record
from .gamesea import SeaModel
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
DRY = 0.0  # the season look's factor: 0 the dry season, 1 the flood (``SeasonCycle`` moves it)
F = QRhiTexture.Format


@dataclass
class Body:
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


def bodies(lines: list, found: list, triangulate) -> list[Body]:
    """The ``Body`` of each river and lake line (the session's: kind, points, widths; they follow the edits), its
    flow speeds and look from ``found`` (``islands.islandview.inland_water`` of the file's, in the same order) while
    its kind and point count are the file's; ``triangulate(outline)`` gives a lake's triangles (x, z)."""
    out = []
    for k, line in enumerate(lines):
        file = found[k] if k < len(found) else None
        same = file is not None and file["kind"] == line["kind"] and len(file["speeds"]) == len(line["points"])
        look = dict(file["look"]) if same else {}
        body = Body(line["kind"], [list(p) for p in line["points"]], list(line.get("widths", [])),
                    list(file["speeds"]) if same else [], look, int(look.pop("RenderOrder", 0)),
                    flags=int(look.pop("RenderFlags", RENDER_FLAGS)))  # fmt: skip
        if body.kind == "Lake":
            body.triangles = list(triangulate([(p[0], p[2]) for p in body.points]))
        out.append(body)
    return out


def compile_stages(source, egypt: bool = False) -> tuple:
    """(the river's and lake's shaders, the flow map's, the maps' DDS files, the waves' volume, the ambient's water
    values, Egypt's season looks or None): the plain variant, Egypt's with ``egypt``."""
    from .gamesea import read_water

    switches = (*[s for s in source.shipped_switches(EFFECT) if s[0] != EGYPT], *([(EGYPT, "")] if egypt else []))
    stages = {k: source.pass_stages(EFFECT, switches, f"{tech}/{fn}")[0] for k, (_e, tech, fn) in STAGES.items()}
    stages["gradients"] = source.pass_stages(EFFECT, switches, f"{GRADIENTS[1]}/{GRADIENTS[2]}")[0]
    if egypt:  # only an Egypt session's rivers keep the sea from under them (island-117-view.md "Round 13")
        stages.update({k: source.pass_stages(EFFECT, switches, f"{tech}/{fn}")[0] for k, (_e, tech, fn) in
                       PREVENT_OCEAN.items()})  # fmt: skip
    from ..models import ddsdecode

    maps = {name: ddsdecode.DdsFile(source.files.read(path)) for name, path in MAPS.items()}
    waves = ddsdecode.DdsFile(source.files.read(WAVES))
    seasons = read_seasons(source.files.read(GLOBALS).decode("utf-8", "replace")) if egypt else None
    if seasons is not None:
        from ..sources.anno117 import ASSETS_XML_PATH

        seasons["cycle"] = read_cycle(source.files.read(ASSETS_XML_PATH).decode("utf-8", "replace"))
    return stages, source.stage_set(FLOW), maps, waves, read_water(source.ambient_text()), seasons


@dataclass(frozen=True)
class Season:
    """One season of the asset file (template ``Season``): flood or not, its length, the rivers' height it brings
    and how long they take to reach it (ms), and whether it moves them at all (``IsRiverHeightAdjusted``)."""

    flood: bool
    duration: float
    river_height: float
    change: float
    adjusted: bool = True


@dataclass(frozen=True)
class SeasonCycle:
    """Egypt's seasons as the session runs them (the ``SeasonPool`` of the ``SeasonFeature``): the pool's seasons in
    order from ``start`` (its first one lasting ``first`` ms), again from the first after the last (``loop``)."""

    seasons: tuple
    start: int
    first: float
    loop: bool = True

    def at(self, ms: float) -> tuple[float, float]:
        """(the season look's factor, the rivers' height offset) ``ms`` into the session, by the season manager's
        rule (island-117-view.md "Round 14"): around the switch to a season that moves the rivers, over the half of
        its change time before it to the mean of both heights, over the half after to its own; the factor is the
        height over the pool's highest, within 0 and 1."""
        ms, n = max(0.0, ms), len(self.seasons)
        k, begin, end = self.start, 0.0, self.first
        height = self.seasons[k].river_height  # the session starts at its first season's height [I]
        while sum(s.duration for s in self.seasons) > 0:
            last = not self.loop and k == n - 1
            nxt = self.seasons[(k + 1) % n]
            if last or not nxt.adjusted:
                if last or ms < end:
                    break
            else:
                half = nxt.change / 2
                start = max(begin, end - half)  # the first half starts once the season has that little left
                mid = (self.seasons[k].river_height + nxt.river_height) / 2
                if ms < start:
                    break
                if ms < end:
                    height = _towards(height, mid, ms - start, half)
                    break
                height = _towards(height, mid, end - start, half)
                if ms < end + half:
                    height = _towards(height, nxt.river_height, ms - end, half)
                    break
                height = nxt.river_height
            k, begin, end = (k + 1) % n, end, end + nxt.duration
        top = max((s.river_height for s in self.seasons if s.adjusted), default=0.0)
        top = top if abs(top) >= 1e-6 else 0.0  # the engine sends no factor then [I: 0 here]
        return (min(1.0, max(0.0, height / top)) if top else 0.0), height


def _towards(a: float, b: float, elapsed: float, length: float) -> float:
    """From ``a`` to ``b`` over ``length`` ms, ``elapsed`` into it."""
    return a + (b - a) * (min(1.0, elapsed / length) if length > 0 else 1.0)


def read_cycle(assets: str) -> SeasonCycle | None:
    """The season pool of the assets' ``SeasonFeature`` that adjusts river heights (Egypt's flooding), None without
    one."""
    feature = re.search(r"<DefaultSeasonPool>(\d+)</DefaultSeasonPool>\s*<IsAdjustingRiverHeights>1<", assets)
    pool = _asset(assets, feature.group(1)) if feature else ""
    order = [int(g) for g in re.findall(r"<Season>(\d+)</Season>", pool)]
    if not order:
        return None
    seasons = []
    for guid in order:
        body = _asset(assets, str(guid))
        number = {
            k: float(v) for k, v in re.findall(r"<(SeasonDuration|RiverHeight|RiverChangeDuration)>([^<]+)<", body)
        }
        seasons.append(Season("<SeasonType>FloodSeason<" in body, number.get("SeasonDuration", 0.0),
                              number.get("RiverHeight", 0.0), number.get("RiverChangeDuration", 0.0),
                              "<IsRiverHeightAdjusted>1<" in body))  # fmt: skip
    start = re.search(r"<StartSeason>(\d+)<", pool)
    first = re.search(r"<StartSeasonDuration>([^<]+)<", pool)
    k = order.index(int(start.group(1))) if start and int(start.group(1)) in order else 0
    return SeasonCycle(tuple(seasons), k, float(first.group(1)) if first else seasons[k].duration,
                       "<LoopAfterLastSeason>1<" in pool)  # fmt: skip


def _asset(assets: str, guid: str) -> str:
    """The text of the asset ``guid`` ("" when there is none)."""
    at = assets.find(f"<GUID>{guid}</GUID>")
    return assets[assets.rfind("<Asset>", 0, at) : assets.find("</Asset>", at)] if at >= 0 else ""


def read_seasons(text: str) -> dict | None:
    """globals.xml's season looks of Egypt's rivers: "Dry" and "Flood" (field -> number or vector), the physical
    waves' share between them and the small waves' gust boost; None without them."""
    from .gamesky import block, block_number, block_vector

    out: dict = {}
    for name in ("Dry", "Flood"):
        found = block(text, SEASON.format(name))
        if not found:
            return None
        out[name] = {k: block_vector(found, k) if k in COLOURS else block_number(found, k) for k in SEASON_FIELDS}
    out["transition"] = block_number(text, "DLC03_InlandWater_TransitionPhysWaveIntensity", 1.0)
    out["gust"] = block_number(text, "DLC03_InlandWater_SmallWaveWindGustBoost", 0.0)
    return out


def season_values(seasons: dict, f: float = DRY) -> dict:
    """``cbInlandWater``'s look of a river with the season's look at season ``f`` (0 dry, 1 flood), as the engine
    blends it: wave size and speed the dry season's, the rest from dry to flood, the physical waves' share through the
    transition's at the middle."""
    dry, flood = seasons["Dry"], seasons["Flood"]
    out = {}
    for key, name in SEASON_FIELDS.items():
        a, b = np.asarray(dry[key], float), np.asarray(flood[key], float)
        value = a if key in ("WaveSize", "WaveSpeed") else a + (b - a) * f
        out[name] = value.tolist() if value.ndim else float(value)
    edge = abs(2 * f - 1)
    side = dry if f < 0.5 else flood
    out["cIWPhysicalWaveIntensity"] = (1 - edge) * seasons["transition"] + edge * side["PhysicalWaveIntensity"]
    return out


def _mirrored(v: np.ndarray) -> np.ndarray:
    out = np.array(v, float)
    out[..., 0] = -out[..., 0]
    return out


def _vectors(rows: np.ndarray) -> np.ndarray:
    """Unit vectors as the shader reads them back (``UnBias``, then ``.bgra``): x in byte 2, z in byte 0."""
    b = unit_bytes(rows)
    return np.stack([b[:, 2], b[:, 1], b[:, 0], np.full(len(b), 255, np.uint8)], -1)


def river_mesh(body: Body, size: float) -> gamedraw.Mesh | None:
    """A river's ribbon (two vertices a point: left shore, right shore) in the view's axes (x turned over); None for
    fewer than two points."""
    pts = np.asarray(body.points, float).reshape(-1, 3)
    n = len(pts)
    if n < 2:
        return None
    along = np.gradient(pts, axis=0)
    flat = along[:, [0, 2]] / np.maximum(np.linalg.norm(along[:, [0, 2]], axis=1, keepdims=True), 1e-6)
    right = np.stack([flat[:, 1], np.zeros(n), -flat[:, 0]], -1)  # the right hand facing the flow (y up, z ahead)
    widths = [body.widths[k] if k < len(body.widths) else (body.widths[-1] if body.widths else WIDTH) for k in range(n)]
    half = np.asarray(widths, float)[:, None] / 2
    tangent = along / np.maximum(np.linalg.norm(along, axis=1, keepdims=True), 1e-6)
    normal = np.cross(tangent, right)
    normal *= np.where(normal[:, 1:2] < 0, -1.0, 1.0)
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-6)
    dist = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
    v = np.zeros(2 * n, VERTEX)
    for shore, sign in ((0, -1.0), (1, 1.0)):
        s = slice(shore, None, 2)
        pos = pts + sign * right * half
        pos[:, 0] = size - pos[:, 0]
        v["pos"][s] = pos
        v["n"][s], v["t"][s], v["b"][s] = (_vectors(_mirrored(x)) for x in (normal, tangent, right))
        v["uv"][s] = np.stack([dist, np.full(n, float(shore))], -1)
        v["speed"][s] = [body.speeds[k] if k < len(body.speeds) and body.speeds[k] else MEAN_SPEEDS for k in range(n)]
        v["flags"][s] = shore * RIGHT_SHORE
    v["flags"][:2] |= START
    v["flags"][-2:] |= END
    k = np.arange(n - 1) * 2
    indices = np.stack([k, k + 1, k + 2, k + 1, k + 3, k + 2], -1).astype(np.uint32).ravel()
    return gamedraw.Mesh(v.tobytes(), VERTEX.itemsize, ELEMENTS, indices, len(v))


def lake_mesh(body: Body, size: float) -> gamedraw.Mesh | None:
    """A lake's outline triangulated (``body.triangles``) flat at its first point's height, in the view's axes; None
    without triangles."""
    if not body.triangles:
        return None
    pts = np.asarray(body.points, float).reshape(-1, 3)
    v = np.zeros(len(pts), VERTEX)
    v["pos"] = np.stack([size - pts[:, 0], np.full(len(pts), pts[0, 1]), pts[:, 2]], -1)
    up, x, z = (np.tile(a, (len(pts), 1)) for a in ([0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]))
    v["n"], v["t"], v["b"] = _vectors(up), _vectors(x), _vectors(z)
    v["uv"] = pts[:, [0, 2]]
    return gamedraw.Mesh(v.tobytes(), VERTEX.itemsize, ELEMENTS, np.asarray(body.triangles, np.uint32).ravel(), len(v))


def look_values(body: Body, seasons: dict | None = None, season: tuple = (DRY, 0.0)) -> dict:
    """``cbInlandWater``'s values of a body but the flow map's place: its block over the template's, then ``FIXED``,
    its flags; in an Egypt session (``seasons``: ``read_seasons``) the season's look where its flags ask for it, the
    small waves' gust boost on every body, and the river height the season cycle has reached (``season``: its
    factor and height, ``SeasonCycle.at``) on every body as the season manager sends it."""
    look = {**(LAKE_TEMPLATE if body.kind == "Lake" else TEMPLATE), **body.look}
    out = {**{LOOK[k]: list(v) if isinstance(v, tuple) else v for k, v in look.items() if k in LOOK}, **FIXED}
    out["cIWFlags"] = body.flags
    if seasons is not None:
        out["cIWSmallWaveWindGustBoost"] = seasons["gust"]
        if seasons.get("cycle") is not None:
            out["cIWHeightOffset"] = float(season[1])
        if body.flags & GLOBAL_SHADING:
            out.update(season_values(seasons, season[0]))
    return out


def flow_side(size: float) -> int:
    """The flow map's texels a side for an island of ``size`` world units."""
    return int(min(FLOW_MAX, max(16, round(size * FLOW_TEXELS))))


def flow_values(size: float, side: int) -> dict:
    """Where the flow map lies (the island's square from 0) for the cluster's and the rivers' buffers."""
    return {"cClusterFlowOriginWS": [0, 0], "cClusterFlowRecSizeWS": [1 / size] * 2,
            "cClusterFlowTextureSize": [side, side, 1 / side, 1 / side], "cClusterFlowRecTerrainSize": [1 / size] * 2}  # fmt: skip


class InlandModel(SeaModel):
    """A river's or lake's draw: what the frame hands by name (the sea's maps among them) first, then its own maps."""

    def _texture(self, name: str, shared: dict, white):
        if name in shared:
            return shared[name]
        return super()._texture(name, shared, white)


class InlandWater(ComputeChain):
    """The island's rivers and lakes (module docstring); ``error`` says why they do not draw."""

    def __init__(self, rhi, stages: dict, flow: dict, maps: dict, waves, water: dict, seasons=None) -> None:
        self.rhi, self.water, self.maps_files, self.waves_file = rhi, water, maps, waves
        self.seasons = seasons  # Egypt's season looks (``read_seasons``; None: not an Egypt session)
        self.river = gamedraw.Program(stages["river.vs"], stages["river.ps"])
        self.lake = gamedraw.Program(stages["lake.vs"], stages["lake.ps"])
        self.raster = gamedraw.Program(flow["vs"], flow["ps"])
        self.states = {"river": renderstates.pass_state(stages["river.ps"].state),
                       "lake": renderstates.pass_state(stages["lake.ps"].state),
                       "raster": replace(renderstates.pass_state(flow["ps"].state, 1), depth_test=False,
                                         depth_write=False)}  # fmt: skip
        self.stages = {"gradients": ComputeStage(rhi, stages["gradients"]),
                       "expand": ComputeStage(rhi, flow["expand"]), "dilate": ComputeStage(rhi, flow["dilate"])}  # fmt: skip
        self.prevent = None  # RiverPreventOcean's program (an Egypt session's) and its state
        if "prevent.ps" in stages:
            ps = stages["prevent.ps"]  # sbInlandWaterPreventOcean: the depth test always passes, nothing but the bit
            state = replace(renderstates.pass_state(ps.state), depth_always=True, depth_write=False, colour=False,
                            blends=(), stencil=renderstates.stencil_of(ps.state))  # fmt: skip
            self.prevent = (gamedraw.Program(stages["river.vs"], ps), state)
        self.bodies: list[Body] = []
        self.size, self.seconds = 0.0, 0.0
        self.session_ms: float | None = None  # the season cycle's time (None: the view's clock from its start) [ours]
        self._season = (DRY, 0.0)  # the season factor and river height the looks hold
        self.own: dict = {}  # the maps every draw binds: the flow map, the gradients, the engine's, black
        self._targets: list = []  # the flow map's raster target, its expansion, the map, their framebuffer
        self._made: list = []  # the textures of ``own`` made here (the targets release themselves)
        self._raster: list = []  # the flow map's draws, one a river
        self._models: list = []
        self._flow_due = False
        self._error = ""

    @property
    def error(self) -> str:
        return self._error or super().error

    def season(self) -> tuple[float, float]:
        """Egypt's season look factor and river height now (``SeasonCycle.at``); the dry look without a cycle."""
        cycle = (self.seasons or {}).get("cycle")
        if cycle is None:
            return self._season
        return cycle.at(self.session_ms if self.session_ms is not None else self.seconds * 1000.0)

    def set_bodies(self, bodies: list[Body], size: float) -> None:
        """The island's rivers and lakes (drawn and their flow map made from the next frame)."""
        self.bodies, self.size, self._flow_due = list(bodies), float(size), True
        self._drop_models()

    def _maps(self, ctx) -> None:
        """The maps made once: the engine's files, black stand-ins, the gradients' target, the flow map's three;
        their pixels go up in a pass of their own, ahead of the passes that read them."""
        rhi, uploads = self.rhi, []
        for name, dds in [*self.maps_files.items(), ("cWavesElevation", self.waves_file)]:
            self.own[name], desc = gamedraw.dds_texture(rhi, dds)
            uploads.append((self.own[name], desc))
        self.maps_files, self.waves_file = {}, None  # on the device once that pass ran
        black = rhi.newTexture(F.RGBA8, 4, 4, 1, 1, QRhiTexture.Flag(0))
        black.create()
        self.own.update(dict.fromkeys(BLACK, black))
        size = self.own["cWavesElevation"].pixelSize()
        grad = rhi.newTexture(F.RGBA16F, size.width(), size.height(), 1, 1, QRhiTexture.Flag.UsedWithLoadStore)
        grad.create()
        self.own["cRiverGradientsRO"] = grad
        self._made = [*(t for t, _d in uploads), black, grad]
        side = flow_side(self.size)
        raster = ctx.native_texture((side, side), F.RGBA8)
        expanded, flow = (ctx.native_texture((side, side), F.RGBA8, storage=True) for _ in range(2))
        self._targets = [raster, expanded, flow, ctx.framebuffer([raster])]
        self.own["cInlandWaterClusterFlowTex"] = flow.native

        def upload(cb, batch) -> None:  # an empty compute pass hands the batch on (one no pass takes is dropped)
            for tex, desc in uploads:
                batch.uploadTexture(tex, desc)
            gamedraw.upload_level(batch, black, bytes(64))
            cb.beginComputePass(batch)
            cb.endComputePass()

        ctx.native_pass(upload)

    def run(self, ctx, seconds: float, heights=None) -> None:
        """Before the frame's passes: the maps once, the flow map when the bodies changed (``heights``: the
        session's coarse height texture, None: none), the gradients at ``seconds``."""
        self.seconds = seconds
        if not self.size or self.error:
            return
        if not self.own:
            self._maps(ctx)
        side = flow_side(self.size)
        if self._flow_due:
            self._flow_due = False
            self._make_flow(ctx, side, heights)
        size = self.own["cRiverGradientsRO"].pixelSize()
        timer = [seconds, 0.0, seconds, seconds]
        record(ctx, self.stages["gradients"], {k: self.own[k] for k in ("cWavesElevation", "cRiverGradientsRO")},
               lambda c: c.set("cTimer", timer), (groups(size.width()), groups(size.height()), 1))  # fmt: skip

    def _make_flow(self, ctx, side: int, heights) -> None:
        """The rivers drawn from above into the raster target by ``ClusterFlow``, expanded, copied into the flow map
        and dilated there (the dilation writes only texels without water)."""
        raster, expanded, flow, fbo = self._targets
        values = flow_values(self.size, side)
        # no session heights: black, read as far below every river (its whole ribbon is water)
        named = {"cFlowTerrainHeightTex": heights if heights is not None else self.own["cWaterInfoMapRG"]}
        for m in self._raster:  # the last flow map's draws (their pass has run)
            m.release()
        self._raster = []
        for body in self.bodies:
            mesh = river_mesh(body, self.size) if body.kind == "River" else None
            if mesh is not None:
                m = self._model(self.raster, mesh, self.states["raster"])
                m.values = values
                self._raster.append(m)
        camera = gs.Camera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 1.0, (side, side))  # placed by itself
        draw_models(ctx, fbo, self._raster, lambda m, rp, sig, batch: m.prepare(rp, sig, batch, camera, None, named),
                    lambda m, cb: m.draw(cb, (side, side)), targets=1, depth=0.0)  # fmt: skip
        n = groups(side, 16)
        record(ctx, self.stages["expand"], {"cExpandFlowSrc": raster.native, "cExpandFlowDst": expanded.native},
               None, (n, n, 1), lambda: copy_into(self.rhi, flow.native, expanded.native, (side, side)))  # fmt: skip
        record(ctx, self.stages["dilate"], {"cExpandFlowSrc": expanded.native, "cExpandFlowDst": flow.native}, None,
               (n, n, 1))  # fmt: skip

    def models(self) -> list:
        """This frame's draws, rivers by their order (each its colour, then its depth), then the lakes; [] before the
        maps are made or when they failed."""
        if self.error or not self.own or not self.size:
            return []
        if not self._models:
            self._models = self._make_models()
        season = self.season()
        if season != self._season:  # the season cycle moved on: the looks and the height again
            self._season = season
            for m in self._models:
                m.look = {**m.look, **look_values(m.body, self.seasons, season)}
        s = self.seconds
        frame = {"cTimer": [s, 0.0, s, s], "cWaveTime": s, "cTerrainSize": [self.size, self.size, 1 / self.size,
                 1 / self.size], "cWaterInfoCoord": [1 / self.size] * 2, **self.water}  # fmt: skip
        for m in self._models:
            m.values = {**m.look, **frame}
        return list(self._models)

    def _make_models(self) -> list:
        flow, out, first = flow_values(self.size, flow_side(self.size)), [], []
        rivers = sorted((b for b in self.bodies if b.kind == "River"), key=lambda b: b.order)
        for body in [*rivers, *(b for b in self.bodies if b.kind != "River")]:
            river = body.kind == "River"
            mesh = river_mesh(body, self.size) if river else lake_mesh(body, self.size)
            if mesh is None:
                continue
            state = self.states["river" if river else "lake"]
            m = self._model(self.river if river else self.lake, mesh, state)
            m.look, m.body = {**look_values(body, self.seasons, self._season), **flow}, body
            out.append(m)
            if river:  # its depth after its colour (p1: ``psRiverDepthOnly`` writes nothing but the depth)
                depth = self._model(self.river, mesh, replace(state, depth_write=True, colour=False, blends=()), m)
                depth.look, depth.body = m.look, body
                out.append(depth)
            if river and self.prevent is not None:  # the sea's bit marked before anything of the water draws
                mark = self._model(self.prevent[0], mesh, self.prevent[1], m)
                mark.look, mark.body = m.look, body
                first.append(mark)
        return first + out

    def _model(self, program, mesh, state, share=None) -> InlandModel:
        m = InlandModel(self.rhi, program, mesh, gamedraw.Material({}, {}), share, state)
        m.own, m.pending, m.lent = self.own, {}, {}
        return m

    def take_failure(self, failed: list) -> str:
        """Why one of its draws failed in the last frame (taken out of ``failed``, the device's (draw, reason) list;
        a pipeline refused counts too; the flow map's draws among them), "" when none did: then none draws again."""
        mine = [x for x in failed if any(x[0] is m for m in (*self._models, *self._raster))]
        failed[:] = [x for x in failed if all(x is not y for y in mine)]
        why = mine[0][1] if mine else next((m.error for m in self._models if m.error), "")
        if why:
            self._error = why
            self._drop_models()
        return why

    def _drop_models(self) -> None:
        for m in self._models:
            m.release()
        self._models = []

    def release(self) -> None:
        self._drop_models()
        for m in self._raster:
            m.release()
        super().release()
        for tex in self._made:
            tex.destroy()
        for t in self._targets:
            t.release()
        self.own, self._targets, self._made, self._raster = {}, [], [], []
