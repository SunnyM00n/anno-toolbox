"""The curves of an effect as the editor's graphs and colour bands take them: points in the curve's own units, colour
stops, and the one edit that writes a changed set back. No Qt.

``XmlCurve`` is a 2070 or 1404 curve (``<X>ValueMin`` ... and its knots ``<X>KnotPos_i`` ..., docs/particle-editor.md
"2070 and 1404"), ``BraceGraph`` a 1701 graph block and ``BraceColors`` a 1701 colour list (``efc1701``). Each has
``points()`` or ``stops()``, ``at(x)`` for drawing, and ``set_points`` / ``set_stops`` returning a ``cfgdoc.Edit``.
An edit swaps a whole list (an effect's tags, the file's lines) for a new one, so undo restores it exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..formats import isd2070
from ..formats.efc1701 import Graph
from ..formats.efc1701edit import BraceDoc, Group, Line, field_text, indent_of, number
from ..models.cfgdoc import Edit
from ..models.particles2070 import CURVE_PART, KNOT

GRAPH, COLORS = "graph", "colors"
CHANNELS = "XYZW"
DEFAULT_TANGENT = ("-0.166667 0.000000", "0.166667 0.000000")  # what the studio's tool writes for a new point


def finite(text: str | None, default: float = 0.0) -> float:
    """The number a file holds in ``text``; ``default`` for none, a word or a value no file can hold (nan, inf)."""
    try:
        value = float(text) if text is not None else default
    except ValueError:
        return default
    return value if math.isfinite(value) else default


def usable(*rows) -> bool:
    """Whether every number of the points or stops ``rows`` is one a file can hold (a drag out of the window gives nan)."""
    return all(math.isfinite(v) for row in rows for v in _flat(row))


def _flat(item):
    if isinstance(item, (int, float)):
        yield item
    else:
        for part in item:
            yield from _flat(part)


def swap(holder: list, new: list, label: str) -> Edit:
    """An edit that makes ``holder`` (a list kept by identity) ``new``, and back."""
    old = list(holder)

    def put(items: list):
        def run() -> None:
            holder[:] = items

        return run

    return Edit(label, put(list(new)), put(old))


def xml_curves(effect: isd2070.Node) -> list[str]:
    """The names of the curves of a 2070 or 1404 emitter, in file order (a curve has its ``Size`` tag)."""
    out = []
    for leaf in effect.children:
        m = CURVE_PART.match(leaf.name)
        if m and m.group(2) == "Size" and m.group(1) not in out:
            out.append(m.group(1))
    return out


def is_curve_tag(name: str, curves: list[str]) -> bool:
    m = CURVE_PART.match(name) or KNOT.match(name)
    return bool(m) and m.group(1) in curves


@dataclass
class XmlCurve:
    """The curve ``name`` of the emitter ``effect``: knots are fractions of ``ValueMin`` .. ``ValueMax``."""

    effect: isd2070.Node
    name: str

    @property
    def kind(self) -> str:
        return COLORS if self.name == "Color" else GRAPH

    def _leaf(self, part: str) -> isd2070.Node | None:
        return self.effect.find(self.name + part)

    def range(self) -> tuple[float, float]:
        return finite(self._value("ValueMin")), finite(self._value("ValueMax"), 1.0)

    def _value(self, part: str) -> str | None:
        leaf = self._leaf(part)
        return leaf.text if leaf is not None else None

    def knots(self) -> list[tuple[float, list[float]]]:
        """(position, the four channels as stored fractions) of each knot, by position."""
        found: dict[int, dict[str, float]] = {}
        for leaf in self.effect.children:
            m = KNOT.match(leaf.name)
            if m and m.group(1) == self.name:
                found.setdefault(int(m.group(3), 16), {})[m.group(2)] = finite(leaf.text)
        out = [(k.get("Pos", 0.0), [k.get(c, 0.0) for c in CHANNELS]) for _i, k in sorted(found.items())]
        return sorted(out, key=lambda kn: kn[0])

    def points(self) -> list[tuple[float, float]]:
        """(position 0..1, value) of each knot in the curve's units; one point at the default when it has none."""
        lo, hi = self.range()
        return [(pos, lo + ch[0] * (hi - lo)) for pos, ch in self.knots()]

    def default(self) -> float:
        return finite(self._value("ValueDef"))

    def at(self, x: float) -> float:
        pts = self.points()
        if not pts:
            return self.default()
        if x <= pts[0][0]:
            return pts[0][1]
        for (xa, ya), (xb, yb) in zip(pts, pts[1:], strict=False):
            if x <= xb:
                return ya if xb - xa < 1e-9 else ya + (yb - ya) * (x - xa) / (xb - xa)
        return pts[-1][1]

    def stops(self) -> list[tuple[float, tuple[float, float, float, float]]]:
        """A colour curve's knots as (position, rgba 0..1): X, Y, Z are red, green, blue; alpha is its own curve."""
        lo, hi = self.range()
        return [(pos, (*(min(max(lo + c * (hi - lo), 0.0), 1.0) for c in ch[:3]), 1.0)) for pos, ch in self.knots()]

    def set_points(self, points: list[tuple[float, float]]) -> Edit | None:
        """The knots made ``points`` (position, value): a channel that followed the first keeps following it; None
        when a number is none a file can hold."""
        if not usable(points):
            return None
        lo, hi = self.range()
        old = self.knots()
        rows = []
        for k, (pos, value) in enumerate(sorted(points)):
            n = (value - lo) / ((hi - lo) or 1.0)
            was = old[k][1] if k < len(old) else None
            rows.append((pos, [n if was is None or abs(c - was[0]) < 1e-6 else c for c in (was or [n] * 4)]))
        return self._write(rows, f"{self.name}: {len(rows)} points")

    def set_stops(self, stops: list[tuple[float, tuple[float, ...]]]) -> Edit | None:
        if not usable(stops):
            return None
        lo, hi = self.range()
        old = self.knots()
        rows = []
        for k, (pos, rgba) in enumerate(sorted(stops, key=lambda s: s[0])):
            w = old[k][1][3] if k < len(old) else 1.0
            rows.append((pos, [(c - lo) / ((hi - lo) or 1.0) for c in rgba[:3]] + [w]))
        return self._write(rows, f"{self.name}: {len(rows)} colours")

    def _write(self, rows: list[tuple[float, list[float]]], label: str) -> Edit:
        """The emitter's tags with this curve's knots made ``rows`` and its ``Size`` their count."""
        size = self._leaf("Size")
        tail = size.tail if size is not None else b"\r\n"
        knots = []
        for i, (pos, channels) in enumerate(rows):
            knots.append(isd2070.Node(f"{self.name}KnotPos_{i:x}", f"{pos:.6f}", tail=tail))
            knots += [
                isd2070.Node(f"{self.name}Knot{c}_{i:x}", f"{v:.6f}", tail=tail)
                for c, v in zip(CHANNELS, channels, strict=True)
            ]
        out = []
        for leaf in self.effect.children:
            m = KNOT.match(leaf.name)
            if m and m.group(1) == self.name:
                continue
            if leaf is size:
                out.append(isd2070.Node(leaf.name, str(len(rows)), tail=leaf.tail, trail=leaf.trail))
                out += knots
                continue
            out.append(leaf)
        return swap(self.effect.children, out, label)


