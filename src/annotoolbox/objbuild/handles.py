"""The math behind the handles over the 3D view, no Qt: the ray through the mouse in the model's space, where it meets
a plane or comes nearest an axis, the placement a part sits in, and the field values a move, a turn or a scaling of
a part's orientation transformer writes.

``part_target(document, role, path)`` is the entry (None for a path that is no placeable part); ``moved``, ``turned`` and
``scaled`` give the new ``SPlaced`` values of a drag, ``field_edits`` the edits that write them (the fields a part lacks
are made). docs/tool-suite.md section 23, "Handles in the view".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..models import cfgdoc, cfgparts
from ..models.cfgdoc import SDoc, SEdit, SNode, SPart, fields, is_config, kind_of, strip
from ..models.meshxform import qaxis, qconj, qmul, qnormalize, qrotate
from ..render import picking

ORIENT = "ORIENTATION_TRANSFORM"
PLACED = ("Files", "Models", "Clothes", "Particles", "Effects", "Lights", "Decals", "TerrainDecals")  # lists of parts
AXES = np.eye(3)
POSITION = ("Position.x", "Position.y", "Position.z")
ROTATION = ("Rotation.x", "Rotation.y", "Rotation.z", "Rotation.w")
SCALE = "Scale"
IDENTITY = (0.0, 0.0, 0.0, 1.0)
DEFAULTS = {"Rotation.w": "1", SCALE: "1"}  # what a field the file does not write means; the rest is 0


def model_ray(camera, x: float, y: float, width_px: int, height_px: int) -> tuple[np.ndarray, np.ndarray]:
    """The ray through the widget's pixel (x, y) in the model's space (the camera's view space with x mirrored, as
    the overlay draws it): origin and unit direction."""
    origin, direction = camera.ray(x, y, width_px, height_px)
    flip = np.array([-1.0, 1.0, 1.0])
    return np.asarray(origin, float) * flip, np.asarray(direction, float) * flip


@dataclass(frozen=True)
class SFrame:
    """The placement a part's first orientation transformer lives in: position, rotation (x y z w), scale per axis."""

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = IDENTITY
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)

    def then(self, placed: SPlaced, non_uniform: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> SFrame:
        """The frame inside this one that a transformer with ``placed`` makes (as ``configtree.placement`` nests)."""
        local = tuple(
            scale_axis * coordinate for scale_axis, coordinate in zip(self.scale, placed.position, strict=True)
        )
        position = tuple(
            base + offset for base, offset in zip(self.position, qrotate(self.rotation, local), strict=True)
        )
        scale = tuple(
            scale_axis * placed.scale * factor for scale_axis, factor in zip(self.scale, non_uniform, strict=True)
        )
        return SFrame(position, qmul(self.rotation, placed.rotation), scale)


@dataclass(frozen=True)
class SPlaced:
    """An orientation transformer's values: position, rotation (x y z w), the uniform scale."""

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = IDENTITY
    scale: float = 1.0


def read_placed(node: SNode) -> SPlaced:
    """The values of an orientation transformer (the defaults for what it does not write; a zero rotation is none)."""
    values_by_name = {strip(field.name): _number(field.value) for field in fields(node)}
    rotation = tuple(values_by_name.get(name, 0.0) for name in ROTATION[:3]) + (values_by_name.get("Rotation.w", 1.0),)
    if not any(rotation):
        rotation = IDENTITY
    return SPlaced(
        tuple(values_by_name.get(name, 0.0) for name in POSITION), qnormalize(rotation), values_by_name.get(SCALE, 1.0)
    )


def _number(text: str, default: float = 0.0) -> float:
    try:
        return float(text)
    except ValueError:
        return default


def _non_uniform(node: SNode) -> tuple[float, float, float]:
    values_by_name = {strip(field.name): _number(field.value, 1.0) for field in fields(node)}
    return tuple(values_by_name.get(f"NonUniformScale.{axis_name}", 1.0) for axis_name in "xyz")


def orientations(part: SNode, is_only_plain: bool = True) -> list[SNode]:
    """The orientation transformers of a part, in order (those with ``Conditions``, a state's pose, left out)."""
    holder = next((child for child in part.children if strip(child.name) == "Transformer"), None)
    found = [
        child
        for child in (holder.children if holder is not None else [])
        if is_config(child) and kind_of(child) == ORIENT
    ]
    if not is_only_plain:
        return found
    return [
        child
        for child in found
        if not any(strip(field.name) == "Conditions" and _number(field.value) for field in fields(child))
    ]


@dataclass
class SPartTarget:
    """A placeable part a drag moves: where it is in its document, the ``frame`` its transformer lives in and the
    transformer (``orient``; None when the part has none yet: the drag then makes one)."""

    role: str
    path: str
    document: SDoc
    part: SPart
    frame: SFrame
    orient: SNode | None
    own: bool = False  # the selected part is the transformer itself

    @property
    def placed(self) -> SPlaced:
        return read_placed(self.orient) if self.orient is not None else SPlaced()

    @property
    def pivot(self) -> np.ndarray:
        """Where the transformer puts the part in the model's space."""
        return np.array(self.frame.then(self.placed).position)


def part_target(document: SDoc, role: str, path: str) -> SPartTarget | None:
    """The target a part (or an orientation transformer, edited itself) of a document makes, None when it is neither."""
    part = document.find(path)
    if part is None or part.is_list or not is_config(part.node):
        return None
    if kind_of(part.node) == ORIENT:
        owner = document.find(_above(path)) if path.count("/") >= 2 else None
        if owner is None:
            return None
        before = orientations(owner.node, is_only_plain=False)
        before = before[: next((i for i, sibling in enumerate(before) if sibling is part.node), 0)]
        frame = _ancestors(document, _above(owner.path))
        for sibling in before:
            frame = frame.then(read_placed(sibling), _non_uniform(sibling))
        return SPartTarget(role, path, document, part, frame, part.node, own=True)
    if part.section not in PLACED:
        return None
    plain = orientations(part.node)
    return SPartTarget(role, path, document, part, _ancestors(document, _above(path)), plain[0] if plain else None)


def _above(path: str) -> str:
    """The path of the part a part sits in ("" for a part of the top list): ``Models/0`` -> ``""``."""
    return path.rsplit("/", 2)[0] if path.count("/") >= 2 else ""


def _ancestors(document: SDoc, path: str) -> SFrame:
    """The frame of the placeable parts above ``path`` (their orientation transformers nested; none for a top part)."""
    frame, parts = SFrame(), path.split("/") if path else []
    for end in range(2, len(parts) + 1, 2):
        parent = document.find("/".join(parts[:end]))
        if parent is not None and parent.section in PLACED:
            for name in orientations(parent.node):
                frame = frame.then(read_placed(name), _non_uniform(name))
    return frame


# What a drag makes of the values


def moved(target: SPartTarget, start: SPlaced, delta) -> SPlaced:
    """``start`` with the pivot moved by ``delta`` in the model's space (the frame's turn and scale taken out)."""
    inside = qrotate(qconj(target.frame.rotation), tuple(float(component) for component in delta))
    step = tuple(i / (scale_axis or 1.0) for i, scale_axis in zip(inside, target.frame.scale, strict=True))
    return SPlaced(
        tuple(base + offset for base, offset in zip(start.position, step, strict=True)), start.rotation, start.scale
    )


def turned(target: SPartTarget, start: SPlaced, axis: int, angle_rad: float) -> SPlaced:
    """``start`` turned by ``angle_rad`` about the world axis ``axis`` through the pivot."""
    world = qaxis(tuple(AXES[axis]), angle_rad)
    inside = qmul(qmul(qconj(target.frame.rotation), world), target.frame.rotation)
    return SPlaced(start.position, qnormalize(qmul(inside, start.rotation)), start.scale)


def scaled(start: SPlaced, factor: float) -> SPlaced:
    return SPlaced(start.position, start.rotation, start.scale * factor)


def ring_angle(ray, pivot, axis: int) -> float | None:
    """The angle of the ray's hit on the plane through ``pivot`` perpendicular to the world axis, seen from the pivot
    (turning the next axis towards the one after it, the right-handed sense)."""
    hit = picking.ray_plane(ray, pivot, AXES[axis])
    if hit is None:
        return None
    rel = hit - np.asarray(pivot)
    return math.atan2(float(np.dot(rel, AXES[(axis + 2) % 3])), float(np.dot(rel, AXES[(axis + 1) % 3])))


# Writing the values


@dataclass
class SWriter:
    """The fields of a transformer a drag writes: the leaves that exist, and the edits that make the ones it lacks
    (``setup``, made before the drag starts so it only sets text)."""

    leaves: dict[str, SNode] = field(default_factory=dict)
    setup: list[SEdit] = field(default_factory=list)


def field_writer(target: SPartTarget, names: tuple[str, ...]) -> SWriter:
    """The ``SWriter`` for the named fields of the target's transformer: made, with its list, when the part has none."""
    out, document = SWriter(), target.document
    orient = target.orient
    if orient is None:
        orient = cfgparts.template_part(document, ORIENT)
        holder, made = cfgparts.list_node(document, target.part.node, "Transformer")
        if made is not None:
            out.setup.append(made)
        out.setup.append(cfgdoc.insert(holder, len(holder.children), [orient], "add " + ORIENT))
        target.orient = orient
    have = {strip(field.name): field.node for field in fields(orient)}
    old = cfgparts.family(document) == "old"
    for name in names:
        if name in have:
            out.leaves[name] = have[name]
            continue
        leaf = SNode(f"m_{name}" if old else name, DEFAULTS.get(name, "0"), tail=document.newline)
        out.leaves[name] = leaf
        out.setup.append(cfgdoc.insert(orient, len(orient.children), [leaf], f"add {name}"))
    return out


def values(placed: SPlaced, names: tuple[str, ...]) -> dict[str, str]:
    """The text of the named fields for ``placed``."""
    all_values = dict(zip(POSITION, placed.position, strict=True)) | dict(zip(ROTATION, placed.rotation, strict=True))
    all_values[SCALE] = placed.scale
    return {name: cfgdoc.number_text(all_values[name]) for name in names}


def field_edits(write: SWriter, placed: SPlaced, label: str) -> list[SEdit]:
    """The edits that set the writer's fields to ``placed`` (made once the preview is put back, so each undoes to the
    values the file had)."""
    return [
        cfgdoc.set_value(write.leaves[name], value, label)
        for name, value in values(placed, tuple(write.leaves)).items()
        if cfgdoc.shown(write.leaves[name].text or "") != value
    ]
