"""The water of a built 117 island: the lakes (2070's outlines carved into basins), the river, lake and coast line
editor objects cloned from vanilla and filled with splines, and the sea shading maps. The rivers' course is
``rivercourse``.
"""

from __future__ import annotations

import copy
import math
import struct
from dataclasses import dataclass

from ..formats import filedb
from .islandgrid import (
    FIXED,
    HEIGHT_UNITS,
    dist_to_polygon,
    edited_points,
    fixed_pairs,
    point_in_polygon,
    read_river_grid,
    warn_unmatched,
)
from .islandground import beach_map
from .riverlook import RENDER_BLEND_BOTH, CRiverFields, flow_speeds, look_fields, river_view_depth, set_look

LAKE_DEPTH = 0.4  # the bed inside a lake outline is carved at least this far below the water
LAKE_RIM = 0.1  # the terrain along the outline is raised at least this far above it
# rd3d WaterInfos (one cell per 2x2 tiles; byte 0 = water depth code for the sea shader: 0 on land,
# 255 * (depth / 5.2)^1.5, 255 from about 5 units) and WaterFlow (bytes 0/1 = flow vector, 127 = none; byte 2 =
# shore mask 255 * (distance to land / 12)^2) as measured on vanilla
WATER_DEPTH_FULL = 5.2
SHORE_MASK_FULL = 12.0
# 2070's look per island (rendering-2070 3.4-3.5): a river and a lake setting named after the island's file
# (``A5_Default`` else), each with a water colour; a lake's also with MaxDepth, the view depth at which it shows its
# colour at the colour's alpha. The eco variants (Dystopia, Utopia) belong to the eco balance and are left out
WATER_SETTINGS = {"River": "data/config/engine/riversettings.xml", "Lake": "data/config/engine/lakesettings.xml"}
DEFAULT_SETTING = "a5_default"


@dataclass
class SSpline:
    kind: str  # River, Lake, CoastLine
    points: list[tuple[float, float, float]]  # 117 (x, height, z)
    widths: list[float] | None = None
    normals: list[tuple[float, float]] | None = None
    speeds: list[tuple[float, float, float]] | None = None  # a river's flow (left, centre, right) per point
    view_depth: float | None = None  # a river's ViewDepth from 2070's depth scale (riverlook.river_view_depth)


def depth_code(depth):
    """The ``WaterInfos`` depth byte of the sea over ground ``depth`` world units down (0 on land)."""
    import numpy as np

    return np.rint(np.where(depth <= 0, 0, 255 * np.clip(depth / WATER_DEPTH_FULL, 0, 1) ** 1.5)).astype(np.uint8)


def read_water_settings(tree, kind: str) -> dict[str, dict]:
    """Setting name (lower case) -> ``Color`` (r, g, b, a) and, for a lake, ``MaxDepth``, from 2070's river or lake
    settings (``kind``: ``River`` or ``Lake``; ``tree``: the ``SourceTree``)."""
    import re

    text = tree.read_bytes(WATER_SETTINGS[kind]).decode("utf-8", "ignore")
    out: dict[str, dict] = {}
    for block in re.findall(rf"<{kind}Setting>(.*?)</{kind}Setting>", text, re.S):
        name, colour = re.search(r"<Name>([^<]*)</Name>", block), re.search(r"<Color>(.*?)</Color>", block, re.S)
        if not name or not colour:
            continue
        values: dict = {
            "Color": tuple(float(re.search(rf"<{channel}>([^<]*)</", colour.group(1))[1]) for channel in "rgba")
        }
        if depth := re.search(r"<MaxDepth>([^<]*)</MaxDepth>", block):
            values["MaxDepth"] = float(depth[1])
        out[name[1].lower()] = values
    return out


def water_look(tree, island_file: str) -> dict[str, dict]:
    """The river and the lake setting 2070 draws an island's water with (``island_file``: its ``.isd``): kind ->
    values (``read_water_settings``), the island's own or the default."""
    stem = island_file.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    out = {}
    for kind in WATER_SETTINGS:
        settings = read_water_settings(tree, kind)
        if found := settings.get(stem, settings.get(DEFAULT_SETTING)):
            out[kind] = found
    return out


def lake_network(lakes, levels) -> list[dict]:
    """The lakes of ``<island>.water.json``: outline, level and ``damHeight``, how far a hydroelectric plant raises the
    lake (2070's ``m_DamHeight``; 0: no plant dams it; island-slots "The dam slot"). ``lakes``: ``(2070 lake,
    outline)`` pairs as ``_lake_polygons`` yields them."""
    return [
        {"points": [list(p) for p in poly], "level": level, "damHeight": item.int("m_DamHeight") / FIXED}
        for (item, poly), level in zip(lakes, levels, strict=False)
    ]


