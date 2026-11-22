"""The sound editor's core without Qt: banks of the three layouts (112, 135, 172) opened, edited, written and read back;
the clips it encodes; the files an edit makes in every language folder. Synthetic banks only."""

from __future__ import annotations

import array
import struct
import sys

import pytest
from tests.formats.formatfakes import NEW_BANK as NEW
from tests.formats.formatfakes import OLD_BANK as OLD
from tests.formats.formatfakes import node_base
from tests.soundedit.soundeditfakes import BANK_PATH, bank_bytes, bank_window, doc_of, snd, wem
from tests.soundedit.soundeditfakes import BANK_VERSIONS as VERSIONS

from annotoolbox.exporting import banks
from annotoolbox.formats import bnk, bnkgraph, bnkmusic, wemadpcm
from annotoolbox.infra.i18n import tr
from annotoolbox.soundedit import clipcodec, containers, nodebase, outputs
from annotoolbox.soundedit.bankdoc import CBankDoc
from annotoolbox.soundedit.registry import CEditError
from annotoolbox.sources import gametree
from annotoolbox.suite import buildout


@pytest.mark.parametrize("version", VERSIONS)
def test_a_bank_nothing_was_done_to_comes_back_byte_for_byte(version):
    data = bank_bytes(version)
    doc = CBankDoc(data)
    assert doc.to_bytes() == data and not doc.changed
    doc.do("set_props", id=11, props={0: -3.0})
    doc.undo()
    assert doc.to_bytes() != b"" and bnk.write(doc.m_bank) == data  # the objects are what they were


@pytest.mark.parametrize("version", VERSIONS)
def test_volume_and_pitch_are_set_read_back_and_undone(version):
    doc = doc_of(version)
    pitch, volume = nodebase.prop_id(version, "pitch"), nodebase.prop_id(version, "volume")
    doc.do(
        "set_props",
        id=11,
        props={volume: -6.0, pitch: 150.0},
        ranges={pitch: (-50.0, 50.0)},
    )
    block = nodebase.read_props(bnk.parse(doc.to_bytes()).find(11), version)
    assert block.float(volume) == -6.0 and block.float(pitch) == 150.0 and block.range(pitch) == (-50.0, 50.0)
    doc.do("set_props", id=11, props={volume: None})
    assert nodebase.read_props(doc.hirc_object(11), version).float(volume, 99.0) == 99.0
    assert doc.undo() == "set_props" and nodebase.read_props(doc.hirc_object(11), version).float(volume) == -6.0
    doc.undo()
    assert bnk.write(doc.m_bank) == bank_bytes(version)
    assert doc.redo() == "set_props" and doc.changed


def test_the_properties_have_the_ids_of_their_version():
    assert nodebase.name_of(112, 2) == "pitch (cents)" and nodebase.name_of(172, 1) == "pitch (cents)"
    assert nodebase.name_of(172, 2) == "low-pass filter"


@pytest.mark.parametrize("version", VERSIONS)
def test_a_random_container_plays_by_its_playlist(version):
    doc = doc_of(version)
    obj, lay = doc.hirc_object(20), containers.read_layout(doc.hirc_object(20), doc.graph[20], version)
    assert lay.playlist == [(11, 50), (12, 70), (11, 10)] and containers.render(obj, lay).payload == obj.payload
    doc.do("set_weight", container=20, child=11, weight=5)
    assert containers.read_layout(doc.hirc_object(20), doc.graph[20], version).playlist == [(11, 5), (12, 70), (11, 5)]
    doc.do("set_playlist", container=20, entries=[(12, 100), (11, 1)])
    assert containers.read_layout(doc.hirc_object(20), doc.graph[20], version).playlist == [(12, 100), (11, 1)]
    with pytest.raises(CEditError):
        doc.do("set_playlist", container=20, entries=[(99, 1)])
    assert doc.undo_name == "set_playlist"  # the refused edit left no step


def test_a_refused_edit_is_raised_as_it_is_not_as_its_own_cause():
    doc = doc_of(NEW)
    with pytest.raises(CEditError) as raised:
        doc.do("set_playlist", container=20, entries=[(99, 1)])
    assert raised.value.__cause__ is None
    with pytest.raises(CEditError) as other:
        doc.do("set_weight", container=999, child=11, weight=5)
    assert other.value.__cause__ is not other.value


def test_a_random_containers_loops_and_order_are_read_in_117s_layout():
    doc = doc_of(NEW)
    doc.do("set_ranseq", container=20, loops=0, avoid_repeat=1, sequence=True)
    params = doc.hirc_object(20).payload[
        doc.graph[20].children and containers.read_layout(doc.hirc_object(20), doc.graph[20], NEW).at - 24 :
    ]
    assert (
        struct.unpack_from("<H", params, 0)[0] == 0 and struct.unpack_from("<H", params, 18)[0] == 1 and params[22] == 1
    )
    with pytest.raises(CEditError):
        doc_of(135).do("set_ranseq", container=20, loops=2)


