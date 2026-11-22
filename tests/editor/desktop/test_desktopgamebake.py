"""What the island view's game bake compares to know a slice or the ground changed: the objects themselves, never
their ids (a released texture's id is given again to the next one made)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def bake_module(monkeypatch):
    pytest.importorskip("PySide6")
    from annotoolbox.editor.desktop import desktopgamebake

    # every object "has" the same id, as a reused one does: a key that holds ids cannot tell objects apart
    monkeypatch.setattr(desktopgamebake, "id", lambda _obj: 1, raising=False)
    return desktopgamebake


def test_a_page_with_new_textures_is_not_baked_with_(bake_module):
    old = {3: (object(), object())}
    new = {3: (object(), object())}
    assert bake_module.baked_with("stored", 3, old) == bake_module.baked_with("stored", 3, old)
    assert bake_module.baked_with("stored", 3, old) != bake_module.baked_with("stored", 3, new)


def test_a_node_without_a_page_is_baked_with_none(bake_module):
    assert bake_module.baked_with("grid", None, {}) == (False, None, None)


def test_the_ground_state_changes_with_the_weights_texture(bake_module):
    def state(weights_tex):
        view = SimpleNamespace(
            m_renderer=SimpleNamespace(m_ground={"ids": (1,), "known": ()}, _m_weights_tex=weights_tex, m_textures={})
        )
        return bake_module.CIslandBake._bake_state(view, SimpleNamespace(look2070=False))

    first, second = object(), object()
    assert state(first) == state(first)
    assert state(first) != state(second)
