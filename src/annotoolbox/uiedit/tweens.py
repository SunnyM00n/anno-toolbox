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
from . import animations, bindings, elements, fields, scopevalues
from .library import CLibrary

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
class STween:
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
        value = self.values.get(name)
        return default if value is None else value

    @property
    def target(self) -> tuple[int, ...]:
        """The property path the tween writes: a built-in of the instance, or the Animator's own CurrentValue."""
        builtin = TARGETS[self.kind][2]
        return (builtin,) if builtin is not None else (self.component, self.uids.get("CurrentValue", 0))

    def fraction(self, time_s: float, eases: dict[int, str]) -> float:
        """How far the tween is at ``time_s`` seconds after it was started (0 start value, 1 end value)."""
        time_s -= float(self.get("Delay", 0.0))
        duration = float(self.get("Duration", 0.0))
        if time_s <= 0 or duration <= 0:
            return 0.0 if time_s <= 0 else 1.0
        yoyo = bool(self.get("Yoyo", False))
        loops = int(self.get("LoopCount", 1))
        period = duration * (2 if yoyo else 1)
        if loops >= 0 and time_s >= period * max(loops, 1):
            return 0.0 if yoyo else 1.0
        phase = time_s % period
        position = phase / duration if phase <= duration else 2.0 - phase / duration
        kind = self.get("EaseType", "Linear")
        return animations.ease(kind if isinstance(kind, str) else eases.get(kind, "Linear"), position)

    def value_at(self, time_s: float, eases: dict[int, str], stored=None):
        start_name, end_name, _ = TARGETS[self.kind]
        start, end = self.get(start_name), self.get(end_name)
        if bool(self.get("StartFromCurrentValue", False)) and stored is not None:
            start = stored
        if start is None or end is None:
            return None
        value = animations.lerp(start, end, self.fraction(time_s, eases))
        if isinstance(value, tuple) and isinstance(stored, (tuple, list)):  # the axes it does not tween stay
            axis_enabled = [bool(self.get(f"TweenOn{axis_name}", axis_name != "Z")) for axis_name in "XYZ"]
            value = tuple(
                axis if i >= len(axis_enabled) or axis_enabled[i] else float(stored[i]) for i, axis in enumerate(value)
            )
        return value


RUN = "tweens"  # the timeline's key for "the file's tweens, all started at 0"


@dataclass
class SRun:
    """The file's tweens all started at once, as the timeline offers an animation."""

    tweens: list[STween]
    name: str = "The file's tweens, started together"
    guid: str = RUN
    tracks: tuple = ()

    @property
    def seconds(self) -> float:
        def length(tween: STween) -> float:
            loops = int(tween.get("LoopCount", 1))
            cycles = max(loops, 1) if loops >= 0 else 2  # an endless tween: two rounds on the timeline
            return float(tween.get("Delay", 0.0)) + float(tween.get("Duration", 0.0)) * cycles * (
                2 if tween.get("Yoyo") else 1
            )

        return max((length(tween) for tween in self.tweens), default=0.0)


def _defaults(load: Callable[[str], dict | None], guid: str) -> dict[int, object]:
    root = load(guid)
    out = {}
    for custom_property in bindings.custom_properties(root) if root is not None else []:
        property_data = bindings.property_info(custom_property)
        if isinstance(property_data["default"], tuple):
            out[property_data["uid"]] = property_data["default"][1]
    return out


def _kind(library: CLibrary, guid: str) -> str | None:
    chain = library.chain(guid)
    if not chain or chain[-1].guid != TWEEN:
        return None
    return next((i.name for i in chain if i.name in TARGETS), None)


def read(root: dict, library: CLibrary, load: Callable[[str], dict | None]) -> list[STween]:
    """The tweens on the instances of ``root``, in element order."""
    out = []
    for element in elements.tree(root).walk():
        if not element.guid:
            continue
        given = fields.property_values(element.node)
        for component in phoenixedit.objects(element.node, "Components"):
            template = fields.get(component, "ComponentTemplate") or ""
            kind = _kind(library, template)
            if kind is None:
                continue
            uid = int(fields.get(component, "UniqueId", 0) or 0)
            tween = STween(
                kind, element.guid, element.node, uid, f"{element.name}.{phoenixedit.object_name(component) or kind}"
            )
            defaults = {**_defaults(load, TWEEN), **_defaults(load, template)}
            for library_property in library.properties_of(template):
                tween.uids[library_property.name], tween.types[library_property.name] = (
                    library_property.uid,
                    library_property.ptype,
                )
                value = given.get(
                    (uid, library_property.uid), defaults.get(library_property.uid, library_property.default)
                )
                tween.values[library_property.name] = tuple(value) if isinstance(value, list) else value
            out.append(tween)
    return out


def writes(tweens: list[STween], time_s: float, eases: dict[int, str]) -> dict[str, dict[tuple[int, ...], object]]:
    """What the tweens write at ``time_s`` seconds, by instance GUID and property path (``Scope.writes``' shape)."""
    out: dict[str, dict[tuple[int, ...], object]] = {}
    for tween in tweens:
        target = tween.target
        stored = scopevalues.builtin_value(tween.node, target[0]) if len(target) == 1 else None
        value = tween.value_at(time_s, eases, stored)
        if value is not None:
            out.setdefault(tween.instance, {})[target] = value
    return out