@pytest.mark.parametrize("version", VERSIONS)
def test_a_sound_is_added_to_a_container_with_the_clip_and_a_playlist_entry(version):
    doc = doc_of(version)
    media = doc.new_id("new_sound_media")
    doc.do("add_sound", sound=77, template=11, parent=20, wem=wem(9), media=media, weight=30)
    node = doc.graph[77]
    assert node.parent == 20 and node.source.media == media and doc.graph[20].children == [11, 12, 77]
    assert containers.read_layout(doc.hirc_object(20), doc.graph[20], version).playlist[-1] == (77, 30)
    if version == OLD:  # the oldest banks keep the audio outside the bank: a loose streamed file
        assert node.source.stream == 2 and media in doc.m_loose and doc.media(media) is None
    else:
        assert node.source.stream == 0 and doc.media(media) == wem(9)
    again = CBankDoc(doc.to_bytes())
    assert again.graph[77].parent == 20 and [o.id for o in again.m_bank.hirc].index(77) < [
        o.id for o in again.m_bank.hirc
    ].index(21)
    doc.do("remove_child", container=20, child=77)
    assert doc.graph[20].children == [11, 12] and doc.graph[77].parent == 0


@pytest.mark.parametrize("version", VERSIONS)
def test_a_new_sound_can_be_assigned_to_a_switch_value(version):
    doc = doc_of(version)
    doc.do("add_sound", sound=78, template=13, parent=30, wem=wem(2), media=doc.new_id("m"), value=2)
    assert doc.graph[30].switch.branches == {1: [13], 2: [14, 78]}
    doc.do("set_branch", container=30, value=1, nodes=[13, 14])
    doc.do("set_branch", container=30, value=3, nodes=[78])
    doc.do("set_switch", container=30, default=2, group=901, state_group=True)
    switch = doc.graph[30].switch
    assert switch.branches == {1: [13, 14], 2: [14, 78], 3: [78]} and (switch.group, switch.default) == (901, 2)
    assert switch.state_group
    with pytest.raises(CEditError):
        doc.do("set_branch", container=30, value=1, nodes=[11])  # not a child of this container


@pytest.mark.parametrize("version", VERSIONS)
def test_an_event_is_added_and_plays_its_target(version):
    doc = doc_of(version)
    doc.do("add_event", event=500, action=501, target=20)
    listing = banks.bank_listing(doc.to_bytes())
    found, exact = banks.event_sounds(listing, 500)
    assert exact and [s.media for s in found] == [1100, 1200]
    doc.do("add_play", event=500, action=502, target=15)
    assert bnkgraph.read_event(doc.hirc_object(500), version) == [501, 502]
    assert sorted(s.media for s in banks.event_sounds(banks.bank_listing(doc.to_bytes()), 500)[0]) == [1100, 1200, 1500]
    doc.do("set_action", action=502, target=13)
    assert bnkgraph.read_action(doc.hirc_object(502)).target == 13
    with pytest.raises(CEditError):
        doc.do("add_event", event=600, action=601, target=11111)  # a target the bank lacks


@pytest.mark.parametrize("version", VERSIONS)
def test_a_set_switch_action_takes_another_group_and_value(version):
    doc = doc_of(version)
    doc.do("set_action", action=23, group=901, value=5)
    read = bnkgraph.read_action(doc.hirc_object(23))
    assert (read.group, read.value, read.target) == (901, 5, 5)
    with pytest.raises(CEditError):
        doc.do("set_action", action=23, target=11)


def test_embedded_media_is_replaced_in_place_and_stays_aligned():
    doc = doc_of(135)
    new = wem(7, 200)
    doc.do("replace_audio", sound=12, wem=new, media=1200)
    out = bnk.parse(doc.to_bytes())
    entries = {m.id: m for m in out.media}
    blob = next(c.data for c in out.chunks if c.tag == b"DATA")
    assert blob[entries[1200].offset : entries[1200].offset + entries[1200].size] == new
    assert all(m.offset % 16 == 0 for m in out.media)
    assert blob[entries[1100].offset : entries[1100].offset + entries[1100].size] == wem(11)
    assert doc.graph[12].source.size == len(new) and CBankDoc(doc.to_bytes()).graph[12].source.size == len(new)
    doc.undo()
    assert doc.media(1200) == wem(12)


def test_a_media_file_two_sounds_share_gets_a_new_id_for_the_sound_that_changes():
    doc = doc_of(135)
    doc.do("add_sound", sound=79, template=11, parent=0, wem=wem(3), media=1100)  # shares media 1100 with 11
    assert doc.media_users(1100) == [11, 79] or sorted(doc.media_users(1100)) == [11, 79]
    media = audio_media = __import__("annotoolbox.soundedit.audioedits", fromlist=["x"]).plan_media(
        doc, 79, "again"
    )
    assert media != 1100
    doc.do("replace_audio", sound=79, wem=wem(4), media=audio_media)
    assert doc.graph[79].source.media == audio_media and doc.graph[11].source.media == 1100


