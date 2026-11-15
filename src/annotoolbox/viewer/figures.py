"""The walking figures of an object's feedback file in the model view: the actors the object makes of each item
(``models.feedbackplay.build``), each with its unit (its asset's config) put into the scene under ``Figures/<k>/``,
moved along its timeline at its unit's pace (``Movements``) and animated by the sequence it plays; an actor whose unit
is not found walks as a marker.

``Figures.of(files, game, root, state)`` reads a feedback tree; ``graft(scene)`` adds the figures' parts,
``animations(seconds)`` gives what they play for ``ViewState.animation``. docs/engine-cloth.md "The figures in the view".
"""

from __future__ import annotations

import re
from dataclasses import replace

import numpy as np

from ..editor.assetmodels import (
    ASSETS_117,
    ASSETS_1800,
    ASSETS_2205,
    BASE,
    TEMPLATE,
    AssetModels,
    Resolved,
    definition_start,
    variations_of,
)
from ..formats import isd2070
from ..models import feedbackplay
from ..models.feedbackplay import Feedback, Movement, Timeline
from ..models.modelread import CORNERS
from .modelscene import BANNER_SHADER, Material, Part, Scene, build
from .motion import Linked, turn
from .panels import sequence_animations

PREFIX = "Figures"
GAMES = {"anno117": ASSETS_117, "anno1800": ASSETS_1800, "anno2205": ASSETS_2205}
ASSETS_2070 = "data/config/game/assets.xml"
PROPERTIES = {"anno117": "data/base/config/export/properties.xml"}  # the properties' defaults (Movement's)
MARKER_SIZE = 0.12  # half the marker card's width: about a person's
MOVEMENT = re.compile(rb"<Movement>(.*?)</Movement>", re.S)
MOVE_FIELDS = {"BaseSpeed": 1.0, "RotationSpeed": 0.0, "SpotTurnRotationSpeed": 0.0}  # without any record (chosen)
SEQUENCE = re.compile(rb"<Sequence>\s*<Id>(\d+)</Id>(.*?)</Sequence>", re.S)
SPEED = re.compile(rb"<Speed>([-0-9.eE]+)</Speed>")


class Walk:
    """A figure's place at a time: ``at_time(seconds)`` is its matrix (``viewer.motion``), zero sized while it hides."""

    def __init__(self, timeline: Timeline) -> None:
        self.timeline = timeline

    def at_time(self, seconds: float) -> np.ndarray:
        pose = self.timeline.at(seconds)
        m = turn((0.0, 1.0, 0.0), pose.heading) * (pose.scale if pose.visible else 0.0)
        m[3, 3] = 1.0
        m[:3, 3] = pose.position
        return m


class _Figure:
    """One actor in the scene: its number (its ``Figures/<index>/`` prefix), timeline, unit scene (None: a marker),
    the feedback item it came from and its sequences by id."""

    def __init__(self, index: int, timeline: Timeline, unit: Scene | None, item: int = 0) -> None:
        self.index, self.timeline, self.unit, self.item = index, timeline, unit, item
        self.walk = Walk(timeline)
        self.prefix = f"{PREFIX}/{index}/"
        self.sequences = {s.sequence_id: s for s in unit.sequences} if unit is not None else {}


class Units2070:
    """2070's asset list as a GUID -> model lookup: its ``<Standard>`` names the asset before the GUID, so the
    newer games' lookup (``AssetModels``) does not find it."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def model_for(self, guid: int, variation: int = 0) -> Resolved:
        tag, at = b"<GUID>%d</GUID>" % guid, -1
        while True:
            at = self.data.find(tag, at + 1)
            if at < 0:
                return Resolved()
            standard = self.data.rfind(b"<Standard>", 0, at)
            if standard >= 0 and self.data.find(b"</Standard>", standard, at) < 0:
                break
        body = self.data[self.data.rfind(b"<Asset>", 0, at) : self.data.find(b"</Asset>", at)]
        found = [v for v in variations_of(body) if v]
        return Resolved(found[variation] if 0 <= variation < len(found) else found[0]) if found else Resolved()


def units_of(files, game: str) -> AssetModels | Units2070 | None:
    """The game's asset list as a GUID -> model lookup (None: a game or an install without one)."""
    try:
        if game == "anno2070":
            return Units2070(files.read(ASSETS_2070))
        asset_game = GAMES.get(game)
        return AssetModels.read(files.read, asset_game) if asset_game is not None else None
    except (KeyError, OSError):
        return None


