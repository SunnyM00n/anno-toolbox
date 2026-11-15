"""The island view's look "The game's own shaders" for an Anno 1800 island (Direct3D 12 or 11): the terrain by 1800's own
terrain shader (``render.terrain1800``) - the island's stored meshes and tint pages where the view shows the game's
terrain - lit in its pixel shader by the island's ambient setting, drawn by the viewer's forward frame
(``render.frameforward``) and composited under what the view's own look draws over it (objects, water, markers).

``GameIsland1800(renderer)`` is a ``desktopgame.GameIsland`` (made for an 1800 island through ``desktopgame.LOOKS``):
the same ``draw`` / ``why_not`` / ``busy``, 117's node tree, heights and culling; docs/rendering/island-1800-2205-view.md.
"""

from __future__ import annotations

import json
from typing import NamedTuple

import numpy as np

from ...infra.i18n import tr
from ...render import (
    ambientforward,
    bake1800,
    frameforward,
    gameinland,
    gameshadow,
    inland1800,
    objects1800,
    ocean1800,
    pages1800,
    shadowforward,
    ssaoforward,
    terrain1800,
    terrainbake,
    terrainpages,
    waves1800,
)
from ...render import gameshader as gs
from ...render.fxcdraw import Dummies, SharedBuffers
from ...render.gamecompute import carry
from ...viewer.forwardpass import shadow_quality
from . import desktopfinish1800
from .desktopgame import GameIsland, frame_planes, install_folder, island_source, sea_maps
from .desktopgamebake import GROUND_JOB, island_ground
from .desktopgameobjects import GameObjects

# the FXC shaders as compiled: Direct3D 12 takes their DXBC too (island-1800-2205-view.md 7)
BACKENDS = ("D3D12", "D3D11")
JOB = "game-island-shaders-forward"
SOURCE_JOB = "game-island-files-forward"
DECAL_JOB = "game-island-baked-decal-maps-{}"  # the maps of a set of baked decals (``_bake_decals``)
# the air and water at their best: 0 turns the props' leaves and trunks into the polluted tints (functions_environment
# .fxh GetAirQuality) and the sea into its polluted colours [E: the engine's values on a clean island]
CLEAN = {"cAirQuality": 1.0, "cWaterQuality": 1.0}
# the island's ambient setting by a word of its path (the first that matches), else the game's temperate one [ours]
SETTINGS = {
    "anno1800": (("colony01", "south_america_caribic_01"), ("colony02", "colony_02"), ("arctic", "dlc03_01"),
                 ("moderate", "moderate_01")),
    "anno2205": (("arctic", "arctic_session01"), ("moon", "moon_session_01"), ("tundra", "tundra_session_01"),
                 ("earth", "moderate_session_01")),
}  # fmt: skip


class Listed:
    """The editor's files (``islandview.Files``) as the ambient reader walks them: ``read``, and ``under`` a folder
    (``Files.list``)."""

    def __init__(self, files) -> None:
        self.files = files

    def read(self, path: str) -> bytes:
        return self.files.read(path)

    def under(self, prefix: str) -> list[str]:
        listed = getattr(self.files, "under", None) or self.files.list
        return listed(prefix)


def setting_of(game: str, path: str) -> str:
    """The local ambient setting an island of ``game`` at ``path`` is lit by (``SETTINGS``)."""
    for word, name in SETTINGS.get(game, ()):
        if word in path.lower():
            return name
    return ambientforward.LOCALS.get(game, ambientforward.DEFAULT_LOCAL)


class LookParts(NamedTuple):
    """What ``GameIsland1800._compile`` makes in the background: the terrain's colour pass (stages, states), the
    island's light, the terrain's shadow pass (or why it did not compile), the tessellated terrain's parts, what the
    objects compile with, the ground bake's stages (or why), the sky and post chain's parts and files, the pre-pass's
    and ambient occlusion's parts."""

    stages: dict
    state: object
    light: ambientforward.Light
    shadow: tuple | str
    tessellation: dict
    objects: object
    bake: dict | str | None
    ending: tuple
    occlusion: dict


