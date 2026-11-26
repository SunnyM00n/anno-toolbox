"""The game look's config decals (``editor.desktop.desktopgamedecals``): what a regroup keeps."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.editor.desktop import desktopgamedecals as decals


class FakeDraw:
    def __init__(self) -> None:
        self.m_released = False

    def release(self) -> None:
        self.m_released = True


def test_a_regroup_keeps_the_looks_that_stayed_and_the_texture_arrays_of_those_that_changed():
    """Each cursor move of the Place tool, and each placement, made every decal look's draws and texture arrays again
    (performance.md 6, item 3): a look whose decals stayed keeps its draws, one whose decals changed its arrays."""
    place = np.identity(4)
    moved = place.copy()
    moved[0, 3] = 3.0
    shape = ((1.0, 1.0), (0.0, 0.0, 1.0, 1.0))
    island = SimpleNamespace(m_renderer=None)
    game = decals.CGameDecals(island)
    array = SimpleNamespace(destroy=lambda: None)
    for key in ("stays", "moves", "goes"):
        look = game.m_looks[key] = decals.CLook((decals.JOB, key), {}, {shape: [place]})
        look.m_draws, look.m_maps = [FakeDraw()], [array]
    stays, moves, goes = (game.m_looks[key] for key in ("stays", "moves", "goes"))
    kept_draw, moved_draw = stays.m_draws[0], moves.m_draws[0]
    game._regroup({"stays": {shape: [place.copy()]}, "moves": {shape: [moved]}})
    assert game.m_looks["stays"] is stays and stays.m_draws == [kept_draw] and not kept_draw.m_released
    assert game.m_looks["moves"] is moves and moved_draw.m_released and moves.m_draws == [] and moves.m_maps == [array]
    assert moves.m_shapes == {shape: [moved]} and "goes" not in game.m_looks and goes.m_maps == []
