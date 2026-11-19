"""The island view's look "The game's own shaders" (Anno 117 on Direct3D 12): the terrain by the game's terrain shader
into the G-buffer, lit by the game's deferred shading (``render.terraindraw``, ``gameframe``), its near nodes
tessellated from the height pages (``render.terrainpages``), its ground baked by the game's bake
(``render.terrainbake``), the island's objects by their materials' own effects (``desktopgameobjects``), composited
under what the view's own look draws over it.

``GameIsland(renderer)`` is the entry, one per ``desktoprender.Renderer``: ``draw(frame, opts, target, size,
camera)`` per frame (False: the view draws its own ground), ``why_not`` says why not; docs/rendering/island-117-view.md.
"""

from __future__ import annotations

import json
import time
from collections import ChainMap, OrderedDict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ...infra.i18n import tr
from ...render import gameinland, gameshadow, terrainbake, terraindraw, terrainnative, terrainnodes, terrainpages
from ...render.cull import boxes_in_view
from ...render.gamecompute import carry
from ...render.gameframe import GameFrame, Light, Passes
from ...render.gamelight import DeferredShading, Sky
from ...render.gamesea import coast_for
from ..editchange import Change, union_box
from .desktopgamebake import GROUND_JOB, IslandBake, PaintedMaps
from .desktopgeometry import triangulate

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
LOOKS = {"anno1800": ("desktopgame1800", "GameIsland1800"), "anno2205": ("desktopgame2205", "GameIsland2205")}


def sea_maps(editor) -> tuple[bytes, bytes]:
    """The island's ``WaterInfos`` and ``WaterFlow`` maps as raw RGBA bytes (b"" for one the file lacks)."""
    rd = getattr(getattr(editor, "view", None), "rd", None)
    flow = rd.path("WaterFlow") if rd is not None else None
    return editor.water_infos(), flow.find("map").data if flow is not None else b""


def install_folder(files):
    """The install folder of a game's files (where its compiler lies): theirs, else above the folder of their
    archives (``maindata``); None when neither is known."""
    folder = getattr(files, "folder", None)
    archives = getattr(files, "archives", None)
    if folder is None and archives and hasattr(archives[0], "path"):
        folder = archives[0].path.parent
        folder = folder.parent if folder.name.lower() == "maindata" else folder
    return folder


class PageFiles(Mapping):
    """The height pages by what each is read from (``sources``: its file's path), each read and decoded into its
    parts when a node asks and the latest ``keep`` kept: a continental island's decoded pages took 1.3 GB, its
    files 0.2 GB (Egypt's quadrant too); a page is read again in 0.1 ms and decoded in 0.2 ms."""

    def __init__(self, sources: dict, decode, keep: int = terrainpages.KEEP) -> None:
        self.sources, self.decode, self.keep = sources, decode, keep
        self.parts: OrderedDict = OrderedDict()

    def __getitem__(self, key):
        got = self.parts.get(key)
        if got is None:
            got = self.parts[key] = self.decode(self.sources[key])
            if len(self.parts) > self.keep:
                self.parts.popitem(last=False)
        else:
            self.parts.move_to_end(key)
        return got

    def __contains__(self, key) -> bool:
        return key in self.sources

    def __iter__(self):
        return iter(self.sources)

    def __len__(self) -> int:
        return len(self.sources)


def island_source(editor) -> terraindraw.IslandSource | None:
    """The island's height pages (their parts) and stored meshes as the terrain draws them, read from the files of
    the island the editor opened (None for an island without them: a new one, another game's)."""
    from ...islands import gameterrain

    view = getattr(editor, "view", None)
    if view is None or not hasattr(view, "gamedata_dir"):
        return None
    paths = gameterrain.page_files(view)
    pages = PageFiles(paths, lambda path: terrainpages.PageParts(*gameterrain.read_page(view.files.read(path))))
    meshes = {level: gameterrain.stored_meshes(view, level) for level in range(terrainnodes.MIN_LEVEL, 13)}
    tints = gameterrain.page_files(view, "tintmaps", ".dds")
    return terraindraw.IslandSource(pages, meshes, tints, view.files.read)


def edited_blocks(heights: np.ndarray, base: np.ndarray, step: float) -> np.ndarray:
    """Per block of the finest nodes whether its heights differ from the island file's (those draw from the view's
    heights: the file's pages and meshes do not hold the edit)."""
    _lo, hi = block_ranges(np.abs(heights - base), step, 2.0**terrainnodes.MIN_LEVEL)
    return hi > 1e-4


def shadow_stages(source) -> dict:
    """The terrain's shadow techniques and the cascades' analysis; the tessellated technique and the analysis None
    when they do not compile (the nodes then cast untessellated, the CPU fits the cascades)."""
    out = {"plain": source.stage_set(terraindraw.SHADOW)}
    for key, make in (("detailed", lambda: source.stage_set(terraindraw.SHADOW_DETAILED)), ("fit", source.shadow_fit)):
        try:
            out[key] = make()
        except Exception:  # said by its absence: no tessellated shadow, the CPU's fit
            out[key] = None
    return out