class GameIsland1800(GameIsland):
    """An 1800 island's frame by its own shaders (module docstring)."""

    GAME = "anno1800"
    SEA_JOB = "game-island-ocean-1800"
    INLAND_JOB = "game-island-inland-1800"
    # the view's own bake (``IslandBake``): the albedo encoded as the game's bake stores it (gamma YCC)
    LANE_TARGETS = (*GameIsland.LANE_TARGETS, ("encoded", "f1"))
    ENCODES = {"encoded": ("albedo", terrain1800.ENCODE_FS)}
    STORES = {"cBakedDiffTexArray": "encoded", "cBakedNormalTexArray": "normal"}
    ARRAY_FORMATS: dict = {}
    # each node into targets of its own: on Direct3D 12 a target drawn again in the same frame after an encode pass
    # read it left the earlier nodes' slices black (island-1800-2205-view.md 7)
    LANES = 32

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.objects = self._objects()
        self.cold = True  # the view's look draws everything in full while the shaders compile
        self.light: ambientforward.Light | None = None
        self.shared: tuple | None = None  # the frame's buffers of the colour pass and the casters', for every draw
        self.ending: desktopfinish1800.IslandFinish | None = None  # the sky, the cloud noise, the post chain
        self.ssao = None  # 1800's ambient occlusion over the frame's pre-pass (``ssaoforward.Ssao1800``), once made
        self.sea = None  # the sea by the game's shaders (1800: ``render.ocean1800``, 2205: ``sea2205``), once made
        self.sea_error = False  # it did not compile or read (said once): the view's own sea
        self.inland = None  # the rivers and lakes by the game's shaders (``render.inland1800``), once made
        self.inland_error = False  # they did not compile or draw (said once): the view's own
        self._inland_key: str | None = None  # the lines their bodies were made from
        self._inland_found: list = []  # the island file's rivers (``islandview.inland_water``)
        self._decal_key: tuple | None = None  # the shown objects the bake's decals were gathered from

    def _objects(self) -> GameObjects:
        """The island's objects: by 1800's model shaders (``desktopobjects1800``, island-1800-2205-view.md 12)."""
        from .desktopobjects1800 import Objects1800

        return Objects1800(self)

    def _object_source(self, files, compiler):
        """In the background: what the objects compile with (None: the view's look draws them)."""
        return objects1800.ObjectSource(files, compiler)

    def why_not(self) -> str:
        ctx, r = self.r.ctx, self.r
        if self.error:
            return self.error
        if not hasattr(ctx, "native_pass") or ctx.rhi.backendName() not in BACKENDS:
            return tr("the game's own shaders of this island draw on Direct3D 12 or 11 (View > Renderer)")
        if r.editor is None or not hasattr(r.editor, "files"):
            return tr("the game's own shaders need the game's files")
        got = r.loader.get(JOB, self._compile)
        if isinstance(got, Exception):
            return tr("the game's shaders do not compile: {reason}", reason=f"{type(got).__name__}: {got}"[:300])
        return ""

    def _game_source(self) -> terrain1800.TerrainSource:
        """In the background: the game's forward compile of its effects by the FXC its install ships."""
        from ...infra.fxc import Fxc

        files = self.r.editor.files
        return terrain1800.TerrainSource(files, Fxc.find(install_folder(getattr(files, "game", files))), self.GAME)

    def _compile(self) -> LookParts:
        """In the background: the look's parts (``LookParts``), each that may fail as the reason it did."""
        source = self._game_source()
        files, compiler = source.files, source.compiler
        stages, state = terrain1800.compile_terrain(files, compiler, self.GAME)
        try:
            shadow = terrain1800.compile_terrain(files, compiler, self.GAME, terrain1800.SHADOW)
        except Exception as exc:  # the colour pass draws on without it, said once (``_make``)
            shadow = f"{type(exc).__name__}: {exc}"
        path = getattr(self.r.editor, "a7m_path", "") or ""
        try:
            light = ambientforward.read_light(Listed(files), setting_of(self.GAME, path))
        except (OSError, KeyError, ValueError, SyntaxError):  # an install without the setting: the record's defaults
            light = ambientforward.Light()
        try:
            bake = self._bake_compile(files, compiler)
        except Exception as exc:  # the view's own bake draws the ground, said once (``_make``)
            bake = f"{type(exc).__name__}: {exc}"
        tessellation, objects = self._tessellation(source), self._object_source(files, compiler)
        read = desktopfinish1800.read_parts(files, light, self.GAME)
        read["wind"] = self._setting_part(desktopfinish1800.read_wind, desktopfinish1800.WIND_STRENGTH)
        ending = (desktopfinish1800.compile_parts(source, self.GAME), read)
        return LookParts(stages, state, light, shadow, tessellation, objects, bake, ending, self._occlusion(source))

    def _occlusion(self, source) -> dict:
        """In the background: the terrain's pre-pass techniques (``PREPASS`` and, where the near terrain is
        tessellated, ``PREPASS_DETAILED``) and the ambient occlusion's kernels (``ssaoforward``), each by key, or why
        it did not compile (an exception: said once, the frame then draws without the occlusion)."""
        files, compiler = source.files, source.compiler
        makers = {"prepass": lambda: terrain1800.compile_terrain(files, compiler, self.GAME, terrain1800.PREPASS),
                  "ssao": lambda: ssaoforward.compile_stages(source)}  # fmt: skip
        if terrain1800.TESSELLATED:
            makers["detailed"] = lambda: terrain1800.compile_terrain(files, compiler, self.GAME,
                                                                     terrain1800.PREPASS_DETAILED, True)  # fmt: skip
        return desktopfinish1800.each_made(makers)

    def _bake_compile(self, files, compiler) -> dict | None:
        """In the background: the game's ground bake (``bake1800``; None: the view's own bake draws the ground)."""
        return bake1800.compile_stages(files, compiler, self.GAME)

    def _tessellation(self, source) -> dict:
        """In the background: the tessellated near terrain's techniques (``DETAILED``, its shadow) and the height
        pages' compute shaders (``pages1800``), each by key, or why it did not compile (an exception: said once,
        the near nodes then stay the stored meshes and the view's heights)."""
        if not terrain1800.TESSELLATED:
            return {}
        files, compiler = source.files, source.compiler

        def detailed(technique: str):
            return lambda: terrain1800.compile_terrain(files, compiler, self.GAME, technique, True)

        makers = {"detailed": detailed(terrain1800.DETAILED), "shadow": detailed(terrain1800.SHADOW_DETAILED),
                  "pages": lambda: pages1800.compile_stages(source)}  # fmt: skip
        return desktopfinish1800.each_made(makers)

    def compiled(self) -> bool:
        return isinstance(self.r.loader.get(JOB, self._compile), tuple)

    def busy(self) -> int:
        if self.why_not():
            return 0
        if not self.compiled() or self.r.loader.get(SOURCE_JOB, self._source) is None or self.objects.waiting:
            return 1
        if self._bake_stages is not None and self.bake is None and self.r.loader.get(GROUND_JOB, self._ground) is None:
            return 1  # the island's ground for the game's bake
        settling = self.ending is not None and self.ending.again  # the exposure still moving: once more
        settling = settling or self.sea_waiting  # the sea or the rivers compile
        return int((self.pages is not None and self.pages.busy()) or bool(self._unbaked) or settling)

    def _ground(self) -> tuple:
        """In the background: the island's ground for the game's bake with the four maps 1800's reads and what only
        its bake reads (``_extras``)."""
        ground = island_ground(self.r.editor, bake1800.MAP_KINDS)
        return (None if ground is None else self._extras(ground),)

    def _extras(self, ground):
        """The ground with its material set's grit materials (``bake1800.read_grit``)."""
        view = self.r.editor.view
        try:
            text = view.files.read(view.material_set).decode("utf-8", "ignore")
        except (OSError, KeyError):  # a set that does not ship: no grit
            text = ""
        return bake1800.with_extras(ground, grit=bake1800.read_grit(text))

    def _new_bake(self, ground, size: int):
        """The game's own bake (``bake1800``) at the quality's bake size and largest map side, into the arrays."""
        q = self.quality
        return bake1800.Bake(self.r.ctx, self._bake_stages, ground, size, max_texture=q.max_texture if q else 0)

    def _bake_island(self) -> dict:
        """The island's values the bake reads (its matrix, size, nugget)."""
        return self.terrain.island

    def _bake_model(self, node, page):
        """A node's job of the game's bake: its height samples, and its page's textures when it draws with one."""
        t, island = self.terrain, self._bake_island()
        mesh = terrainbake.bake_mesh(t.heights, t.step, node, t.width, t.normals())
        mesh.elements = t.ELEMENTS  # the same bytes as the game's vertex names them (2205: its position)
        if page is None:
            return self.bake.job(node, mesh, island)
        height, normal = self.pages.ready[page]
        own = {"cHeightPageTex": height, "cNormalPageTex": normal, "cTerrainHeightTex": self.pages.coarse}
        return self.bake.job(node, mesh, island, terrainpages.page_params(page, t.size), own)

    def _bake_decals(self, opts) -> None:
        """The shown objects' baked decals (``bake1800.baked``) handed to the game's bake, the nodes under those that
        came, went or moved baked again, their maps read in the background; from then on the view's own decal layer
        leaves them out (``Renderer.baked_decal``). island-1800-2205-view.md 19."""
        from .desktopgamedecals import _decal_objects
        from .desktopobjects1800 import part_maps

        r, bake = self.r, self.bake
        if "decal" not in getattr(bake, "extra", {}) or r.library is None or r.editor is None:
            return
        # a config of decals alone has no parts (the island's rock cracks): shown all the same
        shown = [b for b in r._all_batches() if b.count and r._layer_on(b, opts) and b.mats is not None]
        placed = [(b.mats, b.key[1]) for b in shown if b.key[0] != "markers"] + _decal_objects(shown)
        key = tuple((b.key, b.version) for b in shown), tuple(r.library.parts(cfg) is None for _m, cfg in placed)
        if key != self._decal_key:
            found = bake1800.baked_decals([(m, r.library.decals(cfg)) for m, cfg in placed], float(r.session.size))
            names, files = sorted({p for d in found for p in d.maps.values()}), r.editor.files
            job = DECAL_JOB.format(hash(tuple(names)))
            got = r.loader.get(job, lambda: {n: part_maps(files, {"map": n}, self.GAME).get("map") for n in names})
            if got is None:  # the maps still read: the decals as they were
                return
            r.loader.forget(job)  # the files go once on the device
            maps = {n: v[1] for n, v in got.items() if v} if isinstance(got, dict) else {}
            for box in bake.set_decals(found, maps):
                self.terrain.cache.spoil(list(box))
            self._decal_key = key
        r.baked_decal = bake1800.baked

    def _make(self, got: LookParts) -> None:
        """The look on the device from its compiled parts; a part that failed is said once."""
        s, ctx = self.r.session, self.r.ctx
        self.light, self.source = got.light, got.objects
        self.ending = desktopfinish1800.IslandFinish(ctx, self.GAME, got.light, *got.ending, self.r._message)
        self._bake_stages = got.bake if isinstance(got.bake, dict) else None
        if isinstance(got.bake, str):
            self.r._message(tr("the game's ground bake: {reason}", reason=got.bake[:300]))
        self.frame = frameforward.ForwardFrame(ctx, cascaded=self.GAME == "anno1800")
        self.terrain = self._terrain(ctx, terrain1800.program(got.stages), got.state, float(s.size), s.step, s.width)
        self.shared = self.terrain.shared = (SharedBuffers(ctx), SharedBuffers(ctx))
        if isinstance(got.shadow, tuple):
            self.terrain.casting = (terrain1800.program(got.shadow[0]), got.shadow[1])
        else:
            self.r._message(tr("the game's terrain shadow: {reason}", reason=got.shadow[:300]))
        self._make_tessellated(got.tessellation)
        self._make_occlusion(got.occlusion)

    def _make_occlusion(self, got: dict) -> None:
        """The terrain's pre-pass and the ambient occlusion over it, where both compiled (else said once: the frame
        draws without it)."""
        for value in got.values():
            if isinstance(value, Exception):
                self.r._message(
                    tr("the game's ambient occlusion: {reason}", reason=f"{type(value).__name__}: {value}"[:300])
                )
        if not isinstance(got.get("prepass"), tuple) or not isinstance(got.get("ssao"), dict):
            return
        self.terrain.prez = (terrain1800.program(got["prepass"][0]), got["prepass"][1])
        if isinstance(got.get("detailed"), tuple) and self.terrain.detailed is not None:
            self.terrain.prez_detailed = (terrain1800.program(got["detailed"][0]), got["detailed"][1])
        self.ssao = ssaoforward.Ssao1800(self.r.ctx.rhi, got["ssao"])

    def _make_tessellated(self, got: dict) -> None:
        """The tessellated near terrain and its height pages where the device draws patches and the rendering
        quality asks for them (117's rule), else the near nodes stay as they were (section 5)."""
        from PySide6.QtGui import QRhi

        ctx, s = self.r.ctx, self.r.session
        said = {"detailed": lambda why: tr("the game's tessellated terrain: {reason}", reason=why),
                "pages": lambda why: tr("the game's height pages: {reason}", reason=why),
                "shadow": lambda why: tr("the game's terrain shadow: {reason}", reason=why)}  # fmt: skip
        for key, value in got.items():
            if isinstance(value, Exception):
                self.r._message(said[key](f"{type(value).__name__}: {value}"[:300]))
        wants = terrain1800.TESSELLATED and (self.quality is None or self.quality.tessellation)
        if not wants or not ctx.rhi.isFeatureSupported(QRhi.Feature.Tessellation):
            return
        if isinstance(got.get("detailed"), tuple) and isinstance(got.get("pages"), dict):
            self.terrain.detailed = terrain1800.program(got["detailed"][0])
            self.pages = self.terrain.pages = pages1800.Pages1800(ctx.rhi, got["pages"], float(s.size),
                                                                  self.terrain.island)  # fmt: skip
            if isinstance(got.get("shadow"), tuple):
                self.terrain.casting_detailed = (terrain1800.program(got["shadow"][0]), got["shadow"][1])

    def _terrain(self, ctx, program, state, size: float, step: float, width: int):
        return terrain1800.Terrain1800(ctx, program, state, size, step, width)

    def draw(self, f, opts, target, size: tuple[int, int], camera) -> bool:
        """The terrain into ``target`` with its depth; False when it is not drawn (the view's own ground then)."""
        if self.why_not():
            return False
        got = self.r.loader.get(JOB, self._compile)
        if not isinstance(got, tuple):
            return False
        ctx = self.r.ctx
        if self.terrain is None:
            self.quality = getattr(opts, "quality", None)
            self._make(got)
        self._take_failures()
        if self.error:
            return False
        self._sync_heights(f)
        self._sync_files(f)
        terrain, s = self.terrain, self.r.session
        e = camera.eye()
        models = terrain.nodes((s.size - float(e[0]), float(e[1]), float(e[2])), self._visible(f.planes))
        self._after_nodes()
        shadows = self._shadows(opts)
        for buffers in self.shared:  # filled anew by this frame's first draws
            buffers.begin()
        objects = self.objects.frame(f, opts, shadows, size)
        self.r.game_parts = self.objects.drawn
        ctx.native_pass(lambda cb, batch: carry(cb, batch, terrain.uploads))
        if self.pages is not None:
            self.pages.run(ctx)
        if self._game_bake():
            self._bake_decals(opts)
            self._bake_game(models)
        else:
            self._bake_own(opts, [m.node for m in models])
        models = terrain.shown(models)  # a node not baked yet: the nodes drawn before over its square
        near, far = frame_planes(camera)
        cam = gs.direct_camera(size, e, camera.target, camera.fov, near, far)
        values = ambientforward.light_values(self.light, tuple(f.lt["sunDir"]))
        values.update(ambientforward.scattering_values(self.light, float(e[1]), near, far), **CLEAN)
        maps = self._sky_and_clouds(f, target, size, cam, values)
        drawn = models + objects.gbuffer + objects.transparent + self._water(f, opts, e, values) + self._rivers(opts)
        used, cascades = self._cull_casters(drawn, cam, values) if shadows else (0, ())
        self.objects.place(objects, used, cascades)
        passes = frameforward.Passes(shadows=shadows, ssao=self._ambient(opts))
        self.frame.render(target, size, drawn, cam, values, maps, passes)
        self.r.stats["terrain_nodes"] = len(models)
        self.r.stats["game_objects"], self.r.stats["game_object_draws"] = objects.instances, len(drawn) - len(models)
        self.r.stats["game_object_levels"] = objects.levels
        self.r.stats["terrain_stored"] = sum(m.kind[0] == "stored" for m in models)
        self.r.stats["terrain_detailed"] = sum(m.kind[0] == "detailed" for m in models)
        self.animated = self._moving(opts)
        return True

    def _moving(self, opts) -> bool:
        """Whether the view draws on (``Renderer.animated``): the clouds, the sea and the rivers move with the view's
        clock, or the frame still settles (nodes to bake, pages to build, the exposure moving). The cascades are
        fitted in the frame itself (``_cull_casters``): none read back late."""
        clock = getattr(opts, "particle_time", None) is None
        moving = clock and (self.ending is not None or self.r.game_sea or self.r.game_inland)
        settling = (self.pages is not None and self.pages.busy()) or self._unbaked
        return bool(moving or settling or (self.ending is not None and self.ending.again))

    def _water(self, f, opts, eye, values: dict) -> list:
        """The sea by the game's own shaders where the water layer is shown (the view's own sea left out while it
        draws); none while it compiles or where it failed (said once)."""
        self.sea_waiting = False
        s = self.r.session
        if self.sea_error or not (opts.layers.get("water", True) and s.island.get("sea", True)):
            return []
        if self.sea is None:
            got = self.r.loader.get(self.SEA_JOB, self._sea_parts)
            if got is None:
                self.sea_waiting = True
                return []
            if isinstance(got, Exception):
                self.sea_error = True
                self.r._message(tr("the game's sea: {reason}", reason=f"{type(got).__name__}: {got}"[:300]))
                return []
            self.sea = self._new_sea(got)
            self.sea.set_session(*got[-1], float(s.size))
        self.sea.run(self.r.ctx, float(f.lt.get("uwTime", 0.0)))
        self.r.game_sea = True
        values.update(self.sea.frame_values())  # the setting's water in the frame's constants
        return self.sea.models(eye, values.get("cLgtDirection"))

    def _rivers(self, opts) -> list:
        """The rivers and lakes by the game's inland water shader along the session's lines, lit with the sea's maps
        (the view's own left out where they draw); none without the game's sea, while they compile or where they
        failed (said once). island-1800-2205-view.md 18."""
        from .desktopgeometry import triangulate

        lines = [sp for sp in self.r.session.island.get("splines", []) if sp["kind"] in ("River", "Lake")]
        if self.inland_error or self.sea is None or not lines or not opts.layers.get("water", True):
            return []
        if self.inland is None:
            got = self.r.loader.get(self.INLAND_JOB, self._inland_parts)
            if got is None:
                self.sea_waiting = True
                return []
            if isinstance(got, Exception):
                self._inland_failed(f"{type(got).__name__}: {got}")
                return []
            self.inland = inland1800.Inland(self.r.ctx, self.GAME, got[0], Dummies(self.r.ctx.rhi), self.shared[0])
            self._inland_found = got[1]
        key = json.dumps(lines)
        if key != self._inland_key:  # the lines edited: the bodies and the flow map again
            bodies = gameinland.bodies(lines, self._inland_found, triangulate)
            self.inland.set_bodies(bodies, float(self.r.session.size))
            self._inland_key = key
        self.inland.run(self.r.ctx, self.pages.coarse if self.pages is not None else None)
        self.r.game_inland = True
        return self.inland.models(self.sea)

    def _inland_parts(self) -> tuple:
        """In the background: the inland water's shaders and maps (``render.inland1800``) and the island file's
        rivers' flow speeds and looks (``islandview.inland_water``; none where the file holds none)."""
        stages = inland1800.compile_stages(self._game_source(), self.GAME)
        try:
            found = self.r.editor.view.inland_waters()
        except (AttributeError, KeyError, ValueError):
            found = []
        return stages, found

    def _inland_failed(self, why: str) -> None:
        """The rivers and lakes left to the view's own from now on, said once."""
        self.inland_error = True
        self.r._message(tr("the game's rivers and lakes: {reason}", reason=why[:300]))
        if self.inland is not None:
            self.inland.release()
            self.inland = None

    def _sea_parts(self) -> tuple:
        """In the background: the ocean's shaders, the setting's ``Water`` block, the maps and the small waves'
        volumes, the island's ``WaterInfos`` and ``WaterFlow`` (``render.ocean1800``; the session's maps last)."""
        files = self.r.editor.files
        stages = ocean1800.compile_stages(self._game_source())
        water = self._setting_part(ocean1800.read_water, ocean1800.Water())
        return stages, water, ocean1800.read_maps(files), waves1800.read_volumes(files), sea_maps(self.r.editor)

    def _setting_part(self, read, default):
        """A part of the island's local ambient setting by ``read`` of its text (its ``Water`` block, its wind;
        ``default``, the record's values, where it does not read)."""
        setting = setting_of(self.GAME, getattr(self.r.editor, "a7m_path", "") or "")
        try:
            raw = Listed(self.r.editor.files).read(f"{ambientforward.AMBIENT_DIR}local/{setting}.xml")
            return read(raw.decode("utf-8", "replace"))
        except (OSError, KeyError, ValueError, SyntaxError):
            return default

    def _new_sea(self, got: tuple):
        """The sea on the device from what ``_sea_parts`` read."""
        stages, water, maps, volumes, _session = got
        ctx = self.r.ctx
        return ocean1800.Ocean1800(ctx, stages, water, maps, volumes, Dummies(ctx.rhi), self.shared[0])

    def _sky_and_clouds(self, f, target, size: tuple[int, int], cam, values: dict) -> dict:
        """The setting's sky cubes and cloud noise (the clouds drifting by the view's clock) for the frame, the game's
        sky drawn over the view's own, and the view's own light turned into the game's units for its post chain
        (``desktopfinish1800``): the frame's maps."""
        ending = self.ending
        self.r.ctx.native_pass(lambda cb, batch: carry(cb, batch, ending.uploads))
        values.update(ending.values(float(f.lt.get("uwTime", 0.0))))
        ending.draw_sky(target, size, cam, values)
        f.lt.update(ending.view_light(f.lt))
        return dict(ending.textures)

    def finish(self, image, size: tuple[int, int], lut):
        """The game's post chain of the view's resolved colour (its own grade: ``lut`` is 117's), as linear light;
        None without it."""
        q = self.quality
        return self.ending.finish(image, size, q is None or q.eye_adaptation) if self.ending is not None else None

    def _cull_casters(self, drawn: list, cam, values: dict) -> tuple[int, tuple]:
        """Each caster drawn only into the cascades it reaches (1800's GPU culling does so): the cascades the frame
        fits next over the same casters (``shadowforward.cascades``); how many it reads and their matrices' bytes
        ((0, ()) without casters)."""
        casters, frame = [m for m in drawn if m.shadow is not None], self.frame
        if not frame.cascaded or not casters or "cLgtDirection" not in values:
            return 0, ()
        boxes = np.concatenate([m.corners() for m in casters])
        size = frame.shadow_size or gameshadow.SIZE
        found = shadowforward.cascades(cam, values["cLgtDirection"], boxes, size, frame.shadow_cascades)
        used = int(found["cUsedShadowPartitions"])
        matrices = [found[f"cShadowInformation[{i}].ShadowMatrix"] for i in range(used)]
        casts = [m.shadow for m in casters if isinstance(m.shadow, objects1800.Cast)]
        objects1800.cull_casts(casts, matrices)  # the objects' at once
        for m in casters:
            if not isinstance(m.shadow, objects1800.Cast):
                m.shadow.cull(matrices)
        return used, tuple(np.asarray(m, np.float32).tobytes() for m in matrices)

    def _ambient(self, opts):
        """The ambient occlusion where it is made and the rendering quality and View > Rendering ask for it, else
        None (the pre-pass's targets let go)."""
        q, ssao = self.quality, self.ssao
        on = ssao is not None and not ssao.error and (q is None or q.ssao)
        if not on:
            self.frame.drop_prepass()
        return ssao if on else None

    def _shadows(self, opts) -> bool:
        """Whether the terrain casts into the sun's shadow (View > Rendering's switch and the rendering quality's,
        whose map side and cascades the frame takes): the terrain and the objects drawn by the game's shaders."""
        q = self.quality
        self.frame.shadow_size, self.frame.shadow_cascades = shadow_quality(q)
        return getattr(opts, "shadows", True) and (q is None or q.shadows)

    def _source(self) -> tuple:
        """In the background: the island's files the terrain draws from (``desktopgame.island_source``: the height
        pages' parts, the stored meshes, the tint pages) and, for the look that reads them, the session's normal
        pages by (level, x, z)."""
        return island_source(self.r.editor), {}

    def _files_shown(self, f) -> bool:
        """Whether the nodes draw the island's own meshes: where the view shows the game's terrain (117's rule)."""
        return f.game_heights is not None

    def _sync_files(self, f) -> None:
        """The island's files for the nodes once they are read (the nodes made again for their tint pages), and
        whether the view shows them."""
        terrain = self.terrain
        got = self.r.loader.get(SOURCE_JOB, self._source)
        if isinstance(got, Exception):
            self.r._message(tr("the island's terrain files: {reason}", reason=f"{type(got).__name__}: {got}"[:300]))
        elif got is not None and got[0] is not None and terrain.source is None:
            terrain.source = got[0]
            self._use_pages(got[1])
            terrain.remake_nodes()
            if self.pages is not None:  # the island's own heights under its pages (117's rule)
                self.pages.set_coarse(self.r.session.base_heights, self.r.session.step)
        terrain.files_shown = self._files_shown(f)

    def _use_pages(self, pages: dict) -> None:
        """The session's normal pages for the terrain (2205's look; 1800's are height pages, read with phase 2)."""

    def _after_nodes(self) -> None:
        """What the nodes picked this frame asked for (2205's look: the normal pages to read)."""

    def _take_failures(self) -> None:
        """A node that did not prepare, or a pass that raised, turns the look off (said once): the view's own
        ground draws again (docs/viewer.md "When a pass fails")."""
        ctx, terrain = self.r.ctx, self.terrain
        self.objects.take_failures()  # an object's draw: its part to the view's look
        why = self.inland.take_failure(ctx.failed_models) if self.inland is not None else ""
        if why:  # the rivers and lakes: the view's own from now on
            self._inland_failed(why)
        baking = [(m, e) for m, e in ctx.failed_models if isinstance(m, bake1800.NodeBake)]
        if baking:  # the game's bake: the view's own from now on, said once (117's rule)
            ctx.failed_models[:] = [x for x in ctx.failed_models if all(x is not y for y in baking)]
            if self.bake is not None:
                self._drop_bake(baking[0][1])
        mine = [(m, e) for m, e in ctx.failed_models if isinstance(m, terrain1800.TerrainNode)]
        ctx.failed_models[:] = [x for x in ctx.failed_models if all(x is not y for y in mine)]
        patches = [x for x in mine if x[0].kind[0] == "detailed"]
        broken = self.pages is not None and self.pages.error
        if patches or broken:  # the near nodes untessellated from now on, said once (117's rule)
            why = patches[0][1] if patches else self.pages.error
            self.r._message(tr("the game's tessellated terrain: {reason}", reason=why[:300]))
            if self.pages is not None:
                self.pages.release()
            self.pages = terrain.pages = terrain.detailed = None
            terrain.set_heights(terrain.heights, None)
            mine = [x for x in mine if all(x is not y for y in patches)]
        if mine:
            self.error = tr("the game's terrain shader: {reason}", reason=mine[0][1][:300])
        if ctx.pass_errors:
            self.error = tr("the game's frame failed: {reason}", reason=ctx.pass_errors[-1][:300])
            ctx.pass_errors.clear()

    def _rect(self, node) -> tuple:
        """A node's u and v span its square (the stored meshes too)."""
        return (node.x, node.z, node.tiles, node.tiles)

    def release(self) -> None:
        if self.ending is not None:
            self.ending.release()
            self.ending = None
        if self.ssao is not None:
            self.ssao.release()
            self.ssao = None
        if self.sea is not None:  # before the shared buffers its draws bind
            self.sea.release()
            self.sea = None
        if self.inland is not None:
            self.inland.release()
            self.inland = None
        super().release()
        for buffers in self.shared or ():  # after the draws that bind them
            buffers.release()
        self.shared = None
