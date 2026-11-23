"""The island view's look "The game's own shaders" for an Anno 1800 island (Direct3D 12 or 11): the terrain by 1800's own
terrain shader (``render.terrain1800``) - the island's stored meshes and tint pages where the view shows the game's
terrain - lit in its pixel shader by the island's ambient setting, drawn by the viewer's forward frame
(``render.frameforward``) and composited under what the view's own look draws over it (objects, water, markers).

``CGameIsland1800(renderer)`` is a ``desktopgame.CGameIsland`` (made for an 1800 island through ``desktopgame.LOOKS``):
the same ``draw`` / ``why_not`` / ``busy``, 117's node tree, heights and culling; docs/rendering/island-1800-2205-view.md.
"""

from __future__ import annotations

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
from ...render.fxcdraw import CDummies, CSharedBuffers
from ...render.gamecompute import carry
from ...viewer.forwardpass import shadow_quality
from . import desktopfinish1800
from .desktopgame import CGameIsland, frame_planes, install_folder, island_source, sea_maps
from .desktopgamebake import GROUND_JOB, island_ground
from .desktopgameobjects import CGameObjects

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


class CListed:
    """The editor's files (``islandview.CFiles``) as the ambient reader walks them: ``read``, and ``under`` a folder
    (``CFiles.list``)."""

    def __init__(self, files) -> None:
        self.m_files = files

    def read(self, path: str) -> bytes:
        return self.m_files.read(path)

    def under(self, prefix: str) -> list[str]:
        listed = getattr(self.m_files, "under", None) or self.m_files.list
        return listed(prefix)


def setting_of(game: str, path: str) -> str:
    """The local ambient setting an island of ``game`` at ``path`` is lit by (``SETTINGS``)."""
    for word, name in SETTINGS.get(game, ()):
        if word in path.lower():
            return name
    return ambientforward.LOCALS.get(game, ambientforward.DEFAULT_LOCAL)


class SLookParts(NamedTuple):
    """What ``CGameIsland1800._compile`` makes in the background: the terrain's colour pass (stages, states), the
    island's light, the terrain's shadow pass (or why it did not compile), the tessellated terrain's parts, what the
    objects compile with, the ground bake's stages (or why), the sky and post chain's parts and files, the pre-pass's
    and ambient occlusion's parts."""

    stages: dict
    state: object
    light: ambientforward.SLight
    shadow: tuple | str
    tessellation: dict
    objects: object
    bake: dict | str | None
    ending: tuple
    occlusion: dict


