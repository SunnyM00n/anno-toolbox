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


def _edge(session, start, end, rgba, lift_m: float) -> list[tuple]:
    """Line segments from (x, z) ``start`` to ``end`` in tiles, each point on the ground, in view space (x mirrored)."""
    step_count = max(1, round(float(np.hypot(end[0] - start[0], end[1] - start[1])) / STEP))
    points = [
        (start[0] + (end[0] - start[0]) * k / step_count, start[1] + (end[1] - start[1]) * k / step_count)
        for k in range(step_count + 1)
    ]
    rows = [(session.m_size_tiles - x, session.height_at(x, z) + lift_m, z, *rgba) for x, z in points]
    return [row for pair in zip(rows[:-1], rows[1:], strict=True) for row in pair]


def outline(session, cells: np.ndarray, per: int, rgba, lift_m: float = 0.3) -> np.ndarray:
    """The segments round the region of ``True`` cells of ``cells`` (``per`` tiles a cell), as the view's line rows."""
    pad = np.pad(np.asarray(cells, bool), 1, constant_values=False)
    rows: list[tuple] = []
    # a horizontal edge at z lies between rows z - 1 and z; the changes are found for the whole grid at once
    for z, x in np.argwhere(pad[:-1, 1:-1] != pad[1:, 1:-1]).tolist():
        rows += _edge(session, (x * per, z * per), ((x + 1) * per, z * per), rgba, lift_m)
    for z, x in np.argwhere(pad[1:-1, :-1] != pad[1:-1, 1:]).tolist():
        rows += _edge(session, (x * per, z * per), (x * per, (z + 1) * per), rgba, lift_m)
    return np.asarray(rows, "f4").reshape(-1, 7)


def crosses(session, cells: np.ndarray, per: int, rgba, lift_m: float = 0.3) -> np.ndarray:
    """A cross in each cell of the boolean array ``cells`` (the cells the edits changed)."""
    rows: list[tuple] = []
    for z, x in np.argwhere(cells):
        x0, z0, x1, z1 = x * per, z * per, (x + 1) * per, (z + 1) * per
        rows += _edge(session, (x0, z0), (x1, z1), rgba, lift_m) + _edge(session, (x1, z0), (x0, z1), rgba, lift_m)
    return np.asarray(rows, "f4").reshape(-1, 7)


def active_overlay(view) -> list:
    """The overlay of the grid the cell tool edits (``view.m_brush["grid"]``: the ford grid, the build area, the herd
    area, the tint strokes, else the active map): the outline of its cells in the selection colour (the build area's other ids each in a
    colour of their own), the changed cells crossed."""
    grid = view.m_brush.get("grid")
    if grid in ("ford", "environment", "tint"):
        return {"ford": ford_overlay, "environment": environment_overlay, "tint": tint_overlay}[grid](view)
    cells = view.m_session.cells(grid)
    if cells.m_cells is None:
        return []
    out = [("lines", outline(view.m_session, cells.m_cells, cells.m_cell_tiles, view._rgba("select")))]
    for k in range(
        1, len(getattr(cells, "m_kind_ids", ())) + 1
    ):  # the island's other areas, each in a colour of its own
        out.append(
            ("lines", outline(view.m_session, cells.m_kind_cells == k, cells.m_cell_tiles, (*_zone_colour(k), 1.0)))
        )
    changed = cells.m_cells != cells.m_base
    if getattr(cells, "m_kind_base", None) is not None:
        changed |= cells.m_kind_cells != cells.m_kind_base
    if changed.any():
        out.append(("lines", crosses(view.m_session, changed, cells.m_cell_tiles, ACCENT)))
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
    alphas = alpha[zs, xs] if np.ndim(alpha) else np.full(len(zs), alpha)
    rows = np.column_stack([session.m_size_tiles - px, session.heights_at(px, pz) + 0.25, pz, colour, alphas])
    return list(map(tuple, rows))


def _point_overlay(rows: list[tuple]) -> list:
    return [("points", np.asarray(rows, "f4").reshape(-1, 7))] if rows else []


def ford_overlay(view) -> list:
    """The ford grid for the view's overlay list: the river tiles a ford may lie on as faint blue points, the fords
    in the selection colour, the tiles the edits changed crossed in the editor's orange."""
    ford = view.m_session.m_ford
    if ford.m_cells is None:
        return []
    free = ford.m_allowed & ~ford.m_cells if ford.m_allowed is not None else np.zeros_like(ford.m_cells)
    rows = _cell_points(view.m_session, free, ford.m_cell_tiles, CALM[:3], 0.3)
    rows += _cell_points(view.m_session, ford.m_cells, ford.m_cell_tiles, view._rgba("select")[:3], 1.0)
    out = _point_overlay(rows)
    changed = ford.m_cells != ford.m_base
    if changed.any():
        out.append(("lines", crosses(view.m_session, changed, ford.m_cell_tiles, ACCENT)))
    return out


def tint_overlay(view) -> list:
    """The tint strokes for the view's point overlay: a point on the ground at every point of a stroke, in the stroke's
    colour (a factor of 1 is mid grey, more is lighter); the ground itself shows the tint after a build."""
    session = view.m_session
    rows = []
    for stroke in session.m_edits.get("tint", []):
        colour = np.clip(np.asarray(stroke["color"], float) * 0.5, 0, 1)
        rows += [
            (session.m_size_tiles - x, session.height_at(x, z) + 0.25, z, *colour, 1.0) for x, z in stroke["points"]
        ]
    return _point_overlay(rows[:POINTS])


def _zone_colour(zone: int) -> tuple[float, float, float]:
    """A zone's colour: the golden-ratio walk round the hue circle keeps neighbouring numbers apart."""
    red, green, blue = colorsys.hsv_to_rgb((zone * 0.618034) % 1.0, 0.65, 1.0)
    return red, green, blue


def environment_overlay(view) -> list:
    """The environment zones for the view's overlay list: a point on the ground at every cell in its zone's colour,
    the cells the edits changed crossed in the editor's orange."""
    environment = view.m_session.m_environment
    if environment.m_cells is None:
        return []
    colours = np.zeros((*environment.m_cells.shape, 3))
    for zone in np.unique(environment.m_cells):
        colours[environment.m_cells == zone] = _zone_colour(int(zone))
    out = _point_overlay(
        _cell_points(
            view.m_session, np.ones(environment.m_cells.shape, bool), environment.m_tiles_per_cell, colours, 0.8
        )
    )
    changed = environment.changed()
    if changed.any():
        out.append(("lines", crosses(view.m_session, changed, environment.m_tiles_per_cell, ACCENT)))
    return out


def surf_overlay(view) -> list:
    """The sea map's coast waves for the view's point overlay: a point on the ground at every cell with waves weaker
    than full (blue, fainter as the waves weaken) and the cells the edits changed in the editor's orange."""
    session = view.m_session
    cells, base = session.m_surf.m_cells, session.m_surf.m_base
    if cells is None:
        return []
    changed = cells != base
    weak = (cells < 255) & ~changed
    rows = _cell_points(session, weak, session.m_surf.m_tiles_per_cell, CALM[:3], 0.35 + 0.65 * (1 - cells / 255))
    rows += _cell_points(session, changed, session.m_surf.m_tiles_per_cell, ACCENT[:3], 1.0)
    return _point_overlay(rows)
