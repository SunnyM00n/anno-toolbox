"""What the island modules share: heights in world units, FileDB bit and sparse grids, polygon tests."""

from __future__ import annotations

import math
import struct
import warnings

from ..formats import filedb, isd2070

HEIGHT_UNITS = 4.0  # 117 heightmap units per world unit (object and spline positions are world units)
# 2070 heights are kept (world units: sea bed, 0.7 plateau and relief as in 2070; vanilla docks
# also stand on a shelf about 0.5 above the sea)

# 2070's land build test: every footprint tile's logic height (its corners' mean) over this; the building stands at 0.7
# whatever the ground (engine-placement "Building height")
BUILD_FLOOR = 0.6
UNDERWATER_BUILD = (-27.1, -26.9)  # 2070's underwater build level (its "not at a build level" grid), world units
AREA_ISLAND = 0x2001  # AreaIDs value of the island's buildable area
AREA_NONE = 1

FIXED = isd2070.FIXED


def map_height(h):
    """2070 height (world units) -> 117 heightmap height (``HEIGHT_UNITS`` per world unit)."""
    return h * HEIGHT_UNITS


def cell_key(key, size: int) -> tuple[int, int] | None:
    """The (x, z) an edit file's ``"x,z"`` key names, None when it is no such key or lies off a grid ``size`` cells a side."""
    try:
        x, z = (int(v) for v in str(key).split(","))
    except ValueError:
        return None
    return (x, z) if 0 <= x < size and 0 <= z < size else None


def _bit_stride(w: int) -> int:
    """Bits per row of a FileDB bit grid: rows are padded to 32-bit words (a 16x16 grid takes 64
    bytes, 80x80 takes 960; packed rows misread every grid narrower than 32 or not a multiple of
    32, e.g. the Levels HasMeshArray of LOD 4 and up)."""
    return (w + 31) // 32 * 32


def read_bits(tag: filedb.Tag) -> list[list[int]]:
    w, h = tag.find("x").u32(), tag.find("y").u32()
    d = tag.find("bits").data
    stride = _bit_stride(w)
    return [[(d[(y * stride + x) >> 3] >> ((y * stride + x) & 7)) & 1 for x in range(w)] for y in range(h)]


def bits_array(tag: filedb.Tag):
    """The bit grid as a boolean array (rows z): ``read_bits`` without a Python loop per tile, for the large grids."""
    import numpy as np

    w, h = tag.find("x").u32(), tag.find("y").u32()
    stride = _bit_stride(w)
    flat = np.unpackbits(np.frombuffer(tag.find("bits").data, np.uint8), bitorder="little")
    return flat[: h * stride].reshape(h, stride)[:, :w].astype(bool)


