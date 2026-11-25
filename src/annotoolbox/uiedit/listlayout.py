"""Lists and grids in the preview: a widget file whose widget type is one of the dynamic widgets places its one item
child once per element of its data source, along an axis (a list) or in rows (a grid), each copy bound to its
element.

``list_kind(files, root)`` tells a list file; ``element_count(samples, owner)`` how many elements the sample values
hold for one; ``offsets(kind, inputs, n, item)`` where each copy goes. docs/ui/117/lists-grids-tabs-pages.md.
"""

from __future__ import annotations

import math

from . import fields
from .arrange import components
from .viewmodels import CSamples

# the widget types a list file names (or extends), by their GUID's first part
LIST, GRID, DYNAMIC = "dbcf9d78", "af18fdbb", "10997baf"
KINDS = {LIST: "list", GRID: "grid", DYNAMIC: "list", "6ca90344": "list", "07dcf4d8": "list",
         "f67963fe": "radial-game", "277a7512": "radial-list", "9eb9b942": "radial-wheel"}  # fmt: skip
# the radial widgets' StartAngle, TotalAngle and Radius (the Widget Library's UniqueIds) and their defaults
RADIAL = {
    "radial-game": ((0x38677509, 0x3EB7FC2F, 0x3D5E2825), (0.0, 180.0, 50.0)),
    "radial-list": ((0x3C1898AF, 0x3A606D94, 0x37B56040), (0.0, 180.0, 50.0)),
    "radial-wheel": ((0x3D6EA808, 0x3AFA1D66, 0x3D4CBCB7), (0.0, 360.0, 100.0)),
}
PLACEHOLDER_ITEMS = 3  # elements shown for a list whose sample values hold none
# the list and grid properties (the Widget Library's UniqueIds)
LIST_HORIZONTAL, LIST_SPACE, LIST_VISIBLE = 0x38700F5E, 0x3FFDE5EF, 0x374A4E11
GRID_HORIZONTAL, GRID_SPACE_X, GRID_SPACE_Y = 0x3641914A, 0x31757E14, 0x33C30462
GRID_COLUMNS, GRID_ROWS, CELL = 0x331E7ED6, 0x35E3D8E7, 0x39BFC470  # CELL: the game grid's StaticElementSize
TOTAL_ITEMS = 0x3E50D33B  # TotalItemCount: the game fills it with the data source's count
# SectorInfo, the component the game fills on a radial list's item: Position, Rotation, AngleSize
SECTOR, SECTOR_POSITION, SECTOR_ROTATION, SECTOR_SIZE = "5065a655", 0x77B097F0, 0x70937B58, 0x795500AB
ELEMENT = 0x90000000  # the data source's element i is 0x90000000 + i in the files' preview values
# ValueCompare on a list's item: each item reports MyValue, the game gives every item the Min and Max of all of them
COMPARE, COMPARE_MINE, COMPARE_MIN, COMPARE_MAX = "cf1d5ae2", 0x7F71FFA6, 0x77A51626, 0x72E77949
TAB_ELEMENT, TAB_SELECTED = "f36d9167", 0x7F7463C9  # the TabElement component on an item's file, its IsSelected


def selected(placed_root: dict | None, index: int) -> dict:
    """What the game gives the ``index``-th item of a list whose item file is a tab (a ``TabElement`` on its root):
    ``IsSelected`` on the first only, as a list opens on its first entry (layout.md "A list opens on its first
    entry"). Empty for any other item."""
    comp = components(placed_root).get(TAB_ELEMENT) if placed_root is not None else None
    return {} if comp is None else {(comp, TAB_SELECTED): index == 0}


def compared(values: list) -> tuple[tuple, tuple] | None:
    """The smallest and the largest of the items' ``MyValue`` vectors, per axis (None without any): what the game
    gives each item's ``ValueCompare`` as ``MinValue`` and ``MaxValue`` (layout.md "Lists, grids and loaders")."""
    vectors = [tuple(float(x) for x in vector) for vector in values if isinstance(vector, (tuple, list)) and vector]
    if not vectors:
        return None
    length = min(len(vector) for vector in vectors)
    return tuple(min(vector[i] for vector in vectors) for i in range(length)), tuple(
        max(vector[i] for vector in vectors) for i in range(length)
    )


def type_chain(files, root: dict) -> list[str]:
    """The first parts of the GUIDs of a widget file's widget type and the types it extends, its own first."""
    wtype, seen, out = fields.get(root, "WidgetType"), set(), []
    while wtype and wtype not in seen:
        seen.add(wtype)
        out.append(wtype[:8])
        type_root = files.root(wtype)
        wtype = fields.get(type_root, "ParentWidgetType") if type_root is not None else None
    return out


