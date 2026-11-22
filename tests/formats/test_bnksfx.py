"""bnksfx: sound-effect objects cloned from plain donors, assembled into a bank of our own."""

import struct

import pytest
from tests.formats.formatfakes import ATTENUATION, donor_bank

from annotoolbox.formats import bnk, bnksfx
from annotoolbox.formats import bnkmusic as bm


def test_node_base_round_trip_and_donors_found_by_shape():
    donor = donor_bank()
    for o in donor.hirc:
        if o.type in (bnksfx.SOUND, bnksfx.RANSEQ, bnksfx.MIXER):
            pos = 18 if o.type == bnksfx.SOUND else 0
            nb, end = bnksfx.parse_node_base(o.payload, pos)
            assert nb.render() == o.payload[pos:end]
    d = bnksfx.SDonors.find(donor)
    assert d.mixer_attenuation_id == 100 and d.mixer.m_bus == 1213198520
    assert d.attenuation == ATTENUATION


def test_an_attenuation_becomes_a_straight_gain_fall_to_its_reach():
    assert bnksfx.attenuation_radius(ATTENUATION) == 20.0
    line = bnksfx.linear_attenuation(ATTENUATION, 60.0)
    assert bnksfx.attenuation_radius(line) == 60.0
    at, count = bnksfx._attenuation_curves(line)[1][0]
    assert count == 2
    assert [struct.unpack_from("<ff", line, at + 12 * k) for k in range(count)] == [(0.0, 0.0), (60.0, bnksfx.SILENT)]
    with pytest.raises(bnksfx.CDonorError):
        bnksfx.attenuation_radius(b"not an attenuation")


def test_volume_curves_replace_the_game_parameter_block_of_a_node():
    d = bnksfx.SDonors.find(donor_bank())
    curve = bnksfx.SCurve(0xE1C08858, 77, ((-1.0, 0.0), (0.0, bnksfx.SILENT)))
    obj = bnksfx.container(d, 9, 200, [(300, 1000)], bnksfx.SContainer(), [curve])
    nb, end = bnksfx.parse_node_base(obj.payload)
    at = bnksfx._rtpc_offset(nb.m_tail)
    n, rtpc_id, kind, accum, param, curve_id, scaling, points = struct.unpack_from("<HIBBBIBH", nb.m_tail, at)
    assert (n, rtpc_id, kind, accum, param, curve_id, scaling, points) == (1, 0xE1C08858, 0, 2, 0, 77, 2, 2)
    assert struct.unpack_from("<ffI", nb.m_tail, at + 16) == (-1.0, 0.0, 4)
    assert end == len(nb.render()) and bm.skip_node_base(bm.CReader(obj.payload)) == nb.render()


def test_donor_missing_is_reported():
    bank = donor_bank()
    bank.hirc = [o for o in bank.hirc if o.type != bnksfx.MIXER]
    with pytest.raises(bnksfx.CDonorError, match="ActorMixer"):
        bnksfx.SDonors.find(bank)


def test_sound_carries_source_volume_loop_and_random_pitch():
    d = bnksfx.SDonors.find(donor_bank())
    obj = bnksfx.sound(
        d.sound, 900, 800, bnksfx.SSoundSpec(77, 100_000, volume_db=-6.0, pitch=(-25.0, 10.0), is_looping=True)
    )
    plugin, stream, source, mem, bits = struct.unpack_from("<IBQIB", obj.payload, 0)
    assert (plugin, stream, source & 0xFFFFFFFF, source >> 32, mem) == (bnk.PCM_PLUGIN, 2, 77, 900, bnksfx.PREFETCH)
    nb, end = bnksfx.parse_node_base(obj.payload, 18)
    assert end == len(obj.payload) and nb.m_parent == 800
    assert struct.unpack("<f", nb.m_props[bnksfx.PROP_VOLUME])[0] == -6.0
    assert nb.m_props[bnksfx.PROP_LOOP] == bytes(4)  # endless
    assert struct.unpack("<ff", nb.m_ranged[bnksfx.PROP_PITCH]) == (-25.0, 10.0)
    limited = bnksfx.sound(d.sound, 902, 800, bnksfx.SSoundSpec(77, 100, max_count=1))
    nb, _ = bnksfx.parse_node_base(limited.payload, 18)
    assert (
        bnksfx.get_limit(nb) == bnksfx.SLimit(1)
        and struct.unpack_from("<BBHB", nb.m_tail, bnksfx._adv_offset(nb.m_tail))[0] == 3
    )
    quiet = bnksfx.sound(d.sound, 901, 800, bnksfx.SSoundSpec(77, 100))
    nb, _ = bnksfx.parse_node_base(quiet.payload, 18)
    assert nb.m_props == {} and nb.m_ranged == {}  # a one-shot at full volume sets nothing
    assert struct.unpack_from("<I", quiet.payload, 13)[0] == 100  # a tiny clip is not "prefetched" past its end


