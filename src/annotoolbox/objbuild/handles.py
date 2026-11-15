"""The math behind the handles over the 3D view, no Qt: the ray through the mouse in the model's space, where it meets
a plane or comes nearest an axis, the placement a part sits in, and the field values a move, a turn or a scaling of
a part's orientation transformer writes.

``part_target(doc, role, path)`` is the entry (None for a path that is no placeable part); ``moved``, ``turned`` and
``scaled`` give the new ``Placed`` values of a drag, ``field_edits`` the edits that write them (the fields a part lacks
are made). docs/tool-suite.md section 23, "Handles in the view".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..models import cfgdoc, cfgparts
from ..models.cfgdoc import Doc, Edit, Node, Part, fields, is_config, kind_of, strip
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


def model_ray(camera, x: float, y: float, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """The ray through the widget's pixel (x, y) in the model's space (the camera's view space with x mirrored, as
    the overlay draws it): origin and unit direction."""
    origin, direction = camera.ray(x, y, w, h)
    flip = np.array([-1.0, 1.0, 1.0])
    return np.asarray(origin, float) * flip, np.asarray(direction, float) * flip


@dataclass(frozen=True)
class Frame:
    """The placement a part's first orientation transformer lives in: position, rotation (x y z w), scale per axis."""

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = IDENTITY
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)

    def then(self, placed: Placed, non_uniform: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> Frame:
        """The frame inside this one that a transformer with ``placed`` makes (as ``configtree.placement`` nests)."""
        local = tuple(s * p for s, p in zip(self.scale, placed.position, strict=True))
        pos = tuple(a + b for a, b in zip(self.position, qrotate(self.rotation, local), strict=True))
        scale = tuple(s * placed.scale * n for s, n in zip(self.scale, non_uniform, strict=True))
        return Frame(pos, qmul(self.rotation, placed.rotation), scale)


@dataclass(frozen=True)
class Placed:
    """An orientation transformer's values: position, rotation (x y z w), the uniform scale."""

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = IDENTITY
    scale: float = 1.0


def read_placed(node: Node) -> Placed:
    """The values of an orientation transformer (the defaults for what it does not write; a zero rotation is none)."""
    by = {strip(f.name): _number(f.value) for f in fields(node)}
    rotation = tuple(by.get(n, 0.0) for n in ROTATION[:3]) + (by.get("Rotation.w", 1.0),)
    if not any(rotation):
        rotation = IDENTITY
    return Placed(tuple(by.get(n, 0.0) for n in POSITION), qnormalize(rotation), by.get(SCALE, 1.0))


def _number(text: str, default: float = 0.0) -> float:
    try:
        return float(text)
    except ValueError:
        return default


def _non_uniform(node: Node) -> tuple[float, float, float]:
    by = {strip(f.name): _number(f.value, 1.0) for f in fields(node)}
    return tuple(by.get(f"NonUniformScale.{a}", 1.0) for a in "xyz")


def orientations(part: Node, only_plain: bool = True) -> list[Node]:
    """The orientation transformers of a part, in order (those with ``Conditions``, a state's pose, left out)."""
    holder = next((c for c in part.children if strip(c.name) == "Transformer"), None)
    found = [c for c in (holder.children if holder is not None else []) if is_config(c) and kind_of(c) == ORIENT]
    if not only_plain:
        return found
    return [c for c in found if not any(strip(f.name) == "Conditions" and _number(f.value) for f in fields(c))]


@dataclass
class PartTarget:
    """A placeable part a drag moves: where it is in its document, the ``frame`` its transformer lives in and the
    transformer (``orient``; None when the part has none yet: the drag then makes one)."""

    role: str
    path: str
    doc: Doc
    part: Part
    frame: Frame
    orient: Node | None
    own: bool = False  # the selected part is the transformer itself

    @property
    def placed(self) -> Placed:
        return read_placed(self.orient) if self.orient is not None else Placed()

    @property
    def pivot(self) -> np.ndarray:
        """Where the transformer puts the part in the model's space."""
        return np.array(self.frame.then(self.placed).position)


def part_target(doc: Doc, role: str, path: str) -> PartTarget | None:
    """The target a part (or an orientation transformer, edited itself) of a document makes, None when it is neither."""
    part = doc.find(path)
    if part is None or part.is_list or not is_config(part.node):
        return None
    if kind_of(part.node) == ORIENT:
        owner = doc.find(_above(path)) if path.count("/") >= 2 else None
        if owner is None:
            return None
        before = orientations(owner.node, only_plain=False)
        before = before[: next((i for i, n in enumerate(before) if n is part.node), 0)]
        frame = _ancestors(doc, _above(owner.path))
        for n in before:
            frame = frame.then(read_placed(n), _non_uniform(n))
        return PartTarget(role, path, doc, part, frame, part.node, own=True)
    if part.section not in PLACED:
        return None
    plain = orientations(part.node)
    return PartTarget(role, path, doc, part, _ancestors(doc, _above(path)), plain[0] if plain else None)


def _above(path: str) -> str:
    """The path of the part a part sits in ("" for a part of the top list): ``Models/0`` -> ``""``."""
    return path.rsplit("/", 2)[0] if path.count("/") >= 2 else ""


def _ancestors(doc: Doc, path: str) -> Frame:
    """The frame of the placeable parts above ``path`` (their orientation transformers nested; none for a top part)."""
    frame, parts = Frame(), path.split("/") if path else []
    for end in range(2, len(parts) + 1, 2):
        up = doc.find("/".join(parts[:end]))
        if up is not None and up.section in PLACED:
            for n in orientations(up.node):
                frame = frame.then(read_placed(n), _non_uniform(n))
    return frame


# What a drag makes of the values


def moved(target: PartTarget, start: Placed, delta) -> Placed:
    """``start`` with the pivot moved by ``delta`` in the model's space (the frame's turn and scale taken out)."""
    inside = qrotate(qconj(target.frame.rotation), tuple(float(d) for d in delta))
    step = tuple(i / (s or 1.0) for i, s in zip(inside, target.frame.scale, strict=True))
    return Placed(tuple(a + b for a, b in zip(start.position, step, strict=True)), start.rotation, start.scale)


def turned(target: PartTarget, start: Placed, axis: int, angle: float) -> Placed:
    """``start`` turned by ``angle`` about the world axis ``axis`` through the pivot."""
    world = qaxis(tuple(AXES[axis]), angle)
    inside = qmul(qmul(qconj(target.frame.rotation), world), target.frame.rotation)
    return Placed(start.position, qnormalize(qmul(inside, start.rotation)), start.scale)


def scaled(start: Placed, factor: float) -> Placed:
    return Placed(start.position, start.rotation, start.scale * factor)


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
class Writer:
    """The fields of a transformer a drag writes: the leaves that exist, and the edits that make the ones it lacks
    (``setup``, made before the drag starts so it only sets text)."""

    leaves: dict[str, Node] = field(default_factory=dict)
    setup: list[Edit] = field(default_factory=list)


def field_writer(target: PartTarget, names: tuple[str, ...]) -> Writer:
    """The ``Writer`` for the named fields of the target's transformer: made, with its list, when the part has none."""
    out, doc = Writer(), target.doc
    orient = target.orient
    if orient is None:
        orient = cfgparts.template_part(doc, ORIENT)
        holder, made = cfgparts.list_node(doc, target.part.node, "Transformer")
        if made is not None:
            out.setup.append(made)
        out.setup.append(cfgdoc.insert(holder, len(holder.children), [orient], "add " + ORIENT))
        target.orient = orient
    have = {strip(f.name): f.node for f in fields(orient)}
    old = cfgparts.family(doc) == "old"
    for name in names:
        if name in have:
            out.leaves[name] = have[name]
            continue
        leaf = Node(f"m_{name}" if old else name, DEFAULTS.get(name, "0"), tail=doc.newline)
        out.leaves[name] = leaf
        out.setup.append(cfgdoc.insert(orient, len(orient.children), [leaf], f"add {name}"))
    return out


def values(placed: Placed, names: tuple[str, ...]) -> dict[str, str]:
    """The text of the named fields for ``placed``."""
    all_values = dict(zip(POSITION, placed.position, strict=True)) | dict(zip(ROTATION, placed.rotation, strict=True))
    all_values[SCALE] = placed.scale
    return {n: cfgdoc.number_text(all_values[n]) for n in names}


def field_edits(write: Writer, placed: Placed, label: str) -> list[Edit]:
    """The edits that set the writer's fields to ``placed`` (made once the preview is put back, so each undoes to the
    values the file had)."""
    return [
        cfgdoc.set_value(write.leaves[n], v, label)
        for n, v in values(placed, tuple(write.leaves)).items()
        if cfgdoc.shown(write.leaves[n].text or "") != v
    ]
