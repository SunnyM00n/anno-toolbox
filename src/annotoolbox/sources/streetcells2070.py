"""How Anno 2070 draws a street tile: the cell of the street type's 4 x 8 sheet its four neighbours pick.

``cells`` gives every tile of a street grid its cell and turn (``pick`` on the neighbour mask), ``sheet_uv`` the place
of a point of the tile on the sheet, ``sheets`` the sheet of each street type. The rule and its data are in
docs/island-editor-view.md, "Streets of 2070 islands".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .streetgrid2070 import STREET_TYPES, SStreetGrid

COLS, ROWS = 4, 8  # the sheet: one cell a tile, `cell = row * COLS + column`
ASSETS = "data/config/game/assets.xml"
ASSETS_ADDON = "addondata/config/balancing/addon_01_assets.xml"  # the add-on's Tech-Highway
# The neighbour mask: 1 = +z, 2 = +x, 4 = -z, 8 = -x (a quarter turn clockwise, seen from above with +x to the
# right, moves a bit to the next: 1 -> 2 -> 4 -> 8 -> 1). The cells' arms at turn 0, measured on the sheets' opaque
# edges: the end opens to +x, the straight runs along x, the corner has +x and -z, the T is closed towards +z.
SINGLE, END, STRAIGHT, CORNER, TEE, CROSS = 0, 2, 4, 8, 12, 14
SHAPES = (  # (first cell, variants, arms at turn 0), by arm count and form
    (SINGLE, 2, 0),
    (END, 2, 2),
    (STRAIGHT, 4, 2 | 8),
    (CORNER, 4, 2 | 4),
    (TEE, 2, 2 | 4 | 8),
    (CROSS, 2, 15),
)
TURNS = 4


@dataclass(frozen=True)
class SStreetCell:
    """A street tile ``tile`` (x, z) of the street type ``guid`` drawn with sheet cell ``cell`` turned ``turn``
    quarter steps clockwise."""

    tile: tuple[int, int]
    guid: int
    cell: int
    turn: int


def _turned(arms: int, turn: int) -> int:
    for _ in range(turn % TURNS):
        arms = ((arms << 1) | (arms >> 3)) & 15
    return arms


def _variant(x: int, z: int, count: int) -> int:
    """A tile's own pick among a shape's variants: the game's seeded ``rand()`` is not known, so a fixed hash of the
    tile (the same island always draws the same)."""
    return (((x * 0x9E3779B1) ^ (z * 0x85EBCA6B)) >> 11) % count


def pick(mask: int, x: int, z: int) -> tuple[int, int]:
    """(cell, turn) of tile (x, z) whose street neighbours are ``mask`` (1 +z, 2 +x, 4 -z, 8 -x): the shape by the
    form, the turn that puts its arms on them, the variant by ``_variant``. A single and a cross
    are turned by the same hash."""
    for first, count, arms in SHAPES:
        for turn in range(TURNS):
            if _turned(arms, turn) == mask:
                return first + _variant(x, z, count), (_variant(z, x, TURNS) if arms in (0, 15) else turn)
    raise ValueError(f"no street shape for the neighbour mask {mask}")


def neighbour_mask(tiles: set[tuple[int, int]], x: int, z: int) -> int:
    """The bits of the neighbours of (x, z) that are in ``tiles``."""
    return sum(
        bit for bit, (dx, dz) in ((1, (0, 1)), (2, (1, 0)), (4, (0, -1)), (8, (-1, 0))) if (x + dx, z + dz) in tiles
    )


def cells(grid: SStreetGrid) -> list[SStreetCell]:
    """Every street tile of ``grid`` with its cell and turn. A tile under a bridge counts as a street tile to its
    neighbours and takes the type of one of them (the first of +z, +x, -z, -x); a bridge cell with no street beside it
    is left out."""
    present = set(grid.tiles) | grid.bridges
    out = []
    for (x, z), guid in sorted(grid.tiles.items()):
        out.append(_cell(present, x, z, guid))
    for x, z in sorted(grid.bridges):
        beside = next(
            (
                grid.tiles[(x + dx, z + dz)]
                for dx, dz in ((0, 1), (1, 0), (0, -1), (-1, 0))
                if (x + dx, z + dz) in grid.tiles
            ),
            None,
        )
        if beside is not None:
            out.append(_cell(present, x, z, beside))
    return out


def _cell(present: set[tuple[int, int]], x: int, z: int, guid: int) -> SStreetCell:
    cell, turn = pick(neighbour_mask(present, x, z), x, z)
    return SStreetCell((x, z), guid, cell, turn)


def tile_uv(turn: int, s: float, t: float) -> tuple[float, float]:
    """The place (u, v) in a cell of the point (s, t) of its tile (0..1; s along +x, t along +z): turn 0 puts the cell's
    top towards +z and its right edge towards +x, each step a quarter clockwise, never mirrored."""
    return ((s, 1 - t), (1 - t, 1 - s), (1 - s, t), (t, s))[turn % TURNS]


def sheet_uv(cell: int, turn: int, s: float, t: float) -> tuple[float, float]:
    """The place on the whole sheet (0..1) of the point (s, t) of a tile drawn with ``cell`` turned ``turn``."""
    u, v = tile_uv(turn, s, t)
    return (cell % COLS + u) / COLS, (cell // COLS + v) / ROWS


def sheets(tree) -> dict[int, str]:
    """2070 street GUID -> the sheet's key in ``tree`` (``Street/Diffuse``, as the archives ship it); a street
    without a sheet or a texture that is not there is left out."""
    text = tree.read_bytes(ASSETS).decode("utf-8", "ignore")
    if tree.exists(ASSETS_ADDON):
        text += tree.read_bytes(ASSETS_ADDON).decode("utf-8", "ignore")
    out: dict[int, str] = {}
    for block in re.findall(r"<Asset>((?:(?!<Asset>).)*?)</Asset>", text, re.S):
        guid = re.search(r"<GUID>(\d+)</GUID>", block)
        diffuse = re.search(r"<Street>.*?<Diffuse>([^<]*)</Diffuse>", block, re.S)
        if guid is None or diffuse is None or int(guid.group(1)) not in STREET_TYPES.values():
            continue
        key = tree.texture_key(diffuse.group(1).strip().replace("\\", "/"))
        if key:
            out[int(guid.group(1))] = key
    return out