@dataclass
class BraceGraph:
    """A 1701 graph block: ``p`` y of each point is a fraction of ``Minimum`` .. ``Maximum``; ``Single`` makes it
    ``Value`` throughout."""

    doc: BraceDoc
    group: Group
    kind: str = GRAPH

    @property
    def name(self) -> str:
        return self.group.name

    def _number(self, name: str, default: float = 0.0) -> float:
        found = self.group.entries(name)
        return finite(found[0].values[0], default) if found and found[0].values else default

    def range(self) -> tuple[float, float]:
        return self._number("Minimum"), self._number("Maximum", 1.0)

    def _raw(self) -> list[tuple[float, float, float, float, float, float]]:
        out = []
        for point in self.group.groups("Point"):
            vals = {e.name: [finite(v) for v in e.values] + [0.0, 0.0] for e in point.entries()}
            p, i, o = (vals.get(n, [0.0, 0.0]) for n in ("p", "i", "o"))
            out.append((p[0], p[1], i[0], i[1], o[0], o[1]))
        return out

    def graph(self) -> Graph:
        lo, hi = self.range()
        return Graph(lo, hi, self._number("Single", 1.0) != 0, self._number("Value"), self._number("Linear", 1.0) != 0,
                     tuple(sorted(self._raw())))  # fmt: skip

    def points(self) -> list[tuple[float, float]]:
        lo, hi = self.range()
        return sorted((x, lo + y * (hi - lo)) for x, y, *_t in self._raw())

    def default(self) -> float:
        return self._number("Value")

    def at(self, x: float) -> float:
        graph = self.graph()
        return graph.at(x) if graph.points else graph.value

    def set_points(self, points: list[tuple[float, float]]) -> Edit | None:
        """The graph's points made ``points`` (x, value): an old point keeps its tangents, a new one gets the studio
        tool's; ``Single`` is switched off, so the points play. None when a number is none a file can hold."""
        if not usable(points):
            return None
        lo, hi = self.range()
        olds = self.group.groups("Point")
        by_x = sorted(olds, key=_point_x)  # as points() lists them: a file's points need not be in x order
        made: list[Line] = []
        for k, (x, value) in enumerate(sorted(points)):
            y = (value - lo) / ((hi - lo) or 1.0)
            made += _point_lines(by_x[k] if k < len(by_x) else None, olds[0] if olds else None, self.group, x, y)
        lines = _replaced(self.doc, olds, self.group, made)
        single = self.group.entries("Single")
        if single and points:
            lines = [Line(field_text(single[0], ["0"]), x.end) if x is single[0].line else x for x in lines]
        return swap(self.doc.lines, lines, f"{self.name}: {len(points)} points")


