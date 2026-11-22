"""Rivers, lakes, coast building lines, blocker shapes and the path blocker layers of a 2070-format island (``.isd``: 2070's, 1404's).

``network`` reads them for the editor, the ``apply_*`` functions write the editor's edits back into the tag tree
(``formats.isd.edit.CIsland2070.apply``). A river's build blocking outline and path blocking cells are derived from its
points, so an edited river gets them redrawn by ``river_blocking`` (rules fit to the vanilla rivers, island-editor
"Anno 2070 editing"); a river the edits do not name keeps every byte.
"""

from __future__ import annotations

import base64
import copy
import struct

import numpy as np

from ....formats.isd2070 import FIXED, SNode
from ....infra.i18n import N_, tr
from ....islands.islandgrid import edited_points, nearest_segments
from ... import groundedit

POINT_NAMES = (("p", "w"), ("position", "width"))  # a river point's position and width: 2070's, 1404's
BLOCKER_LAYERS = ("m_HeightPathBlockerLayer", "m_PathBlockerLayer")  # 2070 keeps both, 1404 the second
PATH_MARGIN = 0.25  # tiles a path cell centre may lie off a river's outline and still be blocked (vanilla: never less)
CELLS = 2  # path blocker cells per tile and axis
SHAPE_LABELS = {"build": N_("islands.build_blocker_shape"), "camera": N_("islands.camera_blocker_shape")}
SHAPES = {"build": "BuildBlockerShapes", "camera": "CamBlockerShapes"}  # the edit file's kinds and their lists


def _pairs(node: SNode | None, size_bytes: int = 16) -> list[SNode]:
    """The ``i`` children of a list that hold ``size`` bytes of data."""
    return [
        child
        for child in (node.children if node is not None else [])
        if child.data is not None and len(child.data) == size_bytes
    ]


def _fixed_xz(node: SNode) -> list[float]:
    x, z = struct.unpack("<qq", node.data)
    return [x / FIXED, z / FIXED]


def _names(item: SNode) -> tuple[str, str] | None:
    """The field names of the points of a river: 2070's ``p`` / ``w`` or 1404's ``position`` / ``width``."""
    pts = item.find("m_Points")
    first = pts.children[0] if pts is not None and pts.children else None
    return next(
        (pair_names for pair_names in POINT_NAMES if first is not None and first.find(pair_names[0]) is not None), None
    )


def _point_nodes(item: SNode) -> list[SNode]:
    names = _names(item)
    if names is None:
        return []
    return [
        point_node
        for point_node in item.find("m_Points").children
        if point_node.find(names[0]) is not None and point_node.find(names[0]).data
    ]


def river_items(root: SNode) -> list[SNode]:
    """The rivers with a course (two points or more), in the file's order: what an edit's river index counts."""
    rivers = root.find("Rivers")
    return [river for river in (rivers.children if rivers is not None else []) if len(_point_nodes(river)) >= 2]


def lake_items(root: SNode) -> list[SNode]:
    """The lakes with an outline (three points or more)."""
    lakes = root.find("Lakes")
    return [k for k in (lakes.children if lakes is not None else []) if len(_pairs(k.find("m_Points"))) >= 3]


def coast_items(root: SNode) -> list[SNode]:
    """The coast building lines with two points or more."""
    coast = root.find("CoastBuildingLines")
    return [
        coast_item
        for coast_item in (coast.children if coast is not None else [])
        if len(_pairs(coast_item.find("Points"))) >= 2
    ]


def river_points(item: SNode) -> tuple[list[list[float]], list[float]]:
    """A river's points (x, water height, z in tiles) and the width at each."""
    position_name, width = _names(item)
    points = [list(struct.unpack("<3f", point_node.find(position_name).data)) for point_node in _point_nodes(item)]
    return points, [float(point_node.value(width, 4)) for point_node in _point_nodes(item)]


def network(root: SNode) -> dict:
    """The island's water as the edit session takes it (``CEditSession.water_net``): rivers, lakes with their level
    and outline (x, z), coast lines (x, z)."""
    rivers = []
    for item in river_items(root):
        points, widths = river_points(item)
        rivers.append({"points": points, "widths": widths})
    lakes = [
        {
            "points": [_fixed_xz(node) for node in _pairs(k.find("m_Points"))],
            "level": k.int("m_WaterHeight") / FIXED,
            "damHeight": k.int("m_DamHeight") / FIXED,
        }
        for k in lake_items(root)
    ]
    coasts = [{"points": [_fixed_xz(node) for node in _pairs(coast.find("Points"))]} for coast in coast_items(root)]
    return {"rivers": rivers, "lakes": lakes, "coasts": coasts, "blockers": shape_network(root)}


