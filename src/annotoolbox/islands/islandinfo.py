"""What an island's ``.a7minfo`` holds that the editor changes: where the start coast faces, the rectangle the map
generator takes for the island and the grid of 8-tile cells that are drawn (also in rd3d ``Terrain/ActiveMap``).
``read`` gives plain numbers, ``check`` names what the game would not take, ``apply`` writes the edit file's ``meta``
(``start_coast`` degrees, ``active_rect``) and ``active`` (``"cx,cz"`` -> 1 or 0, only the cells that differ) into an
opened island (``CIslandFile``). Format: island-editor-tools "The edit file".
"""

from __future__ import annotations

import math
import struct

from ..formats import filedb
from ..infra.i18n import tr
from .islandgrid import cell_key, read_bits, write_bits

CELL = 8  # tiles a cell of the active map covers
RECT = "ActiveMapRect"
GRID = "ActiveMapGrid"
COAST = "StartCoastDirection"


def read(info_tag: filedb.STag) -> dict:
    """The island's numbers: ``grid`` (tiles), ``start_coast`` (degrees, None when the island sets none),
    ``active_rect`` (x0, z0, x1, z1 tiles, None when it has none) and ``cells`` (cells a side of the active grid)."""
    size = info_tag.find("MapSize")
    grid = max(struct.unpack_from("<2i", size.data)) if size is not None else 0
    coast = info_tag.path(f"ObjectMetaInfo/{COAST}/value")
    rect = info_tag.find(RECT)
    cells = info_tag.find(GRID)
    return {
        "grid": grid,
        "start_coast": round(math.degrees(struct.unpack_from("<f", coast.data)[0]), 2) if coast is not None else None,
        "active_rect": list(struct.unpack("<4i", rect.data)) if rect is not None else None,
        "cells": cells.find("x").u32() if cells is not None else 0,
    }


def check(meta: dict, grid: int) -> list[str]:
    """What is wrong with ``meta`` (the edit file's ``meta``) on an island of ``grid`` tiles."""
    out = []
    angle = meta.get("start_coast")
    if angle is not None and not 0 <= float(angle) < 360:
        out.append(tr("islands.start_coast_direction_degrees_runs", angle=angle))
    rect = meta.get("active_rect")
    if rect is not None:
        x0, z0, x1, z1 = rect
        if not (0 <= x0 < x1 <= grid and 0 <= z0 < z1 <= grid):
            out.append(
                tr(
                    "islands.active_rectangle_must_lie_inside",
                    rect=tuple(rect),
                    grid=grid,
                )
            )
        elif any(value % CELL for value in rect):
            out.append(
                tr(
                    "islands.active_rectangle_cut_along_tile",
                    rect=tuple(rect),
                    cell=CELL,
                )
            )
    return out


def apply_meta(info_tag: filedb.STag, meta: dict) -> int:
    """Write ``meta`` into the island's ``.a7minfo``; returns how many values were set."""
    done = 0
    if meta.get("start_coast") is not None:
        holder = info_tag.path(f"ObjectMetaInfo/{COAST}")
        if holder is not None:
            value = struct.pack("<f", math.radians(float(meta["start_coast"])))
            value_tag = holder.find("value")
            if value_tag is None:
                holder.children.append(filedb.SAttrib("value", value))
            else:
                value_tag.data = value
            done += 1
    if meta.get("active_rect") is not None and info_tag.find(RECT) is not None:
        info_tag.find(RECT).data = struct.pack("<4i", *map(int, meta["active_rect"]))
        done += 1
    return done


def apply_active(island, changes: dict) -> int:
    """Set the cells ``changes`` (``"cx,cz"`` -> 1 or 0) in both active maps of the opened island ``island`` (rd3d's, and
    the ``.a7minfo``'s when it has the same grid), and cut the ``.a7minfo`` rectangle round the drawn cells. Returns
    the cells set; a cell off the grid is left out."""
    active_map = island.rd3d.path("Terrain/ActiveMap")
    if active_map is None or not changes:
        return 0
    cells = read_bits(active_map)
    cell_count, done = len(cells), 0
    for key, value in changes.items():
        cell = cell_key(key, cell_count)
        if cell is not None:  # a malformed key or a cell off the grid is left out
            cells[cell[1]][cell[0]] = 1 if value else 0
            done += 1
    write_bits(active_map, cells)
    grid = island.a7minfo.find(GRID)
    if grid is not None and grid.find("x").u32() == cell_count:
        write_bits(grid, cells)
    drawn = [(x, z) for z in range(cell_count) for x in range(cell_count) if cells[z][x]]
    if drawn and island.a7minfo.find(RECT) is not None:
        step = island.size // cell_count
        rect = (
            min(x for x, _ in drawn) * step,
            min(z for _, z in drawn) * step,
            (max(x for x, _ in drawn) + 1) * step,
            (max(z for _, z in drawn) + 1) * step,
        )
        island.a7minfo.find(RECT).data = struct.pack("<4i", *rect)
    return done


def apply(island, edits: dict) -> dict:
    """The edit file's ``active`` cells, then its ``meta`` (so a rectangle set by hand wins over the one cut round
    the cells), into the opened island; what was set, for the build's report."""
    report = {}
    if edits.get("active"):
        report["active cells"] = apply_active(island, edits["active"])
    if edits.get("meta"):
        report["meta"] = apply_meta(island.a7minfo, edits["meta"])
    return {k: value for k, value in report.items() if value}
