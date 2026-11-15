"""The sound editor's music edits: a segment's markers, a container's transition rules, and the segment and markers
fitted to a track's clip of another length. Synthetic banks of Anno 117's layout (172) only."""

from __future__ import annotations

import array
import struct

import pytest
from tests.test_soundedit import VERSIONS, bank_bytes

from annotoolbox.formats import bnk, bnkmusic
from annotoolbox.soundedit import musicedits
from annotoolbox.soundedit.bankdoc import BankDoc
from annotoolbox.soundedit.registry import EditError

ENTRY, EXIT, CUSTOM = musicedits.CUE_ENTRY, musicedits.CUE_EXIT, bnk.fnv1_32("Verse")
NODE_BASE = bytes(4) + struct.pack("<II", 0, 0) + bytes(3) + bytes(2) + struct.pack("<I", 0) + bytes(6) + bytes(4)
METER = bnkmusic.MeterInfo(1000.0, 0.0, 120.0, 4, 4, 0)


def rule(sync: int = 7, fade: int = 3000, src=(0xFFFFFFFF,), dst=(0xFFFFFFFF,)) -> bnkmusic.TransRule:
    return bnkmusic.TransRule(list(src), list(dst), fade, 4, 0, sync, 0, 1, 0, 4, 0, 0, 0, 0, 0, 1, 0, b"")


def track(oid: int, media: int, play_at: float, begin: float, end: float, source: float) -> bnkmusic.MusicTrack:
    item = bnkmusic.PlaylistItem(oid, media, 0, play_at, begin, end, source)
    src = bnkmusic.TrackSource(bnk.PCM_PLUGIN, 2, media, 3000, 0)
    return bnkmusic.MusicTrack(oid, [src], 0, [item], 0, struct.pack("<I", 0), NODE_BASE, 0, b"", 0)


def segment(oid: int, children: list[int], duration: float, markers: list[tuple[int, float, bytes]]):
    marks = [bnkmusic.Marker(i, p, n) for i, p, n in markers]
    return bnkmusic.MusicSegment(oid, 0, NODE_BASE, children, METER, struct.pack("<I", 0), duration, marks)


def music(rules: list[bnkmusic.TransRule] | None = None, version: int = 172) -> BankDoc:
    """Segment 30 (10 s, markers at 0, 4 s, 10 s) over track 31 (a 12 s clip trimmed 1 s at the start and 1 s at the
    end), segment 40 over the two tracks 41 (10 s) and 42 (6 s), a playlist 50 with ``rules``, a switch container 60."""
    marks = [(ENTRY, 0.0, b""), (CUSTOM, 4000.0, b"Verse"), (EXIT, 10000.0, b"")]
    nodes = [bnkmusic.PlaylistNode(30, 70, 0, 0xFFFFFFFF, 1, 0, 0, 50, 0, 1, 0)]
    playlist = bnkmusic.MusicPlaylist(
        50, 0, NODE_BASE, [30], METER, struct.pack("<I", 0), bnkmusic.render_trans_rules(rules or []), nodes
    )
    objs = [
        bnkmusic.render_track(track(31, 77, -1000.0, 1000.0, -1000.0, 12000.0)),
        bnkmusic.render_segment(segment(30, [31], 10000.0, marks)),
        bnkmusic.render_track(track(41, 78, 0.0, 0.0, 0.0, 10000.0)),
        bnkmusic.render_track(track(42, 79, 0.0, 0.0, 0.0, 6000.0)),
        bnkmusic.render_segment(segment(40, [41, 42], 10000.0, [(ENTRY, 0.0, b""), (EXIT, 10000.0, b"")])),
        bnkmusic.render_playlist(playlist),
    ]
    head = bnk.Chunk(b"BKHD", struct.pack("<III", version, 5, 0))
    return BankDoc(bnk.write(bnk.SoundBank([head, bnk.Chunk(b"HIRC", b"")], objs, [])))


def clip(seconds: float) -> bytes:
    return bnk.pcm_wem(array.array("h", [i % 900 for i in range(round(48000 * seconds))]), 48000, 1)


def markers_of(doc: BankDoc, segment_id: int = 30) -> list[tuple[int, float]]:
    return [(m.id, round(m.position, 3)) for m in bnkmusic.parse_segment(doc.obj(segment_id)).markers]


