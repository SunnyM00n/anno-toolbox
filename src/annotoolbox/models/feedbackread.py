"""A feedback file (``.fc``) read for playing (``models.feedbackplay``): its dummies with their height rule and
groups, its splines, and per ``FeedbackConfigs`` item the actor it makes - units, counts, chance, loops per state of
the object and its barriers. The newer games' floats and 2070's fixed point both.

``read(root)`` (an ``isd2070`` tree) gives a ``Feedback``. The fields: docs/engine-animation.md "Feedback"; their
rules: docs/engine-cloth.md "The walking figures".
"""

from __future__ import annotations

import math
import struct
from collections import Counter
from dataclasses import dataclass, field

from ..formats import isd2070

KEEP_ON_GROUND = 3  # HeightAdaptationMode: the dummy's height above the object's, never below the ground


@dataclass(frozen=True)
class Dummy:
    """A named point: its position in the object (as stored), its turn about +y, its ``HeightAdaptationMode``."""

    name: str
    id: int
    position: tuple[float, float, float]
    heading: float
    height_mode: int = 0

    def placed(self, ground: float = 0.0, origin: float = 0.0) -> tuple[float, float, float]:
        """Where an actor stands at it over ground at ``ground`` with the object at height ``origin`` (the view's: 0
        both): every mode adds the dummy's height to its base (the object, the ground, the walkable surface), mode 3
        keeps it above the ground; the ground and the surface are the view's one ground."""
        x, y, z = self.position
        base = origin if self.height_mode in (0, KEEP_ON_GROUND) else ground
        height = base + y
        return (x, max(height, ground) if self.height_mode == KEEP_ON_GROUND else height, z)


@dataclass(frozen=True)
class Spline:
    name: str
    id: int
    points: tuple[tuple[float, float, float], ...]


@dataclass(frozen=True)
class Loop:
    """One loop of a sequence definition: where the actor starts (a dummy, else one of a group's), whether it shows,
    the elements."""

    dummy: str = ""
    dummy_id: int = 0
    visible: bool = True
    elements: tuple[isd2070.Node, ...] = ()
    start_group: str = ""
    reset: bool = False  # ``ResetToDefaultEveryLoop``: every pass starts again from the default state
    fade: bool = False  # ``FadeVisibility``: the default visibility is reached by a fade
    sequence: int = 0  # ``SequenceID``: what the actor plays from the start until an element picks one


@dataclass(frozen=True)
class Actor:
    """A ``FeedbackConfigs`` item: the unit assets ((GUID, variation) pairs, group names), per state the definition's
    loops (enter, repeat, leave), how many actors it makes and its barrier elements by id (every definition and
    loop)."""

    units: tuple[tuple[int, int], ...]
    groups: tuple[str, ...]
    states: dict[int, tuple[Loop, ...]]
    main_object: bool = False
    actor_count: int = 1
    max_count: int = 1
    chance: int = 100
    by_dummies: str = ""  # MultiplyActorByDummyCount: one actor per dummy of this group
    always_visible: bool = False
    scale_speed: bool = True  # ApplyScaleToMovementSpeed
    barriers: Counter = field(default_factory=Counter)
    node: isd2070.Node | None = None