# Object IDs and spline objects
def last_serial(objects: filedb.STag, high: bytes) -> int:
    """The highest low word among the object IDs whose high word (the type) is ``high``; 0 with none."""
    return max(
        (
            struct.unpack_from("<I", child.find("ID").data)[0]
            for child in objects.children
            if isinstance(child, filedb.STag) and child.find("ID") is not None and child.find("ID").data[4:] == high
        ),
        default=0,
    )


def next_serial(objects: filedb.STag, template: filedb.STag) -> int:
    """A free low word for the object ID (the high word, kept from the template, holds the type)."""
    return last_serial(objects, template.find("ID").data[4:]) + 1


def clone_object(template: filedb.STag, serial: int, guid: int | None = None) -> filedb.STag:
    """A copy of an object template numbered ``serial`` (its type word kept) and, with ``guid``, standing for that
    asset: the start of every object the builders add."""
    clone = copy.deepcopy(template)
    if guid is not None:
        clone.find("guid").data = struct.pack("<I", guid) + clone.find("guid").data[4:]
    clone.find("ID").data = struct.pack("<I", serial) + clone.find("ID").data[4:]
    return clone


def _handle(previous_point, point, next_point) -> tuple[float, float, float]:
    """A spline point's out handle: Catmull-Rom across (x, z); the height's only where the water keeps falling on both
    sides, and no longer than a third of the smaller fall, so the curve never rises over a pool before a step or dips
    under the pool after it (a hump at every step, in game 2026-10-23)."""
    up, down = point[1] - previous_point[1], next_point[1] - point[1]
    y = 0.0
    if up * down > 0:
        y = math.copysign(min(abs(next_point[1] - previous_point[1]) / 6, abs(up) / 3, abs(down) / 3), down)
    return ((next_point[0] - previous_point[0]) / 6, y, (next_point[2] - previous_point[2]) / 6)


def fill_spline(editor_object: filedb.STag, spline: SSpline, serial: int, place=None) -> None:
    """Replace a cloned editor object's position, ID and Bezier curve with the spline's points. ``place(obj, point)``
    sets the position where the object stores it other than as three floats (2205: fixed point)."""
    editor_object.find("ID").data = struct.pack("<I", serial) + editor_object.find("ID").data[4:]
    if place is None:
        editor_object.find("Position").data = struct.pack("<3f", *spline.points[0])
    else:
        place(editor_object, spline.points[0])
    path = editor_object.path("BezierPath/Path")
    xs, ys, zs = zip(*spline.points, strict=False)
    path.find("Minimum").data = struct.pack("<3f", min(xs), min(ys), min(zs))
    path.find("Maximum").data = struct.pack("<3f", max(xs), max(ys), max(zs))
    curve = path.find("BezierCurve")
    protos = [child for child in curve.children if isinstance(child, filedb.STag)]
    point_count = len(spline.points)
    closed = spline.kind == "Lake"
    items = []
    for k, point in enumerate(spline.points):
        previous_point = spline.points[(k - 1) % point_count] if (closed or k > 0) else point
        next_point = spline.points[(k + 1) % point_count] if (closed or k < point_count - 1) else point
        # a lake is 2070's polygon, which its basin is carved to: straight sides (smooth handles bulged the water up
        # to 2.5 tiles past the basin's rim)
        handle = (0.0, 0.0, 0.0) if closed else _handle(previous_point, point, next_point)
        # vanilla rivers carry different per-point values (u0) on the first point than on the rest
        point_node = copy.deepcopy(protos[0] if k == 0 or len(protos) == 1 else protos[1])
        point_node.find("p").data = struct.pack("<3f", *point)
        if point_node.find("i") is not None:
            point_node.find("i").data = struct.pack("<3f", *(-component for component in handle))
        if point_node.find("o") is not None:
            point_node.find("o").data = struct.pack("<3f", *handle)
        if point_node.find("w") is not None and spline.widths:
            point_node.find("w").data = struct.pack("<f", spline.widths[k])
        if point_node.find("u0") is not None and spline.speeds:  # the template's first point flows at full speed
            point_node.find("u0").data = struct.pack("<3f", *spline.speeds[k])
        if point_node.find("d") is not None and spline.normals:
            direction = (
                spline.normals[k] if k < point_count - 1 else (1.0, 0.0)
            )  # vanilla: the last point carries (1, 0)
            point_node.find("d").data = struct.pack("<2f", *direction)
        items.append(point_node)
    curve.children = [child for child in curve.children if not isinstance(child, filedb.STag)] + items


