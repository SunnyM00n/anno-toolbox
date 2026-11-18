"""The asset editor's background jobs (check, build the mod, write the game's files) read a snapshot of the document:
an edit made while one runs neither breaks it nor changes what it writes. Offscreen, with a job held on an event."""

from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication
from tests.fakes import asset_game

from annotoolbox.assetedit import assetgame, checks, native
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.assetwindow import AssetWindow
from annotoolbox.suite.settings import Settings


@pytest.fixture
def window(tmp_path):
    qt = QApplication.instance() or QApplication([])
    settings = Settings(anno117=str(asset_game(tmp_path / "117")), workspace=str(tmp_path / "ws"))
    win = AssetWindow(settings, tmp_path / "s.json")
    win.open("anno117", wait=True)
    yield win
    win.doc.undo.mark_clean()  # closing a changed document asks whether to save it
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
    assert snap.changes.assets[2001].set == {"Building/Upkeep": "7"} and not snap.changes.texts
    assert snap.layers(2001).lookup(("Building", "Upkeep")).text == "7"
    assert snap.index is doc.index and snap.files is doc.files and snap.undo is not doc.undo


def test_an_edit_while_the_mod_is_checked_and_written_is_not_in_the_mod(window, monkeypatch, tmp_path):
    qt = QApplication.instance()
    window.doc.set_value(2001, "Building/Upkeep", "7")
    release, started = held(monkeypatch, checks, "final_check")
    window.build_mod(tmp_path / "mods")
    assert started.wait(30)
    window.doc.set_value(2001, "Building/Upkeep", "4242")  # the job is in the middle of the check
    window.doc.set_text("english", "9", "typed meanwhile")
    release.set()
    for _ in range(2):  # the check, then the write
        window.background.join()
        qt.processEvents()
    written = text_of(tmp_path / "mods" / window.doc.changes.mod_id)
    assert "<Upkeep>7</Upkeep>" in written and "4242" not in written and "typed meanwhile" not in written
    assert window.doc.changes.assets[2001].set == {"Building/Upkeep": "4242"}  # the window's document kept the edit
    assert window.doc.dirty and not window.working


def test_an_edit_while_the_games_files_are_written_is_not_in_them(window, monkeypatch, tmp_path):
    qt = QApplication.instance()
    window.doc.set_value(2001, "Building/Upkeep", "7")
    release, started = held(monkeypatch, native, "render")
    window.write_native(tmp_path / "native")
    assert started.wait(30)
    window.doc.set_value(2001, "Building/Upkeep", "4242")
    release.set()
    window.background.join()
    qt.processEvents()
    written = text_of(tmp_path / "native")
    assert "<Upkeep>7</Upkeep>" in written and "4242" not in written
    assert window.doc.changes.assets[2001].set == {"Building/Upkeep": "4242"} and not window.working


def test_another_document_forgets_the_clipboard_and_fills_the_tab_in_front(window):
    window.go_to(2001)
    window.clip(cut=False)
    window.tabs.setCurrentWidget(window.balancing)
    old = window.doc
    assert window.balancing.doc is old and window.clipboard == ([2001], False)
    window._attach(window.game.document())  # a change set loaded, a mod imported, another game
    assert window.clipboard is None and window.balancing.doc is window.doc is not old
    window.paste()  # nothing to paste: said, no asset made
    assert not window.doc.changes.new


def test_a_pasted_asset_that_is_gone_is_left_out(window):
    window.go_to(2001)
    made = window.doc.new_asset("", "Copy", copy_of=2001)
    window.clipboard = ([made, 2002], False)
    window.doc.remove_asset(made)
    window.go_to(2001)
    window.paste()
    assert [n.copy_of for n in window.doc.changes.new.values()] == [2002]


def test_no_second_game_opens_while_one_opens(window, monkeypatch):
    qt = QApplication.instance()
    release, started = held(monkeypatch, assetgame, "open_game")
    window.open("anno117")
    assert started.wait(30) and not window.open_menu.isEnabled()
    release.set()
    window.background.join()
    qt.processEvents()
    assert window.open_menu.isEnabled() and not window.opening


def test_a_game_opened_again_closes_the_index_of_the_one_before(window):
    old = window.game.index
    old.element(2001)
    assert old._maps
    window.open("anno117", wait=True)
    assert window.game.index is not old and not old._maps


def test_the_check_reads_a_snapshot_not_the_document_being_edited(window, monkeypatch):
    seen = []
    monkeypatch.setattr(checks, "check", lambda doc: seen.append(doc) or [])
    window.doc.set_value(2001, "Building/Upkeep", "7")
    assert window.run_check(wait=True) == []
    assert seen[0] is not window.doc and seen[0].changes.assets[2001].set == {"Building/Upkeep": "7"}


def test_a_used_up_guid_range_is_said_not_raised(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: said.append(text))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("Another", True))
    window.doc.changes.guid_range = (2001, 2001)  # the one number is the game's
    window.go_to(2001)
    window.new_from(base=False)
    window.clip(cut=False)
    window.paste()
    assert len(said) == 2 and "2001" in said[0] and not window.doc.changes.new


def test_a_table_from_a_spreadsheet_is_read_with_its_mark_and_a_bad_file_is_said(window, monkeypatch, tmp_path):
    failed = []
    monkeypatch.setattr(window, "_failed", failed.append)
    table = tmp_path / "table.csv"
    table.write_bytes(b"\xef\xbb\xbfGUID,Name,Building/Upkeep\n2001,Hut,6\n")  # as a spreadsheet saves UTF-8
    window.import_csv(table)
    assert window.doc.changes.assets[2001].set == {"Building/Upkeep": "6"} and not failed
    table.write_bytes(b"GUID,Name\n2001,H\xfct\n")  # not UTF-8
    window.import_csv(table)
    window.import_texts(tmp_path / "missing.csv")
    window.export_csv(tmp_path)  # a folder is no file to write
    assert [type(e).__name__ for e in failed][:2] == ["UnicodeDecodeError", "FileNotFoundError"] and len(failed) == 3