class CGameIsland1800(CGameIsland):
    """An 1800 island's frame by its own shaders (module docstring)."""

    GAME = "anno1800"
    SEA_JOB = "game-island-ocean-1800"
    INLAND_JOB = "game-island-inland-1800"
    # the view's own bake (``CIslandBake``): the albedo encoded as the game's bake stores it (gamma YCC)
    LANE_TARGETS = (*CGameIsland.LANE_TARGETS, ("encoded", "f1"))
    ENCODES = {"encoded": ("albedo", terrain1800.ENCODE_FS)}
    STORES = {"cBakedDiffTexArray": "encoded", "cBakedNormalTexArray": "normal"}
    ARRAY_FORMATS: dict = {}
    # each node into targets of its own: on Direct3D 12 a target drawn again in the same frame after an encode pass
    # read it left the earlier nodes' slices black (island-1800-2205-view.md 7)
    LANES = 32

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.m_objects = self._objects()
        self.m_cold = True  # the view's look draws everything in full while the shaders compile
        self.m_light: ambientforward.SLight | None = None
        self.m_shared: tuple | None = None  # the frame's buffers of the colour pass and the casters', for every draw
        self.m_ending: desktopfinish1800.CIslandFinish | None = None  # the sky, the cloud noise, the post chain
        self.m_ssao = None  # 1800's ambient occlusion over the frame's pre-pass (``ssaoforward.CSsao1800``), once made
        self.m_sea = None  # the sea by the game's shaders (1800: ``render.ocean1800``, 2205: ``sea2205``), once made
        self.m_sea_error = False  # it did not compile or read (said once): the view's own sea
        self.m_inland = None  # the rivers and lakes by the game's shaders (``render.inland1800``), once made
        self.m_inland_error = False  # they did not compile or draw (said once): the view's own
        self._m_inland_key: tuple | None = None  # the lines their bodies were made from
        self._m_inland_found: list = []  # the island file's rivers (``islandview.inland_water``)
        self._m_decal_key: tuple | None = None  # the shown objects the bake's decals were gathered from

    def _objects(self) -> CGameObjects:
        """The island's objects: by 1800's model shaders (``desktopobjects1800``, island-1800-2205-view.md 12)."""
        from .desktopobjects1800 import CObjects1800

        return CObjects1800(self)

    def _object_source(self, files, compiler):
        """In the background: what the objects compile with (None: the view's look draws them)."""
        return objects1800.CObjectSource(files, compiler)

    def why_not(self) -> str:
        ctx, renderer = self.m_renderer.m_ctx, self.m_renderer
        if self.m_error:
            return self.m_error
        if not hasattr(ctx, "native_pass") or ctx.m_rhi.backendName() not in BACKENDS:
            return tr("common.games_own_shaders_island_draw")
        if renderer.m_editor is None or not hasattr(renderer.m_editor, "m_files"):
            return tr("common.games_own_shaders_need_files")
        got = renderer.m_loader.get(JOB, self._compile)
        if isinstance(got, Exception):
            return tr("common.games_shaders_not_compile", reason=f"{type(got).__name__}: {got}"[:300])
        return ""

    def _game_source(self) -> terrain1800.CTerrainSource:
        """In the background: the game's forward compile of its effects by the FXC its install ships."""
        from ...infra.fxc import CFxc

        files = self.m_renderer.m_editor.m_files
        return terrain1800.CTerrainSource(files, CFxc.find(install_folder(getattr(files, "m_game", files))), self.GAME)

    def _compile(self) -> SLookParts:
        """In the background: the look's parts (``SLookParts``), each that may fail as the reason it did."""
        source = self._game_source()
        files, compiler = source.m_files, source.m_compiler
        stages, state = terrain1800.compile_terrain(files, compiler, self.GAME)
        try:
            shadow = terrain1800.compile_terrain(files, compiler, self.GAME, terrain1800.SHADOW)
        except Exception as exc:  # the colour pass draws on without it, said once (``_make``)
            shadow = f"{type(exc).__name__}: {exc}"
        path = getattr(self.m_renderer.m_editor, "m_a7m_path", "") or ""
        try:
            light = ambientforward.read_light(CListed(files), setting_of(self.GAME, path))
        except (OSError, KeyError, ValueError, SyntaxError):  # an install without the setting: the record's defaults
            light = ambientforward.SLight()
        try:
            bake = self._bake_compile(files, compiler)
        except Exception as exc:  # the view's own bake draws the ground, said once (``_make``)
            bake = f"{type(exc).__name__}: {exc}"
        tessellation, objects = self._tessellation(source), self._object_source(files, compiler)
        read = desktopfinish1800.read_parts(files, light, self.GAME)
        read["wind"] = self._setting_part(desktopfinish1800.read_wind, desktopfinish1800.WIND_STRENGTH)
        ending = (desktopfinish1800.compile_parts(source, self.GAME), read)
        return SLookParts(stages, state, light, shadow, tessellation, objects, bake, ending, self._occlusion(source))

    def _occlusion(self, source) -> dict:
        """In the background: the terrain's pre-pass techniques (``PREPASS`` and, where the near terrain is
        tessellated, ``PREPASS_DETAILED``) and the ambient occlusion's kernels (``ssaoforward``), each by key, or why
        it did not compile (an exception: said once, the frame then draws without the occlusion)."""
        files, compiler = source.m_files, source.m_compiler
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
        files, compiler = source.m_files, source.m_compiler

        def detailed(technique: str):
            return lambda: terrain1800.compile_terrain(files, compiler, self.GAME, technique, True)

        makers = {"detailed": detailed(terrain1800.DETAILED), "shadow": detailed(terrain1800.SHADOW_DETAILED),
                  "pages": lambda: pages1800.compile_stages(source)}  # fmt: skip
        return desktopfinish1800.each_made(makers)

    def compiled(self) -> bool:
        return isinstance(self.m_renderer.m_loader.get(JOB, self._compile), tuple)

    def busy(self) -> int:
        if self.why_not():
            return 0
        if (
            not self.compiled()
            or self.m_renderer.m_loader.get(SOURCE_JOB, self._source) is None
            or self.m_objects.m_waiting
        ):
            return 1
        if (
            self._m_bake_stages is not None
            and self.m_bake is None
            and self.m_renderer.m_loader.get(GROUND_JOB, self._ground) is None
        ):
            return 1  # the island's ground for the game's bake
        settling = self.m_ending is not None and self.m_ending.m_again  # the exposure still moving: once more
        settling = settling or self.m_sea_waiting  # the sea or the rivers compile
        return int((self.m_pages is not None and self.m_pages.busy()) or bool(self._m_unbaked) or settling)

    def _ground(self) -> tuple:
        """In the background: the island's ground for the game's bake with the four maps 1800's reads and what only
        its bake reads (``_extras``)."""
        ground = island_ground(self.m_renderer.m_editor, bake1800.MAP_KINDS)
        return (None if ground is None else self._extras(ground),)

    def _extras(self, ground):
        """The ground with its material set's grit materials (``bake1800.read_grit``)."""
        view = self.m_renderer.m_editor.m_view
        try:
            text = view.files.read(view.material_set).decode("utf-8", "ignore")
        except (OSError, KeyError):  # a set that does not ship: no grit
            text = ""
        return bake1800.with_extras(ground, grit=bake1800.read_grit(text))

    def _new_bake(self, ground, size_texels: int):
        """The game's own bake (``bake1800``) at the quality's bake size and largest map side, into the arrays."""
        quality = self.m_quality
        return bake1800.CBake(
            self.m_renderer.m_ctx,
            self._m_bake_stages,
            ground,
            size_texels,
            max_texture=quality.max_texture if quality else 0,
        )

    def _bake_island(self) -> dict:
        """The island's values the bake reads (its matrix, size, nugget)."""
        return self.m_terrain.m_island

    def _bake_model(self, node, page):
        """A node's job of the game's bake: its height samples, and its page's textures when it draws with one."""
        terrain, island = self.m_terrain, self._bake_island()
        mesh = terrainbake.bake_mesh(
            terrain.m_heights, terrain.m_step_tiles, node, terrain.m_width_samples, terrain.normals()
        )
        mesh.m_elements = terrain.ELEMENTS  # the same bytes as the game's vertex names them (2205: its position)
        if page is None:
            return self.m_bake.job(node, mesh, island)
        height, normal = self.m_pages.m_ready[page]
        own = {"cHeightPageTex": height, "cNormalPageTex": normal, "cTerrainHeightTex": self.m_pages.m_coarse}
        return self.m_bake.job(node, mesh, island, terrainpages.page_params(page, terrain.m_size_tiles), own)

    def _bake_decals(self, options) -> None:
        """The shown objects' baked decals (``bake1800.baked``) handed to the game's bake, the nodes under those that
        came, went or moved baked again, their maps read in the background; from then on the view's own decal layer
        leaves them out (``Renderer.baked_decal``). island-1800-2205-view.md 19."""
        from .desktopgamedecals import _decal_objects
        from .desktopobjects1800 import part_maps

        renderer, bake = self.m_renderer, self.m_bake
        if "decal" not in getattr(bake, "m_extra", {}) or renderer.m_library is None or renderer.m_editor is None:
            return
        # a config of decals alone has no parts (the island's rock cracks): shown all the same
        shown = [
            batch
            for batch in renderer._all_batches()
            if batch.m_count and renderer._layer_on(batch, options) and batch.m_mats is not None
        ]
        placed = [(batch.m_mats, batch.m_key[1]) for batch in shown if batch.m_key[0] != "markers"] + _decal_objects(
            shown
        )
        key = (
            tuple((batch.m_key, batch.m_version) for batch in shown),
            tuple(renderer.m_library.parts(cfg) is None for _mats, cfg in placed),
        )
        if key != self._m_decal_key:
            found = bake1800.baked_decals(
                [(mats, renderer.m_library.decals(cfg)) for mats, cfg in placed], float(renderer.m_session.m_size_tiles)
            )
            names, files = (
                sorted({map_name for baked in found for map_name in baked.maps.values()}),
                renderer.m_editor.m_files,
            )
            job = DECAL_JOB.format(hash(tuple(names)))
            got = renderer.m_loader.get(
                job, lambda: {name: part_maps(files, {"map": name}, self.GAME).get("map") for name in names}
            )
            if got is None:  # the maps still read: the decals as they were
                return
            renderer.m_loader.forget(job)  # the files go once on the device
            maps = {name: part[1] for name, part in got.items() if part} if isinstance(got, dict) else {}
            for box in bake.set_decals(found, maps):
                self.m_terrain.m_cache.spoil(list(box))
            self._m_decal_key = key
        renderer.m_baked_decal = bake1800.baked

    def _make(self, got: SLookParts) -> None:
        """The look on the device from its compiled parts; a part that failed is said once."""
        session, ctx = self.m_renderer.m_session, self.m_renderer.m_ctx
        self.m_light, self.m_source = got.light, got.objects
        self.m_ending = desktopfinish1800.CIslandFinish(
            ctx, self.GAME, got.light, *got.ending, self.m_renderer._message
        )
        self._m_bake_stages = got.bake if isinstance(got.bake, dict) else None
        if isinstance(got.bake, str):
            self.m_renderer._message(tr("islands.games_ground_bake", reason=got.bake[:300]))
        self.m_frame = frameforward.CForwardFrame(ctx, cascaded=self.GAME == "anno1800")
        self.m_terrain = self._terrain(
            ctx,
            terrain1800.program(got.stages),
            got.state,
            float(session.m_size_tiles),
            session.m_step_tiles,
            session.m_width_samples,
        )
        self.m_shared = self.m_terrain.m_shared = (CSharedBuffers(ctx), CSharedBuffers(ctx))
        if isinstance(got.shadow, tuple):
            self.m_terrain.m_casting = (terrain1800.program(got.shadow[0]), got.shadow[1])
        else:
            self.m_renderer._message(tr("islands.games_terrain_shadow", reason=got.shadow[:300]))
        self._make_tessellated(got.tessellation)
        self._make_occlusion(got.occlusion)

    def _make_occlusion(self, got: dict) -> None:
        """The terrain's pre-pass and the ambient occlusion over it, where both compiled (else said once: the frame
        draws without it)."""
        for value in got.values():
            if isinstance(value, Exception):
                self.m_renderer._message(
                    tr("islands.games_ambient_occlusion", reason=f"{type(value).__name__}: {value}"[:300])
                )
        if not isinstance(got.get("prepass"), tuple) or not isinstance(got.get("ssao"), dict):
            return
        self.m_terrain.m_prez = (terrain1800.program(got["prepass"][0]), got["prepass"][1])
        if isinstance(got.get("detailed"), tuple) and self.m_terrain.m_detailed is not None:
            self.m_terrain.m_prez_detailed = (terrain1800.program(got["detailed"][0]), got["detailed"][1])
        self.m_ssao = ssaoforward.CSsao1800(self.m_renderer.m_ctx.m_rhi, got["ssao"])

    def _make_tessellated(self, got: dict) -> None:
        """The tessellated near terrain and its height pages where the device draws patches and the rendering
        quality asks for them (117's rule), else the near nodes stay as they were (section 5)."""
        from PySide6.QtGui import QRhi

        ctx, session = self.m_renderer.m_ctx, self.m_renderer.m_session
        said = {"detailed": lambda why: tr("islands.games_tessellated_terrain", reason=why),
                "pages": lambda why: tr("islands.games_height_pages", reason=why),
                "shadow": lambda why: tr("islands.games_terrain_shadow", reason=why)}  # fmt: skip
        for key, value in got.items():
            if isinstance(value, Exception):
                self.m_renderer._message(said[key](f"{type(value).__name__}: {value}"[:300]))
        wants = terrain1800.TESSELLATED and (self.m_quality is None or self.m_quality.tessellation)
        if not wants or not ctx.m_rhi.isFeatureSupported(QRhi.Feature.Tessellation):
            return
        if isinstance(got.get("detailed"), tuple) and isinstance(got.get("pages"), dict):
            self.m_terrain.m_detailed = terrain1800.program(got["detailed"][0])
            self.m_pages = self.m_terrain.m_pages = pages1800.CPages1800(ctx.m_rhi, got["pages"], float(session.m_size_tiles),
                                                                  self.m_terrain.m_island)  # fmt: skip
            if isinstance(got.get("shadow"), tuple):
                self.m_terrain.m_casting_detailed = (terrain1800.program(got["shadow"][0]), got["shadow"][1])

    def _terrain(self, ctx, program, state, size_tiles: float, step_tiles: float, width_samples: int):
        return terrain1800.CTerrain1800(ctx, program, state, size_tiles, step_tiles, width_samples)

    def draw(self, view_frame, options, target, size_px: tuple[int, int], camera) -> bool:
        """The terrain into ``target`` with its depth; False when it is not drawn (the view's own ground then)."""
        if self.why_not():
            return False
        got = self.m_renderer.m_loader.get(JOB, self._compile)
        if not isinstance(got, tuple):
            return False
        ctx = self.m_renderer.m_ctx
        if self.m_terrain is None:
            self.m_quality = getattr(options, "quality", None)
            self._make(got)
        self._take_failures()
        if self.m_error:
            return False
        self._sync_heights(view_frame)
        self._sync_files(view_frame)
        terrain, session = self.m_terrain, self.m_renderer.m_session
        eye = camera.eye()
        models = terrain.nodes(
            (session.m_size_tiles - float(eye[0]), float(eye[1]), float(eye[2])), self._visible(view_frame.planes)
        )
        self._after_nodes()
        shadows = self._shadows(options)
        for buffers in self.m_shared:  # filled anew by this frame's first draws
            buffers.begin()
        objects = self.m_objects.frame(view_frame, options, shadows, size_px)
        self.m_renderer.m_game_parts = self.m_objects.m_drawn
        ctx.native_pass(lambda command_buffer, batch: carry(command_buffer, batch, terrain.uploads))
        if self.m_pages is not None:
            self.m_pages.run(ctx)
        if self._game_bake():
            self._bake_decals(options)
            self._bake_game(models)
        else:
            self._bake_own(options, [model.m_node for model in models])
        models = terrain.shown(models)  # a node not baked yet: the nodes drawn before over its square
        near, far = frame_planes(camera)
        game_camera = gs.direct_camera(size_px, eye, camera.m_target, camera.m_fov_rad, near, far)
        values = ambientforward.light_values(self.m_light, tuple(view_frame.lt["sunDir"]))
        values.update(ambientforward.scattering_values(self.m_light, float(eye[1]), near, far), **CLEAN)
        maps = self._sky_and_clouds(view_frame, target, size_px, game_camera, values)
        drawn = (
            models
            + objects.gbuffer
            + objects.transparent
            + self._water(view_frame, options, eye, values)
            + self._rivers(options)
        )
        used, cascades = self._cull_casters(drawn, game_camera, values) if shadows else (0, ())
        self.m_objects.place(objects, used, cascades)
        passes = frameforward.SPasses(shadows=shadows, ssao=self._ambient(options))
        self.m_frame.render(target, size_px, drawn, game_camera, values, maps, passes)
        self.m_renderer.m_stats["terrain_nodes"] = len(models)
        self.m_renderer.m_stats["game_objects"], self.m_renderer.m_stats["game_object_draws"] = (
            objects.instances,
            len(drawn) - len(models),
        )
        self.m_renderer.m_stats["game_object_levels"] = objects.levels
        self.m_renderer.m_stats["terrain_stored"] = sum(model.m_kind[0] == "stored" for model in models)
        self.m_renderer.m_stats["terrain_detailed"] = sum(model.m_kind[0] == "detailed" for model in models)
        self.m_animated = self._moving(options)
        return True

    def _moving(self, options) -> bool:
        """Whether the view draws on (``Renderer.animated``): the clouds, the sea and the rivers move with the view's
        clock, or the frame still settles (nodes to bake, pages to build, the exposure moving). The cascades are
        fitted in the frame itself (``_cull_casters``): none read back late."""
        clock = not options.is_clock_held
        moving = clock and (self.m_ending is not None or self.m_renderer.m_game_sea or self.m_renderer.m_game_inland)
        settling = (self.m_pages is not None and self.m_pages.busy()) or self._m_unbaked
        return bool(moving or settling or (self.m_ending is not None and self.m_ending.m_again))

    def _water(self, view_frame, options, eye, values: dict) -> list:
        """The sea by the game's own shaders where the water layer is shown (the view's own sea left out while it
        draws); none while it compiles or where it failed (said once)."""
        self.m_sea_waiting = False
        session = self.m_renderer.m_session
        if self.m_sea_error or not (options.layers.get("water", True) and session.m_island.get("sea", True)):
            return []
        if self.m_sea is None:
            got = self.m_renderer.m_loader.get(self.SEA_JOB, self._sea_parts)
            if got is None:
                self.m_sea_waiting = True
                return []
            if isinstance(got, Exception):
                self.m_sea_error = True
                self.m_renderer._message(tr("islands.games_sea_reason", reason=f"{type(got).__name__}: {got}"[:300]))
                return []
            self.m_sea = self._new_sea(got)
            self.m_sea.set_session(*got[-1], float(session.m_size_tiles))
        self.m_sea.run(self.m_renderer.m_ctx, float(view_frame.lt.get("uwTime", 0.0)))
        self.m_renderer.m_game_sea = True
        values.update(self.m_sea.frame_values())  # the setting's water in the frame's constants
        return self.m_sea.models(eye, values.get("cLgtDirection"))

    def _rivers(self, options) -> list:
        """The rivers and lakes by the game's inland water shader along the session's lines, lit with the sea's maps
        (the view's own left out where they draw); none without the game's sea, while they compile or where they
        failed (said once). island-1800-2205-view.md 18."""
        from .desktopgeometry import river_lines, triangulate

        lines, stamp = river_lines(self.m_renderer.m_session.m_island)
        if self.m_inland_error or self.m_sea is None or not lines or not options.layers.get("water", True):
            return []
        if self.m_inland is None:
            got = self.m_renderer.m_loader.get(self.INLAND_JOB, self._inland_parts)
            if got is None:
                self.m_sea_waiting = True
                return []
            if isinstance(got, Exception):
                self._inland_failed(f"{type(got).__name__}: {got}")
                return []
            self.m_inland = inland1800.CInland(
                self.m_renderer.m_ctx, self.GAME, got[0], CDummies(self.m_renderer.m_ctx.m_rhi), self.m_shared[0]
            )
            self._m_inland_found = got[1]
        if stamp != self._m_inland_key:  # the lines edited: the bodies and the flow map again
            bodies = gameinland.bodies(lines, self._m_inland_found, triangulate)
            self.m_inland.set_bodies(bodies, float(self.m_renderer.m_session.m_size_tiles))
            self._m_inland_key = stamp
        self.m_inland.run(self.m_renderer.m_ctx, self.m_pages.m_coarse if self.m_pages is not None else None)
        self.m_renderer.m_game_inland = True
        return self.m_inland.models(self.m_sea)

    def _inland_parts(self) -> tuple:
        """In the background: the inland water's shaders and maps (``render.inland1800``) and the island file's
        rivers' flow speeds and looks (``islandview.inland_water``; none where the file holds none)."""
        stages = inland1800.compile_stages(self._game_source(), self.GAME)
        try:
            found = self.m_renderer.m_editor.m_view.inland_waters()
        except (AttributeError, KeyError, ValueError):
            found = []
        return stages, found

    def _inland_failed(self, why: str) -> None:
        """The rivers and lakes left to the view's own from now on, said once."""
        self.m_inland_error = True
        self.m_renderer._message(tr("common.games_rivers_lakes", reason=why[:300]))
        if self.m_inland is not None:
            self.m_inland.release()
            self.m_inland = None

    def _sea_parts(self) -> tuple:
        """In the background: the ocean's shaders, the setting's ``SWater`` block, the maps and the small waves'
        volumes, the island's ``WaterInfos`` and ``WaterFlow`` (``render.ocean1800``; the session's maps last)."""
        files = self.m_renderer.m_editor.m_files
        stages = ocean1800.compile_stages(self._game_source())
        water = self._setting_part(ocean1800.read_water, ocean1800.SWater())
        return (
            stages,
            water,
            ocean1800.read_maps(files),
            waves1800.read_volumes(files),
            sea_maps(self.m_renderer.m_editor),
        )

    def _setting_part(self, read, default):
        """A part of the island's local ambient setting by ``read`` of its text (its ``SWater`` block, its wind;
        ``default``, the record's values, where it does not read)."""
        setting = setting_of(self.GAME, getattr(self.m_renderer.m_editor, "m_a7m_path", "") or "")
        try:
            raw = CListed(self.m_renderer.m_editor.m_files).read(f"{ambientforward.AMBIENT_DIR}local/{setting}.xml")
            return read(raw.decode("utf-8", "replace"))
        except (OSError, KeyError, ValueError, SyntaxError):
            return default

    def _new_sea(self, got: tuple):
        """The sea on the device from what ``_sea_parts`` read."""
        stages, water, maps, volumes, _session = got
        ctx = self.m_renderer.m_ctx
        return ocean1800.COcean1800(ctx, stages, water, maps, volumes, CDummies(ctx.m_rhi), self.m_shared[0])

    def _sky_and_clouds(self, view_frame, target, size_px: tuple[int, int], game_camera, values: dict) -> dict:
        """The setting's sky cubes and cloud noise (the clouds drifting by the view's clock) for the frame, the game's
        sky drawn over the view's own, and the view's own light turned into the game's units for its post chain
        (``desktopfinish1800``): the frame's maps."""
        ending = self.m_ending
        self.m_renderer.m_ctx.native_pass(lambda command_buffer, batch: carry(command_buffer, batch, ending.uploads))
        values.update(ending.values(float(view_frame.lt.get("uwTime", 0.0))))
        ending.draw_sky(target, size_px, game_camera, values)
        view_frame.lt.update(ending.view_light(view_frame.lt))
        return dict(ending.m_textures)

    def finish(self, image, size_px: tuple[int, int], lut):
        """The game's post chain of the view's resolved colour (its own grade: ``lut`` is 117's), as linear light;
        None without it."""
        quality = self.m_quality
        return (
            self.m_ending.finish(image, size_px, quality is None or quality.eye_adaptation)
            if self.m_ending is not None
            else None
        )

    def _cull_casters(self, drawn: list, game_camera, values: dict) -> tuple[int, tuple]:
        """Each caster drawn only into the cascades it reaches (1800's GPU culling does so): the cascades the frame
        fits next over the same casters (``shadowforward.cascades``); how many it reads and their matrices' bytes
        ((0, ()) without casters)."""
        casters, frame = [model for model in drawn if model.m_shadow is not None], self.m_frame
        if not frame.m_cascaded or not casters or "cLgtDirection" not in values:
            return 0, ()
        boxes = np.concatenate([model.corners() for model in casters])
        size_px = frame.m_shadow_size or gameshadow.SIZE
        found = shadowforward.cascades(game_camera, values["cLgtDirection"], boxes, size_px, frame.m_shadow_cascades)
        used = int(found["cUsedShadowPartitions"])
        matrices = [found[f"cShadowInformation[{i}].ShadowMatrix"] for i in range(used)]
        casts = [model.m_shadow for model in casters if isinstance(model.m_shadow, objects1800.CCast)]
        objects1800.cull_casts(casts, matrices)  # the objects' at once
        for model in casters:
            if not isinstance(model.m_shadow, objects1800.CCast):
                model.m_shadow.cull(matrices)
        return used, tuple(np.asarray(matrix, np.float32).tobytes() for matrix in matrices)

    def _ambient(self, options):
        """The ambient occlusion where it is made and the rendering quality and View > Rendering ask for it, else
        None (the pre-pass's targets let go)."""
        quality, ssao = self.m_quality, self.m_ssao
        is_enabled = ssao is not None and not ssao.m_error and (quality is None or quality.ssao)
        is_enabled = is_enabled and options.ambient_occlusion
        if not is_enabled:
            self.m_frame.drop_prepass()
        return ssao if is_enabled else None

    def _shadows(self, options) -> bool:
        """Whether the terrain casts into the sun's shadow (View > Rendering's switch and the rendering quality's,
        whose map side and cascades the frame takes): the terrain and the objects drawn by the game's shaders."""
        quality = self.m_quality
        self.m_frame.m_shadow_size, self.m_frame.m_shadow_cascades = shadow_quality(quality)
        return getattr(options, "shadows", True) and (quality is None or quality.shadows)

    def _source(self) -> tuple:
        """In the background: the island's files the terrain draws from (``desktopgame.island_source``: the height
        pages' parts, the stored meshes, the tint pages) and, for the look that reads them, the session's normal
        pages by (level, x, z)."""
        return island_source(self.m_renderer.m_editor), {}

    def _files_shown(self, view_frame) -> bool:
        """Whether the nodes draw the island's own meshes: where the view shows the game's terrain (117's rule)."""
        return view_frame.game_heights is not None

    def _sync_files(self, view_frame) -> None:
        """The island's files for the nodes once they are read (the nodes made again for their tint pages), and
        whether the view shows them."""
        terrain = self.m_terrain
        got = self.m_renderer.m_loader.get(SOURCE_JOB, self._source)
        if isinstance(got, Exception):
            self.m_renderer._message(tr("islands.islands_terrain_files", reason=f"{type(got).__name__}: {got}"[:300]))
        elif got is not None and got[0] is not None and terrain.m_source is None:
            terrain.m_source = got[0]
            self._use_pages(got[1])
            terrain.remake_nodes()
            if self.m_pages is not None:  # the island's own heights under its pages (117's rule)
                self.m_pages.set_coarse(
                    self.m_renderer.m_session.m_base_heights, self.m_renderer.m_session.m_step_tiles
                )
        terrain.m_files_shown = self._files_shown(view_frame)

    def _use_pages(self, pages: dict) -> None:
        """The session's normal pages for the terrain (2205's look; 1800's are height pages, read with phase 2)."""

    def _after_nodes(self) -> None:
        """What the nodes picked this frame asked for (2205's look: the normal pages to read)."""

    def _take_failures(self) -> None:
        """A node that did not prepare, or a pass that raised, turns the look off (said once): the view's own
        ground draws again (docs/viewer.md "When a pass fails")."""
        ctx, terrain = self.m_renderer.m_ctx, self.m_terrain
        self.m_objects.take_failures()  # an object's draw: its part to the view's look
        why = self.m_inland.take_failure(ctx.m_failed_models) if self.m_inland is not None else ""
        if why:  # the rivers and lakes: the view's own from now on
            self._inland_failed(why)
        baking = [(model, error) for model, error in ctx.m_failed_models if isinstance(model, bake1800.CNodeBake)]
        if baking:  # the game's bake: the view's own from now on, said once (117's rule)
            ctx.m_failed_models[:] = [
                failure for failure in ctx.m_failed_models if all(failure is not handled for handled in baking)
            ]
            if self.m_bake is not None:
                self._drop_bake(baking[0][1])
        mine = [(model, error) for model, error in ctx.m_failed_models if isinstance(model, terrain1800.CTerrainNode)]
        ctx.m_failed_models[:] = [
            failure for failure in ctx.m_failed_models if all(failure is not handled for handled in mine)
        ]
        patches = [failure for failure in mine if failure[0].m_kind[0] == "detailed"]
        broken = self.m_pages is not None and self.m_pages.m_error
        if patches or broken:  # the near nodes untessellated from now on, said once (117's rule)
            why = patches[0][1] if patches else self.m_pages.m_error
            self.m_renderer._message(tr("islands.games_tessellated_terrain", reason=why[:300]))
            if self.m_pages is not None:
                self.m_pages.release()
            self.m_pages = terrain.m_pages = terrain.m_detailed = None
            terrain.set_heights(terrain.m_heights, None)
            mine = [failure for failure in mine if all(failure is not handled for handled in patches)]
        if mine:
            self.m_error = tr("common.games_terrain_shader", reason=mine[0][1][:300])
        if ctx.m_pass_errors:
            self.m_error = tr("common.games_frame_failed", reason=ctx.m_pass_errors[-1][:300])
            ctx.m_pass_errors.clear()

    def _rect(self, node) -> tuple:
        """A node's u and v span its square (the stored meshes too)."""
        return (node.x, node.z, node.tiles, node.tiles)

    def release(self) -> None:
        if self.m_ending is not None:
            self.m_ending.release()
            self.m_ending = None
        if self.m_ssao is not None:
            self.m_ssao.release()
            self.m_ssao = None
        if self.m_sea is not None:  # before the shared buffers its draws bind
            self.m_sea.release()
            self.m_sea = None
        if self.m_inland is not None:
            self.m_inland.release()
            self.m_inland = None
        super().release()
        for buffers in self.m_shared or ():  # after the draws that bind them
            buffers.release()
        self.m_shared = None
