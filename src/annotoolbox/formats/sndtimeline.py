"""A model's ``.snd`` file of the old games (2070, 1404; 1701 ships none): per animation sequence its length in
milliseconds and the sounds placed on its time line.

``parse(text)`` reads one into a ``Timeline``, ``render(timeline)`` writes it back in the game's layout (CRLF, one
``<k>`` / ``<v>`` pair per sequence, byte for byte the shipped files). A ``Placement`` with ``at`` below 0 plays through
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
class Placement:
    sound: int
    at: int  # ms into the sequence; negative: the sound runs through the whole sequence


@dataclass
class Timeline:
    """A ``.snd``: the sequence's length (ms) and the sounds on its time line, by sequence id."""

    filename: str = ""
    durations: dict[int, int] = field(default_factory=dict)
    placements: dict[int, list[Placement]] = field(default_factory=dict)


def parse(text: str) -> Timeline:
    """The timeline of a ``.snd``'s text; ValueError for text that is not XML."""
    try:
        root = ET.fromstring(text.lstrip("﻿"))
    except ET.ParseError as exc:
        raise ValueError(f"not a sound timeline: {exc}") from exc
    out = Timeline((root.findtext("m_Filename") or "").strip())
    for tag, fill in (("m_SequenceDuration", "durations"), ("m_SequenceSoundIDMap", "placements")):
        node = root.find(tag)
        kids = list(node) if node is not None else []
        for k, v in zip(kids[0::2], kids[1::2], strict=False):
            if fill == "durations":
                out.durations[number(k)] = number(v)
            else:
                out.placements[number(k)] = [
                    Placement(number(i.find("SoundID")), number(i.find("Position"))) for i in v
                ]
    return out


def render(timeline: Timeline) -> str:
    """The file's text for a timeline, in the shipped layout."""
    lengths = "".join(f"<k>{k}</k>{EOL}<v>{v}</v>{EOL}" for k, v in timeline.durations.items())
    placed = "".join(
        f"<k>{k}</k>{EOL}<v>"
        + "".join(f"<i><SoundID>{p.sound}</SoundID>{EOL}<Position>{p.at}</Position>{EOL}</i>{EOL}" for p in row)
        + f"</v>{EOL}"
        for k, row in timeline.placements.items()
    )
    return (
        f"<SequenceSounds><m_Filename>{escape(timeline.filename)}</m_Filename>{EOL}"
        f"<m_SequenceDuration>{lengths}</m_SequenceDuration>{EOL}"
        f"<m_SequenceSoundIDMap>{placed}</m_SequenceSoundIDMap>{EOL}</SequenceSounds>{EOL}"
    )


class TimelineError(ValueError):
    """An edit that cannot be made (no such sequence or placement, a negative length)."""


def _copy(timeline: Timeline) -> Timeline:
    return Timeline(
        timeline.filename,
        dict(timeline.durations),
        {k: [Placement(p.sound, p.at) for p in row] for k, row in timeline.placements.items()},
    )


def set_length(timeline: Timeline, sequence: int, ms: int) -> Timeline:
    """The timeline with the length of ``sequence`` set to ``ms`` (a sequence the file lacks is added)."""
    if ms < 0:
        raise TimelineError(f"a length cannot be negative ({ms})")
    out = _copy(timeline)
    out.durations[sequence] = ms
    return out


def place(timeline: Timeline, sequence: int, sound: int, at: int) -> Timeline:
    """The timeline with ``sound`` placed on ``sequence`` at ``at`` ms (below 0: through the whole sequence)."""
    out = _copy(timeline)
    out.placements.setdefault(sequence, []).append(Placement(sound, at))
    return out


def _row(timeline: Timeline, sequence: int, index: int) -> list[Placement]:
    row = timeline.placements.get(sequence, [])
    if not 0 <= index < len(row):
        raise TimelineError(f"sequence {sequence} has no placement {index}")
    return row


def unplace(timeline: Timeline, sequence: int, index: int) -> Timeline:
    """The timeline without placement ``index`` of ``sequence`` (a sequence left with none keeps its empty entry)."""
    _row(timeline, sequence, index)
    out = _copy(timeline)
    del out.placements[sequence][index]
    return out


def move(timeline: Timeline, sequence: int, index: int, at: int, sound: int | None = None) -> Timeline:
    """The timeline with placement ``index`` of ``sequence`` moved to ``at`` ms (and, given, playing ``sound``)."""
    _row(timeline, sequence, index)
    out = _copy(timeline)
    out.placements[sequence][index] = Placement(
        timeline.placements[sequence][index].sound if sound is None else sound, at
    )
    return out
