"""Anno 2070's particle effect files (``.efc``) read into curves and settings (engine-particles "Particles").

An ``.efc`` is a run of ``<Effect0>``, ``<Effect1>`` ... blocks (no root element), each one emitter of a
kind (``EffectType``: 1 particle, 2 model, 3 billboard, 4 line, 5 mass, 6 arrow, 7 flame). Its numbers are
plain tags, or curves: ``<XValueMin/Max/Def>``, ``<XVarianz>``, ``<XSize>`` and ``Size`` knots
``<XKnotPos_i>``, ``<XKnotX_i>`` ... ``<XKnotW_i>`` (``i`` counts in hex: 0 to 9, a, b ...).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

PARTICLE, MODEL, BILLBOARD, LINE, MASS, ARROW, FLAME = 1, 2, 3, 4, 5, 6, 7
KIND_NAMES = {1: "particle", 2: "model", 3: "billboard", 4: "line", 5: "mass", 6: "arrow", 7: "flame"}

BLENDING_1404 = {
    1: 1
}  # 1404's Blending 1 is taken as 2070's Technique 1, additive (docs/particle-editor.md); the other numbers are not known
_BLOCK = re.compile(r"<Effect(\d+)>(.*?)</Effect\1>", re.S)
_TAG = re.compile(r"<(\w+)>([^<]*)</\1>")
CURVE_PART = re.compile(r"^(\w+?)(ValueMin|ValueMax|ValueDef|Varianz|Size)$")
KNOT = re.compile(r"^(\w+?)Knot(Pos|X|Y|Z|W)_([0-9a-f]+)$")


@dataclass
class SKnot:
    position: float  # 0..1 along the curve's domain
    value: tuple[float, float, float, float]  # normalised: value = min + knot * (max - min)


@dataclass
class SCurve:
    """A keyframed number: normalised knots between ``minimum`` and ``maximum``, varied per particle by
    +-``variance``. Without knots the curve is the constant ``default``."""

    minimum: float = 0.0
    maximum: float = 1.0
    default: float = 0.0
    variance: float = 0.0
    knots: list[SKnot] = field(default_factory=list)

    def normalised(self, position: float) -> tuple[float, float, float, float]:
        """The knots' value at ``t``: linear between knots, held before the first and after the last."""
        knots = self.knots
        if not knots:
            value = self.default
            if self.maximum != self.minimum:
                value = (self.default - self.minimum) / (self.maximum - self.minimum)
            return (value, value, value, value)
        if position <= knots[0].position:
            return knots[0].value
        for start, end in zip(knots, knots[1:], strict=False):
            if position <= end.position:
                span = end.position - start.position
                blend = 0.0 if span <= 0 else (position - start.position) / span
                return tuple(x + (y - x) * blend for x, y in zip(start.value, end.value, strict=True))  # type: ignore[return-value]
        return knots[-1].value

    def at(self, position: float, channel: int = 0) -> float:
        """The value at ``t`` (channel 0..3 of the knots) in the curve's own range; a curve without knots is
        its default as it stands."""
        if not self.knots:
            return self.default
        normalised_value = self.normalised(position)[channel]
        return self.minimum + normalised_value * (self.maximum - self.minimum)


@dataclass
class SEffect:
    """One emitter of an ``.efc``: its header, its curves by name (``Scale``, ``VelocityY``, ``Alpha`` ...)
    and its other plain values (``AtlasTilingU``, ``Alignment``, ``WindDependency`` ...)."""

    index: int
    name: str
    kind: int
    texture: str
    shader: str
    technique: int
    delay_ms: float
    life_ms: float
    duration_ms: float
    loop: bool
    bound: bool
    offset: tuple[float, float, float]
    curves: dict[str, SCurve] = field(default_factory=dict)
    values: dict[str, str] = field(default_factory=dict)

    def number(self, name: str, default: float = 0.0) -> float:
        try:
            value = float(self.values[name])
        except (KeyError, ValueError):
            return default
        return value if math.isfinite(value) else default  # ``int(nan)`` and ``int(inf)`` would raise

    def curve(self, name: str) -> SCurve | None:
        return self.curves.get(name)


def _float(text: str, default: float = 0.0) -> float:
    try:
        value = float(text)
    except ValueError:
        return default
    return value if math.isfinite(value) else default


def _technique(values: dict[str, str]) -> int:
    """2070's ``Technique``; 1404 writes ``Blending`` in its place (``BLENDING_1404``)."""
    if "Technique" in values:
        return int(_float(values.pop("Technique")))
    return BLENDING_1404.get(int(_float(values.get("Blending", "0"))), 0)


def _curves_of(tags: dict[str, str]) -> tuple[dict[str, SCurve], set[str]]:
    """The curves among an effect's tags and the tag names they used."""
    parts: dict[str, dict[str, str]] = {}
    knots: dict[str, dict[int, dict[str, float]]] = {}
    used: set[str] = set()
    for tag, text in tags.items():
        part_match = CURVE_PART.match(tag)
        if part_match:
            parts.setdefault(part_match.group(1), {})[part_match.group(2)] = text
            used.add(tag)
            continue
        knot_match = KNOT.match(tag)
        if knot_match:
            knots.setdefault(knot_match.group(1), {}).setdefault(int(knot_match.group(3), 16), {})[
                knot_match.group(2)
            ] = _float(text)
            used.add(tag)
    curves = {}
    for name, part in parts.items():
        if "Size" not in part:
            used.difference_update(
                f"{name}{suffix}" for suffix in part
            )  # a plain tag that only looks like a curve part
            continue
        curve = SCurve(
            _float(part.get("ValueMin", "0")),
            _float(part.get("ValueMax", "1")),
            _float(part.get("ValueDef", "0")),
            _float(part.get("Varianz", "0")),
        )
        for _, knot_fields in sorted(knots.get(name, {}).items()):
            knot_value = tuple(knot_fields.get(axis, 0.0) for axis in "XYZW")
            curve.knots.append(SKnot(knot_fields.get("Pos", 0.0), knot_value))  # type: ignore[arg-type]
        curve.knots.sort(key=lambda knot: knot.position)
        curves[name] = curve
    return curves, used


def parse_efc(text: str) -> list[SEffect]:
    """The effects of an ``.efc`` file, in file order."""
    out = []
    for block in _BLOCK.finditer(text):
        tags = {tag_match.group(1): tag_match.group(2).strip() for tag_match in _TAG.finditer(block.group(2))}
        curves, used = _curves_of(tags)
        values = {tag_name: tag_value for tag_name, tag_value in tags.items() if tag_name not in used}
        out.append(
            SEffect(
                index=int(block.group(1)),
                name=values.pop("Name", ""),
                kind=int(_float(values.pop("EffectType", "0"))),
                texture=values.pop("Texture0", ""),
                shader=values.pop("Shader", ""),
                technique=_technique(values),
                delay_ms=_float(values.pop("Delay", "0")),
                life_ms=_float(values.pop("LifeTime", "0")),
                duration_ms=_float(values.pop("Duration", "0")),
                loop=values.pop("Loop", "0") == "1",
                bound=values.pop("Bound", "0") == "1",
                offset=tuple(_float(values.pop(f"Offset{axis}", "0")) for axis in "XYZ"),  # type: ignore[arg-type]
                curves=curves,
                values=values,
            )
        )
    return out
