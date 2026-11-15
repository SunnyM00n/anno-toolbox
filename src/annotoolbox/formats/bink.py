"""The header of a Bink video file (``.bik`` Bink 1, ``.bk2`` Bink 2): its version, size, frame rate and sound tracks.

``read_header`` reads the first bytes, ``header_of`` a file's, ``is_whole`` says whether a file was written to its end
(its size field matches the file, which a file cut short by a stopped encoder does not). Standard library only; the
decoding is ``infra.binkdll``'s, the encoding ``infra.radvideo``'s.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

CORE_SIZE = 36  # the bytes up to the frame rate: what a header must hold
HEADER_SIZE = 44  # with the video flags and the sound track count
HEAD_BYTES = HEADER_SIZE + 12 * 32  # the header with room for 32 sound tracks
MAGIC = {1: b"BIK", 2: b"KB2"}  # the first three bytes by version; a revision letter follows
ALPHA_FLAG = 0x00100000  # a video flag of the header (BINKALPHA)
AUDIO_STEREO = 0x2000  # sound flags of a track (0x1000: DCT; the PCM is 16 bit)


class BinkError(ValueError):
    """The bytes are no Bink file (or, for ``infra.binkdll``, a movie cannot be decoded)."""


@dataclass(frozen=True)
class AudioTrack:
    sample_rate: int
    channels: int
    track_id: int


@dataclass(frozen=True)
class Header:
    """The fields of a Bink file's header (the same in Bink 1 and 2)."""

    version: int  # 1 or 2
    frames: int
    width: int
    height: int
    fps: float
    alpha: bool
    tracks: tuple[AudioTrack, ...]


def read_header(head: bytes) -> Header:
    """The header of a Bink file's first bytes: magic ``BIK``/``KB2`` + a revision letter, file size, frame count
    (offset 8), largest frame, frames again, width, height, rate and divisor, video flags (36), the sound track count
    (40), a maximum size per track, then per track its sample rate and flags and (after them all) its id. A header
    cut after the rate has no flags and no tracks."""
    if len(head) < CORE_SIZE or head[:3] not in MAGIC.values():
        raise BinkError("not a Bink file")
    frames, _largest, _again, width, height, rate, scale = struct.unpack_from("<7I", head, 8)
    flags, count = struct.unpack_from("<2I", head, CORE_SIZE) if len(head) >= HEADER_SIZE else (0, 0)
    tracks: list[AudioTrack] = []
    if 0 < count <= 32 and len(head) >= HEADER_SIZE + 12 * count:
        base = HEADER_SIZE + 4 * count
        ids = struct.unpack_from(f"<{count}I", head, base + 4 * count)
        for i in range(count):
            hz, kind = struct.unpack_from("<HH", head, base + 4 * i)
            tracks.append(AudioTrack(hz, 2 if kind & AUDIO_STEREO else 1, ids[i]))
    version = 1 if head[:3] == MAGIC[1] else 2
    return Header(
        version, frames, width, height, rate / scale if scale else 0.0, bool(flags & ALPHA_FLAG), tuple(tracks)
    )


def header_of(path: Path) -> Header:
    """The header of a Bink file; ``BinkError`` when it is none, ``OSError`` when it cannot be read."""
    with open(path, "rb") as f:
        return read_header(f.read(HEAD_BYTES))


def is_whole(path: Path, versions: tuple[int, ...] = (1, 2), by_size: bool = True) -> bool:
    """Whether ``path`` is a Bink file of one of ``versions`` that was written to the end: its header's size field (the
    file size less 8) matches the file. ``by_size=False`` asks for the magic alone."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        if head[:3] not in (MAGIC[v] for v in versions) or len(head) != 8:
            return False
        return not by_size or struct.unpack_from("<I", head, 4)[0] + 8 == Path(path).stat().st_size
    except OSError:
        return False
