"""The older games' sound files: the database, a model's timeline, a speech envelope (formats/sounddb, sndtimeline,
spenvelope), the editor's document over them (soundedit/olddoc) and, gated on the installs, every shipped file."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.sound.soundfakes import DB, tree_of
from tests.sound.soundfakes import wav_bytes as wav

from annotoolbox.formats import sndtimeline, sounddb, spenvelope
from annotoolbox.soundedit import olddoc
from annotoolbox.sources.gametree import open_game


def test_the_database_reads_sounds_lists_ambients_and_groups():
    db = sounddb.read(DB)
    assert db.sounds[12].filename == "data/sound/music/b.mp3" and db.sounds[1].number("MasterVolume") == 80
    assert db.lists[40].events == {4: [(1, 40), (12, 10)]}  # events of 1404 and 1701
    assert db.lists[98000].events == {sounddb.NO_EVENT: [(1, 50)]}  # 2070's list has no events
    assert db.ambients[15].channels[0].members == [(1, 20)] and len(db.groups) == 1
    with pytest.raises(ValueError):
        sounddb.read(b"<Other/>")


def test_an_edit_changes_its_bytes_and_nothing_else():
    new = sounddb.set_fields(DB, 12, MasterVolume=33, SoundType=2)
    assert (
        new.replace(b"<MasterVolume>33</MasterVolume>", b"<MasterVolume>50</MasterVolume>").replace(
            b"<SoundType>2</SoundType>\r\n<Filename>data/sound/music",
            b"<SoundType>3</SoundType>\r\n<Filename>data/sound/music",
        )
        == DB
    )
    heavier = sounddb.set_weight(DB, 40, 12, 99, event=4)
    assert sounddb.read(heavier).lists[40].events[4] == [(1, 40), (12, 99)]
    assert sounddb.read(sounddb.set_weight(DB, 98000, 1, 7)).lists[98000].events[sounddb.NO_EVENT] == [(1, 7)]
    for bad in (lambda: sounddb.set_fields(DB, 99, Prio=1), lambda: sounddb.set_fields(DB, 1, ID=5),
                lambda: sounddb.set_fields(DB, 1, Filename="a<b"), lambda: sounddb.set_weight(DB, 40, 1, 1, event=9)):  # fmt: skip
        with pytest.raises(sounddb.CSoundDbError):
            bad()


def test_a_new_sound_takes_the_next_id_and_the_values_of_another():
    data, new = sounddb.add_sound(DB, "data/sound/c.wav", like=1, Prio=4)
    added = sounddb.read(data).sounds[new]
    assert new == 13 and added.filename == "data/sound/c.wav" and added.number("Prio") == 4
    assert added.number("MasterVolume") == 80 and data.count(b"\r\n") > DB.count(b"\r\n")


def test_a_timeline_and_an_envelope_write_back_as_they_were_read():
    text = (
        "<SequenceSounds><m_Filename>x.snd</m_Filename>\r\n<m_SequenceDuration><k>6001</k>\r\n<v>1000</v>\r\n"
        "<k>6003</k>\r\n<v>500</v>\r\n</m_SequenceDuration>\r\n<m_SequenceSoundIDMap><k>6001</k>\r\n"
        "<v><i><SoundID>2</SoundID>\r\n<Position>0</Position>\r\n</i>\r\n</v>\r\n<k>6003</k>\r\n<v></v>\r\n"
        "</m_SequenceSoundIDMap>\r\n</SequenceSounds>\r\n"
    )
    line = sndtimeline.parse(text)
    assert line.durations == {6001: 1000, 6003: 500} and line.placements[6001][0].sound == 2
    assert sndtimeline.render(line) == text
    raw = bytes([4, 0, 0, 0, 9, 99, 255, 0]) + bytes([50, 0, 0, 0, 0, 0, 0, 0])
    env = spenvelope.read(raw)
    assert env.levels == bytes([9, 99, 255, 0]) and env.seconds == 0.2 and env.to_bytes() == raw
    with pytest.raises(ValueError):
        spenvelope.read(raw[:-1])


def test_an_envelope_follows_a_clips_loudness_to_its_own_peak():
    quiet = [0] * 400 + [100, -100] * 200  # at 8 kHz a step is 400 samples
    env = spenvelope.SEnvelope.from_samples(quiet, 8000)
    assert env.levels[0] == 0 and env.levels[1] == 255 and len(env.levels) == 2


@pytest.fixture
def doc():
    files = {"data/sound/sounddatabase.xml": DB, "data/sound/a.wav": wav(0.1), "data/sound/music/b.mp3": b"ID3x"}
    return olddoc.COldSoundDoc(tree_of(files), "data/sound/sounddatabase.xml")


def test_edits_are_undoable_steps_and_the_files_are_what_changed(doc):
    assert not doc.changed and doc.files() == {}
    doc.set_fields(1, MasterVolume=10)
    doc.set_weight(40, 12, 5, 4)
    assert doc.db.sounds[1].number("MasterVolume") == 10 and list(doc.files()) == ["data/sound/sounddatabase.xml"]
    assert doc.undo() and doc.db.lists[40].events[4][1] == (12, 10) and doc.undo() and not doc.changed
    assert doc.redo() and doc.db.sounds[1].number("MasterVolume") == 10
    with pytest.raises(olddoc.COldEditError, match="no sound 99"):
        doc.set_fields(99, Prio=1)


def test_a_replaced_clip_is_written_where_the_sound_plays(doc):
    clip = wav(0.25)
    assert doc.replace_clip(1, clip, ".wav") == "data/sound/a.wav"
    assert doc.files()["data/sound/a.wav"] == clip and doc.clip(1) == ("data/sound/a.wav", clip)
    again = doc.add_sound("data/sound/new.wav", clip, like=1)
    assert doc.db.sounds[again].filename == "data/sound/new.wav" and doc.files()["data/sound/new.wav"] == clip
    assert doc.target_of(12, ".mp3") == "data/sound/music/b.mp3"  # a clip in the sound's own form stays there
    assert doc.target_of(12, ".wav") == "data/sound/music/b.wav"  # another form becomes a WAV beside it


def test_a_wav_for_an_mp3_sound_is_written_as_wav_and_the_database_follows(doc):
    clip = wav(0.1)
    assert doc.replace_clip(12, clip, ".wav") == "data/sound/music/b.wav"
    assert doc.db.sounds[12].filename == "data/sound/music/b.wav" and doc.files()["data/sound/music/b.wav"] == clip
    with pytest.raises(olddoc.COldEditError, match="not a speech line"):
        doc.replace_speech("data/sound/a.wav", clip, ".wav")


def test_a_clip_that_is_no_audio_is_refused_and_the_document_stays(doc):
    with pytest.raises(olddoc.COldEditError):
        doc.replace_clip(1, b"not audio", ".wav")
    assert not doc.changed


# the installs

INSTALLS = {
    "anno2070": ("ANNO2070_DIR", ("data/sound/sounddatabase.xml",)),
    "anno1404": ("ANNO1404_DIR", ("data/sound/sounddatabase.xml", "addondata/sound/sounddatabase.xml")),
    "anno1701": ("ANNO1701_DIR", ("data/sound/sfx/sounddatabase.xml",)),
}


@pytest.mark.parametrize("key", INSTALLS)
def test_every_shipped_database_timeline_and_envelope_reads_and_writes_back(key, tmp_path):
    variable, databases = INSTALLS[key]
    folder = os.environ.get(variable)
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"{variable} is not set")
    game = open_game(key, Path(folder), tmp_path)
    for path in databases:
        db = sounddb.read(game.read(path))
        assert len(db.sounds) > 500 and db.lists and db.ambients
        first = next(iter(db.sounds))
        assert sounddb.read(sounddb.set_fields(game.read(path), first, Prio=7)).sounds[first].number("Prio") == 7
    for path in (p for p in game.m_paths if p.endswith(".sp")):
        raw = game.read(path)
        envelope = spenvelope.read(raw)
        assert envelope.to_bytes() == raw and envelope.interval_ms == 50
    for path in (p for p in game.m_paths if p.endswith(".snd")):
        text = game.read(path).decode("utf-8")
        assert sndtimeline.render(sndtimeline.parse(text)) == text


def test_a_new_sound_escapes_its_text_and_refuses_a_field_the_entry_lacks():
    data, new = sounddb.add_sound(DB, "data/sound/a&b.wav", like=1)
    assert sounddb.read(data).sounds[new].filename == "data/sound/a&b.wav"
    with pytest.raises(sounddb.CSoundDbError, match="Nonsense"):
        sounddb.add_sound(DB, "data/sound/c.wav", Nonsense=1)


def test_a_timeline_writes_its_filename_escaped():
    text = sndtimeline.render(sndtimeline.STimeline("a&b.snd", {1: 5}, {}))
    assert sndtimeline.parse(text).filename == "a&b.snd"


def test_a_path_outside_the_game_is_refused_before_any_file_is_written(doc):
    for bad in ("../x.wav", "/x.wav", "c:/x.wav", "a/../../x.wav"):
        with pytest.raises(olddoc.COldEditError, match="not a path inside"):
            doc.add_sound(bad, wav(0.1))
        with pytest.raises(olddoc.COldEditError, match="not a path inside"):
            doc.replace_speech(bad.replace(".wav", ".mp3"), wav(0.1), ".wav")
    doc.set_file(1, "../out.wav")  # a database text edit only; a clip is never written there
    with pytest.raises(olddoc.COldEditError, match="not a path inside"):
        doc.replace_clip(1, wav(0.1), ".wav")
    assert not doc.clips


def test_a_clip_is_prepared_without_touching_the_document_and_put_afterwards(doc):
    clip = wav(0.2)
    target, data = doc.prepare_clip(1, clip, ".wav")
    assert (target, data) == ("data/sound/a.wav", clip) and not doc.changed
    doc.put_clip(1, target, data)
    assert doc.changed and doc.has_clip(1) and not doc.has_clip(99)
    assert not doc.has_clip(12) or doc.m_tree.exists("data/sound/music/b.mp3")


SND = sndtimeline.render(
    sndtimeline.STimeline("m.snd", {6001: 1000}, {6001: [sndtimeline.SPlacement(1, 0), sndtimeline.SPlacement(12, -1)]})
)


def test_a_timeline_edit_changes_one_thing_and_writes_the_shipped_layout():
    tl = sndtimeline.parse(SND)
    longer = sndtimeline.set_length(tl, 6001, 1500)
    placed = sndtimeline.place(longer, 6001, 1, 700)
    moved = sndtimeline.move(placed, 6001, 0, 50, sound=12)
    gone = sndtimeline.unplace(moved, 6001, 1)
    assert tl.durations == {6001: 1000} and len(tl.placements[6001]) == 2  # the original is never changed
    assert placed.durations == {6001: 1500} and [(p.sound, p.at_ms) for p in gone.placements[6001]] == [
        (12, 50),
        (1, 700),
    ]
    assert sndtimeline.render(sndtimeline.parse(sndtimeline.render(gone))) == sndtimeline.render(gone)
    with pytest.raises(sndtimeline.CTimelineError):
        sndtimeline.unplace(tl, 6001, 5)
    with pytest.raises(sndtimeline.CTimelineError):
        sndtimeline.set_length(tl, 6001, -1)


def test_a_documents_timeline_edit_is_one_undo_step_and_a_file_to_keep():
    files = {"data/sound/sounddatabase.xml": DB, "data/graphics/m.snd": SND.encode()}
    doc = olddoc.COldSoundDoc(tree_of(files), "data/sound/sounddatabase.xml")
    doc.edit_timeline("data/graphics/m.snd", sndtimeline.place, 6001, 12, 400)
    assert [p.at_ms for p in doc.timeline("data/graphics/m.snd").placements[6001]] == [0, -1, 400]
    assert list(doc.files()) == ["data/graphics/m.snd"]
    assert doc.undo() and doc.files() == {} and len(doc.timeline("data/graphics/m.snd").placements[6001]) == 2
    with pytest.raises(olddoc.COldEditError, match="no placement"):
        doc.edit_timeline("data/graphics/m.snd", sndtimeline.unplace, 6001, 9)
    with pytest.raises(olddoc.COldEditError, match="does not read"):
        doc.timeline("data/graphics/none.snd")


def test_a_sound_is_added_to_a_list_so_the_game_draws_it():
    plain = sounddb.add_member(DB, 98000, 12, 30)
    assert sounddb.read(plain).lists[98000].events[sounddb.NO_EVENT] == [(1, 50), (12, 30)]
    data, new = sounddb.add_sound(DB, "data/sound/c.wav", like=1)
    in_event = sounddb.add_member(data, 40, new, 5, event=4)
    assert sounddb.read(in_event).lists[40].events[4][-1] == (new, 5)
    assert sounddb.read(sounddb.remove_member(in_event, 40, new, event=4)).lists[40].events == {4: [(1, 40), (12, 10)]}
    assert sounddb.remove_member(plain, 98000, 12) == DB  # byte for byte what it was
    for call in (lambda: sounddb.add_member(DB, 98000, 1, 5), lambda: sounddb.add_member(DB, 98000, 999, 5),
                 lambda: sounddb.add_member(DB, 40, 12, 5), lambda: sounddb.add_member(DB, 7, 1, 5),
                 lambda: sounddb.remove_member(DB, 98000, 12)):  # fmt: skip
        with pytest.raises(sounddb.CSoundDbError):
            call()
