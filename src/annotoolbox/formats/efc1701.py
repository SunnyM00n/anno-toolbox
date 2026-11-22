"""Anno 1701's effect files (``.efc``): a text of named blocks in braces, read into the emitters it holds.

``read_efc(text)`` is the entry: each emitter of the file's ``Effect`` block as an ``SEmitter`` (its kind, header,
plain numbers, graphs and colour lists); ``parse`` gives the plain ``SBlock`` tree, ``SGraph.at`` and
``SColors.at`` evaluate a graph and a colour list. docs/formats/anno1701-effects.md.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

_VALUE = re.compile(r'"[^"]*"|\S+')
_BEZIER_STEPS = 24  # the samples a smooth span is solved over for its x


class CEfcError(ValueError):
    """Not an effect file this reader knows: the braces do not hold together, or there is no ``Effect`` block."""


@dataclass
class SBlock:
    """A named block: its fields (name -> the values as written; a later line of a name wins) and its blocks."""

    name: str
    fields: dict[str, list[str]] = field(default_factory=dict)
    blocks: list[SBlock] = field(default_factory=list)

    def block(self, name: str) -> SBlock | None:
        return next((child for child in self.blocks if child.name == name), None)

    def text(self, name: str, default: str = "") -> str:
        values = self.fields.get(name)
        return values[0].strip('"') if values else default

    def numbers(self, name: str) -> tuple[float, ...]:
        try:
            return tuple(float(number) for number in self.fields.get(name, ()))
        except ValueError:
            return ()

    def number(self, name: str, default: float = 0.0) -> float:
        values = self.numbers(name)
        return values[0] if values else default


def parse(text: str) -> list[SBlock]:
    """The top-level blocks of a text: a line with a name alone followed by ``{`` opens a block, ``}`` closes it,
    any other line is a field (its first word the name, the rest its values)."""
    root = SBlock("")
    stack, pending = [root], None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line == "{":
            if pending is None:
                raise CEfcError("a brace without a block name")
            stack[-1].fields.pop(pending, None)
            stack[-1].blocks.append(SBlock(pending))
            stack.append(stack[-1].blocks[-1])
            pending = None
        elif line == "}":
            if len(stack) == 1:
                raise CEfcError("a closing brace too many")
            stack.pop()
            pending = None
        else:
            name, *values = _VALUE.findall(line)
            stack[-1].fields[name] = values
            pending = name if not values else None
    if len(stack) != 1:
        raise CEfcError("a block is not closed")
    return root.blocks


@dataclass(frozen=True)
class SGraph:
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
        points = self.points
        if x <= points[0][0] or len(points) == 1:
            return points[0][1]
        if x >= points[-1][0]:
            return points[-1][1]
        k = bisect.bisect_right([point[0] for point in points], x) - 1
        left, right = points[k], points[k + 1]
        if right[0] - left[0] < 1e-9:
            return right[1]
        if self.linear:
            return left[1] + (right[1] - left[1]) * (x - left[0]) / (right[0] - left[0])
        return _bezier_y(left, right, x)


def _bezier_y(start_point: tuple, end_point: tuple, x: float) -> float:
    """y at ``x`` on the span from ``start_point`` (leaving by its out-tangent) to ``end_point`` (arriving by its in-tangent)."""
    xs = (start_point[0], start_point[0] + start_point[4], end_point[0] + end_point[2], end_point[0])
    ys = (start_point[1], start_point[1] + start_point[5], end_point[1] + end_point[3], end_point[1])

    def curve(control_values: tuple, fraction: float) -> float:
        remaining = 1.0 - fraction
        return (
            remaining * remaining * remaining * control_values[0]
            + 3 * remaining * remaining * fraction * control_values[1]
            + 3 * remaining * fraction * fraction * control_values[2]
            + fraction * fraction * fraction * control_values[3]
        )

    samples = [curve(xs, k / _BEZIER_STEPS) for k in range(_BEZIER_STEPS + 1)]
    k = max(0, min(bisect.bisect_right(samples, x) - 1, _BEZIER_STEPS - 1))
    lo, hi = samples[k], samples[k + 1]
    fraction = (k + ((x - lo) / (hi - lo) if hi - lo > 1e-12 else 0.0)) / _BEZIER_STEPS
    return curve(ys, fraction)


def graph_of(block: SBlock) -> SGraph:
    points = []
    for point_block in block.blocks:
        if point_block.name == "Point":
            xy, in_tangent, out_tangent = (point_block.numbers(key) + (0.0, 0.0) for key in ("p", "i", "o"))
            points.append((xy[0], xy[1], in_tangent[0], in_tangent[1], out_tangent[0], out_tangent[1]))
    return SGraph(block.number("Minimum"), block.number("Maximum", 1.0), block.number("Single", 1.0) != 0,
                 block.number("Value"), block.number("Linear", 1.0) != 0, tuple(sorted(points)))  # fmt: skip


@dataclass(frozen=True)
class SColors:
    """A colour list: (time 0..1, (r, g, b, a) 0..1) in order; white without keys."""

    keys: tuple[tuple[float, tuple[float, float, float, float]], ...] = ()

    def at(self, progress: float) -> tuple[float, float, float, float]:
        keys = self.keys
        if not keys:
            return (1.0, 1.0, 1.0, 1.0)
        if progress <= keys[0][0]:
            return keys[0][1]
        for (time_from, color_from), (time_to, color_to) in zip(keys, keys[1:], strict=False):
            if progress <= time_to:
                blend = (progress - time_from) / (time_to - time_from) if time_to - time_from > 1e-9 else 1.0
                return tuple(x + (y - x) * blend for x, y in zip(color_from, color_to, strict=True))  # type: ignore[return-value]
        return keys[-1][1]


def colors_of(block: SBlock) -> SColors:
    keys = [
        (color_block.number("Time"), (color_block.numbers("RGBA") + (1.0,) * 4)[:4])
        for color_block in block.blocks
        if color_block.name == "Color"
    ]
    return SColors(tuple(sorted(keys, key=lambda k: k[0])))


@dataclass(frozen=True)
class SEmitter:
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
    block: SBlock
    graphs: dict[str, SGraph]
    colors: dict[str, SColors]

    def graph(self, name: str, default: float = 0.0) -> SGraph:
        return self.graphs.get(name) or SGraph(value=default)

    def number(self, name: str, default: float = 0.0) -> float:
        return self.block.number(name, default)


def _emitter(block: SBlock) -> SEmitter:
    graphs, colors = {}, {}
    for inner in block.blocks:
        if any(child.name == "Color" for child in inner.blocks):
            colors[inner.name] = colors_of(inner)
        elif "Minimum" in inner.fields or "Single" in inner.fields:
            graphs[inner.name] = graph_of(inner)
    textures = block.block("Textures")
    texture = block.text("Texture") or block.text("Texture0") or (textures.text("Texture000") if textures else "")
    blends = {block.text("SrcBlend").upper(), block.text("DestBlend").upper()}
    return SEmitter(
        block.name, block.text("Name"), block.number("StartDelay"), block.number("Duration", 1000.0), block.number("Loop") != 0,
        _vector(block.numbers("Position"), (0.0, 0.0, 0.0)), _vector(block.numbers("Rotation"), (0.0, 0.0, 0.0, 1.0)),
        _vector(block.numbers("ScaleFactors"), (1.0, 1.0, 1.0)), texture.replace("\\", "/"),
        _vector(block.numbers("Rect"), (0.0, 1.0, 1.0, 0.0)), "ONE" in blends, block, graphs, colors,
    )  # fmt: skip


def _vector(values: tuple[float, ...], default: tuple) -> tuple:
    return values if len(values) == len(default) else default


def read_efc(text: str) -> list[SEmitter]:
    """The emitters of a 1701 effect file, in file order. Raises ``CEfcError`` for a file without an ``Effect``."""
    effect = next((emitter_block for emitter_block in parse(text) if emitter_block.name == "Effect"), None)
    if effect is None:
        raise CEfcError("no Effect block")
    return [_emitter(emitter_block) for emitter_block in effect.blocks]


def is_efc1701(text: str) -> bool:
    """Whether an effect file's text is 1701's (braces) rather than 2070's (XML)."""
    return text.lstrip().startswith("Effect")
