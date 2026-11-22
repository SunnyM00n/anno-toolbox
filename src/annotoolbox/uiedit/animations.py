"""A template's animations as a timeline: each animation's tracks (the binding end a track drives, its keys with
their interpolators) and the value of every track at a time, for the preview and the sequence editor.

``read(root)`` lists the animations; ``SAnimation.values_at(seconds)`` gives (end index, value) for each track that has
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


def _bounce_out(fraction: float) -> float:
    amplitude, divisor = 7.5625, 2.75
    if fraction < 1 / divisor:
        return amplitude * fraction * fraction
    if fraction < 2 / divisor:
        fraction -= 1.5 / divisor
        return amplitude * fraction * fraction + 0.75
    if fraction < 2.5 / divisor:
        fraction -= 2.25 / divisor
        return amplitude * fraction * fraction + 0.9375
    fraction -= 2.625 / divisor
    return amplitude * fraction * fraction + 0.984375


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


def ease(name: str, fraction: float) -> float:
    """The eased fraction of ``f`` (0..1) by an EaseType name (``Quad_InOut``); an unknown name is linear."""
    fraction = min(1.0, max(0.0, fraction))
    family, _, way = name.partition("_")
    curve = _IN.get(family, _IN["Linear"])
    if way == "Out":
        return 1 - curve(1 - fraction)
    if way == "InOut":
        return curve(2 * fraction) / 2 if fraction < 0.5 else 1 - curve(2 - 2 * fraction) / 2
    return curve(fraction)


def interpolator_name(kind: int) -> str:
    return INTERPOLATORS[kind] if 0 <= kind < len(INTERPOLATORS) else f"({kind})"


@dataclass
class SKey:
    time: float
    value: object
    interpolator: int = 1
    node: dict | None = None


@dataclass
class STrack:
    end: int
    kind: str  # the track's type hash
    keys: list[SKey] = field(default_factory=list)
    node: dict | None = None

    @property
    def is_event(self) -> bool:
        return self.kind in EVENT_TRACKS

    def value_at(self, time_s: float):
        """The track's value at ``t`` seconds: the first key's before it, the last's after it, eased between."""
        if not self.keys or self.is_event:
            return None
        keys = sorted(self.keys, key=lambda k: k.time)  # a key dragged past its neighbour keeps its stored place
        if time_s <= keys[0].time:
            return keys[0].value
        for previous_key, next_key in zip(keys, keys[1:], strict=False):
            if previous_key.time <= time_s <= next_key.time:
                if self.kind in STEPPED or next_key.time <= previous_key.time or previous_key.interpolator == 0:
                    return previous_key.value if time_s < next_key.time else next_key.value
                eased = ease(
                    interpolator_name(previous_key.interpolator),
                    (time_s - previous_key.time) / (next_key.time - previous_key.time),
                )
                return lerp(previous_key.value, next_key.value, eased)
        return keys[-1].value


def lerp(start, end, fraction: float):
    """``a`` to ``b`` by ``f``: numbers and vectors blend, anything else switches at the middle."""
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and not isinstance(start, bool):
        return start + (end - start) * fraction
    if isinstance(start, tuple) and isinstance(end, tuple) and len(start) == len(end):
        return tuple(lerp(x, y, fraction) for x, y in zip(start, end, strict=True))
    return start if fraction < 0.5 else end


@dataclass
class SAnimation:
    node: dict
    name: str
    guid: str | None
    frames: int
    rate: int
    tracks: list[STrack]

    @property
    def seconds(self) -> float:
        return self.frames / self.rate if self.rate else 0.0

    def values_at(self, time_s: float) -> list[tuple[int, object]]:
        return [(track.end, value) for track in self.tracks if (value := track.value_at(time_s)) is not None]


def key_value(key: dict):
    """A key's value (a colour key's as RGB)."""
    color = phoenixedit.objects(key, "Value")
    if color:  # a colour key holds a ColorType
        found = fields.color_of(key, "Value")
        return found[2] if found else None
    return fields.get(key, "Value")


def read_key(k: dict) -> SKey:
    return SKey(float(fields.get(k, "Time", 0.0) or 0.0), key_value(k), int(fields.get(k, INTERPOLATOR, 1) or 0), k)


def read(root: dict) -> list[SAnimation]:
    """The animations of a template, each with its tracks."""
    out = []
    for animation in phoenixedit.objects(root, "AnimationInstances"):
        tracks = []
        for node in phoenixedit.objects(animation, "Tracks"):
            keys = [read_key(k) for k in phoenixedit.objects(node, "Track")]
            tracks.append(
                STrack(int(fields.get(node, "AnimatedPropertyIndex", 0) or 0), f"{node['type']:08x}", keys, node)
            )
        guid = str(uuid.UUID(bytes_le=animation["guid"])) if animation.get("guid") else None
        out.append(SAnimation(animation, phoenixedit.object_name(animation) or tr("ui.animation"), guid,
                             int(fields.get(animation, "Duration", 0) or 0), int(fields.get(animation, "FrameRate", 60) or 60), tracks))  # fmt: skip
    return out
