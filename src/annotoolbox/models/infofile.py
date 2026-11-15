"""The points of an info file (``.ifo``): how its numbers are written and read, and the footprint polygons.

The older games (2070, 1404, 2205) write a point's coordinates in fixed point (``x``, ``z``: 4,096 to a tile), the
newer ones in floats (``xf``, ``zf``). Every reader and editor of an ``.ifo`` goes through here: ``coordinate`` finds
a point's leaf, ``value`` and ``text`` read and write one in the file's style (``fixed_style``), ``points`` reads an
area, ``footprint`` reads the ``BuildBlocker`` polygons of a file's text (1701's ``.info`` grid cells through
``info1701``). The tree is ``cfgdoc``'s; no Qt.
"""

from __future__ import annotations

from ..formats import isd2070
from ..formats.isd2070 import Node
from . import cfgdoc, info1701
from .cfgdoc import Doc

FIXED = isd2070.FIXED  # the older games' fixed point: units to a tile
FOOTPRINT = "BuildBlocker"
Polygons = list[list[tuple[float, float]]]


def coordinate(position: Node, axis: str) -> Node | None:
    """The leaf of ``axis`` (``x`` or ``z``) of a ``Position`` node, as ``x`` or ``xf``."""
    return next((c for c in position.children if c.name.rstrip("f") == axis), None)


def value(leaf: Node | None, fixed: bool, default: float = 0.0) -> float:
    """A coordinate leaf in tiles: a float leaf (``xf``) as it is, any other one by the fixed point when ``fixed``;
    ``default`` for a missing or unreadable one."""
    if leaf is None or leaf.text is None:
        return default
    try:
        number = float(leaf.text)
    except ValueError:
        return default
    return number / FIXED if fixed and not leaf.name.endswith("f") else number


def text(number: float, fixed: bool) -> str:
    """A coordinate in tiles as the file writes it: whole fixed-point units, or a float."""
    return str(round(number * FIXED)) if fixed else cfgdoc.number_text(number)


def fixed_style(doc: Doc, older_game: bool) -> bool:
    """Whether the file writes its numbers in fixed point: by the points it has, else by the game (2070, 1404)."""
    for node in doc.root.iter():
        for position in (c for c in node.children if c.name == "Position"):
            names = {c.name for c in position.children}
            if names & {"xf", "zf"}:
                return False
            if names & {"x", "z"}:
                return True
    return older_game


def points(area: Node, fixed: bool) -> list[tuple[float, float]]:
    """The (x, z) of an area's ``Position`` nodes, in tiles and in order."""
    return [
        (value(coordinate(p, "x"), fixed), value(coordinate(p, "z"), fixed))
        for p in area.children
        if p.name == "Position"
    ]


def footprint(ifo_text: str, ocf_text: str = "") -> Polygons:
    """The ``BuildBlocker`` polygons of an info file's text (each with at least three points), in tiles; a text that
    is no tagged file has none. A point may mix styles (``xf`` with ``z``): each leaf by its own name. A 1701
    ``.info`` gives its footprint tiles (``info1701.footprint``; ``ocf_text`` its object config, for the offsets)."""
    if info1701.is_info1701(ifo_text):
        return info1701.footprint(ifo_text, ocf_text)
    try:
        root = isd2070.parse(ifo_text.encode("utf-8"))
    except (UnicodeError, IndexError, ValueError):
        return []
    polygons = (points(node, True) for node in root.iter() if node.name == FOOTPRINT)
    return [poly for poly in polygons if len(poly) >= 3]