@dataclass
class Feedback:
    dummies: dict[str, Dummy] = field(default_factory=dict)
    by_id: dict[int, Dummy] = field(default_factory=dict)
    splines: dict[str, Spline] = field(default_factory=dict)
    actors: list[Actor] = field(default_factory=list)
    groups: dict[str, tuple[Dummy, ...]] = field(default_factory=dict)  # by name
    group_paths: dict[tuple[int, ...], tuple[Dummy, ...]] = field(default_factory=dict)  # by indexes from the root

    def dummy(self, name: str = "", ident: int = 0) -> Dummy | None:
        return self.by_id.get(ident) if ident else self.dummies.get(name)

    def group_dummy(self, node: isd2070.Node | None, rng) -> Dummy | None:
        """A random dummy of the group an element's ``DummyGroup`` names (its count, then the group's index at each
        depth; -1 none), drawn again each time; None when it names none."""
        if node is None or not node.data or len(node.data) < 8:
            return None
        words = struct.unpack(f"<{len(node.data) // 4}i", node.data[: len(node.data) // 4 * 4])
        if not 0 < words[0] < len(words):
            return None
        found = self.group_paths.get(tuple(words[1 : 1 + words[0]]), ())
        return found[rng.randrange(len(found))] if found else None


def element_kind(e: isd2070.Node) -> int:
    return e.int("elementType", e.int("m_ElementType", -1))


def _vec3(node: isd2070.Node | None) -> tuple[float, float, float] | None:
    """A position: x, y, z children (the newer games) or 2070's three fixed-point words (x, z, height)."""
    if node is None:
        return None
    if node.data is not None and len(node.data) >= 24:
        x, z, h = (v / isd2070.FIXED for v in struct.unpack_from("<3q", node.data))
        return x, h, z
    if node.find("x") is not None:
        return tuple(node.float(a) for a in "xyz")  # type: ignore[return-value]
    return None


def _heading(node: isd2070.Node | None) -> float:
    """The turn about +y of an orientation: w, x, y, z children or 2070's four floats (x, y, z, w)."""
    if node is None:
        return 0.0
    if node.data is not None and len(node.data) >= 16:
        _x, y, _z, w = struct.unpack_from("<4f", node.data)
    else:
        y, w = node.float("y"), node.float("w", 1.0)
    return 2.0 * math.atan2(y, w)


def _dummies(group: isd2070.Node, out: Feedback, at: tuple[int, ...]) -> None:
    own = []
    for d in group.path("Dummies").findall("i") if group.path("Dummies") is not None else []:
        pos = _vec3(d.find("Position"))
        if pos is not None and d.value("Name"):
            dummy = Dummy(d.value("Name"), d.int("Id"), pos, _heading(d.find("Orientation")),
                          d.int("HeightAdaptationMode"))  # fmt: skip
            out.dummies[dummy.name] = dummy
            own.append(dummy)
            if dummy.id:
                out.by_id[dummy.id] = dummy
    if at:
        out.group_paths[at] = tuple(own)
        if group.value("Name"):
            out.groups.setdefault(group.value("Name"), tuple(own))
    for k, g in enumerate(group.path("Groups").findall("i") if group.path("Groups") is not None else []):
        _dummies(g, out, (*at, k))


def _splines(root: isd2070.Node, out: Feedback) -> None:
    data = root.find("SplineData")
    for v in data.findall("v") if data is not None else []:
        points = [p for p in (_vec3(i) for i in (v.path("ApproximationPoints") or isd2070.Node("")).findall("i")) if p]
        if len(points) < 2:
            points = [p for p in (_vec3(i) for i in (v.path("ControlPoints") or isd2070.Node("")).findall("i")) if p]
        if v.value("Name") and points:
            out.splines[v.value("Name")] = Spline(v.value("Name"), v.int("Id"), tuple(points))


def _loop(node: isd2070.Node | None) -> Loop | None:
    if node is None:
        return None
    start = node.find("DefaultState")
    elements = node.path("ElementContainer/Elements")
    return Loop(
        start.value("DummyName", "") or "" if start is not None else "",
        start.int("DummyId") if start is not None else 0,
        start.int("Visible", 1) != 0 if start is not None else True,
        tuple(elements.findall("i")) if elements is not None else (),
        (start.value("StartDummyGroup", "") or "") if start is not None else "",
        start.int("ResetToDefaultEveryLoop") != 0 if start is not None else False,
        start.int("FadeVisibility") != 0 if start is not None else False,
        start.int("SequenceID") if start is not None else 0,
    )


def _pairs(node: isd2070.Node | None) -> tuple[tuple[int, int], ...]:
    if node is None or not node.data:
        return ()
    words = struct.unpack(f"<{len(node.data) // 4}i", node.data[: len(node.data) // 4 * 4])
    return tuple(zip(words[::2], words[1::2], strict=False))


def barriers_in(elements, out: Counter) -> None:
    """The barrier elements of a list and its branches, counted by ``BarrierId`` into ``out``."""
    for e in elements:
        kind = element_kind(e)
        if kind == 7:
            out[e.int("BarrierId")] += 1
        elif kind == 3 and e.path("BranchList") is not None:
            for b in e.path("BranchList").findall("i"):
                found = b.path("pair2/Elements")
                barriers_in(found.findall("i") if found is not None else [], out)


def _actor(item: isd2070.Node) -> Actor:
    definitions = item.path("SequenceDefinitions")
    loops, barriers = [], Counter()
    for d in definitions.findall("i") if definitions is not None else []:
        found = tuple(x for x in (_loop(d.find(f"Loop{k}")) for k in range(3)) if x is not None)
        loops.append(found)
        for loop in found:
            barriers_in(loop.elements, barriers)
    states, pairs = {}, item.find("FeedbackLoops") or item.find("FeedbackLoops2")  # 2070 numbers some lists
    keys = pairs.findall("k") if pairs is not None else []
    values = pairs.findall("v") if pairs is not None else []
    for k, v in zip(keys, values, strict=False):
        index = int(v.text or 0)
        if 0 <= index < len(loops):
            states[int(k.text or 0)] = loops[index]
    assets = item.find("AssetVariationList") or item.find("AssetVariationList2")
    groups = assets.find("AssetGroupNames") if assets is not None else None
    names = tuple(n.text for n in groups.children if n.text) if groups is not None else ()
    count = max(item.int("ActorCount", 1), 0)
    return Actor(
        _pairs(assets.find("GuidVariationList") if assets is not None else None), names, states,
        item.int("MainObject") != 0, count, max(item.int("MaxActorCount", count), count), item.int("CreateChance", 100),
        item.value("MultiplyActorByDummyCount", "") or "", item.int("IsAlwaysVisibleActor") != 0,
        item.int("ApplyScaleToMovementSpeed", 1) != 0, barriers, item,
    )  # fmt: skip


def read(root: isd2070.Node) -> Feedback:
    """The dummies, groups, splines and actors of a feedback file's tree."""
    out = Feedback()
    dummy_root = root.find("DummyRoot")
    if dummy_root is not None:
        _dummies(dummy_root, out, ())
    _splines(root, out)
    configs = root.path("FeedbackDefinition/FeedbackConfigs")
    out.actors = [_actor(i) for i in configs.findall("i")] if configs is not None else []
    return out
