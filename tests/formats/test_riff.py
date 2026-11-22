"""``formats.riff``: chunks read the way the games' sound files need, and files built from chunks."""

import struct

import pytest

from annotoolbox.formats import riff


def test_a_file_built_from_chunks_reads_back_with_the_odd_chunk_padded():
    data = riff.build([(b"odd ", b"abc"), (b"fmt ", b"F" * 16), (b"data", b"1234")])
    assert data[4:8] == struct.pack("<I", len(data) - 8)
    assert riff.chunks(data) == {b"odd ": b"abc", b"fmt ": b"F" * 16, b"data": b"1234"}
    assert data[12 + 8 : 12 + 8 + 4] == b"abc\0"


def test_the_first_chunk_of_a_tag_wins():
    data = riff.build([(b"fmt ", b"first"), (b"fmt ", b"second"), (b"data", b"x")])
    assert riff.chunks(data)[b"fmt "] == b"first"


def test_a_chunk_whose_size_runs_past_the_end_is_cut_there():
    data = riff.build([(b"fmt ", b"F" * 16)]) + b"data" + struct.pack("<I", 0xFFFFFFFF) + b"abc"  # a streamed WAV
    assert riff.chunks(data)[b"data"] == b"abc"


def test_anything_but_a_riff_of_the_form_is_refused():
    with pytest.raises(ValueError, match="RIFF/WAVE"):
        riff.chunks(b"OggS" + bytes(40))
    with pytest.raises(ValueError, match="RIFF/AVI"):
        riff.chunks(riff.build([(b"fmt ", b"F" * 16)]), form=b"AVI ")


def test_wav_bytes_states_the_format_of_its_samples():
    wav = riff.wav_bytes(bytes(8), 22050, 2)
    fmt = riff.chunks(wav)[b"fmt "]
    assert struct.unpack("<HHIIHH", fmt) == (1, 2, 22050, 22050 * 4, 4, 16)
    assert riff.chunks(riff.wav_bytes(bytes(6), 8000, 1, 24))[b"fmt "][14:16] == struct.pack("<H", 24)
