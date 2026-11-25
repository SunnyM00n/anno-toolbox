"""The view's frame passes (``editor.desktop.desktopframe``): what is kept from one frame to the next."""

from types import SimpleNamespace

import numpy as np
from tests.editor.editorfakes import edit_island

from annotoolbox.editor.desktop.desktopframe import CFramePasses
from annotoolbox.editor.editsession import CEditSession


def _view_of_a_session() -> tuple[SimpleNamespace, bytes]:
    session = CEditSession(edit_island())
    session.finish_loading()
    width = session.m_width_samples
    game = np.full((width, width), 3.0, "<f4").tobytes()
    return SimpleNamespace(_m_game_heights=None, m_session=session), game


def _sync(view, game: bytes, *, heights_edited: bool, game_terrain: bool = True):
    """One frame's ``_sync_scene`` of ``view`` with the game's heights file ``game`` loaded."""
    change = SimpleNamespace(heights=view.m_session.m_heights if heights_edited else None)
    view._take_pending = lambda: change
    view.m_editor, view.m_loader = SimpleNamespace(game_heights=None), SimpleNamespace(get=lambda key, read: game)
    view._m_shown_game_terrain, view._m_ground_version = False, 0
    view._update_terrain = view._sync_batches = view._sync_ground = lambda *args: None
    view._game_heights_of = lambda got: CFramePasses._game_heights_of(view, got)
    return CFramePasses._sync_scene(view, SimpleNamespace(game_terrain=game_terrain))


def test_the_games_heights_with_the_edits_are_kept_until_an_edit_or_a_new_file():
    view, game = _view_of_a_session()
    first = _sync(view, game, heights_edited=False)
    assert first.shape == view.m_session.m_heights.shape
    assert _sync(view, game, heights_edited=False) is first  # a still frame: not added up again
    view.m_session.m_heights[2, 2] += 1.5  # an edit of the heights announces itself in the frame's change
    edited = _sync(view, game, heights_edited=True)
    assert edited is not first and edited[2, 2] == first[2, 2] + 1.5
    other = bytes(bytearray(game))  # the game's file read again: another object
    assert _sync(view, other, heights_edited=False) is not edited


def test_an_edit_of_the_heights_while_the_game_terrain_is_off_is_in_its_heights_when_it_is_on_again():
    view, game = _view_of_a_session()
    first = _sync(view, game, heights_edited=False)
    view.m_session.m_heights[2, 2] += 1.5
    assert _sync(view, game, heights_edited=True, game_terrain=False) is None
    again = _sync(view, game, heights_edited=False)
    assert again[2, 2] == first[2, 2] + 1.5


def test_a_laying_of_the_decals_begun_before_the_games_decals_draw_is_given_up(monkeypatch):
    """The view's own decals are laid over several frames; once the game's decal shader draws them the view stops
    laying, so ``busy`` does not wait for a laying no frame goes on with (a parallel test run waited 800 s)."""
    from annotoolbox.editor.desktop import desktopdecals

    monkeypatch.setattr(desktopdecals, "DECAL_BUDGET", -1.0)  # every frame's share of the laying is used up at once
    layer = desktopdecals.CDecalLayer(SimpleNamespace(buffer=lambda data: object()))
    decal = {"kind": "Terrain", "on_streets": False, "diffuse": "", "normal": "", "color": (1.0, 1.0, 1.0), "opacity": 1.0,
             "extents": (1.0, 1.0), "matrix": np.identity(4)}  # fmt: skip
    batch = SimpleNamespace(m_count=1, m_parts=["part"], m_mats=np.array([np.identity(4)] * 3), m_key=("objects", "a.cfg"),
                            m_version=1)  # fmt: skip
    session = SimpleNamespace(m_heights=np.zeros((3, 3)), m_step_tiles=1.0, m_width_samples=3)
    view = SimpleNamespace(m_library=SimpleNamespace(decals=lambda cfg: [decal]), _all_batches=lambda: [batch],
                           _street_cells=lambda: [], _layer_on=lambda b, opts: True, _m_ground_version=0,
                           m_decals=layer, m_prog={"model": 0}, m_session=session, m_game_decals=False)  # fmt: skip
    frame = SimpleNamespace(game_heights=None)
    CFramePasses._lay_decals(view, frame, None)
    assert layer.is_laying()  # three objects: more frames to come
    view.m_game_decals = True  # the game's decals compiled: the view's own are neither laid nor drawn
    CFramePasses._lay_decals(view, frame, None)
    assert not layer.is_laying()