@pytest.mark.parametrize("version", (112, 172))
def test_a_streamed_sound_gets_a_loose_file_and_keeps_its_language_flag(version):
    doc = doc_of(version, streamed=True)
    new = wem(5, 400)
    doc.do("replace_audio", sound=13, wem=new, media=1300)
    src = doc.graph[13].source
    assert src.stream == 2 and doc.m_loose[1300].data == new
    if version == NEW:
        assert src.size == min(len(new), 3000)
    else:
        assert src.size == len(new)
    assert doc.to_bytes() and CBankDoc(doc.to_bytes()).graph[13].source.media == 1300


def test_a_silence_source_is_not_given_a_clip():
    obj = doc_of(NEW).hirc_object(11)
    silent = bnk.SHircObject(
        2, 90, struct.pack("<IBQIBI", 0x00650002, 0, 1, 0, 0, 0) + obj.payload[struct.calcsize("<IBQIB") :]
    )
    doc = doc_of(NEW)
    doc.add(silent)
    with pytest.raises(CEditError, match="silence"):
        doc.do("replace_audio", sound=90, wem=wem(), media=1)


def test_edits_are_replayed_on_another_copy_and_say_what_did_not_apply():
    a = doc_of(NEW)
    a.do("set_props", id=11, props={0: -2.0})
    a.do("add_sound", sound=77, template=11, parent=20, wem=wem(9), media=a.new_id("x"), weight=10)
    b = CBankDoc(bank_bytes(NEW))
    assert b.replay(a.m_log) == [] and b.to_bytes() == a.to_bytes()
    c = CBankDoc(bank_bytes(NEW))
    c.m_bank.hirc = [o for o in c.m_bank.hirc if o.id != 11]
    c._m_graph = c._m_index = None
    assert any("set_props" in p for p in c.replay(a.m_log))


def test_every_language_copy_gets_the_edits_and_loose_clips_go_where_the_game_keeps_them():
    root = "data/sound/generatedsoundbanks/windows/"
    files = {f"{root}{lang}/9.bnk": bank_bytes(NEW) for lang in ("en_us", "de_de")}
    files[f"{root}9.bnk"] = bank_bytes(NEW)
    files[f"{root}other/7.bnk"] = bank_bytes(NEW)
    tree = gametree.CGameTree("t", lambda p: files[p], files, lambda p: "t")
    doc = CBankDoc(files[f"{root}en_us/9.bnk"], f"{root}en_us/9.bnk")
    assert outputs.copies_of(tree.m_paths, doc.m_path) == [f"{root}9.bnk", f"{root}de_de/9.bnk", f"{root}en_us/9.bnk"]
    assert outputs.render(doc, tree).files == {}
    doc.do("set_props", id=11, props={0: -2.0})
    doc.do("replace_audio", sound=11, wem=wem(8, 300), media=1100)
    out = outputs.render(doc, tree)
    assert (
        sorted(out.files) == sorted([f"{root}9.bnk", f"{root}de_de/9.bnk", f"{root}en_us/9.bnk"]) and not out.problems
    )
    assert len(set(out.files.values())) == 1
    streamed = doc_of(NEW, streamed=True)
    streamed.m_path = f"{root}en_us/9.bnk"
    streamed.do("replace_audio", sound=11, wem=wem(8, 300), media=1100)
    streamed.m_loose[1100] = streamed.m_loose[1100].__class__(
        streamed.m_loose[1100].data, True
    )  # a voice: a copy per language
    assert outputs.render(streamed, tree, only=[streamed.m_path]).files.keys() >= {f"{root}media/en_us/1100.wem"}
    streamed.m_loose[1100] = streamed.m_loose[1100].__class__(streamed.m_loose[1100].data, False)
    assert f"{root}media/1100.wem" in outputs.render(streamed, tree, only=[streamed.m_path]).files


def music_bank() -> bytes:
    nb = bytes(4) + struct.pack("<II", 0, 0) + bytes(3) + bytes(2) + struct.pack("<I", 0) + bytes(6) + bytes(4)
    meter = bnkmusic.SMeterInfo(1000.0, 0.0, 120.0, 4, 4, 0)
    source = bnkmusic.STrackSource(bnk.PCM_PLUGIN, 2, 77, 3000, 0)
    track = bnkmusic.CMusicTrack(
        31,
        [source],
        0,
        [bnkmusic.SPlaylistItem(31, 77, 0, 0.0, 0.0, 0.0, 1000.0)],
        0,
        struct.pack("<I", 0),
        nb,
        0,
        b"",
        0,
    )
    segment = bnkmusic.CMusicSegment(30, 0, nb, [31], meter, struct.pack("<I", 0), 1000.0, [])
    nodes = [bnkmusic.SPlaylistNode(30, 40, 0, 0xFFFFFFFF, 1, 0, 0, 50, 0, 1, 0)]
    playlist = bnkmusic.SMusicPlaylist(
        32, 0, nb, [30], meter, struct.pack("<I", 0), bnkmusic.render_trans_rules([]), nodes
    )
    objs = [bnkmusic.render_track(track), bnkmusic.render_segment(segment), bnkmusic.render_playlist(playlist)]
    head = bnk.SChunk(b"BKHD", struct.pack("<III", NEW, 5, 0))
    return bnk.write(bnk.SSoundBank([head, bnk.SChunk(b"HIRC", b"")], objs, []))


