"""The tween components of a template's instances (Animator, AlphaTween, ScaleTween, PositionTween, RotationTween)
as the preview plays them: start and end values, duration, delay, ease, yoyo and loops, and the value each writes at
a time.

``read(root, library, load)`` lists the tweens; ``writes(tweens, seconds)`` gives the values for the preview
(``layout.Context.tweens``). The rules are the Widget Library's descriptions (docs/ui/117/components.md "Tween").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats import phoenixedit
from . import animations, bindings, elements, fields, scope
from .library import Library

TWEEN = "a91c68a5-9803-4016-af14-d28b054325d2"  # the base every tween component extends
# a tween's start and end property names and the built-in property it drives (None: its own CurrentValue)
TARGETS = {
    "Animator": ("StartValue", "EndValue", None),
    "AlphaTween": ("StartAlpha", "EndAlpha", 0x80000006),
    "ScaleTween": ("StartScale", "EndScale", 0x80000035),
    "PositionTween": ("StartPosition", "EndPosition", 0x80000001),
    "RotationTween": ("StartRotationAngle", "EndRotationAngle", 0x80000003),
}


@dataclass
class Tween:
    """One tween component on one instance, its values as the instance gives them (else the template's)."""

    kind: str  # a key of TARGETS
    instance: str  # the instance's GUID
    node: dict  # the instance
    component: int  # the component's UniqueId on the instance
    name: str
    uids: dict[str, int] = field(default_factory=dict)  # property name -> UniqueId
    values: dict[str, object] = field(default_factory=dict)  # property name -> value
    types: dict[str, int] = field(default_factory=dict)  # property name -> property type

    def get(self, name: str, default=None):
        v = self.values.get(name)
        return default if v is None else v

    @property
    def target(self) -> tuple[int, ...]:
        """The property path the tween writes: a built-in of the instance, or the Animator's own CurrentValue."""
        builtin = TARGETS[self.kind][2]
        return (builtin,) if builtin is not None else (self.component, self.uids.get("CurrentValue", 0))

    def fraction(self, t: float, eases: dict[int, str]) -> float:
        """How far the tween is at ``t`` seconds after it was started (0 start value, 1 end value)."""
        t -= float(self.get("Delay", 0.0))
        duration = float(self.get("Duration", 0.0))
        if t <= 0 or duration <= 0:
            return 0.0 if t <= 0 else 1.0
        yoyo = bool(self.get("Yoyo", False))
        loops = int(self.get("LoopCount", 1))
        period = duration * (2 if yoyo else 1)
        if loops >= 0 and t >= period * max(loops, 1):
            return 0.0 if yoyo else 1.0
        phase = t % period
        f = phase / duration if phase <= duration else 2.0 - phase / duration
        kind = self.get("EaseType", "Linear")
        return animations.ease(kind if isinstance(kind, str) else eases.get(kind, "Linear"), f)

    def value_at(self, t: float, eases: dict[int, str], stored=None):
        start_name, end_name, _ = TARGETS[self.kind]
        start, end = self.get(start_name), self.get(end_name)
        if bool(self.get("StartFromCurrentValue", False)) and stored is not None:
            start = stored
        if start is None or end is None:
            return None
        value = animations.lerp(start, end, self.fraction(t, eases))
        if isinstance(value, tuple) and isinstance(stored, (tuple, list)):  # the axes it does not tween stay
            on = [bool(self.get(f"TweenOn{a}", a != "Z")) for a in "XYZ"]
            value = tuple(v if i >= len(on) or on[i] else float(stored[i]) for i, v in enumerate(value))
        return value


RUN = "tweens"  # the timeline's key for "the file's tweens, all started at 0"


@dataclass
class Run:
    """The file's tweens all started at once, as the timeline offers an animation."""

    tweens: list[Tween]
    name: str = "The file's tweens, started together"
    guid: str = RUN
    tracks: tuple = ()

    @property
    def seconds(self) -> float:
        def length(tw: Tween) -> float:
            loops = int(tw.get("LoopCount", 1))
            cycles = max(loops, 1) if loops >= 0 else 2  # an endless tween: two rounds on the timeline
            return float(tw.get("Delay", 0.0)) + float(tw.get("Duration", 0.0)) * cycles * (2 if tw.get("Yoyo") else 1)

        return max((length(tw) for tw in self.tweens), default=0.0)


def _defaults(load: Callable[[str], dict | None], guid: str) -> dict[int, object]:
    root = load(guid)
    out = {}
    for p in bindings.custom_properties(root) if root is not None else []:
        info = bindings.property_info(p)
        if isinstance(info["default"], tuple):
            out[info["uid"]] = info["default"][1]
    return out


def _kind(library: Library, guid: str) -> str | None:
    chain = library.chain(guid)
    if not chain or chain[-1].guid != TWEEN:
        return None
    return next((i.name for i in chain if i.name in TARGETS), None)


def read(root: dict, library: Library, load: Callable[[str], dict | None]) -> list[Tween]:
    """The tweens on the instances of ``root``, in element order."""
    out = []
    for el in elements.tree(root).walk():
        if not el.guid:
            continue
        given = fields.property_values(el.node)
        for comp in phoenixedit.objects(el.node, "Components"):
            tpl = fields.get(comp, "ComponentTemplate") or ""
            kind = _kind(library, tpl)
            if kind is None:
                continue
            uid = int(fields.get(comp, "UniqueId", 0) or 0)
            tw = Tween(kind, el.guid, el.node, uid, f"{el.name}.{phoenixedit.object_name(comp) or kind}")
            defaults = {**_defaults(load, TWEEN), **_defaults(load, tpl)}
            for p in library.properties_of(tpl):
                tw.uids[p.name], tw.types[p.name] = p.uid, p.ptype
                value = given.get((uid, p.uid), defaults.get(p.uid, p.default))
                tw.values[p.name] = tuple(value) if isinstance(value, list) else value
            out.append(tw)
    return out


def writes(tweens: list[Tween], t: float, eases: dict[int, str]) -> dict[str, dict[tuple[int, ...], object]]:
    """What the tweens write at ``t`` seconds, by instance GUID and property path (``Scope.writes``' shape)."""
    out: dict[str, dict[tuple[int, ...], object]] = {}
    for tw in tweens:
        target = tw.target
        stored = scope.builtin_value(tw.node, target[0]) if len(target) == 1 else None
        value = tw.value_at(t, eases, stored)
        if value is not None:
            out.setdefault(tw.instance, {})[target] = value
    return out
