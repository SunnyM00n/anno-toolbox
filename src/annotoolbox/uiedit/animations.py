"""A template's animations as a timeline: each animation's tracks (the binding end a track drives, its keys with
their interpolators) and the value of every track at a time, for the preview and the sequence editor.

``read(root)`` lists the animations; ``Animation.values_at(seconds)`` gives (end index, value) for each track that has
a value then. A track drives the end of the template's one index space (``bindings``) by its
``AnimatedPropertyIndex``; between two keys the earlier key's ``InterpolatorType`` shapes the curve (``ease``; the
numbering is inferred, docs/ui-editor.md "The sequence editor").
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

from ..formats import phoenixedit
from ..infra.i18n import tr
from . import fields

EVENT_TRACKS = {"4fe03177", "45432180", "217350a0"}  # events and actions fire at a key, they hold no value
STEPPED = {"7ab8a12e", "fc284752", "dda23f23", "21fb1be8", "eabeeed5", "376db26f", "5da1d9a7"}  # no blending
INTERPOLATOR = "InterpolatorType"

# The Widget Library's EaseType entries, in its order; a key's interpolator 0 holds, 1 is linear, 2.. these from
# Sine_In on (inferred from the shipped keys: 1 on 5,166 of 6,989, the ease-in-outs next).
EASES = ("Linear Sine_In Sine_Out Sine_InOut Quad_In Quad_Out Quad_InOut Cubic_In Cubic_Out Cubic_InOut Quart_In "
         "Quart_Out Quart_InOut Quint_In Quint_Out Quint_InOut Expo_In Expo_Out Expo_InOut Circ_In Circ_Out "
         "Circ_InOut Back_In Back_Out Back_InOut Elastic_In Elastic_Out Elastic_InOut Bounce_In Bounce_Out "
         "Bounce_InOut").split()  # fmt: skip
INTERPOLATORS = ("Constant", *EASES)


def _bounce_out(f: float) -> float:
    n, d = 7.5625, 2.75
    if f < 1 / d:
        return n * f * f
    if f < 2 / d:
        f -= 1.5 / d
        return n * f * f + 0.75
    if f < 2.5 / d:
        f -= 2.25 / d
        return n * f * f + 0.9375
    f -= 2.625 / d
    return n * f * f + 0.984375


_IN = {
    "Linear": lambda f: f,
    "Sine": lambda f: 1 - math.cos(f * math.pi / 2),
    "Quad": lambda f: f**2,
    "Cubic": lambda f: f**3,
    "Quart": lambda f: f**4,
    "Quint": lambda f: f**5,
    "Expo": lambda f: 0.0 if f <= 0 else 2 ** (10 * f - 10),
    "Circ": lambda f: 1 - math.sqrt(max(0.0, 1 - f * f)),
    "Back": lambda f: 2.70158 * f**3 - 1.70158 * f**2,
    "Elastic": lambda f: f if f in (0.0, 1.0) else -(2 ** (10 * f - 10)) * math.sin((f * 10 - 10.75) * 2 * math.pi / 3),
    "Bounce": lambda f: 1 - _bounce_out(1 - f),
}


def ease(name: str, f: float) -> float:
    """The eased fraction of ``f`` (0..1) by an EaseType name (``Quad_InOut``); an unknown name is linear."""
    f = min(1.0, max(0.0, f))
    family, _, way = name.partition("_")
    curve = _IN.get(family, _IN["Linear"])
    if way == "Out":
        return 1 - curve(1 - f)
    if way == "InOut":
        return curve(2 * f) / 2 if f < 0.5 else 1 - curve(2 - 2 * f) / 2
    return curve(f)


def interpolator_name(kind: int) -> str:
    return INTERPOLATORS[kind] if 0 <= kind < len(INTERPOLATORS) else f"({kind})"


@dataclass
class Key:
    time: float
    value: object
    interpolator: int = 1
    node: dict | None = None


@dataclass
class Track:
    end: int
    kind: str  # the track's type hash
    keys: list[Key] = field(default_factory=list)
    node: dict | None = None

    @property
    def is_event(self) -> bool:
        return self.kind in EVENT_TRACKS

    def value_at(self, t: float):
        """The track's value at ``t`` seconds: the first key's before it, the last's after it, eased between."""
        if not self.keys or self.is_event:
            return None
        if t <= self.keys[0].time:
            return self.keys[0].value
        for k0, k1 in zip(self.keys, self.keys[1:], strict=False):
            if k0.time <= t <= k1.time:
                if self.kind in STEPPED or k1.time <= k0.time or k0.interpolator == 0:
                    return k0.value if t < k1.time else k1.value
                f = ease(interpolator_name(k0.interpolator), (t - k0.time) / (k1.time - k0.time))
                return lerp(k0.value, k1.value, f)
        return self.keys[-1].value


def lerp(a, b, f: float):
    """``a`` to ``b`` by ``f``: numbers and vectors blend, anything else switches at the middle."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return a + (b - a) * f
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        return tuple(lerp(x, y, f) for x, y in zip(a, b, strict=True))
    return a if f < 0.5 else b


@dataclass
class Animation:
    node: dict
    name: str
    guid: str | None
    frames: int
    rate: int
    tracks: list[Track]

    @property
    def seconds(self) -> float:
        return self.frames / self.rate if self.rate else 0.0

    def values_at(self, t: float) -> list[tuple[int, object]]:
        return [(track.end, v) for track in self.tracks if (v := track.value_at(t)) is not None]


def key_value(key: dict):
    """A key's value (a colour key's as RGB)."""
    color = phoenixedit.objects(key, "Value")
    if color:  # a colour key holds a ColorType
        found = fields.color_of(key, "Value")
        return found[2] if found else None
    return fields.get(key, "Value")


def read_key(k: dict) -> Key:
    return Key(float(fields.get(k, "Time", 0.0) or 0.0), key_value(k), int(fields.get(k, INTERPOLATOR, 1) or 0), k)


def read(root: dict) -> list[Animation]:
    """The animations of a template, each with its tracks."""
    out = []
    for a in phoenixedit.objects(root, "AnimationInstances"):
        tracks = []
        for node in phoenixedit.objects(a, "Tracks"):
            keys = [read_key(k) for k in phoenixedit.objects(node, "Track")]
            tracks.append(
                Track(int(fields.get(node, "AnimatedPropertyIndex", 0) or 0), f"{node['type']:08x}", keys, node)
            )
        guid = str(uuid.UUID(bytes_le=a["guid"])) if a.get("guid") else None
        out.append(Animation(a, phoenixedit.object_name(a) or tr("ui.animation"), guid,
                             int(fields.get(a, "Duration", 0) or 0), int(fields.get(a, "FrameRate", 60) or 60), tracks))  # fmt: skip
    return out
