"""Overlays for the cell grids the island carries, through the view's line and point overlays: the outline of the
active cells following the ground with a cross in every cell the edits changed, and the weak coast waves of the
sea map, the ford grid's tiles and the environment zones. Used by the cell tool (active map, ford, environment) and the
surf tool.
"""

from __future__ import annotations

import colorsys

import numpy as np

ACCENT = (1.0, 0.62, 0.11, 1.0)  # the editor's orange: what the edits changed
STEP = 2.0  # tiles between the points of an outline: it follows the ground at this resolution


def _edge(session, a, b, rgba, lift: float) -> list[tuple]:
    """Line segments from (x, z) ``a`` to ``b`` in tiles, each point on the ground, in view space (x mirrored)."""
    n = max(1, round(float(np.hypot(b[0] - a[0], b[1] - a[1])) / STEP))
    pts = [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)]
    rows = [(session.size - x, session.height_at(x, z) + lift, z, *rgba) for x, z in pts]
    return [row for pair in zip(rows[:-1], rows[1:], strict=True) for row in pair]


def outline(session, cells: np.ndarray, per: int, rgba, lift: float = 0.3) -> np.ndarray:
    """The segments round the region of ``True`` cells of ``cells`` (``per`` tiles a cell), as the view's line rows."""
    n = len(cells)
    pad = np.pad(cells, 1, constant_values=False)
    rows: list[tuple] = []
    for z in range(n + 1):
        for x in range(n):
            if pad[z, x + 1] != pad[z + 1, x + 1]:  # a horizontal edge at z between rows z - 1 and z
                rows += _edge(session, (x * per, z * per), ((x + 1) * per, z * per), rgba, lift)
    for z in range(n):
        for x in range(n + 1):
            if pad[z + 1, x] != pad[z + 1, x + 1]:
                rows += _edge(session, (x * per, z * per), (x * per, (z + 1) * per), rgba, lift)
    return np.asarray(rows, "f4").reshape(-1, 7)


def crosses(session, cells: np.ndarray, per: int, rgba, lift: float = 0.3) -> np.ndarray:
    """A cross in each cell of the boolean array ``cells`` (the cells the edits changed)."""
    rows: list[tuple] = []
    for z, x in np.argwhere(cells):
        x0, z0, x1, z1 = x * per, z * per, (x + 1) * per, (z + 1) * per
        rows += _edge(session, (x0, z0), (x1, z1), rgba, lift) + _edge(session, (x1, z0), (x0, z1), rgba, lift)
    return np.asarray(rows, "f4").reshape(-1, 7)


def active_overlay(view) -> list:
    """The overlay of the grid the cell tool edits (``view.brush["grid"]``: the ford grid, the build area, the herd
    area, the tint strokes, else the active map): the outline of its cells in the selection colour (the build area's other ids each in a
    colour of their own), the changed cells crossed."""
    grid = view.brush.get("grid")
    if grid in ("ford", "environment", "tint"):
        return {"ford": ford_overlay, "environment": environment_overlay, "tint": tint_overlay}[grid](view)
    a = view.session.cells(grid)
    if a.cells is None:
        return []
    out = [("lines", outline(view.session, a.cells, a.cell, view._rgba("select")))]
    for k in range(1, len(getattr(a, "kind_ids", ())) + 1):  # the island's other areas, each in a colour of its own
        out.append(("lines", outline(view.session, a.kind_cells == k, a.cell, (*_zone_colour(k), 1.0))))
    changed = a.cells != a.base
    if getattr(a, "kind_base", None) is not None:
        changed |= a.kind_cells != a.kind_base
    if changed.any():
        out.append(("lines", crosses(view.session, changed, a.cell, ACCENT)))
    return out


POINTS = 20000  # the most points of one kind drawn: a 768-tile island has 147,000 cells
CALM = (0.35, 0.65, 0.9, 1.0)  # cells whose coast waves are weaker than full; the river tiles a ford may lie on