def movement_fields(text: bytes) -> dict[str, float]:
    """The walk's fields of the first ``Movement`` block in ``text`` that sets any."""
    for block in MOVEMENT.findall(text):
        found = {}
        for name in MOVE_FIELDS:
            m = re.search(rb"<%s>([-0-9.eE]+)</%s>" % (name.encode(), name.encode()), block)
            if m:
                found[name] = float(m.group(1))
        if found:
            return found
    return {}


class Movements:
    """A unit asset's ``Movement`` the way the newer games fill a property: the asset's own fields, then its base
    asset's, its template's, then the properties' defaults (117's file; the other games' defaults are not in their
    files, so ``MOVE_FIELDS``). 2070's units have none: ``of`` is None for them."""

    def __init__(self, files, game: str, units) -> None:
        self.units = units if isinstance(units, AssetModels) else None
        self.defaults = dict(MOVE_FIELDS)
        try:
            self.defaults.update(movement_fields(files.read(PROPERTIES[game])) if game in PROPERTIES else {})
        except (KeyError, OSError):
            pass
        self._seen: dict[int, dict[str, float]] = {}

    def _own(self, guid: int, depth: int = 0) -> dict[str, float]:
        if guid in self._seen:
            return self._seen[guid]
        found: dict[str, float] = {}
        definition = _definition(self.units, guid)
        if definition is not None:
            data, start = definition
            head = data[data.rfind(b"<Asset>", 0, start) : start]
            body = data[start : data.find(b"</Asset>", start)]
            base, template = BASE.search(head), TEMPLATE.search(head)
            inherited = self._own(int(base.group(1)), depth + 1) if base and depth < 8 else {}
            if not inherited and template:
                inherited = self._template(template.group(1).decode("utf-8", "replace"))
            found = {**inherited, **movement_fields(body)}
        self._seen[guid] = found
        return found

    def _template(self, name: str) -> dict[str, float]:
        text = self.units.templates if self.units is not None else ""
        at = text.find(f"<Name>{name}</Name>")
        end = text.find("</Template>", at)
        return movement_fields(text[at:end].encode("utf-8")) if at >= 0 else {}

    def of(self, guid: int) -> dict[str, float] | None:
        if self.units is None:
            return None
        return {**self.defaults, **self._own(guid)}


def _definition(units: AssetModels | None, guid: int) -> tuple[bytes, int] | None:
    """The asset file and place where ``guid`` is defined (a later file wins), None when none is."""
    for data in reversed(units.files if units is not None else []):
        start = definition_start(data, guid)
        if start >= 0:
            return data, start
    return None


def ifo_speeds(files, cfg: str, unit: float = 1.0) -> dict[int, float]:
    """Per sequence id its ``Speed`` in the unit's info file (beside its config) divided by ``unit`` (2070 stores it
    x4096); none: {}."""
    path = cfg.rsplit(".", 1)[0] + ".ifo"
    try:
        data = files.read(path) if files.exists(path) else b""
    except (KeyError, OSError):
        return {}
    out = {}
    for ident, body in SEQUENCE.findall(data):
        m = SPEED.search(body)
        if m:
            out[int(ident)] = float(m.group(1)) / unit
    return out


class _Units:
    """The figures' units: each unit config's scene built once, its movement and its sequences' speeds."""

    def __init__(self, files, game: str, units) -> None:
        self.files, self.game, self.units = files, game, units
        self.movements = Movements(files, game, units) if files is not None else None
        self.built: dict[str, Scene | None] = {}

    def cfg(self, unit: tuple[int, int] | None) -> str:
        if self.units is None or unit is None:
            return ""
        guid, variation = unit
        cfg = self.units.model_for(guid, max(variation, 0)).cfg
        return cfg if cfg and self.files.exists(cfg) else ""

    def scene(self, cfg: str) -> Scene | None:
        if not cfg:
            return None
        if cfg not in self.built:
            try:
                self.built[cfg] = build(self.files, cfg, self.game)
            except Exception:  # a unit that does not read walks as a marker
                self.built[cfg] = None
        return self.built[cfg]

    def movement(self, unit: tuple[int, int] | None, cfg: str, scene: Scene | None) -> Movement:
        found = self.movements.of(unit[0]) if self.movements is not None and unit is not None else None
        cycles = _cycles(scene)
        if self.game == "anno2070":  # no BaseSpeed: the walk sequence's own Speed (engine-animation.md "Pace")
            return Movement(speeds=ifo_speeds(self.files, cfg, isd2070.FIXED) if cfg else {}, cycles=cycles)
        if found is None:
            return Movement(cycles=cycles)
        return Movement(found["BaseSpeed"], found["RotationSpeed"], found["SpotTurnRotationSpeed"],
                        ifo_speeds(self.files, cfg) if cfg else {}, cycles)  # fmt: skip