def test_container_loops_and_pauses():
    d = bnksfx.SDonors.find(donor_bank())
    once = bnksfx.container(d, 1, 2, [(10, 50000), (11, 25000)], bnksfx.SContainer())
    nb, end = bnksfx.parse_node_base(once.payload)
    assert nb.m_parent == 2 and nb.m_ranged == {}  # the donor's ranged property is not kept
    params = struct.unpack_from("<HHHfffHBBBB", once.payload, end)
    assert params[0] == 1 and params[7] == 0 and params[10] == 18  # plays once, no delay
    rest = once.payload[end + 24 :]
    assert struct.unpack_from("<III", rest, 0) == (2, 10, 11)
    assert struct.unpack_from("<H", rest, 12)[0] == 2 and struct.unpack_from("<IIII", rest, 14) == (
        10,
        50000,
        11,
        25000,
    )
    wait = bnksfx.container(d, 1, 2, [(10, 1000)], bnksfx.SContainer(0, 4000, 10000))
    p = struct.unpack_from("<HHHfffHBBBB", wait.payload, end)
    assert p[0] == 0 and (p[3], p[4], p[5]) == (7000.0, -3000.0, 3000.0) and p[7] == 3  # between 4 s and 10 s


def test_actions_and_events_follow_the_shipped_layout():
    play = bnksfx.play_action(5, 400, 0xAB)
    assert struct.unpack_from("<HIB", play.payload) == (0x0403, 400, 0) and len(play.payload) == 7 + 2 + 9
    assert struct.unpack_from("<BII", play.payload, 9) == (4, 0xAB, 0)
    stop = bnksfx.stop_action(6, 400, 1500)
    assert stop.payload == struct.pack("<HIB", 0x0103, 400, 0) + b"\1;" + struct.pack("<I", 1500) + b"\0\7\6\0"
    ev = bnksfx.event(9, [5, 6])
    assert ev.payload == bytes(7) + b"\2" + struct.pack("<II", 5, 6)
    assert bnksfx.event_actions(ev) == [5, 6] and bnksfx.action_target(play) == 400


def test_new_bank_orders_children_first_and_keeps_the_header_form():
    donor = donor_bank()
    d = bnksfx.SDonors.find(donor)
    objs = [
        bnksfx.event(30, [20]),
        bnksfx.play_action(20, 12, 4242),
        bnksfx.mixer(d, 13, 11, [12]),
        bnksfx.sound(d.sound, 12, 13, bnksfx.SSoundSpec(1, 10)),
        bnksfx.attenuation(d, 11),
    ]
    bank = bnksfx.new_bank(donor, "BNK_TEST", 4242, objs)
    assert [o.type_name for o in bank.hirc] == ["Attenuation", "Sound", "ActorMixer", "Action", "Event"]
    again = bnk.parse(bnk.write(bank))
    assert again.bank_id == 4242 and again.version == 172
    assert struct.unpack_from("<I", again.header.data, 8)[0] == bnksfx.BKHD_LANGUAGE
    assert len(again.header.data) == len(donor.header.data) and [o.id for o in again.hirc] == [11, 12, 13, 20, 30]
    mixer = again.find(13)
    nb, end = bnksfx.parse_node_base(mixer.payload)
    assert nb.m_parent == 0 and struct.unpack("<I", nb.m_props[bnksfx.PROP_ATTENUATION])[0] == 11
    assert bm.CReader(mixer.payload[end:]).u32() == 1


def test_append_to_a_shipped_bank_keeps_its_order_and_skips_known_ids():
    donor = donor_bank()
    d = bnksfx.SDonors.find(donor)
    shipped = bnk.SSoundBank(
        chunks=[bnk.SChunk(b"BKHD", donor.header.data), bnk.SChunk(b"HIRC", b"")],
        hirc=[
            bnk.SHircObject(bnksfx.SOUND, 1, b"s"),
            bnk.SHircObject(bnksfx.ACTION, 2, b"a"),
            bnk.SHircObject(bnksfx.EVENT, 3, b"e"),
        ],
    )
    mine = [
        bnksfx.event(30, [20]),
        bnksfx.play_action(20, 12, 7),
        bnksfx.sound(d.sound, 12, 0, bnksfx.SSoundSpec(1, 10)),
        bnksfx.event(3, []),
    ]
    assert bnksfx.append_to(shipped, mine) == 3  # id 3 exists already
    assert [o.id for o in shipped.hirc] == [1, 12, 2, 3, 20, 30]