def test_the_cue_ids_are_the_hashes_of_their_names():
    assert (ENTRY, EXIT) == (bnk.fnv1_32("Entry Cue"), bnk.fnv1_32("Exit Cue"))


def test_a_marker_is_moved_renamed_added_and_removed():
    doc = music()
    doc.do("set_marker", segment=30, marker=CUSTOM, position=5500.0, label="Chorus")
    seg = bnkmusic.parse_segment(doc.obj(30))
    assert [(m.id, m.position, m.name) for m in seg.markers][1] == (CUSTOM, 5500.0, b"Chorus")
    extra = bnk.fnv1_32("Bridge")
    doc.do("add_marker", segment=30, marker=extra, position=7000.0, label="Bridge")
    assert [i for i, _p in markers_of(doc)] == [ENTRY, CUSTOM, extra, EXIT]  # before the exit cue
    doc.do("remove_marker", segment=30, marker=CUSTOM)
    assert [i for i, _p in markers_of(doc)] == [ENTRY, extra, EXIT]
    for _ in range(3):
        doc.undo()
    assert markers_of(doc) == [(ENTRY, 0.0), (CUSTOM, 4000.0), (EXIT, 10000.0)] and not doc.changed


def test_markers_are_refused_outside_the_segment_without_the_cues_or_twice():
    doc = music()
    before = bnk.write(doc.bank)
    for name, args in (
        ("set_marker", {"segment": 30, "marker": CUSTOM, "position": 10001.0}),
        ("set_marker", {"segment": 30, "marker": CUSTOM, "position": -1.0}),
        ("set_marker", {"segment": 30, "marker": CUSTOM, "position": float("nan")}),
        ("set_marker", {"segment": 30, "marker": 5, "position": 1.0}),
        ("remove_marker", {"segment": 30, "marker": EXIT}),
        ("remove_marker", {"segment": 30, "marker": ENTRY}),
        ("add_marker", {"segment": 30, "marker": CUSTOM, "position": 1.0}),
        ("set_markers", {"segment": 30, "markers": [(ENTRY, 0.0, "")]}),
        ("set_marker", {"segment": 31, "marker": ENTRY, "position": 1.0}),
    ):
        with pytest.raises(EditError):
            doc.do(name, **args)
    assert bnk.write(doc.bank) == before and not doc.changed


def test_a_clip_of_another_length_scales_the_trims_the_segment_and_its_markers():
    doc = music()
    note = doc.do("replace_track_audio", track=31, index=0, wem=clip(6.0), media=77)  # 12 s -> 6 s: half
    item = bnkmusic.parse_track(doc.obj(31)).playlist[0]
    assert (item.src_duration, item.begin_trim, item.end_trim, item.play_at) == (
        pytest.approx(6000.0),
        pytest.approx(500.0),
        pytest.approx(-500.0),
        pytest.approx(-500.0),
    )
    assert bnkmusic.parse_segment(doc.obj(30)).duration == pytest.approx(5000.0)
    assert markers_of(doc) == [(ENTRY, 0.0), (CUSTOM, 2000.0), (EXIT, 5000.0)]
    assert "5000 ms" in note and "3 markers" in note
    doc.undo()
    assert markers_of(doc) == [(ENTRY, 0.0), (CUSTOM, 4000.0), (EXIT, 10000.0)]


def test_keeping_the_trims_moves_only_the_exit_cue_and_clamps_the_rest():
    doc = music()
    doc.do("add_marker", segment=30, marker=bnk.fnv1_32("Late"), position=9000.0, label="Late")
    doc.do("replace_track_audio", track=31, index=0, wem=clip(8.0), media=77, markers="keep")
    item = bnkmusic.parse_track(doc.obj(31)).playlist[0]
    assert (item.begin_trim, item.end_trim, item.play_at, round(item.src_duration)) == (1000.0, -1000.0, -1000.0, 8000)
    assert bnkmusic.parse_segment(doc.obj(30)).duration == pytest.approx(6000.0)  # 8 s - 1 s - 1 s
    assert [p for _i, p in markers_of(doc)] == [
        0.0,
        4000.0,
        6000.0,
        6000.0,
    ]  # the late one clamped, the exit at the end
    with pytest.raises(EditError, match="cut away"):
        doc.do("replace_track_audio", track=31, index=0, wem=clip(1.5), media=77, markers="keep")
    with pytest.raises(EditError):
        doc.do("replace_track_audio", track=31, index=0, wem=clip(8.0), media=77, markers="stretch")