def write_bits(tag: filedb.Tag, grid: list[list[int]]) -> None:
    w, h = tag.find("x").u32(), tag.find("y").u32()
    stride = _bit_stride(w)
    out = bytearray(max(len(tag.find("bits").data), stride * h // 8))
    for y in range(h):
        for x in range(w):
            if grid[y][x]:
                k = y * stride + x
                out[k >> 3] |= 1 << (k & 7)
    tag.find("bits").data = bytes(out)


def read_river_grid(node: isd2070.Node | None) -> list[list[int]] | None:
    """2070's ``RiverGrid`` (``m_BitGrid``: one bit per tile, rows ``m_YSize`` of ``m_XSize`` tiles, each row padded
    to ``m_IntXSize`` 32-bit words as 117's bit grids are) as rows of 0/1, None without one. Read packed, every
    island whose width is off the 32 grid (29 of the 79 surface islands) had its river tiles skewed row by row."""
    bits = node.path("m_BitGrid") if node is not None else None
    if bits is None or not bits.data:
        return None
    w, h = node.int("m_XSize"), node.int("m_YSize")
    stride = node.int("m_IntXSize", (w + 31) // 32) * 32
    d = bits.data
    return [[(d[(z * stride + x) >> 3] >> ((z * stride + x) & 7)) & 1 for x in range(w)] for z in range(h)]


def river_grid_node(grid) -> isd2070.Node:
    """A ``RiverGrid`` node from rows of 0/1 (rows z), padded as 2070 stores it (``read_river_grid``)."""
    h, w = len(grid), len(grid[0]) if grid else 0
    words = (w + 31) // 32
    out = bytearray(words * 4 * h)
    for z, row in enumerate(grid):
        for x, v in enumerate(row):
            if v:
                k = z * words * 32 + x
                out[k >> 3] |= 1 << (k & 7)
    children = [
        isd2070.Node("m_XSize", text=str(w)),
        isd2070.Node("m_YSize", text=str(h)),
        isd2070.Node("m_IntXSize", text=str(words)),
        isd2070.Node("m_BitGrid", data=bytes(out)),
    ]
    return isd2070.Node("RiverGrid", children=children)


def read_sparse(tag: filedb.Tag, fmt: str = "H") -> list[list[int]]:
    w, h = tag.find("x").u32(), tag.find("y").u32()
    blocks = tag.findall("block")
    bs = struct.unpack("<H", blocks[0].find("x").data)[0]
    size = struct.calcsize(fmt)
    grid = [[0] * w for _ in range(h)]
    for b in blocks[1:]:
        mode = b.find("mode").data[0] if b.find("mode") is not None else 0
        if mode == 0 and b.find("values") is None:
            continue
        bx = struct.unpack("<H", b.find("x").data)[0] if b.find("x") is not None else 0
        by = struct.unpack("<H", b.find("y").data)[0] if b.find("y") is not None else 0
        if mode == 2:
            d = b.find("default")
            vals = [struct.unpack("<" + fmt, d.data.ljust(size, b"\0"))[0] if d is not None else 0] * (bs * bs)
        else:
            raw = b.find("values").data
            vals = struct.unpack(f"<{len(raw) // size}{fmt}", raw)
        for j in range(bs):
            for i in range(bs):
                if by + j < h and bx + i < w:
                    grid[by + j][bx + i] = vals[j * bs + i]
    return grid


def write_sparse(tag: filedb.Tag, grid: list[list[int]], fmt: str = "H") -> None:
    """Rewrite a sparse block grid the way the shipped islands hold one: the header block (which
    carries the block size), then one ``x``/``y``/``values`` block per 16x16 cell that has
    anything in it, then the terminator.

    Sparse means sparse: a block for every cell (the empty ones a uniform ``mode 2``) gave the forest
    map 258 blocks where vanilla holds 29, and the game grew no tree at all (2026-09-24).
    """
    w, h = tag.find("x").u32(), tag.find("y").u32()
    blocks = tag.findall("block")
    header = blocks[0]
    bs = struct.unpack("<H", header.find("x").data)[0]
    new_blocks = [header]
    for by in range(0, h, bs):
        for bx in range(0, w, bs):
            vals = [grid[by + j][bx + i] if by + j < h and bx + i < w else 0 for j in range(bs) for i in range(bs)]
            if not any(vals):
                continue
            b = filedb.Tag("block")
            if bx:
                b.children.append(filedb.Attrib("x", struct.pack("<H", bx)))
            if by:
                b.children.append(filedb.Attrib("y", struct.pack("<H", by)))
            b.children.append(filedb.Attrib("values", struct.pack(f"<{len(vals)}{fmt}", *vals)))
            new_blocks.append(b)
    new_blocks.append(filedb.Tag("block", [filedb.Attrib("mode", b"\x00")]))
    tag.children = [c for c in tag.children if c.name != "block"] + new_blocks


def fixed_pairs(nodes) -> list[tuple[float, float]]:
    """2070 polygon/line points: CDATA of int64 x, int64 z (fixed point)."""
    return [
        (struct.unpack_from("<q", n.data, 0)[0] / FIXED, struct.unpack_from("<q", n.data, 8)[0] / FIXED)
        for n in nodes
        if n.data
    ]


def edited_points(edit, least: int) -> list[tuple[float, float]] | None:
    """The (x, z) points of a line or outline edit (``{"points": [[x, z], ...]}``), None when it has fewer than ``least``
    or is no list of pairs: a hand-edited file must not stop a build."""
    try:
        points = [(float(p[0]), float(p[1])) for p in edit["points"]]
    except (TypeError, KeyError, IndexError, ValueError):
        return None
    return points if len(points) >= least else None


def nearest_segments(old: list, new: list) -> list[int]:
    """For each segment of the line ``new`` the index of the segment of the line ``old`` whose middle is nearest to its
    middle (a coast line with another number of points keeps the direction of the nearest old segment)."""
    mid = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(old[:-1], old[1:], strict=False)]
    out = []
    for a, b in zip(new[:-1], new[1:], strict=False):
        m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        out.append(min(range(len(mid)), key=lambda i: (mid[i][0] - m[0]) ** 2 + (mid[i][1] - m[1]) ** 2))
    return out


def turned_normals(old: list, old_normals: list, points: list) -> tuple[list[int], list[tuple[float, float]]]:
    """A coast line through new (x, z) ``points``: for each the index of the nearest point of the line ``old`` and the
    normal there, turned with the new line and on the side the old normal faced (a coast faces the sea). A point
    where the line has no direction keeps the old normal."""
    near, normals = [], []
    for k, (x, z) in enumerate(points):
        i = min(range(len(old)), key=lambda j: (old[j][0] - x) ** 2 + (old[j][1] - z) ** 2)
        ahead, behind = points[min(k + 1, len(points) - 1)], points[max(k - 1, 0)]
        sx, sz = ahead[1] - behind[1], behind[0] - ahead[0]  # the line's normal in x, z
        length = math.hypot(sx, sz)
        was = tuple(old_normals[i])
        near.append(i)
        if length < 1e-9:
            normals.append(was)
            continue
        sx, sz = sx / length, sz / length
        normals.append((sx, sz) if sx * was[0] + sz * was[1] >= 0 else (-sx, -sz))
    return near, normals


def dist_to_polygon(x: float, z: float, poly: list[tuple[float, float]]) -> float:
    best = float("inf")
    for k in range(len(poly)):
        (x0, z0), (x1, z1) = poly[k], poly[(k + 1) % len(poly)]
        vx, vz = x1 - x0, z1 - z0
        t = max(0.0, min(1.0, ((x - x0) * vx + (z - z0) * vz) / ((vx * vx + vz * vz) or 1.0)))
        best = min(best, math.hypot(x - x0 - t * vx, z - z0 - t * vz))
    return best


def ground_at(h, xs, zs):
    """A corner grid ``h`` (rows z) at the points (xs, zs), bilinear between its corners, clamped to the grid."""
    import numpy as np

    n, m = h.shape
    x, z = np.clip(xs, 0, m - 1.001), np.clip(zs, 0, n - 1.001)
    ix, iz = x.astype(int), z.astype(int)
    fx, fz = x - ix, z - iz
    top = h[iz, ix] * (1 - fx) + h[iz, ix + 1] * fx
    return top * (1 - fz) + (h[iz + 1, ix] * (1 - fx) + h[iz + 1, ix + 1] * fx) * fz


def point_in_polygon(x: float, z: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, zi = poly[i]
        xj, zj = poly[j]
        if (zi > z) != (zj > z) and x < (xj - xi) * (z - zi) / (zj - zi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def warn_unmatched(edits: dict, count: int, what: str, things: str = "", stacklevel: int = 3) -> None:
    """Warn about the edits of ``edits`` (index -> edit) whose index the built island does not have: its ``count``
    ``things`` are fewer than the edit file knew (the island was built anew since it was written)."""
    stale = sorted(k for k in edits if not str(k).isdigit() or int(k) >= count)
    if stale:
        has = f"{count} {things}" if things else str(count)
        message = f"{what} edits for {', '.join(map(str, stale))} match nothing: the island has {has}"
        warnings.warn(message, RuntimeWarning, stacklevel=stacklevel)
