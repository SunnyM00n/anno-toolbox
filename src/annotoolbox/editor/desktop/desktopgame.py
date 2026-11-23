"""The island view's look "The game's own shaders" (Anno 117 on Direct3D 12): the terrain by the game's terrain shader
into the G-buffer, lit by the game's deferred shading (``render.terraindraw``, ``gameframe``), its near nodes
tessellated from the height pages (``render.terrainpages``), its ground baked by the game's bake
(``render.terrainbake``), the island's objects by their materials' own effects (``desktopgameobjects``), composited
under what the view's own look draws over it.

``CGameIsland(renderer)`` is the entry, one per ``desktoprender.CRenderer``: ``draw(view_frame, options, target, size_px,
camera)`` per frame (False: the view draws its own ground), ``why_not`` says why not; docs/rendering/island-117-view.md.
"""

from __future__ import annotations

import time
from collections import ChainMap, OrderedDict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ...infra.i18n import tr
from ...render import gameinland, gameshadow, terrainbake, terraindraw, terrainnative, terrainnodes, terrainpages
from ...render.cull import boxes_in_view
from ...render.gamecompute import carry
from ...render.gameframe import CGameFrame, SLight, SPasses
from ...render.gamelight import CDeferredShading, SSky
from ...render.gamesea import coast_for
from ..editchange import SChange, union_box
from .desktopgamebake import GROUND_JOB, CIslandBake, CPaintedMaps
from .desktopgameparts import native_shaders, shadow_stages
from .desktopgeometry import river_lines, triangulate

BACKEND = "D3D12"
JOB = "game-island-shaders"
FILES_JOB = "game-island-files"
SEA_JOB = "game-island-sea-maps"
COAST_JOB = "game-island-coast-distance"
INLAND_JOB = "game-island-inland-water"  # the file's rivers' and lakes' flow speeds and looks
FOG_JOB = "game-island-fog-map"
COAST_SECONDS = 1.0  # an edited coast's distance for the sea is made again at most this often (a distance pass)
# the other games' looks by their own shaders, by the game the island follows: (module here, class); 117's otherwise
# (docs/rendering/island-1800-2205-view.md)
LOOKS = {"anno1800": ("desktopgame1800", "CGameIsland1800"), "anno2205": ("desktopgame2205", "CGameIsland2205")}


def sea_maps(editor) -> tuple[bytes, bytes]:
    """The island's ``WaterInfos`` and ``WaterFlow`` maps as raw RGBA bytes (b"" for one the file lacks)."""
    view_renderer = getattr(getattr(editor, "m_view", None), "rd3d", None)
    flow = view_renderer.path("WaterFlow") if view_renderer is not None else None
    return editor.water_infos(), flow.find("map").data if flow is not None else b""


def install_folder(files):
    """The install folder of a game's files (where its compiler lies): theirs, else above the folder of their
    archives (``maindata``); None when neither is known."""
    folder = getattr(files, "m_folder", None)  # a CGameTree or a wrapper
    archives = getattr(files, "m_archives", None)
    if folder is None and archives:
        first = archives[0].m_path
        folder = first.parent.parent if first.parent.name.lower() == "maindata" else first.parent
    return folder


class CPageFiles(Mapping):
    """The height pages by what each is read from (``sources``: its file's path), each read and decoded into its
    parts when a node asks and the latest ``keep`` kept: a continental island's decoded pages took 1.3 GB, its
    files 0.2 GB (Egypt's quadrant too); a page is read again in 0.1 ms and decoded in 0.2 ms."""

    def __init__(self, sources: dict, decode, keep: int = terrainpages.KEEP) -> None:
        self.m_sources, self.m_decode, self.m_keep = sources, decode, keep
        self.m_parts: OrderedDict = OrderedDict()

    def __getitem__(self, key):
        got = self.m_parts.get(key)
        if got is None:
            got = self.m_parts[key] = self.m_decode(self.m_sources[key])
            if len(self.m_parts) > self.m_keep:
                self.m_parts.popitem(last=False)
        else:
            self.m_parts.move_to_end(key)
        return got

    def __contains__(self, key) -> bool:
        return key in self.m_sources

    def __iter__(self):
        return iter(self.m_sources)

    def __len__(self) -> int:
        return len(self.m_sources)


def island_source(editor) -> terraindraw.SIslandSource | None:
    """The island's height pages (their parts) and stored meshes as the terrain draws them, read from the files of
    the island the editor opened (None for an island without them: a new one, another game's)."""
    from ...islands import gameterrain

    view = getattr(editor, "m_view", None)
    if view is None or not hasattr(view, "gamedata_dir"):
        return None
    paths = gameterrain.page_files(view)
    pages = CPageFiles(paths, lambda path: terrainpages.SPageParts(*gameterrain.read_page(view.files.read(path))))
    meshes = {level: gameterrain.stored_meshes(view, level) for level in range(terrainnodes.MIN_LEVEL, 13)}
    tints = gameterrain.page_files(view, "tintmaps", ".dds")
    return terraindraw.SIslandSource(pages, meshes, tints, view.files.read)


def edited_blocks(heights: np.ndarray, base: np.ndarray, step_tiles: float) -> np.ndarray:
    """Per block of the finest nodes whether its heights differ from the island file's (those draw from the view's
    heights: the file's pages and meshes do not hold the edit)."""
    _low, high = block_ranges(np.abs(heights - base), step_tiles, 2.0**terrainnodes.MIN_LEVEL)
    return high > 1e-4


def frame_planes(camera) -> tuple[float, float]:
    """The near and far plane of ``render.camera.COrbitCamera.proj``: the composite writes the view's depth by them."""
    return max(0.1, camera.m_distance_m / 500), max(4000.0, camera.m_distance_m * 8)