def test_a_segment_that_is_not_its_tracks_length_keeps_its_markers():
    doc = music()
    note = doc.do("replace_track_audio", track=42, index=0, wem=clip(3.0), media=79)  # the 6 s layer of segment 40
    assert "stay" in note
    seg = bnkmusic.parse_segment(doc.obj(40))
    assert seg.duration == 10000.0 and [m.position for m in seg.markers] == [0.0, 10000.0]
    assert bnkmusic.parse_track(doc.obj(42)).playlist[0].src_duration == pytest.approx(3000.0)


def test_transition_rules_are_changed_added_and_removed():
    doc = music([rule(sync=7), rule(sync=0, fade=0)])
    doc.do("set_transition", container=50, index=0, fields={"sync_type": 2, "src_fade_time": 1500, "src": [30]})
    start, end = bnkmusic.locate_trans_rules(doc.obj(50).payload)
    rules = bnkmusic.parse_trans_rules(bnkmusic.Reader(doc.obj(50).payload[start:end]))
    assert (rules[0].sync_type, rules[0].src_fade_time, rules[0].src, rules[1].sync_type) == (2, 1500, [30], 0)
    doc.do("add_transition", container=50, copy=1, fields={"dst_fade_time": 800})
    doc.do("remove_transition", container=50, index=0)
    start, end = bnkmusic.locate_trans_rules(doc.obj(50).payload)
    rules = bnkmusic.parse_trans_rules(bnkmusic.Reader(doc.obj(50).payload[start:end]))
    assert [(r.sync_type, r.dst_fade_time) for r in rules] == [(0, 0), (0, 800)]
    assert bnkmusic.parse_playlist(doc.obj(50)).nodes[0].weight == 50  # what follows the rules is untouched
    for _ in range(3):
        doc.undo()
    assert not doc.changed


def test_a_rule_edit_that_does_not_fit_is_refused_and_leaves_the_bank():
    doc = music([rule()])
    before = bnk.write(doc.bank)
    for name, args in (
        ("set_transition", {"container": 50, "index": 3, "fields": {"sync_type": 1}}),
        ("set_transition", {"container": 50, "index": 0, "fields": {"nonsense": 1}}),
        ("set_transition", {"container": 50, "index": 0, "fields": {"play_pre_entry": 300}}),
        ("set_transition", {"container": 50, "index": 0, "fields": {"src_fade_time": 2**31}}),
        ("set_transition", {"container": 30, "index": 0, "fields": {}}),
        ("remove_transition", {"container": 50, "index": 1}),
        ("add_transition", {"container": 50, "copy": 4}),
    ):
        with pytest.raises(EditError):
            doc.do(name, **args)
    assert bnk.write(doc.bank) == before


@pytest.mark.parametrize("version", (112, 135))
def test_music_is_refused_in_the_older_banks(version):
    doc = BankDoc(bank_bytes(version))
    for name, args in (
        ("set_markers", {"segment": 20, "markers": []}),
        ("set_transition", {"container": 20, "index": 0, "fields": {}}),
    ):
        with pytest.raises(EditError, match="Anno 117"):
            doc.do(name, **args)
    assert VERSIONS  # the older layouts' music objects are not read, so no edit is offered for them


def test_the_marker_and_rule_texts_parse_and_print_back():
    pytest.importorskip("PySide6")
    from annotoolbox.soundedit import musicpanel

    marks = bnkmusic.parse_segment(music().obj(30)).markers
    assert musicpanel.markers_text(marks) == f"{ENTRY}:0, {CUSTOM}:4000:Verse, {EXIT}:10000"
    assert musicpanel.parse_markers(musicpanel.markers_text(marks)) == [
        (ENTRY, 0.0, ""), (CUSTOM, 4000.0, "Verse"), (EXIT, 10000.0, ""),
    ]  # fmt: skip
    assert musicpanel.parse_markers("Verse:4000") == [(CUSTOM, 4000.0, "Verse")]  # a name gets the id Wwise gives it
    assert musicpanel.parse_fields("sync_type=7, src=30;31, dst=") == {"sync_type": 7, "src": [30, 31], "dst": []}
    for bad in ("nothing", "sync_type=x"):
        with pytest.raises(ValueError):
            musicpanel.parse_fields(bad)
    with pytest.raises(ValueError):
        musicpanel.parse_markers("5:abc")