def test_a_music_playlist_entry_and_a_tracks_clip_are_edited():
    doc = CBankDoc(music_bank())
    doc.do("set_music_node", container=32, index=0, fields={"weight": 90, "avoid_repeat": 2})
    node = bnkmusic.parse_playlist(doc.hirc_object(32)).nodes[0]
    assert (node.weight, node.avoid_repeat, node.loop) == (90, 2, 1)
    with pytest.raises(CEditError):
        doc.do("set_music_node", container=32, index=0, fields={"bogus": 1})
    new = bnk.pcm_wem(array.array("h", [i % 1000 for i in range(48000 * 2)]), 48000, 1)  # two seconds
    doc.do("replace_track_audio", track=31, index=0, wem=new, media=77)
    track = bnkmusic.parse_track(doc.hirc_object(31))
    assert track.m_sources[0].stream_type == 2 and doc.m_loose[77].data == new
    assert track.m_playlist[0].source_duration_ms == pytest.approx(2000.0)
    assert bnkmusic.parse_segment(doc.hirc_object(30)).m_duration == pytest.approx(2000.0)


# the clips


def wav(samples: list[int], rate: int = 44100, channels: int = 1, bits: int = 16) -> bytes:
    if bits == 16:
        data = struct.pack(f"<{len(samples)}h", *samples)
    else:  # 24 bit
        data = b"".join(struct.pack("<i", s << 8)[:3] for s in samples)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.mark.parametrize("decoders", [True, False])
def test_wav_clips_of_16_and_24_bits_are_read_as_16_bit_samples(monkeypatch, decoders):
    if not decoders:  # the channel count is the reason whether or not ffmpeg or miniaudio is installed
        monkeypatch.setattr(clipcodec, "find_tool", lambda name: None)
        monkeypatch.setitem(sys.modules, "miniaudio", None)
    clip = clipcodec.read_clip(wav([0, 1000, -1000, 32767]), "a.wav")
    assert clip.samples.tolist() == [0, 1000, -1000, 32767] and (clip.rate, clip.channels) == (44100, 1)
    assert clipcodec.read_clip(wav([0, 1000, -1000], bits=24), "b.wav").samples.tolist() == [0, 1000, -1000]
    with pytest.raises(clipcodec.CClipError, match="channels"):
        clipcodec.read_clip(wav([0] * 6, channels=3), "c.wav")


def test_pcm_and_adpcm_clips_decode_to_what_they_were_made_from():
    import math

    pcm = array.array("h", [round(8000 * math.sin(i / 9)) for i in range(640)])
    clip = clipcodec.SClip(pcm, 32000, 1)
    assert bnk.read_pcm_wem(clipcodec.encode(clip, "pcm", NEW))[0] == pcm
    adpcm = clipcodec.encode(clip, "adpcm", 112)
    assert banks.wave_info(adpcm)["format"] == "ADPCM" and banks.wave_info(adpcm)["seconds"] == pytest.approx(
        640 / 32000
    )
    decoded, rate, channels = wemadpcm.read(adpcm)
    assert (rate, channels, len(decoded)) == (32000, 1, 640)
    assert (
        max(abs(int(a) - int(b)) for a, b in zip(decoded[16:], pcm[16:], strict=True)) < 700
    )  # the step has grown by then
    stereo = clipcodec.SClip(array.array("h", [0, 100] * 128), 44100, 2)
    assert wemadpcm.read(clipcodec.encode(stereo, "adpcm", NEW))[2] == 2


def test_wwise_vorbis_availability_names_what_is_missing_and_codecs_are_checked(monkeypatch):
    for version in VERSIONS:
        assert clipcodec.availability(version)["pcm"] == "" == clipcodec.availability(version)["adpcm"]
    monkeypatch.setattr(clipcodec, "find_tool", lambda name: None)
    for version in VERSIONS:  # the layout is known in all three: what is missing is the tool
        assert "ffmpeg is not installed" in clipcodec.availability(version)["vorbis"]
    with pytest.raises(clipcodec.CClipError, match="vorbis cannot be written"):
        clipcodec.encode(clipcodec.SClip(array.array("h", [0] * 10), 48000, 1), "vorbis", 135)
    with pytest.raises(clipcodec.CClipError, match="no codec"):
        clipcodec.encode(clipcodec.SClip(array.array("h", [0]), 48000, 1), "mp3", NEW)


