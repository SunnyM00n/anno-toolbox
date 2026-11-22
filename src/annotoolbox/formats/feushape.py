"""The vector shapes of a UI movie (DefineShape 1 to 4), decoded into fills, lines and closed outlines for a preview.

``read_shape`` gives a ``SShape``: its fill and line styles and, per style, the outlines built from the edges (a
Flash edge names the fill on each side; ``_contours`` joins the edges of one fill into closed loops). Reading only:
the editor changes shapes through their placements and the images they fill with, not their edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .feuread import DEFINE_SHAPE, DEFINE_SHAPE3, DEFINE_SHAPE4, CBits, CFeuError, SMatrix, STag, read_matrix, read_rect

# fmt: off
SOLID, LINEAR, RADIAL, FOCAL = 0x00, 0x10, 0x12, 0x13
BITMAP_REPEAT, BITMAP_CLIP, BITMAP_REPEAT_HARD, BITMAP_CLIP_HARD = 0x40, 0x41, 0x42, 0x43
# fmt: on

Point = tuple[int, int]
# ("L", start, end) or ("Q", start, control, end), points in twips
Segment = tuple


@dataclass(frozen=True)
class SFill:
    kind: int
    color: tuple[int, int, int, int] = (0, 0, 0, 255)
    stops: tuple[tuple[int, tuple[int, int, int, int]], ...] = ()  # (ratio 0..255, RGBA)
    matrix: SMatrix | None = None  # gradient square or bitmap pixels -> shape space
    bitmap_id: int | None = None
    spread: int = 0  # 0 pad, 1 reflect, 2 repeat
    focal: float = 0.0


@dataclass(frozen=True)
class SLine:
    width: int  # twips
    color: tuple[int, int, int, int]
    fill: SFill | None = None


@dataclass
class SShape:
    char_id: int
    bounds: tuple[int, int, int, int]
    fills: list[tuple[SFill, list[list[Segment]]]] = field(default_factory=list)  # a style and its closed loops
    lines: list[tuple[SLine, list[Segment]]] = field(default_factory=list)
    bitmap_ids: set[int] = field(default_factory=set)


class _CReader:
    def __init__(self, tag: STag) -> None:
        self.code, self.body = tag.code, tag.body
        self.alpha = tag.code >= DEFINE_SHAPE3

    def color(self, bits: CBits) -> tuple[int, int, int, int]:
        rgb = (bits.read_unsigned(8), bits.read_unsigned(8), bits.read_unsigned(8))
        return (*rgb, bits.read_unsigned(8) if self.alpha else 255)

    def count(self, bits: CBits) -> int:
        first_byte = bits.read_unsigned(8)
        return (
            bits.read_unsigned(8) | (bits.read_unsigned(8) << 8)
            if first_byte == 0xFF and self.code != DEFINE_SHAPE
            else first_byte
        )

    def fill(self, bits: CBits) -> SFill:
        kind = bits.read_unsigned(8)
        if kind == SOLID:
            return SFill(kind, self.color(bits))
        if kind in (LINEAR, RADIAL, FOCAL):
            matrix = read_matrix(bits)
            spread, _interp, stop_count = bits.read_unsigned(2), bits.read_unsigned(2), bits.read_unsigned(4)
            stops = tuple((bits.read_unsigned(8), self.color(bits)) for _ in range(stop_count))
            focal = 0.0
            if kind == FOCAL:
                raw = bits.read_unsigned(8) | (bits.read_unsigned(8) << 8)
                focal = (raw - 0x10000 if raw & 0x8000 else raw) / 256
            return SFill(kind, stops[0][1] if stops else (0, 0, 0, 255), stops, matrix, None, spread, focal)
        if kind in (BITMAP_REPEAT, BITMAP_CLIP, BITMAP_REPEAT_HARD, BITMAP_CLIP_HARD):
            bid = bits.read_unsigned(8) | (bits.read_unsigned(8) << 8)
            return SFill(kind, (255, 255, 255, 255), (), read_matrix(bits), bid)
        raise CFeuError(f"shape {self.body[:2].hex()}: unknown fill type {kind:#x}")

    def styles(self, bits: CBits) -> tuple[list[SFill], list[SLine]]:
        bits.align()
        fills = [self.fill(bits) for _ in range(self.count(bits))]
        lines = []
        for _ in range(self.count(bits)):
            width = bits.read_unsigned(8) | (bits.read_unsigned(8) << 8)
            if self.code != DEFINE_SHAPE4:
                lines.append(SLine(width, self.color(bits)))
                continue
            _start_cap, join, has_fill = bits.read_unsigned(2), bits.read_unsigned(2), bits.read_unsigned(1)
            bits.read_unsigned(11)  # scaling, hinting, reserved, no-close, end cap
            if join == 2:
                bits.read_unsigned(16)  # miter limit
            fill = self.fill(bits) if has_fill else None
            lines.append(SLine(width, fill.color if fill else self.color(bits), fill))
        return fills, lines


class _CPen:
    """The running state of a shape's records: the position, the styles in use (numbers over all style arrays) and
    the style arrays read so far."""

    def __init__(self, reader: _CReader, bits: CBits) -> None:
        self.reader, self.bits = reader, bits
        self.fills, self.lines = reader.styles(bits)
        self.nfill, self.nline = bits.read_unsigned(4), bits.read_unsigned(4)
        self.base_fill = self.base_line = 0
        self.f0 = self.f1 = self.ln = 0
        self.x = self.y = 0

    def change(self) -> bool:
        """A style change record: a move, other styles in use, new style arrays; False for the end record."""
        bits = self.bits
        flags = bits.read_unsigned(5)
        if not flags:
            return False
        if flags & 0x01:
            bit_count = bits.read_unsigned(5)
            self.x, self.y = bits.read_signed(bit_count), bits.read_signed(bit_count)
        if flags & 0x02:
            self.f0 = _style(bits.read_unsigned(self.nfill), self.base_fill)
        if flags & 0x04:
            self.f1 = _style(bits.read_unsigned(self.nfill), self.base_fill)
        if flags & 0x08:
            self.ln = _style(bits.read_unsigned(self.nline), self.base_line)
        if flags & 0x10:
            self.base_fill, self.base_line = len(self.fills), len(self.lines)
            more_fills, more_lines = self.reader.styles(bits)
            self.fills += more_fills
            self.lines += more_lines
            self.nfill, self.nline = bits.read_unsigned(4), bits.read_unsigned(4)
            self.f0 = self.f1 = self.ln = 0
        return True

    def edge(self) -> Segment:
        """An edge record, drawn from the position, which moves to its end."""
        bits = self.bits
        x, y = self.x, self.y
        start = (x, y)
        straight = bits.read_unsigned(1)
        bit_count = bits.read_unsigned(4) + 2
        if straight:
            if bits.read_unsigned(1):
                x, y = x + bits.read_signed(bit_count), y + bits.read_signed(bit_count)
            elif bits.read_unsigned(1):
                y += bits.read_signed(bit_count)
            else:
                x += bits.read_signed(bit_count)
            seg: Segment = ("L", start, (x, y))
        else:
            cx, cy = x + bits.read_signed(bit_count), y + bits.read_signed(bit_count)
            x, y = cx + bits.read_signed(bit_count), cy + bits.read_signed(bit_count)
            seg = ("Q", start, (cx, cy), (x, y))
        self.x, self.y = x, y
        return seg


def read_shape(tag: STag) -> SShape:
    """A shape tag's styles and outlines."""
    body = tag.body
    character_id = body[0] | (body[1] << 8)
    bounds, offset_bytes = read_rect(body, 2)
    if tag.code == DEFINE_SHAPE4:
        offset_bytes = read_rect(body, offset_bytes)[1] + 1  # the edge bounds and the winding flags
    pen = _CPen(_CReader(tag), CBits(body, offset_bytes))
    fill_edges: dict[int, list[Segment]] = {}
    line_edges: dict[int, list[Segment]] = {}
    while True:
        if pen.bits.read_unsigned(1) == 0:
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
    shape = SShape(character_id, bounds)
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
