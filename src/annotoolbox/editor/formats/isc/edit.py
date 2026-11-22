"""Anno 1701 editing of the island editor: an edit file (``objects``, ``added_objects``, ``heights``, ``paint``, the
water tool's ``rivers`` and ``coast``, ``properties``, ``clear``) applied to an ``isd1701.CIsland1701`` that the reader
opened.

``apply(island, edits)`` is the entry; it changes the island in place and returns the report, ``island.write()`` gives the
``.isd`` and ``island.write_header()`` the ``.isc`` (rivers, coast lines and properties live there). The reader
(``othergames/isd1701``) knows the format, this module only the editor's changes to it.
"""

from __future__ import annotations

import copy
import math
import re
import struct
from collections.abc import Callable

from ....formats.isd2070 import SNode
from ....islands import islandedits, objectedits
from ....islands.islandground import stroke_cover, stroke_material
from ....othergames import grid1701
from ....othergames.isd1701 import FIXED, TILE, TILE_RECORD, VERTEX, CIsland1701
from ..isd import props as isd_props
from . import water as isc_water

KEY_PREFIX = "anno1701:"  # an object's name in the edit file: this and its index in ``CIsland1701.objects``
PAINT_COVER = 0.5  # a ground tile takes a painted texture where the brush covers at least this much of it


def apply(island: CIsland1701, edits: dict, shapes: Callable[[], dict[int, grid1701.SShape]] | None = None) -> dict:
    """Apply an edit file and say what it changed; an object it names that the island does not have is listed as
    ``unmatched``. A moved, added or deleted object's tiles of the occupation grid follow (``occupation``);
    ``shapes`` gives the footprints of the assets the island has none of (``grid1701.scan_shapes``)."""
    report = {"objects changed": 0, "objects deleted": 0, "objects added": 0, "unmatched": []}
    if edits.get("clear"):
        report["cleared"] = clear(island)
    known = grid1701.island_shapes(island) if edits.get("objects") or edits.get("added_objects") else {}
    moved: list[SNode] = []
    apply_objects(island, edits.get("objects", {}), report, moved)
    added = apply_added(island, edits.get("added_objects", []), report)
    tiles = occupation(island, moved, added, known, shapes)
    if tiles:
        report["grid tiles"] = tiles
    report["heights"] = apply_heights(island, edits.get("heights", {}))
    report["paint"] = apply_paint(island, edits.get("paint", []))
    water, stale = isc_water.apply(island, edits)
    report |= {name: count for name, count in water.items() if count}
    report["unmatched"] += stale
    if island.m_header is not None and edits.get("properties"):
        report["properties"] = isd_props.apply(
            island.m_header, edits["properties"], isd_props.HEADER_1701, isd_props.RANGES_1701
        )
    return report


def clear(island: CIsland1701) -> dict:
    """A blank island: every game object and prop deleted (the grid tiles they held freed), the rivers emptied.
    The terrain, the coast lines and the logic grids stay. Returns what was removed."""
    count = len(island.object_nodes())
    gone = {f"{KEY_PREFIX}{k}": {"deleted": True} for k in range(count)}
    apply_objects(island, gone, {"objects deleted": 0, "unmatched": []})
    return {"objects": count, "rivers": isc_water.clear(island)}


def apply_objects(island: CIsland1701, changes: dict, report: dict, moved: list[SNode] | None = None) -> None:
    """Move and delete the objects ``changes`` names; the game objects moved are appended to ``moved``."""

    def move(handle: tuple[str, SNode], change: dict) -> None:
        kind, node = handle
        (_move_hid if kind == "HID" else _move_pid)(node, change)
        if kind == "HID" and moved is not None:
            moved.append(node)

    def remove(gone: list[tuple[str, SNode]]) -> None:
        for kind, node in gone:
            if kind == "HID":
                grid1701.vacate(island, int(node.name, 16))
        for manager in ("m_HIDManager", "m_PIDManager"):
            node = island.m_root.path(f"m_GOPManager/{manager}")
            if node is not None:
                node.children = [
                    child for child in node.children if not any(child is removed_handle[1] for removed_handle in gone)
                ]

    keyed = ((f"{KEY_PREFIX}{k}", handle) for k, handle in enumerate(island.object_nodes()))
    objectedits.apply_changes(keyed, changes, objectedits.SObjectOps(move, remove), report)