def test_wem_plugin_names_the_codec_of_a_file():
    from annotoolbox.soundedit import audioedits

    assert audioedits.wem_plugin(wem()) == bnk.PCM_PLUGIN
    assert (
        audioedits.wem_plugin(clipcodec.adpcm_wem(clipcodec.SClip(array.array("h", [0] * 64), 8000, 1)))
        == clipcodec.ADPCM_PLUGIN
    )
    with pytest.raises(CEditError):
        audioedits.wem_plugin(b"not a wem")


def test_node_base_helpers_find_the_parent_of_every_object_kind():
    assert (
        bnkgraph.parent_of(snd(NEW, 11, 1100, 20), NEW) == 20
        and bnkgraph.parent_of(doc_of(135).hirc_object(30), 135) == 0
    )
    moved = bnkgraph.with_parent(snd(112, 11, 1100, 20), 112, 77)
    assert bnkgraph.parent_of(moved, 112) == 77
    assert node_base(NEW, 5)  # the builder the graph tests share is what the editor reads


def test_the_outline_lists_events_the_hierarchy_and_music():
    from annotoolbox.soundedit import outline

    doc = doc_of(135)
    lists = {tr(title): rows for title, rows in outline.outline(doc).items()}
    assert [r.id for r in lists["Events"]] == [1, 2, 3]
    tops = {r.id: [c.id for c in r.children] for r in lists["Sounds and containers"]}
    assert tops == {20: [11, 12], 30: [13, 14], 40: [15]}
    assert "pitch" not in " ".join(outline.describe(doc, 11)) and outline.describe(doc, 20)[1].startswith("parent none")
    doc.do("set_props", id=11, props={0: -3.0})
    assert "volume (dB): -3" in outline.describe(doc, 11)


def test_the_outline_looks_a_child_up_by_the_index_not_by_a_walk_of_every_object():
    from annotoolbox.soundedit import outline

    class FakeCountedList(list):
        walks = 0

        def __iter__(self):
            FakeCountedList.walks += 1
            return super().__iter__()

    doc = doc_of(172)
    edges = sum(len(node.children) for node in doc.graph.values())
    doc.m_bank.hirc = FakeCountedList(doc.m_bank.hirc)
    outline.outline(doc)
    # one walk more per child made a bank of thousands of objects freeze the window at every edit
    assert edges == 5 and FakeCountedList.walks <= 6


def test_saving_keeps_the_files_in_the_project_and_announces_banks(tmp_path):
    from tests.fakegame import FakeLink

    from annotoolbox.soundedit import projectpart
    from annotoolbox.suite import livelink
    from annotoolbox.suite.project import CProject

    project = CProject(tmp_path, mod_id="m")
    link = FakeLink()
    livelink.use(link)
    try:
        notice = projectpart.save(project, "anno117", {"data/sound/generatedsoundbanks/windows/9.bnk": b"x"})
    finally:
        livelink.use(livelink.CNotConnected())
    assert link.names == [] and notice.text.startswith(
        "saved in the project: build and install"
    )  # sent with the install
    assert projectpart.PART.has_work(project) and buildout.read_all(
        projectpart.PART.render(project, None, lambda _t: None)
    ) == {"data/sound/generatedsoundbanks/windows/9.bnk": b"x"}


def test_opening_a_game_lists_its_banks_and_counts_them(sound_window, monkeypatch):
    from annotoolbox.soundedit import soundwindow

    files = {BANK_PATH: bank_bytes(172)}
    tree = gametree.CGameTree("t", lambda p: files[p], files, lambda p: "t")
    monkeypatch.setattr(soundwindow.gameopen, "open_installed", lambda *a, then, **k: then(tree))
    sound_window.open_game("anno117")
    assert sound_window.m_banks.count() == 1 and "1" in sound_window.statusBar().currentMessage()


def test_the_window_lists_a_bank_and_edits_through_the_forms(sound_window, monkeypatch):
    window = bank_window(sound_window, monkeypatch, 172)
    window.m_outline.expandAll()
    window.m_outline.setCurrentItem(window.m_outline.topLevelItem(1).child(0).child(0))  # sound 11
    window.m_volume.setValue(-4)
    window._apply()
    assert nodebase.read_props(window.m_doc.hirc_object(11), 172).float(0) == -4.0
    window.undo()
    assert not window.m_doc.changed


def test_a_group_heading_of_the_outline_is_no_object(sound_window, monkeypatch):
    window = bank_window(sound_window, monkeypatch, 172)
    window.m_outline.expandAll()
    window.m_outline.setCurrentItem(window.m_outline.topLevelItem(1).child(0).child(0))
    assert window.m_selected == 11
    window.m_outline.setCurrentItem(window.m_outline.topLevelItem(1))  # the heading carries no object id
    assert window.m_selected == 0


