"""What a game dump says about a label's vertical alignment and its sizing modes, counted against the readings the
files and the editor's descriptions allow.

``fit(dump)`` goes over the labels of a dump (``gamedump.load``): per stored ``AlignmentY`` value it counts the labels
whose text sits where each reading puts it (top, centre, bottom, baseline on the bottom), per stored sizing value those
whose box has the size each reading gives (the plane times the scale, the shown text, the larger, the smaller).
``report`` writes the counts. It does not say what a value means, only which reading the game's numbers agree with, or
that none does (docs/ui-editor.md "Comparing with the game"; docs/ui/117/layout.md "Labels").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .gamedump import Dump, GameWidget

TOLERANCE = 1.0  # reference pixels: two numbers this close are the same
VERTICAL = ("top", "centre", "bottom", "baseline")
SIZING = ("plane", "shown text", "larger", "smaller")


@dataclass
class Group:
    """The labels that store one value: how many, how many a reading tells apart from the others, and for each
    reading how many agree with it."""

    labels: int = 0
    informative: int = 0
    agree: Counter = field(default_factory=Counter)
    none: list[str] = field(default_factory=list)  # names of informative labels no reading agrees with


@dataclass
class LabelFit:
    vertical: dict[float, Group] = field(default_factory=dict)  # stored AlignmentY -> its labels
    horizontal: dict[int, Group] = field(default_factory=dict)  # stored HorizontalSizing -> its labels
    height: dict[int, Group] = field(default_factory=dict)  # stored VerticalSizing -> its labels
    labels: int = 0


def _pair(value) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2 and all(isinstance(v, (int, float)) for v in value[:2]):
        return float(value[0]), float(value[1])
    return None


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _near(a: float, b: float) -> bool:
    return abs(a - b) <= TOLERANCE


def vertical_readings(box: float, shown: float, offset: float, baseline: float | None) -> dict[str, bool]:
    """Which readings of the vertical alignment put a text of height ``shown`` at ``offset`` in a box of ``box``."""
    return {
        "top": _near(offset, 0.0),
        "centre": _near(offset, (box - shown) / 2),
        "bottom": _near(offset, box - shown),
        "baseline": baseline is not None and _near(offset + baseline, box),
    }


def sizing_readings(box: float, plane: float, shown: float) -> dict[str, bool]:
    """Which readings of a sizing mode give a box of ``box``, from the plane's size times the scale and the shown text's."""
    return {
        "plane": _near(box, plane),
        "shown text": _near(box, shown),
        "larger": _near(box, max(plane, shown)),
        "smaller": _near(box, min(plane, shown)),
    }


def _count(groups: dict, key, readings: dict[str, bool], informative: bool, name: str) -> None:
    group = groups.setdefault(key, Group())
    group.labels += 1
    if not informative:
        return
    group.informative += 1
    group.agree.update(r for r, ok in readings.items() if ok)
    if not any(readings.values()):
        group.none.append(name)


def _label(fit: LabelFit, w: GameWidget) -> None:
    props = w.raw.get("props") or {}
    plane, shown, offset, scale = (
        _pair(props.get(k)) for k in ("TextPlane", "DisplayedSize", "DisplayedOffset", "Scale")
    )
    if plane is None or shown is None:
        return
    fit.labels += 1
    scale = scale or (1.0, 1.0)
    name = w.name or ""
    if (align := _number(props.get("AlignmentY"))) is not None and offset is not None:
        readings = vertical_readings(w.bbox[3], shown[1], offset[1], _number(props.get("BaselineY")))
        _count(fit.vertical, round(align, 3), readings, w.bbox[3] - shown[1] > TOLERANCE, name)
    for groups, key, axis in ((fit.horizontal, "HorizontalSizing", 0), (fit.height, "VerticalSizing", 1)):
        if (mode := _number(props.get(key))) is not None:
            box, flat = w.bbox[2 + axis], plane[axis] * scale[axis]
            readings = sizing_readings(box, flat, shown[axis])
            _count(groups, int(mode), readings, not _near(flat, shown[axis]), name)


def fit(dump: Dump) -> LabelFit:
    """The dump's labels counted against the readings."""
    out = LabelFit()
    for w in dump.widgets:
        if w.cls.startswith("Label"):
            _label(out, w)
    return out


def _lines(title: str, groups: dict, readings: tuple[str, ...]) -> list[str]:
    out = [title]
    for key in sorted(groups):
        g = groups[key]
        counts = ", ".join(f"{r} {g.agree[r]}" for r in readings)
        out.append(f"  {key}: {g.labels} labels, {g.informative} that tell the readings apart; agree with: {counts}")
        if g.none:
            out.append(f"     no reading fits: {', '.join(g.none[:8])}{' ...' if len(g.none) > 8 else ''}")
    return out


def report(result: LabelFit) -> str:
    """The counts as text."""
    lines = [f"{result.labels} labels"]
    lines += _lines("AlignmentY as stored:", result.vertical, VERTICAL)
    lines += _lines("HorizontalSizing as stored (the box's width):", result.horizontal, SIZING)
    lines += _lines("VerticalSizing as stored (the box's height):", result.height, SIZING)
    return "\n".join(lines) + "\n"
