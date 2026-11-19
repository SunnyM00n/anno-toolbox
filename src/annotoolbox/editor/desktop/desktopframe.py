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
from ...render.modellibrary import WATER_SHADER
from ..editsession import game_of

if TYPE_CHECKING:
    from .desktoprender import ViewOptions

GRASS_FAR = 220.0  # world units from the pivot: grass is specks beyond
STREET_SHADER = 19  # a street model: between the decals before and after the streets (render.decalmesh)
# the islands 117's grade is read for: 1800's and 2205's looks grade in their own post chains
# (island-1800-2205-view.md 18), and without a chain their picture stays ungraded [ours]
GRADE_GAMES = ("anno117", "anno2070")


@dataclass
class Frame:
    """What the passes of one ``Renderer.render`` share: the camera's matrices, the light, the clock."""

    view: np.ndarray
    view_proj: np.ndarray
    eye: np.ndarray
    planes: object
    distance: float
    lt: dict
    shadow: object
    scale: float
    game_heights: np.ndarray | None
    now: float = 0.0
    lod: object = None  # render.cull.LodCamera: the levels of detail by screen size (None: all finest)


def heights_with_edits(game: np.ndarray, session) -> np.ndarray:
    """The game's near terrain of the saved island with the session's height edits on top: the build adds the
    corner deltas before it generates the height pages, so the game's rebuilt surface moves by them."""
    return game + (session.heights - session.base_heights)


