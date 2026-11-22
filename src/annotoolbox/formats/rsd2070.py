"""Anno 2070 record sets (``.rsd``): the fixed layouts the computer players build, read over ``isd2070``'s tree.

A record set is the island files' tag tree (``isd2070.parse`` reads it and ``serialize`` writes it back byte for byte)
holding an anchor (``VillageCenter``) and a list of nodes; a node has a name, the names it waits for and a list of
objects: buildings and props, streets as tile lists, multi-objects such as quay pieces. Positions are in tiles of the
record, directions in degrees (the file stores both x 4096). ``read`` is the entry.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .isd2070 import FIXED, SNode, parse

OBJECT, STREET, MULTI = 0, 1, 2  # an object's Type
ANGLE = 4096.0  # Direction: degrees x 4096


@dataclass(frozen=True)
class SRecordObject:
    kind: int  # OBJECT, STREET or MULTI
    guid: int
    x: float = 0.0  # tiles from the record's origin (an object's own; streets and multi-objects use their parts)
    z: float = 0.0
    angle: float = 0.0  # degrees
    variation: int = 0
    name: str = ""  # War_* is placed only at war; Deco, Str, Bridge, ...
    on_water: bool = False
    tiles: tuple[tuple[int, int], ...] = ()  # STREET: the tiles the street covers
    parts: tuple[tuple[int, float, float, float, int], ...] = ()  # MULTI: (GUID, x, z, angle, variation)


@dataclass(frozen=True)
class SRecordNode:
    name: str
    main: bool
    after: tuple[str, ...]  # DependencyList: the nodes that must be finished first
    rect: tuple[int, int, int, int]  # left, top, right, bottom in tiles (999 / 0: only harbor pieces)
    objects: tuple[SRecordObject, ...] = ()


@dataclass
class SRecordSet:
    anchor: tuple[float, float]  # VillageCenter: the record's centre building, in tiles
    nodes: list[SRecordNode] = field(default_factory=list)
    root: SNode | None = None  # the tree as read, for writing back


def _point(node: SNode | None, count: int) -> tuple[float, ...]:
    if node is None or node.data is None or len(node.data) < 8 * count:
        return (0.0,) * count
    return tuple(value / FIXED for value in struct.unpack_from(f"<{count}q", node.data))


def _object(object_node: SNode) -> SRecordObject:
    kind = object_node.int("Type")
    name = object_node.value("Name") or ""
    if kind == STREET:  # Position .. Variation are not set for streets and multi-objects
        tiles = tuple(
            (child.int("x"), child.int("y")) for child in (object_node.find("Positions") or SNode("")).children
        )
        return SRecordObject(kind, object_node.int("GUID"), name=name, tiles=tiles)
    if kind == MULTI:
        parts = []
        for child in (object_node.find("MultiObject") or SNode("")).children:
            x, z, _y = _point(child.find("Position"), 3)
            parts.append((child.int("GUID"), x, z, child.int("Direction") / ANGLE, child.int("Variation")))
        return SRecordObject(kind, 0, name=name, parts=tuple(parts))
    x, z, _y = _point(object_node.find("Position"), 3)
    return SRecordObject(
        kind,
        object_node.int("GUID"),
        x,
        z,
        object_node.int("Direction") / ANGLE,
        object_node.int("Variation"),
        name,
        object_node.int("m_bSetOnWater") == 1,
    )


def _node(node: SNode) -> SRecordNode:
    after = tuple(child.text for child in (node.find("DependencyList") or SNode("")).children if child.text)
    rect = tuple(node.int(f"Rect/{side}") for side in ("left", "top", "right", "bottom"))
    objects = tuple(_object(object_node) for object_node in (node.find("BuildList") or SNode("")).children)
    return SRecordNode(node.value("Name") or "", node.int("IsMainNode") == 1, after, rect, objects)  # type: ignore[arg-type]


def read(data: bytes) -> SRecordSet:
    """A record set from the file's bytes."""
    root = parse(data)
    anchor = _point(root.find("VillageCenter"), 2)
    nodes = [_node(node) for node in (root.find("RecordSet") or SNode("")).children]
    return SRecordSet((anchor[0], anchor[1]), nodes, root)
