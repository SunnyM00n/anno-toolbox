"""The music objects of the older banks (formats/bnkmusicold): objects built by hand in the layouts of versions 135 and
112, and, where the installs are set, every music object of 2205's and 1800's banks read to its last byte."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from annotoolbox.exporting import banks
from annotoolbox.formats import bnk, bnkmusic, bnkmusicold
from annotoolbox.formats.bnk import HircObject


def node_base(version: int, parent: int = 0) -> bytes:
    """An empty node base of a bank of ``version`` (no effects, properties, states or RTPC), then its RTPC count."""
    out = struct.pack("<BBBII", 0, 0, 0, 0, parent) + b"\0" + b"\0\0"  # head, bit vector, the two property lists
    out += b"\0" + b"\0"  # positioning bits, aux bits
    out += struct.pack("<I", 0) if version >= 135 else b""  # the reflections bus
    out += bytes(6)  # advanced settings
    out += b"\0\0" if version >= 128 else struct.pack("<I", 0)  # states: property count, group count
    return out + struct.pack("<H", 0)


def segment(version: int, children: list[int], duration: float, marks: list[tuple[int, float, bytes]]) -> HircObject:
    body = (
        b"\0" + node_base(version) + struct.pack("<I", len(children)) + b"".join(struct.pack("<I", c) for c in children)
    )
    body += struct.pack("<ddfBBB", 1000.0, 0.0, 120.0, 4, 4, 0) + struct.pack("<I", 0) + struct.pack("<d", duration)
    body += struct.pack("<I", len(marks))
    for ident, at, name in marks:
        body += struct.pack("<IdI", ident, at, len(name)) + name
    return HircObject(bnkmusic.MUSIC_SEGMENT, 7, body)


def track(version: int, media: int) -> HircObject:
    source = struct.pack("<IBI", 0x40001, 2, media)  # plugin, stream type, media id
    source += (struct.pack("<I", 0) if version < 128 else b"") + struct.pack("<IB", 100, 0)  # (file id,) size, flags
    body = b"\0" + struct.pack("<I", 1) + source + struct.pack("<I", 1)
    body += struct.pack("<IIIdddd", 7, media, 0, 0.0, 0.0, 1000.0, 1000.0)
    body += struct.pack("<I", 1) if version >= 128 else b""  # the sub-track count
    body += struct.pack("<I", 0) + node_base(version) + b"\0" + struct.pack("<i", 0)
    return HircObject(bnkmusic.MUSIC_TRACK, 8, body)


@pytest.mark.parametrize("version", [112, 135])
def test_a_segment_a_track_and_a_container_read_to_their_last_byte(version):
    seg = segment(version, [8, 9], 6506.5, [(43573010, 0.0, b""), (1539036744, 6506.5, b"end")])
    read = bnkmusicold.segment(seg, version)
    assert read.children == [8, 9] and read.duration == 6506.5 and read.markers[1] == (1539036744, 6506.5, b"end")
    assert bnkmusicold.children(seg, version) == [8, 9]
    sources = bnkmusicold.track_sources(track(version, 4242), version)
    assert [(s.media, s.stream) for s in sources] == [(4242, 2)]


def test_an_object_that_does_not_end_where_it_should_is_refused():
    seg = segment(135, [8], 1.0, [])
    broken = HircObject(seg.type, seg.id, seg.payload + b"\0")
    with pytest.raises(ValueError, match="unparsed"):
        bnkmusicold.segment(broken, 135)
    with pytest.raises(ValueError, match="is bnkmusic's"):
        bnkmusicold.children(seg, 172)
    with pytest.raises(ValueError, match="no music track"):
        bnkmusicold.track_sources(seg, 135)


def test_an_event_reaching_a_music_track_of_an_old_bank_plays_its_media():
    objects = [segment(135, [8], 2000.0, []), track(135, 4242)]
    seg_obj = objects[0]
    action = HircObject(3, 11, struct.pack("<HI", 0x0400, 7) + bytes(8))  # play, target the segment
    event = HircObject(4, 12, struct.pack("<B", 1) + struct.pack("<I", 11))
    objects += [action, event]
    listing = banks.BankListing(135, 1)
    listing.hirc = {o.id: o for o in objects}
    listing.graph = {}
    listing.events = [12]
    # the segment hangs under no container here: the walk reads its child list through bnkmusicold
    kids = banks._children(listing, seg_obj)
    assert kids == ([8], True)
    assert banks._track_sources(listing, objects[1]) == [(4242, "streamed, prefetched")]
    info = banks.music_tracks(listing)
    assert [(t.id, t.segment, t.seconds) for t in info] == [(8, 7, 2.0)]


# the playlist nodes' fields (docs/sound/bank-objects.md, "Playlist nodes")

LEAF = 0xFFFFFFFF


def assert_a_playlist_tree(nodes) -> None:
    """What every playlist of every installed bank (112, 135, 172) satisfies, the evidence for the fields' meaning."""
    pending = [1]  # pre-order: the first node is the root, each node's children follow it
    for n in nodes:
        assert pending, "a node after the tree is complete"
        pending[-1] -= 1
        while pending and pending[-1] == 0:
            pending.pop()
        if n.num_children:
            pending.append(n.num_children)
        if n.rs_type == LEAF:  # a segment: its own, no children, the flag and the avoid count unused
            assert n.segment_id and not n.num_children and not n.use_weight and not n.avoid_repeat and n.loop in (0, 1)
        else:  # a group: no segment, 0 and 1 are sequences, 2 and 3 random; the avoid count fits its children
            assert not n.segment_id and n.rs_type in (0, 1, 2, 3) and n.use_weight == 1
            assert n.loop in (0, 1) and 1 <= n.avoid_repeat <= max(n.num_children, 1)
        assert n.loop_min == n.loop_max == 0 and 10000 <= n.weight <= 100000
    assert not pending and len({n.item_id for n in nodes}) == len(nodes)