def apply_added(island: CIsland1701, entries: list[dict], report: dict) -> list[SNode]:
    """New objects (``added_objects`` with a ``guid``): each a copy of the island's first game object of that asset,
    else of its first game object at all, under a handle of its own (the next number after ``m_LastHID``, the sample's
    high word), turned and placed as the entry says. Returns the new nodes; the grid fields not set by ``occupation``
    are the sample's (open: whether the game takes the grid position from them, island-editor "1701")."""
    manager = island.m_root.path("m_GOPManager/m_HIDManager")
    nodes = [node for kind, node in island.object_nodes() if kind == "HID"]
    made: list[SNode] = []

    def add(entry: dict) -> bool:
        guid = entry.get("guid")
        sample = next((node for node in nodes if node.int("m_GUID") == int(guid or -1)), nodes[0] if nodes else None)
        if guid is None or sample is None or manager is None:
            return False
        new = copy.deepcopy(sample)
        new.name = _next_handle(manager, sample.name)
        _set_text(new, "m_GUID", str(int(guid)))
        if entry.get("variation") or new.find("m_Variation") is not None:
            _set_text(new, "m_Variation", str(int(entry.get("variation", 0))))
        _move_hid(new, {"position": entry["position"], "rotation": entry.get("rotation", [0, 0, 0, 1])})
        manager.children.append(new)
        made.append(new)
        return True

    objectedits.add_each(entries, add, lambda entry: f"added guid:{entry.get('guid')}", report)
    return made


def occupation(
    island: CIsland1701,
    moved: list[SNode],
    added: list[SNode],
    known: dict[int, grid1701.SShape],
    shapes: Callable[[], dict[int, grid1701.SShape]] | None,
) -> int:
    """The occupation grid after the edits: each moved or new object holds the tiles of its asset's footprint at its
    place and turn (``grid1701.occupy``); a new object also takes the footprint's grid offsets. An asset with no known
    footprint (a tree, a prop) holds none. Returns the tiles held."""
    new = {id(candidate) for candidate in added}
    wanted = [candidate for candidate in moved if candidate.int("m_GUID") in known] + added
    if grid1701.grid_of(island) is None or not wanted:
        return 0
    table = dict(known)
    if any(candidate.int("m_GUID") not in table for candidate in added) and shapes is not None:
        table = {**shapes(), **known}
    total = 0
    for node in wanted:
        shape = table.get(node.int("m_GUID"))
        if shape is None:
            continue
        if id(node) in new:
            _set_text(node, "m_GRIDOffsetX", str(shape.ox))
            _set_text(node, "m_GRIDOffsetZ", str(shape.oz))
        total += grid1701.occupy(island, node, shape)
    return total


def _next_handle(manager: SNode, like: str) -> str:
    """A handle no object of the manager has: the sample's high word, one past ``m_LastHID`` and every low word."""
    last = manager.find("m_LastHID")
    lows = [int(child.name, 16) & 0xFFFF for child in manager.children if re.fullmatch(r"[0-9A-F]{8}", child.name)]
    low = max([int(last.text or 0) if last is not None else 0, *lows]) + 1
    if last is not None:
        last.text = str(low)
    return f"{int(like, 16) >> 16:04X}{low:04X}"


def apply_heights(island: CIsland1701, heights: dict) -> int:
    """Raise or lower vertices (``"z,x"`` -> world units / ``TILE``), with the normals of the tiles and vertices
    around them and the logic grid's height (``m_HeightMap``, a tile's height in world units)."""
    import numpy as np

    width_vertices, height_vertices = island.m_width + 1, island.m_height + 1
    delta = islandedits.height_grid(heights, height_vertices, width_vertices) * TILE
    if not delta.any():
        return 0
    grid = np.array([vertex[1] for vertex in island.m_vertices], float).reshape(height_vertices, width_vertices) + delta
    for i in np.flatnonzero(delta.ravel()):
        vertex = island.m_vertices[i]
        island.m_vertices[i] = (vertex[0], float(grid.ravel()[i]), *vertex[2:])
    touched = (delta[:-1, :-1] != 0) | (delta[1:, :-1] != 0) | (delta[:-1, 1:] != 0) | (delta[1:, 1:] != 0)
    tile_n1, tile_n2, vertex_n = _normals(grid)
    for j, i in zip(*np.nonzero(touched), strict=True):
        tile_record = island.m_tile_records[j * island.m_width + i]
        island.m_tile_records[j * island.m_width + i] = (
            *tile_record[:4],
            *map(float, tile_n1[j, i]),
            *map(float, tile_n2[j, i]),
        )
    around = np.zeros((height_vertices, width_vertices), bool)
    for row_offset, column_offset in ((0, 0), (0, 1), (1, 0), (1, 1)):
        around[row_offset : row_offset + island.m_height, column_offset : column_offset + island.m_width] |= touched
    for j, i in zip(*np.nonzero(around), strict=True):
        vertex = island.m_vertices[j * width_vertices + i]
        island.m_vertices[j * width_vertices + i] = (*vertex[:3], *map(float, vertex_n[j, i]), *vertex[6:])
    terrain = island.m_root.path("Terrain")
    terrain.path("HeightMap").data = b"".join(VERTEX.pack(*vertex) for vertex in island.m_vertices)
    terrain.path("TileMap").data = b"".join(TILE_RECORD.pack(*tile_record) for tile_record in island.m_tile_records)
    _lift_logic_grid(island, delta)
    return int(np.count_nonzero(delta))


