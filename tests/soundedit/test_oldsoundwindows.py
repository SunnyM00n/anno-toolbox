"""The windows of the older games' sounds, offscreen: the sound editor's page (soundedit/oldpanel in the sound window)
and the viewer's page (viewer/sounddb), over a small database, a timeline and a speech envelope."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication
from tests.sound.soundfakes import DB, tree_of
from tests.sound.soundfakes import wav_bytes as wav

from annotoolbox.formats import sndtimeline, spenvelope
from annotoolbox.soundedit import olddoc, projectpart

FILES = {
    "data/sound/sounddatabase.xml": DB,
    "data/sound/a.wav": wav(0.1),
    "data/sound/music/b.mp3": b"ID3x",
    "data/graphics/mill.snd": sndtimeline.render(
        sndtimeline.STimeline(
            "mill.snd", {6001: 1000}, {6001: [sndtimeline.SPlacement(1, 0), sndtimeline.SPlacement(12, -1)]}
        )
    ).encode(),
    "data/loca/eng/speech/x/1.sp": spenvelope.SEnvelope(bytes([0, 128, 255]), 50).to_bytes(),
}


@pytest.fixture(scope="module")
def qt(monkeypatch_module=None):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_the_viewer_shows_a_database_a_timeline_and_an_envelope(qt, tmp_path):
    from annotoolbox.viewer.sounddb import CSoundDbView

    view, tree = CSoundDbView(cache=tmp_path), tree_of(FILES)
    view.show_database("data/sound/sounddatabase.xml", DB, tree)
    assert view.m_tables["Sounds"].rowCount() == 2 and view.m_tables["Lists"].rowCount() == 3
    assert view.m_tables["Ambients"].item(0, 1).text() == "amb"
    view.show_timeline("data/graphics/mill.snd", FILES["data/graphics/mill.snd"], tree, is_waiting=True)
    table = view.m_tables["Timeline"]
    assert table.rowCount() == 2 and table.item(0, 4).text() == "data/sound/a.wav"
    assert not view.m_tabs.isTabVisible(view.m_tabs.indexOf(view.m_tables["Sounds"]))
    view.show_speech("data/loca/eng/speech/x/1.sp", FILES["data/loca/eng/speech/x/1.sp"], tree)
    assert view.m_bars.m_levels == bytes([0, 128, 255]) and "3 levels" in view.m_summary.text()
    assert view.m_tabs.isHidden()  # an envelope has its bars, not the tables
    view.show_database("data/sound/sounddatabase.xml", DB, tree)
    assert view.m_tabs.isTabVisible(view.m_tabs.indexOf(view.m_tables["Sounds"]))
    view.show_database("bad.xml", b"<nothing/>", tree)
    assert "not a sound database" in view.m_summary.text()


def test_a_timeline_reads_its_database_off_the_window_thread_and_a_later_page_wins(qt, tmp_path):
    import threading

    from annotoolbox.viewer.sounddb import CSoundDbView

    view, tree = CSoundDbView(cache=tmp_path), tree_of(FILES)
    release, reading = threading.Event(), []
    real = view._database_of

    def slow(files):
        reading.append(threading.current_thread())
        assert release.wait(30)
        return real(files)

    view._database_of = slow
    view.show_timeline("data/graphics/mill.snd", FILES["data/graphics/mill.snd"], tree)
    assert view.m_tables["Timeline"].rowCount() == 0 and "2 sounds placed" in view.m_summary.text()  # shown at once
    release.set()
    view.m_background.join()
    qt.processEvents()
    assert reading[0] is not threading.main_thread()
    table = view.m_tables["Timeline"]
    assert table.rowCount() == 2 and table.item(0, 4).text() == "data/sound/a.wav"
    release.clear()
    view.show_timeline("data/graphics/mill.snd", FILES["data/graphics/mill.snd"], tree)
    view.show_database("data/sound/sounddatabase.xml", DB, tree)  # the user went on before the database came
    release.set()
    view.m_background.join()
    qt.processEvents()
    assert view.m_tables["Timeline"].rowCount() == 0 and view.m_tables["Sounds"].rowCount() == 2


def test_the_editor_page_edits_replaces_a_clip_and_keeps_the_files_in_the_project(qt, tmp_path):
    from annotoolbox.soundedit.soundwindow import CSoundWindow
    from annotoolbox.suite import project as suite_project
    from annotoolbox.suite.settings import CSettings

    settings = CSettings(workspace=str(tmp_path))
    window = CSoundWindow(settings)
    window.project = lambda: suite_project.CProject(tmp_path / "proj", "m", "m", game="anno2070")
    window.m_key, window.m_tree = "anno2070", tree_of(FILES)
    window.m_pages.setCurrentIndex(1)
    window.m_old.open_game(window.m_tree)
    panel = window.m_old
    assert panel.m_doc is not None and panel.m_table.rowCount() == 2
    panel.m_table.selectRow(0)
    panel.m_boxes["MasterVolume"].setValue(33)
    panel.apply()
    assert panel.m_doc.db.sounds[1].number("MasterVolume") == 33
    panel.m_filter.setText("music")
    assert panel.m_table.rowCount() == 1
    panel.m_filter.setText("")
    panel.m_table.selectRow(0)
    panel.m_doc.replace_clip(1, wav(0.2), ".wav")
    window.save()
    saved = projectpart.EDITS.saved_files(window.project(), "anno2070")
    assert set(saved) == {"data/sound/sounddatabase.xml", "data/sound/a.wav"}
    window.undo()
    assert panel.m_doc.clips == {} and panel.m_doc.db.sounds[1].number("MasterVolume") == 33
    window.undo()
    assert not panel.m_doc.changed
    window.save()  # everything was undone: the files an earlier save made go again
    assert projectpart.EDITS.saved_files(window.project(), "anno2070") == {}


def test_the_clip_player_says_why_a_clip_cannot_play(qt, tmp_path):
    from annotoolbox.suite.clipplayer import CClipPlayer

    player = CClipPlayer()
    assert "cannot play" in player.play("x.wem", b"not a riff file", tmp_path)
    assert "no cache folder" in player.play("x.mp3", b"ID3", None)
    (tmp_path / "taken").write_bytes(b"")  # a file where the cache folder would be: it cannot be written into
    assert "cannot play" in player.play("x.mp3", b"ID3", tmp_path / "taken")  # said, as the others: it was raised


def _panel(qt, tmp_path):
    from annotoolbox.soundedit.oldpanel import COldSoundPanel

    panel = COldSoundPanel(lambda _text: None, tmp_path)
    panel.open_game(tree_of(FILES))
    return panel


def test_a_value_the_box_cannot_show_is_not_rewritten_by_apply(qt, tmp_path):
    files = {**FILES, "data/sound/sounddatabase.xml": FILES["data/sound/sounddatabase.xml"].replace(
        b"<MasterVolume>80</MasterVolume>", b"<MasterVolume>150</MasterVolume>")}  # fmt: skip
    from annotoolbox.soundedit.oldpanel import COldSoundPanel

    panel = COldSoundPanel(lambda _text: None, tmp_path)
    panel.open_game(tree_of(files))
    panel.m_table.selectRow(0)
    assert panel.m_boxes["MasterVolume"].value() == 100  # the box's top
    panel.m_boxes["Prio"].setValue(5)
    panel.apply()
    assert panel.m_doc.db.sounds[1].number("MasterVolume") == 150 and panel.m_doc.db.sounds[1].number("Prio") == 5


def test_selecting_a_sound_does_not_read_its_clip(qt, tmp_path):
    panel = _panel(qt, tmp_path)
    reads = []
    real = panel.m_doc.m_tree.read
    panel.m_doc.m_tree.read = lambda path: reads.append(path) or real(path)
    panel.m_table.selectRow(1)
    assert reads == [] and "no such file" not in panel.m_facts.text()


def test_a_clip_is_prepared_off_the_window_thread_and_put_when_done(qt, tmp_path):
    panel = _panel(qt, tmp_path)
    panel.m_table.selectRow(0)
    panel._pick = lambda: (wav(0.2), ".wav")
    panel.replace_clip()
    assert panel.m_busy
    panel.replace_clip()  # a second one while the first runs is said, not started
    panel.m_background.join()
    qt.processEvents()
    assert not panel.m_busy and panel.m_doc.files()["data/sound/a.wav"] == wav(0.2)


def test_a_clip_for_a_sound_that_was_undone_meanwhile_is_said_not_raised(qt, tmp_path):
    panel = _panel(qt, tmp_path)
    doc = panel.m_doc
    new = doc.add_sound("data/sound/new.wav", wav(0.1), like=1)
    target, data = doc.prepare_clip(new, wav(0.2), ".wav")
    assert doc.undo() and new not in doc.db.sounds  # the add is undone while the clip is converted
    with pytest.raises(olddoc.COldEditError, match="no sound"):
        doc.put_clip(new, target, data)
    assert "data/sound/new.wav" not in doc.files()


def test_a_spoken_line_is_replaced_with_its_envelope(qt, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    panel = _panel(qt, tmp_path)
    files = {**FILES, "data/loca/eng/speech/x/1.mp3": b"ID3old"}
    panel.m_tree = panel.m_doc.m_tree = tree_of(files)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("data/loca/eng/speech/x/1.mp3", True))
    monkeypatch.setattr("annotoolbox.soundedit.olddoc.export.to_mp3", lambda clip, suffix: b"MP3new")
    panel._pick = lambda: (wav(0.2), ".wav")
    panel.replace_speech()
    panel.m_background.join()
    qt.processEvents()
    saved = panel.m_doc.files()
    assert saved["data/loca/eng/speech/x/1.mp3"] == b"MP3new" and saved["data/loca/eng/speech/x/1.sp"]
    assert spenvelope.read(saved["data/loca/eng/speech/x/1.sp"]).levels


def test_a_sound_joins_a_list_and_a_timeline_is_edited_in_the_panel(qt, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    panel = _panel(qt, tmp_path)
    panel.m_table.selectRow(1)  # sound 12
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("98000", True))
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (20, True))
    panel.add_to_list()
    assert panel.m_doc.db.lists[98000].events[-1] == [(1, 50), (12, 20)]
    line = panel.m_timelines
    assert line.m_files.count() == 1
    line.m_files.setCurrentRow(0)
    assert line.m_table.rowCount() == 2
    line.m_table.item(0, 2).setText("250")
    line.m_table.item(0, 1).setText("1200")
    line.apply()
    shown = panel.m_doc.timeline("data/graphics/mill.snd")
    assert shown.durations == {6001: 1200} and shown.placements[6001][0].at_ms == 250
    line.m_table.selectRow(1)
    line.remove()
    assert len(panel.m_doc.timeline("data/graphics/mill.snd").placements[6001]) == 1
    assert panel.m_doc.undo() and line.refresh() is None and line.m_table.rowCount() == 2


def test_a_game_without_a_database_shows_nothing_of_the_game_before(qt, tmp_path):
    panel = _panel(qt, tmp_path)
    assert panel.m_table.rowCount() == 2 and panel.m_timelines.m_files.count() == 1
    panel.open_game(tree_of({"data/graphics/other.snd": FILES["data/graphics/mill.snd"]}))
    assert panel.m_doc is None and panel.m_table.rowCount() == 0 and panel.m_timelines.m_files.count() == 0
    panel.open_game(tree_of(FILES))
    panel.open_database(panel.m_tree, "data/sound/missing/sounddatabase.xml")  # one that does not read
    assert panel.m_doc is None and panel.m_table.rowCount() == 0 and panel.m_timelines.m_doc is None


def test_a_weight_that_is_no_number_changes_no_list(qt, tmp_path):
    said: list[str] = []
    panel = _panel(qt, tmp_path)
    panel.m_say = said.append
    panel.m_table.selectRow(0)  # sound 1: drawn by the lists 40 (event 4) and 98000
    assert panel.m_members.rowCount() == 2
    panel.m_members.item(0, 2).setText("45")
    panel.m_members.item(1, 2).setText("many")
    panel.apply_weights()
    assert not panel.m_doc.changed and said[-1] == "a weight is a whole number"
    panel.m_members.item(1, 2).setText("55")
    panel.apply_weights()
    assert panel.m_doc.db.lists[40].events[4][0] == (1, 45) and panel.m_doc.db.lists[98000].events[-1] == [(1, 55)]


def test_no_row_clears_the_facts_of_the_sound_before_and_a_clip_that_does_not_read_is_said(qt, tmp_path):
    said: list[str] = []
    panel = _panel(qt, tmp_path)
    panel.m_say = said.append
    panel.m_table.selectRow(0)
    assert panel.m_facts.text() and panel.m_members.rowCount() == 2
    panel.m_filter.setText("zzz-nothing")
    assert panel.m_table.rowCount() == 0 and not panel.m_facts.text() and panel.m_members.rowCount() == 0
    panel.m_filter.setText("")
    panel.m_table.selectRow(0)
    panel.m_doc.m_tree.read = lambda path: (_ for _ in ()).throw(OSError("archive cut"))
    panel.play()
    assert said[-1] == "archive cut"
