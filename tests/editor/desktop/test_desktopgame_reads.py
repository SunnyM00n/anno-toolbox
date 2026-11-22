"""Reads the attribute renames (``m_`` on classes, plain fields on records) had left on the old spelling, in the
desktop view's game look: each test drives the reader with the real class, or a stand-in carrying the real class's
names."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_the_desktop_view_hands_the_native_water_to_the_frames_passes():
    pytest.importorskip("PySide6")
    from annotoolbox.editor.desktop.desktopgame import CGameIsland
    from annotoolbox.render.gameframe import SPasses

    island = object.__new__(CGameIsland)
    island.m_native_water = SimpleNamespace(m_error="", coast_waves="the coast step")
    island.m_native_objects = None
    waves = SimpleNamespace(m_native=None)  # as gamesea.CCoastWaves
    passes = SPasses(sea=SimpleNamespace(m_waves=waves))
    island._water_passes(passes)
    assert waves.m_native == "the coast step"
    assert passes.native_water is not None and passes.native_water.water is island.m_native_water
