"""Where elements lie and how they turn: an island's area after a quarter turn (the game's sense), the complexes of
islands whose grids overlap, group turns and mirrored copies. Pure arithmetic on ``template.SElement``; nothing here
reads a file.

docs/map-editor.md "What the vanilla data settles", "Mirrored copies" and "Island complexes".
"""

from __future__ import annotations

from collections.abc import Callable

from ..infra.i18n import N_
from .template import KIND_FIXED, KIND_START, SElement, snap

Box = tuple[int, int, int, int]
MIRRORS = {
    "x": N_("maps.across_north_south_line"),
    "z": N_("maps.across_east_west_line"),
    "xz": N_("maps.across_both_lines_four_quarters"),
    "point": N_("maps.through_centre_half_turn"),
}


def turned(rect: Box, edge: int, quarter: int) -> Box:
    """A rectangle inside a grid of ``edge`` after ``quarter`` quarter turns of the grid about its centre, as the game
    turns ``Rotation90``: each turn takes (u, v) to (edge - v, u), counter-clockwise seen from above with x east and z
    north (read from the vanilla sessions' heights, docs/map-editor.md "What the vanilla data settles")."""
    x0, z0, x1, z1 = rect
    for _ in range(quarter % 4):
        x0, z0, x1, z1 = edge - z1, x0, edge - z0, x1
    return (x0, z0, x1, z1)


def grid(element: SElement, edge: int) -> Box:
    return (element.x, element.z, element.x + edge, element.z + edge)


def overlap(first: Box, second: Box) -> bool:
    """Whether two boxes share ground (touching edges do not)."""
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def complexes(elements: list[SElement], edge_of: Callable[[SElement], int]) -> list[list[int]]:
    """The groups of two or more fixed islands whose grids overlap, joined transitively (the Aegyptus map's four),
    largest first; indexes into ``elements``."""
    fixed = [i for i, element in enumerate(elements) if element.kind == KIND_FIXED]
    boxes = {i: grid(elements[i], edge_of(elements[i])) for i in fixed}
    parent = {i: i for i in fixed}

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for number, i in enumerate(fixed):
        for j in fixed[number + 1 :]:
            if overlap(boxes[i], boxes[j]):
                parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for i in fixed:
        groups.setdefault(root(i), []).append(i)
    return sorted((group for group in groups.values() if len(group) > 1), key=lambda group: (-len(group), group[0]))


def complex_of(index: int, groups: list[list[int]]) -> list[int]:
    """The complex an element belongs to, or the element alone."""
    return next((group for group in groups if index in group), [index])


def seam(first: SElement, second: SElement, edge_a: int, edge_b: int) -> tuple:
    """Where island ``b`` lies to ``a``, independent of where the pair stands and how it is turned as one: the files,
    ``b``'s turns relative to ``a``'s, and the offset of the centres turned back by ``a``'s turns."""
    dx = (second.x + edge_b / 2) - (first.x + edge_a / 2)
    dz = (second.z + edge_b / 2) - (first.z + edge_a / 2)
    for _ in range(first.Rotation90 % 4):  # undo a's turns: each took (dx, dz) to (-dz, dx)
        dx, dz = dz, -dx
    return (first.MapFilePath, second.MapFilePath, (second.Rotation90 - first.Rotation90) % 4, dx, dz)


def seams(elements: list[SElement], edge_of: Callable[[SElement], int]) -> set[tuple]:
    """The seams of every complex: a pair of islands found in one keeps its place to the other when the complex is
    moved, turned or copied as one, and the checks let such a pair overlap (docs/map-editor.md "Island complexes")."""
    out = set()
    for group in complexes(elements, edge_of):
        for i in group:
            for j in group:
                if i != j:
                    out.add(seam(elements[i], elements[j], edge_of(elements[i]), edge_of(elements[j])))
    return out


def _centre(element: SElement, edge: int) -> tuple[float, float]:
    return (element.x + edge / 2, element.z + edge / 2)


def _place(element: SElement, cx: float, cz: float, edge: int) -> None:
    """The element's corner put so that its square is centred on (cx, cz), on the 8-tile grid."""
    element.x, element.z = snap(cx - edge / 2), snap(cz - edge / 2)


def turn_group(elements: list[SElement], edge_of: Callable[[SElement], int]) -> None:
    """Turn elements a quarter turn as one, about the middle of their squares, in the game's sense: each square's
    centre goes round, each fixed island turns once more."""
    boxes = [grid(element, edge_of(element)) for element in elements]
    # a pivot on the 8-tile grid turns every centre onto it exactly, so the islands keep their places to each other
    mx = snap((min(box[0] for box in boxes) + max(box[2] for box in boxes)) / 2)
    mz = snap((min(box[1] for box in boxes) + max(box[3] for box in boxes)) / 2)
    for element in elements:
        edge = edge_of(element)
        cx, cz = _centre(element, edge)
        _place(element, mx - (cz - mz), mz + (cx - mx), edge)
        if element.kind == KIND_FIXED:
            element.Rotation90 = (element.Rotation90 + 1) % 4


def mirrored(element: SElement, mode: str, centre: tuple[float, float], edge: int) -> SElement:
    """A copy of ``e`` mirrored by ``mode`` ("x", "z", "point") about ``centre``; a fixed island cannot be mirrored
    by the game, so its copy turns two quarter turns (the side that faced the line still faces it)."""
    out = element.copy()
    cx, cz = _centre(element, edge)
    mx, mz = centre
    if mode in ("x", "point"):
        cx = 2 * mx - cx
    if mode in ("z", "point"):
        cz = 2 * mz - cz
    if element.kind == KIND_START:
        out.x, out.z = snap(cx), snap(cz)
    else:
        _place(out, cx, cz, edge)
    if element.kind == KIND_FIXED:
        out.Rotation90 = (element.Rotation90 + 2) % 4
    return out


def mirror_copies(elements: list[SElement], mode: str, centre: tuple[float, float], edge_of) -> list[SElement]:
    """The mirrored copies of ``elements`` for a mode of ``MIRRORS`` ("xz": three copies, the other quarters)."""
    modes = ("x", "z", "point") if mode == "xz" else (mode,)
    return [mirrored(element, mirror_mode, centre, edge_of(element)) for mirror_mode in modes for element in elements]