def _lift_logic_grid(island: CIsland1701, delta) -> None:
    import numpy as np

    node = island.m_root.path("m_GOPManager/m_GRIDManager/m_HeightMap")
    if node is None or node.data is None or len(node.data) != island.m_width * island.m_height * 2:
        return
    mean = (delta[:-1, :-1] + delta[1:, :-1] + delta[:-1, 1:] + delta[1:, 1:]) / 4
    grid = np.frombuffer(node.data, "<i2").reshape(island.m_height, island.m_width).astype(np.int32)
    node.data = np.clip(grid + np.round(mean), -32768, 32767).astype("<i2").tobytes()


def apply_paint(island: CIsland1701, strokes: list[dict]) -> int:
    """Lay painted strokes (``material``: a palette texture id) over the ground: a tile takes the texture
    where a stroke covers at least ``PAINT_COVER`` of it (1701 blends nothing)."""
    done = 0
    size_tiles = max(island.m_width, island.m_height)
    for stroke in strokes:
        guid = stroke_material(stroke)
        hit = stroke_cover(stroke, size_tiles, 1)[: island.m_height, : island.m_width] >= PAINT_COVER
        if not 0 <= guid < 65536 or not hit.any():
            continue
        for j, i in zip(*hit.nonzero(), strict=True):
            tile_record = island.m_tile_records[j * island.m_width + i]
            island.m_tile_records[j * island.m_width + i] = (*tile_record[:2], guid, *tile_record[3:])
        done += 1
    if done:
        island.m_root.path("Terrain/TileMap").data = b"".join(
            TILE_RECORD.pack(*tile_record) for tile_record in island.m_tile_records
        )
    return done


def _normals(heights):
    """The tile normals (two triangles a tile, ``corner_a corner_b corner_c`` and ``corner_d corner_c corner_b`` for the corners (x, z), (x+1, z), (x, z+1),
    (x+1, z+1)) and the vertex normals (minus the mean of the unit normals of the triangles at the vertex) of a
    height grid: the rule 1701's files follow to the last float (anno1701-world-format "Island data")."""
    import numpy as np

    z, x = np.mgrid[0 : heights.shape[0], 0 : heights.shape[1]]
    points = np.stack([x * TILE, heights, z * TILE], axis=-1)
    corner_a, corner_b, corner_c, corner_d = points[:-1, :-1], points[:-1, 1:], points[1:, :-1], points[1:, 1:]

    def unit(vector):
        return vector / np.maximum(np.linalg.norm(vector, axis=-1, keepdims=True), 1e-12)

    first_normals, second_normals = (
        unit(np.cross(corner_b - corner_a, corner_c - corner_a)),
        unit(np.cross(corner_d - corner_b, corner_c - corner_b)),
    )
    total, count = np.zeros_like(points), np.zeros(points.shape[:2] + (1,))
    row_count, column_count = first_normals.shape[:2]
    for normal, corners in ((first_normals, ((0, 0), (0, 1), (1, 0))), (second_normals, ((0, 1), (1, 0), (1, 1)))):
        for row_offset, column_offset in corners:
            total[row_offset : row_offset + row_count, column_offset : column_offset + column_count] += normal
            count[row_offset : row_offset + row_count, column_offset : column_offset + column_count] += 1
    return first_normals, second_normals, -total / count


def _set_text(parent: SNode, name: str, text: str) -> None:
    node = parent.find(name)
    if node is None:
        node = SNode(name)
        parent.children.append(node)
    node.text = text


def _turn(change: dict) -> float:
    """The turn about the vertical a quaternion edit means (radians, 0 to 2 pi)."""
    quaternion = change["rotation"]
    return (-2.0 * math.atan2(quaternion[1], quaternion[3])) % (2 * math.pi)


def _move_hid(node: SNode, change: dict) -> None:
    """A game object moves (logic position, fixed point) and turns (``m_Direction``, radians x 4096)."""
    if "position" in change:
        position_node = node.find("m_Position")
        values = [round(float(coordinate) * FIXED) for coordinate in change["position"]]  # x, y / TILE, z: tiles
        data = position_node.find("data")
        if data is not None and data.data is not None and len(data.data) == 12:
            data.data = struct.pack("<3i", *values)
        else:
            for name, value in zip(("m_x", "m_y", "m_z"), values, strict=True):
                _set_text(position_node, name, str(value))
    if "rotation" in change:
        _set_text(node, "m_Direction", str(round(_turn(change) * FIXED)))
        if node.find("m_GRIDDirection") is not None:  # the grid's turn in 64ths of a full turn (world-format "Objects")
            _set_text(node, "m_GRIDDirection", str(round(_turn(change) / (2 * math.pi) * 64) % 64))


def _move_pid(node: SNode, change: dict) -> None:
    """A prop moves (world units), turns (``m_Direction``, radians) and scales (``m_Size``)."""
    if "position" in change:
        for name, coordinate in zip("xyz", change["position"], strict=True):
            _set_text(node, f"m_Position.{name}", f"{float(coordinate) * TILE:.6f}")
    if "rotation" in change:
        _set_text(node, "m_Direction", f"{_turn(change):.6f}")
    if "scale" in change:
        scale_value = change["scale"]
        for name in "xyz":
            _set_text(
                node, f"m_Size.{name}", f"{float(scale_value[0] if isinstance(scale_value, list) else scale_value):.6f}"
            )