class CGameIsland(CIslandBake):
    """The island's frame by the game's own shaders (module docstring)."""

    # how the view's final pass takes ``finish``'s picture (``CFramePasses._finish``, the final shader's modes): 3 linear
    # light, encoded by sRGB there; 4 encoded already (a look whose chain ends encoded skips its decode)
    FINISH_MODE = 3

    def __new__(cls, renderer):
        """The look of the island's game where it has one of its own (``LOOKS``)."""
        if cls is CGameIsland:
            from importlib import import_module

            from ..editsession import game_of

            found = LOOKS.get(game_of(getattr(renderer.m_session, "m_island", None) or {}))
            if found is not None:
                cls = getattr(import_module(f"{__package__}.{found[0]}"), found[1])
        return super().__new__(cls)

    def __init__(self, renderer) -> None:
        self.m_renderer = renderer
        self.m_frame: CGameFrame | None = None
        self.m_terrain: terraindraw.CTerrain | None = None
        self.m_shading: CDeferredShading | None = None
        self.m_sky = None  # gamesky.CSkyPasses: the sky's light by the game's sky passes (None: the look's)
        self.m_fit = None  # shadowfit.CShadowFit: the sun's cascades fitted by the game's analysis (None: on the CPU)
        self.m_pages: terrainpages.CPages | None = None
        self.m_lanes: list[dict] = []  # the view's own bake: per node baked in a frame its targets (``_lane``)
        self.m_composites: dict = {}  # its encode passes by target (``ENCODES``)
        self.m_arrays: dict = {}  # what it goes into by the shader's name (``STORES``)
        self._m_bake_key: tuple | None = None
        self.m_bake: terrainbake.CBake | None = None  # the game's bake, once its shaders and the island's ground are in
        self._m_bake_stages: dict | None = None
        self._m_baked_with: dict = {}  # node -> (stored, page, its textures) its slice was baked with
        self._m_own_rects: dict = {}  # node -> the rectangle the view's own bake drew its slice over
        self._m_unbaked = 0  # shown nodes the game's bake has still to do or did this frame
        self._m_painted: CPaintedMaps | None = None  # the session's strokes in the bake's material maps
        self._m_heights = None  # (game heights or not) the terrain was last made from
        self._m_ranges = None  # the heights' range per block of the finest nodes (``_bounds``)
        self._m_pending = SChange()
        self._m_coarse = False  # the coarse heights were handed to the pages
        self._m_cascades = None  # gameshadow.SCascades read back from the game's analysis (casters culled by them)
        self._m_cascades_for = self._m_asked_for = (
            None  # the camera they were fitted for, the one of the read in flight
        )
        self._m_late = False  # this frame's camera is not the one ``_cascades`` were fitted for
        self.m_animated = False  # the frame moves with the view's clock or still settles: the view draws on
        self.m_error = ""
        self.m_source = None  # models.shaderview.CShaderSource: the install's shaders, once read (the objects compile)
        self.m_quality = None  # render.quality.SQuality the frame was made for (None: the best)
        # whether the shaders were not in the build cache (a first start; None: not known yet): the view's own look
        # draws while they compile (island-117-view.md "Speed", part 6)
        self.m_cold: bool | None = None
        self.m_extras = None  # render.gameextras.CFrameExtras: the water's copies, the visible sky, the post chain
        self.m_sea_waiting = False  # the game's sea compiles or its maps load (``_sea``)
        self._m_coast_edited, self._m_coast_at = False, 0.0  # heights edited since the sea's coast was made, and when
        self.m_inland_waiting = False  # the game's rivers and lakes compile or the file's flow and look load
        self.m_native = None  # render.terrainnative.CNativeTerrain: the shipped shaders' terrain (None: ours)
        self.m_native_objects = None  # render.objectsnative.CNativeObjects: the objects by them (None: the rewrite)
        self.m_native_water = None  # render.waternative.CNativeWater: the water by them (None: the rewrite)
        self.m_native_shading = (
            None  # render.shadingnative.CNativeShading: the light, sky, haze, clouds and fit by them
        )
        self.m_native_post = None  # render.postnative.CNativePost: the eye adaptation, grade, tone map and composite
        self.m_native_ocean = None  # render.oceannative.CNativeOcean: the ocean's simulation (``CFrameExtras`` runs it)
        self.m_native_copies = None  # render.copiesnative.CNativeCopies: the water's depth copy (``CSceneCopies``)
        self.m_native_inland = (
            None  # render.inlandnative.CNativeInland: the rivers' compute passes (``CInlandWater``'s)
        )
        # the native backend's device over QRhi's and its textures handed to QRhi (the pages' and the bake's); the
        # current path's page shaders (the native pages' fallback) and the shipped ones
        self._m_device = self._m_handover = None
        self._m_page_stages: dict | None = None
        self._m_natives = None
        self._m_inland_key: tuple | None = None  # the lines and heights their bodies were made from
        from .desktopgamedecals import CGameDecals
        from .desktopgameground import CGroundMaps
        from .desktopgameobjects import CGameObjects

        self.m_objects = CGameObjects(self)
        self.m_decals = CGameDecals(self)
        self.m_ground = CGroundMaps(self)  # the coarse displacement and the grit the models and the terrain read
        renderer.m_session.m_listeners.append(self.on_change)

    def on_change(self, change: SChange) -> None:
        pending = self._m_pending
        for name in ("heights", "weights"):
            box = getattr(change, name)
            if box is not None:
                setattr(pending, name, union_box(getattr(pending, name), box))
        self._m_coast_edited |= change.heights is not None

    def why_not(self) -> str:
        """Why the look cannot draw here ("" when it can, or while it compiles)."""
        ctx, renderer = self.m_renderer.m_ctx, self.m_renderer
        if self.m_error:
            return self.m_error
        if not hasattr(ctx, "native_pass") or ctx.m_rhi.backendName() != BACKEND:
            return tr("common.games_own_shaders_draw_direct3d")
        if renderer.m_editor is None or not hasattr(renderer.m_editor, "m_files"):
            return tr("common.games_own_shaders_need_files")
        got = renderer.m_loader.get(JOB, self._compile)
        if isinstance(got, Exception):
            return tr("common.games_shaders_not_compile", reason=f"{type(got).__name__}: {got}"[:300])
        return ""

    def _compile(self):
        """In the background: the terrain's two shaders, the deferred shading with its tables, then what may fail
        alone (``_make`` says it): the sky passes (else the look's light), the tessellated technique's four shaders
        and the pages' compute shaders (else every node from the view's heights), the bake's (else our own), the
        shadow's (else no shadow)."""
        from ...infra import dxc
        from ...models import shaderview

        files = self.m_renderer.m_editor.m_files
        game = getattr(files, "m_game", files)  # the install's, not a mod folder's
        compiler = dxc.CDxc.find(anno117=install_folder(game))
        source = self.m_source = shaderview.shared(game, compiler, frame_instances=True)  # (gameinstances)
        try:
            self.m_cold = not source.cached(terraindraw.STAGES)  # a first start: the view's look draws meanwhile
        except Exception:  # the sources do not read: the compile below says why
            self.m_cold = True
        flip = (terrainbake.FLIP_HLSL, terrainbake.FLIP_ENTRY)
        makes = (source.sky_passes, lambda: source.stage_set(terraindraw.DETAILED),
                 lambda: source.stage_set(terrainpages.STAGES),
                 lambda: {**source.stage_set(terrainbake.STAGES), "flip": source.own_stage(*flip)},
                 lambda: shadow_stages(source), lambda: native_shaders(game, compiler, self.m_renderer.m_editor.m_files))  # fmt: skip
        with ThreadPoolExecutor(len(makes) + 2, thread_name_prefix="view-data-base") as pool:  # side by side
            needed = [pool.submit(lambda: source.stage_set(terraindraw.STAGES)), pool.submit(source.shading)]
            optional = [pool.submit(make) for make in makes]
            terrain, shading = (future.result() for future in needed)
        # what may fail alone: the terrain draws all the same, by the look's sky light, untessellated
        return source, terrain, shading, *(future.exception() or future.result() for future in optional)

    def _adopt(self, got: tuple, quality) -> None:
        """The frame's parts made from the compiled shaders ``got`` (``_compile``'s) for ``quality``. Their source
        comes with them: a look made again for another quality did not compile them itself, and without the source
        its objects, decals, water and post chain were never made."""
        self.m_quality = quality  # what the frame is made for (a change makes it anew)
        self.m_source = got[0]
        self._make(*got[1:])

    def compiled(self) -> bool:
        """The shaders are in (``why_not`` said nothing against them): ``draw`` draws."""
        return isinstance(self.m_renderer.m_loader.get(JOB, self._compile), tuple)

    def busy(self) -> int:
        if self.why_not():
            return 0
        if not isinstance(self.m_renderer.m_loader.get(JOB, self._compile), tuple):
            return 1
        loader = self.m_renderer.m_loader
        if self._m_bake_stages is not None and self.m_bake is None and loader.get(GROUND_JOB, self._ground) is None:
            return 1  # the island's ground for the game's bake
        if self.m_objects.m_waiting or self.m_objects.m_ahead_left or self.m_decals.m_waiting:
            return 1  # the objects' or decals' materials still compiling, or parts within reach to be made ahead
        if self.m_sea_waiting or self.m_inland_waiting or (self._m_coast_edited and self.m_renderer.m_game_sea):
            return 1  # the sea's or the rivers' shaders, the island's water maps or an edited coast
        return 1 if (self.m_pages is not None and self.m_pages.busy()) or self._m_unbaked else 0

    def draw(self, view_frame, options, target, size_px: tuple[int, int], camera) -> bool:
        """The terrain into the G-buffer, lit, composited into ``target`` with its depth; False when it is not
        drawn (the view's own ground then)."""
        if self.why_not():
            return False
        got = self.m_renderer.m_loader.get(JOB, self._compile)
        if not isinstance(got, tuple):
            return False
        ctx = self.m_renderer.m_ctx
        if self.m_terrain is None:
            self._adopt(got, getattr(options, "quality", None))
        self._take_failures()  # the last frame's, before this frame's nodes are picked
        if self.m_error:
            return False
        self._sync_heights(view_frame)
        terrain, session = self.m_terrain, self.m_renderer.m_session
        self._sync_files(view_frame)
        x, y, z = camera.eye()
        eye = (session.m_size_tiles - float(x), float(y), float(z))  # the view's x is mirrored
        models = terrain.nodes(eye, self._visible(view_frame.planes))
        quality = getattr(options, "quality", None)  # this frame's: what the look was made with is ``self.quality``
        self.m_frame.set_quality(quality)  # its shadow cascades (the map's side is in ``quality.MADE_WITH``)
        shadows = getattr(options, "shadows", True) and (quality is None or quality.shadows)
        ground = self.m_ground.ground(self.m_pages.m_coarse if self.m_pages is not None else None)
        self.m_objects.m_is_terrain_adapted = ground is not None  # without the session's terrain no model adapts to it
        objects = self.m_objects.frame(
            view_frame, options, shadows, size_px, self._seen_cascades(view_frame, size_px) if shadows else None
        )
        self.m_renderer.m_game_parts = self.m_objects.m_drawn
        decals = (
            self.m_decals.frame(options, ChainMap(self.m_objects.m_neutral or {}, terrain.m_textures)) if ground else []
        )
        self.m_renderer.m_game_decals = bool(decals)  # the view's own decals while the game's compile (or failed)
        ctx.native_pass(
            lambda queue, batch: carry(queue, batch, lambda job: (terrain.uploads(job), self.m_objects.upload(job)))
        )
        if self.m_pages is not None:
            self.m_pages.run(ctx)
        if self._game_bake():
            self._bake_game(models)
        else:
            self._bake_own(options, [model.m_node for model in models])
        models = terrain.shown(models)  # a node not baked yet: the nodes drawn before over its square
        lighting = view_frame.lt
        sun, sun_color = tuple(lighting["sunDir"]), tuple(lighting["sunColor"])
        ambient = tuple(lighting.get("sh", [(0.0, 0.0, 0.0)] * 9))
        light = SLight(sun, sun_color, tuple(channel * lighting.get("shFactor", 1.0) * 0.886 for channel in ambient[0]))
        sky = SSky(sun, sun_color, ambient, lighting.get("shFactor", 1.0))
        shading = (self.m_shading, sky) if not self.m_shading.m_error else None
        passes = SPasses(sky=self.m_sky if self.m_sky is not None and not self.m_sky.m_error else None, ground=ground)
        passes.transparent = objects.transparent
        seconds = float(lighting.get("uwTime", 0.0))
        passes.sea = self._sea(options, seconds) if shading is not None else None
        passes.inland = self._inland(options, seconds) if shading is not None else None
        self._water_passes(passes)
        if shading is not None:  # the water's copies, the sea, the visible sky, haze and clouds (phases 6 and 7)
            transparent, held = bool(passes.transparent), not options.waves  # held: the ocean is not stepped again
            self.m_extras.passes(passes, passes.sky, seconds, ctx, transparent, *options.sky_parts, held=held)
            self._fog(passes.atmosphere)
        near, far = frame_planes(camera)  # with clouds the rays reach their own far plane (frame-117-viewer.md 12)
        cam = self.m_frame.camera(size_px, (x, y, z), camera.m_target, camera.m_fov_rad, near, far, self.m_extras.far())
        native = self._terrain_passes(passes, models, objects.casters, shading is not None and shadows)
        passes.objects = self._object_passes(objects, ground, shading is not None and shadows)
        # the ground stage's order: the terrain, the cut-outs clearing its depth, the decals, the models
        drawn = (models if native is None else []) + objects.cutouts + decals + objects.gbuffer
        self.m_frame.render(target, size_px, drawn, cam, light, finish=False, shading=shading, passes=passes)
        self._end_native(native)
        self.m_renderer.m_stats["terrain_nodes"] = len(models)
        native_casters = len(passes.native.caster_list) if passes.native is not None else 0  # the terrain's
        self.m_renderer.m_stats["terrain_casters"] = len(passes.casters) + native_casters
        self.m_renderer.m_stats.update(
            game_objects=objects.instances, game_object_draws=len(objects.gbuffer), objects_native=len(objects.native),
            transparent_native=sum(getattr(model, "m_native", False) for model in objects.transparent),
            cutouts=len(objects.cutouts), cutouts_native=len(objects.native_cutouts), game_object_levels=objects.levels,
        )  # fmt: skip
        self.m_renderer.m_stats["game_decals"] = sum(decal.m_count for decal in decals)
        for how in ("detailed", "stored"):
            self.m_renderer.m_stats[f"terrain_{how}"] = sum(model.m_kind[0] == how for model in models)
        moving = not options.is_clock_held and any(  # the water and the clouds move with the view's clock
            render_pass is not None for render_pass in (passes.sea, passes.inland, passes.clouds)
        )
        fitted = (
            shadows and self._m_late and self.m_frame.m_cascades is not None and self.m_frame.m_cascades.gpu
        )  # read back
        settling = self._m_unbaked or (self.m_pages is not None and self.m_pages.busy()) or fitted
        self.m_animated = bool(moving or settling)
        return True

    def _end_native(self, native) -> None:
        """The frame's end for the native parts (``native``: the terrain's, None when the current path drew it):
        the device's frame ended once for all of them (their late releases count in it), which ways drew."""
        if native is not None:
            native.end_frame()
        if self.m_native_objects is not None:
            self.m_native_objects.end_frame()
        water = self.m_native_water
        self.m_renderer.m_stats["water_native"] = water.m_drawn if water is not None else 0
        if water is not None:
            water.end_frame()
        if self.m_native_shading is not None:
            self.m_native_shading.end_frame()
        if self._m_device is not None:
            self._m_device.end_frame()
        self.m_renderer.m_stats["terrain_native"] = int(native is not None)
        self.m_renderer.m_stats["pages_native"] = int(getattr(self.m_pages, "m_native", False))
        self.m_renderer.m_stats["bake_native"] = int(getattr(self.m_bake, "m_native", False))
        self.m_renderer.m_stats["shading_native"] = int(
            self.m_native_shading is not None and not self.m_native_shading.m_error
        )
        post = self.m_native_post
        if post is not None:
            post.end_frame()
        self.m_renderer.m_stats["post_native"] = int(post is not None and not post.m_error)
        self.m_renderer.m_stats["ocean_native"] = int(
            self.m_native_ocean is not None and not self.m_native_ocean.m_error
        )
        self._end_copies()

    def _end_copies(self) -> None:
        """The native depth copy's frame end; once it failed (said once), the current path's pass from then on."""
        copies = self.m_native_copies
        self.m_renderer.m_stats["copies_native"] = int(copies is not None and not copies.m_error)
        if copies is not None and copies.m_error:
            self.m_renderer._message(tr("islands.games_native_water", reason=f"copy: {copies.m_error}"[:300]))
            copies.release()
            self.m_native_copies = self.m_extras.m_native_copies = None
        elif copies is not None:
            copies.end_frame()
        inland = self.m_native_inland
        self.m_renderer.m_stats["inland_native"] = int(inland is not None and not inland.m_error)
        if inland is not None and inland.m_error:  # the rivers' passes by the current path from now on, said once
            self.m_renderer._message(tr("islands.games_native_water", reason=f"rivers: {inland.m_error}"[:300]))
            inland.release()
            self.m_native_inland = None
        elif inland is not None:
            inland.end_frame()

    def _object_passes(self, objects, ground, shadows_enabled: bool):
        """The frame's objects by the shipped shaders (``objectsnative.SFrameDraws``: recorded last in the G-buffer's
        and the shadow's passes, reading the parts' and the ground's maps), None when none draws so."""
        native = self.m_native_objects
        cut, cut_casters = objects.native_cutouts, objects.native_cut_casters
        if native is None or native.m_error or not (objects.native or objects.native_casters or cut):
            return None
        from ...render.objectsnative import SFrameDraws

        if (cut or cut_casters) and (self.m_native is None or self.m_native.m_error):  # they follow the native terrain
            native.m_failed += [(entry.part, "the terrain is not drawn natively") for entry in cut + cut_casters]
            cut, cut_casters = [], []
        seen = {
            **ChainMap(self.m_objects.m_neutral or {}, self.m_terrain.m_textures),
            **(ground.maps if ground else {}),
        }
        return SFrameDraws(native, objects.native, objects.native_casters if shadows_enabled else [], seen, ground, cut,
                          cut_casters if shadows_enabled else [])  # fmt: skip

    def _water_passes(self, passes) -> None:
        """The coast waves' step and the water's and transparent parts' draws by the shipped shaders where they draw
        so (``waternative``: the transparent pass's native runs; the current path's for the rest)."""
        water = self.m_native_water if self.m_native_water is not None and not self.m_native_water.m_error else None
        waves = getattr(passes.sea, "m_waves", None)
        if waves is not None:
            waves.m_native = water.coast_waves if water is not None else None
        if water is not None or self.m_native_objects is not None:
            from ...render.waternative import SFrameWater

            passes.native_water = SFrameWater(water, self.m_native_objects)

    def _terrain_passes(self, passes, models: list, object_casters: list, shadows_enabled: bool):
        """The frame's shadow casters and fit, the light's way (``shadingnative``) and the terrain's: drawn first in
        the G-buffer's and the shadow's passes by the shipped shaders (``terrainnative``: returned), or among the
        current path's models (None)."""
        if self.m_native_shading is not None and not self.m_native_shading.m_error:
            passes.shading = self.m_native_shading
        native = self.m_native if self.m_native is not None and not self.m_native.m_error else None
        casters = []
        if shadows_enabled:
            casters = self.m_terrain.casters(models)
            passes.casters, passes.box = (casters if native is None else []) + object_casters, self._box()
            passes.fit = self._fit()
        if native is not None:
            passes.native = terrainnative.SFrameDraws(native, models, casters, passes.ground, self.m_terrain.m_detailed)
        return native

    def finish(self, image, size_px: tuple[int, int], lut):
        """The game's grade and tone map of the view's resolved colour (phase 8; ``lut`` the ambient's grade cube or
        None): the tone-mapped texture, None while the post chain compiles or when it does not."""
        if self.m_extras is None or self.m_shading is None or self.m_shading.m_error:
            return None
        noise = self.m_shading.m_fixed.get("cBlueNoiseTex")
        return self.m_extras.finish(
            self.m_renderer.m_ctx, image, size_px, lut.native if lut is not None else None, noise
        )

    def post(self, image, size_px: tuple[int, int], lut, fbo, view: tuple[int, int]) -> bool:
        """The eye adaptation, grade, tone map and the composite into the view's ``fbo`` (``view`` pixels) by the
        shipped shaders (``postnative``) in place of ``finish`` and the view's final pass; False where they do not
        draw (the current path's then)."""
        native = getattr(self, "m_native_post", None)
        if native is None or native.m_error or self.m_shading is None or self.m_shading.m_error:
            return False
        from ...render.nativeforeign import CNativeError
        from ...render.postnative import SMaps

        tables = self.m_native_shading.m_tables if self.m_native_shading is not None else {}
        noise = tables.get("cBlueNoiseTex") or self.m_shading.m_fixed.get("cBlueNoiseTex")
        try:
            native.run(self.m_renderer.m_ctx, image, size_px, fbo, view, SMaps(lut, noise))
        except CNativeError as exc:  # a view target the module has no format for: the current path, said once
            native.m_error = str(exc)
            return False
        return True

    def steady(self, step: float) -> None:
        """The eye adaptation from the next frame on at its target, eased by ``step`` seconds a frame, whichever way
        draws it (``CFrameExtras.steady``, ``CNativePost.steady``): a bench's same exposure history both ways."""
        for part in (self.m_extras, self.m_native_post):
            if part is not None:
                part.steady(step)

    def read_exposure(self) -> None:
        """The next frame reads its exposure back (``exposure``), from the way that draws the post chain."""
        part = (
            self.m_native_post if self.m_native_post is not None and not self.m_native_post.m_error else self.m_extras
        )
        if part is not None:
            part.read_exposure()

    def exposure(self) -> float | None:
        """The exposure ``read_exposure`` asked for (None: not asked, not in yet, no eye adaptation)."""
        part = (
            self.m_native_post if self.m_native_post is not None and not self.m_native_post.m_error else self.m_extras
        )
        return part.exposure() if part is not None else None

    def _sea(self, opts, seconds: float):
        """The sea by the game's ocean shader with this island's maps (phase 6, step 2), the view's own sea left out
        where it draws; None while it compiles or its maps load (``sea_waiting``: the view is busy), when it does
        not, and without the sea layer."""
        session, extras = self.m_renderer.m_session, self.m_extras
        self.m_sea_waiting = False
        if not (opts.layers.get("water", True) and session.m_island.get("sea", True)) or extras is None:
            return None
        sea, water = extras.sea(), extras.water()  # it reads the scene's copies
        if sea is None or water is None:
            self.m_sea_waiting = all(extras.m_made.get(k) is not False for k in ("sea", "water"))
            return None
        if not sea.m_size_tiles:
            maps = self.m_renderer.m_loader.get(SEA_JOB, lambda: sea_maps(self.m_renderer.m_editor))
            if maps is None:
                self.m_sea_waiting = True
                return None
            if isinstance(maps, Exception):  # the maps do not read: the sea as open sea
                maps = (b"", b"")
            size_tiles = float(session.m_size_tiles)
            coast = self.m_renderer.m_loader.get(
                COAST_JOB, lambda: coast_for(maps[0], session.m_heights.copy(), size_tiles)
            )
            if coast is None:  # the distance pass runs off the window's thread
                self.m_sea_waiting = True
                return None
            sea.set_island(
                maps[0], session.m_heights, size_tiles, maps[1], coast if not isinstance(coast, Exception) else None
            )
            self._m_coast_edited, self._m_coast_at = False, time.monotonic()
        elif self._m_coast_edited and time.monotonic() - self._m_coast_at >= COAST_SECONDS:
            sea.set_coast(session.m_heights)  # the shore's foam and fade follow an edited coast, at most once a second
            self._m_coast_edited, self._m_coast_at = False, time.monotonic()
        if sea.m_waves_error:  # the sea draws on without its coast waves: said once
            self.m_renderer._message(tr("islands.games_coast_waves", reason=sea.m_waves_error[:300]))
            sea.m_waves_error = ""
        sea.m_seconds = seconds
        self.m_renderer.m_game_sea = not sea.m_error
        return sea if not sea.m_error else None

    def _fog(self, atmosphere) -> None:
        """The island's fog map handed to the visible sky's passes once it is read (phase 7), and the session's
        heights it stands on; an island file without one: no fog."""
        if atmosphere is None:
            return
        atmosphere.m_heights = self.m_pages.m_coarse if self.m_pages is not None else None
        size_tiles = float(self.m_renderer.m_session.m_size_tiles)
        if atmosphere.m_fog_size != size_tiles:
            raw = self.m_renderer.m_loader.get(FOG_JOB, lambda: self.m_renderer.m_editor.m_view.rd3d.path("Fog").data)
            if raw is not None:
                atmosphere.set_fog(raw if isinstance(raw, bytes) else b"", size_tiles)

    def _inland(self, opts, seconds: float):
        """The rivers and lakes by the game's inland water shader along the session's lines (phase 6, step 4), the
        view's own left out where they draw; None without any, while it compiles or the file's flow and look load
        (``inland_waiting``: the view is busy), when it does not, and without the water layer."""
        session, extras = self.m_renderer.m_session, self.m_extras
        self.m_inland_waiting = False
        # every river and lake line, the short ones too: the file's flow and look are matched to them by place
        lines, stamp = river_lines(session.m_island)
        if not opts.layers.get("water", True) or extras is None or all(len(spline["points"]) < 2 for spline in lines):
            return None
        inland = extras.inland("egypt" in str(getattr(self.m_renderer.m_editor, "m_a7m_path", "")).lower())
        found = self.m_renderer.m_loader.get(INLAND_JOB, lambda: self.m_renderer.m_editor.m_view.inland_waters())
        if inland is None or found is None:
            self.m_inland_waiting = extras.m_made.get("inland") is not False
            return None
        coarse = self.m_pages.m_coarse if self.m_pages is not None else None
        key = (stamp, id(coarse))  # the lines edited, the session's heights come in: the flow again
        if key != self._m_inland_key:
            inland.set_bodies(gameinland.bodies(lines, [] if isinstance(found, Exception) else found, triangulate),
                              float(session.m_size_tiles))  # fmt: skip
            self._m_inland_key = key
        inland.m_session_ms = getattr(opts, "season_ms", None)  # Egypt's season cycle (``gameinland.SSeasonCycle``)
        inland.m_native = self.m_native_inland
        inland.run(self.m_renderer.m_ctx, seconds, coarse)
        self.m_renderer.m_game_inland = not inland.m_error
        return inland if not inland.m_error else None

    def _seen_cascades(self, frame, size_px: tuple[int, int]):
        """The sun's cascades as last known on the CPU (the casters are culled by them): fitted there, or read back
        from the game's analysis a frame or two ago (one read in flight at a time); None before the first.
        ``_late``: they were fitted for another camera than this frame's (the view draws on until they are not)."""
        fit = self._fit()
        self._m_late = False
        if fit is None:
            cascades = self.m_frame.m_cascades if self.m_frame is not None else None
            return cascades if cascades is not None and not cascades.gpu else None
        seen = (np.asarray(frame.view_proj).tobytes(), tuple(size_px))
        if fit.m_readbacks and fit.m_readbacks[1].data.size() >= gameshadow.INFO_BYTES * gameshadow.COUNT:
            self._m_cascades = gameshadow.read_info(bytes(fit.m_readbacks[1].data), np.zeros(3), 0.0)
            self._m_cascades_for, fit.m_readbacks = self._m_asked_for, []
        if not fit.m_readbacks:
            fit.m_read_back, self._m_asked_for = True, seen
        self._m_late = self._m_cascades_for != seen
        return self._m_cascades

    def _fit(self):
        """The cascades' analysis: the native one with the native shading, else the current path's (None: none)."""
        native = self.m_native_shading
        if native is not None and not native.m_error:
            return native.m_fit
        return self.m_fit if self.m_fit is not None and not self.m_fit.m_error else None

    def _sync_files(self, future) -> None:
        """The island files' geometry for the nodes when the view shows the game's terrain: the pages and meshes
        read once in the background, the coarse heights uploaded once (the file's: an edited block draws from the
        view's heights)."""
        terrain, session = self.m_terrain, self.m_renderer.m_session
        got = self.m_renderer.m_loader.get(FILES_JOB, lambda: island_source(self.m_renderer.m_editor))
        if isinstance(got, Exception):
            self.m_renderer._message(tr("islands.islands_height_pages", reason=f"{type(got).__name__}: {got}"[:300]))
        source = got if isinstance(got, terraindraw.SIslandSource) else None
        if source is not None and terrain.m_source is None and terrain.m_heights is not None:
            terrain.set_heights(terrain.m_heights, None)  # the nodes made before it came read its tint pages now
        terrain.m_source = source
        terrain.m_files_shown = future.game_heights is not None
        if (
            self.m_pages is not None
            and terrain.m_source is not None
            and self.m_pages.m_coarse is None
            and not self._m_coarse
        ):
            self.m_pages.set_coarse(session.m_base_heights, session.m_step_tiles)
            self._m_coarse = True

    def _visible(self, planes):
        """Whether a node's box (its heights' range) is in view (None: cull off, all are)."""
        if planes is None:
            return None
        session = self.m_renderer.m_session
        bounds = self._bounds

        def visible(node) -> bool:
            low, high = bounds(node)
            box_lo = np.array([[session.m_size_tiles - node.x - node.tiles, low, node.z]])
            box_hi = np.array([[session.m_size_tiles - node.x, high, node.z + node.tiles]])
            return bool(boxes_in_view(planes, box_lo, box_hi)[0])

        return visible

    def _box(self) -> tuple[np.ndarray, np.ndarray]:
        """The island's bounds in the view's axes: what the sun's cascades are fitted to on the CPU."""
        low, high = self._block_ranges()
        size_px = float(self.m_renderer.m_session.m_size_tiles)
        return np.array([0.0, float(low.min()), 0.0]), np.array([size_px, float(high.max()), size_px])

    def _block_ranges(self) -> tuple[np.ndarray, np.ndarray]:
        """The heights' range per block of the finest nodes, made once per height change."""
        if self._m_ranges is None:
            self._m_ranges = block_ranges(
                self.m_terrain.m_heights, self.m_renderer.m_session.m_step_tiles, 2.0**terrainnodes.MIN_LEVEL
            )
        return self._m_ranges

    def _bounds(self, node) -> tuple[float, float]:
        """The lowest and highest height under a node (from ``_block_ranges``)."""
        low, high = self._block_ranges()
        per = 2**terrainnodes.MIN_LEVEL
        block_i, block_j = int(node.x // per), int(node.z // per)
        blocks = max(1, int(node.tiles // per))
        return float(low[block_j : block_j + blocks, block_i : block_i + blocks].min()) - 0.5, float(
            high[block_j : block_j + blocks, block_i : block_i + blocks].max()
        ) + 2.5

    def _take_failures(self) -> None:
        """A node that did not prepare in the last frame, or a pass that raised, turns the look off (said once):
        the view's own ground draws again (docs/viewer.md "When a pass fails"); a tessellated node, or the pages'
        compute shaders, turn only themselves off (the nodes then draw untessellated, from the view's heights)."""
        ctx = self.m_renderer.m_ctx
        self.m_objects.take_failures()  # an object's draw: its part to the view's look
        if (
            self.m_native_objects is not None and self.m_native_objects.m_error
        ):  # the objects by the current path, said once
            self.m_renderer._message(tr("islands.games_native_objects", reason=self.m_native_objects.m_error[:300]))
            self.m_objects.drop_native()
            self.m_native_objects.release()
            self.m_native_objects = None
        if self.m_native_water is not None and self.m_native_water.m_error:  # the water by the current path, said once
            self.m_renderer._message(tr("islands.games_native_water", reason=self.m_native_water.m_error[:300]))
            self.m_native_water.release()
            self.m_native_water = None
        if self.m_native_shading is not None and self.m_native_shading.m_error:  # step 5 by the current path, said once
            self.m_renderer._message(tr("islands.games_native_shading", reason=self.m_native_shading.m_error[:300]))
            self.m_native_shading.release()
            self.m_native_shading = None
        if self.m_native_post is not None and self.m_native_post.m_error:  # step 7 by the current path, said once
            self.m_renderer._message(tr("islands.games_native_post", reason=self.m_native_post.m_error[:300]))
            self.m_native_post.release()
            self.m_native_post = None
        if (
            self.m_native is not None and self.m_native.m_error
        ):  # the terrain by the current path from now on, said once
            self.m_renderer._message(tr("islands.games_native_terrain", reason=self.m_native.m_error[:300]))
            self.m_native.release()
            self.m_native = None
        self.m_decals.take_failures(ctx.m_failed_models)  # a decal's draw: the view's own decals from now on
        made = self.m_extras.m_made if self.m_extras is not None else {}
        from ...render.gameextras import N_INLAND, N_SEA

        for key, said in (("sea", N_SEA), ("inland", N_INLAND)):
            why = made[key].take_failure(ctx.m_failed_models) if made.get(key) else ""
            if why:  # that part alone: the view's own water from now on, said once
                self.m_renderer._message(tr(said, reason=why[:300]))
        mine = [
            (model, reason) for model, reason in ctx.m_failed_models if isinstance(model, terraindraw.CTerrainModel)
        ]
        ctx.m_failed_models[:] = [x for x in ctx.m_failed_models if all(x is not y for y in mine)]
        terrain = self.m_terrain
        patches = [x for x in mine if x[0].m_program is terrain.m_detailed]
        if patches:  # the tessellated nodes alone: they draw untessellated from now on, said once
            terrain.m_detailed = None
            self.m_renderer._message(tr("islands.games_tessellated_terrain", reason=patches[0][1][:300]))
            terrain.set_heights(terrain.m_heights, None)
        if self.m_pages is not None and self.m_pages.m_error:
            native = getattr(self.m_pages, "m_native", False)  # the native pages: the current path's from now on
            said = "islands.games_native_pages" if native else "islands.games_height_pages"
            self.m_renderer._message(tr(said, reason=self.m_pages.m_error[:300]))
            self.m_pages.release()
            if native and getattr(self.m_bake, "m_native", False):  # it reads the native pages: the current path's too
                self._drop_native_bake(tr("islands.games_native_pages", reason=self.m_pages.m_error[:300]))
            self.m_pages = terrain.m_pages = self._new_pages(self._m_page_stages, is_native=False) if native else None
            self._m_coarse = False
            terrain.set_heights(terrain.m_heights, None)
        mine = [x for x in mine if all(x is not y for y in patches)]
        shadows = [x for x in mine if terrain.m_shadow is not None and x[0].m_program in terrain.m_shadow]
        if shadows:  # the casters alone: no terrain shadow from now on, said once
            terrain.m_shadow = None
            self.m_renderer._message(tr("islands.games_terrain_shadow", reason=shadows[0][1][:300]))
        mine = [x for x in mine if all(x is not y for y in shadows)]
        baking = [x for x in mine if self.m_bake is not None and x[0].m_program is self.m_bake.m_program]
        if self.m_bake is not None and self.m_bake.m_error and getattr(self.m_bake, "m_native", False):
            self._drop_native_bake(self.m_bake.m_error)  # the native bake alone: the current path's from now on
        elif baking or (self.m_bake is not None and self.m_bake.m_error):
            self._drop_bake(baking[0][1] if baking else self.m_bake.m_error)
        mine = [x for x in mine if all(x is not y for y in baking)]
        if mine:
            self.m_error = tr("common.games_terrain_shader", reason=mine[0][1][:300])
        if ctx.m_pass_errors:
            self.m_error = tr("common.games_frame_failed", reason=ctx.m_pass_errors[-1][:300])
            ctx.m_pass_errors.clear()

    def _make(self, stages: dict, shading, sky, detailed, pages, bake, shadow, native=None) -> None:
        from PySide6.QtGui import QRhi

        from ...render.gamedraw import CProgram
        from ...render.gameextras import CFrameExtras
        from ...render.gamesky import CSkyPasses

        session, ctx = self.m_renderer.m_session, self.m_renderer.m_ctx
        self.m_frame = CGameFrame(ctx, has_stencil=True)  # the cut-outs' and the water's stencil bits
        self.m_frame.set_quality(self.m_quality)
        self.m_shading = CDeferredShading(ctx.m_rhi, *shading)
        self.m_extras = CFrameExtras(
            ctx.m_rhi, lambda: self.m_source, self.m_renderer.m_loader.get, self.m_renderer._message
        )
        said = {"sky": lambda why: tr("islands.games_sky_passes", reason=why),
                "detailed": lambda why: tr("islands.games_tessellated_terrain", reason=why),
                "bake": lambda why: tr("islands.games_ground_bake", reason=why),
                "shadow": lambda why: tr("islands.games_terrain_shadow", reason=why)}  # fmt: skip
        for key, got in (("sky", sky), ("detailed", detailed), ("bake", bake), ("shadow", shadow)):
            if isinstance(got, Exception):
                self.m_renderer._message(said[key](f"{type(got).__name__}: {got}"[:300]))
        if not isinstance(sky, Exception):
            self.m_sky = CSkyPasses(ctx.m_rhi, *sky)
        tessellated = None
        wants = self.m_quality is None or self.m_quality.tessellation
        if isinstance(detailed, dict) and wants and ctx.m_rhi.isFeatureSupported(QRhi.Feature.Tessellation):
            tessellated = CProgram(detailed["vs"], detailed["ps"], hull_stage=detailed["hs"], domain_stage=detailed["ds"])  # fmt: skip
        self.m_terrain = terraindraw.CTerrain(ctx.m_rhi, CProgram(stages["vs"], stages["ps"]), float(session.m_size_tiles), session.m_step_tiles,
                                           session.m_width_samples, tessellated)  # fmt: skip
        self._m_page_stages = pages if isinstance(pages, dict) else None
        self._m_bake_stages = bake if isinstance(bake, dict) else None
        if isinstance(shadow, dict):
            self._make_shadow(shadow, tessellated is not None)
        self._make_native(native)
        self._make_shading(shading[1])
        self._make_post()
        self.m_pages = self.m_terrain.m_pages = self._new_pages(pages)

    def _make_native(self, natives) -> None:
        """The native backend on QRhi's device where the shipped shaders read, and the terrain by them
        (``terrainnative``); else, or when its pipelines are refused, the current path draws it (said once)."""
        if natives is None:
            return
        from ...render import nativecompute, nativegpu

        try:
            if isinstance(natives, Exception):
                raise natives
            device = nativegpu.attach(self.m_renderer.m_ctx.m_rhi)
            nativegpu.check(device)
        except Exception as exc:  # the whole native look off, said once and by View > Rendering's switch
            why = f"{type(exc).__name__}: {exc}"[:300]
            nativegpu.failed(why)
            self.m_renderer._message(tr("islands.shipped_shaders_off", reason=why))
            return
        try:
            self._m_device = device
            self._m_handover = nativecompute.CHandover(self._m_device, self.m_renderer.m_ctx.m_rhi)
            self._m_natives = natives
            if terrainnative.is_enabled():
                self.m_native = terrainnative.CNativeTerrain(self._m_device, natives, has_stencil=True)
        except Exception as exc:
            self.m_native = None
            self.m_renderer._message(tr("islands.games_native_terrain", reason=f"{type(exc).__name__}: {exc}"[:300]))
        from ...render import objectsnative, waternative

        if self._m_device is not None and objectsnative.is_enabled():
            try:
                most = self.m_quality.max_texture if self.m_quality is not None else 0
                self.m_native_objects = objectsnative.CNativeObjects(self._m_device, natives, True, most)
            except Exception as exc:  # the objects by the current path, said once
                self.m_renderer._message(
                    tr("islands.games_native_objects", reason=f"{type(exc).__name__}: {exc}"[:300])
                )
        if self._m_device is not None and waternative.is_enabled():
            try:
                self.m_native_water = waternative.CNativeWater(self._m_device, natives)
            except Exception as exc:  # the water by the current path, said once
                self.m_renderer._message(tr("islands.games_native_water", reason=f"{type(exc).__name__}: {exc}"[:300]))
        self._make_ocean(natives)
        self._make_copies(natives)
        self._make_inland(natives)

    def _make_ocean(self, natives) -> None:
        """The ocean's simulation by the shipped shaders (``oceannative``), run by ``CFrameExtras``; else, or when
        it does not bind, the current path's (said once)."""
        from ...render import gameocean, oceannative

        if self._m_device is None or self.m_extras is None or not oceannative.is_enabled():
            return
        try:
            water = gameocean.read_water(self.m_source.ambient_text())
            self.m_native_ocean = oceannative.CNativeOcean(self._m_device, natives, self._m_handover, water)
            self.m_extras.m_native_ocean = self.m_native_ocean
        except Exception as exc:
            why = f"{oceannative.STEM}: {type(exc).__name__}: {exc}"[:300]
            self.m_renderer._message(tr("islands.games_native_water", reason=why))

    def _make_copies(self, natives) -> None:
        """The water's depth copy by the shipped shader (``copiesnative``), run by ``gamewater.CSceneCopies``; else,
        or when it does not bind, the current path's (said once)."""
        from ...render import copiesnative

        if self._m_device is None or self.m_extras is None or not copiesnative.is_enabled():
            return
        try:
            self.m_native_copies = self.m_extras.m_native_copies = copiesnative.CNativeCopies(self._m_device, natives)
        except Exception as exc:
            why = f"{copiesnative.STEM}: {type(exc).__name__}: {exc}"[:300]
            self.m_renderer._message(tr("islands.games_native_water", reason=why))

    def _make_inland(self, natives) -> None:
        """The rivers' compute passes by the shipped shaders (``inlandnative``), run by ``gameinland.CInlandWater``;
        else, or when they do not bind, the current path's (said once)."""
        from ...render import inlandnative

        if self._m_device is None or not inlandnative.is_enabled():
            return
        try:
            self.m_native_inland = inlandnative.CNativeInland(self._m_device, natives)
        except Exception as exc:
            self.m_renderer._message(
                tr("islands.games_native_water", reason=f"rivers: {type(exc).__name__}: {exc}"[:300])
            )

    def _make_shading(self, tables) -> None:
        """The light, sky, haze, clouds and the cascades' fit by the shipped shaders (``shadingnative``) where the
        native backend runs; else, or when they do not bind, the current path's (said once)."""
        from ...render import gamesky, shadingnative

        if self._m_device is None or not shadingnative.is_enabled():
            return
        try:
            atmosphere = gamesky.read_atmosphere(self.m_source.ambient_text())
            self.m_native_shading = shadingnative.CNativeShading(self._m_device, self._m_natives, self._m_handover,
                                                              self.m_renderer.m_ctx.m_rhi, tables, atmosphere)  # fmt: skip
        except Exception as exc:
            self.m_renderer._message(tr("islands.games_native_shading", reason=f"{type(exc).__name__}: {exc}"[:300]))

    def _make_post(self) -> None:
        """The eye adaptation, grade, tone map and composite by the shipped shaders (``postnative``) where the
        native backend runs; else, or when they do not bind, the current path's (said once)."""
        from ...render import postnative

        if self._m_device is None or not postnative.is_enabled():
            return
        try:
            self.m_native_post = postnative.CNativePost(self._m_device, self._m_natives)
        except Exception as exc:
            self.m_renderer._message(tr("islands.games_native_post", reason=f"{type(exc).__name__}: {exc}"[:300]))

    def _new_pages(self, stages, is_native: bool = True):
        """The height pages by the shipped shaders on the native backend where they read (``pagesnative``), else by
        the current path's compile (``stages``; its error said); None without either."""
        from ...render import pagesnative

        session, ctx = self.m_renderer.m_session, self.m_renderer.m_ctx
        if is_native and self._m_handover is not None and pagesnative.is_enabled():
            try:
                return pagesnative.CNativePages(self._m_device, self._m_natives, self._m_handover, float(session.m_size_tiles),
                                               self.m_terrain.m_island)  # fmt: skip
            except Exception as exc:
                self.m_renderer._message(tr("islands.games_native_pages", reason=f"{type(exc).__name__}: {exc}"[:300]))
        if isinstance(stages, Exception):
            self.m_renderer._message(
                tr("islands.games_height_pages", reason=f"{type(stages).__name__}: {stages}"[:300])
            )
        return (
            terrainpages.CPages(ctx.m_rhi, stages, float(session.m_size_tiles), self.m_terrain.m_island)
            if isinstance(stages, dict)
            else None
        )

    def _make_shadow(self, shadow: dict, is_tessellated: bool) -> None:
        """The terrain's shadow programs (the tessellated one for tessellated nodes) and the cascades' analysis."""
        from ...render.gamedraw import CProgram
        from ...render.shadowfit import CShadowFit

        plain, detailed = shadow["plain"], shadow["detailed"]
        patches = None
        if is_tessellated and detailed is not None:
            patches = CProgram(detailed["vs"], detailed["ps"], hull_stage=detailed["hs"], domain_stage=detailed["ds"])
        self.m_terrain.m_shadow = (CProgram(plain["vs"], plain["ps"]), patches)
        self.m_fit = CShadowFit(self.m_renderer.m_ctx.m_rhi, shadow["fit"]) if shadow["fit"] is not None else None

    def _sync_heights(self, future) -> None:
        """The terrain's heights: the game's near terrain with the edits when the view shows it, else the editor's;
        all nodes again when that choice changed, else those an edit touched."""
        session = self.m_renderer.m_session
        game = future.game_heights is not None
        heights = future.game_heights if game else session.m_heights
        box, self._m_pending.heights = self._m_pending.heights, None
        if self._m_heights != game:
            self._m_heights = game
            self.m_terrain.set_heights(heights, None)
        elif box is not None:
            self.m_terrain.set_heights(heights, box)
        else:
            return
        self._m_ranges = None
        self.m_terrain.m_edited = edited_blocks(session.m_heights, session.m_base_heights, session.m_step_tiles)

    def release(self) -> None:
        if self.on_change in self.m_renderer.m_session.m_listeners:
            self.m_renderer.m_session.m_listeners.remove(self.on_change)
        self._drop_own_bake()
        self.m_objects.release()
        self.m_decals.release()
        for part in (self.m_bake, self.m_terrain, self.m_pages, self.m_sky, self.m_fit, self.m_shading, self.m_frame, self.m_extras,
                     getattr(self, "m_native", None), self.m_native_objects, self.m_native_water, self.m_native_shading,
                     self.m_native_post, self.m_native_ocean, self.m_native_copies, self.m_native_inland,
                     self._m_handover):  # fmt: skip
            if part is not None:
                part.release()
        self.m_ground.release()
        self.m_bake = self.m_terrain = self.m_pages = self.m_sky = self.m_fit = self.m_shading = self.m_frame = (
            self.m_extras
        ) = None
        self.m_native = self.m_native_objects = self.m_native_water = self.m_native_shading = self.m_native_post = None
        self.m_native_ocean = self.m_native_copies = self.m_native_inland = None
        self._m_device = self._m_handover = self._m_natives = None
        self._m_baked_with.clear()
        self._m_own_rects.clear()
        self._m_painted = None
        self._m_bake_key = self._m_heights = self._m_ranges = self._m_cascades = None
        self._m_coarse = False


def block_ranges(heights: np.ndarray, step_tiles: float, tiles: float) -> tuple[np.ndarray, np.ndarray]:
    """The lowest and highest height per block of ``tiles`` x ``tiles`` (its far edge included), [rows, columns]."""
    per = max(1, round(tiles / step_tiles))
    blocks = (heights.shape[0] - 1 + per - 1) // per
    side = blocks * per + 1  # the samples the blocks reach, the far edge's repeated past the grid (a slice clips there)
    padded = np.pad(heights, ((0, max(0, side - heights.shape[0])), (0, max(0, side - heights.shape[1]))), mode="edge")
    padded = padded[:side, :side]

    def reduce(array: np.ndarray, reducer, axis: int) -> np.ndarray:
        """Per block along ``axis``: ``reducer`` over its ``per`` samples and the next block's first (its far edge)."""
        array = np.moveaxis(array, axis, 0)
        inner = reducer(array[: blocks * per].reshape(blocks, per, *array.shape[1:]), axis=1)
        return np.moveaxis(reducer(np.stack([inner, array[per::per][:blocks]]), axis=0), 0, axis)

    low = reduce(reduce(padded, np.min, 0), np.min, 1)
    high = reduce(reduce(padded, np.max, 0), np.max, 1)
    return low.astype(float), high.astype(float)
