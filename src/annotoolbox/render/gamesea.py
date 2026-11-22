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
from .gamecompute import CComputeChain, CComputeStage, groups, record
from .gamesky import block, block_number, block_vector
from .nativepass import compute_pass

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
        switches = (*[switch for switch in source.shipped_switches(EFFECT) if switch[0] != PHYSICAL[0]], PHYSICAL)
        stages = {
            k: source.pass_stages(EFFECT, switches, f"{technique}/{function_name}")[0]
            for k, (_e, technique, function_name) in STAGES.items()
        }
        return stages, water, source.pass_stage(*WAVES)
    except Exception:  # the sea of step 2 all the same
        return source.stage_set(STAGES), water, None


def read_water(text: str) -> dict:
    """The sea's constants by name from an ambient file's ``Water`` block (Roman's where it lacks one)."""
    water = block(text, "Water")
    out = {f"c{k}": block_vector(water, k, default) for k, default in VECTORS.items()}
    out.update({f"c{k}": block_number(water, k, default) for k, default in NUMBERS.items()})
    return out


def grid(side: int = GRID) -> gamedraw.CMesh:
    """The sea's grid (``OceanVertex``: float2) over -1..1 a side, its points at ``u |u|`` (denser at the middle)."""
    axis = np.linspace(-1.0, 1.0, side)
    axis = axis * np.abs(axis)
    x, z = np.meshgrid(axis, axis)
    data = np.stack([x.ravel(), z.ravel()], -1).astype("<f4")
    k = np.arange((side - 1) * (side - 1))
    corner = (k // (side - 1)) * side + k % (side - 1)
    indices = (
        np.stack([corner, corner + 1, corner + side, corner + 1, corner + side + 1, corner + side], -1)
        .astype(np.uint32)
        .ravel()
    )
    return gamedraw.CMesh(data.tobytes(), 8, ((0, 7, 2, 0),), indices, side * side)


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
    side = math.isqrt(len(raw) // 4)  # a map cut short: its whole rows
    if side < 2:  # no room for the ring: open sea
        return np.full((2, 2, 4), ring, np.uint8)
    cells = np.frombuffer(raw[: side * side * 4], np.uint8).reshape(side, side, 4)[:, ::-1].copy()
    cells[[0, -1], :], cells[:, [0, -1]] = ring, ring
    return cells


def coast_distance(heights: np.ndarray, cell_count: int, size_tiles: float) -> np.ndarray:
    """The coast's signed distance (world units, positive at sea) at ``n`` x ``n`` cells over the island's
    ``heights`` (rows along z), x turned over as ``info_maps``, the outer ring ``OPEN_SEA``."""
    at = np.minimum(((np.arange(cell_count) + 0.5) * len(heights) / cell_count).astype(int), len(heights) - 1)
    sea = np.asarray(heights)[np.ix_(at, at)][:, ::-1] < 0.0
    try:
        from scipy.ndimage import distance_transform_edt
    except ImportError:  # the light install: at sea or not
        out = np.where(sea, NO_SCIPY, -NO_SCIPY).astype(np.float32)
    else:
        cell = size_tiles / cell_count
        out = ((distance_transform_edt(sea) - distance_transform_edt(~sea)) * cell).astype(np.float32)
    out[[0, -1], :], out[:, [0, -1]] = OPEN_SEA, OPEN_SEA
    return out


def coast_for(infos: bytes, heights: np.ndarray, size_tiles: float) -> np.ndarray:
    """The coast's distance ``set_island`` takes for these water maps: on the cells of their info map (a distance
    pass over the island: seconds on a large one, so made where the window's thread does not wait for it)."""
    cells = len(info_maps(infos)[0]) if infos else 2
    return coast_distance(heights, cells, size_tiles)


def place(eye, radius_m: float) -> np.ndarray:
    """``cMtxOceanLocal`` (row vectors): the grid scaled to ``radius`` around the eye, its centre on ``SNAP``."""
    matrix = np.diag([radius_m, 1.0, radius_m, 1.0])
    matrix[3, [0, 2]] = np.round(np.asarray(eye, float)[[0, 2]] / SNAP) * SNAP
    return matrix


class CSeaModel(gamedraw.CGameModel):
    """The sea's draw: a ``CGameModel`` that binds its own maps (``own``) before what the frame hands by name, made
    and uploaded from ``pending`` in its first frame."""

    m_own: dict
    m_pending: dict
    m_lent: dict  # the coast waves' maps by the names the sea reads (``CCoastWaves.maps``: theirs to destroy)
    _m_bound: tuple = ()  # the frame's maps its binding set was made with (held: an id alone can be reused)
    m_switches: tuple = ()  # its variant's material switches (the native backend finds the shipped one by them)

    def lookup(self, name: str, shared: dict):
        """The map it binds as ``name``: its own (the coast waves' first), else the frame's; None for a stand-in."""
        for maps in (self.m_lent, self.m_own):
            if name in maps:
                return maps[name]
        return shared.get(name)

    def _texture(self, name: str, shared: dict, white):
        found = self.lookup(name, shared)
        return found if found is not None else super()._texture(name, shared, white)

    def prepare(self, render_pass, signature, batch, camera, fill=None, shared: dict | None = None) -> None:
        # the frame's maps are made anew with its size (the scene's copies): their binding set with them
        bound = tuple((shared or {}).get(name) for name, kind in self.m_program.m_numbers if kind == "texture")
        changed = len(bound) != len(self._m_bound) or any(
            now is not before for now, before in zip(bound, self._m_bound, strict=False)
        )
        if self.m_keep and changed:
            self.release()
        self._m_bound = bound
        self.upload(batch)
        super().prepare(render_pass, signature, batch, camera, fill, shared)

    def upload(self, batch) -> None:
        """Its own maps made and their pixels uploaded in ``batch``, once (``pending``; the native draw's too)."""
        for name, (texture_format, pixels) in self.m_pending.items():
            texture = self.m_own.get(name)  # a map again at its size (the coast after an edit): its binding set holds
            if texture is None or (texture.pixelSize().width(), texture.pixelSize().height()) != pixels.shape[1::-1]:
                if texture is not None:
                    texture.destroy()
                    self.release()
                texture = self.m_own[name] = self.m_rhi.newTexture(
                    texture_format, QSize(pixels.shape[1], pixels.shape[0])
                )
                texture.create()
            gamedraw.upload_level(batch, texture, np.ascontiguousarray(pixels).tobytes())
        self.m_pending = {}


def waves_centre(camera, side: float) -> np.ndarray:
    """Where the coast waves' square is centred (world x, z): where the view's ray meets the sea, at most a third of
    ``side`` ahead of the eye (looking up or far out: the eye's own place) [ours]."""
    eye = np.asarray(camera.eye, float)
    forward = np.linalg.inv(camera.view)[2, :3]  # the engine's view looks along +z
    if forward[1] >= -1e-6:
        return eye[[0, 2]]
    reach = min(-eye[1] / forward[1], side / 3 / max(1e-6, float(np.hypot(forward[0], forward[2]))))
    return (eye + forward * max(0.0, reach))[[0, 2]]


def waves_settings(centre, last: np.ndarray | None) -> tuple[np.ndarray, dict]:
    """The square's corner for ``centre`` (on whole texels) and ``cbWaterPhysicalWaves``: the corner and side, the
    texel shift from the ``last`` corner (old texels read at ``id + shift``; -10000: start empty), 1 / the texels."""
    side = WAVES_TEXELS * WAVES_CELL
    corner = np.round((np.asarray(centre, float) - side / 2) / WAVES_CELL) * WAVES_CELL
    shift = (corner - last) / WAVES_CELL if last is not None else np.array([-10000.0, -10000.0])
    texel_step = 1.0 / WAVES_TEXELS
    return corner, {"cSettings0": [*corner, side, side], "cSettings1": [*np.round(shift), texel_step, texel_step]}


class CCoastWaves(CComputeChain):
    """The physical waves' simulation (``csPhysicalWaves``) on a square around the camera: one step a frame from the
    copies of the last step's outputs (``maps``, what the sea reads), its place for the sea (``placement``)."""

    def __init__(self, rhi, stage) -> None:
        self.m_rhi = rhi
        self.m_stages = {"waves": CComputeStage(rhi, stage)}
        fmt, flag = QRhiTexture.Format, QRhiTexture.Flag
        size = QSize(WAVES_TEXELS, WAVES_TEXELS)
        out = flag.UsedWithLoadStore | flag.UsedAsTransferSource
        self.m_targets = {"cPhysWavesRT0": rhi.newTexture(fmt.RGBA16F, size, 1, out),
                        "cOceanFoamRT0": rhi.newTexture(fmt.RGBA8, size, 1, out)}  # fmt: skip
        self.m_maps = {
            "cPhysicalWavesMap0": rhi.newTexture(fmt.RGBA16F, size),
            "cOceanFoamMap": rhi.newTexture(fmt.RGBA8, size),
        }
        for tex in (*self.m_targets.values(), *self.m_maps.values()):
            tex.create()
        self.m_corner: np.ndarray | None = None
        self.m_placement = [0.0, 0.0, 1.0, 1.0]  # cPhysicalWaves: the corner, 1 / the side
        # the step by the game's shipped shader on QRhi's list: ``native(command_buffer, named, fill, camera, groups)``
        # (waternative.CNativeWater.coast_waves); None: by the current path's compile
        self.m_native = None

    def run(self, ctx, inputs: dict, fill, camera) -> None:
        """One step for ``camera`` with ``inputs`` (the island's maps, the ocean's, the depth) and ``fill(c)`` (the
        sea's constants)."""
        side = WAVES_TEXELS * WAVES_CELL
        corner, settings = waves_settings(waves_centre(camera, side), self.m_corner)
        first, self.m_corner = self.m_corner is None, corner
        self.m_placement = [*corner, 1.0 / side, 1.0 / side]
        stage = self.m_stages["waves"]
        named = {**inputs, "cPhysicalWavesMap": self.m_maps["cPhysicalWavesMap0"], "cOceanFoamMap": self.m_maps["cOceanFoamMap"],
                 **self.m_targets}  # fmt: skip

        def write(constants) -> None:
            fill(constants)
            for key, value in settings.items():
                constants.set(key, value)

        def before(batch) -> None:
            if first:  # what the sea reads before the first copy: still water, no foam
                for name, texture in self.m_maps.items():
                    zero = bytes(WAVES_TEXELS * WAVES_TEXELS * (8 if name == "cPhysicalWavesMap0" else 4))
                    gamedraw.upload_level(batch, texture, zero)

        def after():
            if stage.m_error:
                return None
            batch = self.m_rhi.nextResourceUpdateBatch()
            for source_name, target_name in (
                ("cPhysWavesRT0", "cPhysicalWavesMap0"),
                ("cOceanFoamRT0", "cOceanFoamMap"),
            ):
                batch.copyTexture(self.m_maps[target_name], self.m_targets[source_name], QRhiTextureCopyDescription())
            return batch

        group_count = groups(WAVES_TEXELS, WAVES_GROUP)
        if self.m_native is not None:
            native = self.m_native
            compute_pass(
                ctx,
                lambda command_buffer: native(command_buffer, named, write, camera, (group_count, group_count, 1)),
                before,
                after,
            )
            return
        record(ctx, stage, named, write, (group_count, group_count, 1), after, before)

    def release(self) -> None:
        super().release()
        for texture in (*self.m_targets.values(), *self.m_maps.values()):
            texture.destroy()
        self.m_targets, self.m_maps = {}, {}


class CSea:
    """The sea of one island view (module docstring); ``error`` says why it does not draw."""

    def __init__(self, rhi, stages: dict, water: dict, waves=None) -> None:
        self.m_rhi, self.m_water, self.m_error = rhi, water, ""
        self.m_waves = CCoastWaves(rhi, waves) if waves is not None else None  # None: no coast waves (step 2's sea)
        self.m_switches = (PHYSICAL,) if waves is not None else ()  # the variant ``compile_stages`` compiled
        self.m_waves_error = ""  # why the coast waves stopped (the sea draws on without them)
        self.m_program = gamedraw.CProgram(stages["vs"], stages["ps"])
        # its stencil test keeps it out of what a cut-out volume marked (CUT_OUT_WATER: the island's frame has stencil)
        state = stages["ps"].state
        self.m_state = replace(renderstates.pass_state(state), stencil=renderstates.stencil_of(state))
        self.m_size_tiles, self.m_seconds = 0.0, 0.0  # the island's side, the view's clock
        self._m_cells = 2  # the water maps' cells a side (the coast's distance has as many)
        self._m_pending: dict[str, tuple] | None = None  # map name -> (format, pixels) to upload
        self._m_model: CSeaModel | None = None

    def set_island(
        self,
        infos: bytes,
        heights: np.ndarray,
        size_tiles: float,
        flow: bytes = b"",
        signed_distance: np.ndarray | None = None,
    ) -> None:
        """The island's water maps (``infos``, ``flow``) and the coast's distance from its heights (made anew: drawn
        from the next frame); ``sdf``: that distance made before (``coast_for``, off the caller's thread)."""
        red_green, blue_alpha = (
            info_maps(infos) if infos else (np.full((2, 2, 2), DEEP[:2], np.uint8), np.zeros((2, 2, 2), np.uint8))
        )
        if signed_distance is None:
            signed_distance = coast_distance(heights, len(red_green), size_tiles)
        texture_format = QRhiTexture.Format
        black = np.zeros((4, 4, 4), np.uint8)
        self._m_pending = {"cWaterInfoMapRG": (texture_format.RG8, red_green), "cWaterInfoMapBA": (texture_format.RG8, blue_alpha),
                         "cTerrainSDF": (texture_format.R32F, signed_distance), "cWaterFlowMap": (texture_format.RGBA8, flow_map(flow)),
                         **dict.fromkeys(BLACK, (texture_format.RGBA8, black))}  # fmt: skip
        self.m_size_tiles, self._m_cells = float(size_tiles), len(red_green)
        self._drop_model()

    def set_coast(self, heights: np.ndarray) -> None:
        """The coast's distance again from edited ``heights`` (after ``set_island``): uploaded into its map."""
        if not self.m_size_tiles:
            return
        signed_distance = coast_distance(heights, self._m_cells, self.m_size_tiles)
        target = self._m_model.m_pending if self._m_model is not None else self._m_pending
        if target is not None:
            target["cTerrainSDF"] = (QRhiTexture.Format.R32F, signed_distance)

    def model(self, camera) -> CSeaModel | None:
        """This frame's draw for ``camera`` (``gameshader.SCamera``) at ``seconds``; None before the island."""
        if self.m_error or (self._m_model is None and self._m_pending is None):
            return None
        if self._m_model is None:
            self._m_model = CSeaModel(
                self.m_rhi, self.m_program, grid(), gamedraw.SMaterial({}, {}), state=self.m_state
            )
            self._m_model.m_own, self._m_model.m_pending, self._m_pending = {}, self._m_pending, None
            self._m_model.m_lent = self.m_waves.m_maps if self.m_waves is not None else {}
            self._m_model.m_switches = self.m_switches
        elapsed_s = float(self.m_seconds)
        self._m_model.m_values = {"cMtxOceanLocal": place(camera.eye, camera.shadow_distance),
                              "cMtxOceanWorld": np.identity(4), "cLocalEye": list(camera.eye), **self.m_water,
                              "cWaterInfoCoord": [1 / self.m_size_tiles] * 2, "cWaterInfoDebugMode": 0,
                              "cTerrainSize": [self.m_size_tiles, self.m_size_tiles, 1 / self.m_size_tiles, 1 / self.m_size_tiles],
                              "cTerrainHeightTexIsValid": 0, "cTimer": [elapsed_s, 0.0, elapsed_s, elapsed_s], "cWaveTime": elapsed_s,
                              "cWaterShadowReflectionIntensity": REFLECTION_SHADOW,
                              "cPhysicalWaves": self.m_waves.m_placement if self.m_waves is not None else [0.0, 0.0, 1.0, 1.0]}  # fmt: skip
        return self._m_model

    def maps(self) -> dict:
        """Its maps the rivers and lakes read too (``LENT``: the water infos, the moving foam and waves) by name;
        empty before its draw made them."""
        model = self._m_model
        return (
            {k: texture for k, texture in {**model.m_own, **model.m_lent}.items() if k in LENT}
            if model is not None and model.m_own
            else {}
        )

    def simulate(self, ctx, inputs: dict, camera, fill) -> None:
        """One step of the coast waves (after ``model`` this frame, before the sea draws): ``inputs`` the ocean's maps,
        the noise and the depth by name, ``fill(c)`` the water's constants; nothing before the island's maps are up."""
        model, waves = self._m_model, self.m_waves
        if waves is None or model is None or model.m_pending or not model.m_own:
            return
        if waves.m_error:
            self.m_waves_error, self.m_waves = waves.m_error, None
            waves.release()
            model.m_lent = {}
            model.release()  # bound again without them
            return

        def write(constants) -> None:
            for key, value in model.m_values.items():
                constants.set(key, value)
            fill(constants)

        waves.run(ctx, {**inputs, **model.m_own}, write, camera)

    def take_failure(self, failed: list) -> str:
        """Why its draw failed in the last frame (taken out of ``failed``, the device's (draw, reason) list; its
        pipeline refused counts too), "" when it did not: it does not draw again."""
        mine = [x for x in failed if x[0] is self._m_model and self._m_model is not None]
        failed[:] = [x for x in failed if all(x is not y for y in mine)]
        why = mine[0][1] if mine else self._m_model.m_error if self._m_model is not None else ""
        if why:
            self.m_error = why
            self._drop_model()
        return why

    def _drop_model(self) -> None:
        if self._m_model is not None:
            for texture in self._m_model.m_own.values():
                texture.destroy()
            self._m_model.release()
        self._m_model = None

    def release(self) -> None:
        self._drop_model()
        self._m_pending = None
        if self.m_waves is not None:
            self.m_waves.release()
            self.m_waves = None
