"""The ``.sp`` lip file beside a speech file of 1404 and 1701 (docs/text-editor.md, "Lip files"). No Qt.

``SLips.read(data)``: a 32-bit count ``n``, then ``n + 8`` bytes and four zero bytes; the bytes in between are read as one
mouth-opening value per frame of the clip. ``mp3_seconds(data)`` walks the MPEG frames of the clip so the frame rate
can be told (the game's own rate is not known: the frames are spread over the clip).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

_BITRATES = {  # layer III: MPEG-1, MPEG-2 and 2.5, in kbit/s
    1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0),
}
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}  # by the version bits


@dataclass(frozen=True)
class SLips:
    """The frames of one ``.sp`` file: ``levels`` are 0 to 255."""

    levels: tuple[int, ...]

    @classmethod
    def read(cls, data: bytes) -> SLips | None:
        """The file's frames, None for bytes that are not such a file."""
        if len(data) < 12:
            return None
        (count,) = struct.unpack_from("<I", data)
        if len(data) != count + 12:
            return None
        return cls(tuple(data[4:-4]))

    @property
    def peak(self) -> int:
        return max(self.levels, default=0)

    def bars(self, width: int) -> str:
        """The levels averaged into ``width`` characters of a bar chart (eight heights), for a one-line display."""
        if not self.levels or width < 1:
            return ""
        step, top = len(self.levels) / width, max(self.peak, 1)
        out = []
        for i in range(width):
            chunk = self.levels[int(i * step) : max(int((i + 1) * step), int(i * step) + 1)]
            out.append(" ▁▂▃▄▅▆▇█"[round(8 * max(chunk) / top)])
        return "".join(out)


def mp3_seconds(data: bytes) -> float:
    """The length of an MP3 (layer III) in seconds, 0 where no frame is found."""
    offset = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        offset = 10 + ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F))
    seconds = 0.0
    while offset + 4 <= len(data):
        head = int.from_bytes(data[offset : offset + 4], "big")
        frame = _frame(head)
        if frame is None:
            offset += 1
            continue
        size, duration = frame
        seconds += duration
        offset += size
    return seconds


def _frame(head: int) -> tuple[int, float] | None:
    """(bytes, seconds) of the frame a 32-bit header starts, None for a header that is none."""
    version, layer, bitrate, rate = (head >> 19) & 3, (head >> 17) & 3, (head >> 12) & 15, (head >> 10) & 3
    if (head >> 21) != 0x7FF or version == 1 or layer != 1 or rate == 3 or bitrate in (0, 15):
        return None
    mpeg1 = version == 3
    kbit = _BITRATES[1 if mpeg1 else 2][bitrate]
    sample_rate_hz = _RATES[version][rate]
    samples = 1152 if mpeg1 else 576
    size = samples // 8 * kbit * 1000 // sample_rate_hz + ((head >> 9) & 1)
    return (size, samples / sample_rate_hz) if size > 4 else None
