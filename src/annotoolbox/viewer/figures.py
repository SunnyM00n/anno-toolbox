"""The walking figures of an object's feedback file in the model view: the actors the object makes of each item
(``models.feedbackplay.build``), each with its unit (its asset's config) put into the scene under ``CFigures/<k>/``,
moved along its timeline at its unit's pace (``CMovements``) and animated by the sequence it plays; an actor whose unit
is not found walks as a marker.

``CFigures.of(files, game, root, state)`` reads a feedback tree; ``graft(scene)`` adds the figures' parts,
``animations(seconds)`` gives what they play for ``SViewState.animation``. docs/engine-cloth.md "The figures in the view".
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
    CAssetModels,
    SResolved,
    definition_start,
    variations_of,
)
from ..formats import isd2070
from ..models import feedbackplay
from ..models.feedbackplay import CTimeline, SFeedback, SMovement
from ..models.modelread import CORNERS
from .modelscene import BANNER_SHADER, CScene, SMaterial, SPart, build
from .motion import SLinked, turn
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


class CWalk:
    """A figure's place at a time: ``at_time(seconds)`` is its matrix (``viewer.motion``), zero sized while it hides."""

    def __init__(self, timeline: CTimeline) -> None:
        self.m_timeline = timeline

    def at_time(self, seconds: float) -> np.ndarray:
        pose = self.m_timeline.at(seconds)
        matrix = turn((0.0, 1.0, 0.0), pose.heading) * (pose.scale if pose.visible else 0.0)
        matrix[3, 3] = 1.0
        matrix[:3, 3] = pose.position
        return matrix


class _CFigure:
    """One actor in the scene: its number (its ``CFigures/<index>/`` prefix), timeline, unit scene (None: a marker),
    the feedback item it came from and its sequences by id."""

    def __init__(self, index: int, timeline: CTimeline, unit: CScene | None, item: int = 0) -> None:
        self.m_index, self.m_timeline, self.m_unit, self.m_item = index, timeline, unit, item
        self.m_walk = CWalk(timeline)
        self.m_prefix = f"{PREFIX}/{index}/"
        self.m_sequences = {sequence.sequence_id: sequence for sequence in unit.m_sequences} if unit is not None else {}


class CUnits2070:
    """2070's asset list as a GUID -> model lookup: its ``<Standard>`` names the asset before the GUID, so the
    newer games' lookup (``CAssetModels``) does not find it."""

    def __init__(self, data: bytes) -> None:
        self.m_data = data

    def model_for(self, guid: int, variation: int = 0) -> SResolved:
        tag, offset = b"<GUID>%d</GUID>" % guid, -1
        while True:
            offset = self.m_data.find(tag, offset + 1)
            if offset < 0:
                return SResolved()
            standard = self.m_data.rfind(b"<Standard>", 0, offset)
            if standard >= 0 and self.m_data.find(b"</Standard>", standard, offset) < 0:
                break
        body = self.m_data[self.m_data.rfind(b"<Asset>", 0, offset) : self.m_data.find(b"</Asset>", offset)]
        found = [candidate for candidate in variations_of(body) if candidate]
        return SResolved(found[variation] if 0 <= variation < len(found) else found[0]) if found else SResolved()


def units_of(files, game: str) -> CAssetModels | CUnits2070 | None:
    """The game's asset list as a GUID -> model lookup (None: a game or an install without one)."""
    try:
        if game == "anno2070":
            return CUnits2070(files.read(ASSETS_2070))
        asset_game = GAMES.get(game)
        return CAssetModels.read(files.read, asset_game) if asset_game is not None else None
    except (KeyError, OSError):
        return None


def movement_fields(text: bytes) -> dict[str, float]:
    """The walk's fields of the first ``SMovement`` block in ``text`` that sets any."""
    for block in MOVEMENT.findall(text):
        found = {}
        for name in MOVE_FIELDS:
            match = re.search(rb"<%s>([-0-9.eE]+)</%s>" % (name.encode(), name.encode()), block)
            if match:
                found[name] = float(match.group(1))
        if found:
            return found
    return {}


