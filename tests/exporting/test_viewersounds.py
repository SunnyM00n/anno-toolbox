"""The viewer's sounds without Qt: Wwise ADPCM decoded, an event walked to the sounds its containers hold, a bank's music
tracks with their segment; synthetic banks of version 172 (Anno 117's layout)."""

from __future__ import annotations

import struct

import pytest
from tests.exporting.exportingfakes import NODE_BASE, adpcm_wem

from annotoolbox.exporting import banks
from annotoolbox.formats import bnk, bnksfx, wemadpcm


def test_wwise_adpcm_plays_as_ima_steps_from_its_block_header():
    clip = adpcm_wem(bytes([0x70]))  # low nibble 0, then 7
    samples, rate, channels = wemadpcm.read(clip)
    assert (rate, channels, len(samples)) == (48000, 1, 64)
    assert samples[:3].tolist() == [1000, 1011, 1013]  # 7 adds 1 + 3 + 7 at step 7; the next step is 16
    assert banks.wave_info(clip)["seconds"] == pytest.approx(64 / 48000)
    wav, suffix = banks.playable(clip, "x.wem")
    assert suffix == ".wav" and len(wav) == 44 + 128


def sound(oid: int, media: int) -> bnk.SHircObject:
    return bnk.SHircObject(2, oid, struct.pack("<IBI", bnk.PCM_PLUGIN, 0, media) + bytes(9) + NODE_BASE)


def bank_bytes() -> bytes:
    """Event 1 plays random container 5, which holds sounds 11 and 12; event 2 stops it; event 3 plays music track 31
    of segment 30 (40 s)."""
    ranseq = bnk.SHircObject(5, 5, NODE_BASE + bytes(24) + struct.pack("<III", 2, 11, 12))
    source = struct.pack("<IBQIB", bnk.PCM_PLUGIN, 1, 77, 0, 0)
    track = bnk.SHircObject(
        11, 31, struct.pack("<I", 1) + source + b"\0" + struct.pack("<II", 0, 0) + NODE_BASE + b"\0" + bytes(4)
    )
    meter = struct.pack("<ddfBBB", 1000.0, 0.0, 120.0, 4, 4, 0)
    segment = bnk.SHircObject(
        10, 30, b"\0" + NODE_BASE + struct.pack("<II", 1, 31) + meter + bytes(4) + struct.pack("<dI", 40000.0, 0)
    )
    objects = [
        sound(11, 55), sound(12, 66), ranseq, track, segment,
        bnksfx.play_action(21, 5, 9), bnksfx.stop_action(22, 5), bnksfx.play_action(23, 31, 9),
        bnksfx.event(1, [21]), bnksfx.event(2, [22]), bnksfx.event(3, [23]),
    ]  # fmt: skip
    head = bnk.SChunk(b"BKHD", struct.pack("<III", 172, 9, 0))
    bank = bnk.SSoundBank([head, bnk.SChunk(b"HIRC", b"")])
    bank.hirc = objects
    return bnk.write(bank)


def test_an_event_is_walked_through_its_actions_and_containers_to_its_sounds():
    listing = banks.bank_listing(bank_bytes())
    assert sorted(listing.events) == [1, 2, 3]
    found, exact = banks.event_sounds(listing, 1)
    assert [s.media for s in found] == [55, 66] and exact
    assert banks.event_sounds(listing, 2) == ([], True)  # a stop action plays nothing
    assert banks.event_sounds(listing, 3) == ([77], True)  # a music track's media


def test_a_banks_music_tracks_name_their_sources_and_their_segment():
    (track,) = banks.music_tracks(banks.bank_listing(bank_bytes()))
    assert track.id == 31 and track.sources == ((77, "streamed"),) and (track.segment, track.seconds) == (30, 40.0)
