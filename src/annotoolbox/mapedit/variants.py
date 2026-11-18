"""Easy, medium and hard maps derived from one map, as 117's three templates of a layout differ: Normal slots one size
up (easy) or down (hard), the hard map's XL starters Large; every slot keeps its centre.

``derive(elements, level, classes)`` returns the level's elements (copies; medium unchanged); ``LEVELS`` names the
three with the ``IslandSize`` their ``MapTemplate`` asset carries. docs/map-editor.md "Three difficulties from one map".
"""

from __future__ import annotations

from dataclasses import dataclass

from ..infra.i18n import N_
from .template import KIND_SLOT, SIZE_LARGE, SIZE_MEDIUM, SIZE_SMALL, SIZE_XL, TYPE_STARTER, Element, snap

RESIZED_TYPES = (0,)  # Normal slots: the Volcanic ones keep their size in every vanilla layout's three levels
STEPS = 2  # slots moved by a size per step: the vanilla layouts' average (docs/map-editor.md)


@dataclass(frozen=True)
class Level:
    key: str  # the file name's suffix: easy, medium, hard
    title: str
    island_size: str  # the MapTemplate asset's IslandSize: what the setup's island size picks


LEVELS = (
    Level("easy", N_("maps.easy"), "Large"),
    Level("medium", N_("maps.medium"), "Medium"),
    Level("hard", N_("maps.hard"), "Small"),
)


def _room(i: int, elements: list[Element], edges: list[int]) -> float:
    """How much sea a slot has: the gap from its square to the nearest other element's (negative: they overlap)."""
    a = elements[i]
    ax0, az0, ax1, az1 = a.x, a.z, a.x + edges[i], a.z + edges[i]
    best = float("inf")
    for j, b in enumerate(elements):
        if j == i:
            continue
        bx0, bz0, bx1, bz1 = b.x, b.z, b.x + edges[j], b.z + edges[j]
        best = min(best, max(bx0 - ax1, ax0 - bx1, bz0 - az1, az0 - bz1))
    return best


def _resize(e: Element, size: int, classes: dict[int, int]) -> None:
    """A slot to another size class about its centre."""
    old, new = classes.get(e.size, 0), classes.get(size, 0)
    e.x, e.z = snap(e.x + (old - new) / 2), snap(e.z + (old - new) / 2)
    e.size = size


def _step(out: list[Element], edges: list[int], frm: int, to: int, count: int | None, classes) -> None:
    """``count`` pool slots of size ``frm`` (all with None) to ``to``: growing, the ones with the most sea around them
    first; shrinking, the most crowded first."""
    picked = [i for i, e in enumerate(out) if e.kind == KIND_SLOT and e.type in RESIZED_TYPES and e.size == frm]
    picked.sort(key=lambda i: (_room(i, out, edges), out[i].x, out[i].z), reverse=to > frm)
    for i in picked if count is None else picked[:count]:
        _resize(out[i], to, classes)
        edges[i] = classes.get(to, edges[i])


def derive(elements: list[Element], level: str, classes: dict[int, int], steps: int = STEPS) -> list[Element]:
    """The elements of the ``level`` map from the medium map's ``elements`` (copies; their nodes too)."""
    out = [e.copy() for e in elements]
    if level == "medium":
        return out
    edges = [classes.get(e.size, 0) if e.kind == KIND_SLOT else 0 for e in out]
    if level == "easy":
        _step(out, edges, SIZE_MEDIUM, SIZE_LARGE, steps, classes)
        _step(out, edges, SIZE_SMALL, SIZE_MEDIUM, steps, classes)
    elif level == "hard":
        _step(out, edges, SIZE_LARGE, SIZE_MEDIUM, None, classes)
        _step(out, edges, SIZE_MEDIUM, SIZE_SMALL, steps, classes)
        for e in out:
            if e.kind == KIND_SLOT and e.type == TYPE_STARTER and e.size == SIZE_XL:
                _resize(e, SIZE_LARGE, classes)
    else:
        raise ValueError(f"no level {level!r}: easy, medium or hard")
    return out


def sibling(path: str, level: str) -> str:
    """A vanilla ``_medium`` template's path for another level (its folder and file renamed alike), "" for none."""
    return path.replace("_medium", f"_{level}") if "_medium" in path.rsplit("/", 1)[-1] else ""
