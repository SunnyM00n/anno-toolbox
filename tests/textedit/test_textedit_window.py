"""The Texts window offscreen over a synthetic 117: the game opens in the background, two languages show side by side,
an edit is applied and kept in the project, a CSV goes out and comes back, and the window speaks German."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from tests.suite.suitefakes import shown_texts, untranslated
from tests.textedit.texteditfakes import AUDIO, GERMAN_117, META_TWINS, METADATA, PORTRAIT, XML_117, text_export

from annotoolbox.infra import i18n
from annotoolbox.suite.settings import CSettings
from annotoolbox.textedit import exchange, projectpart
from annotoolbox.textedit.editfile import CEditFile
from annotoolbox.textedit.textwindow import CTextWindow


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qt, tmp_path):
    gui = tmp_path / "game" / "data" / "base" / "config" / "gui"
    gui.mkdir(parents=True)
    for name, text in (("english", XML_117), ("german", GERMAN_117), ("metadata", METADATA)):
        (gui / f"texts_{name}.xml").write_text(text, encoding="utf-8", newline="")
    yield from _opened(qt, tmp_path)


def _opened(qt, tmp_path):
    """The window on the game written under ``tmp_path``, its texts read; closed without a question afterwards."""
    settings = CSettings(workspace=str(tmp_path / "ws"), mods=str(tmp_path / "mods"), anno117=str(tmp_path / "game"),
                        project=str(tmp_path / "proj"))  # fmt: skip
    win = CTextWindow(settings, tmp_path / "settings.json")
    win.open_game("anno117", is_waiting=True)
    settle(qt, win)
    yield win
    win.doc.m_dirty = False
    win.close()


def settle(qt, win) -> None:
    """The window's background jobs done and their answers handled."""
    for _ in range(3):
        win.m_background.join()
        qt.processEvents()


def test_the_game_opens_and_the_two_languages_show_side_by_side(window):
    assert window.m_model.m_ids == ["-10", "-11", "-12", "-13"] and window.m_source_box.currentData() == "english"
    assert window.m_target_box.currentData() == "german"
    assert window.m_model.index(3, 2).data() == "Wood" and window.m_model.index(3, 3).data() == "Holz"
    assert "untranslated" in window.statusBar().currentMessage()
    window.m_table.selectRow(3)
    assert window.m_detail.m_source_box.toPlainText() == "Wood" and window.m_detail.m_target_box.toPlainText() == "Holz"


def test_an_applied_edit_changes_the_row_shows_its_problems_and_is_kept_in_the_project(qt, window, tmp_path):
    window.m_table.selectRow(0)
    window.m_detail.m_target_box.setPlainText("Speichern {1}")
    assert any(
        "{0}" in line
        for line in [window.m_detail.m_problems.item(i).text() for i in range(window.m_detail.m_problems.count())]
    )
    window._apply("Speichern {1}")
    assert window.m_model.index(0, 1).data() == "changed" and window.doc.m_dirty
    window.save()
    saved = CEditFile.load(projectpart.edits_path(window.project(), "anno117"))
    assert saved.get("german", "-10").text == "Speichern {1}" and saved.get("german", "-10").source.startswith("Save")
    window.m_search.setText("holz")
    window._refilter()
    settle(qt, window)
    assert window.m_model.m_ids == ["-13"]


def test_a_save_that_fails_in_the_close_prompt_keeps_the_window_open(qt, window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    def refuse(_self, _path):
        raise OSError("the disk is full")

    shown: list[str] = []
    monkeypatch.setattr(CEditFile, "save", refuse)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QMessageBox, "warning", lambda _w, _t, text, *_a: shown.append(text))
    window.show()
    window._apply("Speichern {0}")
    assert not window.close() and window.isVisible() and window.doc.m_dirty
    assert shown and "the disk is full" in shown[0]


def test_a_filter_by_status_finds_the_untranslated_and_the_next_one_is_selected(qt, window):
    window.m_status_box.setCurrentIndex(window.m_status_box.findData("untranslated"))
    settle(qt, window)
    assert window.m_model.m_ids == ["-11", "-12"]
    window.m_table.selectRow(0)
    window.next_untranslated()
    settle(qt, window)
    assert window.current_key() == "-12"


def test_a_translation_goes_out_as_csv_and_comes_back(qt, window):
    shown = exchange.rows_of(window.doc, window.m_model.m_ids)
    assert [r.id for r in shown] == window.m_model.m_ids
    sent = exchange.write("csv", [exchange.SRow("-13", "Wood", "Holzscheit")], "anno117", "english", "german")
    result = exchange.apply(window.doc, exchange.read("csv", sent))
    assert result.applied == 1 and window.doc.target_text("-13") == "Holzscheit"
    assert window.doc.undo() and window.doc.target_text("-13") == "Holz"


def test_the_window_speaks_german_with_every_marked_text_translated(qt, window):
    i18n.use("de")
    try:
        from annotoolbox.textedit.textwindow import CTextWindow as Fresh

        german = Fresh(window.settings, None)
        assert untranslated(shown_texts(german)) == [] and not i18n.missing()
        german.close()
    finally:
        i18n.use("en")


