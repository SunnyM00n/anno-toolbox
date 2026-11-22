"""The asset editor's background jobs (check, build the mod, write the game's files) read a snapshot of the document:
an edit made while one runs neither breaks it nor changes what it writes. Offscreen, with a job held on an event."""

from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication
from tests.sources.sourcesfakes import asset_game

from annotoolbox.assetedit import assetgame, checks, native
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.assetwindow import CAssetWindow
from annotoolbox.suite.settings import CSettings


@pytest.fixture
def window(tmp_path):
    qt = QApplication.instance() or QApplication([])
    settings = CSettings(anno117=str(asset_game(tmp_path / "117")), workspace=str(tmp_path / "ws"))
    win = CAssetWindow(settings, tmp_path / "s.json")
    win.open("anno117", is_waiting=True)
    yield win
    win.m_doc.m_undo.mark_clean()  # closing a changed document asks whether to save it
    win.close()
    qt.processEvents()


def text_of(folder) -> str:
    return "".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(folder.rglob("*")) if p.is_file())


def held(monkeypatch, module, name):
    """``module.name`` waits on an event the test sets, then does what it did; returns the event and a started flag."""
    release, started, real = threading.Event(), threading.Event(), getattr(module, name)

    def slow(*args, **kwargs):
        started.set()
        assert release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, slow)
    return release, started


def test_the_snapshot_of_a_document_is_apart_from_it(tmp_path):
    doc = open_game("anno117", asset_game(tmp_path / "117"), tmp_path / "cache").document()
    doc.set_value(2001, "Building/Upkeep", "7")
    snap = doc.snapshot()
    doc.set_value(2001, "Building/Upkeep", "8")
    doc.set_text("english", "5", "five")
    assert snap.m_changes.m_assets[2001].set == {"Building/Upkeep": "7"} and not snap.m_changes.m_texts
    assert snap.layers(2001).lookup(("Building", "Upkeep")).text == "7"
    assert snap.m_index is doc.m_index and snap.m_files is doc.m_files and snap.m_undo is not doc.m_undo


def test_an_edit_while_the_mod_is_checked_and_written_is_not_in_the_mod(window, monkeypatch, tmp_path):
    qt = QApplication.instance()
    window.m_doc.set_value(2001, "Building/Upkeep", "7")
    release, started = held(monkeypatch, checks, "final_check")
    window.build_mod(tmp_path / "mods")
    assert started.wait(30)
    window.m_doc.set_value(2001, "Building/Upkeep", "4242")  # the job is in the middle of the check
    window.m_doc.set_text("english", "9", "typed meanwhile")
    release.set()
    for _ in range(2):  # the check, then the write
        window.m_background.join()
        qt.processEvents()
    written = text_of(tmp_path / "mods" / window.m_doc.m_changes.m_mod_id)
    assert "<Upkeep>7</Upkeep>" in written and "4242" not in written and "typed meanwhile" not in written
    assert window.m_doc.m_changes.m_assets[2001].set == {
        "Building/Upkeep": "4242"
    }  # the window's document kept the edit
    assert window.m_doc.dirty and not window.m_working


def test_an_edit_while_the_games_files_are_written_is_not_in_them(window, monkeypatch, tmp_path):
    qt = QApplication.instance()
    window.m_doc.set_value(2001, "Building/Upkeep", "7")
    release, started = held(monkeypatch, native, "render")
    window.write_native(tmp_path / "native")
    assert started.wait(30)
    window.m_doc.set_value(2001, "Building/Upkeep", "4242")
    release.set()
    window.m_background.join()
    qt.processEvents()
    written = text_of(tmp_path / "native")
    assert "<Upkeep>7</Upkeep>" in written and "4242" not in written
    assert window.m_doc.m_changes.m_assets[2001].set == {"Building/Upkeep": "4242"} and not window.m_working


def test_another_document_forgets_the_clipboard_and_fills_the_tab_in_front(window):
    window.go_to(2001)
    window.clip(cut=False)
    window.m_tabs.setCurrentWidget(window.m_balancing)
    old = window.m_doc
    assert window.m_balancing.m_doc is old and window.m_clipboard == ([2001], False)
    window._attach(window.m_game.document())  # a change set loaded, a mod imported, another game
    assert window.m_clipboard is None and window.m_balancing.m_doc is window.m_doc is not old
    window.paste()  # nothing to paste: said, no asset made
    assert not window.m_doc.m_changes.m_new


