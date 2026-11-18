"""The island's config decals in the look "The game's own shaders": each shown model's DECALs by the game's decal
shader (``render.gamedecals``) into the G-buffer after the terrain, laid on the session's heights by the shader, one
draw per look and shape of up to 204 decals; while a look compiles, or when one fails, the view's own decals.

``GameDecals(island)`` is the entry (one per ``desktopgame.GameIsland``): ``frame(opts, textures)`` the draws of a
frame, ``take_failures`` and ``release``. docs/rendering/island-117-view.md "Config decals".
"""

from __future__ import annotations

import numpy as np

from ...infra.i18n import tr
from ...render import gamedecals, gamedraw

# with a material's values, its loader job: the loader outlives a look (made again for another rendering
# quality), so the key names the material itself, not the order it was met in
JOB = "game-decal"


class Look:
    """One material of the island's decals: its loader job, its decals by shape, its draws and their maps."""

    def __init__(self, job: tuple, values: dict, shapes: dict) -> None:
        self.job, self.values, self.shapes = job, values, shapes
        self.draws: list = []
        self.maps: list = []  # the look's texture arrays (``gamedecals.decal_draws``)

    def release(self) -> None:
        for d in self.draws:
            d.release()
        gamedecals.release_maps(self.maps)
        self.draws = []


class GameDecals:
    """The decals of the island's shown models by the game's decal shader (module docstring)."""

    def __init__(self, island) -> None:
        self.island, self.r = island, island.r
        self.looks: dict[tuple, Look] = {}
        self.programs: dict = {}
        self.waiting = 0  # looks still compiling
        self.error = ""  # why the game's decals do not draw (said once): the view's own from then on
        self._key: tuple | None = None

    def frame(self, opts, textures) -> list:
        """This frame's decal draws (``textures``: the session maps the terrain stands in for), [] while any look
        compiles or after a failure (the view then draws its own: ``Renderer.game_decals`` False)."""
        r = self.r
        source = getattr(self.island, "source", None)
        if self.error or source is None or r.library is None or r.editor is None:
            return []
        shown = [b for b in r._all_batches() if b.count and b.parts and r._layer_on(b, opts) and b.mats is not None]
        placed = [(b.mats, b.key[1]) for b in shown if b.key[0] != "markers"] + _decal_objects(shown)
        r.library.request({cfg for _mats, cfg in placed})
        key = tuple((b.key, b.version) for b in shown), tuple(r.library.parts(cfg) is None for _m, cfg in placed)
        if key != self._key:  # the objects moved, came or went: the decals grouped again, their draws made again
            self._key = key
            self._regroup(gamedecals.decal_looks([(mats, r.library.decals(cfg)) for mats, cfg in placed]))
        self.waiting, out = sum(r.library.parts(cfg) is None for _mats, cfg in placed), []  # configs resolving
        for look in self.looks.values():
            got = r.loader.get(look.job, lambda v=look.values: _compile(source, r.editor.files, v))
            if got is None:
                self.waiting += 1
            elif isinstance(got, Exception):
                self._fail(f"{type(got).__name__}: {got}")
                return []
            elif not look.draws:
                look.draws = self._draws(got, look, textures)
            out += look.draws
        return [] if self.waiting else out

    def _regroup(self, found: dict) -> None:
        for look in self.looks.values():
            look.release()
        self.looks = {}
        for k, shapes in found.items():
            self.looks[k] = Look((JOB, k), dict(k), shapes)

    def _draws(self, got: tuple, look: Look, textures) -> list:
        stages, material = got
        key = (stages.vs.entry, stages.vs.blob, stages.ps.entry, stages.ps.blob)
        program = self.programs.get(key)
        if program is None:
            program = self.programs[key] = gamedraw.Program(stages.vs, stages.ps)
        state = gamedecals.decal_state(stages.ps.state)
        return gamedecals.decal_draws(self.r.ctx.rhi, program, material, state, look.shapes, look.maps, textures)

    def _fail(self, why: str) -> None:
        self.error = why
        self.r._message(tr("islands.games_decals_reason", reason=why[:300]))
        self.release()

    def take_failures(self, failed: list) -> None:
        """A decal draw that failed in the last frame (taken out of ``failed``): the view's own decals from now on."""
        mine = {id(d) for look in self.looks.values() for d in look.draws}
        found = [x for x in failed if id(x[0]) in mine]
        if found:
            failed[:] = [x for x in failed if id(x[0]) not in mine]
            self._fail(found[0][1])

    def release(self) -> None:
        for look in self.looks.values():
            look.release()
        self.looks, self.programs, self._key = {}, {}, None


def _decal_objects(batches: list) -> list[tuple]:
    """(matrices, config) of the objects that draw nothing but decals (the island's beach, crack and cave decals:
    pins in the view, ``assetmodels.Reason.EMPTY``), per config, from the marker batches among ``batches``."""
    found: dict[str, list] = {}
    for b in batches:
        if b.key[0] != "markers":
            continue
        for k, e in enumerate(b.entries):
            if e.cfg and e.marker == "NoModel" and k < len(b.mats):
                found.setdefault(e.cfg, []).append(b.mats[k])
    return [(np.asarray(mats), cfg) for cfg, mats in found.items()]


def _compile(source, files, values: dict) -> tuple:
    """In the background: a decal material's stages (``decal_pbr``'s G-buffer technique) and its material."""
    stages = source.draw_stages(gamedecals.EFFECT, values)
    return stages, source.material(stages.refs, values, files)
