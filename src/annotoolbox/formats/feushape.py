"""The vector shapes of a UI movie (DefineShape 1 to 4), decoded into fills, lines and closed outlines for a preview.

``read_shape`` gives a ``Shape``: its fill and line styles and, per style, the outlines built from the edges (a
Flash edge names the fill on each side; ``_contours`` joins the edges of one fill into closed loops). Reading only:
the editor changes shapes through their placements and the images they fill with, not their edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .feuread import DEFINE_SHAPE, DEFINE_SHAPE3, DEFINE_SHAPE4, Bits, FeuError, Matrix, Tag, read_matrix, read_rect

# fmt: off
SOLID, LINEAR, RADIAL, FOCAL = 0x00, 0x10, 0x12, 0x13
BITMAP_REPEAT, BITMAP_CLIP, BITMAP_REPEAT_HARD, BITMAP_CLIP_HARD = 0x40, 0x41, 0x42, 0x43
# fmt: on

Point = tuple[int, int]
# ("L", start, end) or ("Q", start, control, end), points in twips
Segment = tuple


@dataclass(frozen=True)
class Fill:
    kind: int
    color: tuple[int, int, int, int] = (0, 0, 0, 255)
    stops: tuple[tuple[int, tuple[int, int, int, int]], ...] = ()  # (ratio 0..255, RGBA)
    matrix: Matrix | None = None  # gradient square or bitmap pixels -> shape space
    bitmap_id: int | None = None
    spread: int = 0  # 0 pad, 1 reflect, 2 repeat
    focal: float = 0.0


@dataclass(frozen=True)
class Line:
    width: int  # twips
    color: tuple[int, int, int, int]
    fill: Fill | None = None


@dataclass
class Shape:
    char_id: int
    bounds: tuple[int, int, int, int]
    fills: list[tuple[Fill, list[list[Segment]]]] = field(default_factory=list)  # a style and its closed loops
    lines: list[tuple[Line, list[Segment]]] = field(default_factory=list)
    bitmap_ids: set[int] = field(default_factory=set)


class _Reader:
    def __init__(self, tag: Tag) -> None:
        self.code, self.body = tag.code, tag.body
        self.alpha = tag.code >= DEFINE_SHAPE3

    def color(self, bits: Bits) -> tuple[int, int, int, int]:
        rgb = (bits.u(8), bits.u(8), bits.u(8))
        return (*rgb, bits.u(8) if self.alpha else 255)

    def count(self, bits: Bits) -> int:
        n = bits.u(8)
        return bits.u(8) | (bits.u(8) << 8) if n == 0xFF and self.code != DEFINE_SHAPE else n

    def fill(self, bits: Bits) -> Fill:
        kind = bits.u(8)
        if kind == SOLID:
            return Fill(kind, self.color(bits))
        if kind in (LINEAR, RADIAL, FOCAL):
            matrix = read_matrix(bits)
            spread, _interp, n = bits.u(2), bits.u(2), bits.u(4)
            stops = tuple((bits.u(8), self.color(bits)) for _ in range(n))
            focal = 0.0
            if kind == FOCAL:
                raw = bits.u(8) | (bits.u(8) << 8)
                focal = (raw - 0x10000 if raw & 0x8000 else raw) / 256
            return Fill(kind, stops[0][1] if stops else (0, 0, 0, 255), stops, matrix, None, spread, focal)
        if kind in (BITMAP_REPEAT, BITMAP_CLIP, BITMAP_REPEAT_HARD, BITMAP_CLIP_HARD):
            bid = bits.u(8) | (bits.u(8) << 8)
            return Fill(kind, (255, 255, 255, 255), (), read_matrix(bits), bid)
        raise FeuError(f"shape {self.body[:2].hex()}: unknown fill type {kind:#x}")

    def styles(self, bits: Bits) -> tuple[list[Fill], list[Line]]:
        bits.align()
        fills = [self.fill(bits) for _ in range(self.count(bits))]
        lines = []
        for _ in range(self.count(bits)):
            width = bits.u(8) | (bits.u(8) << 8)
            if self.code != DEFINE_SHAPE4:
                lines.append(Line(width, self.color(bits)))
                continue
            _start_cap, join, has_fill = bits.u(2), bits.u(2), bits.u(1)
            bits.u(11)  # scaling, hinting, reserved, no-close, end cap
            if join == 2:
                bits.u(16)  # miter limit
            fill = self.fill(bits) if has_fill else None
            lines.append(Line(width, fill.color if fill else self.color(bits), fill))
        return fills, lines


class _Pen:
    """The running state of a shape's records: the position, the styles in use (numbers over all style arrays) and
    the style arrays read so far."""

    def __init__(self, reader: _Reader, bits: Bits) -> None:
        self.reader, self.bits = reader, bits
        self.fills, self.lines = reader.styles(bits)
        self.nfill, self.nline = bits.u(4), bits.u(4)
        self.base_fill = self.base_line = 0
        self.f0 = self.f1 = self.ln = 0
        self.x = self.y = 0

    def change(self) -> bool:
        """A style change record: a move, other styles in use, new style arrays; False for the end record."""
        bits = self.bits
        flags = bits.u(5)
        if not flags:
            return False
        if flags & 0x01:
            n = bits.u(5)
            self.x, self.y = bits.s(n), bits.s(n)
        if flags & 0x02:
            self.f0 = _style(bits.u(self.nfill), self.base_fill)
        if flags & 0x04:
            self.f1 = _style(bits.u(self.nfill), self.base_fill)
        if flags & 0x08:
            self.ln = _style(bits.u(self.nline), self.base_line)
        if flags & 0x10:
            self.base_fill, self.base_line = len(self.fills), len(self.lines)
            more_fills, more_lines = self.reader.styles(bits)
            self.fills += more_fills
            self.lines += more_lines
            self.nfill, self.nline = bits.u(4), bits.u(4)
            self.f0 = self.f1 = self.ln = 0
        return True

    def edge(self) -> Segment:
        """An edge record, drawn from the position, which moves to its end."""
        bits = self.bits
        x, y = self.x, self.y
        start = (x, y)
        straight = bits.u(1)
        n = bits.u(4) + 2
        if straight:
            if bits.u(1):
                x, y = x + bits.s(n), y + bits.s(n)
            elif bits.u(1):
                y += bits.s(n)
            else:
                x += bits.s(n)
            seg: Segment = ("L", start, (x, y))
        else:
            cx, cy = x + bits.s(n), y + bits.s(n)
            x, y = cx + bits.s(n), cy + bits.s(n)
            seg = ("Q", start, (cx, cy), (x, y))
        self.x, self.y = x, y
        return seg


def read_shape(tag: Tag) -> Shape:
    """A shape tag's styles and outlines."""
    body = tag.body
    cid = body[0] | (body[1] << 8)
    bounds, pos = read_rect(body, 2)
    if tag.code == DEFINE_SHAPE4:
        pos = read_rect(body, pos)[1] + 1  # the edge bounds and the winding flags
    pen = _Pen(_Reader(tag), Bits(body, pos))
    fill_edges: dict[int, list[Segment]] = {}
    line_edges: dict[int, list[Segment]] = {}
    while True:
        if pen.bits.u(1) == 0:
            if not pen.change():
                break
            continue
        seg = pen.edge()
        if pen.f1:
            fill_edges.setdefault(pen.f1, []).append(seg)
        if pen.f0:
            fill_edges.setdefault(pen.f0, []).append(_reverse(seg))
        if pen.ln:
            line_edges.setdefault(pen.ln, []).append(seg)
    shape = Shape(cid, bounds)
    for index in sorted(fill_edges):
        fill = pen.fills[index - 1]
        shape.fills.append((fill, _contours(fill_edges[index])))
        if fill.bitmap_id is not None:
            shape.bitmap_ids.add(fill.bitmap_id)
    for index in sorted(line_edges):
        shape.lines.append((pen.lines[index - 1], line_edges[index]))
    return shape


def _style(index: int, base: int) -> int:
    """A style number of the current arrays as a number over all arrays (0 stays "none")."""
    return index + base if index else 0


def _reverse(seg: Segment) -> Segment:
    return ("L", seg[2], seg[1]) if seg[0] == "L" else ("Q", seg[3], seg[2], seg[1])


def _end(seg: Segment) -> Point:
    return seg[-1]


def _contours(segments: list[Segment]) -> list[list[Segment]]:
    """The edges of one fill joined end to start into loops (an open run stays a run: the fill closes it)."""
    starts: dict[Point, list[int]] = {}
    for i, seg in enumerate(segments):
        starts.setdefault(seg[1], []).append(i)
    used = [False] * len(segments)
    loops = []
    for first in range(len(segments)):
        if used[first]:
            continue
        loop, i = [], first
        while i is not None and not used[i]:
            used[i] = True
            loop.append(segments[i])
            end = _end(segments[i])
            if end == segments[first][1]:
                break
            i = next((j for j in starts.get(end, ()) if not used[j]), None)
        loops.append(loop)
    return loops
