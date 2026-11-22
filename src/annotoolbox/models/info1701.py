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
class SCells:
    """A block's cells (x, z as written: no 0) at ``resolution`` cells a tile."""

    resolution: int
    cells: tuple[tuple[int, int], ...]

    def tiles(self) -> list[tuple[float, float, float, float]]:
        """Each cell as (x0, z0, x1, z1) in tiles from the grid point."""
        resolution = float(self.resolution)
        return [
            (_low(x) / resolution, _low(z) / resolution, (_low(x) + 1) / resolution, (_low(z) + 1) / resolution)
            for x, z in self.cells
        ]


@dataclass
class SInfo1701:
    """An ``.info``: the box (min, max; tiles), the blocks by name, the dummies (name -> centre, extents; tiles)."""

    box: tuple[tuple[float, ...], tuple[float, ...]] = ((0.0,) * 3, (0.0,) * 3)
    blocks: dict[str, SCells] = field(default_factory=dict)
    dummies: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = field(default_factory=dict)


def _low(k: int) -> int:
    """The cell's first step from the grid point: cell 1 starts at 0, cell -1 at -1."""
    return k - 1 if k > 0 else k


def read_info(text: str) -> SInfo1701:
    """An ``.info``'s text (tagged elements without a root). A text that does not parse gives an empty one."""
    try:
        root = ET.fromstring(f"<info>{BARE_AMP.sub('&amp;', text)}</info>")
    except ET.ParseError:
        return SInfo1701()
    out = SInfo1701()
    box = root.find("BoundingBox")
    if box is not None:
        out.box = tuple(tuple(child_float(box, f"{edge}/{left}") / UNITS for left in "xyz") for edge in ("Min", "Max"))
    for name in BLOCKS:
        node = root.find(name)
        if node is None:
            continue
        # NoBuildings counts whole tiles: the one file that gives it 32 a tile matches the islands only at 1
        resolution = 1 if name == FOOTPRINT else int(child_float(node, "GridResolution", 1.0)) or 1
        cells = tuple((int(child_float(point, "x")), int(child_float(point, "z"))) for point in node.findall("GridPos"))
        out.blocks[name] = SCells(resolution, cells)
    for dummy in root.findall("Dummies/*"):
        centre = tuple(child_float(dummy, f"Center.{left}") / DUMMY_UNITS for left in "xyz")
        extents = tuple(child_float(dummy, f"Extents.{left}") / DUMMY_UNITS for left in "xyz")
        out.dummies[dummy.tag] = (centre, extents)
    return out


def rectangles(cells: SCells, offset: tuple[float, float] = (0.0, 0.0)) -> Polygons:
    """A block's cells merged into rectangles (runs along x, then equal runs along z), in tiles from the object's
    origin (``offset``: the origin's tiles past the grid point)."""
    resolution = cells.resolution
    taken = {(_low(x), _low(z)) for x, z in cells.cells}
    runs: dict[tuple[int, int], list[int]] = {}  # (x0, x1) -> the rows that have that run
    for z in sorted({z for _x, z in taken}):
        xs = sorted(x for x, zz in taken if zz == z)
        start = previous = xs[0]
        for x in [*xs[1:], None]:
            if x is not None and x == previous + 1:
                previous = x
                continue
            runs.setdefault((start, previous + 1), []).append(z)
            if x is not None:
                start = previous = x
    out = []
    for (x0, x1), rows in sorted(runs.items()):
        first = last = rows[0]
        for z in [*rows[1:], None]:
            if z is not None and z == last + 1:
                last = z
                continue
            left, top = x0 / resolution - offset[0], first / resolution - offset[1]
            right, bottom = x1 / resolution - offset[0], (last + 1) / resolution - offset[1]
            out.append([(left, top), (right, top), (right, bottom), (left, bottom)])
            if z is not None:
                first = last = z
    return out


def ocf_offset(ocf_text: str) -> tuple[float, float]:
    """An ``.ocf``'s ``XOffset`` / ``ZOffset`` in tiles."""
    found = [re.search(rf"<{tag}>(-?\d+)</{tag}>", ocf_text) for tag in ("XOffset", "ZOffset")]
    return tuple(int(match.group(1)) / UNITS if match else 0.0 for match in found)


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
