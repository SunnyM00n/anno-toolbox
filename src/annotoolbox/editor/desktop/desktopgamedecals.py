"""The island's config decals in the look "The game's own shaders": each shown model's DECALs by the game's decal
shader (``render.gamedecals``) into the G-buffer after the terrain, laid on the session's heights by the shader, one
draw per look and shape of up to 204 decals; while a look compiles, or when one fails, the view's own decals.

``CGameDecals(island)`` is the entry (one per ``desktopgame.CGameIsland``): ``frame(opts, textures)`` the draws of a
frame, ``take_failures`` and ``release``. docs/rendering/island-117-view.md "Config decals".
"""

from __future__ import annotations

import numpy as np

from ...infra.i18n import tr
from ...render import gamedecals, gamedraw

# with a material's values, its loader job: the loader outlives a look (made again for another rendering
# quality), so the key names the material itself, not the order it was met in
JOB = "game-decal"


class CLook:
    """One material of the island's decals: its loader job, its decals by shape, its draws and their maps."""

    def __init__(self, job: tuple, values: dict, shapes: dict) -> None:
        self.m_job, self.m_values, self.m_shapes = job, values, shapes
        self.m_draws: list = []
        self.m_maps: list = []  # the look's texture arrays (``gamedecals.decal_draws``)

    def release(self) -> None:
        for draw in self.m_draws:
            draw.release()
        gamedecals.release_maps(self.m_maps)
        self.m_draws = []


class CGameDecals:
    """The decals of the island's shown models by the game's decal shader (module docstring)."""

    def __init__(self, island) -> None:
        self.m_island, self.m_renderer = island, island.m_renderer
        self.m_looks: dict[tuple, CLook] = {}
        self.m_programs: dict = {}
        self.m_waiting = 0  # looks still compiling
        self.m_error = ""  # why the game's decals do not draw (said once): the view's own from then on
        self._m_key: tuple | None = None

    def frame(self, options, textures) -> list:
        """This frame's decal draws (``textures``: the session maps the terrain stands in for), [] while any look
        compiles or after a failure (the view then draws its own: ``Renderer.game_decals`` False)."""
        renderer = self.m_renderer
        source = getattr(self.m_island, "m_source", None)
        if self.m_error or source is None or renderer.m_library is None or renderer.m_editor is None:
            return []
        shown = [
            batch
            for batch in renderer._all_batches()
            if batch.m_count and batch.m_parts and renderer._layer_on(batch, options) and batch.m_mats is not None
        ]
        placed = [(batch.m_mats, batch.m_key[1]) for batch in shown if batch.m_key[0] != "markers"] + _decal_objects(
            shown
        )
        renderer.m_library.request({cfg for _mats, cfg in placed})
        key = (
            tuple((batch.m_key, batch.m_version) for batch in shown),
            tuple(renderer.m_library.parts(cfg) is None for _m, cfg in placed),
        )
        if key != self._m_key:  # the objects moved, came or went: the decals grouped again, their draws made again
            self._m_key = key
            self._regroup(
                gamedecals.decal_looks([(materials, renderer.m_library.decals(cfg)) for materials, cfg in placed])
            )
        self.m_waiting, out = (
            sum(renderer.m_library.parts(cfg) is None for _mats, cfg in placed),
            [],
        )  # configs resolving
        for look in self.m_looks.values():
            got = renderer.m_loader.get(
                look.m_job, lambda value=look.m_values: _compile(source, renderer.m_editor.m_files, value)
            )
            if got is None:
                self.m_waiting += 1
            elif isinstance(got, Exception):
                self._fail(f"{type(got).__name__}: {got}")
                return []
            elif not look.m_draws:
                look.m_draws = self._draws(got, look, textures)
            out += look.m_draws
        return [] if self.m_waiting else out

    def _regroup(self, found: dict) -> None:
        for look in self.m_looks.values():
            look.release()
        self.m_looks = {}
        for k, shapes in found.items():
            self.m_looks[k] = CLook((JOB, k), dict(k), shapes)

    def _draws(self, got: tuple, look: CLook, textures) -> list:
        stages, material = got
        key = (stages.vertex_stage.entry, stages.vertex_stage.blob, stages.pixel_stage.entry, stages.pixel_stage.blob)
        program = self.m_programs.get(key)
        if program is None:
            program = self.m_programs[key] = gamedraw.CProgram(stages.vertex_stage, stages.pixel_stage)
        state = gamedecals.decal_state(stages.pixel_stage.state)
        return gamedecals.decal_draws(
            self.m_renderer.m_ctx.m_rhi, program, material, state, look.m_shapes, look.m_maps, textures
        )

    def _fail(self, why: str) -> None:
        self.m_error = why
        self.m_renderer._message(tr("islands.games_decals_reason", reason=why[:300]))
        self.release()

    def take_failures(self, failed: list) -> None:
        """A decal draw that failed in the last frame (taken out of ``failed``): the view's own decals from now on."""
        mine = {id(draw) for look in self.m_looks.values() for draw in look.m_draws}
        found = [x for x in failed if id(x[0]) in mine]
        if found:
            failed[:] = [x for x in failed if id(x[0]) not in mine]
            self._fail(found[0][1])

    def release(self) -> None:
        for look in self.m_looks.values():
            look.release()
        self.m_looks, self.m_programs, self._m_key = {}, {}, None


def _decal_objects(batches: list) -> list[tuple]:
    """(matrices, config) of the objects that draw nothing but decals (the island's beach, crack and cave decals:
    pins in the view, ``assetmodels.CReason.EMPTY``), per config, from the marker batches among ``batches``."""
    found: dict[str, list] = {}
    for batch in batches:
        if batch.m_key[0] != "markers":
            continue
        for k, entry in enumerate(batch.m_entries):
            if entry.cfg and entry.marker == "NoModel" and k < len(batch.m_mats):
                found.setdefault(entry.cfg, []).append(batch.m_mats[k])
    return [(np.asarray(materials), cfg) for cfg, materials in found.items()]


def _compile(source, files, values: dict) -> tuple:
    """In the background: a decal material's stages (``decal_pbr``'s G-buffer technique) and its material."""
    stages = source.draw_stages(gamedecals.EFFECT, values)
    return stages, source.material(stages.refs, values, files)
