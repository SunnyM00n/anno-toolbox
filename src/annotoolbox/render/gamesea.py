"""Anno 117's sea by its own ocean shader (``water_ocean.fx``): a grid around the eye drawn in the transparent pass,
reading the island's water maps (``WaterInfos``, ``WaterFlow``), the coast's signed distance made from its heights,
the ocean's FFT, the scene's copies and the coast waves and foam of the physical waves (``water_physical_waves.fx``).

``Sea(rhi, stages, water, waves)`` is the entry (``compile_stages``: the compiled shaders and the ambient's ``Water``
values); ``set_island(infos, heights, size, flow)`` hands it the island, ``simulate`` steps the coast waves,
``model(camera)`` is the frame's draw at the view's clock (``seconds``). docs/rendering/island-117-view.md "Phase 6",
steps 2 and 3.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture, QRhiTextureCopyDescription

from . import gamedraw, renderstates
from .gamecompute import ComputeChain, ComputeStage, groups, record
from .gamesky import block, block_number, block_vector

EFFECT = "data/shaders/fx/water_ocean.fx"
STAGES = {"vs": (EFFECT, "PatchInner", "vsOcean"), "ps": (EFFECT, "PatchInner", "psOcean")}
WAVES = ("data/shaders/fx/water_physical_waves.fx", "tPhysicalWavesSimulation", "csPhysicalWaves")
PHYSICAL = ("PHYSICAL_WAVES_ENABLED", "")  # the ocean's shipped variant that reads the physical waves
WAVES_TEXELS, WAVES_CELL, WAVES_GROUP = 512, 0.5, 8  # the map's texels a side, world units a texel [ours]; its groups
GRID, SNAP = 161, 16.0  # the grid's points a side, the step its centre keeps to (a moving grid swims) [ours]
DEEP = (255, 0, 0, 0)  # the open sea's WaterInfos: deep, no coast waves, no beach, no foam
NO_FLOW = (127, 127, 255, 0)  # the open sea's WaterFlow: no flow, far from the coast
OPEN_SEA, NO_SCIPY = 100.0, 50.0  # the coast's distance past the island's square, and at sea without scipy [ours]
REFLECTION_SHADOW = 0.5  # cWaterShadowReflectionIntensity: the engine's [E], ours
# what the sea reads that the island view has none of: black (no moving foam without the coast waves, planar
# reflection, local colour, undiscovered land, gusts, no building that damps the waves)
BLACK = ("cOceanFoamMap", "cReflectionColorMap", "cLocalWaterColorTex", "cUndiscoveredMask", "cWindTex",
         "cPropBlurredVisibilityTex")  # fmt: skip
LENT = ("cWaterInfoMapRG", "cWaterInfoMapBA", "cOceanFoamMap", "cPhysicalWavesMap0")  # what inland water reads too
VECTORS = {"WaterColorDeep": (0.0588, 0.1882, 0.2863), "WaterColorShallow": (0.7569, 0.949, 1.0),
           "ScatterColor": (0.1294, 0.3373, 0.4)}  # fmt: skip
NUMBERS = {"ScatterStrength": 0.18, "AbsorptionShallow": 0.6, "ShallowWaterDepth": 0.31, "AbsorptionSmoothness": 4.0,
           "ViewDepth": 9.0, "OffshoreDistance": 2.0, "OffshoreTransitionDist": 50.0, "CausticsIntensity": 0.351}  # fmt: skip


def compile_stages(source) -> tuple:
    """(the ocean's shaders, the ambient's water values, the physical waves' compute shader): the ocean's variant
    that reads the waves with their simulation, else the plain ocean and None (no coast waves)."""
    water = read_water(source.ambient_text())
    try:
        switches = (*[s for s in source.shipped_switches(EFFECT) if s[0] != PHYSICAL[0]], PHYSICAL)
        stages = {k: source.pass_stages(EFFECT, switches, f"{tech}/{fn}")[0] for k, (_e, tech, fn) in STAGES.items()}
        return stages, water, source.pass_stage(*WAVES)
    except Exception:  # the sea of step 2 all the same
        return source.stage_set(STAGES), water, None


def read_water(text: str) -> dict:
    """The sea's constants by name from an ambient file's ``Water`` block (Roman's where it lacks one)."""
    water = block(text, "Water")
    out = {f"c{k}": block_vector(water, k, d) for k, d in VECTORS.items()}
    out.update({f"c{k}": block_number(water, k, d) for k, d in NUMBERS.items()})
    return out


def grid(n: int = GRID) -> gamedraw.Mesh:
    """The sea's grid (``OceanVertex``: float2) over -1..1 a side, its points at ``u |u|`` (denser at the middle)."""
    u = np.linspace(-1.0, 1.0, n)
    u = u * np.abs(u)
    x, z = np.meshgrid(u, u)
    data = np.stack([x.ravel(), z.ravel()], -1).astype("<f4")
    k = np.arange((n - 1) * (n - 1))
    c = (k // (n - 1)) * n + k % (n - 1)
    indices = np.stack([c, c + 1, c + n, c + 1, c + n + 1, c + n], -1).astype(np.uint32).ravel()
    return gamedraw.Mesh(data.tobytes(), 8, ((0, 7, 2, 0),), indices, n * n)


def info_maps(raw: bytes) -> tuple[np.ndarray, np.ndarray]:
    """The island's ``WaterInfos`` (RGBA bytes, square, rows along z) as the two maps the sea reads (bytes 0-1 and
    2-3), x turned over into the view's mirrored axes, the outer ring open sea (``DEEP``)."""
    cells = _cells(raw, DEEP)
    return np.ascontiguousarray(cells[..., :2]), np.ascontiguousarray(cells[..., 2:])


def flow_map(raw: bytes) -> np.ndarray:
    """The island's ``WaterFlow`` (RGBA bytes, square: flow x, z, the distance from the coast) as the sea and the
    waves read it, x turned over as ``info_maps``, the outer ring open sea (``NO_FLOW``); no flow where it has none."""
    if not raw:
        return np.full((2, 2, 4), NO_FLOW, np.uint8)
    cells = _cells(raw, NO_FLOW)
    cells[..., 0] = 254 - np.minimum(cells[..., 0], 254)  # the flow's x turned over with the axis (127: none)
    return cells


def _cells(raw: bytes, ring: tuple) -> np.ndarray:
    """A square map of RGBA bytes (rows along z), x turned over into the view's mirrored axes, its outer ring
    ``ring``: the maps are read clamped, and past the island's square the sea is open [ours]."""
    n = math.isqrt(len(raw) // 4)  # a map cut short: its whole rows
    if n < 2:  # no room for the ring: open sea
        return np.full((2, 2, 4), ring, np.uint8)
    cells = np.frombuffer(raw[: n * n * 4], np.uint8).reshape(n, n, 4)[:, ::-1].copy()
    cells[[0, -1], :], cells[:, [0, -1]] = ring, ring
    return cells


def coast_distance(heights: np.ndarray, n: int, size: float) -> np.ndarray:
    """The coast's signed distance (world units, positive at sea) at ``n`` x ``n`` cells over the island's
    ``heights`` (rows along z), x turned over as ``info_maps``, the outer ring ``OPEN_SEA``."""
    at = np.minimum(((np.arange(n) + 0.5) * len(heights) / n).astype(int), len(heights) - 1)
    sea = np.asarray(heights)[np.ix_(at, at)][:, ::-1] < 0.0
    try:
        from scipy.ndimage import distance_transform_edt
    except ImportError:  # the light install: at sea or not
        out = np.where(sea, NO_SCIPY, -NO_SCIPY).astype(np.float32)
    else:
        cell = size / n
        out = ((distance_transform_edt(sea) - distance_transform_edt(~sea)) * cell).astype(np.float32)
    out[[0, -1], :], out[:, [0, -1]] = OPEN_SEA, OPEN_SEA
    return out


def place(eye, radius: float) -> np.ndarray:
    """``cMtxOceanLocal`` (row vectors): the grid scaled to ``radius`` around the eye, its centre on ``SNAP``."""
    m = np.diag([radius, 1.0, radius, 1.0])
    m[3, [0, 2]] = np.round(np.asarray(eye, float)[[0, 2]] / SNAP) * SNAP
    return m


class SeaModel(gamedraw.GameModel):
    """The sea's draw: a ``GameModel`` that binds its own maps (``own``) before what the frame hands by name, made
    and uploaded from ``pending`` in its first frame."""

    own: dict
    pending: dict
    lent: dict  # the coast waves' maps by the names the sea reads (``CoastWaves.maps``: theirs to destroy)
    _bound: tuple = ()  # the frame's maps its binding set was made with (held: an id alone can be reused)

    def _texture(self, name: str, shared: dict, white):
        for maps in (self.lent, self.own):
            if name in maps:
                return maps[name]
        return super()._texture(name, shared, white)

    def prepare(self, rp, signature, batch, camera, fill=None, shared: dict | None = None) -> None:
        # the frame's maps are made anew with its size (the scene's copies): their binding set with them
        bound = tuple((shared or {}).get(name) for name, kind in self.program.numbers if kind == "texture")
        changed = len(bound) != len(self._bound) or any(a is not b for a, b in zip(bound, self._bound, strict=False))
        if self.keep and changed:
            self.release()
        self._bound = bound
        for name, (fmt, pixels) in self.pending.items():
            tex = self.own.get(name)  # a map again at its size (the coast after an edit): its binding set holds
            if tex is None or (tex.pixelSize().width(), tex.pixelSize().height()) != pixels.shape[1::-1]:
                if tex is not None:
                    tex.destroy()
                    self.release()
                tex = self.own[name] = self.rhi.newTexture(fmt, QSize(pixels.shape[1], pixels.shape[0]))
                tex.create()
            gamedraw.upload_level(batch, tex, np.ascontiguousarray(pixels).tobytes())
        self.pending = {}
        super().prepare(rp, signature, batch, camera, fill, shared)


def waves_centre(camera, side: float) -> np.ndarray:
    """Where the coast waves' square is centred (world x, z): where the view's ray meets the sea, at most a third of
    ``side`` ahead of the eye (looking up or far out: the eye's own place) [ours]."""
    eye = np.asarray(camera.eye, float)
    ahead = np.linalg.inv(camera.view)[2, :3]  # the engine's view looks along +z
    if ahead[1] >= -1e-6:
        return eye[[0, 2]]
    reach = min(-eye[1] / ahead[1], side / 3 / max(1e-6, float(np.hypot(ahead[0], ahead[2]))))
    return (eye + ahead * max(0.0, reach))[[0, 2]]


def waves_settings(centre, last: np.ndarray | None) -> tuple[np.ndarray, dict]:
    """The square's corner for ``centre`` (on whole texels) and ``cbWaterPhysicalWaves``: the corner and side, the
    texel shift from the ``last`` corner (old texels read at ``id + shift``; -10000: start empty), 1 / the texels."""
    side = WAVES_TEXELS * WAVES_CELL
    corner = np.round((np.asarray(centre, float) - side / 2) / WAVES_CELL) * WAVES_CELL
    shift = (corner - last) / WAVES_CELL if last is not None else np.array([-10000.0, -10000.0])
    inv = 1.0 / WAVES_TEXELS
    return corner, {"cSettings0": [*corner, side, side], "cSettings1": [*np.round(shift), inv, inv]}


class CoastWaves(ComputeChain):
    """The physical waves' simulation (``csPhysicalWaves``) on a square around the camera: one step a frame from the
    copies of the last step's outputs (``maps``, what the sea reads), its place for the sea (``placement``)."""

    def __init__(self, rhi, stage) -> None:
        self.rhi = rhi
        self.stages = {"waves": ComputeStage(rhi, stage)}
        fmt, flag = QRhiTexture.Format, QRhiTexture.Flag
        size = QSize(WAVES_TEXELS, WAVES_TEXELS)
        out = flag.UsedWithLoadStore | flag.UsedAsTransferSource
        self.targets = {"cPhysWavesRT0": rhi.newTexture(fmt.RGBA16F, size, 1, out),
                        "cOceanFoamRT0": rhi.newTexture(fmt.RGBA8, size, 1, out)}  # fmt: skip
        self.maps = {
            "cPhysicalWavesMap0": rhi.newTexture(fmt.RGBA16F, size),
            "cOceanFoamMap": rhi.newTexture(fmt.RGBA8, size),
        }
        for tex in (*self.targets.values(), *self.maps.values()):
            tex.create()
        self.corner: np.ndarray | None = None
        self.placement = [0.0, 0.0, 1.0, 1.0]  # cPhysicalWaves: the corner, 1 / the side

    def run(self, ctx, inputs: dict, fill, camera) -> None:
        """One step for ``camera`` with ``inputs`` (the island's maps, the ocean's, the depth) and ``fill(c)`` (the
        sea's constants)."""
        side = WAVES_TEXELS * WAVES_CELL
        corner, settings = waves_settings(waves_centre(camera, side), self.corner)
        first, self.corner = self.corner is None, corner
        self.placement = [*corner, 1.0 / side, 1.0 / side]
        stage = self.stages["waves"]
        named = {**inputs, "cPhysicalWavesMap": self.maps["cPhysicalWavesMap0"], "cOceanFoamMap": self.maps["cOceanFoamMap"],
                 **self.targets}  # fmt: skip

        def write(c) -> None:
            fill(c)
            for key, value in settings.items():
                c.set(key, value)

        def before(batch) -> None:
            if first:  # what the sea reads before the first copy: still water, no foam
                for name, tex in self.maps.items():
                    zero = bytes(WAVES_TEXELS * WAVES_TEXELS * (8 if name == "cPhysicalWavesMap0" else 4))
                    gamedraw.upload_level(batch, tex, zero)

        def after():
            if stage.error:
                return None
            batch = self.rhi.nextResourceUpdateBatch()
            for src, dst in (("cPhysWavesRT0", "cPhysicalWavesMap0"), ("cOceanFoamRT0", "cOceanFoamMap")):
                batch.copyTexture(self.maps[dst], self.targets[src], QRhiTextureCopyDescription())
            return batch

        n = groups(WAVES_TEXELS, WAVES_GROUP)
        record(ctx, stage, named, write, (n, n, 1), after, before)

    def release(self) -> None:
        super().release()
        for tex in (*self.targets.values(), *self.maps.values()):
            tex.destroy()
        self.targets, self.maps = {}, {}


class Sea:
    """The sea of one island view (module docstring); ``error`` says why it does not draw."""

    def __init__(self, rhi, stages: dict, water: dict, waves=None) -> None:
        self.rhi, self.water, self.error = rhi, water, ""
        self.waves = CoastWaves(rhi, waves) if waves is not None else None  # None: no coast waves (step 2's sea)
        self.waves_error = ""  # why the coast waves stopped (the sea draws on without them)
        self.program = gamedraw.Program(stages["vs"], stages["ps"])
        # its stencil test keeps it out of what a cut-out volume marked (CUT_OUT_WATER: the island's frame has stencil)
        state = stages["ps"].state
        self.state = replace(renderstates.pass_state(state), stencil=renderstates.stencil_of(state))
        self.size, self.seconds = 0.0, 0.0  # the island's side, the view's clock
        self._cells = 2  # the water maps' cells a side (the coast's distance has as many)
        self._pending: dict[str, tuple] | None = None  # map name -> (format, pixels) to upload
        self._model: SeaModel | None = None

    def set_island(self, infos: bytes, heights: np.ndarray, size: float, flow: bytes = b"") -> None:
        """The island's water maps (``infos``, ``flow``) and the coast's distance from its heights (made anew: drawn
        from the next frame)."""
        rg, ba = info_maps(infos) if infos else (np.full((2, 2, 2), DEEP[:2], np.uint8), np.zeros((2, 2, 2), np.uint8))
        sdf = coast_distance(heights, len(rg), size)
        fmt = QRhiTexture.Format
        black = np.zeros((4, 4, 4), np.uint8)
        self._pending = {"cWaterInfoMapRG": (fmt.RG8, rg), "cWaterInfoMapBA": (fmt.RG8, ba),
                         "cTerrainSDF": (fmt.R32F, sdf), "cWaterFlowMap": (fmt.RGBA8, flow_map(flow)),
                         **dict.fromkeys(BLACK, (fmt.RGBA8, black))}  # fmt: skip
        self.size, self._cells = float(size), len(rg)
        self._drop_model()

    def set_coast(self, heights: np.ndarray) -> None:
        """The coast's distance again from edited ``heights`` (after ``set_island``): uploaded into its map."""
        if not self.size:
            return
        sdf = coast_distance(heights, self._cells, self.size)
        target = self._model.pending if self._model is not None else self._pending
        if target is not None:
            target["cTerrainSDF"] = (QRhiTexture.Format.R32F, sdf)

    def model(self, camera) -> SeaModel | None:
        """This frame's draw for ``camera`` (``gameshader.Camera``) at ``seconds``; None before the island."""
        if self.error or (self._model is None and self._pending is None):
            return None
        if self._model is None:
            self._model = SeaModel(self.rhi, self.program, grid(), gamedraw.Material({}, {}), state=self.state)
            self._model.own, self._model.pending, self._pending = {}, self._pending, None
            self._model.lent = self.waves.maps if self.waves is not None else {}
        s = float(self.seconds)
        self._model.values = {"cMtxOceanLocal": place(camera.eye, camera.shadow_distance),
                              "cMtxOceanWorld": np.identity(4), "cLocalEye": list(camera.eye), **self.water,
                              "cWaterInfoCoord": [1 / self.size] * 2, "cWaterInfoDebugMode": 0,
                              "cTerrainSize": [self.size, self.size, 1 / self.size, 1 / self.size],
                              "cTerrainHeightTexIsValid": 0, "cTimer": [s, 0.0, s, s], "cWaveTime": s,
                              "cWaterShadowReflectionIntensity": REFLECTION_SHADOW,
                              "cPhysicalWaves": self.waves.placement if self.waves is not None else [0.0, 0.0, 1.0, 1.0]}  # fmt: skip
        return self._model

    def maps(self) -> dict:
        """Its maps the rivers and lakes read too (``LENT``: the water infos, the moving foam and waves) by name;
        empty before its draw made them."""
        m = self._model
        return {k: v for k, v in {**m.own, **m.lent}.items() if k in LENT} if m is not None and m.own else {}

    def simulate(self, ctx, inputs: dict, camera, fill) -> None:
        """One step of the coast waves (after ``model`` this frame, before the sea draws): ``inputs`` the ocean's maps,
        the noise and the depth by name, ``fill(c)`` the water's constants; nothing before the island's maps are up."""
        m, waves = self._model, self.waves
        if waves is None or m is None or m.pending or not m.own:
            return
        if waves.error:
            self.waves_error, self.waves = waves.error, None
            waves.release()
            m.lent = {}
            m.release()  # bound again without them
            return

        def write(c) -> None:
            for key, value in m.values.items():
                c.set(key, value)
            fill(c)

        waves.run(ctx, {**inputs, **m.own}, write, camera)

    def take_failure(self, failed: list) -> str:
        """Why its draw failed in the last frame (taken out of ``failed``, the device's (draw, reason) list; its
        pipeline refused counts too), "" when it did not: it does not draw again."""
        mine = [x for x in failed if x[0] is self._model and self._model is not None]
        failed[:] = [x for x in failed if all(x is not y for y in mine)]
        why = mine[0][1] if mine else self._model.error if self._model is not None else ""
        if why:
            self.error = why
            self._drop_model()
        return why

    def _drop_model(self) -> None:
        if self._model is not None:
            for tex in self._model.own.values():
                tex.destroy()
            self._model.release()
        self._model = None

    def release(self) -> None:
        self._drop_model()
        self._pending = None
        if self.waves is not None:
            self.waves.release()
            self.waves = None
