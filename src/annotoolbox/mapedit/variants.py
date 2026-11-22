"""Easy, medium and hard maps derived from one map, as 117's three templates of a layout differ: Normal slots one size
up (easy) or down (hard), the hard map's XL starters Large; every slot keeps its centre.

``derive(elements, level, classes)`` returns the level's elements (copies; medium unchanged); ``LEVELS`` names the
three with the ``IslandSize`` their ``MapTemplate`` asset carries. docs/map-editor.md "Three difficulties from one map".
"""

from __future__ import annotations

from dataclasses import dataclass

from ..infra.i18n import N_
from .template import KIND_SLOT, SIZE_LARGE, SIZE_MEDIUM, SIZE_SMALL, SIZE_XL, TYPE_STARTER, SElement, snap

RESIZED_TYPES = (0,)  # Normal slots: the Volcanic ones keep their size in every vanilla layout's three levels
STEPS = 2  # slots moved by a size per step: the vanilla layouts' average (docs/map-editor.md)


@dataclass(frozen=True)
class SLevel:
    key: str  # the file name's suffix: easy, medium, hard
    title: str
    island_size: str  # the MapTemplate asset's IslandSize: what the setup's island size picks


LEVELS = (
    SLevel("easy", N_("maps.easy"), "Large"),
    SLevel("medium", N_("maps.medium"), "Medium"),
    SLevel("hard", N_("maps.hard"), "Small"),
)


def _room(i: int, elements: list[SElement], edges: list[int]) -> float:
    """How much sea a slot has: the gap from its square to the nearest other element's (negative: they overlap)."""
    first = elements[i]
    ax0, az0, ax1, az1 = first.x, first.z, first.x + edges[i], first.z + edges[i]
    best = float("inf")
    for j, other in enumerate(elements):
        if j == i:
            continue
        bx0, bz0, bx1, bz1 = other.x, other.z, other.x + edges[j], other.z + edges[j]
        best = min(best, max(bx0 - ax1, ax0 - bx1, bz0 - az1, az0 - bz1))
    return best


def _resize(element: SElement, size: int, classes: dict[int, int]) -> None:
    """A slot to another size class about its centre."""
    old, new = classes.get(element.size, 0), classes.get(size, 0)
    element.x, element.z = snap(element.x + (old - new) / 2), snap(element.z + (old - new) / 2)
    element.size = size


def _step(out: list[SElement], edges: list[int], frm: int, to: int, count: int | None, classes) -> None:
    """``count`` pool slots of size ``frm`` (all with None) to ``to``: growing, the ones with the most sea around them
    first; shrinking, the most crowded first."""
    picked = [
        i
        for i, element in enumerate(out)
        if element.kind == KIND_SLOT and element.type in RESIZED_TYPES and element.size == frm
    ]
    picked.sort(key=lambda i: (_room(i, out, edges), out[i].x, out[i].z), reverse=to > frm)
    for i in picked if count is None else picked[:count]:
        _resize(out[i], to, classes)
        edges[i] = classes.get(to, edges[i])


def derive(elements: list[SElement], level: str, classes: dict[int, int], steps: int = STEPS) -> list[SElement]:
    """The elements of the ``level`` map from the medium map's ``elements`` (copies; their nodes too)."""
    out = [element.copy() for element in elements]
    if level == "medium":
        return out
    edges = [classes.get(element.size, 0) if element.kind == KIND_SLOT else 0 for element in out]
    if level == "easy":
        _step(out, edges, SIZE_MEDIUM, SIZE_LARGE, steps, classes)
        _step(out, edges, SIZE_SMALL, SIZE_MEDIUM, steps, classes)
    elif level == "hard":
        _step(out, edges, SIZE_LARGE, SIZE_MEDIUM, None, classes)
        _step(out, edges, SIZE_MEDIUM, SIZE_SMALL, steps, classes)
        for element in out:
            if element.kind == KIND_SLOT and element.type == TYPE_STARTER and element.size == SIZE_XL:
                _resize(element, SIZE_LARGE, classes)
    else:
        raise ValueError(f"no level {level!r}: easy, medium or hard")
    return out


def sibling(path: str, level: str) -> str:
    """A vanilla ``_medium`` template's path for another level (its folder and file renamed alike), "" for none."""
    return path.replace("_medium", f"_{level}") if "_medium" in path.rsplit("/", 1)[-1] else ""