def test_the_playlist_tree_check_accepts_a_tree_and_refuses_a_broken_one():
    group = bnkmusic.PlaylistNode(0, 5, 2, 3, 1, 0, 0, 50000, 1, 1, 0)
    leaf = lambda item, weight: bnkmusic.PlaylistNode(7, item, 0, LEAF, 1, 0, 0, weight, 0, 0, 0)  # noqa: E731
    assert_a_playlist_tree([group, leaf(6, 50000), leaf(8, 30000)])
    with pytest.raises(AssertionError):
        assert_a_playlist_tree([group, leaf(6, 50000)])  # a child missing
    with pytest.raises(AssertionError):
        assert_a_playlist_tree([group, leaf(6, 50000), leaf(6, 50000)])  # an item id twice


# the installs

INSTALLS = {"anno1800": "ANNO1800_DIR", "anno2205": "ANNO2205_DIR"}


@pytest.mark.parametrize("key", INSTALLS)
def test_every_music_object_of_the_installed_banks_reads_and_names_objects_of_its_bank(key, tmp_path):
    folder = os.environ.get(INSTALLS[key])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"{INSTALLS[key]} is not set")
    from annotoolbox.sources.gametree import open_game

    game = open_game(key, Path(folder), tmp_path)
    counts = {"segments": 0, "tracks": 0, "containers": 0}
    leaves: set[int] = set()
    every_id: set[int] = set()
    for path in (p for p in game.paths if p.endswith(".bnk")):
        bank = bnk.parse(game.read(path))
        ids = {o.id for o in bank.hirc}
        every_id |= ids
        for obj in bank.hirc:
            if obj.type == bnkmusic.MUSIC_SEGMENT:
                assert set(bnkmusicold.segment(obj, bank.version).children) <= ids
                counts["segments"] += 1
            elif obj.type == bnkmusic.MUSIC_TRACK:
                bnkmusicold.track_sources(obj, bank.version)
                counts["tracks"] += 1
            elif obj.type in (bnkmusic.MUSIC_SWITCH_CNTR, bnkmusic.MUSIC_RANSEQ_CNTR):
                assert set(bnkmusicold.children(obj, bank.version)) <= ids
                bnkmusicold.rules(obj, bank.version)
                if obj.type == bnkmusic.MUSIC_RANSEQ_CNTR:
                    nodes = bnkmusicold.playlist(obj, bank.version)
                    assert {n.segment_id for n in nodes} <= ids | {0}
                    assert_a_playlist_tree(nodes)
                else:
                    tree = bnkmusicold.switch_tree(obj, bank.version)
                    leaves |= {leaf for _path, leaf, _at in tree.leaves()}  # a leaf may sit in another bank
                counts["containers"] += 1
    assert all(counts.values()), counts
    assert leaves <= every_id | {0}


def container(version: int, kind: int, tail: bytes, rules: bytes = struct.pack("<I", 0)) -> HircObject:
    body = b"\0" + node_base(version) + struct.pack("<I", 1) + struct.pack("<I", 7)
    body += struct.pack("<ddfBBB", 1000.0, 0.0, 120.0, 4, 4, 0) + struct.pack("<I", 0) + rules + tail
    return HircObject(kind, 30, body)


def one_rule(version: int) -> bytes:
    src = struct.pack("<IIII", 1, 0xFFFFFFFF, 1, 0xFFFFFFFF) + struct.pack("<iIiIIB", 0, 4, 0, 7, 0, 255)
    dst = struct.pack("<iIiII", 0, 4, 0, 0, 0) + struct.pack("<HH", 0, 0) + (b"\0\0" if version >= 132 else b"")
    return struct.pack("<I", 1) + src + dst + b"\0"


@pytest.mark.parametrize("version", [112, 135])
def test_a_playlist_a_switch_and_their_rules_read_to_their_last_byte(version):
    node = struct.pack("<IIIIhhhIHBB", 7, 31, 0, 0xFFFFFFFF, 1, 0, 0, 50, 1, 0, 0)
    play = container(version, bnkmusic.MUSIC_RANSEQ_CNTR, struct.pack("<I", 1) + node, one_rule(version))
    nodes = bnkmusicold.playlist(play, version)
    assert [(n.segment_id, n.weight) for n in nodes] == [(7, 50)]
    (rule,) = bnkmusicold.rules(play, version)
    assert (rule.src, rule.sync_type, rule.src_fade_curve) == ([0xFFFFFFFF], 7, 4)
    tree = struct.pack("<IHHHH", 0, 1, 1, 50, 0) + struct.pack("<II", 99, 7) + struct.pack("<I", 0)
    switch_tail = (
        struct.pack("<BI", 1, 1) + struct.pack("<I", 555) + b"\x01" + struct.pack("<I", len(tree)) + b"\0" + tree
    )
    switch = container(version, bnkmusic.MUSIC_SWITCH_CNTR, switch_tail, one_rule(version))
    found = bnkmusicold.switch_tree(switch, version)
    assert found.args == [555] and [(path, leaf) for path, leaf, _at in found.leaves()] == [([99], 7)]
    with pytest.raises(ValueError, match="unparsed"):
        bnkmusicold.playlist(HircObject(play.type, play.id, play.payload + b"\0"), version)
    with pytest.raises(ValueError, match="no playlist"):
        bnkmusicold.playlist(switch, version)