class FramePasses:
    """The layer passes of ``Renderer.render``."""

    def _sync_scene(self, opts: ViewOptions) -> np.ndarray | None:
        """Bring the terrain, the batches and the ground up to the edits since the last frame; the game's heights
        (with the height edits) when the game terrain is on and loaded."""
        change = self._take_pending()
        s = self.session
        game_heights = None
        if opts.game_terrain and self.editor is not None:
            got = self.loader.get("game_heights", self.editor.game_heights)
            if isinstance(got, bytes):
                game_heights = heights_with_edits(np.frombuffer(got, "<f4").reshape(s.width, s.width), s)
            elif isinstance(got, Exception):
                self._message(tr("islands.no_game_terrain", reason=got))
        if change.heights is not None or (game_heights is not None) != self._shown_game_terrain:
            self._ground_version += 1  # the decals follow the ground
        self._update_terrain(change.heights, game_heights)
        self._sync_batches(change)
        self._sync_ground(change, opts)
        return game_heights

    def _full_models(self, opts: ViewOptions) -> set[str] | None:
        """The models the view's own look must draw, loaded in full (None: all): every one unless the game's look
        draws; while its shaders compile, all on a first start and none on a later one (seconds); then those with a
        part it does not draw (island-117-view.md "Speed", part 6)."""
        g = self.game
        if not opts.native or g is None or g.why_not():
            return None
        ghost = {self.ghost.key[1]} if self.ghost is not None and self.ghost.key[0] == "ghost" else set()
        if not g.compiled():
            return None if g.cold else ghost
        return set(g.objects.fallback) | ghost  # the model about to be placed: the view's look draws it

    def _sync_full(self, opts: ViewOptions) -> None:
        """The library's full models for the next frame; the device copies of the meshes and maps only the models
        let go used are released (their batches take their boxes, ``_sync_batches``)."""
        if self.library is None or not self.library.set_full(self._full_models(opts)):
            return
        for b in self._all_batches():
            if b.parts is not None and b.key[0] != "markers" and b.lean == self.library.full(b.key[1]):
                b.parts = None  # the game's look took the model over, or gave it back: its other meshes
                b.release_draws()
        meshes, maps = self.library.wanted()
        for key in [k for k in self.meshes if k != "pin" and k[1] != "box" and k[0] not in meshes]:
            buffers = {id(b): b for vbo, ibo, *_ in self.meshes.pop(key).values() for b in (vbo, ibo)}
            for buf in buffers.values():
                buf.release()
        missing = self.textures.get("missing")  # the checker of missing maps, shared by their keys
        for key in [k for k in self.textures if k[:2] in ("d:", "n:") and k not in maps]:
            tex = self.textures.pop(key)
            if tex is not None and tex is not missing:
                tex.release()

    def _draw_ground(self, f: Frame, opts: ViewOptions) -> None:
        prog = self.prog["terrain"]
        prog["viewProj"].write(gl(f.view_proj))
        self._set_light(prog, f.lt, f.eye, f.shadow, f.lt.get("envGround", 1.0))
        self._bind_ground(prog, opts)
        self._draw_terrain(f.planes)

    def _visible_parts(self, f: Frame, opts: ViewOptions) -> tuple[list, list, list]:
        """(batch, part, VAOs) of every visible batch: the street parts, the other opaque ones, and the water-shader
        parts (drawn last); the caller draws the decals between the first two. The parts the game's own shaders drew
        (``game_parts``) are left out."""
        far_grass = f.distance > GRASS_FAR
        streets, models, translucent = [], [], []
        for b in self._all_batches():
            if not b.count or not b.parts or not self._layer_on(b, opts) or (b.key[0] == "grass" and far_grass):
                continue
            if b.lean:  # its boxes are for picking: the game's look draws it (or will once its parts are in)
                continue
            if not self._cull(b, f.planes, f.lod):
                continue
            drawn = getattr(self, "game_parts", ())
            for k, (p, vaos) in enumerate(b.vaos):
                if (b.key, k) in drawn:
                    continue
                shader = p["shader"]
                (translucent if shader == WATER_SHADER else streets if shader == STREET_SHADER else models).append(
                    (b, p, vaos)
                )
        return streets, models, translucent

    def _lay_decals(self, f: Frame, opts: ViewOptions) -> None:
        """The decals of the shown models laid on the drawn ground, again when the objects or the ground changed."""
        if self.library is None:
            return
        shown = [
            b for b in self._all_batches() if b.count and b.parts and self._layer_on(b, opts) and b.mats is not None
        ]
        baked = getattr(self, "baked_decal", None)  # what the game's ground bake drew (1800's look): left out here
        placed = [(b.mats, self.library.decals(b.key[1])) for b in shown if b.key[0] != "markers"]
        placed = [(m, [x for x in d if baked is None or not baked(x)]) for m, d in placed]
        placed = [(m, d) for m, d in placed if d]
        key = (self._ground_version, tuple((b.key, b.version) for b in shown), baked is not None)
        s = self.session
        heights = f.game_heights if f.game_heights is not None else s.heights
        height_at = decalmesh.heights_sampler(heights, s.step, (s.width - 1) * s.step)
        if self.decals.update(key, placed, height_at, s.step, self.prog["model"]):
            self.animated = True  # the rest is laid in the next frames

    def _draw_sky(self, view_proj, lt: dict, eye) -> None:
        """The sky behind everything: the editor's gradient, the game's HDR sky or 2070's."""
        self.ctx.disable(self.ctx.DEPTH_TEST)
        sky = self.prog["sky"]
        sky["invViewProj"].write(gl(np.linalg.inv(view_proj)))
        mode, zen, hor, fall = lt["sky"]
        sky["skyMode"].value = mode
        sky["zenith"].value, sky["horizon"].value, sky["fallOff"].value = zen, hor, fall
        sky["below"].value = lt.get("skyBelow", hor)
        self._set_light(sky, lt, eye, None)
        self.fullscreen["sky"].render(vertices=3)
        self.ctx.enable(self.ctx.DEPTH_TEST)

    def _finish(self, t: dict, fbo, size: tuple[int, int], lt: dict, game: bool = False) -> None:
        """The resolved frame through the look's finish (ACES, the game's grade, 2070's mood; ``game``: the game's
        post chain where it is in, island-117-view.md phase 8) into ``fbo``; the final pass takes the chain's picture
        by the look's ``FINISH_MODE`` (3: linear light, encoded here; 4: encoded already)."""
        ctx = self.ctx
        ctx.copy_framebuffer(t["resolve"], t["msaa"])
        source, finish = t["resolved"], lt["finish"]
        if game:
            graded = self.game.finish(source, t["size"], self._lut() if lt["finish"] == 1 else None)
            source, finish = (graded, self.game.FINISH_MODE) if graded is not None else (source, finish)
        fbo.use()
        ctx.viewport = (0, 0, size[0], size[1])
        ctx.disable(ctx.DEPTH_TEST)
        fin = self.prog["final"]
        source.use(0)
        fin["frame"].value = 0
        fin["finish"].value = finish
        fin["exposure"].value = lt["exposure"]
        if "mood" in fin:
            fin["mood"].value = LOOK2070["mood"]
        lut = self._lut() if lt["finish"] == 1 else None
        (lut or self._dummy_3d).use(1)
        if "hasLut" in fin:
            fin["hasLut"].value = lut is not None
        if "lut" in fin:
            fin["lut"].value = 1
        self.fullscreen["final"].render(vertices=3)

    def _lut(self):
        """117's grade as a volume; None while it reads, where it failed and on an island of a game outside
        ``GRADE_GAMES``."""
        if game_of(self.session.island) not in GRADE_GAMES:
            return None
        if "lut" not in self.textures:
            got = self.loader.get("lut", self.editor.grade_lut) if self.editor is not None else None
            if got is None:
                return None
            if isinstance(got, Exception):
                self._message(tr("islands.no_game_grade", reason=got))
                self.textures["lut"] = None
                return None
            n = round((len(got) // 12) ** (1 / 3))
            tex = self.ctx.texture3d((n, n, n), 3, got, dtype="f4")
            tex.repeat_x = tex.repeat_y = tex.repeat_z = False
            self.textures["lut"] = tex
        return self.textures["lut"]

    def _draw_dots(self, f: Frame) -> None:
        dots = self._dots_vao()
        if dots:
            fp = self.prog["flat"]
            fp["viewProj"].write(gl(f.view_proj))
            fp["pointSize"].value = max(2.0, min(8.0, 1500.0 / max(f.distance, 1.0))) * f.scale
            fp["alpha"].value, fp["gain"].value = 1.0, 0.8
            self.ctx.enable(self.ctx.PROGRAM_POINT_SIZE)
            dots.render(self.ctx.POINTS)

    def _draw_sea(self, f: Frame, opts: ViewOptions) -> None:
        if opts.layers.get("water", True) and self.session.island.get("sea", True):  # a portrait scene has no sea
            fp = self.prog["flat"]
            fp["viewProj"].write(gl(f.view_proj))
            fp["alpha"].value, fp["gain"].value, fp["pointSize"].value = 1.0, 1.0, 1.0
            if not opts.underwater:  # under water neither the surface nor the dark bed is between eye and haze
                sea = not self.game_sea  # the game's sea drew in its frame: the rivers and lakes alone
                inland = not self.game_inland  # and its rivers and lakes: the sea alone, or nothing
                if sea:
                    self._water()["bed"].render(self.ctx.TRIANGLE_STRIP)
                if sea or inland:
                    self._draw_water(f.view_proj, f.lt, f.eye, f.shadow, sea, inland)