def test_mixer_limit_sets_instances_kill_newest_and_virtual_voices():
    d = bnksfx.SDonors.find(donor_bank())
    plain = bnksfx.mixer(d, 1, 2, [3])
    limited = bnksfx.mixer(d, 1, 2, [3], bnksfx.SLimit(30, 2))
    nb, end = bnksfx.parse_node_base(limited.payload)
    at = bnksfx._adv_offset(nb.m_tail)
    bits, queue, instances, below = struct.unpack_from("<BBHB", nb.m_tail, at)
    assert (bits, queue, instances, below) == (7, 1, 30, 2)  # kill newest, virtual, ignores the parent's limit
    assert nb.m_bits & bnksfx.PRIORITY_BY_DISTANCE and bm.CReader(limited.payload[end:]).u32() == 1
    base, _ = bnksfx.parse_node_base(plain.payload)
    assert (
        base.m_bits & bnksfx.PRIORITY_BY_DISTANCE == 0 and nb.m_tail[:at] == base.m_tail[:at]
    )  # only the limit differs
    assert nb.m_tail[at + 5 :] == base.m_tail[at + 5 :]
    oldest = bnksfx.mixer(d, 1, 2, [3], bnksfx.SLimit(10, 1, kill_newest=False))
    nb, _ = bnksfx.parse_node_base(oldest.payload)
    assert struct.unpack_from("<BBHB", nb.m_tail, at) == (6, 1, 10, 1)
    assert bnksfx.get_limit(nb) == bnksfx.SLimit(10, 1, kill_newest=False)
    assert bnksfx.get_limit(bnksfx.parse_node_base(limited.payload)[0]) == bnksfx.SLimit(30, 2)


def test_a_mixer_cloned_from_a_shipped_one_keeps_its_bus_and_takes_children_and_a_bus_override():
    d = bnksfx.SDonors.find(donor_bank())
    base = d.mixer
    obj = bnksfx.clone_mixer(base, 5, [7, 8], bus=99)
    nb, end = bnksfx.parse_node_base(obj.payload)
    assert (nb.m_bus, nb.m_parent, nb.m_tail, nb.m_props) == (99, 0, base.m_tail, base.m_props)
    assert struct.unpack_from("<3I", obj.payload, end) == (2, 7, 8)
    assert bnksfx.parse_node_base(bnksfx.clone_mixer(base, 5, [], parent=3).payload)[0].m_bus == base.m_bus


def test_a_language_bank_header_carries_the_folder_id_and_a_project_hash_of_its_name():
    donor = donor_bank()
    a = bnksfx.header(donor, "BNK_A", 10, bnk.fnv1_32("de_de"))
    assert struct.unpack_from("<III", a, 0) == (172, 10, bnk.fnv1_32("de_de")) and len(a) == 40
    assert a[24:] != bnksfx.header(donor, "BNK_B", 10, bnk.fnv1_32("de_de"))[24:]
    assert struct.unpack_from("<I", bnksfx.header(donor, "BNK_A", 10), 8)[0] == bnksfx.BKHD_LANGUAGE


def test_sound_in_bank_names_the_codec_and_holds_the_whole_clip():
    d = bnksfx.SDonors.find(donor_bank())
    obj = bnksfx.sound(d.sound, 900, 800, bnksfx.SSoundSpec(77, 40_000, plugin=bnk.VORBIS_PLUGIN, is_in_bank=True))
    plugin, kind, source, held, _bits = struct.unpack_from("<IBQIB", obj.payload, 0)
    assert (plugin, kind, source & 0xFFFFFFFF, held) == (bnk.VORBIS_PLUGIN, bnksfx.IN_MEMORY, 77, 40_000)
    nb, end = bnksfx.parse_node_base(obj.payload, 18)
    assert end == len(obj.payload) and nb.m_parent == 800
    voice = bnksfx.sound(
        d.sound, 901, 800, bnksfx.SSoundSpec(78, 100, plugin=bnk.VORBIS_PLUGIN, is_in_bank=True, flags=1)
    )
    assert struct.unpack_from("<IBQIB", voice.payload, 0)[4] == 1  # language-specific, as the shipped voices


def test_new_bank_with_media_aligns_the_files_and_round_trips():
    donor = donor_bank()
    d = bnksfx.SDonors.find(donor)
    files = {5: b"a" * 21, 6: b"b" * 16, 7: b"c"}
    bank = bnksfx.new_bank(
        donor, "BNK_TEST", 4242, [bnksfx.sound(d.sound, 12, 0, bnksfx.SSoundSpec(5, 21, is_in_bank=True))], files
    )
    assert [c.tag for c in bank.chunks] == [b"BKHD", b"DIDX", b"DATA", b"HIRC"]
    again = bnk.parse(bnk.write(bank))
    data = next(c.data for c in again.chunks if c.tag == b"DATA")
    assert [(m.id, m.offset % bnksfx.MEDIA_ALIGN, m.size) for m in again.media] == [(5, 0, 21), (6, 0, 16), (7, 0, 1)]
    for m in again.media:
        assert data[m.offset : m.offset + m.size] == files[m.id]
