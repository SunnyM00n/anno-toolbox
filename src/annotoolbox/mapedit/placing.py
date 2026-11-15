"""Where elements lie and how they turn: an island's area after a quarter turn (the game's sense), the complexes of
islands whose grids overlap, group turns and mirrored copies. Pure arithmetic on ``template.Element``; nothing here
reads a file.

docs/map-editor.md "What the vanilla data settles", "Mirrored copies" and "Island complexes".
"""

from __future__ import annotations

from collections.abc import Callable

from ..infra.i18n import N_
from .template import KIND_FIXED, KIND_START, Element, snap

Box = tuple[int, int, int, int]
MIRRORS = {
    "x": N_("across the north-south line"),
    "z": N_("across the east-west line"),
    "xz": N_("across both lines (four quarters)"),
    "point": N_("through the centre (a half turn)"),
}


def turned(rect: Box, edge: int, quarter: int) -> Box:
    """A rectangle inside a grid of ``edge`` after ``quarter`` quarter turns of the grid about its centre, as the game
    turns ``Rotation90``: each turn takes (u, v) to (edge - v, u), counter-clockwise seen from above with x east and z
    north (read from the vanilla sessions' heights, docs/map-editor.md "What the vanilla data settles")."""
    x0, z0, x1, z1 = rect
    for _ in range(quarter % 4):
        x0, z0, x1, z1 = edge - z1, x0, edge - z0, x1
    return (x0, z0, x1, z1)


def grid(e: Element, edge: int) -> Box:
    return (e.x, e.z, e.x + edge, e.z + edge)


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def complexes(elements: list[Element], edge_of: Callable[[Element], int]) -> list[list[int]]:
    """The groups of two or more fixed islands whose grids overlap, joined transitively (the Aegyptus map's four),
    largest first; indexes into ``elements``."""
    fixed = [i for i, e in enumerate(elements) if e.kind == KIND_FIXED]
    boxes = {i: grid(elements[i], edge_of(elements[i])) for i in fixed}
    parent = {i: i for i in fixed}

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for n, i in enumerate(fixed):
        for j in fixed[n + 1 :]:
            if _overlap(boxes[i], boxes[j]):
                parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for i in fixed:
        groups.setdefault(root(i), []).append(i)
    return sorted((g for g in groups.values() if len(g) > 1), key=lambda g: (-len(g), g[0]))


def complex_of(index: int, groups: list[list[int]]) -> list[int]:
    """The complex an element belongs to, or the element alone."""
    return next((g for g in groups if index in g), [index])


def seam(a: Element, b: Element, edge_a: int, edge_b: int) -> tuple:
    """Where island ``b`` lies to ``a``, independent of where the pair stands and how it is turned as one: the files,
    ``b``'s turns relative to ``a``'s, and the offset of the centres turned back by ``a``'s turns."""
    dx = (b.x + edge_b / 2) - (a.x + edge_a / 2)
    dz = (b.z + edge_b / 2) - (a.z + edge_a / 2)
    for _ in range(a.rotation90 % 4):  # undo a's turns: each took (dx, dz) to (-dz, dx)
        dx, dz = dz, -dx
    return (a.map_file_path, b.map_file_path, (b.rotation90 - a.rotation90) % 4, dx, dz)


def seams(elements: list[Element], edge_of: Callable[[Element], int]) -> set[tuple]:
    """The seams of every complex: a pair of islands found in one keeps its place to the other when the complex is
    moved, turned or copied as one, and the checks let such a pair overlap (docs/map-editor.md "Island complexes")."""
    out = set()
    for group in complexes(elements, edge_of):
        for i in group:
            for j in group:
                if i != j:
                    out.add(seam(elements[i], elements[j], edge_of(elements[i]), edge_of(elements[j])))
    return out


def _centre(e: Element, edge: int) -> tuple[float, float]:
    return (e.x + edge / 2, e.z + edge / 2)


def _place(e: Element, cx: float, cz: float, edge: int) -> None:
    """The element's corner put so that its square is centred on (cx, cz), on the 8-tile grid."""
    e.x, e.z = snap(cx - edge / 2), snap(cz - edge / 2)


def turn_group(elements: list[Element], edge_of: Callable[[Element], int]) -> None:
    """Turn elements a quarter turn as one, about the middle of their squares, in the game's sense: each square's
    centre goes round, each fixed island turns once more."""
    boxes = [grid(e, edge_of(e)) for e in elements]
    # a pivot on the 8-tile grid turns every centre onto it exactly, so the islands keep their places to each other
    mx = snap((min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2)
    mz = snap((min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2)
    for e in elements:
        edge = edge_of(e)
        cx, cz = _centre(e, edge)
        _place(e, mx - (cz - mz), mz + (cx - mx), edge)
        if e.kind == KIND_FIXED:
            e.rotation90 = (e.rotation90 + 1) % 4


def mirrored(e: Element, mode: str, centre: tuple[float, float], edge: int) -> Element:
    """A copy of ``e`` mirrored by ``mode`` ("x", "z", "point") about ``centre``; a fixed island cannot be mirrored
    by the game, so its copy turns two quarter turns (the side that faced the line still faces it)."""
    out = e.copy()
    cx, cz = _centre(e, edge)
    mx, mz = centre
    if mode in ("x", "point"):
        cx = 2 * mx - cx
    if mode in ("z", "point"):
        cz = 2 * mz - cz
    if e.kind == KIND_START:
        out.x, out.z = snap(cx), snap(cz)
    else:
        _place(out, cx, cz, edge)
    if e.kind == KIND_FIXED:
        out.rotation90 = (e.rotation90 + 2) % 4
    return out


def mirror_copies(elements: list[Element], mode: str, centre: tuple[float, float], edge_of) -> list[Element]:
    """The mirrored copies of ``elements`` for a mode of ``MIRRORS`` ("xz": three copies, the other quarters)."""
    modes = ("x", "z", "point") if mode == "xz" else (mode,)
    return [mirrored(e, m, centre, edge_of(e)) for m in modes for e in elements]