@pytest.mark.parametrize("version", VERSIONS)
def test_a_container_cannot_be_put_under_itself_or_below_its_own_child(version):
    doc = doc_of(version)
    before = bnk.write(doc.m_bank)
    with pytest.raises(CEditError):
        doc.do("add_child", container=20, child=20)
    doc.do("add_child", container=40, child=20)
    with pytest.raises(CEditError):
        doc.do("add_child", container=20, child=40)
    doc.undo()
    assert bnk.write(doc.m_bank) == before and not doc.changed


@pytest.mark.parametrize("version", VERSIONS)
def test_the_last_child_of_any_container_can_be_removed_and_added_again(version):
    doc = doc_of(version)
    before = bnk.write(doc.m_bank)
    doc.do("remove_child", container=40, child=15)  # the only child of the Actor-Mixer
    assert doc.graph[40].children == [] and doc.graph[40].exact
    doc.do("remove_child", container=20, child=11)
    doc.do("remove_child", container=20, child=12)  # the Random container emptied, playlist and all
    assert doc.graph[20].children == [] and doc.graph[20].exact
    again = CBankDoc(doc.to_bytes())  # the emptied containers read back from the written bank
    assert again.graph[20].children == [] and again.graph[40].children == []
    doc.do("add_child", container=20, child=12, weight=7)
    doc.do("add_child", container=20, child=11)
    doc.do("add_child", container=40, child=15)
    assert doc.graph[20].children == [12, 11] and doc.graph[40].children == [15]
    assert containers.read_layout(doc.hirc_object(20), doc.graph[20], version).playlist == [(12, 7), (11, 50)]
    for _ in range(5):
        doc.undo()
    doc.undo()
    assert bnk.write(doc.m_bank) == before


def test_an_edit_that_fails_halfway_with_any_error_leaves_the_bank_as_it_was():
    from annotoolbox.soundedit import registry

    @registry.edit
    def _breaks_halfway(doc, id):
        doc.put(bnk.SHircObject(doc.hirc_object(id).type, id, b"changed"))
        raise TypeError("not a number")

    try:
        doc = doc_of(NEW)
        before = bnk.write(doc.m_bank)
        with pytest.raises(CEditError):
            doc.do("_breaks_halfway", id=11)
        assert bnk.write(doc.m_bank) == before and not doc.changed
    finally:
        registry.g_edits.pop("_breaks_halfway", None)


def test_a_save_after_undoing_everything_takes_the_earlier_files_out_of_the_project(
    sound_window, monkeypatch, tmp_path
):
    from annotoolbox.soundedit import projectpart

    window = bank_window(sound_window, monkeypatch, 172, tmp_path)
    project = window.project()
    window.m_selected = 11
    window._edit("set_props", id=11, props={0: -4.0})
    assert window.statusBar().currentMessage().startswith("change the settings done")  # the label, not the name
    window.save()
    assert projectpart.EDITS.saved_files(project) != {}
    window.undo()
    window.save()
    assert projectpart.EDITS.saved_files(project) == {}


def test_the_files_a_save_made_are_remembered_per_game_not_per_bank_path(sound_window, monkeypatch, tmp_path):
    from annotoolbox.soundedit import projectpart

    window = bank_window(sound_window, monkeypatch, 172, tmp_path)
    project = window.project()
    window._edit("set_props", id=11, props={0: -4.0})
    window.save()
    other = projectpart.EDITS.folder(project, "anno1800")
    stale = other.joinpath(*BANK_PATH.split("/"))
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"another game's bank")
    window.m_key = "anno1800"  # the other game's bank of the same path, opened and not edited
    window.open_bank(BANK_PATH)
    window.save()
    assert stale.read_bytes() == b"another game's bank"


def test_unkept_edits_are_asked_about_before_another_bank_opens_or_the_window_closes(
    sound_window, monkeypatch, tmp_path
):
    window = bank_window(sound_window, monkeypatch, 172, tmp_path)
    path, project = BANK_PATH, window.project()
    from PySide6.QtWidgets import QMessageBox  # after the window: the helper skips the test without Qt

    window.m_doc.do("set_props", id=11, props={0: -4.0})
    answers, shown = [QMessageBox.StandardButton.Cancel], []
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: answers[0])
    monkeypatch.setattr(QMessageBox, "warning", lambda _w, _t, text, *_a: shown.append(text))
    edited = window.m_doc
    window.open_bank(path)
    assert window.m_doc is edited  # cancelled: the edits stay
    window.show()
    assert not window.close() and window.isVisible()
    answers[0] = QMessageBox.StandardButton.Save
    monkeypatch.setattr(window, "project", lambda: (_ for _ in ()).throw(OSError("read-only")))
    assert not window.close() and window.isVisible() and "read-only" in shown[0]  # a failed save keeps it open
    monkeypatch.setattr(window, "project", lambda: project)
    assert window.close() and not window.m_doc.unsaved  # saved, then closed
    window.m_doc.do("set_props", id=11, props={0: -2.0})
    answers[0] = QMessageBox.StandardButton.Discard
    window.open_bank(path)
    assert window.m_doc is not edited and not window.m_doc.unsaved