@dataclass
class BraceColors:
    """A 1701 colour list: ``Color`` blocks of ``RGBA`` (0..1) and ``Time`` (0..1)."""

    doc: BraceDoc
    group: Group
    kind: str = COLORS

    @property
    def name(self) -> str:
        return self.group.name

    def range(self) -> tuple[float, float]:
        """What the view's axes span: a colour list writes its colours as they are, 0..1."""
        return 0.0, 1.0

    def stops(self) -> list[tuple[float, tuple[float, float, float, float]]]:
        out = []
        for c in self.group.groups("Color"):
            rgba = next((e.values for e in c.entries("RGBA")), [])
            t = next((e.values for e in c.entries("Time")), ["0"])
            out.append((finite(t[0]), tuple(finite(v) for v in rgba + ["1"] * 4)[:4]))
        return sorted(out, key=lambda s: s[0])

    def set_stops(self, stops: list[tuple[float, tuple[float, ...]]]) -> Edit | None:
        if not usable(stops):
            return None
        olds = self.group.groups("Color")
        made: list[Line] = []
        for k, (t, rgba) in enumerate(sorted(stops, key=lambda s: s[0])):
            model = olds[k] if k < len(olds) else (olds[0] if olds else None)
            inner = indent_of(model.head) + "\t" if model is not None else indent_of(self.group.head) + "\t\t"
            outer = indent_of(model.head) if model is not None else indent_of(self.group.head) + "\t"
            end = (model.head if model is not None else self.group.head).end
            made += [Line(f"{outer}Color", end), Line(f"{outer}{{", end),
                     Line(f"{inner}RGBA {' '.join(number(c) for c in (tuple(rgba) + (1.0,) * 4)[:4])}", end),
                     Line(f"{inner}Time {number(t)}", end), Line(f"{outer}}}", end)]  # fmt: skip
        return swap(self.doc.lines, _replaced(self.doc, olds, self.group, made), f"{self.name}: {len(stops)} colours")


def _point_x(point: Group) -> float:
    """The x of a 1701 graph's ``Point`` block (0 without a ``p``, as the graph reads it)."""
    found = point.entries("p")
    return finite(found[0].values[0]) if found and found[0].values else 0.0


def _point_lines(old: Group | None, model: Group | None, holder: Group, x: float, y: float) -> list[Line]:
    """The lines of one point: an old point's with its ``p`` changed, else a new one shaped as ``model``."""
    xy = f"{number(x)} {number(y)}"
    if old is not None:
        out = []
        for line in old.lines():
            entry = next((e for e in old.entries("p") if e.line is line), None)
            out.append(
                Line(field_text(entry, xy.split()), line.end) if entry is not None else Line(line.text, line.end)
            )
        return out
    shape = model or holder
    outer = indent_of(model.head) if model is not None else indent_of(holder.head) + "\t"
    inner = outer + "\t"
    end = shape.head.end
    return [Line(f"{outer}Point", end), Line(f"{outer}{{", end), Line(f"{inner}p {xy}", end),
            Line(f"{inner}i {DEFAULT_TANGENT[0]}", end), Line(f"{inner}o {DEFAULT_TANGENT[1]}", end),
            Line(f"{inner}udt 0", end), Line(f"{outer}}}", end)]  # fmt: skip


def _replaced(doc: BraceDoc, olds: list[Group], holder: Group, made: list[Line]) -> list[Line]:
    """The file's lines with the blocks ``olds`` of ``holder`` taken out and ``made`` put where the first was (or
    before the holder's closing brace)."""
    gone = {id(line) for g in olds for line in g.lines()}
    at = doc.index(olds[0].head) if olds else doc.index(holder.close)
    kept = [line for line in doc.lines[:at] if id(line) not in gone]
    rest = [line for line in doc.lines[at:] if id(line) not in gone]
    return kept + made + rest