def test_a_save_as_that_fails_keeps_the_change_sets_own_file(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    before = window.m_doc.m_path
    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text: said.append(text))
    (tmp_path / "a file").write_text("x")
    target = tmp_path / "a file" / "mine.json"  # below a file: no folder can be made for it
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    window.m_doc.set_value(2001, "Building/Upkeep", "7")
    window.save_as()
    assert said and window.m_doc.m_path == before and window.state.get("edits", {}).get("anno117") != str(target)
    ok = tmp_path / "mine.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(ok), ""))
    window.save_as()
    assert ok.is_file() and window.m_doc.m_path == ok and window.state["edits"]["anno117"] == str(ok)


def test_a_pasted_asset_that_is_gone_is_left_out(window):
    window.go_to(2001)
    made = window.m_doc.new_asset("", "Copy", copy_of=2001)
    window.m_clipboard = ([made, 2002], False)
    window.m_doc.remove_asset(made)
    window.go_to(2001)
    window.paste()
    assert [n.copy_of for n in window.m_doc.m_changes.m_new.values()] == [2002]


def test_no_second_game_opens_while_one_opens(window, monkeypatch):
    qt = QApplication.instance()
    release, started = held(monkeypatch, assetgame, "open_game")
    window.open("anno117")
    assert started.wait(30) and not window.m_open_menu.isEnabled()
    release.set()
    window.m_background.join()
    qt.processEvents()
    assert window.m_open_menu.isEnabled() and not window.m_opening


def test_a_game_opened_again_closes_the_index_of_the_one_before(window):
    old = window.m_game.m_index
    old.element(2001)
    assert old._m_maps
    window.open("anno117", is_waiting=True)
    assert window.m_game.m_index is not old and not old._m_maps


def test_the_check_reads_a_snapshot_not_the_document_being_edited(window, monkeypatch):
    seen = []
    monkeypatch.setattr(checks, "check", lambda doc: seen.append(doc) or [])
    window.m_doc.set_value(2001, "Building/Upkeep", "7")
    assert window.run_check(is_waiting=True) == []
    assert seen[0] is not window.m_doc and seen[0].m_changes.m_assets[2001].set == {"Building/Upkeep": "7"}


def test_a_used_up_guid_range_is_said_not_raised(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: said.append(text))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("Another", True))
    window.m_doc.m_changes.m_guid_range = (2001, 2001)  # the one number is the game's
    window.go_to(2001)
    window.new_from(base=False)
    window.clip(cut=False)
    window.paste()
    assert len(said) == 2 and "2001" in said[0] and not window.m_doc.m_changes.m_new


def test_a_table_from_a_spreadsheet_is_read_with_its_mark_and_a_bad_file_is_said(window, monkeypatch, tmp_path):
    failed = []
    monkeypatch.setattr(window, "_failed", failed.append)
    table = tmp_path / "table.csv"
    table.write_bytes(b"\xef\xbb\xbfGUID,Name,Building/Upkeep\n2001,Hut,6\n")  # as a spreadsheet saves UTF-8
    window.import_csv(table)
    assert window.m_doc.m_changes.m_assets[2001].set == {"Building/Upkeep": "6"} and not failed
    table.write_bytes(b"GUID,Name\n2001,H\xfct\n")  # not UTF-8
    window.import_csv(table)
    window.import_texts(tmp_path / "missing.csv")
    window.export_csv(tmp_path)  # a folder is no file to write
    assert [type(e).__name__ for e in failed][:2] == ["UnicodeDecodeError", "FileNotFoundError"] and len(failed) == 3


def test_an_archive_import_without_a_workspace_refuses_instead_of_writing_beside_the_current_folder(window, tmp_path):
    window.m_settings.workspace = ""
    before = list(tmp_path.glob("**/*"))
    window.import_archive(tmp_path / "x.rda", is_waiting=True)
    assert not (tmp_path / "imported-archives").exists() and list(tmp_path.glob("**/*")) == before
    assert "workspace" in window.statusBar().currentMessage().lower()


def test_the_window_does_not_close_the_game_index_under_a_running_job(window, monkeypatch, tmp_path):
    qt = QApplication.instance()
    release, started = held(monkeypatch, checks, "final_check")
    window.show()
    window.build_mod(tmp_path / "mods")
    assert started.wait(30)
    closed = []
    monkeypatch.setattr(window.m_game.m_index, "close", lambda: closed.append(True))
    assert not window.close() and window.isVisible() and not closed  # the job still reads the index
    release.set()
    for _ in range(2):
        window.m_background.join()
        qt.processEvents()
    assert not window.m_working
    assert window.close() and closed == [True]