def test_the_detail_panel_lists_markers_rules_and_track_items_and_the_waveform_gets_the_cues():
    from annotoolbox.soundedit import outline

    doc = music([rule(sync=2)])
    text = "\n".join(outline.describe(doc, 30))
    assert "10000.0 ms long, 3 markers" in text and f"marker Verse ({CUSTOM}) at 4000.0 ms" in text
    assert "marker Entry Cue" in text
    assert "trims 1000.0 and -1000.0 ms, clip 12000.0 ms" in "\n".join(outline.describe(doc, 31))
    assert "rule 0:" in "\n".join(outline.describe(doc, 50)) and "sync 2" in "\n".join(outline.describe(doc, 50))
    marks = musicedits.track_marks(doc, 31)  # the clip starts 1 s before the segment: its cues sit 1 s further in
    assert marks == [(1.0, "Entry Cue"), (5.0, "Verse"), (11.0, "Exit Cue")]
    assert musicedits.track_marks(doc, 30) == []


def test_the_window_edits_markers_and_rules_through_the_music_panel(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.soundedit.soundwindow import SoundWindow
    from annotoolbox.suite.settings import Settings

    QApplication.instance() or QApplication([])
    window = SoundWindow(Settings())
    window.doc = music([rule()])
    window._fill_outline()
    window._select(30)
    assert window.music.markers.text().startswith(f"{ENTRY}:0")
    window.music.markers.setText(f"{ENTRY}:0, {CUSTOM}:2500:Verse, {EXIT}:10000")
    window.music._apply_markers()
    assert markers_of(window.doc)[1] == (CUSTOM, 2500.0)
    window._select(50)
    window.music.fields.setText("sync_type=2")
    window.music._apply_rule()
    assert "sync 2" in window.facts.toPlainText()
    window.music.markers.setText("")
    window._select(30)
    window.music.markers.setText("x")
    window.music._apply_markers()  # no number: said, not raised
    assert "write the markers" in window.statusBar().currentMessage()
    window._select(31)
    window.music.fit.setCurrentText(musicedits.KEEP)
    assert window.music.fit.currentText() == "keep"


def test_a_rule_index_that_does_not_exist_says_how_many_rules_there_are():
    doc = music([rule()])
    with pytest.raises(EditError, match="there are 1 rules, none at 3"):
        doc.do("set_transition", container=50, index=3, fields={})
    with pytest.raises(EditError, match="there are 1 rules, none at 2"):
        doc.do("add_transition", container=50, copy=2)
    with pytest.raises(EditError, match="there are 1 rules, none at 1"):
        doc.do("remove_transition", container=50, index=1)


def test_comma_lists_and_button_columns_are_made_in_one_place():
    from PySide6.QtWidgets import QApplication, QFormLayout, QVBoxLayout, QWidget

    from annotoolbox.soundedit import formrows
    from annotoolbox.soundedit.curvepanel import parse_points
    from annotoolbox.soundedit.musicpanel import parse_fields, parse_markers

    assert formrows.entries(" a , b,, c ,") == ["a", "b", "c"] and formrows.entries("  ") == []
    assert parse_points("0:1, 5:2:9,") == [(0.0, 1.0, 4), (5.0, 2.0, 9)]
    assert parse_markers("12:500, Verse:4000")[0] == (12, 500.0, "") and parse_fields("sync_type=7, src=3;4,") == {
        "sync_type": 7,
        "src": [3, 4],
    }
    QApplication.instance() or QApplication([])
    seen = []
    hosts = [QWidget(), QWidget()]
    for layout in (QFormLayout(hosts[0]), QVBoxLayout(hosts[1])):
        buttons = formrows.add_buttons(layout, (("Play", lambda: seen.append("p")), ("Stop", lambda: seen.append("s"))))
        assert [b.text() for b in buttons] == ["Play", "Stop"]
        buttons[1].click()
    assert seen == ["s", "s"]
