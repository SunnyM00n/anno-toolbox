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
from .riverlook import RENDER_BLEND_BOTH, RiverFields, flow_speeds, look_fields, river_view_depth, set_look

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
class Spline:
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
        values: dict = {"Color": tuple(float(re.search(rf"<{c}>([^<]*)</", colour.group(1))[1]) for c in "rgba")}
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
def last_serial(objects: filedb.Tag, high: bytes) -> int:
    """The highest low word among the object IDs whose high word (the type) is ``high``; 0 with none."""
    return max(
        (
            struct.unpack_from("<I", o.find("ID").data)[0]
            for o in objects.children
            if isinstance(o, filedb.Tag) and o.find("ID") is not None and o.find("ID").data[4:] == high
        ),
        default=0,
    )


def next_serial(objects: filedb.Tag, template: filedb.Tag) -> int:
    """A free low word for the object ID (the high word, kept from the template, holds the type)."""
    return last_serial(objects, template.find("ID").data[4:]) + 1


def clone_object(template: filedb.Tag, serial: int, guid: int | None = None) -> filedb.Tag:
    """A copy of an object template numbered ``serial`` (its type word kept) and, with ``guid``, standing for that
    asset: the start of every object the builders add."""
    obj = copy.deepcopy(template)
    if guid is not None:
        obj.find("guid").data = struct.pack("<I", guid) + obj.find("guid").data[4:]
    obj.find("ID").data = struct.pack("<I", serial) + obj.find("ID").data[4:]
    return obj


def _handle(prev, p, nxt) -> tuple[float, float, float]:
    """A spline point's out handle: Catmull-Rom across (x, z); the height's only where the water keeps falling on both
    sides, and no longer than a third of the smaller fall, so the curve never rises over a pool before a step or dips
    under the pool after it (a hump at every step, in game 2026-10-23)."""
    up, down = p[1] - prev[1], nxt[1] - p[1]
    y = 0.0
    if up * down > 0:
        y = math.copysign(min(abs(nxt[1] - prev[1]) / 6, abs(up) / 3, abs(down) / 3), down)
    return ((nxt[0] - prev[0]) / 6, y, (nxt[2] - prev[2]) / 6)


def fill_spline(obj: filedb.Tag, sp: Spline, serial: int, place=None) -> None:
    """Replace a cloned editor object's position, ID and Bezier curve with the spline's points. ``place(obj, point)``
    sets the position where the object stores it other than as three floats (2205: fixed point)."""
    obj.find("ID").data = struct.pack("<I", serial) + obj.find("ID").data[4:]
    if place is None:
        obj.find("Position").data = struct.pack("<3f", *sp.points[0])
    else:
        place(obj, sp.points[0])
    path = obj.path("BezierPath/Path")
    xs, ys, zs = zip(*sp.points, strict=False)
    path.find("Minimum").data = struct.pack("<3f", min(xs), min(ys), min(zs))
    path.find("Maximum").data = struct.pack("<3f", max(xs), max(ys), max(zs))
    curve = path.find("BezierCurve")
    protos = [c for c in curve.children if isinstance(c, filedb.Tag)]
    n = len(sp.points)
    closed = sp.kind == "Lake"
    items = []
    for k, p in enumerate(sp.points):
        prev = sp.points[(k - 1) % n] if (closed or k > 0) else p
        nxt = sp.points[(k + 1) % n] if (closed or k < n - 1) else p
        # a lake is 2070's polygon, which its basin is carved to: straight sides (smooth handles bulged the water up
        # to 2.5 tiles past the basin's rim)
        t = (0.0, 0.0, 0.0) if closed else _handle(prev, p, nxt)
        # vanilla rivers carry different per-point values (u0) on the first point than on the rest
        pt = copy.deepcopy(protos[0] if k == 0 or len(protos) == 1 else protos[1])
        pt.find("p").data = struct.pack("<3f", *p)
        if pt.find("i") is not None:
            pt.find("i").data = struct.pack("<3f", *(-v for v in t))
        if pt.find("o") is not None:
            pt.find("o").data = struct.pack("<3f", *t)
        if pt.find("w") is not None and sp.widths:
            pt.find("w").data = struct.pack("<f", sp.widths[k])
        if pt.find("u0") is not None and sp.speeds:  # the template's first point flows at full speed
            pt.find("u0").data = struct.pack("<3f", *sp.speeds[k])
        if pt.find("d") is not None and sp.normals:
            d = sp.normals[k] if k < n - 1 else (1.0, 0.0)  # vanilla: the last point carries (1, 0)
            pt.find("d").data = struct.pack("<2f", *d)
        items.append(pt)
    curve.children = [c for c in curve.children if not isinstance(c, filedb.Tag)] + items


