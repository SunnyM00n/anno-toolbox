"""An island's slot list: the ``.a7minfo``'s ``ObjectMetaInfo/RandomSlotObjects``, per slot asset the id and the place of
every slot object the island stands. The map generator reads it to decide which slots an island offers, so it has to
follow the objects: a slot moved, deleted or added by hand is listed where it now is (island-editor "The slot list").

``read`` and ``write`` are the list as plain data, ``refresh`` brings it in line with the placed objects.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable

from ..formats import filedb

NODE = "ObjectMetaInfo/RandomSlotObjects"
GUID = "#32768"  # the attribute that opens a slot asset's entries (the group node follows it)

Slot = tuple[bytes, tuple[float, float, float]]  # the object's ID (eight bytes) and its position


def read(info: filedb.Tag) -> dict[int, list[Slot]]:
    """The list as the file holds it: slot asset GUID -> its slots, in file order; empty without the node."""
    node = info.path(NODE)
    out: dict[int, list[Slot]] = {}
    guid = None
    for child in node.children if node is not None else []:
        if isinstance(child, filedb.Attrib) and child.name == GUID:
            guid = struct.unpack_from("<I", child.data)[0]
        elif isinstance(child, filedb.Tag) and guid is not None:
            out[guid] = [
                (e.find("ObjectId").data, struct.unpack("<3f", e.find("Position").data))
                for e in child.children
                if isinstance(e, filedb.Tag) and e.find("ObjectId") is not None and e.find("Position") is not None
            ]
            guid = None
    return out


def write(info: filedb.Tag, slots: dict[int, list[Slot]]) -> bool:
    """Replace the list by ``slots``; False when the file has no node for it (a donor without the list)."""
    node = info.path(NODE)
    if node is None:
        return False
    children: list = []
    for guid, entries in slots.items():
        children.append(filedb.Attrib(GUID, struct.pack("<I", guid)))
        group = filedb.Tag("#1")
        for oid, position in entries:
            group.children.append(
                filedb.Tag(
                    "#1",
                    [filedb.Attrib("ObjectId", oid), filedb.Attrib("Position", struct.pack("<3f", *position))],
                )
            )
        children.append(group)
    node.children = children
    return True


def refresh(info: filedb.Tag, placed: Iterable[tuple[bytes, int, tuple[float, float, float]]]) -> dict[str, int]:
    """Bring the list in line with the objects (``placed``: each object's ID, asset GUID and position). The assets the
    list names keep their order and their slots theirs; a slot whose object is gone is dropped, one that moved takes
    its object's place, an object of a listed asset that is not listed yet is added at the end. Returns what changed
    (``moved``, ``dropped``, ``added``), empty and the file untouched when nothing did."""
    listed = read(info)
    if not listed:
        return {}
    where: dict[int, dict[bytes, tuple[float, float, float]]] = {guid: {} for guid in listed}
    for oid, guid, position in placed:
        if guid in where:
            where[guid][oid] = tuple(float(v) for v in position)
    report = {"moved": 0, "dropped": 0, "added": 0}
    result: dict[int, list[Slot]] = {}
    for guid, entries in listed.items():
        kept: list[Slot] = []
        for oid, position in entries:
            now = where[guid].pop(oid, None)
            if now is None:
                report["dropped"] += 1
                continue
            report["moved"] += int(struct.pack("<3f", *now) != struct.pack("<3f", *position))
            kept.append((oid, now))
        report["added"] += len(where[guid])
        result[guid] = kept + list(where[guid].items())
    if not any(report.values()):
        return {}
    write(info, result)
    return {k: v for k, v in report.items() if v}
