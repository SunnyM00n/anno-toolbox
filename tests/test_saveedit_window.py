"""The Saves app's window, offscreen, on a synthetic Documents folder (the real one is never read): the saves listed,
one opened into the summary, the tree and the form, a change written beside it, the mods it needs and the link to the
Mods app, a file that is no save said in the status line."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox
from tests.fakes import fake_mod, fake_save117, fake_save2070

from annotoolbox.formats import savegame117
from annotoolbox.saveedit.savewindow import SavesWindow
from annotoolbox.suite import links
from annotoolbox.suite.settings import Settings


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def docs(tmp_path):
    account = tmp_path / "Documents" / "Anno 117 - Pax Romana" / "accounts" / "abc" / "Tester"
    fake_save117(account / "Autosave 0.a8s", mods={"m-one": "1.0.0", "m-two": "2.0"})
    fake_save2070(tmp_path / "Documents" / "ANNO 2070" / "Accounts" / "Me" / "Savegames" / "Spielstand.sww")
    fake_mod(tmp_path / "mods", "m-one", {})
    return tmp_path / "Documents", Settings(mods=str(tmp_path / "mods"), workspace=str(tmp_path / "ws"))


def window(qt, docs) -> SavesWindow:
    folder, settings = docs
    return SavesWindow(settings, None, sync=True, documents=folder)


def first_save(w: SavesWindow):
    group = w.list.topLevelItem(0)
    return group.child(0)


def test_the_saves_of_the_game_are_listed_and_one_opens(qt, docs):
    w = window(qt, docs)
    assert w.list.topLevelItem(0).text(0) == "Tester" and first_save(w).text(0) == "Autosave 0"
    w._picked(first_save(w))
    assert w.save is not None and w.save.game == "anno117"
    html = w.summary_view.toPlainText()
    assert "Autosave 0.a8s" in html and "m-one" in html and "Islands" in html  # the sessions' islands came after
    assert w.tree_model.rowCount() == 4
    assert w.write_button.isEnabled() and "money:41" in w.inputs
    w.close()


def test_a_change_is_written_beside_the_save(qt, docs, monkeypatch):
    w = window(qt, docs)
    w._picked(first_save(w))
    w.inputs["name"].setText("Julia")
    w.inputs["money:41"].setText("123")
    assert w.changes() == {"name": "Julia", "money:41": 123}
    w.write_new()
    written = w.save.path.with_name("Autosave 0 (edited).a8s")
    back = savegame117.read(written)
    assert savegame117.read_player(back)["header.a7s"].name == "Julia" and savegame117.read_money(back) == 123
    assert "written" in w.statusBar().currentMessage()
    names = [first_save(w).parent().child(i).text(0) for i in range(first_save(w).parent().childCount())]
    assert "Autosave 0 (edited)" in names  # listed again
    w.inputs["money:41"].setText("lots")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    w.write_new()  # not a number: said, nothing written
    assert not written.with_name("Autosave 0 (edited 2).a8s").exists()
    w.close()


def test_the_mods_it_needs_and_the_link_to_the_mods_app(qt, docs):
    w = window(qt, docs)
    asked = []
    handler = lambda game, ids: asked.append((game, ids)) or True  # noqa: E731
    links.provide(links.SHOW_MODS, handler)
    try:
        w._picked(first_save(w))
        table = w.mods_table
        states = {table.item(r, 0).text(): table.item(r, 3).text() for r in range(table.rowCount())}
        assert states == {"m-one": "installed", "m-two": "missing"}
        assert w.mods_button.isEnabled()
        table.setCurrentCell(1, 0)
        w.show_in_mods()
        assert asked == [("anno117", ["m-two", "m-one"])]
    finally:
        links.withdraw(links.SHOW_MODS, handler)
    w.close()


def test_the_2070_save_opens_with_its_money_form(qt, docs):
    w = window(qt, docs)
    w.game_box.setCurrentIndex(w.game_box.findData("anno2070"))
    w._picked(first_save(w))
    assert list(w.inputs) == ["money"] and w.inputs["money"].text() == "80000"
    assert "carry no list of mods" in w.mods_note.text()
    w.close()


def test_the_tree_opens_a_part_and_shows_a_values_readings(qt, docs):
    w = window(qt, docs)
    w._picked(first_save(w))
    model = w.tree_model
    meta = model.index(0, 0)
    assert model.canFetchMore(meta)
    model.fetchMore(meta)
    assert model.rowCount(meta) == 4
    w._show_value(model.index(0, 0, meta))
    assert "u32: 80" in w.value_view.toPlainText()
    w.find_box.setText("LastModTime")
    w.find_next()
    assert model.item(w.tree.currentIndex()).name == "LastModTime"
    w.close()


def test_a_file_that_is_no_save_is_said(qt, docs, tmp_path, monkeypatch):
    w = window(qt, docs)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    bad = tmp_path / "bad.a8s"
    bad.write_bytes(b"Resource File V2.2" + bytes(100))
    w.open_path(bad, "anno117")
    assert shown and w.save is None
    w.close()
