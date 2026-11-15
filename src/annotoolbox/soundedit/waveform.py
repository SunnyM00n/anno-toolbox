"""The shape of a clip for the window's waveform view, without Qt: ``shape(wem, bins)`` decodes a ``.wem`` (PCM, Wwise
ADPCM or Wwise Vorbis) and returns the lowest and highest sample of each of ``bins`` columns with the clip's length.

The window draws it (``wavewidget``); what the view shows and what it needs is in docs/sound-editor.md "The waveform".
"""

from __future__ import annotations

from dataclasses import dataclass

from ..infra.i18n import tr
from .clipcodec import Clip, ClipError, read_wem

MAX_BYTES = 40_000_000  # a larger file is not decoded for a picture


@dataclass(frozen=True)
class Shape:
    """A clip's peaks: per column (lowest, highest) sample, and the clip's length, rate and channels."""

    peaks: tuple[tuple[int, int], ...]
    seconds: float
    rate: int
    channels: int


def peaks(clip: Clip, bins: int) -> tuple[tuple[int, int], ...]:
    """The lowest and highest sample of each of ``bins`` equal parts of the clip (channels together); an empty clip
    gives flat columns."""
    count = max(bins, 1)
    total = len(clip.samples)
    out = []
    for i in range(count):
        part = clip.samples[total * i // count : total * (i + 1) // count]
        out.append((min(part), max(part)) if len(part) else (0, 0))
    return tuple(out)


def shape(wem: bytes, bins: int) -> Shape:
    """The shape of a ``.wem``; a ``ClipError`` says why it cannot be drawn."""
    if len(wem) > MAX_BYTES:
        raise ClipError(tr("{size} MB: too large to draw", size=len(wem) // 1_000_000))
    clip = read_wem(wem)
    return Shape(peaks(clip, bins), clip.seconds, clip.rate, clip.channels)