def _cell_points(session, mask: np.ndarray, per: int, rgb, alpha) -> list[tuple]:
    """A point on the ground at the centre of every cell of ``mask`` (``per`` tiles a cell), in view space: the
    colour ``rgb`` (one, or one per cell), ``alpha`` a number or one per cell; at most ``POINTS``, spread evenly."""
    zs, xs = np.nonzero(mask)
    if len(zs) > POINTS:
        keep = np.linspace(0, len(zs) - 1, POINTS).astype(int)
        zs, xs = zs[keep], xs[keep]
    px, pz = (xs + 0.5) * per, (zs + 0.5) * per
    colour = rgb[zs, xs] if np.ndim(rgb) == 3 else np.tile(rgb, (len(zs), 1))
    a = alpha[zs, xs] if np.ndim(alpha) else np.full(len(zs), alpha)
    rows = np.column_stack([session.size - px, session.heights_at(px, pz) + 0.25, pz, colour, a])
    return list(map(tuple, rows))


def _point_overlay(rows: list[tuple]) -> list:
    return [("points", np.asarray(rows, "f4").reshape(-1, 7))] if rows else []


def ford_overlay(view) -> list:
    """The ford grid for the view's overlay list: the river tiles a ford may lie on as faint blue points, the fords
    in the selection colour, the tiles the edits changed crossed in the editor's orange."""
    f = view.session.ford
    if f.cells is None:
        return []
    free = f.allowed & ~f.cells if f.allowed is not None else np.zeros_like(f.cells)
    rows = _cell_points(view.session, free, f.cell, CALM[:3], 0.3)
    rows += _cell_points(view.session, f.cells, f.cell, view._rgba("select")[:3], 1.0)
    out = _point_overlay(rows)
    changed = f.cells != f.base
    if changed.any():
        out.append(("lines", crosses(view.session, changed, f.cell, ACCENT)))
    return out


def tint_overlay(view) -> list:
    """The tint strokes for the view's point overlay: a point on the ground at every point of a stroke, in the stroke's
    colour (a factor of 1 is mid grey, more is lighter); the ground itself shows the tint after a build."""
    s = view.session
    rows = []
    for stroke in s.edits.get("tint", []):
        colour = np.clip(np.asarray(stroke["color"], float) * 0.5, 0, 1)
        rows += [(s.size - x, s.height_at(x, z) + 0.25, z, *colour, 1.0) for x, z in stroke["points"]]
    return _point_overlay(rows[:POINTS])


def _zone_colour(zone: int) -> tuple[float, float, float]:
    """A zone's colour: the golden-ratio walk round the hue circle keeps neighbouring numbers apart."""
    r, g, b = colorsys.hsv_to_rgb((zone * 0.618034) % 1.0, 0.65, 1.0)
    return r, g, b


def environment_overlay(view) -> list:
    """The environment zones for the view's overlay list: a point on the ground at every cell in its zone's colour,
    the cells the edits changed crossed in the editor's orange."""
    e = view.session.environment
    if e.cells is None:
        return []
    colours = np.zeros((*e.cells.shape, 3))
    for zone in np.unique(e.cells):
        colours[e.cells == zone] = _zone_colour(int(zone))
    out = _point_overlay(_cell_points(view.session, np.ones(e.cells.shape, bool), e.per, colours, 0.8))
    changed = e.changed()
    if changed.any():
        out.append(("lines", crosses(view.session, changed, e.per, ACCENT)))
    return out


def surf_overlay(view) -> list:
    """The sea map's coast waves for the view's point overlay: a point on the ground at every cell with waves weaker
    than full (blue, fainter as the waves weaken) and the cells the edits changed in the editor's orange."""
    s = view.session
    cells, base = s.surf.cells, s.surf.base
    if cells is None:
        return []
    changed = cells != base
    weak = (cells < 255) & ~changed
    rows = _cell_points(s, weak, s.surf.per, CALM[:3], 0.35 + 0.65 * (1 - cells / 255))
    rows += _cell_points(s, changed, s.surf.per, ACCENT[:3], 1.0)
    return _point_overlay(rows)
