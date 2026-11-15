"""The windows of the older games' sounds, offscreen: the sound editor's page (soundedit/oldpanel in the sound window)
and the viewer's page (viewer/sounddb), over a small database, a timeline and a speech envelope."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from annotoolbox.formats import sndtimeline, spenvelope
from annotoolbox.soundedit import olddoc, projectpart
from test_sounddb import DB, tree_of, wav

FILES = {
    "data/sound/sounddatabase.xml": DB,
    "data/sound/a.wav": wav(0.1),
    "data/sound/music/b.mp3": b"ID3x",
    "data/graphics/mill.snd": sndtimeline.render(
        sndtimeline.Timeline(
            "mill.snd", {6001: 1000}, {6001: [sndtimeline.Placement(1, 0), sndtimeline.Placement(12, -1)]}
        )
    ).encode(),
    "data/loca/eng/speech/x/1.sp": spenvelope.Envelope(bytes([0, 128, 255]), 50).to_bytes(),
}


@pytest.fixture(scope="module")
def qt(monkeypatch_module=None):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_the_viewer_shows_a_database_a_timeline_and_an_envelope(qt, tmp_path):
    from annotoolbox.viewer.sounddb import SoundDbView

    view, tree = SoundDbView(cache=tmp_path), tree_of(FILES)
    view.show_database("data/sound/sounddatabase.xml", DB, tree)
    assert view.tables["Sounds"].rowCount() == 2 and view.tables["Lists"].rowCount() == 3
    assert view.tables["Ambients"].item(0, 1).text() == "amb"
    view.show_timeline("data/graphics/mill.snd", FILES["data/graphics/mill.snd"], tree, wait=True)
    table = view.tables["Timeline"]
    assert table.rowCount() == 2 and table.item(0, 4).text() == "data/sound/a.wav"
    assert not view.tabs.isTabVisible(view.tabs.indexOf(view.tables["Sounds"]))
    view.show_speech("data/loca/eng/speech/x/1.sp", FILES["data/loca/eng/speech/x/1.sp"], tree)
    assert view.bars.levels == bytes([0, 128, 255]) and "3 levels" in view.summary.text()
    assert view.tabs.isHidden()  # an envelope has its bars, not the tables
    view.show_database("data/sound/sounddatabase.xml", DB, tree)
    assert view.tabs.isTabVisible(view.tabs.indexOf(view.tables["Sounds"]))
    view.show_database("bad.xml", b"<nothing/>", tree)
    assert "not a sound database" in view.summary.text()


def test_a_timeline_reads_its_database_off_the_window_thread_and_a_later_page_wins(qt, tmp_path):
    import threading

    from annotoolbox.viewer.sounddb import SoundDbView

    view, tree = SoundDbView(cache=tmp_path), tree_of(FILES)
    release, reading = threading.Event(), []
    real = view._database_of

    def slow(files):
        reading.append(threading.current_thread())
        assert release.wait(30)
        return real(files)

    view._database_of = slow
    view.show_timeline("data/graphics/mill.snd", FILES["data/graphics/mill.snd"], tree)
    assert view.tables["Timeline"].rowCount() == 0 and "2 sounds placed" in view.summary.text()  # shown at once
    release.set()
    view.background.join()
    qt.processEvents()
    assert reading[0] is not threading.main_thread()
    table = view.tables["Timeline"]
    assert table.rowCount() == 2 and table.item(0, 4).text() == "data/sound/a.wav"
    release.clear()
    view.show_timeline("data/graphics/mill.snd", FILES["data/graphics/mill.snd"], tree)
    view.show_database("data/sound/sounddatabase.xml", DB, tree)  # the user went on before the database came
    release.set()
    view.background.join()
    qt.processEvents()
    assert view.tables["Timeline"].rowCount() == 0 and view.tables["Sounds"].rowCount() == 2


def test_the_editor_page_edits_replaces_a_clip_and_keeps_the_files_in_the_project(qt, tmp_path):
    from annotoolbox.soundedit.soundwindow import SoundWindow
    from annotoolbox.suite import project as suite_project
    from annotoolbox.suite.settings import Settings

    settings = Settings(workspace=str(tmp_path))
    window = SoundWindow(settings)
    window.project = lambda: suite_project.Project(tmp_path / "proj", "m", "m", game="anno2070")
    window.key, window.tree = "anno2070", tree_of(FILES)
    window.pages.setCurrentIndex(1)
    window.old.open_game(window.tree)
    panel = window.old
    assert panel.doc is not None and panel.table.rowCount() == 2
    panel.table.selectRow(0)
    panel.boxes["MasterVolume"].setValue(33)
    panel.apply()
    assert panel.doc.db.sounds[1].number("MasterVolume") == 33
    panel.filter.setText("music")
    assert panel.table.rowCount() == 1
    panel.filter.setText("")
    panel.table.selectRow(0)
    panel.doc.replace_clip(1, wav(0.2), ".wav")
    window.save()
    saved = projectpart.EDITS.saved_files(window.project(), "anno2070")
    assert set(saved) == {"data/sound/sounddatabase.xml", "data/sound/a.wav"}
    window.undo()
    assert panel.doc.clips == {} and panel.doc.db.sounds[1].number("MasterVolume") == 33
    window.undo()
    assert not panel.doc.changed
    window.save()  # everything was undone: the files an earlier save made go again
    assert projectpart.EDITS.saved_files(window.project(), "anno2070") == {}


def test_the_clip_player_says_why_a_clip_cannot_play(qt, tmp_path):
    from annotoolbox.suite.clipplayer import ClipPlayer

    player = ClipPlayer()
    assert "cannot play" in player.play("x.wem", b"not a riff file", tmp_path)
    assert "no cache folder" in player.play("x.mp3", b"ID3", None)


def _panel(qt, tmp_path):
    from annotoolbox.soundedit.oldpanel import OldSoundPanel

    panel = OldSoundPanel(lambda _text: None, tmp_path)
    panel.open_game(tree_of(FILES))
    return panel


def test_a_value_the_box_cannot_show_is_not_rewritten_by_apply(qt, tmp_path):
    files = {**FILES, "data/sound/sounddatabase.xml": FILES["data/sound/sounddatabase.xml"].replace(
        b"<MasterVolume>80</MasterVolume>", b"<MasterVolume>150</MasterVolume>")}  # fmt: skip
    from annotoolbox.soundedit.oldpanel import OldSoundPanel

    panel = OldSoundPanel(lambda _text: None, tmp_path)
    panel.open_game(tree_of(files))
    panel.table.selectRow(0)
    assert panel.boxes["MasterVolume"].value() == 100  # the box's top
    panel.boxes["Prio"].setValue(5)
    panel.apply()
    assert panel.doc.db.sounds[1].number("MasterVolume") == 150 and panel.doc.db.sounds[1].number("Prio") == 5


def test_selecting_a_sound_does_not_read_its_clip(qt, tmp_path):
    panel = _panel(qt, tmp_path)
    reads = []
    real = panel.doc.tree.read
    panel.doc.tree.read = lambda path: reads.append(path) or real(path)
    panel.table.selectRow(1)
    assert reads == [] and "no such file" not in panel.facts.text()


def test_a_clip_is_prepared_off_the_window_thread_and_put_when_done(qt, tmp_path):
    panel = _panel(qt, tmp_path)
    panel.table.selectRow(0)
    panel._pick = lambda: (wav(0.2), ".wav")
    panel.replace_clip()
    assert panel.busy
    panel.replace_clip()  # a second one while the first runs is said, not started
    panel.background.join()
    qt.processEvents()
    assert not panel.busy and panel.doc.files()["data/sound/a.wav"] == wav(0.2)


def test_a_clip_for_a_sound_that_was_undone_meanwhile_is_said_not_raised(qt, tmp_path):
    panel = _panel(qt, tmp_path)
    doc = panel.doc
    new = doc.add_sound("data/sound/new.wav", wav(0.1), like=1)
    target, data = doc.prepare_clip(new, wav(0.2), ".wav")
    assert doc.undo() and new not in doc.db.sounds  # the add is undone while the clip is converted
    with pytest.raises(olddoc.OldEditError, match="no sound"):
        doc.put_clip(new, target, data)
    assert "data/sound/new.wav" not in doc.files()


def test_a_spoken_line_is_replaced_with_its_envelope(qt, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    panel = _panel(qt, tmp_path)
    files = {**FILES, "data/loca/eng/speech/x/1.mp3": b"ID3old"}
    panel.tree = panel.doc.tree = tree_of(files)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("data/loca/eng/speech/x/1.mp3", True))
    monkeypatch.setattr("annotoolbox.soundedit.olddoc.export.to_mp3", lambda clip, suffix: b"MP3new")
    panel._pick = lambda: (wav(0.2), ".wav")
    panel.replace_speech()
    panel.background.join()
    qt.processEvents()
    saved = panel.doc.files()
    assert saved["data/loca/eng/speech/x/1.mp3"] == b"MP3new" and saved["data/loca/eng/speech/x/1.sp"]
    assert spenvelope.read(saved["data/loca/eng/speech/x/1.sp"]).levels


def test_a_sound_joins_a_list_and_a_timeline_is_edited_in_the_panel(qt, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    panel = _panel(qt, tmp_path)
    panel.table.selectRow(1)  # sound 12
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("98000", True))
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (20, True))
    panel.add_to_list()
    assert panel.doc.db.lists[98000].events[-1] == [(1, 50), (12, 20)]
    line = panel.timelines
    assert line.files.count() == 1
    line.files.setCurrentRow(0)
    assert line.table.rowCount() == 2
    line.table.item(0, 2).setText("250")
    line.table.item(0, 1).setText("1200")
    line.apply()
    shown = panel.doc.timeline("data/graphics/mill.snd")
    assert shown.durations == {6001: 1200} and shown.placements[6001][0].at == 250
    line.table.selectRow(1)
    line.remove()
    assert len(panel.doc.timeline("data/graphics/mill.snd").placements[6001]) == 1
    assert panel.doc.undo() and line.refresh() is None and line.table.rowCount() == 2
