"""Wwise Vorbis written for the banks of 1800 and 2205 (and 117): the file's header per bank version, the clip stored in a
bank of each layout. Needs ffmpeg with libvorbis and the codebook file (``tools/``), else skipped; the part against the
installed games' own Vorbis files needs their ``*_DIR`` variables."""

from __future__ import annotations

import array
import math
import struct

import pytest
from tests.soundedit.soundeditfakes import BANK_VERSIONS as VERSIONS
from tests.soundedit.soundeditfakes import doc_of, installed_banks

from annotoolbox.formats import bnk, bnkgraph, riff, wwvorbis
from annotoolbox.soundedit import audioedits, clipcodec
from annotoolbox.soundedit.bankdoc import CBankDoc

needs_tools = pytest.mark.skipif(
    bool(clipcodec.availability(172)["vorbis"]), reason="needs ffmpeg and the codebook file"
)


def tone(seconds: float = 0.5, channels: int = 1) -> clipcodec.SClip:
    n = round(48000 * seconds)
    mono = [round(9000 * math.sin(i / 7)) for i in range(n)]
    return clipcodec.SClip(array.array("h", [v for s in mono for v in (s,) * channels]), 48000, channels)


def relations(wem: bytes) -> dict[str, bool]:
    """What every shipped Wwise Vorbis file of 1800 and 2205 satisfies (read from 10,000 of them): the setup packet
    sits after the seek table, the audio after the setup, the sizes add up."""
    chunks = riff.chunks(wem)
    fmt, data = chunks[b"fmt "], chunks[b"data"]
    (frames, setup_size, size, _loop, _extra, seek, audio_at, *_rest) = struct.unpack_from("<IIIHHIIHHIIIBB", fmt, 24)
    return {
        "fmt of 0x42 bytes": len(fmt) == 66 and fmt[:2] == b"\xff\xff",
        "audio after the setup": audio_at - seek == setup_size,
        "size without the seek table": size == len(data) - seek,
        "the setup packet is sized": struct.unpack_from("<H", data, seek)[0] + 2 == setup_size,
        "frames": frames > 0,
    }


@needs_tools
@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("channels", (1, 2))
def test_a_vorbis_clip_has_the_channel_word_of_its_bank_version_and_decodes(version, channels):
    clip = tone(0.5, channels)
    wem = clipcodec.encode(clip, "vorbis", version)
    fmt = riff.chunks(wem)[b"fmt "]
    assert struct.unpack_from("<I", fmt, 20)[0] == bnkgraph.layout(version).vorbis_channels[channels]
    assert all(relations(wem).values()), relations(wem)
    read = clipcodec.read_wem(wem)
    assert (read.channels, read.rate) == (channels, 48000) and read.seconds == pytest.approx(0.5, abs=0.06)
    assert max(read.samples) > 5000


@needs_tools
@pytest.mark.parametrize("version", (112, 135))
def test_an_older_banks_sound_takes_a_vorbis_clip_where_it_keeps_its_audio(version):
    doc = doc_of(version)
    wem = clipcodec.encode(tone(0.3), "vorbis", version)
    doc.do("replace_audio", sound=11, wem=wem, media=audioedits.plan_media(doc, 11))
    node = doc.graph[11]
    assert node.source.plugin == bnk.VORBIS_PLUGIN
    if bnkgraph.layout(version).stream0_embedded:  # 1800: in the bank's own media
        assert doc.media(node.source.media) == wem and node.source.size == len(wem)
    else:  # 2205: the bank keeps no clip, the whole file is loose and streamed
        assert doc.m_loose[node.source.media].data == wem and node.source.stream == 2
    again = CBankDoc(doc.to_bytes())
    assert again.graph[11].source.plugin == bnk.VORBIS_PLUGIN


@needs_tools
@pytest.mark.parametrize("game", ("anno1800", "anno2205"))
def test_the_header_of_a_written_file_follows_the_games_own_vorbis_files(game):
    version = 135 if game == "anno1800" else 112
    shipped = {}
    for _path, data in installed_banks(game):
        bank = bnk.parse(data)
        blob = next((c.data for c in bank.chunks if c.tag == b"DATA"), b"")
        for m in bank.media:
            wem = blob[m.offset : m.offset + m.size]
            try:
                fmt = riff.chunks(wem)[b"fmt "]
            except (ValueError, KeyError):
                continue
            if len(fmt) == 66 and fmt[:2] == b"\xff\xff":
                shipped.setdefault(struct.unpack_from("<H", fmt, 2)[0], wem)
        if len(shipped) == 2:
            break
    assert shipped
    for channels, wem in shipped.items():
        assert all(relations(wem).values()), (game, relations(wem))  # what the check holds the written file to
        ours = clipcodec.encode(tone(0.4, channels), "vorbis", version)
        theirs_fmt, our_fmt = riff.chunks(wem)[b"fmt "], riff.chunks(ours)[b"fmt "]
        assert our_fmt[:2] == theirs_fmt[:2] and our_fmt[18:24] == theirs_fmt[18:24]  # tag, cbSize, the channel word
        assert our_fmt[-2:] == theirs_fmt[-2:]  # the block sizes: 2^8 and 2^11 in both
        assert all(relations(ours).values())


def test_a_lattice_codebook_without_dimensions_is_a_wem_error():
    w = wwvorbis.CBitWriter()
    for value, bits in ((0x564342, 24), (0, 16), (0, 24), (0, 1), (0, 1), (1, 4)):  # sync, dims 0, no entries, lookup 1
        w.write(value, bits)
    with pytest.raises(wwvorbis.CWemError):
        wwvorbis.read_std_book(wwvorbis.CBitReader(w.tobytes() + bytes(16)))
