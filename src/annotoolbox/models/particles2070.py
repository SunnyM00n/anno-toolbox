"""Anno 2070's particle effect files (``.efc``) read into curves and settings (engine-particles "Particles").

An ``.efc`` is a run of ``<Effect0>``, ``<Effect1>`` ... blocks (no root element), each one emitter of a
kind (``EffectType``: 1 particle, 2 model, 3 billboard, 4 line, 5 mass, 6 arrow, 7 flame). Its numbers are
plain tags, or curves: ``<XValueMin/Max/Def>``, ``<XVarianz>``, ``<XSize>`` and ``Size`` knots
``<XKnotPos_i>``, ``<XKnotX_i>`` ... ``<XKnotW_i>`` (``i`` counts in hex: 0 to 9, a, b ...).
"""

from __future__ import annotations

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
class Knot:
    pos: float  # 0..1 along the curve's domain
    value: tuple[float, float, float, float]  # normalised: value = min + knot * (max - min)


@dataclass
class Curve:
    """A keyframed number: normalised knots between ``minimum`` and ``maximum``, varied per particle by
    +-``variance``. Without knots the curve is the constant ``default``."""

    minimum: float = 0.0
    maximum: float = 1.0
    default: float = 0.0
    variance: float = 0.0
    knots: list[Knot] = field(default_factory=list)

    def normalised(self, t: float) -> tuple[float, float, float, float]:
        """The knots' value at ``t``: linear between knots, held before the first and after the last."""
        knots = self.knots
        if not knots:
            n = self.default
            if self.maximum != self.minimum:
                n = (self.default - self.minimum) / (self.maximum - self.minimum)
            return (n, n, n, n)
        if t <= knots[0].pos:
            return knots[0].value
        for a, b in zip(knots, knots[1:], strict=False):
            if t <= b.pos:
                span = b.pos - a.pos
                k = 0.0 if span <= 0 else (t - a.pos) / span
                return tuple(x + (y - x) * k for x, y in zip(a.value, b.value, strict=True))  # type: ignore[return-value]
        return knots[-1].value

    def at(self, t: float, channel: int = 0) -> float:
        """The value at ``t`` (channel 0..3 of the knots) in the curve's own range; a curve without knots is
        its default as it stands."""
        if not self.knots:
            return self.default
        n = self.normalised(t)[channel]
        return self.minimum + n * (self.maximum - self.minimum)


@dataclass
class Effect:
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
    curves: dict[str, Curve] = field(default_factory=dict)
    values: dict[str, str] = field(default_factory=dict)

    def number(self, name: str, default: float = 0.0) -> float:
        try:
            return float(self.values[name])
        except (KeyError, ValueError):
            return default

    def curve(self, name: str) -> Curve | None:
        return self.curves.get(name)


def _float(text: str, default: float = 0.0) -> float:
    try:
        return float(text)
    except ValueError:
        return default


def _technique(values: dict[str, str]) -> int:
    """2070's ``Technique``; 1404 writes ``Blending`` in its place (``BLENDING_1404``)."""
    if "Technique" in values:
        return int(_float(values.pop("Technique")))
    return BLENDING_1404.get(int(_float(values.get("Blending", "0"))), 0)


def _curves_of(tags: dict[str, str]) -> tuple[dict[str, Curve], set[str]]:
    """The curves among an effect's tags and the tag names they used."""
    parts: dict[str, dict[str, str]] = {}
    knots: dict[str, dict[int, dict[str, float]]] = {}
    used: set[str] = set()
    for tag, text in tags.items():
        m = CURVE_PART.match(tag)
        if m:
            parts.setdefault(m.group(1), {})[m.group(2)] = text
            used.add(tag)
            continue
        k = KNOT.match(tag)
        if k:
            knots.setdefault(k.group(1), {}).setdefault(int(k.group(3), 16), {})[k.group(2)] = _float(text)
            used.add(tag)
    curves = {}
    for name, p in parts.items():
        if "Size" not in p:
            used.difference_update(f"{name}{s}" for s in p)  # a plain tag that only looks like a curve part
            continue
        curve = Curve(
            _float(p.get("ValueMin", "0")),
            _float(p.get("ValueMax", "1")),
            _float(p.get("ValueDef", "0")),
            _float(p.get("Varianz", "0")),
        )
        for _, k in sorted(knots.get(name, {}).items()):
            v = tuple(k.get(c, 0.0) for c in "XYZW")
            curve.knots.append(Knot(k.get("Pos", 0.0), v))  # type: ignore[arg-type]
        curve.knots.sort(key=lambda kn: kn.pos)
        curves[name] = curve
    return curves, used


def parse_efc(text: str) -> list[Effect]:
    """The effects of an ``.efc`` file, in file order."""
    out = []
    for m in _BLOCK.finditer(text):
        tags = {t.group(1): t.group(2).strip() for t in _TAG.finditer(m.group(2))}
        curves, used = _curves_of(tags)
        values = {k: v for k, v in tags.items() if k not in used}
        out.append(
            Effect(
                index=int(m.group(1)),
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
                offset=tuple(_float(values.pop(f"Offset{c}", "0")) for c in "XYZ"),  # type: ignore[arg-type]
                curves=curves,
                values=values,
            )
        )
    return out