# Rivers
def river_blocking(points, widths) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """The build blocking outline of a river (one bank forward, the other back: vanilla's within 0.02 tiles) and the
    centres of the path cells it blocks (a cell whose centre lies in the outline or within ``PATH_MARGIN`` of it:
    vanilla blocks no cell outside that, and about 18 % more inside)."""
    points_xz = np.asarray([[point[0], point[2]] for point in points], float)
    half = np.asarray(widths, float)[:, None] / 2
    direction = np.gradient(points_xz, axis=0)
    length = np.linalg.norm(direction, axis=1, keepdims=True)
    direction /= np.where(length == 0, 1, length)
    normal = np.stack([-direction[:, 1], direction[:, 0]], axis=1)
    outline = np.concatenate([points_xz - normal * half, (points_xz + normal * half)[::-1]])
    return [(float(x), float(z)) for x, z in outline], _blocked_cells(outline)


def _blocked_cells(outline: np.ndarray) -> list[tuple[float, float]]:
    min_corner, max_corner = np.floor(outline.min(axis=0)) - 1, np.ceil(outline.max(axis=0)) + 1
    cell_xs = (np.arange(min_corner[0] * CELLS, max_corner[0] * CELLS) + 0.5) / CELLS
    cell_zs = (np.arange(min_corner[1] * CELLS, max_corner[1] * CELLS) + 0.5) / CELLS
    grid_x, grid_z = np.meshgrid(cell_xs, cell_zs)
    cells = np.stack([grid_x.ravel(), grid_z.ravel()], axis=1)
    near = _inside(cells, outline) | (_distance(cells, outline) <= PATH_MARGIN)
    return [(float(x), float(z)) for x, z in cells[near]]  # x ascending, then z, as vanilla lists them


def _inside(cells: np.ndarray, poly: np.ndarray) -> np.ndarray:
    x, z = cells[:, 0], cells[:, 1]
    inside = np.zeros(len(cells), bool)
    for (start_x, start_z), (end_x, end_z) in zip(poly, np.roll(poly, -1, axis=0), strict=True):
        crosses = (start_z > z) != (end_z > z)
        inside ^= crosses & (x < (end_x - start_x) * (z - start_z) / (end_z - start_z + 1e-12) + start_x)
    return inside


def _distance(cells: np.ndarray, poly: np.ndarray) -> np.ndarray:
    best = np.full(len(cells), np.inf)
    for start, end in zip(poly, np.roll(poly, -1, axis=0), strict=True):
        segment = end - start
        along = np.clip(((cells - start) @ segment) / (segment @ segment + 1e-12), 0, 1)
        best = np.minimum(best, np.linalg.norm(cells - (start + along[:, None] * segment), axis=1))
    return best


def _copy_node(template: SNode | None, name: str, **fields) -> SNode:
    node = copy.deepcopy(template) if template is not None else SNode(name)
    for key, value in fields.items():
        setattr(node, key, value)
    return node


def _fixed_pair(x: float, z: float) -> bytes:
    return struct.pack("<qq", round(x * FIXED), round(z * FIXED))


def _set_pairs(parent: SNode, pairs, template: SNode | None) -> None:
    parent.children = [_copy_node(template, "i", text=None, data=_fixed_pair(x, z)) for x, z in pairs]


def _set_river(item: SNode, points, widths) -> bool:
    """Put edited points into a river; True when it moved or changed width (then its blocking is redrawn)."""
    position_name, width = _names(item)
    old = _point_nodes(item)
    before, old_widths = river_points(item)
    new_nodes = []
    for point, point_width in zip(points, widths, strict=True):
        nearest = min(range(len(before)), key=lambda i: (before[i][0] - point[0]) ** 2 + (before[i][2] - point[2]) ** 2)
        node = old[nearest]
        same = (
            tuple(np.float32(before[nearest])) == tuple(np.float32(point))
            and abs(old_widths[nearest] - point_width) < 5e-7
        )
        if not same:
            node = copy.deepcopy(old[nearest])
            node.find(position_name).data = struct.pack("<3f", *map(float, point))
            width_node = node.find(width)
            width_node.text = f"{float(point_width):.6f}"
        new_nodes.append(node)
    item.find("m_Points").children = new_nodes
    moved = len(points) != len(before) or any(
        abs(new_point[0] - old_point[0]) > 1e-6
        or abs(new_point[2] - old_point[2]) > 1e-6
        or abs(new_width - old_width) > 5e-7
        for new_point, old_point, new_width, old_width in zip(points, before, widths, old_widths, strict=False)
    )
    return moved


