"""The map editor's window and dialogs, offscreen, on a synthetic game: a map opened, edited, grown, kept and written
as three difficulties, the mine slot dialog, and what a slow read, another game or a failed step must not break
(docs/map-editor.md "The window")."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox
from tests.test_mapedit import ISLAND, STEM, _game

from annotoolbox.mapedit import mapdialogs, mapdoc, pool, projectpart, template
from annotoolbox.mapedit.mapdialogs import DetailsDialog
from annotoolbox.mapedit.mapwindow import MapWindow
from annotoolbox.mapedit.template import KIND_FIXED
from annotoolbox.suite.settings import Settings

MAP = STEM + ".a7tinfo"


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def settle(window: MapWindow) -> None:
    window.background.join()
    QApplication.processEvents()


@pytest.fixture
def window(qt, tmp_path):
    """The window on a synthetic 117 with its one map open; closed without the question about unkept changes."""
    w = MapWindow(Settings(workspace=str(tmp_path / "ws")))
    tree = _game(tmp_path / "game")
    w.tree, w.key, w.maps = tree, "anno117", pool.GameMaps.read(tree, "anno117")
    w.paths = mapdoc.map_paths(tree, "anno117")
    w._fill_list()
    w.open_map(MAP, wait=True)
    settle(w)
    yield w
    w.doc.undos.clear()
    w.close()


def kept_files(window: MapWindow, game: str = "anno117") -> set[str]:
    return set(projectpart.EDITS.saved_files(window.project(), game))


def test_the_window_opens_a_map_and_edits_it(window):
    assert window.list.count() == 1
    assert window.doc is not None and window.panel.findings.count() >= 1
    window.view.resize(600, 600)
    window.view.fit()
    window.view.select([1])
    window._apply_element({"x": 320, "z": 320, "size": 2, "type": 1})
    assert (window.doc.elements[1].x, window.doc.elements[1].size) == (320, 2)
    window.undo()
    assert window.doc.elements[1].x == 200
    window.view.grab()  # paints every kind of element
    window._run_grow((64, 64, 0, 0), True, wait=True)
    settle(window)
    assert window.doc.size == (2112, 2112)
    window.save(wait=True)
    settle(window)
    assert {MAP, STEM + ".a7t", STEM + ".a7te"} <= kept_files(window)
    window.maps.islands.clear()  # an added island's grid is read off the window's thread before it is placed
    window._add_fixed(ISLAND, wait=True)
    QApplication.processEvents()
    assert window.doc.elements[-1].map_file_path == ISLAND and ISLAND in window.maps.islands


def test_the_window_writes_the_three_difficulties_and_mirrors(window):
    lo, hi = window.project().guid_range
    window.write_variants((True, "isles", {"easy": hi, "medium": hi - 1, "hard": hi - 2}), wait=True)
    settle(window)
    mod = window.project().mod_id
    assert {f"data/mods/{mod}/maps/isles_{k}/isles_{k}.a7tinfo" for k in ("easy", "hard")} <= kept_files(window)
    window.write_variants((True, "bad", {"easy": lo - 1, "medium": hi - 3, "hard": hi - 4}), wait=True)
    assert "outside" in window.statusBar().currentMessage()
    window.view.select([1])
    window.doc.mirror(window.view.selected, "x")
    assert len(window.doc.elements) == 5


def test_the_mine_slot_dialog_lists_the_islands_slots_and_returns_the_values(qt, monkeypatch):
    e = template.new_element(
        KIND_FIXED, 0, 0, map_file_path=ISLAND, mine_slot_activation=((5, 1),), fertility_set_guids=((1, 3656),)
    )
    slots = [pool.SlotObject(5, 1, "random slot"), pool.SlotObject(6, 1, "random slot")]
    maps = pool.GameMaps("anno117", fertility_sets={31312: "Fertility Set"}, fertilities={2203: "Fertility Oats"})
    dialog = DetailsDialog(e, slots, template.MINE_SLOT_ACTIVATION, maps, None)
    assert dialog.mine_slot_activation.rowCount() == 2 and dialog.areas.rowCount() == 1
    dialog.mine_slot_activation.cellWidget(1, 2).setCurrentIndex(2)  # inactive
    values = dialog.values()
    assert values["mine_slot_activation"] == ((5, 1), (6, 0)) and values["fertility_set_guids"] == ((1, 3656),)
    dialog.areas.setCurrentCell(0, 0)
    picked = iter([31312, 2203, 2203])
    monkeypatch.setattr(mapdialogs, "pick_guid", lambda *_a: next(picked))
    dialog.pick_into(1, maps.fertility_sets)
    dialog.pick_into(2, maps.fertilities)
    dialog.pick_into(2, maps.fertilities)  # once only
    found = dialog.values()
    assert found["fertility_set_guids"] == ((1, 31312),) and found["fertilities_per_area_index"] == ((1, (2203,)),)
    slot_assets = {1010500: "Coal Mine Slot"}
    other = DetailsDialog(
        e, slots, template.MINE_SLOT_MAPPING, pool.GameMaps("anno1800", slot_assets=slot_assets), None
    )
    box = other.mine_slot_activation.cellWidget(0, 2)
    box.setCurrentIndex(box.findData(1010500))
    assert other.values()["mine_slot_activation"][0] == (5, 1010500) and other.areas.rowCount() == 1


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
    e = template.new_element(KIND_FIXED, 0, 0, map_file_path=ISLAND, fertility_set_guids=((1, 3656),))
    dialog = DetailsDialog(e, [], template.MINE_SLOT_ACTIVATION, pool.GameMaps("anno117"), None)
    dialog.areas.item(0, column).setText(text)
    with pytest.raises(ValueError):
        dialog.values()
    dialog.accept()
    assert dialog.problem.text() and not dialog.result()


def test_fertilities_no_guid_can_be_are_refused_by_the_panel(window):
    window.view.select([0])
    before = window.doc.elements[0].fertility_guids
    window.panel.fertility_guids.setText(f"1, {2**32}")
    window.panel._element_values()
    assert window.doc.elements[0].fertility_guids == before and not window.doc.undos
    window.doc.render()  # still writes


# Faults the audit of 2026-11-17 found, one test each.
def test_a_map_asked_for_earlier_does_not_replace_the_one_asked_for_last_which_says_so(window):
    from annotoolbox.infra.i18n import tr

    held = []
    run = window.background.run
    window.background.run = lambda work, callback, wait=False: held.append((work, callback))
    window.open_map(MAP)  # asked first, read slowly
    assert window.statusBar().currentMessage() == tr("maps.opening_path", path=MAP)
    window.open_map(MAP)
    assert window.statusBar().currentMessage() == tr("maps.opening_instead", path=MAP, before=MAP)
    window.background.run = run
    (first_work, first_done), (last_work, last_done) = held
    last_done(last_work())
    shown = window.doc
    first_done(first_work())  # arrives late
    assert window.doc is shown
    window.open_map(MAP, wait=True)  # nothing is read any more: an open like any other
    assert window.doc is not shown and "instead" not in window.statusBar().currentMessage()


def test_a_map_is_kept_under_its_own_game_after_another_game_was_opened(window):
    window.doc.move([1], 8, 8)
    window.key = "anno2070"  # File > Open game: the open map stays until another one is opened
    window.save(wait=True)
    settle(window)
    assert MAP in kept_files(window, "anno117") and not kept_files(window, "anno2070")


def test_leaving_a_map_whose_save_fails_stays_on_it(window, monkeypatch):
    window.doc.move([1], 8, 8)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)

    def fails(*_a, **_k):
        raise OSError("the disk is full")

    monkeypatch.setattr(projectpart, "save", fails)
    assert not window._may_leave() and "disk is full" in window.statusBar().currentMessage()
    monkeypatch.undo()
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)
    assert window._may_leave() and MAP in kept_files(window)


def test_apply_to_map_with_nothing_changed_is_no_step(window):
    window.panel._map_values()
    assert not window.doc.undos and window._may_leave()  # no question about changes: there are none
    window.panel.area[0].setValue(window.doc.playable[0] + 4)
    window.panel._map_values()
    assert len(window.doc.undos) == 1


def test_a_growth_that_fails_is_said_and_leaves_the_map(window, tmp_path):
    (tmp_path / "game" / (STEM + ".a7t")).write_bytes(b"no archive")
    size, places = window.doc.size, [(e.x, e.z) for e in window.doc.elements]
    window._run_grow((64, 0, 0, 0), True, wait=True)
    settle(window)
    assert "not an RDA archive" in window.statusBar().currentMessage()
    assert (window.doc.size, [(e.x, e.z) for e in window.doc.elements]) == (size, places)
    assert not window.doc.undos and not window.doc.changed()


def test_a_complex_of_another_map_is_found_whatever_the_case_of_its_paths(window, monkeypatch):
    other = mapdoc.open_map(window.doc.tree, "anno117", MAP, window.doc.maps)
    other.elements[0].map_file_path = ISLAND.upper()
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getItem", lambda _p, _t, _l, items, *_a: (items[0], True))
    window.maps.islands.clear()
    window._place_complex(other, [[0]])
    settle(window)
    assert ISLAND in window.doc.extra_files  # under the key ``MapDoc.info`` asks for
