"""The Saves app's window, offscreen, on a synthetic Documents folder (the real one is never read): the saves listed,
one opened into the summary, the tree and the form, a change written beside it, the mods it needs and the link to the
Mods app, a file that is no save said in the status line, and what a slow or failed read must not leave behind."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox
from tests.fakes import fake_mod, fake_save117, fake_save2070
from tests.test_saveedit import damaged_117

from annotoolbox.formats import savegame117
from annotoolbox.saveedit import locations
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


@pytest.fixture
def window(qt, docs):
    """The window on the synthetic Documents folder, its work run at once; closed after the test."""
    folder, settings = docs
    w = SavesWindow(settings, None, sync=True, documents=folder)
    yield w
    w.close()


@pytest.fixture
def opened(window):
    """The window with the 117 save open."""
    window._picked(first_save(window))
    return window


@pytest.fixture
def warnings(monkeypatch) -> list[str]:
    """The texts of the warning boxes shown (none is opened)."""
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **_k: shown.append(a[2]))
    return shown


def first_save(w: SavesWindow):
    return w.list.topLevelItem(0).child(0)


def no_save(tmp_path):
    bad = tmp_path / "bad.a8s"
    bad.write_bytes(b"Resource File V2.2" + bytes(100))
    return bad


def test_the_saves_of_the_game_are_listed_and_one_opens(window):
    assert window.list.topLevelItem(0).text(0) == "Tester" and first_save(window).text(0) == "Autosave 0"
    window._picked(first_save(window))
    assert window.save is not None and window.save.game == "anno117"
    html = window.summary_view.toPlainText()
    assert "Autosave 0.a8s" in html and "m-one" in html and "Islands" in html  # the sessions' islands came after
    assert window.tree_model.rowCount() == 4
    assert window.write_button.isEnabled() and "money:41" in window.inputs


def test_a_change_is_written_beside_the_save(opened, warnings):
    w = opened
    w.inputs["name"].setText("Julia")
    w.inputs["money:41"].setText("123")
    assert w.changes() == {"name": "Julia", "money:41": 123}
    w.write_new()
    written = w.save.path.with_name("Autosave 0 (edited).a8s")
    back = savegame117.read(written)
    assert savegame117.read_player(back)["header.a7s"].name == "Julia" and savegame117.read_money(back) == 123
    assert "written" in w.statusBar().currentMessage()
    group = first_save(w).parent()
    assert "Autosave 0 (edited)" in [group.child(i).text(0) for i in range(group.childCount())]  # listed again
    w.inputs["money:41"].setText("lots")
    w.write_new()  # not a number: said, nothing written
    assert warnings and not written.with_name("Autosave 0 (edited 2).a8s").exists()


def test_the_mods_it_needs_and_the_link_to_the_mods_app(window):
    asked = []
    handler = lambda game, ids: asked.append((game, ids)) or True  # noqa: E731
    links.provide(links.SHOW_MODS, handler)
    try:
        window._picked(first_save(window))
        table = window.mods_table
        states = {table.item(r, 0).text(): table.item(r, 3).text() for r in range(table.rowCount())}
        assert states == {"m-one": "installed", "m-two": "missing"}
        assert window.mods_button.isEnabled()
        table.setCurrentCell(1, 0)
        window.show_in_mods()
        assert asked == [("anno117", ["m-two", "m-one"])]
    finally:
        links.withdraw(links.SHOW_MODS, handler)


def test_the_2070_save_opens_with_its_money_form(window):
    window.game_box.setCurrentIndex(window.game_box.findData("anno2070"))
    window._picked(first_save(window))
    assert list(window.inputs) == ["money"] and window.inputs["money"].text() == "80000"
    assert "carry no list of mods" in window.mods_note.text()


def test_the_tree_opens_a_part_and_shows_a_values_readings(opened):
    model = opened.tree_model
    meta = model.index(0, 0)
    assert model.canFetchMore(meta)
    model.fetchMore(meta)
    assert model.rowCount(meta) == 4
    opened._show_value(model.index(0, 0, meta))
    assert "u32: 80" in opened.value_view.toPlainText()
    opened.find_box.setText("LastModTime")
    opened.find_next()
    assert model.item(opened.tree.currentIndex()).name == "LastModTime"


def test_a_file_that_is_no_save_is_said(window, warnings, tmp_path):
    window.open_path(no_save(tmp_path), "anno117")
    assert warnings and window.save is None


# Faults the audit of 2026-11-17 found, one test each.
def test_a_file_that_does_not_open_leaves_the_open_save_writable(opened, warnings, tmp_path):
    opened.open_path(no_save(tmp_path), "anno117")
    assert warnings and opened.save.path.name == "Autosave 0.a8s"
    assert opened.write_button.isEnabled() and not opened.busy
    opened.inputs["money:41"].setText("7")
    opened.write_new()
    assert savegame117.read_money(savegame117.read(opened.save.path.with_name("Autosave 0 (edited).a8s"))) == 7


def test_a_second_pick_replaces_the_read_under_way_and_stop_keeps_the_open_save(opened, qt, tmp_path, monkeypatch):
    import threading

    from annotoolbox.infra.i18n import tr
    from annotoolbox.saveedit import container

    release, reads, real = threading.Event(), [], container.open_file

    def slow(path, game):
        reads.append((path.name, threading.current_thread()))
        assert release.wait(30)
        return real(path, game)

    def settle():
        release.set()
        for _ in range(3):  # the read, then what an opened save reads after it (its names, its islands)
            opened.background.join()
            qt.processEvents()
        release.clear()

    monkeypatch.setattr(container, "open_file", slow)
    first, other = opened.save, opened.save.path.with_name("Other.a8s")
    other.write_bytes(first.path.read_bytes())
    opened.sync = False
    opened.open_path(first.path, "anno117")
    assert opened.busy and not opened.stop_button.isHidden() and not opened.write_button.isEnabled()
    opened.open_path(other, "anno117")  # picked while the first is read: said, and the one shown in the end
    assert opened.statusBar().currentMessage() == tr("saves.reading_instead", name="Other.a8s", before=first.path.name)
    settle()
    assert [name for name, _thread in reads] == [first.path.name, "Other.a8s"]
    assert all(thread is not threading.main_thread() for _name, thread in reads)
    assert opened.save.path == other and not opened.busy and opened.stop_button.isHidden()

    shown = opened.save
    opened.open_path(first.path, "anno117")
    opened.stop_button.click()  # a long read given up: the save shown stays, and stays writable
    assert opened.statusBar().currentMessage() == tr("saves.read_stopped", name=first.path.name)
    assert not opened.busy and opened.stop_button.isHidden() and opened.write_button.isEnabled()
    settle()
    assert opened.save is shown and opened.statusBar().currentMessage() == tr(
        "saves.read_stopped", name=first.path.name
    )

    opened.busy = True  # a write under way
    opened.open_path(first.path, "anno117")
    assert opened.statusBar().currentMessage() == tr("saves.wait_for_write", name=first.path.name) and len(reads) == 3
    opened.busy = False


def test_the_mods_of_a_save_left_meanwhile_are_not_shown_for_the_next(window):
    held = []
    run = window.background.run

    def hold_the_mods(work, callback, wait=False):
        names = (getattr(callback, "__name__", ""), *callback.__code__.co_names)
        if "_needs" in names:  # the look into the mod folders: answered late
            held.append((work, callback))
        else:
            run(work, callback, wait)

    window.background.run = hold_the_mods
    window._picked(first_save(window))  # the 117 save: its mods are still looked up ...
    window.game_box.setCurrentIndex(window.game_box.findData("anno2070"))
    window._picked(first_save(window))  # ... when a save without a mod list is opened
    assert len(held) == 1
    work, callback = held[0]
    callback(work())
    assert window.mods_table.rowCount() == 0 and "carry no list of mods" in window.mods_note.text()


def test_a_save_whose_world_state_is_damaged_still_opens_read_only(window, warnings, tmp_path):
    window.open_path(damaged_117(tmp_path / "damaged.a8s"), "anno117")
    assert not warnings and window.save is not None and window.tree_model.rowCount() == 4
    text = window.summary_view.toPlainText()
    assert "data.a7s" in text and "read only" in text and "Tester" in text  # the notes, and the header's rows
    assert not window.write_button.isEnabled() and window.inputs == {}


def test_a_remembered_game_is_listed_once_at_the_start(qt, docs, monkeypatch):
    folder, settings = docs
    settings.apps["saves"] = {"game": "anno2070"}
    asked = []
    find = locations.find
    monkeypatch.setattr(locations, "find", lambda game, documents=None: asked.append(game) or find(game, documents))
    w = SavesWindow(settings, None, sync=True, documents=folder)
    assert asked == ["anno2070"] and first_save(w).text(0) == "Spielstand"
    w.close()


def test_a_folder_that_cannot_be_listed_is_said(window, monkeypatch):
    def fails(_game, _documents=None):
        raise OSError("the folder is gone")

    monkeypatch.setattr(locations, "find", fails)
    window.open_game("anno117")
    assert window.list.topLevelItemCount() == 0 and "the folder is gone" in window.statusBar().currentMessage()


def test_an_old_games_save_picked_by_hand_opens_as_the_shown_game(window, monkeypatch, tmp_path):
    old = tmp_path / "Meine Welt.sww"
    old.write_bytes(
        b"<WorldConfig><m_Name>Meine Welt</m_Name><m_Width>320</m_Width><m_Height>320</m_Height></WorldConfig>"
    )
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *_a, **_k: (str(old), ""))
    window.game_box.setCurrentIndex(window.game_box.findData("anno1701"))
    window.browse()
    assert window.save.game == "anno1701" and "Meine Welt" in window.summary_view.toPlainText()