def _redraw_blocking(item: SNode, points, widths) -> None:
    outline, cells = river_blocking(points, widths)
    blocking = item.find("m_BuildBlocking")
    polygon = blocking.children[0] if blocking is not None and blocking.children else None
    if polygon is not None:
        _set_pairs(polygon, outline, next(iter(_pairs(polygon)), None))
        del blocking.children[1:]
    paths = item.find("m_PathBlockings")
    if paths is not None:
        _set_pairs(paths, cells, next(iter(_pairs(paths)), None))


def apply_rivers(root: SNode, rivers: dict) -> tuple[int, list[str]]:
    """Edited rivers (``"k"`` -> ``points``, ``widths``): returns how many were written and the unmatched."""
    items, done, stale = river_items(root), 0, []
    for k, change in sorted(rivers.items(), key=lambda kv: str(kv[0])):
        if not str(k).isdigit() or int(k) >= len(items) or not groundedit.river_edit_ok(change):
            stale.append(tr("islands.river_k", k=k))
            continue
        item = items[int(k)]
        if _set_river(item, change["points"], change["widths"]):
            _redraw_blocking(item, change["points"], change["widths"])
        done += 1
    return done, stale


# Lakes and coast lines
def apply_lakes(root: SNode, lakes: dict) -> tuple[int, list[str]]:
    """Edited lake levels (``"k"`` -> world units)."""
    items, done, stale = lake_items(root), 0, []
    for k, level in sorted(lakes.items(), key=lambda kv: str(kv[0])):
        if not str(k).isdigit() or int(k) >= len(items):
            stale.append(tr("islands.lake_k", k=k))
            continue
        node = items[int(k)].find("m_WaterHeight")
        node.text = str(round(float(level) * FIXED))
        done += 1
    return done, stale


def _directions(node: SNode, old_points, points) -> None:
    """A coast line with another number of points gets, per segment, the direction of the nearest old segment."""
    dirs = node.find("Directions")
    if dirs is None or len(dirs.children) == len(points) - 1:
        return
    old = list(dirs.children)
    out = [copy.deepcopy(old[min(k, len(old) - 1)]) for k in nearest_segments(old_points, points)]
    dirs.children = out


def apply_coast(root: SNode, coast: dict) -> tuple[int, list[str]]:
    """Edited coast building lines (``"k"`` -> ``points``: x, z in tiles)."""
    items, done, stale = coast_items(root), 0, []
    for k, change in sorted(coast.items(), key=lambda kv: str(kv[0])):
        if not str(k).isdigit() or int(k) >= len(items) or len(change["points"]) < 2:
            stale.append(tr("islands.coast_line_k", k=k))
            continue
        node = items[int(k)]
        old_nodes = _pairs(node.find("Points"))
        old_points = [_fixed_xz(point_node) for point_node in old_nodes]
        new_points = [(float(point[0]), float(point[1])) for point in change["points"]]
        rows = []
        for i, (x, z) in enumerate(new_points):
            source = old_nodes[min(i, len(old_nodes) - 1)]
            same = i < len(old_points) and abs(old_points[i][0] - x) < 1e-3 and abs(old_points[i][1] - z) < 1e-3
            rows.append(
                source if same and source.data == _fixed_pair(x, z) else _copy_node(source, "i", data=_fixed_pair(x, z))
            )
        node.find("Points").children = rows
        _directions(node, old_points, new_points)
        done += 1
    return done, stale


# Blocker shapes
def shape_items(root: SNode, kind: str) -> list[SNode]:
    """The ``kind`` (``SHAPES`` key) blocker shapes with a polygon of three points or more, in the file's order."""
    shapes = root.find(SHAPES[kind])
    return [i for i in (shapes.children if shapes is not None else []) if len(_pairs(i.find("Polygon"))) >= 3]


