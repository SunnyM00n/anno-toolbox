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

from annotoolbox.assetedit import checks, native
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
