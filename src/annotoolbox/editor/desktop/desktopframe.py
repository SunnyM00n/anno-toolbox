"""The passes of one editor frame that draw a layer of the scene: the ground, the models, the decals laid on the
ground (``desktopdecals``), the dots, the sea, the sky and the finish; and which models the view's own look loads in
full (``_sync_full``).

``Renderer.render`` (``desktoprender``) sets the frame up and calls them in order; they use the renderer's own
state, so this is a part of the class, not a stand-alone module (island-editor-view "The 3D view").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ...infra.i18n import tr
from ...render import decalmesh
from ...render.camera import gl
from ...render.light import LOOK2070
from ...render.modellibrary import FOAM_SHADER, WATER_SHADER
from ..editsession import game_of
from . import desktopstreets

if TYPE_CHECKING:
    from .desktoprender import SViewOptions

GRASS_FAR = 220.0  # world units from the pivot: grass is specks beyond
STREET_SHADER = 19  # a street model: between the decals before and after the streets (render.decalmesh)
# the islands 117's grade is read for: 1800's and 2205's looks grade in their own post chains
# (island-1800-2205-view.md 18), and without a chain their picture stays ungraded [ours]
GRADE_GAMES = ("anno117", "anno2070")


@dataclass
class SFrame:
    """What the passes of one ``Renderer.render`` share: the camera's matrices, the light, the clock."""

    view: np.ndarray
    view_proj: np.ndarray
    eye: np.ndarray
    planes: object
    distance_m: float
    lt: dict
    shadow: object
    scale: float
    game_heights: np.ndarray | None
    now: float = 0.0
    lod: object = None  # render.cull.SLodCamera: the levels of detail by screen size (None: all finest)


def heights_with_edits(game: np.ndarray, session) -> np.ndarray:
    """The game's near terrain of the saved island with the session's height edits on top: the build adds the
    corner deltas before it generates the height pages, so the game's rebuilt surface moves by them."""
    return game + (session.m_heights - session.m_base_heights)