class WaterMixin:
    """The methods of ``IslandBuilder`` that build the lakes, the water objects and the water maps."""

    def river_mask(self) -> list[list[int]]:
        """2070's ``RiverGrid`` (the tiles 2070 counts as river) in the builder's tiles."""
        s = self.size
        grid = [[0] * s for _ in range(s)]
        rows = read_river_grid(self.isl.root.path("RiverGrid")) or []
        for z, row in enumerate(rows[: self.isl.height]):
            for x, v in enumerate(row[: self.isl.width]):
                if v:
                    grid[z + self.pad_z][x + self.pad_x] = 1
        return grid

    # Lakes
    def _lake_polygons(self):
        """(2070 lake item, its outline in the builder's tiles) for every lake with an outline; the editor's outline
        (``water_edits["lake_outlines"]``, the lake's number as ``lake_levels`` counts) replaces 2070's."""
        px, pz = self.pad_x, self.pad_z
        lakes = self.isl.root.path("Lakes")
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

    def _carve_lakes(self, heights, carve: bool = True, whole: bool = True) -> list[float]:
        """Lake levels (world units): the 2070 level, or the editor's. With ``carve`` the ground inside the outline
        that lies below the level is deepened to ``LAKE_DEPTH`` under it, so the water is 2070's where 2070 has it
        (a dam lake's outline is its full basin: carved whole, the valley flooded at the unbuilt level, n_l14 in game
        2026-11-02). ``whole`` (an earlier patch): the whole outline carved and the terrain within a tile outside it
        raised ``LAKE_RIM`` over the level. ``heights`` (heightmap units per corner, numpy) is changed in place."""
        levels = []
        n = heights.shape[0]
        for item, poly in self._lake_polygons():
            level = item.int("m_WaterHeight") / FIXED
            level = float(self.water_edits.get("lakes", {}).get(str(len(levels)), level))
            if not carve:
                levels.append(level)
                continue
            x0, x1 = int(min(x for x, _ in poly)) - 2, int(max(x for x, _ in poly)) + 3
            z0, z1 = int(min(z for _, z in poly)) - 2, int(max(z for _, z in poly)) + 3
            for z in range(max(0, z0), min(n, z1)):
                for x in range(max(0, x0), min(n, x1)):
                    if point_in_polygon(x, z, poly):
                        if whole or heights[z, x] < level * HEIGHT_UNITS:
                            heights[z, x] = min(heights[z, x], (level - LAKE_DEPTH) * HEIGHT_UNITS)
                    elif whole and dist_to_polygon(x, z, poly) <= 1.5:
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

        wi = self.rd.path("WaterInfos")
        if wi is None:
            return
        w = wi.find("width").u32()
        cell = self.size / w
        hm = np.asarray(self.heights, np.float32)
        c = ((np.arange(w) + 0.5) * cell).astype(int)  # cell centres on the corner grid
        depth = -hm[np.ix_(c, c)] / HEIGHT_UNITS
        land = depth <= 0
        info = np.zeros((w, w, 4), np.uint8)
        info[..., 0] = depth_code(depth)
        info[..., 1] = 255
        info[..., 2] = beach_map(-depth, cell)
        # a lake near the sea's level (n_m05's reservoir) got the beach's band as a brown carpet where its river
        # comes in (in game, 2026-10-23); vanilla paints duckweed on few cells
        centres = (np.arange(w) + 0.5) * cell
        for _, poly in self._lake_polygons():
            xs, zs = [x for x, _ in poly], [z for _, z in poly]
            for iz in np.flatnonzero((centres >= min(zs) - 1) & (centres <= max(zs) + 1)):
                for ix in np.flatnonzero((centres >= min(xs) - 1) & (centres <= max(xs) + 1)):
                    cx, cz = centres[ix], centres[iz]
                    if point_in_polygon(cx, cz, poly) or dist_to_polygon(cx, cz, poly) <= 1.0:
                        info[iz, ix, 2] = 0
        wi.find("map").data = info.tobytes()
        wf = self.rd.path("WaterFlow")
        if wf is not None and wf.find("width").u32() == w:
            dist = ndimage.distance_transform_edt(~land) * cell  # tiles to land
            flow = np.zeros((w, w, 4), np.uint8)
            flow[..., 0] = flow[..., 1] = 127
            flow[..., 2] = np.rint(255 * np.clip(dist / SHORE_MASK_FULL, 0, 1) ** 2).astype(np.uint8)
            wf.find("map").data = flow.tobytes()

    # Editor objects
    def splines(self) -> list[Spline]:
        """The rivers (with their flow and view depth from 2070's point fields, ``riverlook``), the lakes at their
        level and the coast lines."""
        found = RiverFields(list(self._river_piece_fields()) if hasattr(self, "_river_piece_fields") else [])
        out = []
        for pts, widths in self.rivers:
            fields = [found.at(x, z) for x, _, z in pts]
            speeds, depth = flow_speeds(fields), river_view_depth(pts, fields)
            out.append(Spline("River", pts, widths=widths, speeds=speeds, view_depth=depth))
        for (_item, poly), level in zip(self._lake_polygons(), self.lake_levels, strict=False):
            out.append(Spline("Lake", [(x, level, z) for x, z in poly]))
        return out + self.coast_splines()

    def add_splines(self) -> None:
        """Clone the vanilla editor objects for every spline: rivers and lakes into the island's area, coast lines
        into area 0. Every river gets a ``RenderOrder`` of its own, the longest first: with one order for all, water
        surfaces that overlap at a junction fought over the same pixels (island-rivers "Rivers")."""
        areas = self._areas_data()
        counts: dict[str, int] = {}
        splines = self.splines()
        look = getattr(self, "water_look", None) or {}  # water_look, set before the splines are added
        rivers = sorted((sp for sp in splines if sp.kind == "River"), key=lambda sp: -len(sp.points))
        render_order = {id(sp): k + 1 for k, sp in enumerate(rivers)}
        for sp in splines:
            area_index = 0 if sp.kind == "CoastLine" else min(1, len(areas) - 1)
            eo = self._objects(areas[area_index][1], "EditorObject")
            if eo is None:
                continue
            obj = copy.deepcopy(self.templates[sp.kind])
            fill_spline(obj, sp, serial=next_serial(eo, obj))
            if sp.kind in look:  # 2070's colour for the island's water, else the template's (vanilla's)
                set_look(obj, sp.kind, look_fields(sp.kind, look[sp.kind], sp.view_depth))
            if sp.kind == "River":  # 2070's depth scale, and its fade at both ends of every piece
                depth = {"ViewDepth": (sp.view_depth,)} if sp.view_depth else {}
                set_look(obj, sp.kind, depth, RENDER_BLEND_BOTH)
            order = obj.path("River/RenderOrder")
            if order is not None and id(sp) in render_order:
                order.data = struct.pack("<I", render_order[id(sp)])
            eo.children.append(obj)
            counts[sp.kind] = counts.get(sp.kind, 0) + 1
        self.report["splines"] = counts
