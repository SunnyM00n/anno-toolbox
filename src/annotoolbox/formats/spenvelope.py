"""The ``.sp`` file of the old games (2070, 1404, 1701): the loudness envelope of one spoken line, which the game
reads to move a speaker's mouth.

Layout (read from every ``.sp`` of the three installs): ``u32 count``, ``count`` loudness bytes (0 silent, 255
loudest), ``u32 interval`` (ms between two bytes, 50 in all files) and a ``u32`` that is 0. ``read(data)`` gives an
``Envelope``, ``Envelope.to_bytes()`` the file again; ``Envelope.from_samples`` makes one for a replaced clip.
A line of ``n`` bytes is about ``n * interval`` milliseconds long (``docs/sounds.md``, "What 2070 has").
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

INTERVAL_MS = 50  # the shipped files' step


@dataclass
class Envelope:
    levels: bytes
    interval_ms: int = INTERVAL_MS
    spare: int = 0  # the last u32 (0 in every shipped file)

    @property
    def seconds(self) -> float:
        return len(self.levels) * self.interval_ms / 1000

    def to_bytes(self) -> bytes:
        return struct.pack("<I", len(self.levels)) + self.levels + struct.pack("<II", self.interval_ms, self.spare)

    @classmethod
    def from_samples(cls, samples, rate: int, interval_ms: int = INTERVAL_MS) -> Envelope:
        """An envelope from mono PCM ``samples`` (signed 16 bit values) at ``rate`` Hz: per step the loudest sample
        scaled to 0..255 against the clip's own peak, so a quiet clip still moves the mouth."""
        step = max(1, rate * interval_ms // 1000)
        peaks = [max((abs(s) for s in samples[i : i + step]), default=0) for i in range(0, len(samples), step)]
        top = max(peaks, default=0) or 1
        return cls(bytes(min(255, p * 255 // top) for p in peaks), interval_ms)


def read(data: bytes) -> Envelope:
    """The envelope of a ``.sp`` file; ValueError when the size does not fit its count."""
    if len(data) < 12:
        raise ValueError("not a speech envelope: shorter than its 12 byte frame")
    (count,) = struct.unpack_from("<I", data)
    if len(data) != count + 12:
        raise ValueError(f"not a speech envelope: {count} levels need {count + 12} bytes, the file has {len(data)}")
    interval, spare = struct.unpack_from("<II", data, 4 + count)
    return Envelope(data[4 : 4 + count], interval, spare)