class CMovements:
    """A unit asset's ``SMovement`` the way the newer games fill a property: the asset's own fields, then its base
    asset's, its template's, then the properties' defaults (117's file; the other games' defaults are not in their
    files, so ``MOVE_FIELDS``). 2070's units have none: ``of`` is None for them."""

    def __init__(self, files, game: str, units) -> None:
        self.m_units = units if isinstance(units, CAssetModels) else None
        self.m_defaults = dict(MOVE_FIELDS)
        try:
            self.m_defaults.update(movement_fields(files.read(PROPERTIES[game])) if game in PROPERTIES else {})
        except (KeyError, OSError):
            pass
        self._m_seen: dict[int, dict[str, float]] = {}

    def _own(self, guid: int, depth: int = 0) -> dict[str, float]:
        if guid in self._m_seen:
            return self._m_seen[guid]
        found: dict[str, float] = {}
        definition = _definition(self.m_units, guid)
        if definition is not None:
            data, start = definition
            head = data[data.rfind(b"<Asset>", 0, start) : start]
            body = data[start : data.find(b"</Asset>", start)]
            base, template = BASE.search(head), TEMPLATE.search(head)
            inherited = self._own(int(base.group(1)), depth + 1) if base and depth < 8 else {}
            if not inherited and template:
                inherited = self._template(template.group(1).decode("utf-8", "replace"))
            found = {**inherited, **movement_fields(body)}
        self._m_seen[guid] = found
        return found

    def _template(self, name: str) -> dict[str, float]:
        text = self.m_units.m_templates if self.m_units is not None else ""
        offset = text.find(f"<Name>{name}</Name>")
        end = text.find("</Template>", offset)
        return movement_fields(text[offset:end].encode("utf-8")) if offset >= 0 else {}

    def of(self, guid: int) -> dict[str, float] | None:
        if self.m_units is None:
            return None
        return {**self.m_defaults, **self._own(guid)}


def _definition(units: CAssetModels | None, guid: int) -> tuple[bytes, int] | None:
    """The asset file and place where ``guid`` is defined (a later file wins), None when none is."""
    for data in reversed(units.m_files if units is not None else []):
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
        match = SPEED.search(body)
        if match:
            out[int(ident)] = float(match.group(1)) / unit
    return out


class _CUnits:
    """The figures' units: each unit config's scene built once, its movement and its sequences' speeds."""

    def __init__(self, files, game: str, units) -> None:
        self.m_files, self.m_game, self.m_units = files, game, units
        self.m_movements = CMovements(files, game, units) if files is not None else None
        self.m_built: dict[str, CScene | None] = {}

    def cfg(self, unit: tuple[int, int] | None) -> str:
        if self.m_units is None or unit is None:
            return ""
        guid, variation = unit
        cfg = self.m_units.model_for(guid, max(variation, 0)).cfg
        return cfg if cfg and self.m_files.exists(cfg) else ""

    def scene(self, cfg: str) -> CScene | None:
        if not cfg:
            return None
        if cfg not in self.m_built:
            try:
                self.m_built[cfg] = build(self.m_files, cfg, self.m_game)
            except Exception:  # a unit that does not read walks as a marker
                self.m_built[cfg] = None
        return self.m_built[cfg]

    def movement(self, unit: tuple[int, int] | None, cfg: str, scene: CScene | None) -> SMovement:
        found = self.m_movements.of(unit[0]) if self.m_movements is not None and unit is not None else None
        cycles = _cycles(scene)
        if self.m_game == "anno2070":  # no BaseSpeed: the walk sequence's own Speed (engine-animation.md "Pace")
            return SMovement(speeds=ifo_speeds(self.m_files, cfg, isd2070.FIXED) if cfg else {}, cycles=cycles)
        if found is None:
            return SMovement(cycles=cycles)
        return SMovement(found["BaseSpeed"], found["RotationSpeed"], found["SpotTurnRotationSpeed"],
                        ifo_speeds(self.m_files, cfg) if cfg else {}, cycles)  # fmt: skip