class Figures:
    """The actors of a feedback file in one state of the object (0 not working, 1 working)."""

    def __init__(self, feedback: Feedback, figures: list[_Figure], state: int) -> None:
        self.feedback, self.figures, self.state = feedback, figures, state

    @classmethod
    def of(cls, files, game: str, root, state: int = 1, units=None) -> Figures:
        """The figures of the feedback tree ``root`` over ``files``; ``units`` finds an actor's config (default: the
        game's asset list)."""
        feedback = feedbackplay.read(root)
        units = units if units is not None or files is None else units_of(files, game)
        found = _Units(files, game, units)
        built = feedbackplay.build(feedback)
        cfgs = [found.cfg(b.unit) for b in built]
        scenes = [found.scene(cfg) for cfg in cfgs]
        moves = {id(b): found.movement(b.unit, cfg, scene) for b, cfg, scene in zip(built, cfgs, scenes, strict=True)}
        timelines = feedbackplay.play_all(feedback, built, state, lambda b: moves[id(b)])
        out: list[_Figure] = []
        for b, scene, tl in zip(built, scenes, timelines, strict=True):
            if not b.main:  # the object itself waits at barriers, it is no figure
                out.append(_Figure(len(out), tl, scene, b.item))
        return cls(feedback, out, state)

    def graft(self, scene: Scene) -> None:
        """Add every figure's parts to ``scene``, each moved by its walk."""
        for f in self.figures:
            if f.unit is None:
                scene.parts.append(_marker(f))
                continue
            for part in f.unit.parts:
                scene.parts.append(_moved(part, f))

    def animations(self, seconds: float) -> dict:
        """Per figure part node the clip it plays at ``seconds`` (``ViewState.animation``)."""
        out = {}
        for f in self.figures:
            pose = f.timeline.at(seconds)
            seq = f.sequences.get(pose.sequence)
            if f.unit is None or seq is None or not pose.visible:
                continue
            local = sequence_animations(f.unit, seq, seconds - pose.since)
            for node, (file, start, scale, loops) in local.items():
                out[f.prefix + node] = (file, start + pose.since, scale, loops)
        return out


def _cycles(unit: Scene | None) -> dict[int, float]:
    """A sequence's cycle in seconds from the unit's config."""
    if unit is None:
        return {}
    return {s.sequence_id: s.duration_ms / 1000 / (s.speed if s.speed > 0 else 1.0) for s in unit.sequences}


def _moved(part: Part, f: _Figure) -> Part:
    """A unit's part under the figure's prefix, placed by its walk (a part on a joint follows its host)."""
    steps = tuple(replace(s, host=f.prefix + s.host) if isinstance(s, Linked) else s for s in part.motion)
    if not steps or not isinstance(steps[0], Linked):
        steps = (f.walk, *(steps or (part.matrix,)))
    return replace(part, node=f.prefix + part.node, motion=steps)


def _marker(f: _Figure) -> Part:
    """A standing card for an actor without a unit to draw (a leaf card of the trees: one quad facing the camera)."""
    corners = [(sx * MARKER_SIZE, sy * 2 * MARKER_SIZE) for sx, sy in CORNERS]
    banner = {"positions": [0.0, 2 * MARKER_SIZE, 0.0] * 4, "corners": [v for c in corners for v in c],
              "radial": [0.0, 0.0, 1.0] * 4, "uvs": [0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0],
              "indices": [0, 1, 2, 0, 2, 3]}  # fmt: skip
    node = f.prefix + "marker"
    return Part(node, node + "|marker", np.identity(4), banner=banner, motion=(f.walk, np.identity(4)),
                default=Material("marker", color=(1.0, 0.55, 0.1), shader=BANNER_SHADER, node=node))  # fmt: skip