def test_a_replaced_streamed_clip_goes_where_the_banks_info_says_it_lives():
    root = "data/sound/generatedsoundbanks/windows/"
    info = banks.SBankInfo(media={1100: ("clip", "voices/en/1100.wem")})
    files = {f"{root}9.bnk": bank_bytes(NEW, streamed=True), f"{root}voices/en/1100.wem": b"old"}
    tree = gametree.CGameTree("t", lambda p: files[p], files, lambda p: "t")
    doc = CBankDoc(files[f"{root}9.bnk"], f"{root}9.bnk", info)
    doc.do("replace_audio", sound=11, wem=wem(8, 300), media=1100)
    out = outputs.render(doc, tree, only=[doc.m_path])
    assert f"{root}voices/en/1100.wem" in out.files and f"{root}media/1100.wem" not in out.files


def test_a_media_file_a_music_track_plays_counts_its_player():
    doc = CBankDoc(music_bank())
    assert doc.media_users(77) == [31]


def test_a_clip_is_cut_to_whole_frames_and_refuses_samples_that_are_not_numbers():
    stereo = struct.pack("<4h", 1, 2, 3, 4) + b"\x05\x00"  # a cut last frame
    assert list(clipcodec._to_16(stereo, 1, 16, 2)) == [1, 2, 3, 4]
    bad = array.array("f", [0.5, float("nan")]).tobytes()
    with pytest.raises(clipcodec.CClipError):
        clipcodec._to_16(bad, 3, 32, 1)


def test_media_the_index_lists_but_cannot_be_read_is_reported_not_dropped_silently():
    doc = doc_of(NEW)
    doc.m_bank.media.append(bnk.SMediaEntry(5555, 10**6, 40))  # past the end of the data
    doc.drop_embedded(1100)
    doc.to_bytes()
    assert doc.m_unreadable == [5555]


def test_the_settings_form_writes_only_what_was_changed(sound_window, monkeypatch):
    window = bank_window(sound_window, monkeypatch, NEW)
    window.m_doc.do("set_props", id=11, props={nodebase.prop_id(NEW, "volume"): 40.0})  # beyond the box's 24 dB
    window._select(11)
    window.m_pitch.setValue(100)
    window._apply()
    block = nodebase.read_props(window.m_doc.hirc_object(11), NEW)
    assert block.float(nodebase.prop_id(NEW, "volume")) == 40.0 and block.float(nodebase.prop_id(NEW, "pitch")) == 100.0


def test_every_edit_has_a_label_for_the_status_line():
    from annotoolbox.soundedit import (
        bankdoc,  # noqa: F401  # registers the edits
        registry,
    )

    assert registry.g_edits and set(registry.g_edits) == set(registry.g_labels)
    assert registry.label("set_props") == "change the settings"
    assert all("_" not in tr(text) for text in registry.g_labels.values())
    assert registry.label("not_an_edit") == "not_an_edit"


def test_the_edit_checks_say_what_failed_and_pass_an_edit_error_through():
    import struct

    from annotoolbox.soundedit.registry import CEditError, check_byte, check_index, check_u32, reading

    with pytest.raises(CEditError, match=r"the layers of 5 does not read \(bad\)"), reading("the layers of 5"):
        raise ValueError("bad")
    with pytest.raises(CEditError, match="the node base does not read"), reading("the node base"):
        struct.unpack("<I", b"")
    with pytest.raises(CEditError, match="^own words$"), reading("the node base"):
        raise CEditError("own words")
    assert check_index([1, 2], 1, "curves") == 1
    with pytest.raises(CEditError, match="there are 2 curves, none at 2"):
        check_index([1, 2], 2, "curves")
    with pytest.raises(CEditError, match="there are 0 curves, none at -1"):
        check_index([], -1, "curves")
    assert check_u32(2**32 - 1, "an id") == 2**32 - 1 and check_u32(0, "an id") == 0
    with pytest.raises(CEditError, match="an id is a 32-bit number"):
        check_u32(0, "an id", zero=False)
    with pytest.raises(CEditError, match="an id is a 32-bit number"):
        check_u32(2**32, "an id")
    assert check_byte(255, "the scaling") == 255
    with pytest.raises(CEditError, match="the scaling is a number from 0 to 255"):
        check_byte(256, "the scaling")


def test_splice_replaces_a_span_of_an_objects_payload():
    doc = CBankDoc(bank_bytes(172))
    obj = doc.hirc_object(11)
    doc.splice(obj, 2, 4, b"\xaa\xbb\xcc")
    assert doc.hirc_object(11).payload == obj.payload[:2] + b"\xaa\xbb\xcc" + obj.payload[4:]