class CFigures:
    """The actors of a feedback file in one state of the object (0 not working, 1 working)."""

    def __init__(self, feedback: SFeedback, figures: list[_CFigure], state: int) -> None:
        self.m_feedback, self.m_figures, self.m_state = feedback, figures, state

    @classmethod
    def of(cls, files, game: str, root, state: int = 1, units=None) -> CFigures:
        """The figures of the feedback tree ``root`` over ``files``; ``units`` finds an actor's config (default: the
        game's asset list)."""
        feedback = feedbackplay.read(root)
        units = units if units is not None or files is None else units_of(files, game)
        found = _CUnits(files, game, units)
        built = feedbackplay.build(feedback)
        cfgs = [found.cfg(built_item.unit) for built_item in built]
        scenes = [found.scene(cfg) for cfg in cfgs]
        moves = {
            id(built_item): found.movement(built_item.unit, cfg, scene)
            for built_item, cfg, scene in zip(built, cfgs, scenes, strict=True)
        }
        timelines = feedbackplay.play_all(feedback, built, state, lambda built_item: moves[id(built_item)])
        out: list[_CFigure] = []
        for built_item, scene, timeline in zip(built, scenes, timelines, strict=True):
            if not built_item.main:  # the object itself waits at barriers, it is no figure
                out.append(_CFigure(len(out), timeline, scene, built_item.item))
        return cls(feedback, out, state)

    def graft(self, scene: CScene) -> None:
        """Add every figure's parts to ``scene``, each moved by its walk."""
        for figure in self.m_figures:
            if figure.m_unit is None:
                scene.m_parts.append(_marker(figure))
                continue
            for part in figure.m_unit.m_parts:
                scene.m_parts.append(_moved(part, figure))

    def animations(self, seconds: float) -> dict:
        """Per figure part node the clip it plays at ``seconds`` (``SViewState.animation``)."""
        out = {}
        for figure in self.m_figures:
            pose = figure.m_timeline.at(seconds)
            seq = figure.m_sequences.get(pose.sequence)
            if figure.m_unit is None or seq is None or not pose.visible:
                continue
            local = sequence_animations(figure.m_unit, seq, seconds - pose.since_s)
            for node, (file, start, scale, loops) in local.items():
                out[figure.m_prefix + node] = (file, start + pose.since_s, scale, loops)
        return out


def _cycles(unit: CScene | None) -> dict[int, float]:
    """A sequence's cycle in seconds from the unit's config."""
    if unit is None:
        return {}
    return {
        sequence.sequence_id: sequence.duration_ms / 1000 / (sequence.speed if sequence.speed > 0 else 1.0)
        for sequence in unit.m_sequences
    }


def _moved(part: SPart, figure: _CFigure) -> SPart:
    """A unit's part under the figure's prefix, placed by its walk (a part on a joint follows its host)."""
    steps = tuple(
        replace(step, host=figure.m_prefix + step.host) if isinstance(step, SLinked) else step for step in part.motion
    )
    if not steps or not isinstance(steps[0], SLinked):
        steps = (figure.m_walk, *(steps or (part.matrix,)))
    return replace(part, node=figure.m_prefix + part.node, motion=steps)


def _marker(figure: _CFigure) -> SPart:
    """A standing card for an actor without a unit to draw (a leaf card of the trees: one quad facing the camera)."""
    corners = [(sign_x * MARKER_SIZE, sign_y * 2 * MARKER_SIZE) for sign_x, sign_y in CORNERS]
    banner = {"positions": [0.0, 2 * MARKER_SIZE, 0.0] * 4, "corners": [value for corner in corners for value in corner],
              "radial": [0.0, 0.0, 1.0] * 4, "uvs": [0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0],
              "indices": [0, 1, 2, 0, 2, 3]}  # fmt: skip
    node = figure.m_prefix + "marker"
    return SPart(node, node + "|marker", np.identity(4), banner=banner, motion=(figure.m_walk, np.identity(4)),
                default=SMaterial("marker", color=(1.0, 0.55, 0.1), shader=BANNER_SHADER, node=node))  # fmt: skip
