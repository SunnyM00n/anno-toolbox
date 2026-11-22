"""The map editor's window and dialogs, offscreen, on a synthetic game: a map opened, edited, grown, kept and written
as three difficulties, the mine slot dialog, and what a slow read, another game or a failed step must not break
(docs/map-editor.md "The window")."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QMessageBox
from tests.mapedit.mapeditfakes import ISLAND, STEM
from tests.mapedit.mapeditfakes import mapedit_game as _game

from annotoolbox.mapedit import mapdialogs, mapdoc, pool, projectpart, template
from annotoolbox.mapedit.mapdialogs import CDetailsDialog
from annotoolbox.mapedit.mapwindow import CMapWindow
from annotoolbox.mapedit.template import KIND_FIXED
from annotoolbox.suite.settings import CSettings

MAP = STEM + ".a7tinfo"


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def settle(window: CMapWindow) -> None:
    window.m_background.join()
    QApplication.processEvents()


@pytest.fixture
def window(qt, tmp_path):
    """The window on a synthetic 117 with its one map open; closed without the question about unkept changes."""
    w = CMapWindow(CSettings(workspace=str(tmp_path / "ws")))
    tree = _game(tmp_path / "game")
    w.m_tree, w.m_key, w.m_maps = tree, "anno117", pool.CGameMaps.read(tree, "anno117")
    w.m_paths = mapdoc.map_paths(tree, "anno117")
    w._fill_list()
    w.open_map(MAP, is_waiting=True)
    settle(w)
    yield w
    w.m_doc.m_undos.clear()
    w.close()


def kept_files(window: CMapWindow, game: str = "anno117") -> set[str]:
    return set(projectpart.EDITS.saved_files(window.project(), game))


def test_the_window_opens_a_map_and_edits_it(window):
    assert window.m_list.count() == 1
    assert window.m_doc is not None and window.m_panel.m_findings.count() >= 1
    window.m_view.setFixedSize(600, 600)
    window.m_view.fit()
    window.m_view.select([1])
    window._apply_element({"x": 320, "z": 320, "size": 2, "type": 1})
    assert (window.m_doc.elements[1].x, window.m_doc.elements[1].size) == (320, 2)
    window.undo()
    assert window.m_doc.elements[1].x == 200
    window.m_view.grab()  # paints every kind of element
    window._run_grow((64, 64, 0, 0), True, is_waiting=True)
    settle(window)
    assert window.m_doc.size == (2112, 2112)
    window.save(is_waiting=True)
    settle(window)
    assert {MAP, STEM + ".a7t", STEM + ".a7te"} <= kept_files(window)
    window.m_maps.m_islands.clear()  # an added island's grid is read off the window's thread before it is placed
    window._add_fixed(ISLAND, is_waiting=True)
    QApplication.processEvents()
    assert window.m_doc.elements[-1].MapFilePath == ISLAND and ISLAND in window.m_maps.m_islands


def test_the_window_writes_the_three_difficulties_and_mirrors(window):
    lo, hi = window.project().guid_range
    window.write_variants((True, "isles", {"easy": hi, "medium": hi - 1, "hard": hi - 2}), is_waiting=True)
    settle(window)
    mod = window.project().mod_id
    assert {f"data/mods/{mod}/maps/isles_{k}/isles_{k}.a7tinfo" for k in ("easy", "hard")} <= kept_files(window)
    window.write_variants((True, "bad", {"easy": lo - 1, "medium": hi - 3, "hard": hi - 4}), is_waiting=True)
    assert "outside" in window.statusBar().currentMessage()
    window.m_view.select([1])
    window.m_doc.mirror(window.m_view.m_selected, "x")
    assert len(window.m_doc.elements) == 5


def test_the_mine_slot_dialog_lists_the_islands_slots_and_returns_the_values(qt, monkeypatch):
    e = template.new_element(
        KIND_FIXED, 0, 0, MapFilePath=ISLAND, MineSlotActivation=((5, 1),), FertilitySetGUIDs=((1, 3656),)
    )
    slots = [pool.SSlotObject(5, 1, "random slot"), pool.SSlotObject(6, 1, "random slot")]
    maps = pool.CGameMaps("anno117", m_fertility_sets={31312: "Fertility Set"}, m_fertilities={2203: "Fertility Oats"})
    dialog = CDetailsDialog(e, slots, template.MINE_SLOT_ACTIVATION, maps, None)
    assert dialog.m_mine_slot_activation.rowCount() == 2 and dialog.m_areas.rowCount() == 1
    dialog.m_mine_slot_activation.cellWidget(1, 2).setCurrentIndex(2)  # inactive
    values = dialog.values()
    assert values["MineSlotActivation"] == ((5, 1), (6, 0)) and values["FertilitySetGUIDs"] == ((1, 3656),)
    dialog.m_areas.setCurrentCell(0, 0)
    picked = iter([31312, 2203, 2203])
    monkeypatch.setattr(mapdialogs, "pick_asset", lambda *_a: next(picked))
    dialog.pick_into(1, maps.m_fertility_sets)
    dialog.pick_into(2, maps.m_fertilities)
    dialog.pick_into(2, maps.m_fertilities)  # once only
    found = dialog.values()
    assert found["FertilitySetGUIDs"] == ((1, 31312),) and found["FertilitiesPerAreaIndex"] == ((1, (2203,)),)
    slot_assets = {1010500: "Coal Mine Slot"}
    other = CDetailsDialog(
        e, slots, template.MINE_SLOT_MAPPING, pool.CGameMaps("anno1800", m_slot_assets=slot_assets), None
    )
    box = other.m_mine_slot_activation.cellWidget(0, 2)
    box.setCurrentIndex(box.findData(1010500))
    assert other.values()["MineSlotActivation"][0] == (5, 1010500) and other.m_areas.rowCount() == 1


@pytest.mark.parametrize(
    ("column", "text"),
    [
        (1, "x"),  # no number
        (1, "31312, 31313"),  # an area has one set
        (1, str(2**32)),  # more than a GUID's 32 bits: the file could not hold it
        (2, "2203, -1"),
        (0, "40000"),  # an area index is 16 bits
    ],
)
def test_the_mine_slot_dialog_refuses_what_the_file_cannot_hold(qt, column, text):
    e = template.new_element(KIND_FIXED, 0, 0, MapFilePath=ISLAND, FertilitySetGUIDs=((1, 3656),))
    dialog = CDetailsDialog(e, [], template.MINE_SLOT_ACTIVATION, pool.CGameMaps("anno117"), None)
    dialog.m_areas.item(0, column).setText(text)
    with pytest.raises(ValueError):
        dialog.values()
    dialog.accept()
    assert dialog.m_problem.text() and not dialog.result()


def test_fertilities_no_guid_can_be_are_refused_by_the_panel(window):
    window.m_view.select([0])
    before = window.m_doc.elements[0].FertilityGuids
    window.m_panel.m_fertility_guids.setText(f"1, {2**32}")
    window.m_panel._element_values()
    assert window.m_doc.elements[0].FertilityGuids == before and not window.m_doc.m_undos
    window.m_doc.render()  # still writes


# Faults the audit of 2026-11-17 found, one test each.
def test_a_map_asked_for_earlier_does_not_replace_the_one_asked_for_last_which_says_so(window):
    from annotoolbox.infra.i18n import tr

    held = []
    run = window.m_background.run
    window.m_background.run = lambda work, callback, is_waiting=False: held.append((work, callback))
    window.open_map(MAP)  # asked first, read slowly
    assert window.statusBar().currentMessage() == tr("maps.opening_path", path=MAP)
    window.open_map(MAP)
    assert window.statusBar().currentMessage() == tr("maps.opening_instead", path=MAP, before=MAP)
    window.m_background.run = run
    (first_work, first_done), (last_work, last_done) = held
    last_done(last_work())
    shown = window.m_doc
    first_done(first_work())  # arrives late
    assert window.m_doc is shown
    window.open_map(MAP, is_waiting=True)  # nothing is read any more: an open like any other
    assert window.m_doc is not shown and "instead" not in window.statusBar().currentMessage()


def test_a_map_is_kept_under_its_own_game_after_another_game_was_opened(window):
    window.m_doc.move([1], 8, 8)
    window.m_key = "anno2070"  # File > Open game: the open map stays until another one is opened
    window.save(is_waiting=True)
    settle(window)
    assert MAP in kept_files(window, "anno117") and not kept_files(window, "anno2070")


def test_leaving_a_map_whose_save_fails_stays_on_it(window, monkeypatch):
    window.m_doc.move([1], 8, 8)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)

    def fails(*_a, **_k):
        raise OSError("the disk is full")

    monkeypatch.setattr(projectpart, "save", fails)
    assert not window._may_leave() and "disk is full" in window.statusBar().currentMessage()
    monkeypatch.undo()
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)
    assert window._may_leave() and MAP in kept_files(window)


def test_apply_to_map_with_nothing_changed_is_no_step(window):
    window.m_panel._map_values()
    assert not window.m_doc.m_undos and window._may_leave()  # no question about changes: there are none
    window.m_panel.m_area[0].setValue(window.m_doc.playable[0] + 4)
    window.m_panel._map_values()
    assert len(window.m_doc.m_undos) == 1


def test_a_growth_that_fails_is_said_and_leaves_the_map(window, tmp_path):
    (tmp_path / "game" / (STEM + ".a7t")).write_bytes(b"no archive")
    size, places = window.m_doc.size, [(e.x, e.z) for e in window.m_doc.elements]
    window._run_grow((64, 0, 0, 0), True, is_waiting=True)
    settle(window)
    assert "not an RDA archive" in window.statusBar().currentMessage()
    assert (window.m_doc.size, [(e.x, e.z) for e in window.m_doc.elements]) == (size, places)
    assert not window.m_doc.m_undos and not window.m_doc.changed()


def test_a_complex_of_another_map_is_found_whatever_the_case_of_its_paths(window, monkeypatch):
    other = mapdoc.open_map(window.m_doc.m_tree, "anno117", MAP, window.m_doc.m_maps)
    other.elements[0].MapFilePath = ISLAND.upper()
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getItem", lambda _p, _t, _l, items, *_a: (items[0], True))
    window.m_maps.m_islands.clear()
    window._place_complex(other, [[0]])
    settle(window)
    assert ISLAND in window.m_doc.m_extra_files  # under the key ``CMapDoc.info`` asks for


def test_a_resize_keeps_the_view_the_user_zoomed_or_panned(window):
    window.show()
    view = window.m_view
    view.setFixedSize(500, 400)
    QApplication.processEvents()
    fitted = (view.m_scale, QPointF(view.m_origin))
    view.setFixedSize(700, 600)
    QApplication.processEvents()
    assert view.m_scale != fitted[0]  # untouched: a resize refits
    view.fit()
    view.m_origin += QPointF(30, 10)
    view._m_has_user_view = True  # what a pan or a wheel step sets
    moved = (view.m_scale, QPointF(view.m_origin))
    view.setFixedSize(500, 400)
    QApplication.processEvents()
    assert (view.m_scale, view.m_origin) == moved
    view.fit()  # the user's fit action
    assert (view.m_scale, view.m_origin) != moved
    view.setFixedSize(800, 700)
    QApplication.processEvents()
    assert view.m_scale == pytest.approx(0.95 * 700 / max(*window.m_doc.size))


def test_what_a_save_made_is_remembered_per_game_and_project(window):
    window.m_doc.move([1], 8, 8)
    window.save(is_waiting=True)
    settle(window)
    first = set(window.m_kept[("anno117", str(window.project()), MAP)])
    assert first
    window.m_doc.m_game = "anno2070"  # the same game path in another game
    window.m_doc.move([1], 8, 8)
    window.save(is_waiting=True)
    settle(window)
    assert window.m_kept[("anno117", str(window.project()), MAP)] == first and len(window.m_kept) == 2
    window.m_doc.m_game = "anno117"