def test_a_clip_that_does_not_read_is_said_by_play_not_raised(sound_window, monkeypatch):
    window = bank_window(sound_window, monkeypatch)

    def broken(_obj_id):
        raise OSError("the archive is gone")

    monkeypatch.setattr(window, "_media_of", broken)
    window._play()
    assert "the archive is gone" in window.statusBar().currentMessage()
    monkeypatch.setattr(window, "_media_of", lambda _obj_id: None)
    window._play()
    assert window.statusBar().currentMessage() == tr("sound.nothing_play_here_pick_sound")
    window.close()


def test_a_bank_a_waveform_and_a_played_clip_are_read_off_the_window_thread(sound_window, monkeypatch, tmp_path):
    import threading

    from PySide6.QtWidgets import QApplication

    window = bank_window(sound_window, monkeypatch)
    window.m_is_synchronous = False
    monkeypatch.setattr(window, "_cache", lambda: tmp_path)
    release, readers, started = threading.Event(), [], []
    files = {BANK_PATH: bank_bytes(172), "other.bnk": bank_bytes(172, bank_id=7)}

    def read(path):
        readers.append(threading.current_thread())
        assert release.wait(30)
        return files[path]

    def settle():
        release.set()
        window.m_background.join()
        QApplication.processEvents()
        release.clear()

    window.m_tree = gametree.CGameTree("t", read, files, lambda p: "t")
    before = window.m_doc
    window.open_bank(BANK_PATH)
    window.open_bank("other.bnk")  # picked while the first is read: the one asked for last is shown
    assert window.m_doc is before and "other.bnk" in window.statusBar().currentMessage()
    settle()
    assert readers and threading.main_thread() not in readers
    assert window.m_doc is not before and window.m_doc.m_path == "other.bnk"

    media = window._media_of
    monkeypatch.setattr(window, "_media_of", lambda obj: (read(BANK_PATH), media(obj))[1])
    monkeypatch.setattr(window.m_player, "start", started.append)
    del readers[:]
    window._select(11)
    assert window.m_wave.m_shape is None and window.m_wave.m_message == tr("sound.reading_clip")
    window._play()
    assert not started and window.statusBar().currentMessage() == tr("sound.reading_clip")
    settle()
    assert len(readers) == 2 and threading.main_thread() not in readers
    assert window.m_wave.m_shape is not None and [p.parent for p in started] == [tmp_path] and started[0].is_file()
    window.close()


@pytest.mark.parametrize("answers", [[("Weather", False)], [("Weather", True), ("Rain, Sun", False)]])
def test_a_new_group_cancelled_at_any_question_asks_no_more_and_adds_nothing(sound_window, monkeypatch, answers):
    from PySide6.QtWidgets import QInputDialog

    window = bank_window(sound_window, monkeypatch)
    left = list(answers)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *_a, **_k: (_a[3][0], True))  # a state group
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: left.pop(0))  # an IndexError: asked once more
    window._new_group()
    assert left == [] and not window.m_doc.changed and "Weather" not in window.statusBar().currentMessage()
    window.close()


def test_the_played_clips_go_into_the_workspace_cache_not_the_temp_folder(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.assetedit import assetgame
    from annotoolbox.soundedit.soundwindow import CSoundWindow
    from annotoolbox.suite.settings import CSettings

    QApplication.instance() or QApplication([])
    window = CSoundWindow(CSettings(workspace=str(tmp_path / "workspace")))
    try:
        folder = assetgame.cache_dir(tmp_path / "workspace") / "sounds"  # the folder the other apps' clips share
        assert window._cache() == folder and window.m_old.m_cache == folder
    finally:
        window.deleteLater()


@pytest.mark.parametrize(
    "broken", [b"", b"BKHD", struct.error("short"), KeyError(7), IndexError("past"), ValueError("bad")]
)
def test_a_bank_that_does_not_read_is_said_in_the_status_line_whatever_the_reader_raises(
    sound_window, monkeypatch, broken
):
    from annotoolbox.soundedit import soundwindow

    if isinstance(broken, Exception):
        monkeypatch.setattr(soundwindow, "CBankDoc", lambda *_a: (_ for _ in ()).throw(broken))
        files = {BANK_PATH: bank_bytes(172)}
    else:
        files = {BANK_PATH: broken}
    sound_window.m_tree = gametree.CGameTree("t", lambda p: files[p], files, lambda p: "t")
    sound_window.open_bank(BANK_PATH)
    message = sound_window.statusBar().currentMessage()
    assert BANK_PATH in message and message != tr("sound.reading_bank", path=BANK_PATH)
    assert sound_window.m_doc is None


def test_the_window_has_one_status_line_and_no_second_label_for_it(sound_window):
    assert not hasattr(sound_window, "status_label")
    sound_window.statusBar().showMessage("x")
    assert sound_window.statusBar().currentMessage() == "x"
