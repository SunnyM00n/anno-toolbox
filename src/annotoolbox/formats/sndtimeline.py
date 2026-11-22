"""A model's ``.snd`` file of the old games (2070, 1404; 1701 ships none): per animation sequence its length in
milliseconds and the sounds placed on its time line.

``parse(text)`` reads one into a ``STimeline``, ``render(timeline)`` writes it back in the game's layout (CRLF, one
``<k>`` / ``<v>`` pair per sequence, byte for byte the shipped files). A ``SPlacement`` with ``at`` below 0 plays through
the whole sequence. ``set_length``, ``place``, ``unplace`` and ``move`` return a changed copy. The ids name
entries of ``sounddatabase.xml`` (``formats/sounddb``); ``docs/sounds.md``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from .sounddb import number

EOL = "\r\n"


@dataclass
class SPlacement:
    sound: int
    at_ms: int  # ms into the sequence; negative: the sound runs through the whole sequence


@dataclass
class STimeline:
    """A ``.snd``: the sequence's length (ms) and the sounds on its time line, by sequence id."""

    filename: str = ""
    durations: dict[int, int] = field(default_factory=dict)
    placements: dict[int, list[SPlacement]] = field(default_factory=dict)


def parse(text: str) -> STimeline:
    """The timeline of a ``.snd``'s text; ValueError for text that is not XML."""
    try:
        root = ET.fromstring(text.lstrip("﻿"))
    except ET.ParseError as exc:
        raise ValueError(f"not a sound timeline: {exc}") from exc
    out = STimeline((root.findtext("m_Filename") or "").strip())
    for tag, fill in (("m_SequenceDuration", "durations"), ("m_SequenceSoundIDMap", "placements")):
        node = root.find(tag)
        kids = list(node) if node is not None else []
        for key_node, value_node in zip(kids[0::2], kids[1::2], strict=False):
            if fill == "durations":
                out.durations[number(key_node)] = number(value_node)
            else:
                out.placements[number(key_node)] = [
                    SPlacement(number(placement_node.find("SoundID")), number(placement_node.find("Position")))
                    for placement_node in value_node
                ]
    return out


def render(timeline: STimeline) -> str:
    """The file's text for a timeline, in the shipped layout."""
    lengths = "".join(f"<k>{sequence}</k>{EOL}<v>{length}</v>{EOL}" for sequence, length in timeline.durations.items())
    placed = "".join(
        f"<k>{sequence}</k>{EOL}<v>"
        + "".join(
            f"<i><SoundID>{placement.sound}</SoundID>{EOL}<Position>{placement.at_ms}</Position>{EOL}</i>{EOL}"
            for placement in row
        )
        + f"</v>{EOL}"
        for sequence, row in timeline.placements.items()
    )
    return (
        f"<SequenceSounds><m_Filename>{escape(timeline.filename)}</m_Filename>{EOL}"
        f"<m_SequenceDuration>{lengths}</m_SequenceDuration>{EOL}"
        f"<m_SequenceSoundIDMap>{placed}</m_SequenceSoundIDMap>{EOL}</SequenceSounds>{EOL}"
    )


class CTimelineError(ValueError):
    """An edit that cannot be made (no such sequence or placement, a negative length)."""


def _copy(timeline: STimeline) -> STimeline:
    return STimeline(
        timeline.filename,
        dict(timeline.durations),
        {
            sequence: [SPlacement(placement.sound, placement.at_ms) for placement in row]
            for sequence, row in timeline.placements.items()
        },
    )


def set_length(timeline: STimeline, sequence: int, length_ms: int) -> STimeline:
    """The timeline with the length of ``sequence`` set to ``length_ms`` (a sequence the file lacks is added)."""
    if length_ms < 0:
        raise CTimelineError(f"a length cannot be negative ({length_ms})")
    out = _copy(timeline)
    out.durations[sequence] = length_ms
    return out


def place(timeline: STimeline, sequence: int, sound: int, at_ms: int) -> STimeline:
    """The timeline with ``sound`` placed on ``sequence`` at ``at_ms`` (below 0: through the whole sequence)."""
    out = _copy(timeline)
    out.placements.setdefault(sequence, []).append(SPlacement(sound, at_ms))
    return out


def _row(timeline: STimeline, sequence: int, index: int) -> list[SPlacement]:
    row = timeline.placements.get(sequence, [])
    if not 0 <= index < len(row):
        raise CTimelineError(f"sequence {sequence} has no placement {index}")
    return row


def unplace(timeline: STimeline, sequence: int, index: int) -> STimeline:
    """The timeline without placement ``index`` of ``sequence`` (a sequence left with none keeps its empty entry)."""
    _row(timeline, sequence, index)
    out = _copy(timeline)
    del out.placements[sequence][index]
    return out


def move(timeline: STimeline, sequence: int, index: int, at_ms: int, sound: int | None = None) -> STimeline:
    """The timeline with placement ``index`` of ``sequence`` moved to ``at_ms`` (and, given, playing ``sound``)."""
    _row(timeline, sequence, index)
    out = _copy(timeline)
    out.placements[sequence][index] = SPlacement(
        timeline.placements[sequence][index].sound if sound is None else sound, at_ms
    )
    return out
