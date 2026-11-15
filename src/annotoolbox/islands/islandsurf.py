"""2070's surf lines as the coast-wave byte of an imported island's sea map.

2070 spawns its coast waves along the island's ``SurfLines``: polylines with a width per point and a setting
(``surfsettings.xml``). The shipped 2070 never uses them (its waves come from the sea shader on every coast), so
by default nothing is painted. 117's sea shader takes the coast waves from byte 1 of ``WaterInfos``
(rendering/water.md, "Physical waves and coast waves"); with ``SURF_LINES`` on, the lines' bands are painted there,
graded by the setting's ``WaveHeightFactor``, and the sea elsewhere gets none (an archived experiment).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

# False (default, as 2070 never uses its lines): byte 1 is 255 over the sea, the constant 117's own islands carry, so
# waves run on every coast; True paints the lines' bands, an archived experiment
SURF_LINES = False
# surfsettings.xml of 2070 (Complete Edition): name -> WaveHeightFactor; ``read_settings`` reads the file itself
SETTINGS_FALLBACK = {"Default": 0.9, "Default_Slow": 0.8, "Steep": 1.0, "Spume": 1.0}
SURF_PATH = "data/config/engine/surfsettings.xml"
INLAND = 255  # lakes and rivers: the flattest water, as vanilla paints them


@dataclass(frozen=True)
class SurfLine:
    setting: str
    points: list[tuple[float, float, float]]  # island tiles: x, z, width (the band's full width in tiles)


def read_settings(tree) -> dict[str, float]:
    """Setting name -> ``WaveHeightFactor`` from 2070's ``surfsettings.xml`` (``tree``: the ``SourceTree``)."""
    text = tree.read_bytes(SURF_PATH).decode("utf-8", "ignore")
    out = {}
    for block in re.findall(r"<SurfSetting>(.*?)</SurfSetting>", text, re.S):
        name = re.search(r"<Name>([^<]*)</Name>", block)
        factor = re.search(r"<WaveHeightFactor>([^<]*)</WaveHeightFactor>", block)
        if name and factor:
            out[name.group(1)] = float(factor.group(1))
    return out


def surf_lines(isl) -> list[SurfLine]:
    """The island's surf lines: a setting and the points (x, z, width) of each."""
    node = isl.root.path("SurfLines")
    out = []
    for item in node.children if node is not None else []:
        points = item.path("SurfLinePoints")
        pts = []
        for pt in points.children if points is not None else []:
            pos = pt.find("Position")
            if pos is not None and pos.data and len(pos.data) == 12:
                x, _, z = struct.unpack("<3f", pos.data)
                pts.append((x, z, pt.float("Width", 1.0)))
        if pts:
            out.append(SurfLine(item.value("SurfSetting", "Default"), pts))
    return out


def grade(factor: float) -> int:
    """The byte for a ``WaveHeightFactor`` (1 = full)."""
    return int(round(255 * min(max(factor, 0.0), 1.0)))


def band(shape, cell: float, pts, offset: tuple[float, float]):
    """Per-cell distance-in-half-widths test: a boolean grid of the cells (``cell`` tiles wide, centres at
    ``(i + 0.5) * cell``) within half the line's interpolated width of the polyline ``pts`` (x, z, width)."""
    import numpy as np

    px, pz = offset
    hit = np.zeros(shape, bool)
    xs = (np.arange(shape[1]) + 0.5) * cell
    zs = (np.arange(shape[0]) + 0.5) * cell
    nodes = [(x + px, z + pz, w) for x, z, w in pts]
    if len(nodes) == 1:
        nodes = nodes * 2
    for (x0, z0, w0), (x1, z1, w1) in zip(nodes, nodes[1:], strict=False):
        half = max(w0, w1) / 2 + cell / 2
        ix = np.flatnonzero((xs >= min(x0, x1) - half) & (xs <= max(x0, x1) + half))
        iz = np.flatnonzero((zs >= min(z0, z1) - half) & (zs <= max(z0, z1) + half))
        if not len(ix) or not len(iz):
            continue
        gx, gz = np.meshgrid(xs[ix], zs[iz])
        dx, dz = x1 - x0, z1 - z0
        length2 = dx * dx + dz * dz
        t = np.clip(((gx - x0) * dx + (gz - z0) * dz) / length2, 0, 1) if length2 else np.zeros_like(gx)
        width = (w0 + (w1 - w0) * t) / 2
        dist = np.hypot(gx - (x0 + t * dx), gz - (z0 + t * dz))
        hit[np.ix_(iz, ix)] |= dist <= np.maximum(width, cell / 2)
    return hit


def surf_map(shape, cell: float, lines, factors: dict[str, float], offset: tuple[float, float]):
    """Byte 1 of the sea map over ``shape`` cells of ``cell`` tiles: each line's band at its setting's grade (the
    highest where bands overlap), 0 elsewhere. ``offset``: the island's tiles in the grid's (``pad_x``, ``pad_z``)."""
    import numpy as np

    out = np.zeros(shape, np.uint8)
    for line in lines:
        value = grade(factors.get(line.setting, SETTINGS_FALLBACK.get(line.setting, 1.0)))
        mask = band(shape, cell, line.points, offset)
        out[mask] = np.maximum(out[mask], value)
    return out


def inland_cells(builder, shape, cell: float):
    """Cells of the builder's lakes and rivers (boolean grid), which keep the flat-water byte."""
    import numpy as np

    from .islandgrid import dist_to_polygon, point_in_polygon

    out = np.zeros(shape, bool)
    centres = (np.arange(shape[0]) + 0.5) * cell
    for _, poly in builder._lake_polygons():
        xs, zs = [x for x, _ in poly], [z for _, z in poly]
        for iz in np.flatnonzero((centres >= min(zs) - 1) & (centres <= max(zs) + 1)):
            for ix in np.flatnonzero((centres >= min(xs) - 1) & (centres <= max(xs) + 1)):
                cx, cz = centres[ix], centres[iz]
                if point_in_polygon(cx, cz, poly) or dist_to_polygon(cx, cz, poly) <= 1.0:
                    out[iz, ix] = True
    for pts, widths in getattr(builder, "rivers", []):
        out |= band(shape, cell, [(p[0], p[2], w + 2) for p, w in zip(pts, widths, strict=False)], (0.0, 0.0))
    return out


def apply(builder) -> dict:
    """Write byte 1 of ``builder``'s ``WaterInfos`` from its island's surf lines (after ``apply_water_maps``); returns
    the report entry. Without lines, or with ``SURF_LINES`` off, the 255 constant stays."""
    import numpy as np

    isl = getattr(builder, "isl", None)
    if getattr(isl, "root", None) is None:  # a vanilla island of the editor has no surf lines to read
        return {"surf lines": 0}
    wi = builder.rd.path("WaterInfos")
    lines = surf_lines(isl) if SURF_LINES else []
    if wi is None or not lines:
        return {"surf lines": 0}
    w = wi.find("width").u32()
    cell = builder.size / w
    info = np.frombuffer(wi.find("map").data, np.uint8).reshape(w, w, 4).copy()
    factors = getattr(builder, "surf_settings", None) or SETTINGS_FALLBACK
    surf = surf_map((w, w), cell, lines, factors, (builder.pad_x, builder.pad_z))
    inland = inland_cells(builder, (w, w), cell)
    info[..., 1] = np.where(inland, INLAND, surf)
    wi.find("map").data = info.tobytes()
    covered = float(np.count_nonzero(info[..., 1])) / info[..., 1].size
    report = {"surf lines": len(lines), "surf share of the map": round(covered, 3)}
    builder.report.update(report)
    return report