def list_kind(files, root: dict) -> str | None:
    """ "list", "grid" or a "radial-..." kind for a widget file of a dynamic widget type (through the types it
    extends), else None."""
    return next((KINDS[type_name] for type_name in type_chain(files, root) if type_name in KINDS), None)


def element_count(samples: CSamples, owner: str) -> int:
    """The elements the sample values hold for the list instance ``owner`` (its data source's preview values, or
    values set by hand)."""
    prefix = CSamples.key(owner, [0x30000001]) + "/"
    found = set()
    for key in samples.given_under(prefix):  # made-up values fill elements, they do not add any
        part = key[len(prefix) :].split("/", 1)[0]
        if len(part) == 8 and int(part, 16) >= ELEMENT:
            found.add(part)
    if found:
        return max(int(hex_digits, 16) - ELEMENT for hex_digits in found) + 1
    return PLACEHOLDER_ITEMS if samples.m_fill else 1


def element_key(owner: str, index: int) -> str:
    return CSamples.key(owner, [0x30000001, ELEMENT + index])


def _number(inputs: dict, uid: int, default: float) -> float:
    value = inputs.get(uid)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def offsets(kind: str, inputs: dict, item_count: int, item: tuple[float, float]) -> list[tuple[float, float]]:
    """The place of each of ``item_count`` copies of an item of size ``item``: a list along its axis, a grid in rows of its
    visible column count (columns of its row count when horizontal); the visible counts cap how many are made
    (the editor's word: the most a list instantiates)."""
    if kind in RADIAL:
        return _arc(kind, inputs, item_count)
    if kind == "grid":
        cell = inputs.get(CELL)
        sized = isinstance(cell, (tuple, list)) and len(cell) >= 2 and cell[0] and cell[1]  # a bound value may be short
        width, height = (float(cell[0]), float(cell[1])) if sized else item
        space_x, space_y = _number(inputs, GRID_SPACE_X, 0.0), _number(inputs, GRID_SPACE_Y, 0.0)
        horizontal = bool(inputs.get(GRID_HORIZONTAL))
        across = max(1, int(_number(inputs, GRID_ROWS if horizontal else GRID_COLUMNS, 2)))
        down = int(_number(inputs, GRID_COLUMNS if horizontal else GRID_ROWS, 2))
        out = []
        for i in range(min(item_count, across * down) if down > 0 else item_count):
            across_index, along_index = i % across, i // across
            column, row = (along_index, across_index) if horizontal else (across_index, along_index)
            out.append((column * (width + space_x), row * (height + space_y)))
        return out
    space = _number(inputs, LIST_SPACE, 0.0)
    visible = int(_number(inputs, LIST_VISIBLE, 0))
    count = min(item_count, visible) if visible > 0 else item_count
    horizontal = bool(inputs.get(LIST_HORIZONTAL))
    step = (item[0] + space, 0.0) if horizontal else (0.0, item[1] + space)
    return [(step[0] * i, step[1] * i) for i in range(count)]


def _arc(kind: str, inputs: dict, count: int) -> list[tuple[float, float]]:
    """The copies of a radial list on its arc: the first at ``StartAngle``, the rest spread over ``TotalAngle`` (a
    full circle leaves its end free), the angle from straight up, clockwise: the game's selection wheels (layout.md
    "Lists, grids and loaders in the game"; a radial list's step is open there)."""
    return [(x, y) for x, y, _, _ in sectors(kind, inputs, count)]


def sectors(kind: str, inputs: dict, count: int) -> list[tuple[float, float, float, float]]:
    """Each copy's segment of a radial list: its point on the arc (x, y), its angle and the angle each item spans, as
    the game fills the item's ``SectorInfo`` (``SECTOR``: a file that binds it to its own place and rotation is
    turned by it; layout.md "Lists, grids and loaders in the game")."""
    ids, defaults = RADIAL[kind]
    start, total, radius = (_number(inputs, uid, default) for uid, default in zip(ids, defaults, strict=True))
    step = (
        total / count if total >= 360.0 and count else (total / (count - 1) if count > 1 else 0.0)
    )  # no copies: no step to take
    out = []
    for i in range(count):
        angle = start + step * i
        angle_rad = math.radians(angle)
        out.append((radius * math.sin(angle_rad), -radius * math.cos(angle_rad), angle, step))
    return out


def preview_values(top, key: str) -> dict[str, object]:
    """The preview values a file carries for a list's elements (``DataSource`` paths ``30000001 9000000i ...``),
    as sample keys under the instance that holds them; ``top`` is its element tree."""
    out = {}
    for element in top.walk():
        if not element.guid:
            continue
        for path, value in fields.property_values(element.node).items():
            if len(path) > 2 and path[0] == 0x30000001 and value is not None:
                out[CSamples.key(f"{key}:{element.guid}", path)] = value
    return out