def native_shaders(game, compiler):
    """The shipped shaders of the native backend's terrain parts (``render.terrainnative``, ``pagesnative``,
    ``bakenative``) read and reflected, in the ``NativeShaders`` that keep them; None where none draws natively
    (switched off, no module). A part whose shaders do not read raises again when it is made, which says why."""
    from ...render import bakenative, pagesnative

    parts = [p for p in (terrainnative, pagesnative, bakenative) if p.enabled()]
    if not parts:
        return None
    from ...models import shadernative

    natives = shadernative.NativeShaders(game, compiler)
    for part in parts:
        try:
            part.shaders(natives)
        except Exception:  # that part alone draws the earlier way (``GameIsland._make``)
            continue
    return natives


def frame_planes(camera) -> tuple[float, float]:
    """The near and far plane of ``render.camera.OrbitCamera.proj``: the composite writes the view's depth by them."""
    return max(0.1, camera.distance / 500), max(4000.0, camera.distance * 8)


class GameIsland(IslandBake):
    """The island's frame by the game's own shaders (module docstring)."""

    # how the view's final pass takes ``finish``'s picture (``FramePasses._finish``, the final shader's modes): 3 linear
    # light, encoded by sRGB there; 4 encoded already (a look whose chain ends encoded skips its decode)
    FINISH_MODE = 3

    def __new__(cls, renderer):
        """The look of the island's game where it has one of its own (``LOOKS``)."""
        if cls is GameIsland:
            from importlib import import_module

            from ..editsession import game_of

            found = LOOKS.get(game_of(getattr(renderer.session, "island", None) or {}))
            if found is not None:
                cls = getattr(import_module(f"{__package__}.{found[0]}"), found[1])
        return super().__new__(cls)

    def __init__(self, renderer) -> None:
        self.r = renderer
        self.frame: GameFrame | None = None
        self.terrain: terraindraw.Terrain | None = None
        self.shading: DeferredShading | None = None
        self.sky = None  # gamesky.SkyPasses: the sky's light by the game's sky passes (None: the look's)
        self.fit = None  # shadowfit.ShadowFit: the sun's cascades fitted by the game's analysis (None: on the CPU)
        self.pages: terrainpages.Pages | None = None
        self.lanes: list[dict] = []  # the view's own bake: per node baked in a frame its targets (``_lane``)
        self.composites: dict = {}  # its encode passes by target (``ENCODES``)
        self.arrays: dict = {}  # what it goes into by the shader's name (``STORES``)
        self._bake_key: tuple | None = None
        self.bake: terrainbake.Bake | None = None  # the game's bake, once its shaders and the island's ground are in
        self._bake_stages: dict | None = None
        self._baked_with: dict = {}  # node -> (stored, page, its textures' id) its slice was baked with
        self._own_rects: dict = {}  # node -> the rectangle the view's own bake drew its slice over
        self._unbaked = 0  # shown nodes the game's bake has still to do or did this frame
        self._painted: PaintedMaps | None = None  # the session's strokes in the bake's material maps
        self._heights = None  # (game heights or not) the terrain was last made from
        self._ranges = None  # the heights' range per block of the finest nodes (``_bounds``)
        self._pending = Change()
        self._coarse = False  # the coarse heights were handed to the pages
        self._cascades = None  # gameshadow.Cascades read back from the game's analysis (casters culled by them)
        self._cascades_for = self._asked_for = None  # the camera they were fitted for, the one of the read in flight
        self._late = False  # this frame's camera is not the one ``_cascades`` were fitted for
        self.animated = False  # the frame moves with the view's clock or still settles: the view draws on
        self.error = ""
        self.source = None  # models.shaderview.ShaderSource: the install's shaders, once read (the objects compile)
        self.quality = None  # render.quality.Quality the frame was made for (None: the best)
        # whether the shaders were not in the build cache (a first start; None: not known yet): the view's own look
        # draws while they compile (island-117-view.md "Speed", part 6)
        self.cold: bool | None = None
        self.extras = None  # render.gameextras.FrameExtras: the water's copies, the visible sky, the post chain
        self.sea_waiting = False  # the game's sea compiles or its maps load (``_sea``)
        self._coast_edited, self._coast_at = False, 0.0  # heights edited since the sea's coast was made, and when
        self.inland_waiting = False  # the game's rivers and lakes compile or the file's flow and look load
        self.native = None  # render.terrainnative.NativeTerrain: the terrain by the shipped shaders (None: the rewrite)
        # the native backend's device over QRhi's and its textures handed to QRhi (the pages' and the bake's); the
        # current path's page shaders (the native pages' fallback) and the shipped ones
        self._device = self._handover = None
        self._page_stages: dict | None = None
        self._natives = None
        self._inland_key: tuple | None = None  # the lines and heights their bodies were made from
        from .desktopgamedecals import GameDecals
        from .desktopgameground import GroundMaps
        from .desktopgameobjects import GameObjects

        self.objects = GameObjects(self)
        self.decals = GameDecals(self)
        self.ground = GroundMaps(self)  # the coarse displacement and the grit the models and the terrain read
        renderer.session.listeners.append(self.on_change)

    def on_change(self, change: Change) -> None:
        p = self._pending
        for name in ("heights", "weights"):
            box = getattr(change, name)
            if box is not None:
                setattr(p, name, union_box(getattr(p, name), box))
        self._coast_edited |= change.heights is not None

    def why_not(self) -> str:
        """Why the look cannot draw here ("" when it can, or while it compiles)."""
        ctx, r = self.r.ctx, self.r
        if self.error:
            return self.error
        if not hasattr(ctx, "native_pass") or ctx.rhi.backendName() != BACKEND:
            return tr("common.games_own_shaders_draw_direct3d")
        if r.editor is None or not hasattr(r.editor, "files"):
            return tr("common.games_own_shaders_need_files")
        got = r.loader.get(JOB, self._compile)
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

        game = getattr(self.r.editor.files, "game", self.r.editor.files)  # the install's, not a mod folder's
        compiler = dxc.Dxc.find(anno117=install_folder(game))
        source = self.source = shaderview.shared(game, compiler, frame_instances=True)  # (gameinstances)
        try:
            self.cold = not source.cached(terraindraw.STAGES)  # a first start: the view's look draws meanwhile
        except Exception:  # the sources do not read: the compile below says why
            self.cold = True
        flip = (terrainbake.FLIP_HLSL, terrainbake.FLIP_ENTRY)
        makes = (source.sky_passes, lambda: source.stage_set(terraindraw.DETAILED),
                 lambda: source.stage_set(terrainpages.STAGES),
                 lambda: {**source.stage_set(terrainbake.STAGES), "flip": source.own_stage(*flip)},
                 lambda: shadow_stages(source), lambda: native_shaders(game, compiler))  # fmt: skip
        with ThreadPoolExecutor(len(makes) + 2, thread_name_prefix="view-data-base") as pool:  # side by side
            needed = [pool.submit(lambda: source.stage_set(terraindraw.STAGES)), pool.submit(source.shading)]
            optional = [pool.submit(make) for make in makes]
            terrain, shading = (f.result() for f in needed)
        # what may fail alone: the terrain draws all the same, by the look's sky light, untessellated
        return source, terrain, shading, *(f.exception() or f.result() for f in optional)

    def _adopt(self, got: tuple, quality) -> None:
        """The frame's parts made from the compiled shaders ``got`` (``_compile``'s) for ``quality``. Their source
        comes with them: a look made again for another quality did not compile them itself, and without the source
        its objects, decals, water and post chain were never made."""
        self.quality = quality  # what the frame is made for (a change makes it anew)
        self.source = got[0]
        self._make(*got[1:])

    def compiled(self) -> bool:
        """The shaders are in (``why_not`` said nothing against them): ``draw`` draws."""
        return isinstance(self.r.loader.get(JOB, self._compile), tuple)

    def busy(self) -> int:
        if self.why_not():
            return 0
        if not isinstance(self.r.loader.get(JOB, self._compile), tuple):
            return 1
        if self._bake_stages is not None and self.bake is None and self.r.loader.get(GROUND_JOB, self._ground) is None:
            return 1  # the island's ground for the game's bake
        if self.objects.waiting or self.objects.ahead_left or self.decals.waiting:
            return 1  # the objects' or decals' materials still compiling, or parts within reach to be made ahead
        if self.sea_waiting or self.inland_waiting or (self._coast_edited and self.r.game_sea):
            return 1  # the sea's or the rivers' shaders, the island's water maps or an edited coast
        return 1 if (self.pages is not None and self.pages.busy()) or self._unbaked else 0

    def draw(self, f, opts, target, size: tuple[int, int], camera) -> bool:
        """The terrain into the G-buffer, lit, composited into ``target`` with its depth; False when it is not
        drawn (the view's own ground then)."""
        if self.why_not():
            return False
        got = self.r.loader.get(JOB, self._compile)
        if not isinstance(got, tuple):
            return False
        ctx = self.r.ctx
        if self.terrain is None:
            self._adopt(got, getattr(opts, "quality", None))
        self._take_failures()  # the last frame's, before this frame's nodes are picked
        if self.error:
            return False
        self._sync_heights(f)
        terrain, s = self.terrain, self.r.session
        self._sync_files(f)
        e = camera.eye()
        eye = (s.size - float(e[0]), float(e[1]), float(e[2]))  # the view's x is mirrored
        models = terrain.nodes(eye, self._visible(f.planes))
        q = getattr(opts, "quality", None)  # this frame's: what the look was made with is ``self.quality``
        self.frame.set_quality(q)  # its shadow cascades (the map's side is in ``quality.MADE_WITH``)
        shadows = getattr(opts, "shadows", True) and (q is None or q.shadows)
        ground = self.ground.ground(self.pages.coarse if self.pages is not None else None)
        self.objects.adapt = ground is not None  # without the session's terrain no model adapts to it
        objects = self.objects.frame(f, opts, shadows, size, self._seen_cascades(f, size) if shadows else None)
        self.r.game_parts = self.objects.drawn
        decals = self.decals.frame(opts, ChainMap(self.objects.neutral or {}, terrain.textures)) if ground else []
        self.r.game_decals = bool(decals)  # the view's own decals while the game's compile (or failed)
        ctx.native_pass(lambda cb, batch: carry(cb, batch, lambda b: (terrain.uploads(b), self.objects.upload(b))))
        if self.pages is not None:
            self.pages.run(ctx)
        if self._game_bake():
            self._bake_game(models)
        else:
            self._bake_own(opts, [m.node for m in models])
        models = terrain.shown(models)  # a node not baked yet: the nodes drawn before over its square
        lt = f.lt
        sun, sun_color = tuple(lt["sunDir"]), tuple(lt["sunColor"])
        sh = tuple(lt.get("sh", [(0.0, 0.0, 0.0)] * 9))
        light = Light(sun, sun_color, tuple(c * lt.get("shFactor", 1.0) * 0.886 for c in sh[0]))
        sky = Sky(sun, sun_color, sh, lt.get("shFactor", 1.0))
        shading = (self.shading, sky) if not self.shading.error else None
        passes = Passes(sky=self.sky if self.sky is not None and not self.sky.error else None, ground=ground)
        passes.transparent = objects.transparent
        passes.sea = self._sea(opts, float(lt.get("uwTime", 0.0))) if shading is not None else None
        passes.inland = self._inland(opts, float(lt.get("uwTime", 0.0))) if shading is not None else None
        if shading is not None:  # the water's copies, the sea, the visible sky, haze and clouds (phases 6 and 7)
            self.extras.passes(passes, passes.sky, float(lt.get("uwTime", 0.0)), ctx, bool(passes.transparent))
            self._fog(passes.atmosphere)
        near, far = frame_planes(camera)  # with clouds the rays reach their own far plane (frame-117-viewer.md 12)
        cam = self.frame.camera(size, e, camera.target, camera.fov, near, far, self.extras.far())
        native = self._terrain_passes(passes, models, objects.casters, shading is not None and shadows)
        # the ground stage's order: the terrain, the cut-outs clearing its depth, the decals, the models
        drawn = (models if native is None else []) + objects.cutouts + decals + objects.gbuffer
        self.frame.render(target, size, drawn, cam, light, finish=False, shading=shading, passes=passes)
        self._end_native(native)
        self.r.stats["terrain_nodes"] = len(models)
        native_casters = len(passes.native.caster_list) if passes.native is not None else 0  # the terrain's
        self.r.stats["terrain_casters"] = len(passes.casters) + native_casters
        self.r.stats["game_objects"], self.r.stats["game_object_draws"] = objects.instances, len(objects.gbuffer)
        self.r.stats["game_object_levels"] = objects.levels
        self.r.stats["game_decals"] = sum(d.count for d in decals)
        for how in ("detailed", "stored"):
            self.r.stats[f"terrain_{how}"] = sum(m.kind[0] == how for m in models)
        clock = getattr(opts, "particle_time", None) is None  # the water and the clouds move with the view's clock
        moving = clock and any(p is not None for p in (passes.sea, passes.inland, passes.clouds))
        fitted = shadows and self._late and self.frame.cascades is not None and self.frame.cascades.gpu  # read back
        settling = self._unbaked or (self.pages is not None and self.pages.busy()) or fitted
        self.animated = bool(moving or settling)
        return True

    def _end_native(self, native) -> None:
        """The frame's end for the native parts (``native``: the terrain's, None when the current path drew it):
        the device's frame ended once for all of them (their late releases count in it), which ways drew."""
        if native is not None:
            native.end_frame()
        if self._device is not None:
            self._device.end_frame()
        self.r.stats["terrain_native"] = int(native is not None)
        self.r.stats["pages_native"] = int(getattr(self.pages, "native", False))
        self.r.stats["bake_native"] = int(getattr(self.bake, "native", False))

    def _terrain_passes(self, passes, models: list, object_casters: list, shadows: bool):
        """The frame's shadow casters and fit, and the terrain's way: drawn first in the G-buffer's and the shadow's
        passes by the shipped shaders (``terrainnative``: returned), or among the current path's models (None)."""
        native = self.native if self.native is not None and not self.native.error else None
        casters = []
        if shadows:
            casters = self.terrain.casters(models)
            passes.casters, passes.box = (casters if native is None else []) + object_casters, self._box()
            passes.fit = self.fit if self.fit is not None and not self.fit.error else None
        if native is not None:
            passes.native = terrainnative.FrameDraws(native, models, casters, passes.ground, self.terrain.detailed)
        return native

    def finish(self, image, size: tuple[int, int], lut):
        """The game's grade and tone map of the view's resolved colour (phase 8; ``lut`` the ambient's grade cube or
        None): the tone-mapped texture, None while the post chain compiles or when it does not."""
        if self.extras is None or self.shading is None or self.shading.error:
            return None
        noise = self.shading.fixed.get("cBlueNoiseTex")
        return self.extras.finish(self.r.ctx, image, size, lut.native if lut is not None else None, noise)

    def _sea(self, opts, seconds: float):
        """The sea by the game's ocean shader with this island's maps (phase 6, step 2), the view's own sea left out
        where it draws; None while it compiles or its maps load (``sea_waiting``: the view is busy), when it does
        not, and without the sea layer."""
        s, extras = self.r.session, self.extras
        self.sea_waiting = False
        if not (opts.layers.get("water", True) and s.island.get("sea", True)) or extras is None:
            return None
        sea, water = extras.sea(), extras.water()  # it reads the scene's copies
        if sea is None or water is None:
            self.sea_waiting = all(extras.made.get(k) is not False for k in ("sea", "water"))
            return None
        if not sea.size:
            maps = self.r.loader.get(SEA_JOB, lambda: sea_maps(self.r.editor))
            if maps is None:
                self.sea_waiting = True
                return None
            if isinstance(maps, Exception):  # the maps do not read: the sea as open sea
                maps = (b"", b"")
            coast = self.r.loader.get(COAST_JOB, lambda: coast_for(maps[0], s.heights.copy(), float(s.size)))
            if coast is None:  # the distance pass runs off the window's thread
                self.sea_waiting = True
                return None
            sea.set_island(
                maps[0], s.heights, float(s.size), maps[1], coast if not isinstance(coast, Exception) else None
            )
            self._coast_edited, self._coast_at = False, time.monotonic()
        elif self._coast_edited and time.monotonic() - self._coast_at >= COAST_SECONDS:
            sea.set_coast(s.heights)  # the shore's foam and fade follow an edited coast, at most once a second
            self._coast_edited, self._coast_at = False, time.monotonic()
        if sea.waves_error:  # the sea draws on without its coast waves: said once
            self.r._message(tr("islands.games_coast_waves", reason=sea.waves_error[:300]))
            sea.waves_error = ""
        sea.seconds = seconds
        self.r.game_sea = not sea.error
        return sea if not sea.error else None

    def _fog(self, atmosphere) -> None:
        """The island's fog map handed to the visible sky's passes once it is read (phase 7), and the session's
        heights it stands on; an island file without one: no fog."""
        if atmosphere is None:
            return
        atmosphere.heights = self.pages.coarse if self.pages is not None else None
        if atmosphere.fog_size != float(self.r.session.size):
            raw = self.r.loader.get(FOG_JOB, lambda: self.r.editor.view.rd.path("Fog").data)
            if raw is not None:
                atmosphere.set_fog(raw if isinstance(raw, bytes) else b"", float(self.r.session.size))

    def _inland(self, opts, seconds: float):
        """The rivers and lakes by the game's inland water shader along the session's lines (phase 6, step 4), the
        view's own left out where they draw; None without any, while it compiles or the file's flow and look load
        (``inland_waiting``: the view is busy), when it does not, and without the water layer."""
        s, extras = self.r.session, self.extras
        self.inland_waiting = False
        # every river and lake line, the short ones too: the file's flow and look are matched to them by place
        lines = [sp for sp in s.island.get("splines", []) if sp["kind"] in ("River", "Lake")]
        if not opts.layers.get("water", True) or extras is None or all(len(sp["points"]) < 2 for sp in lines):
            return None
        inland = extras.inland("egypt" in str(getattr(self.r.editor, "a7m_path", "")).lower())
        found = self.r.loader.get(INLAND_JOB, lambda: self.r.editor.view.inland_waters())
        if inland is None or found is None:
            self.inland_waiting = extras.made.get("inland") is not False
            return None
        coarse = self.pages.coarse if self.pages is not None else None
        key = (json.dumps(lines), id(coarse))  # the lines edited, the session's heights come in: the flow again
        if key != self._inland_key:
            inland.set_bodies(gameinland.bodies(lines, [] if isinstance(found, Exception) else found, triangulate),
                              float(s.size))  # fmt: skip
            self._inland_key = key
        inland.session_ms = getattr(opts, "season_ms", None)  # Egypt's season cycle (``gameinland.SeasonCycle``)
        inland.run(self.r.ctx, seconds, coarse)
        self.r.game_inland = not inland.error
        return inland if not inland.error else None

    def _seen_cascades(self, f, size: tuple[int, int]):
        """The sun's cascades as last known on the CPU (the casters are culled by them): fitted there, or read back
        from the game's analysis a frame or two ago (one read in flight at a time); None before the first.
        ``_late``: they were fitted for another camera than this frame's (the view draws on until they are not)."""
        fit = self.fit if self.fit is not None and not self.fit.error else None
        self._late = False
        if fit is None:
            cascades = self.frame.cascades if self.frame is not None else None
            return cascades if cascades is not None and not cascades.gpu else None
        seen = (np.asarray(f.view_proj).tobytes(), tuple(size))
        if fit.readbacks and fit.readbacks[1].data.size() >= gameshadow.INFO_BYTES * gameshadow.COUNT:
            self._cascades = gameshadow.read_info(bytes(fit.readbacks[1].data), np.zeros(3), 0.0)
            self._cascades_for, fit.readbacks = self._asked_for, []
        if not fit.readbacks:
            fit.read_back, self._asked_for = True, seen
        self._late = self._cascades_for != seen
        return self._cascades

    def _sync_files(self, f) -> None:
        """The island files' geometry for the nodes when the view shows the game's terrain: the pages and meshes
        read once in the background, the coarse heights uploaded once (the file's: an edited block draws from the
        view's heights)."""
        terrain, s = self.terrain, self.r.session
        got = self.r.loader.get(FILES_JOB, lambda: island_source(self.r.editor))
        if isinstance(got, Exception):
            self.r._message(tr("islands.islands_height_pages", reason=f"{type(got).__name__}: {got}"[:300]))
        source = got if isinstance(got, terraindraw.IslandSource) else None
        if source is not None and terrain.source is None and terrain.heights is not None:
            terrain.set_heights(terrain.heights, None)  # the nodes made before it came read its tint pages now
        terrain.source = source
        terrain.files_shown = f.game_heights is not None
        if self.pages is not None and terrain.source is not None and self.pages.coarse is None and not self._coarse:
            self.pages.set_coarse(s.base_heights, s.step)
            self._coarse = True

    def _visible(self, planes):
        """Whether a node's box (its heights' range) is in view (None: cull off, all are)."""
        if planes is None:
            return None
        s = self.r.session
        bounds = self._bounds

        def visible(node) -> bool:
            lo, hi = bounds(node)
            box_lo = np.array([[s.size - node.x - node.tiles, lo, node.z]])
            box_hi = np.array([[s.size - node.x, hi, node.z + node.tiles]])
            return bool(boxes_in_view(planes, box_lo, box_hi)[0])

        return visible

    def _box(self) -> tuple[np.ndarray, np.ndarray]:
        """The island's bounds in the view's axes: what the sun's cascades are fitted to on the CPU."""
        lo, hi = self._block_ranges()
        size = float(self.r.session.size)
        return np.array([0.0, float(lo.min()), 0.0]), np.array([size, float(hi.max()), size])

    def _block_ranges(self) -> tuple[np.ndarray, np.ndarray]:
        """The heights' range per block of the finest nodes, made once per height change."""
        if self._ranges is None:
            self._ranges = block_ranges(self.terrain.heights, self.r.session.step, 2.0**terrainnodes.MIN_LEVEL)
        return self._ranges

    def _bounds(self, node) -> tuple[float, float]:
        """The lowest and highest height under a node (from ``_block_ranges``)."""
        lo, hi = self._block_ranges()
        per = 2**terrainnodes.MIN_LEVEL
        i0, j0 = int(node.x // per), int(node.z // per)
        n = max(1, int(node.tiles // per))
        return float(lo[j0 : j0 + n, i0 : i0 + n].min()) - 0.5, float(hi[j0 : j0 + n, i0 : i0 + n].max()) + 2.5

    def _take_failures(self) -> None:
        """A node that did not prepare in the last frame, or a pass that raised, turns the look off (said once):
        the view's own ground draws again (docs/viewer.md "When a pass fails"); a tessellated node, or the pages'
        compute shaders, turn only themselves off (the nodes then draw untessellated, from the view's heights)."""
        ctx = self.r.ctx
        self.objects.take_failures()  # an object's draw: its part to the view's look
        if self.native is not None and self.native.error:  # the terrain by the current path from now on, said once
            self.r._message(tr("islands.games_native_terrain", reason=self.native.error[:300]))
            self.native.release()
            self.native = None
        self.decals.take_failures(ctx.failed_models)  # a decal's draw: the view's own decals from now on
        made = self.extras.made if self.extras is not None else {}
        from ...render.gameextras import N_INLAND, N_SEA

        for key, said in (("sea", N_SEA), ("inland", N_INLAND)):
            why = made[key].take_failure(ctx.failed_models) if made.get(key) else ""
            if why:  # that part alone: the view's own water from now on, said once
                self.r._message(tr(said, reason=why[:300]))
        mine = [(m, e) for m, e in ctx.failed_models if isinstance(m, terraindraw.TerrainModel)]
        ctx.failed_models[:] = [x for x in ctx.failed_models if all(x is not y for y in mine)]
        terrain = self.terrain
        patches = [x for x in mine if x[0].program is terrain.detailed]
        if patches:  # the tessellated nodes alone: they draw untessellated from now on, said once
            terrain.detailed = None
            self.r._message(tr("islands.games_tessellated_terrain", reason=patches[0][1][:300]))
            terrain.set_heights(terrain.heights, None)
        if self.pages is not None and self.pages.error:
            native = getattr(self.pages, "native", False)  # the native pages: the current path's from now on
            said = "islands.games_native_pages" if native else "islands.games_height_pages"
            self.r._message(tr(said, reason=self.pages.error[:300]))
            self.pages.release()
            if native and getattr(self.bake, "native", False):  # it reads the native pages: the current path's too
                self._drop_native_bake(tr("islands.games_native_pages", reason=self.pages.error[:300]))
            self.pages = terrain.pages = self._new_pages(self._page_stages, native=False) if native else None
            self._coarse = False
            terrain.set_heights(terrain.heights, None)
        mine = [x for x in mine if all(x is not y for y in patches)]
        shadows = [x for x in mine if terrain.shadow is not None and x[0].program in terrain.shadow]
        if shadows:  # the casters alone: no terrain shadow from now on, said once
            terrain.shadow = None
            self.r._message(tr("islands.games_terrain_shadow", reason=shadows[0][1][:300]))
        mine = [x for x in mine if all(x is not y for y in shadows)]
        baking = [x for x in mine if self.bake is not None and x[0].program is self.bake.program]
        if self.bake is not None and self.bake.error and getattr(self.bake, "native", False):
            self._drop_native_bake(self.bake.error)  # the native bake alone: the current path's from now on
        elif baking or (self.bake is not None and self.bake.error):
            self._drop_bake(baking[0][1] if baking else self.bake.error)
        mine = [x for x in mine if all(x is not y for y in baking)]
        if mine:
            self.error = tr("common.games_terrain_shader", reason=mine[0][1][:300])
        if ctx.pass_errors:
            self.error = tr("common.games_frame_failed", reason=ctx.pass_errors[-1][:300])
            ctx.pass_errors.clear()

    def _make(self, stages: dict, shading, sky, detailed, pages, bake, shadow, native=None) -> None:
        from PySide6.QtGui import QRhi

        from ...render.gamedraw import Program
        from ...render.gameextras import FrameExtras
        from ...render.gamesky import SkyPasses

        s, ctx = self.r.session, self.r.ctx
        self.frame = GameFrame(ctx, stencil=True)  # the cut-outs' and the water's stencil bits
        self.frame.set_quality(self.quality)
        self.shading = DeferredShading(ctx.rhi, *shading)
        self.extras = FrameExtras(ctx.rhi, lambda: self.source, self.r.loader.get, self.r._message)
        said = {"sky": lambda why: tr("islands.games_sky_passes", reason=why),
                "detailed": lambda why: tr("islands.games_tessellated_terrain", reason=why),
                "bake": lambda why: tr("islands.games_ground_bake", reason=why),
                "shadow": lambda why: tr("islands.games_terrain_shadow", reason=why)}  # fmt: skip
        for key, got in (("sky", sky), ("detailed", detailed), ("bake", bake), ("shadow", shadow)):
            if isinstance(got, Exception):
                self.r._message(said[key](f"{type(got).__name__}: {got}"[:300]))
        if not isinstance(sky, Exception):
            self.sky = SkyPasses(ctx.rhi, *sky)
        tessellated = None
        wants = self.quality is None or self.quality.tessellation
        if isinstance(detailed, dict) and wants and ctx.rhi.isFeatureSupported(QRhi.Feature.Tessellation):
            tessellated = Program(detailed["vs"], detailed["ps"], hs=detailed["hs"], ds=detailed["ds"])
        self.terrain = terraindraw.Terrain(ctx.rhi, Program(stages["vs"], stages["ps"]), float(s.size), s.step,
                                           s.width, tessellated)  # fmt: skip
        self._page_stages = pages if isinstance(pages, dict) else None
        self._bake_stages = bake if isinstance(bake, dict) else None
        if isinstance(shadow, dict):
            self._make_shadow(shadow, tessellated is not None)
        self._make_native(native)
        self.pages = self.terrain.pages = self._new_pages(pages)

    def _make_native(self, natives) -> None:
        """The native backend on QRhi's device where the shipped shaders read, and the terrain by them
        (``terrainnative``); else, or when its pipelines are refused, the current path draws it (said once)."""
        if natives is None:
            return
        try:
            if isinstance(natives, Exception):
                raise natives
            from ...render import nativecompute, nativegpu

            self._device = nativegpu.attach(self.r.ctx.rhi)
            self._handover = nativecompute.Handover(self._device, self.r.ctx.rhi)
            self._natives = natives
            if terrainnative.enabled():
                self.native = terrainnative.NativeTerrain(self._device, natives, stencil=True)
        except Exception as exc:
            self.native = None
            self.r._message(tr("islands.games_native_terrain", reason=f"{type(exc).__name__}: {exc}"[:300]))

    def _new_pages(self, stages, native: bool = True):
        """The height pages by the shipped shaders on the native backend where they read (``pagesnative``), else by
        the current path's compile (``stages``; its error said); None without either."""
        from ...render import pagesnative

        s, ctx = self.r.session, self.r.ctx
        if native and self._handover is not None and pagesnative.enabled():
            try:
                return pagesnative.NativePages(self._device, self._natives, self._handover, float(s.size),
                                               self.terrain.island)  # fmt: skip
            except Exception as exc:
                self.r._message(tr("islands.games_native_pages", reason=f"{type(exc).__name__}: {exc}"[:300]))
        if isinstance(stages, Exception):
            self.r._message(tr("islands.games_height_pages", reason=f"{type(stages).__name__}: {stages}"[:300]))
        return (
            terrainpages.Pages(ctx.rhi, stages, float(s.size), self.terrain.island)
            if isinstance(stages, dict)
            else None
        )

    def _make_shadow(self, shadow: dict, tessellated: bool) -> None:
        """The terrain's shadow programs (the tessellated one for tessellated nodes) and the cascades' analysis."""
        from ...render.gamedraw import Program
        from ...render.shadowfit import ShadowFit

        plain, detailed = shadow["plain"], shadow["detailed"]
        patches = None
        if tessellated and detailed is not None:
            patches = Program(detailed["vs"], detailed["ps"], hs=detailed["hs"], ds=detailed["ds"])
        self.terrain.shadow = (Program(plain["vs"], plain["ps"]), patches)
        self.fit = ShadowFit(self.r.ctx.rhi, shadow["fit"]) if shadow["fit"] is not None else None

    def _sync_heights(self, f) -> None:
        """The terrain's heights: the game's near terrain with the edits when the view shows it, else the editor's;
        all nodes again when that choice changed, else those an edit touched."""
        s = self.r.session
        game = f.game_heights is not None
        heights = f.game_heights if game else s.heights
        box, self._pending.heights = self._pending.heights, None
        if self._heights != game:
            self._heights = game
            self.terrain.set_heights(heights, None)
        elif box is not None:
            self.terrain.set_heights(heights, box)
        else:
            return
        self._ranges = None
        self.terrain.edited = edited_blocks(s.heights, s.base_heights, s.step)

    def release(self) -> None:
        if self.on_change in self.r.session.listeners:
            self.r.session.listeners.remove(self.on_change)
        self._drop_own_bake()
        self.objects.release()
        self.decals.release()
        for part in (self.bake, self.terrain, self.pages, self.sky, self.fit, self.shading, self.frame, self.extras,
                     getattr(self, "native", None), self._handover):  # fmt: skip
            if part is not None:
                part.release()
        self.ground.release()
        self.bake = self.terrain = self.pages = self.sky = self.fit = self.shading = self.frame = self.extras = None
        self.native = self._device = self._handover = self._natives = None
        self._baked_with.clear()
        self._own_rects.clear()
        self._painted = None
        self._bake_key = self._heights = self._ranges = self._cascades = None
        self._coarse = False


def block_ranges(heights: np.ndarray, step: float, tiles: float) -> tuple[np.ndarray, np.ndarray]:
    """The lowest and highest height per block of ``tiles`` x ``tiles`` (its far edge included), [rows, columns]."""
    per = max(1, round(tiles / step))
    n = (heights.shape[0] - 1 + per - 1) // per
    side = n * per + 1  # the samples the blocks reach, the far edge's repeated past the grid (a slice clips there)
    h = np.pad(heights, ((0, max(0, side - heights.shape[0])), (0, max(0, side - heights.shape[1]))), mode="edge")
    h = h[:side, :side]

    def reduce(a: np.ndarray, op, axis: int) -> np.ndarray:
        """Per block along ``axis``: ``op`` over its ``per`` samples and the next block's first (its far edge)."""
        a = np.moveaxis(a, axis, 0)
        inner = op(a[: n * per].reshape(n, per, *a.shape[1:]), axis=1)
        return np.moveaxis(op(np.stack([inner, a[per::per][:n]]), axis=0), 0, axis)

    lo = reduce(reduce(h, np.min, 0), np.min, 1)
    hi = reduce(reduce(h, np.max, 0), np.max, 1)
    return lo.astype(float), hi.astype(float)
