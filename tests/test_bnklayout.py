"""``formats/bnkgraph``: the layout record per bank family, a node base's parent and property lists."""

from __future__ import annotations

import struct

import pytest
from tests.test_bnkgraph import MID, NEW, OLD, container, event, node_base, sound

from annotoolbox.formats import bnkgraph
from annotoolbox.formats.bnkmusic import Reader


def test_each_family_has_one_record_and_117_alone_has_a_known_container_layout():
    assert {v: bnkgraph.layout(v).family for v in (OLD, MID, NEW)} == {OLD: "old", MID: "mid", NEW: "new"}
    new = bnkgraph.layout(NEW)
    assert new.child_gap == {bnkgraph.RANSEQ: 24, bnkgraph.SWITCH: 10, bnkgraph.MIXER: 0, bnkgraph.LAYER: 0}
    assert all(gap is None for v in (OLD, MID) for gap in bnkgraph.layout(v).child_gap.values())
    assert (new.prop_ids["pitch"], bnkgraph.layout(MID).prop_ids["pitch"]) == (1, 2)
    assert bnkgraph.layout(OLD).prop_names[3] == "low-pass filter" and new.prop_names[3] == "high-pass filter"
    assert not bnkgraph.layout(OLD).stream0_embedded and bnkgraph.layout(MID).stream0_embedded
    assert (new.media_high_word, new.prefetch, new.vorbis_known, new.music_readable) == (True,) * 4
    assert not bnkgraph.layout(MID).media_high_word
    assert all(bnkgraph.layout(v).vorbis_known for v in (OLD, MID, NEW))  # the Wwise Vorbis layout is the same in all
    assert [bnkgraph.layout(v).vorbis_channels for v in (OLD, MID, NEW)] == [
        {1: 4, 2: 3}, {1: 0x4101, 2: 0x3102}, {1: 0x4101, 2: 0x3102},
    ]  # fmt: skip


@pytest.mark.parametrize("version", (OLD, MID, NEW))
def test_the_action_count_of_an_event_reads_by_the_layout(version):
    assert bnkgraph.read_event(event(version, 5, [11, 22, 33]), version) == [11, 22, 33]


@pytest.mark.parametrize("version", (OLD, MID, NEW))
def test_a_nodes_parent_is_read_and_changed_in_every_layout(version):
    cont = container(version, bnkgraph.RANSEQ, 7, 40, [1, 2])
    snd = sound(version, 8, 100, 40)
    assert bnkgraph.parent_of(cont, version) == 40 and bnkgraph.parent_of(snd, version) == 40
    moved = bnkgraph.with_parent(snd, version, 99)
    assert bnkgraph.parent_of(moved, version) == 99
    assert moved.payload[:4] == snd.payload[:4] and len(moved.payload) == len(snd.payload)


def test_an_object_without_a_node_base_is_refused():
    with pytest.raises(ValueError, match="no node base"):
        bnkgraph.parent_of(event(NEW, 5, [1]), NEW)


def test_the_property_lists_of_a_node_base_read_as_ids_and_values():
    lists = bytes([2, 0, 1]) + struct.pack("<ff", 1.0, 2.0) + bytes([1, 1]) + struct.pack("<ff", -1.0, 1.0)
    props, ranged = bnkgraph.read_prop_lists(Reader(lists))
    assert props == {0: struct.pack("<f", 1.0), 1: struct.pack("<f", 2.0)}
    assert ranged == {1: struct.pack("<ff", -1.0, 1.0)}


def test_a_property_listed_twice_means_the_head_was_not_read_right():
    with pytest.raises(ValueError, match="twice"):
        bnkgraph.read_prop_lists(Reader(bytes([2, 3, 3]) + bytes(8) + bytes([0])))


def test_a_node_base_head_with_effects_reads_up_to_its_parent():
    r = Reader(node_base(NEW, 321, fx=True))
    assert bnkgraph.skip_head(r, bnkgraph.NEW) == 321


def test_the_state_manager_and_node_base_facts_are_on_the_record_and_the_attenuation_id_is_one_name():
    assert [(bnkgraph.layout(v).stmg_head, bnkgraph.layout(v).stmg_tail) for v in (OLD, MID, NEW)] == [
        (6, 0), (8, 4), (14, 4),
    ]  # fmt: skip
    from annotoolbox.formats import bnksfx

    assert bnkgraph.ATTENUATION == bnksfx.ATTENUATION == 14 and bnksfx.SOUND == bnkgraph.SOUND
