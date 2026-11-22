"""The scene geometry the renderer (``desktoprender``) builds itself: the terrain grid from the editor's
heights, a river's ribbon, a lake's surface (ear clipping) and the marker pin. View space: x mirrored."""

from __future__ import annotations

import math

import numpy as np


def terrain_vertices(heights: np.ndarray, step_tiles: float, rows: slice | None = None) -> np.ndarray:
    """Positions (x mirrored) and normals of a height grid: [n, 6]; ``rows``: only those rows."""
    row_count = heights.shape[0]
    size = (row_count - 1) * step_tiles
    rows = rows or slice(0, row_count)
    first_row, end_row = rows.start, rows.stop
    slope_start, slope_end = max(0, first_row - 1), min(row_count, end_row + 1)  # a row either side for the slopes
    part = heights[slope_start:slope_end].astype(np.float32)
    slope_z, slope_x = (
        np.gradient(part, step_tiles)
        if part.shape[0] > 1
        else (np.zeros_like(part), np.gradient(part[0], step_tiles)[None])
    )
    slope_z, slope_x = (
        slope_z[first_row - slope_start : first_row - slope_start + (end_row - first_row)],
        slope_x[first_row - slope_start : first_row - slope_start + (end_row - first_row)],
    )
    row_heights = heights[first_row:end_row].astype(np.float32)
    j, i = np.mgrid[first_row:end_row, 0:row_count].astype(np.float32)
    normal = np.stack([slope_x, np.ones_like(slope_x), -slope_z], -1)  # x is mirrored, so is its slope's sign
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    return (
        np.concatenate([np.stack([size - i * step_tiles, row_heights, j * step_tiles], -1), normal], -1)
        .reshape(-1, 6)
        .astype("f4")
    )


def outline(polygons, to_view, height_at, colour, lift_m: float = 0.15) -> list:
    """Footprint polygons (world x, z rows) as an overlay of lines on the ground: ``(mode, rows)`` of (view x, y, z,
    r, g, b, a), ``lift`` over the ground; [] for none."""
    rows = [
        (*to_view([x, height_at(x, z) + lift_m, z]), *colour)
        for polygon in polygons
        for start, end in zip(polygon, np.roll(polygon, -1, axis=0), strict=True)
        for x, z in (start, end)
    ]
    return [("lines", np.asarray(rows, "f4").reshape(-1, 7))] if rows else []


def river_lines(island: dict) -> tuple[list[dict], tuple[int, int]]:
    """The island's river and lake lines and a cheap stamp of them: a frame compares the stamp, not the points. The
    stamp is the list's identity and length (the session keeps the list for as long as it is open)."""
    splines = island.get("splines", [])
    return [spline for spline in splines if spline["kind"] in ("River", "Lake")], (id(splines), len(splines))


def ribbon(points, widths, lift_m: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """A river's surface along its centreline (view coordinates): vertices [2n, 3] and triangles."""
    centreline = np.asarray(points, "f4")
    positions, indices = [], []
    for k, point in enumerate(centreline):
        next_point, previous_point = centreline[min(k + 1, len(centreline) - 1)], centreline[max(k - 1, 0)]
        direction_x, direction_z = next_point[0] - previous_point[0], next_point[2] - previous_point[2]
        length = math.hypot(direction_x, direction_z) or 1.0
        direction_x, direction_z = direction_x / length, direction_z / length
        half = (widths[k] if k < len(widths) else (widths[-1] if widths else 4.0)) / 2
        positions += [
            (point[0] - direction_z * half, point[1] + lift_m, point[2] + direction_x * half),
            (point[0] + direction_z * half, point[1] + lift_m, point[2] - direction_x * half),
        ]
        if k:
            indices += [2 * k - 2, 2 * k - 1, 2 * k, 2 * k - 1, 2 * k + 1, 2 * k]
    return np.asarray(positions, "f4").reshape(-1, 3), np.asarray(indices, "u4")


def triangulate(polygon) -> list[tuple[int, int, int]]:
    """Ear clipping of a simple polygon (x, z): the triangles' corner indices."""
    corners = [tuple(point) for point in polygon]
    corner_count = len(corners)
    if corner_count < 3:
        return []
    area = sum(
        corners[i][0] * corners[(i + 1) % corner_count][1] - corners[(i + 1) % corner_count][0] * corners[i][1]
        for i in range(corner_count)
    )
    order = list(range(corner_count)) if area > 0 else list(range(corner_count))[::-1]

    def cross(origin, corner_a, corner_b):
        return (corner_a[0] - origin[0]) * (corner_b[1] - origin[1]) - (corner_a[1] - origin[1]) * (
            corner_b[0] - origin[0]
        )

    def inside(point, corner_a, corner_b, corner_c):
        return (
            cross(corner_a, corner_b, point) >= 0
            and cross(corner_b, corner_c, point) >= 0
            and cross(corner_c, corner_a, point) >= 0
        )

    out, guard = [], 0
    while len(order) > 3 and guard < 10 * corner_count:
        guard += 1
        for k in range(len(order)):
            previous_index, current_index, next_index = order[k - 1], order[k], order[(k + 1) % len(order)]
            corner_a, corner_b, corner_c = corners[previous_index], corners[current_index], corners[next_index]
            if cross(corner_a, corner_b, corner_c) <= 0:
                continue
            if any(
                inside(corners[j], corner_a, corner_b, corner_c)
                for j in order
                if j not in (previous_index, current_index, next_index)
            ):
                continue
            out.append((previous_index, current_index, next_index))
            del order[k]
            break
        else:
            break  # not simple: what is left stays open
    if len(order) == 3:
        out.append(tuple(order))
    return out


def pin_mesh() -> tuple[np.ndarray, np.ndarray]:
    """An editor-only object's marker: a cone pointing down with a ball on top (vertices [n, 8])."""
    vertices, indices = [], []
    segments = 12
    for k in range(segments):  # cone: tip at 0, rim of radius 0.45 at 1.6
        angle_start, angle_end = 2 * math.pi * k / segments, 2 * math.pi * (k + 1) / segments
        base = len(vertices)
        for angle in (angle_start, angle_end):
            normal_x, normal_z = math.cos(angle), math.sin(angle)
            vertices.append((0.45 * normal_x, 1.6, 0.45 * normal_z, normal_x, -0.28, normal_z, 0, 0))
        vertices.append(
            (0, 0, 0, math.cos((angle_start + angle_end) / 2), -0.28, math.sin((angle_start + angle_end) / 2), 0, 0)
        )
        indices += [base, base + 1, base + 2]
    rings, base = 8, len(vertices)
    for ring in range(rings + 1):
        phi = math.pi * ring / rings
        for k in range(segments + 1):
            theta = 2 * math.pi * k / segments
            sphere_normal = (math.sin(phi) * math.cos(theta), math.cos(phi), math.sin(phi) * math.sin(theta))
            vertices.append(
                (0.55 * sphere_normal[0], 1.9 + 0.55 * sphere_normal[1], 0.55 * sphere_normal[2], *sphere_normal, 0, 0)
            )
    for ring in range(rings):
        for k in range(segments):
            corner = base + ring * (segments + 1) + k
            indices += [
                corner,
                corner + segments + 1,
                corner + 1,
                corner + 1,
                corner + segments + 1,
                corner + segments + 2,
            ]
    return np.asarray(vertices, "f4"), np.asarray(indices, "u4")
