"""Anno 1701's info files (``.info``, one beside each ``.mcf``): the bounding box, the grid cells an object blocks
(``NoBuildings``: the tiles nothing else is built on, its footprint; ``Blocking``: half tiles units do not walk;
``Entrances``, ``LandPositions``, ``WaterPositions``) and the named dummies (hit boxes, fire, airport).

A cell counts from the grid point at the object's tile corner and skips 0 (cell 1 is the first past it, -1 the
first before it), at ``GridResolution`` cells a tile; the object's own origin is the ``.ocf``'s ``XOffset`` /
``ZOffset`` world units past that point (32 a tile). ``read_info`` is the entry, ``footprint`` the polygons the
island editor takes (``editor.footprints``). docs/formats/granny.md "1701's info files".
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .cfg2070 import child_float
from .configtree import BARE_AMP

UNITS = 32.0  # world units to a tile (the bounding box, the .ocf offsets)
DUMMY_UNITS = 4096.0  # a dummy's centre and extents: 128ths of a world unit
FOOTPRINT = "NoBuildings"
BLOCKS = ("NoBuildings", "Blocking", "Entrances", "LandPositions", "WaterPositions")
Polygons = list[list[tuple[float, float]]]


@dataclass(frozen=True)
class Cells:
    """A block's cells (x, z as written: no 0) at ``resolution`` cells a tile."""

    resolution: int
    cells: tuple[tuple[int, int], ...]

    def tiles(self) -> list[tuple[float, float, float, float]]:
        """Each cell as (x0, z0, x1, z1) in tiles from the grid point."""
        r = float(self.resolution)
        return [(_low(x) / r, _low(z) / r, (_low(x) + 1) / r, (_low(z) + 1) / r) for x, z in self.cells]


@dataclass
class Info1701:
    """An ``.info``: the box (min, max; tiles), the blocks by name, the dummies (name -> centre, extents; tiles)."""

    box: tuple[tuple[float, ...], tuple[float, ...]] = ((0.0,) * 3, (0.0,) * 3)
    blocks: dict[str, Cells] = field(default_factory=dict)
    dummies: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = field(default_factory=dict)


def _low(k: int) -> int:
    """The cell's first step from the grid point: cell 1 starts at 0, cell -1 at -1."""
    return k - 1 if k > 0 else k


def read_info(text: str) -> Info1701:
    """An ``.info``'s text (tagged elements without a root). A text that does not parse gives an empty one."""
    try:
        root = ET.fromstring(f"<info>{BARE_AMP.sub('&amp;', text)}</info>")
    except ET.ParseError:
        return Info1701()
    out = Info1701()
    box = root.find("BoundingBox")
    if box is not None:
        out.box = tuple(tuple(child_float(box, f"{e}/{a}") / UNITS for a in "xyz") for e in ("Min", "Max"))
    for name in BLOCKS:
        node = root.find(name)
        if node is None:
            continue
        # NoBuildings counts whole tiles: the one file that gives it 32 a tile matches the islands only at 1
        resolution = 1 if name == FOOTPRINT else int(child_float(node, "GridResolution", 1.0)) or 1
        cells = tuple((int(child_float(p, "x")), int(child_float(p, "z"))) for p in node.findall("GridPos"))
        out.blocks[name] = Cells(resolution, cells)
    for dummy in root.findall("Dummies/*"):
        centre = tuple(child_float(dummy, f"Center.{a}") / DUMMY_UNITS for a in "xyz")
        extents = tuple(child_float(dummy, f"Extents.{a}") / DUMMY_UNITS for a in "xyz")
        out.dummies[dummy.tag] = (centre, extents)
    return out


def rectangles(cells: Cells, offset: tuple[float, float] = (0.0, 0.0)) -> Polygons:
    """A block's cells merged into rectangles (runs along x, then equal runs along z), in tiles from the object's
    origin (``offset``: the origin's tiles past the grid point)."""
    r = cells.resolution
    taken = {(_low(x), _low(z)) for x, z in cells.cells}
    runs: dict[tuple[int, int], list[int]] = {}  # (x0, x1) -> the rows that have that run
    for z in sorted({z for _x, z in taken}):
        xs = sorted(x for x, zz in taken if zz == z)
        start = prev = xs[0]
        for x in [*xs[1:], None]:
            if x is not None and x == prev + 1:
                prev = x
                continue
            runs.setdefault((start, prev + 1), []).append(z)
            if x is not None:
                start = prev = x
    out = []
    for (x0, x1), rows in sorted(runs.items()):
        first = last = rows[0]
        for z in [*rows[1:], None]:
            if z is not None and z == last + 1:
                last = z
                continue
            a, b = x0 / r - offset[0], first / r - offset[1]
            c, d = x1 / r - offset[0], (last + 1) / r - offset[1]
            out.append([(a, b), (c, b), (c, d), (a, d)])
            if z is not None:
                first = last = z
    return out


def ocf_offset(ocf_text: str) -> tuple[float, float]:
    """An ``.ocf``'s ``XOffset`` / ``ZOffset`` in tiles."""
    found = [re.search(rf"<{tag}>(-?\d+)</{tag}>", ocf_text) for tag in ("XOffset", "ZOffset")]
    return tuple(int(m.group(1)) / UNITS if m else 0.0 for m in found)


def footprint(info_text: str, ocf_text: str = "") -> Polygons:
    """The footprint of an object: its ``.info``'s ``NoBuildings`` tiles as rectangles round its origin (the
    ``.ocf``'s offsets, when given). The tiles match the footprints the shipped islands' grids hold
    (granny.md "1701's info files")."""
    cells = read_info(info_text).blocks.get(FOOTPRINT)
    if cells is None or not cells.cells:
        return []
    return rectangles(cells, ocf_offset(ocf_text) if ocf_text else (0.0, 0.0))


def is_info1701(text: str) -> bool:
    """Whether an info text is 1701's (grid cells) rather than the later games' ``.ifo`` (polygons)."""
    return "<GridPos>" in text or ("<BoundingBox>" in text and "<Position>" not in text and "<Dummies" in text)