class CWaterMixin:
    """The methods of ``CIslandBuilder`` that build the lakes, the water objects and the water maps."""

    def river_mask(self) -> list[list[int]]:
        """2070's ``RiverGrid`` (the tiles 2070 counts as river) in the builder's tiles."""
        size = self.size
        grid = [[0] * size for _ in range(size)]
        rows = read_river_grid(self.island.m_root.path("RiverGrid")) or []
        for z, row in enumerate(rows[: self.island.m_height]):
            for x, v in enumerate(row[: self.island.m_width]):
                if v:
                    grid[z + self.pad_z][x + self.pad_x] = 1
        return grid

    # Lakes
    def _lake_polygons(self):
        """(2070 lake item, its outline in the builder's tiles) for every lake with an outline; the editor's outline
        (``water_edits["lake_outlines"]``, the lake's number as ``lake_levels`` counts) replaces 2070's."""
        px, pz = self.pad_x, self.pad_z
        lakes = self.island.m_root.path("Lakes")
        edits = self.water_edits.get("lake_outlines", {})
        k = 0
        for item in lakes.children if lakes else []:
            poly = (
                [(x + px, z + pz) for x, z in fixed_pairs(item.path("m_Points").children)]
                if item.path("m_Points")
                else []
            )
            if len(poly) >= 3:
                yield item, edited_points(edits.get(str(k)), 3) or poly
                k += 1

    def _carve_lakes(self, heights, is_carved: bool = True, is_whole: bool = True) -> list[float]:
        """Lake levels (world units): the 2070 level, or the editor's. With ``is_carved`` the ground inside the outline
        that lies below the level is deepened to ``LAKE_DEPTH`` under it, so the water is 2070's where 2070 has it
        (a dam lake's outline is its full basin: carved whole, the valley flooded at the unbuilt level, n_l14 in game
        2026-11-02). ``is_whole`` (an earlier patch): the whole outline carved and the terrain within a tile outside it
        raised ``LAKE_RIM`` over the level. ``heights`` (heightmap units per corner, numpy) is changed in place."""
        levels = []
        corner_count = heights.shape[0]
        for item, poly in self._lake_polygons():
            level = item.int("m_WaterHeight") / FIXED
            level = float(self.water_edits.get("lakes", {}).get(str(len(levels)), level))
            if not is_carved:
                levels.append(level)
                continue
            x0, x1 = int(min(x for x, _ in poly)) - 2, int(max(x for x, _ in poly)) + 3
            z0, z1 = int(min(z for _, z in poly)) - 2, int(max(z for _, z in poly)) + 3
            for z in range(max(0, z0), min(corner_count, z1)):
                for x in range(max(0, x0), min(corner_count, x1)):
                    if point_in_polygon(x, z, poly):
                        if is_whole or heights[z, x] < level * HEIGHT_UNITS:
                            heights[z, x] = min(heights[z, x], (level - LAKE_DEPTH) * HEIGHT_UNITS)
                    elif is_whole and dist_to_polygon(x, z, poly) <= 1.5:
                        heights[z, x] = max(heights[z, x], (level + LAKE_RIM) * HEIGHT_UNITS)
            levels.append(level)
        for key in ("lakes", "lake_outlines"):
            warn_unmatched(self.water_edits.get(key, {}), len(levels), "lake", "lakes")
        return levels

    # The water maps
    def apply_water_maps(self) -> None:
        """Regenerate the rd3d sea shading maps from the new terrain (the donor's describe the donor's coast: depth 0
        over our shallows, deep water over our land): ``WaterInfos`` byte 0 = depth code, byte 1 = coast waves (255
        everywhere, lakes too, as vanilla), byte 2 = beach (wet sand, calm water, no cliff foam: ``beach_map``; none
        on a lake, where the byte is duckweed), byte 3 = painted foam (none); ``WaterFlow`` = no flow and the shore
        mask (rendering/water.md)."""
        import numpy as np
        from scipy import ndimage

        water_infos = self.rd3d.path("WaterInfos")
        if water_infos is None:
            return
        map_size = water_infos.find("width").u32()
        cell = self.size / map_size
        corner_heights = np.asarray(self.heights, np.float32)
        cell_centres = ((np.arange(map_size) + 0.5) * cell).astype(int)  # cell centres on the corner grid
        depth = -corner_heights[np.ix_(cell_centres, cell_centres)] / HEIGHT_UNITS
        land = depth <= 0
        info_map = np.zeros((map_size, map_size, 4), np.uint8)
        info_map[..., 0] = depth_code(depth)
        info_map[..., 1] = 255
        info_map[..., 2] = beach_map(-depth, cell)
        # a lake near the sea's level (n_m05's reservoir) got the beach's band as a brown carpet where its river
        # comes in (in game, 2026-10-23); vanilla paints duckweed on few cells
        centres = (np.arange(map_size) + 0.5) * cell
        for _, poly in self._lake_polygons():
            xs, zs = [x for x, _ in poly], [z for _, z in poly]
            for iz in np.flatnonzero((centres >= min(zs) - 1) & (centres <= max(zs) + 1)):
                for ix in np.flatnonzero((centres >= min(xs) - 1) & (centres <= max(xs) + 1)):
                    cx, cz = centres[ix], centres[iz]
                    if point_in_polygon(cx, cz, poly) or dist_to_polygon(cx, cz, poly) <= 1.0:
                        info_map[iz, ix, 2] = 0
        water_infos.find("map").data = info_map.tobytes()
        water_flow = self.rd3d.path("WaterFlow")
        if water_flow is not None and water_flow.find("width").u32() == map_size:
            dist = ndimage.distance_transform_edt(~land) * cell  # tiles to land
            flow = np.zeros((map_size, map_size, 4), np.uint8)
            flow[..., 0] = flow[..., 1] = 127
            flow[..., 2] = np.rint(255 * np.clip(dist / SHORE_MASK_FULL, 0, 1) ** 2).astype(np.uint8)
            water_flow.find("map").data = flow.tobytes()

    # Editor objects
    def splines(self) -> list[SSpline]:
        """The rivers (with their flow and view depth from 2070's point fields, ``riverlook``), the lakes at their
        level and the coast lines."""
        found = CRiverFields(list(self._river_piece_fields()) if hasattr(self, "_river_piece_fields") else [])
        out = []
        for pts, widths in self.rivers:
            fields = [found.at(x, z) for x, _, z in pts]
            speeds, depth = flow_speeds(fields), river_view_depth(pts, fields)
            out.append(SSpline("River", pts, widths=widths, speeds=speeds, view_depth=depth))
        for (_item, poly), level in zip(self._lake_polygons(), self.lake_levels, strict=False):
            out.append(SSpline("Lake", [(x, level, z) for x, z in poly]))
        return out + self.coast_splines()

    def add_splines(self) -> None:
        """Clone the vanilla editor objects for every spline: rivers and lakes into the island's area, coast lines
        into area 0. Every river gets a ``RenderOrder`` of its own, the longest first: with one order for all, water
        surfaces that overlap at a junction fought over the same pixels (island-rivers "Rivers")."""
        areas = self._areas_data()
        counts: dict[str, int] = {}
        splines = self.splines()
        look = getattr(self, "water_look", None) or {}  # water_look, set before the splines are added
        rivers = sorted(
            (spline for spline in splines if spline.kind == "River"), key=lambda spline: -len(spline.points)
        )
        render_order = {id(spline): k + 1 for k, spline in enumerate(rivers)}  # pattern: ok (``splines`` outlives it)
        for spline in splines:
            area_index = 0 if spline.kind == "CoastLine" else min(1, len(areas) - 1)
            editor_objects = self._objects(areas[area_index][1], "EditorObject")
            if editor_objects is None:
                continue
            water_object = copy.deepcopy(self.templates[spline.kind])
            fill_spline(water_object, spline, serial=next_serial(editor_objects, water_object))
            if spline.kind in look:  # 2070's colour for the island's water, else the template's (vanilla's)
                set_look(water_object, spline.kind, look_fields(spline.kind, look[spline.kind], spline.view_depth))
            if spline.kind == "River":  # 2070's depth scale, and its fade at both ends of every piece
                depth = {"ViewDepth": (spline.view_depth,)} if spline.view_depth else {}
                set_look(water_object, spline.kind, depth, RENDER_BLEND_BOTH)
            order = water_object.path("River/RenderOrder")
            if order is not None and id(spline) in render_order:  # pattern: ok (as above)
                order.data = struct.pack("<I", render_order[id(spline)])  # pattern: ok (as above)
            editor_objects.children.append(water_object)
            counts[spline.kind] = counts.get(spline.kind, 0) + 1
        self.report["splines"] = counts
