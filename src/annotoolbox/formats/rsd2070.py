"""Anno 2070 record sets (``.rsd``): the fixed layouts the computer players build, read over ``isd2070``'s tree.

A record set is the island files' tag tree (``isd2070.parse`` reads it and ``serialize`` writes it back byte for byte)
holding an anchor (``VillageCenter``) and a list of nodes; a node has a name, the names it waits for and a list of
objects: buildings and props, streets as tile lists, multi-objects such as quay pieces. Positions are in tiles of the
record, directions in degrees (the file stores both x 4096). ``read`` is the entry.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .isd2070 import FIXED, Node, parse

OBJECT, STREET, MULTI = 0, 1, 2  # an object's Type
ANGLE = 4096.0  # Direction: degrees x 4096


@dataclass(frozen=True)
class RecordObject:
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
class RecordNode:
    name: str
    main: bool
    after: tuple[str, ...]  # DependencyList: the nodes that must be finished first
    rect: tuple[int, int, int, int]  # left, top, right, bottom in tiles (999 / 0: only harbour pieces)
    objects: tuple[RecordObject, ...] = ()


@dataclass
class RecordSet:
    anchor: tuple[float, float]  # VillageCenter: the record's centre building, in tiles
    nodes: list[RecordNode] = field(default_factory=list)
    root: Node | None = None  # the tree as read, for writing back


def _point(node: Node | None, count: int) -> tuple[float, ...]:
    if node is None or node.data is None or len(node.data) < 8 * count:
        return (0.0,) * count
    return tuple(v / FIXED for v in struct.unpack_from(f"<{count}q", node.data))


def _object(o: Node) -> RecordObject:
    kind = o.int("Type")
    name = o.value("Name") or ""
    if kind == STREET:  # Position .. Variation are not set for streets and multi-objects
        tiles = tuple((p.int("x"), p.int("y")) for p in (o.find("Positions") or Node("")).children)
        return RecordObject(kind, o.int("GUID"), name=name, tiles=tiles)
    if kind == MULTI:
        parts = []
        for p in (o.find("MultiObject") or Node("")).children:
            x, z, _y = _point(p.find("Position"), 3)
            parts.append((p.int("GUID"), x, z, p.int("Direction") / ANGLE, p.int("Variation")))
        return RecordObject(kind, 0, name=name, parts=tuple(parts))
    x, z, _y = _point(o.find("Position"), 3)
    return RecordObject(
        kind, o.int("GUID"), x, z, o.int("Direction") / ANGLE, o.int("Variation"), name, o.int("m_bSetOnWater") == 1
    )


def _node(n: Node) -> RecordNode:
    after = tuple(c.text for c in (n.find("DependencyList") or Node("")).children if c.text)
    rect = tuple(n.int(f"Rect/{side}") for side in ("left", "top", "right", "bottom"))
    objects = tuple(_object(o) for o in (n.find("BuildList") or Node("")).children)
    return RecordNode(n.value("Name") or "", n.int("IsMainNode") == 1, after, rect, objects)  # type: ignore[arg-type]


def read(data: bytes) -> RecordSet:
    """A record set from the file's bytes."""
    root = parse(data)
    anchor = _point(root.find("VillageCenter"), 2)
    nodes = [_node(n) for n in (root.find("RecordSet") or Node("")).children]
    return RecordSet((anchor[0], anchor[1]), nodes, root)