def test_an_import_and_an_export_run_off_the_windows_thread_and_come_back(qt, window, tmp_path):
    sent = exchange.write("csv", [exchange.SRow("-13", "Wood", "Holzscheit"), exchange.SRow("-555", "Ghost", "Geist")],
                          "anno117", "english", "german")  # fmt: skip
    path = tmp_path / "in.csv"
    path.write_bytes(sent)
    window.ask_add = lambda unknown: False
    window.import_path(str(path), is_waiting=True)
    settle(qt, window)
    assert window.doc.target_text("-13") == "Holzscheit" and "1 applied" in window.statusBar().currentMessage()
    assert "-555" not in window.m_model.m_ids
    out = tmp_path / "out.po"
    window.export_path(str(out), is_waiting=True)
    assert exchange.read("po", out.read_bytes())[0].id == window.m_model.m_ids[0]
    window.import_path(str(tmp_path / "missing.csv"), is_waiting=True)
    assert "did not read" in window.statusBar().currentMessage()


def test_an_import_read_for_a_game_that_was_left_meanwhile_is_not_applied_to_the_next(
    qt, window, tmp_path, monkeypatch
):
    path = tmp_path / "in.csv"
    path.write_bytes(
        exchange.write("csv", [exchange.SRow("-13", "Wood", "Holzscheit")], "anno117", "english", "german")
    )
    applied = []
    monkeypatch.setattr(exchange, "apply", lambda doc, *_a, **_k: applied.append(doc))
    first = window.doc
    window.import_path(str(path))  # read on a thread; its answer is handled when the loop next runs
    window.m_background.join()
    window.doc = object()  # another game's document, opened meanwhile
    qt.processEvents()
    assert applied == [] and window.isEnabled()
    window.doc = first


def test_a_new_text_cancelled_at_its_id_does_not_ask_for_the_words(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    asked: list[str] = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("900", False))
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *_a, **_k: (asked.append("words") or "New", True))
    window.new_text()
    assert asked == [] and not window.doc.has("900")
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("-13", True))  # an id the game has
    window.new_text()
    assert asked == [] and "-13" in window.statusBar().currentMessage()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: (" 900 ", True))
    window.new_text()
    assert asked == ["words"] and window.doc.text("english", "900") == "New"


def test_the_rows_of_a_game_closed_meanwhile_are_not_shown_and_a_filter_that_fails_says_so(window, monkeypatch):
    runs: list[tuple] = []
    monkeypatch.setattr(window.m_background, "run", lambda work, done, is_waiting=False: runs.append((work, done)))
    before, doc = list(window.m_model.m_ids), window.doc
    window.m_search.setText("holz")
    window._refilter()
    work, done = runs[-1]
    window.doc = None  # another game is being opened
    done(work())
    assert window.m_model.m_ids == before
    window.doc = doc
    window._refilter()
    runs[-1][1](ValueError("the file went away"))
    assert "the file went away" in window.statusBar().currentMessage() and window.m_model.m_ids == before


def test_a_pair_of_languages_that_does_not_read_leaves_the_boxes_on_the_pair_shown(qt, window, monkeypatch):
    def fail(_source, _target):
        raise OSError("unreadable")

    monkeypatch.setattr(window.doc, "prepare", fail)
    window.m_target_box.setCurrentIndex(window.m_target_box.findData("english"))
    window._switch()
    settle(qt, window)
    assert "unreadable" in window.statusBar().currentMessage() and window.isEnabled()
    assert window.m_target_box.currentData() == "german" == window.doc.m_target


@pytest.fixture
def voice_window(qt, tmp_path):
    root = tmp_path / "game" / "data" / "base" / "config"
    for folder, files in (("gui", {"texts_english.xml": text_export(("-20", "Hello Marcus"), ("-21", "Hello Marcus"), ("-23", "Other")),
                                   "texts_german.xml": text_export(("-20", "Hello Marcus"), ("-21", "Hello Marcus"), ("-23", "Anderes")),
                                   "texts_metadata.xml": META_TWINS}),
                          ("export", {"audio_generated.xml": AUDIO, "assets.xml": PORTRAIT})):  # fmt: skip
        (root / folder).mkdir(parents=True)
        for name, text in files.items():
            (root / folder / name).write_text(text, encoding="utf-8", newline="")
    yield from _opened(qt, tmp_path)


def test_the_panel_shows_the_speaker_the_twins_and_fills_them_and_a_voice_that_cannot_play_says_so(qt, voice_window):
    win = voice_window
    assert win.m_model.m_ids == ["-20", "-21", "-23"]  # the twin stands behind its main line
    win.m_table.selectRow(0)
    assert win.m_detail.m_voice_label.text() == "Spoken by Ben Baalion (Neutral)"
    assert (
        win.m_detail.m_twins.count() == 1 and win.m_detail.m_play_buttons["target"].isEnabled()
    )  # -22 has no text here
    win._apply("Hallo Marcus")
    win._fill_twins()
    assert win.doc.target_text("-21") == "Hallo Marcus" and "1 twin" in win.statusBar().currentMessage()
    win.m_voiced_only.setChecked(True)
    settle(qt, win)
    assert win.m_model.m_ids == ["-20", "-23"]
    win._play("target", is_waiting=True)
    settle(qt, win)
    assert win.statusBar().currentMessage().startswith("cannot play")