class CFramePasses:
    """The layer passes of ``Renderer.render``."""

    def _game_heights_of(self, got: bytes) -> np.ndarray:
        """The game's heights (``got``: its file's bytes) with the session's edits: worked out again only when the
        file or an edit of the heights is new (``_sync_scene`` forgets them; a continental island's are millions of
        samples a frame)."""
        held = self._m_game_heights
        if held is not None and held[0] is got:
            return held[1]
        width = self.m_session.m_width_samples
        heights = heights_with_edits(np.frombuffer(got, "<f4").reshape(width, width), self.m_session)
        self._m_game_heights = (got, heights)
        return heights

    def _sync_scene(self, opts: SViewOptions) -> np.ndarray | None:
        """Bring the terrain, the batches and the ground up to the edits since the last frame; the game's heights
        (with the height edits) when the game terrain is on and loaded."""
        change = self._take_pending()
        game_heights = None
        if change.heights is not None:
            self._m_game_heights = None  # also while the game terrain is off: the edit is taken now
        if opts.game_terrain and self.m_editor is not None:
            got = self.m_loader.get("game_heights", self.m_editor.game_heights)
            if isinstance(got, bytes):
                game_heights = self._game_heights_of(got)
            elif isinstance(got, Exception):
                self._message(tr("islands.no_game_terrain", reason=got))
        if change.heights is not None or (game_heights is not None) != self._m_shown_game_terrain:
            self._m_ground_version += 1  # the decals follow the ground
        self._update_terrain(change.heights, game_heights)
        self._sync_batches(change)
        self._sync_ground(change, opts)
        return game_heights

    def _full_models(self, opts: SViewOptions) -> set[str] | None:
        """The models the view's own look must draw, loaded in full (None: all): every one unless the game's look
        draws; while its shaders compile, all on a first start and none on a later one (seconds); then those with a
        part it does not draw (island-117-view.md "Speed", part 6)."""
        game = self.m_game
        if not opts.native or game is None or game.why_not():
            return None
        ghost = {self.m_ghost.m_key[1]} if self.m_ghost is not None and self.m_ghost.m_key[0] == "ghost" else set()
        if not game.compiled():
            return None if game.m_cold else ghost
        return set(game.m_objects.m_fallback) | ghost  # the model about to be placed: the view's look draws it

    def _sync_full(self, opts: SViewOptions) -> None:
        """The library's full models for the next frame; the device copies of the meshes and maps only the models
        let go used are released (their batches take their boxes, ``_sync_batches``)."""
        if self.m_library is None or not self.m_library.set_full(self._full_models(opts)):
            return
        self._m_scene_serial += 1  # meshes and maps go: the shadow map is drawn again
        for batch in self._all_batches():
            if (
                batch.m_parts is not None
                and batch.m_key[0] != "markers"
                and batch.m_lean == self.m_library.full(batch.m_key[1])
            ):
                batch.m_parts = None  # the game's look took the model over, or gave it back: its other meshes
                batch.release_draws()
        meshes, maps = self.m_library.wanted()
        for key in [k for k in self.m_meshes if k != "pin" and k[1] != "box" and k[0] not in meshes]:
            buffers = {id(batch): batch for vbo, ibo, *_ in self.m_meshes.pop(key).values() for batch in (vbo, ibo)}
            for buffer in buffers.values():
                buffer.release()
        missing = self.m_textures.get("missing")  # the checker of missing maps, shared by their keys
        for key in [k for k in self.m_textures if k[:2] in ("d:", "n:") and k not in maps]:
            texture = self.m_textures.pop(key)
            if texture is not None and texture is not missing:
                texture.release()

    def _draw_ground(self, frame: SFrame, opts: SViewOptions) -> None:
        prog = self.m_prog["terrain"]
        prog["viewProj"].write(gl(frame.view_proj))
        self._set_light(prog, frame.lt, frame.eye, frame.shadow, frame.lt.get("envGround", 1.0))
        self._bind_ground(prog, opts)
        self._draw_terrain(frame.planes)

    def _visible_parts(self, frame: SFrame, opts: SViewOptions) -> tuple[list, list, list]:
        """(batch, part, VAOs) of every visible batch: the street parts, the other opaque ones, and the water-shader
        parts (drawn last); the caller draws the decals between the first two. The parts the game's own shaders drew
        (``game_parts``) are left out."""
        far_grass = frame.distance_m > GRASS_FAR
        streets, models, translucent = [], [], []
        for batch in self._all_batches():
            if (
                not batch.m_count
                or not batch.m_parts
                or not self._layer_on(batch, opts)
                or (batch.m_key[0] == "grass" and far_grass)
            ):
                continue
            if batch.m_lean:  # its boxes are for picking: the game's look draws it (or will once its parts are in)
                continue
            if not self._cull(batch, frame.planes, frame.lod):
                continue
            drawn = getattr(self, "m_game_parts", ())
            for k, (part, vaos) in enumerate(batch.m_vaos):
                if (batch.m_key, k) in drawn:
                    continue
                shader = part["shader"]
                (
                    translucent
                    if shader in (WATER_SHADER, FOAM_SHADER)
                    else streets
                    if shader == STREET_SHADER
                    else models
                ).append((batch, part, vaos))
        return streets, models, translucent

    def _lay_decals(self, frame: SFrame, opts: SViewOptions) -> None:
        """The decals of the shown models laid on the drawn ground, again when the objects or the ground changed; none
        while the game's decal shader lays them."""
        if self.m_game_decals:  # a laying begun while the game's compiled is given up: ``busy`` waited on it for good
            self.m_decals.stop_laying()
            return
        if self.m_library is None:
            return
        shown = [
            batch
            for batch in self._all_batches()
            if batch.m_count
            and batch.m_parts is not None  # a model of ground decals alone has no parts
            and self._layer_on(batch, opts)
            and batch.m_mats is not None
        ]
        baked = getattr(self, "m_baked_decal", None)  # what the game's ground bake drew (1800's look): left out here
        placed = [
            (batch.m_mats, self.m_library.decals(batch.m_key[1])) for batch in shown if batch.m_key[0] != "markers"
        ]
        placed = [(matrices, [x for x in decals if baked is None or not baked(x)]) for matrices, decals in placed]
        placed = [(matrices, decals) for matrices, decals in placed if decals]
        session = self.m_session
        streets = self._street_cells()
        placed += streets
        key = (
            self._m_ground_version,
            tuple((batch.m_key, batch.m_version) for batch in shown),
            baked is not None,
            len(streets),
        )
        heights = frame.game_heights if frame.game_heights is not None else session.m_heights
        height_at = decalmesh.heights_sampler(
            heights, session.m_step_tiles, (session.m_width_samples - 1) * session.m_step_tiles
        )
        if self.m_decals.update(key, placed, height_at, session.m_step_tiles, self.m_prog["model"]):
            self.m_animated = True  # the rest is laid in the next frames

    def _street_cells(self) -> list:
        """The island's stored streets as decals (``desktopstreets``), made once; their sheets are asked of the model
        library as decal maps."""
        if self.m_street_cells is None:
            streets = self.m_session.m_island.get("streets")
            self.m_street_cells = desktopstreets.entries(streets, self.m_session.m_size_tiles)
            for path in (streets or {}).get("sheets", {}).values():
                self.m_library.keep_decal_map(path)
                self.m_library.texture(f"d:{path}")  # starts loading now, not at the first draw
        return self.m_street_cells

    def _draw_sky(self, view_proj, lt: dict, eye) -> None:
        """The sky behind everything: the editor's gradient, the game's HDR sky or 2070's."""
        self.m_ctx.disable(self.m_ctx.DEPTH_TEST)
        sky = self.m_prog["sky"]
        sky["invViewProj"].write(gl(np.linalg.inv(view_proj)))
        mode, zen, hor, fall = lt["sky"]
        sky["skyMode"].value = mode
        sky["zenith"].value, sky["horizon"].value, sky["fallOff"].value = zen, hor, fall
        sky["below"].value = lt.get("skyBelow", hor)
        self._set_light(sky, lt, eye, None)
        self.m_fullscreen["sky"].render(vertices=3)
        self.m_ctx.enable(self.m_ctx.DEPTH_TEST)

    def _finish(self, targets: dict, fbo, size_px: tuple[int, int], lt: dict, game: bool = False) -> None:
        """The resolved frame through the look's finish (ACES, the game's grade, 2070's mood; ``game``: the game's
        post chain where it is in, island-117-view.md phase 8) into ``fbo``; the final pass takes the chain's picture
        by the look's ``FINISH_MODE`` (3: linear light, encoded here; 4: encoded already)."""
        ctx = self.m_ctx
        ctx.copy_framebuffer(targets["resolve"], targets["msaa"])
        source, finish = targets["resolved"], lt["finish"]
        if game and self.m_game.post(source, targets["size"], self._lut() if finish == 1 else None, fbo, size_px):
            return  # the chain and the composite by the game's shipped shaders, into ``fbo`` (native-backend.md 14)
        if game:
            graded = self.m_game.finish(source, targets["size"], self._lut() if lt["finish"] == 1 else None)
            source, finish = (graded, self.m_game.FINISH_MODE) if graded is not None else (source, finish)
        fbo.use()
        ctx.viewport = (0, 0, size_px[0], size_px[1])
        ctx.disable(ctx.DEPTH_TEST)
        fin = self.m_prog["final"]
        source.use(0)
        fin["frame"].value = 0
        fin["finish"].value = finish
        fin["exposure"].value = lt["exposure"]
        if "mood" in fin:
            fin["mood"].value = LOOK2070["mood"]
        lut = self._lut() if lt["finish"] == 1 else None
        (lut or self._m_dummy_3d).use(1)
        if "hasLut" in fin:
            fin["hasLut"].value = lut is not None
        if "lut" in fin:
            fin["lut"].value = 1
        self.m_fullscreen["final"].render(vertices=3)

    def _lut(self):
        """117's grade as a volume; None while it reads, where it failed and on an island of a game outside
        ``GRADE_GAMES``."""
        if game_of(self.m_session.m_island) not in GRADE_GAMES:
            return None
        if "lut" not in self.m_textures:
            got = self.m_loader.get("lut", self.m_editor.grade_lut) if self.m_editor is not None else None
            if got is None:
                return None
            if isinstance(got, Exception):
                self._message(tr("islands.no_game_grade", reason=got))
                self.m_textures["lut"] = None
                return None
            side = round((len(got) // 12) ** (1 / 3))
            texture = self.m_ctx.texture3d((side, side, side), 3, got, dtype="f4")
            texture.repeat_x = texture.repeat_y = texture.repeat_z = False
            self.m_textures["lut"] = texture
        return self.m_textures["lut"]

    def _draw_dots(self, frame: SFrame) -> None:
        dots = self._dots_vao()
        if dots:
            fp = self.m_prog["flat"]
            fp["viewProj"].write(gl(frame.view_proj))
            fp["pointSize"].value = max(2.0, min(8.0, 1500.0 / max(frame.distance_m, 1.0))) * frame.scale
            fp["alpha"].value, fp["gain"].value = 1.0, 0.8
            self.m_ctx.enable(self.m_ctx.PROGRAM_POINT_SIZE)
            dots.render(self.m_ctx.POINTS)

    def _draw_sea(self, frame: SFrame, opts: SViewOptions) -> None:
        if opts.layers.get("water", True) and self.m_session.m_island.get("sea", True):  # a portrait scene has no sea
            fp = self.m_prog["flat"]
            fp["viewProj"].write(gl(frame.view_proj))
            fp["alpha"].value, fp["gain"].value, fp["pointSize"].value = 1.0, 1.0, 1.0
            if not opts.underwater:  # under water neither the surface nor the dark bed is between eye and haze
                sea = not self.m_game_sea  # the game's sea drew in its frame: the rivers and lakes alone
                inland = not self.m_game_inland  # and its rivers and lakes: the sea alone, or nothing
                if sea:
                    self._water()["bed"].render(self.m_ctx.TRIANGLE_STRIP)
                if sea or inland:
                    self._draw_water(frame.view_proj, frame.lt, frame.eye, frame.shadow, sea, inland)