def shape_network(root: SNode) -> dict[str, list[dict]]:
    """The blocker shapes for the edit session: per kind the polygons' points (x, z in tiles)."""
    return {
        kind: [{"points": [_fixed_xz(node) for node in _pairs(i.find("Polygon"))]} for i in shape_items(root, kind)]
        for kind in SHAPES
    }


def apply_shapes(root: SNode, shapes: dict) -> tuple[int, list[str]]:
    """Edited blocker shapes (``{"build": {"k": {"points": ...}}, "camera": {...}}``): a polygon's points are written as
    the new pairs; returns how many were written and the unmatched."""
    done, stale = 0, []
    for kind in SHAPES:
        items = shape_items(root, kind)
        for k, change in sorted((shapes.get(kind) or {}).items(), key=lambda kv: str(kv[0])):
            points = edited_points(change, 3)
            if not str(k).isdigit() or int(k) >= len(items) or points is None:
                stale.append(tr(SHAPE_LABELS[kind], k=k))
                continue
            polygon = items[int(k)].find("Polygon")
            _set_pairs(polygon, points, next(iter(_pairs(polygon)), None))
            done += 1
    return done, stale


# Path blockers
def blocker_layers(root: SNode) -> list[SNode]:
    """The path blocker layers the island keeps (``BitGrid`` nodes), 2070's height layer first: the one that is edited."""
    manager = root.path("m_GOPManager/m_GRIDManager")
    out = []
    for name in BLOCKER_LAYERS:
        grid = manager.path(f"{name}/BitGrid") if manager is not None else None
        if grid is not None and grid.find("m_BitGrid") is not None and grid.find("m_BitGrid").data:
            out.append(grid)
    return out


def _bits(grid: SNode) -> np.ndarray:
    size, words = grid.int("m_XSize"), grid.int("m_IntXSize")
    flat = np.unpackbits(np.frombuffer(grid.find("m_BitGrid").data, np.uint8), bitorder="little")
    return flat.reshape(size, words * 32)


def blocker_cells(root: SNode) -> np.ndarray | None:
    """The cells (z, x; ``CELLS`` a tile) the editable blocker layer marks, or None without a layer."""
    layers = blocker_layers(root)
    if not layers:
        return None
    size = layers[0].int("m_XSize")
    return _bits(layers[0])[:, :size].astype(bool)


def blockers_json(root: SNode) -> dict | None:
    """The editable layer for the island JSON: its side in cells and the packed bits (rows = z)."""
    cells = blocker_cells(root)
    if cells is None:
        return None
    return {
        "n": int(cells.shape[0]),
        "bits": base64.b64encode(np.packbits(cells, bitorder="little").tobytes()).decode(),
    }


def apply_blockers(root: SNode, cells: dict) -> tuple[int, list[str]]:
    """Painted blocker cells (``"cx,cz"`` -> 1 blocked, 0 free) into the editable layer (``blocker_layers``[0]): 2070's
    height layer, 1404's only one. 2070's general layer is left alone (44 of the 285 islands have height-layer cells
    outside it, so the game combines the two)."""
    layers = blocker_layers(root)
    if not cells or not layers:
        return 0, [tr("islands.path_cell_k", k=k) for k in cells]
    layer = layers[0]
    bits = _bits(layer).copy()
    size = layer.int("m_XSize")
    done, stale = 0, []
    for k, value in sorted(cells.items()):
        cell_x, cell_z = map(int, k.split(","))
        if not (0 <= cell_x < size and 0 <= cell_z < size):
            stale.append(tr("islands.path_cell_k", k=k))
            continue
        bits[cell_z, cell_x] = 1 if value else 0
        done += 1
    layer.find("m_BitGrid").data = np.packbits(bits, bitorder="little").tobytes()
    return done, stale


def apply(root: SNode, edits: dict) -> tuple[dict, list[str]]:
    """Every water and blocker edit of an edit file; returns the counts and what matched nothing."""
    report, stale = {}, []
    for label, key, handler in (
        ("rivers", "rivers", apply_rivers),
        ("lakes", "lakes", apply_lakes),
        ("coast lines", "coast", apply_coast),
        ("path cells", "path_blockers", apply_blockers),
        ("blocker shapes", "blocker_shapes", apply_shapes),
    ):
        report[label], missed = handler(root, edits.get(key) or {})
        stale += missed
    return report, stale
