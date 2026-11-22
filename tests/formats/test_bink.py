"""``formats/bink``: a Bink file's header read from bytes and from a file, and whether a file was written whole."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from annotoolbox.formats import bink


def header_bytes(magic: bytes, frames: int, size: tuple[int, int], rate: int, flags: int = 0, total: int = 0) -> bytes:
    """A 44-byte header: magic, size field, frames, largest frame, frames again, width, height, rate, divisor, flags,
    no sound tracks."""
    return magic + struct.pack("<I", total) + struct.pack("<IIIIIIIII", frames, 1, frames, *size, rate, 1, flags, 0)


def test_the_header_gives_version_frames_size_rate_and_alpha(tmp_path: Path):
    p = tmp_path / "a.bik"
    p.write_bytes(header_bytes(b"BIKi", 750, (1280, 720), 25))
    h = bink.header_of(p)
    assert (h.version, h.frames, h.width, h.height, h.fps, h.alpha, h.tracks) == (1, 750, 1280, 720, 25.0, False, ())
    p.write_bytes(header_bytes(b"KB2i", 240, (1920, 1080), 30, flags=bink.ALPHA_FLAG))
    h = bink.header_of(p)
    assert h.version == 2 and h.alpha and h.fps == 30.0


def test_bytes_that_are_no_bink_file_raise_a_value_error(tmp_path: Path):
    for head in (b"RIFF" + bytes(60), b"BIKi", b""):
        with pytest.raises(bink.CBinkError):
            bink.read_header(head)
    assert issubclass(bink.CBinkError, ValueError)
    p = tmp_path / "x.bik"
    p.write_bytes(b"RIFF" + bytes(60))
    with pytest.raises(ValueError):
        bink.header_of(p)


def test_a_file_is_whole_when_its_size_field_matches_and_only_for_the_asked_versions(tmp_path: Path):
    p = tmp_path / "w.bk2"
    body = b"video"
    p.write_bytes(header_bytes(b"KB2i", 1, (2, 2), 25, total=44 - 8 + len(body)) + body)
    assert bink.is_whole(p) and bink.is_whole(p, (2,)) and not bink.is_whole(p, (1,))
    p.write_bytes(p.read_bytes()[:-1])
    assert not bink.is_whole(p) and bink.is_whole(p, (2,), by_size=False)
    assert not bink.is_whole(tmp_path / "missing.bk2")


def test_a_header_cut_after_the_rate_reads_without_flags_and_tracks():
    h = bink.read_header(header_bytes(b"BIKi", 5, (4, 2), 25, flags=bink.ALPHA_FLAG)[: bink.CORE_SIZE])
    assert (h.frames, h.width, h.alpha, h.tracks) == (5, 4, False, ())
