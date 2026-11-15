"""The scene geometry the renderer (``desktoprender``) builds itself: the terrain grid from the editor's
heights, a river's ribbon, a lake's surface (ear clipping) and the marker pin. View space: x mirrored."""

from __future__ import annotations

import math

import numpy as np


def terrain_vertices(heights: np.ndarray, step: float, rows: slice | None = None) -> np.ndarray:
    """Positions (x mirrored) and normals of a height grid: [n, 6]; ``rows``: only those rows."""
    w = heights.shape[0]
    size = (w - 1) * step
    rows = rows or slice(0, w)
    j0, j1 = rows.start, rows.stop
    a, b = max(0, j0 - 1), min(w, j1 + 1)  # a row either side for the slopes
    part = heights[a:b].astype(np.float32)
    dz, dx = np.gradient(part, step) if part.shape[0] > 1 else (np.zeros_like(part), np.gradient(part[0], step)[None])
    dz, dx = dz[j0 - a : j0 - a + (j1 - j0)], dx[j0 - a : j0 - a + (j1 - j0)]
    h = heights[j0:j1].astype(np.float32)
    j, i = np.mgrid[j0:j1, 0:w].astype(np.float32)
    normal = np.stack([dx, np.ones_like(dx), -dz], -1)  # x is mirrored, so is its slope's sign
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    return np.concatenate([np.stack([size - i * step, h, j * step], -1), normal], -1).reshape(-1, 6).astype("f4")


def outline(polys, to_view, height_at, colour, lift: float = 0.15) -> list:
    """Footprint polygons (world x, z rows) as an overlay of lines on the ground: ``(mode, rows)`` of (view x, y, z,
    r, g, b, a), ``lift`` over the ground; [] for none."""
    rows = [
        (*to_view([x, height_at(x, z) + lift, z]), *colour)
        for poly in polys
        for a, b in zip(poly, np.roll(poly, -1, axis=0), strict=True)
        for x, z in (a, b)
    ]
    return [("lines", np.asarray(rows, "f4").reshape(-1, 7))] if rows else []


def ribbon(points, widths, lift: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """A river's surface along its centreline (view coordinates): vertices [2n, 3] and triangles."""
    pts = np.asarray(points, "f4")
    pos, idx = [], []
    for k, p in enumerate(pts):
        q, r = pts[min(k + 1, len(pts) - 1)], pts[max(k - 1, 0)]
        dx, dz = q[0] - r[0], q[2] - r[2]
        n = math.hypot(dx, dz) or 1.0
        dx, dz = dx / n, dz / n
        half = (widths[k] if k < len(widths) else (widths[-1] if widths else 4.0)) / 2
        pos += [(p[0] - dz * half, p[1] + lift, p[2] + dx * half), (p[0] + dz * half, p[1] + lift, p[2] - dx * half)]
        if k:
            idx += [2 * k - 2, 2 * k - 1, 2 * k, 2 * k - 1, 2 * k + 1, 2 * k]
    return np.asarray(pos, "f4").reshape(-1, 3), np.asarray(idx, "u4")


def triangulate(poly) -> list[tuple[int, int, int]]:
    """Ear clipping of a simple polygon (x, z): the triangles' corner indices."""
    pts = [tuple(p) for p in poly]
    n = len(pts)
    if n < 3:
        return []
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    order = list(range(n)) if area > 0 else list(range(n))[::-1]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0

    out, guard = [], 0
    while len(order) > 3 and guard < 10 * n:
        guard += 1
        for k in range(len(order)):
            i0, i1, i2 = order[k - 1], order[k], order[(k + 1) % len(order)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if cross(a, b, c) <= 0:
                continue
            if any(inside(pts[j], a, b, c) for j in order if j not in (i0, i1, i2)):
                continue
            out.append((i0, i1, i2))
            del order[k]
            break
        else:
            break  # not simple: what is left stays open
    if len(order) == 3:
        out.append(tuple(order))
    return out


def pin_mesh() -> tuple[np.ndarray, np.ndarray]:
    """An editor-only object's marker: a cone pointing down with a ball on top (vertices [n, 8])."""
    verts, idx = [], []
    seg = 12
    for k in range(seg):  # cone: tip at 0, rim of radius 0.45 at 1.6
        a0, a1 = 2 * math.pi * k / seg, 2 * math.pi * (k + 1) / seg
        base = len(verts)
        for a in (a0, a1):
            nx, nz = math.cos(a), math.sin(a)
            verts.append((0.45 * nx, 1.6, 0.45 * nz, nx, -0.28, nz, 0, 0))
        verts.append((0, 0, 0, math.cos((a0 + a1) / 2), -0.28, math.sin((a0 + a1) / 2), 0, 0))
        idx += [base, base + 1, base + 2]
    rings, base = 8, len(verts)
    for r in range(rings + 1):
        phi = math.pi * r / rings
        for k in range(seg + 1):
            th = 2 * math.pi * k / seg
            n = (math.sin(phi) * math.cos(th), math.cos(phi), math.sin(phi) * math.sin(th))
            verts.append((0.55 * n[0], 1.9 + 0.55 * n[1], 0.55 * n[2], *n, 0, 0))
    for r in range(rings):
        for k in range(seg):
            a = base + r * (seg + 1) + k
            idx += [a, a + seg + 1, a + 1, a + 1, a + seg + 1, a + seg + 2]
    return np.asarray(verts, "f4"), np.asarray(idx, "u4")
