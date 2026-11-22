"""The island view's look "The game's own shaders" for an Anno 2205 session (a session map): 1800's look
(``desktopgame1800``) with 2205's terrain (``render.terrain2205``: mirrored patches, the session's stored levels and
normal pages, a baked texture per node), its single shadow map and the session's ambient setting.

``CGameIsland2205(renderer)`` is made for a 2205 session through ``desktopgame.LOOKS``;
docs/rendering/island-1800-2205-view.md section 3.
"""

from __future__ import annotations

from ...infra.i18n import tr
from ...render import bake1800, terrain2205, terraindraw
from .desktopgame import sea_maps
from .desktopgame1800 import CGameIsland1800

PAGE_JOB = "game-island-normal-page"
PAGES_IN_FLIGHT = 8  # normal pages read at once in the background [ours]
NORMAL_Y_FS = bake1800.normal_fs("vec4(vec3(n.g), 1.0)")  # the normal's y as red, as the game's bake keeps it


class CGameIsland2205(CGameIsland1800):
    """A 2205 session's frame by its own shaders (module docstring)."""

    GAME = "anno2205"
    SEA_JOB = "game-island-sea-2205"
    INLAND_JOB = "game-island-inland-2205"
    LANE_TARGETS = (*CGameIsland1800.LANE_TARGETS, ("green", "f1"))  # the normal's y as red
    ENCODES = {**CGameIsland1800.ENCODES, "green": ("normal", NORMAL_Y_FS)}
    # a node's own textures <- a target: the albedo encoded, the normal's x (red) and y (green) as reds
    STORES = {"cBakedDiffTex": "encoded", "cBakedNormalXTex": "normal", "cBakedNormalYTex": "green"}
    ARRAYS = False

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.m_reading: dict[tuple, str] = {}  # the normal pages being read: (level, x, z) -> path

    def _objects(self):
        """The island's objects by 2205's model and prop shaders (``desktopobjects2205``, island-1800-2205-view.md
        18)."""
        from .desktopobjects2205 import CObjects2205

        return CObjects2205(self)

    def _object_source(self, files, compiler):
        """In the background: what the objects compile with (2205's effects)."""
        from ...render.objects2205 import CObjectSource2205

        return CObjectSource2205(files, compiler)

    def _new_bake(self, ground, size_texels: int):
        """2205's own bake (``bake1800``) into each node's textures, at their side (``SLICE_SIZE``)."""
        quality, terrain = self.m_quality, self.m_terrain
        made = bake1800.CBake(self.m_renderer.m_ctx, self._m_bake_stages, ground, terraindraw.SLICE_SIZE,
                             max_texture=quality.max_texture if quality else 0, destination=lambda k: (terrain.slot_textures(k), 0))  # fmt: skip
        terrain.use_levels(made.m_levels)
        return made

    def _drop_bake(self, why: str) -> None:
        """117's rule, the node textures back to one level (the view's own bake writes no mips)."""
        super()._drop_bake(why)
        self.m_terrain.use_levels(1)

    def _extras(self, ground):
        """The ground with the session's tint map (``terrain2205.tint_map``; 2205's bake has no grit)."""
        return bake1800.with_extras(ground, tint=terrain2205.tint_map(self.m_renderer.m_editor.m_view.rd3d))

    def _bake_island(self) -> dict:
        """The session's values (2205 has no island matrix: the bake's shared names read it unturned)."""
        return {**terraindraw.island_values(self.m_terrain.m_size_tiles, is_mirrored=False), **self.m_terrain.m_island}

    def _terrain(self, ctx, program, state, size_tiles: float, step_tiles: float, width_samples: int):
        return terrain2205.CTerrain2205(ctx, program, state, size_tiles, step_tiles, width_samples)

    def _tessellation(self, source) -> dict:
        return {}  # 2205 draws every node from its stored mesh: no height pages, no tessellation

    def _occlusion(self, source) -> dict:
        return {}  # 2205 has no ambient occlusion and its terrain no normals-and-depth pass

    def _source(self) -> tuple:
        """In the background: the session's stored levels and its normal pages (``formats.a7m.sessionpages``)."""
        from ..formats.a7m.sessionpages import page_files

        view = getattr(self.m_renderer.m_editor, "m_view", None)
        if view is None or not hasattr(view, "rd3d") or not hasattr(view, "gamedata_dir"):
            return None, {}
        pages = {(level, x, z): path for path, level, x, z in page_files(view)}
        return terraindraw.SIslandSource({}, terrain2205.stored_levels(view.rd3d)), pages

    def _files_shown(self, view_frame) -> bool:
        return True  # the game draws every node from its stored mesh: no near terrain of another source

    def _use_pages(self, pages: dict) -> None:
        self.m_terrain.m_normal_pages = pages

    def _after_nodes(self) -> None:
        """The normal pages the shown nodes asked for this frame, read in the background a few at a time (nearest
        first); a read once begun is taken in when done, asked for or not (the pages kept make room for it)."""
        self._take_pages()
        for key in self.m_terrain.m_wanted:
            if len(self.m_reading) >= PAGES_IN_FLIGHT:
                break
            self.m_reading.setdefault(key, self.m_terrain.m_normal_pages[key])
        self._take_pages()  # begins the new ones

    def _take_pages(self) -> None:
        """The reads that are done onto the device (``CTerrain2205.add_page``); the others begun or left running."""
        terrain, loader, files = self.m_terrain, self.m_renderer.m_loader, self.m_renderer.m_editor.m_files
        for key, path in list(self.m_reading.items()):
            job = (PAGE_JOB, key)
            got = loader.get(job, lambda path=path: terrain2205.session_page(files.read(path)))
            if got is None:
                continue
            loader.forget(job)
            del self.m_reading[key]
            if isinstance(got, Exception):
                self.m_renderer._message(tr("islands.normal_page_session", reason=f"{type(got).__name__}: {got}"[:300]))
            terrain.add_page(key, got if isinstance(got, tuple) else None)

    def busy(self) -> int:
        reading = bool(self.m_reading) or (self.m_terrain is not None and bool(self.m_terrain.m_wanted))
        return super().busy() or int(reading or self.m_sea_waiting)

    def _sea_parts(self) -> tuple:
        """In the background: the sea's shaders, the setting's ``SWater`` block, the water maps and the session's
        ``WaterInfos`` and ``WaterFlow`` (``render.sea2205``)."""
        from ...render import sea2205

        stages = sea2205.compile_stages(self._game_source())
        water = self._setting_part(sea2205.read_water, sea2205.SWater())
        return stages, water, sea2205.read_maps(self.m_renderer.m_editor.m_files), sea_maps(self.m_renderer.m_editor)

    def _new_sea(self, got: tuple):
        """2205's sea on the device (``render.sea2205``)."""
        from ...render import sea2205
        from ...render.fxcdraw import CDummies

        stages, water, maps, _session = got
        ctx = self.m_renderer.m_ctx
        return sea2205.CSea2205(ctx, stages, water, maps, CDummies(ctx.m_rhi), self.m_shared[0])

    def release(self) -> None:
        self.m_reading.clear()  # a read in flight stays with the loader: a look made again takes it from there
        super().release()
