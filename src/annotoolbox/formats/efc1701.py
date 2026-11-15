"""Anno 1701's effect files (``.efc``): a text of named blocks in braces, read into the emitters it holds.

``read_efc(text)`` is the entry: each emitter of the file's ``Effect`` block as an ``Emitter`` (its kind, header,
plain numbers, graphs and colour lists); ``parse`` gives the plain ``Block`` tree, ``Graph.at`` and
``Colors.at`` evaluate a graph and a colour list. docs/formats/anno1701-effects.md.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

_VALUE = re.compile(r'"[^"]*"|\S+')
_BEZIER_STEPS = 24  # the samples a smooth span is solved over for its x


class EfcError(ValueError):
    """Not an effect file this reader knows: the braces do not hold together, or there is no ``Effect`` block."""


@dataclass
class Block:
    """A named block: its fields (name -> the values as written; a later line of a name wins) and its blocks."""

    name: str
    fields: dict[str, list[str]] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)

    def block(self, name: str) -> Block | None:
        return next((b for b in self.blocks if b.name == name), None)

    def text(self, name: str, default: str = "") -> str:
        values = self.fields.get(name)
        return values[0].strip('"') if values else default

    def numbers(self, name: str) -> tuple[float, ...]:
        try:
            return tuple(float(v) for v in self.fields.get(name, ()))
        except ValueError:
            return ()

    def number(self, name: str, default: float = 0.0) -> float:
        values = self.numbers(name)
        return values[0] if values else default


def parse(text: str) -> list[Block]:
    """The top-level blocks of a text: a line with a name alone followed by ``{`` opens a block, ``}`` closes it,
    any other line is a field (its first word the name, the rest its values)."""
    root = Block("")
    stack, pending = [root], None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line == "{":
            if pending is None:
                raise EfcError("a brace without a block name")
            stack[-1].fields.pop(pending, None)
            stack[-1].blocks.append(Block(pending))
            stack.append(stack[-1].blocks[-1])
            pending = None
        elif line == "}":
            if len(stack) == 1:
                raise EfcError("a closing brace too many")
            stack.pop()
            pending = None
        else:
            name, *values = _VALUE.findall(line)
            stack[-1].fields[name] = values
            pending = name if not values else None
    if len(stack) != 1:
        raise EfcError("a block is not closed")
    return root.blocks


@dataclass(frozen=True)
class Graph:
    """A number along 0..1: ``value`` throughout when ``single``, else its points (x, y, in-tangent, out-tangent;
    y 0..1 between ``minimum`` and ``maximum``), straight between them when ``linear``, else smooth."""

    minimum: float = 0.0
    maximum: float = 1.0
    single: bool = True
    value: float = 0.0
    linear: bool = True
    points: tuple[tuple[float, float, float, float, float, float], ...] = ()  # x, y, ix, iy, ox, oy

    def at(self, x: float) -> float:
        if self.single or not self.points:
            return self.value
        return self.minimum + self._normalised(min(max(x, 0.0), 1.0)) * (self.maximum - self.minimum)

    def _normalised(self, x: float) -> float:
        pts = self.points
        if x <= pts[0][0] or len(pts) == 1:
            return pts[0][1]
        if x >= pts[-1][0]:
            return pts[-1][1]
        k = bisect.bisect_right([p[0] for p in pts], x) - 1
        a, b = pts[k], pts[k + 1]
        if b[0] - a[0] < 1e-9:
            return b[1]
        if self.linear:
            return a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])
        return _bezier_y(a, b, x)


def _bezier_y(a: tuple, b: tuple, x: float) -> float:
    """y at ``x`` on the span from ``a`` (leaving by its out-tangent) to ``b`` (arriving by its in-tangent)."""
    xs = (a[0], a[0] + a[4], b[0] + b[2], b[0])
    ys = (a[1], a[1] + a[5], b[1] + b[3], b[1])

    def curve(c: tuple, s: float) -> float:
        r = 1.0 - s
        return r * r * r * c[0] + 3 * r * r * s * c[1] + 3 * r * s * s * c[2] + s * s * s * c[3]

    samples = [curve(xs, k / _BEZIER_STEPS) for k in range(_BEZIER_STEPS + 1)]
    k = max(0, min(bisect.bisect_right(samples, x) - 1, _BEZIER_STEPS - 1))
    lo, hi = samples[k], samples[k + 1]
    s = (k + ((x - lo) / (hi - lo) if hi - lo > 1e-12 else 0.0)) / _BEZIER_STEPS
    return curve(ys, s)


def graph_of(block: Block) -> Graph:
    points = []
    for p in block.blocks:
        if p.name == "Point":
            xy, i, o = (p.numbers(n) + (0.0, 0.0) for n in ("p", "i", "o"))
            points.append((xy[0], xy[1], i[0], i[1], o[0], o[1]))
    return Graph(block.number("Minimum"), block.number("Maximum", 1.0), block.number("Single", 1.0) != 0,
                 block.number("Value"), block.number("Linear", 1.0) != 0, tuple(sorted(points)))  # fmt: skip


@dataclass(frozen=True)
class Colors:
    """A colour list: (time 0..1, (r, g, b, a) 0..1) in order; white without keys."""

    keys: tuple[tuple[float, tuple[float, float, float, float]], ...] = ()

    def at(self, t: float) -> tuple[float, float, float, float]:
        keys = self.keys
        if not keys:
            return (1.0, 1.0, 1.0, 1.0)
        if t <= keys[0][0]:
            return keys[0][1]
        for (ta, ca), (tb, cb) in zip(keys, keys[1:], strict=False):
            if t <= tb:
                f = (t - ta) / (tb - ta) if tb - ta > 1e-9 else 1.0
                return tuple(x + (y - x) * f for x, y in zip(ca, cb, strict=True))  # type: ignore[return-value]
        return keys[-1][1]


def colors_of(block: Block) -> Colors:
    keys = [(c.number("Time"), (c.numbers("RGBA") + (1.0,) * 4)[:4]) for c in block.blocks if c.name == "Color"]
    return Colors(tuple(sorted(keys, key=lambda k: k[0])))


@dataclass(frozen=True)
class Emitter:
    """One emitter: its kind (``ParticleEffect``, ``BillboardEffect``, ...), its header (times in ms; its place in
    the effect: a move, a quaternion x y z w, a scale per axis; the texture as named and its ``Rect``), and the
    rest of its block: ``block`` the plain fields, ``graphs`` and ``colors`` by name."""

    kind: str
    name: str
    start_ms: float
    duration_ms: float
    loop: bool
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    scale: tuple[float, float, float]
    texture: str
    rect: tuple[float, float, float, float]  # u0, v1, u1, v0 as written
    additive: bool
    block: Block
    graphs: dict[str, Graph]
    colors: dict[str, Colors]

    def graph(self, name: str, default: float = 0.0) -> Graph:
        return self.graphs.get(name) or Graph(value=default)

    def number(self, name: str, default: float = 0.0) -> float:
        return self.block.number(name, default)


def _emitter(b: Block) -> Emitter:
    graphs, colors = {}, {}
    for inner in b.blocks:
        if any(c.name == "Color" for c in inner.blocks):
            colors[inner.name] = colors_of(inner)
        elif "Minimum" in inner.fields or "Single" in inner.fields:
            graphs[inner.name] = graph_of(inner)
    textures = b.block("Textures")
    texture = b.text("Texture") or b.text("Texture0") or (textures.text("Texture000") if textures else "")
    blends = {b.text("SrcBlend").upper(), b.text("DestBlend").upper()}
    return Emitter(
        b.name, b.text("Name"), b.number("StartDelay"), b.number("Duration", 1000.0), b.number("Loop") != 0,
        _vector(b.numbers("Position"), (0.0, 0.0, 0.0)), _vector(b.numbers("Rotation"), (0.0, 0.0, 0.0, 1.0)),
        _vector(b.numbers("ScaleFactors"), (1.0, 1.0, 1.0)), texture.replace("\\", "/"),
        _vector(b.numbers("Rect"), (0.0, 1.0, 1.0, 0.0)), "ONE" in blends, b, graphs, colors,
    )  # fmt: skip


def _vector(values: tuple[float, ...], default: tuple) -> tuple:
    return values if len(values) == len(default) else default


def read_efc(text: str) -> list[Emitter]:
    """The emitters of a 1701 effect file, in file order. Raises ``EfcError`` for a file without an ``Effect``."""
    effect = next((b for b in parse(text) if b.name == "Effect"), None)
    if effect is None:
        raise EfcError("no Effect block")
    return [_emitter(b) for b in effect.blocks]


def is_efc1701(text: str) -> bool:
    """Whether an effect file's text is 1701's (braces) rather than 2070's (XML)."""
    return text.lstrip().startswith("Effect")
