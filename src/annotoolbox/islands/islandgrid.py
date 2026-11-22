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


def map_height(height_m):
    """2070 height (world units) -> 117 heightmap height (``HEIGHT_UNITS`` per world unit)."""
    return height_m * HEIGHT_UNITS


def parse_pair(key) -> tuple[int, int] | None:
    """The two numbers an edit file's ``"a,b"`` key names, None when it is no such key: a hand-edited file must not
    stop a build."""
    try:
        first, second = (int(part) for part in str(key).split(","))
    except ValueError:
        return None
    return first, second


def cell_key(key, size_tiles: int) -> tuple[int, int] | None:
    """The (x, z) an edit file's ``"x,z"`` key names, None when it is no such key or lies off a grid ``size`` cells a side."""
    cell = parse_pair(key)
    return cell if cell is not None and 0 <= cell[0] < size_tiles and 0 <= cell[1] < size_tiles else None


def _bit_stride(width: int) -> int:
    """Bits per row of a FileDB bit grid: rows are padded to 32-bit words (a 16x16 grid takes 64
    bytes, 80x80 takes 960; packed rows misread every grid narrower than 32 or not a multiple of
    32, e.g. the Levels HasMeshArray of LOD 4 and up)."""
    return (width + 31) // 32 * 32


def read_bits(tag: filedb.STag) -> list[list[int]]:
    width, height = tag.find("x").u32(), tag.find("y").u32()
    data = tag.find("bits").data
    stride = _bit_stride(width)
    return [[(data[(y * stride + x) >> 3] >> ((y * stride + x) & 7)) & 1 for x in range(width)] for y in range(height)]


def bits_array(tag: filedb.STag):
    """The bit grid as a boolean array (rows z): ``read_bits`` without a Python loop per tile, for the large grids."""
    import numpy as np

    width, height = tag.find("x").u32(), tag.find("y").u32()
    stride = _bit_stride(width)
    flat = np.unpackbits(np.frombuffer(tag.find("bits").data, np.uint8), bitorder="little")
    return flat[: height * stride].reshape(height, stride)[:, :width].astype(bool)


def write_bits(tag: filedb.STag, grid: list[list[int]]) -> None:
    width, height = tag.find("x").u32(), tag.find("y").u32()
    stride = _bit_stride(width)
    out = bytearray(max(len(tag.find("bits").data), stride * height // 8))
    for y in range(height):
        for x in range(width):
            if grid[y][x]:
                k = y * stride + x
                out[k >> 3] |= 1 << (k & 7)
    tag.find("bits").data = bytes(out)


def read_river_grid(node: isd2070.SNode | None) -> list[list[int]] | None:
    """2070's ``RiverGrid`` (``m_BitGrid``: one bit per tile, rows ``m_YSize`` of ``m_XSize`` tiles, each row padded
    to ``m_IntXSize`` 32-bit words as 117's bit grids are) as rows of 0/1, None without one. Read packed, every
    island whose width is off the 32 grid (29 of the 79 surface islands) had its river tiles skewed row by row."""
    bits = node.path("m_BitGrid") if node is not None else None
    if bits is None or not bits.data:
        return None
    width, height = node.int("m_XSize"), node.int("m_YSize")
    stride = node.int("m_IntXSize", (width + 31) // 32) * 32
    data = bits.data
    return [[(data[(z * stride + x) >> 3] >> ((z * stride + x) & 7)) & 1 for x in range(width)] for z in range(height)]


def river_grid_node(grid) -> isd2070.SNode:
    """A ``RiverGrid`` node from rows of 0/1 (rows z), padded as 2070 stores it (``read_river_grid``)."""
    height, width = len(grid), len(grid[0]) if grid else 0
    words = (width + 31) // 32
    out = bytearray(words * 4 * height)
    for z, row in enumerate(grid):
        for x, value in enumerate(row):
            if value:
                k = z * words * 32 + x
                out[k >> 3] |= 1 << (k & 7)
    children = [
        isd2070.SNode("m_XSize", text=str(width)),
        isd2070.SNode("m_YSize", text=str(height)),
        isd2070.SNode("m_IntXSize", text=str(words)),
        isd2070.SNode("m_BitGrid", data=bytes(out)),
    ]
    return isd2070.SNode("RiverGrid", children=children)


def read_sparse(tag: filedb.STag, value_format: str = "H") -> list[list[int]]:
    width, height = tag.find("x").u32(), tag.find("y").u32()
    blocks = tag.findall("block")
    bs = struct.unpack("<H", blocks[0].find("x").data)[0]
    size = struct.calcsize(value_format)
    grid = [[0] * width for _ in range(height)]
    for block in blocks[1:]:
        mode = block.find("mode").data[0] if block.find("mode") is not None else 0
        if mode == 0 and block.find("values") is None:
            continue
        bx = struct.unpack("<H", block.find("x").data)[0] if block.find("x") is not None else 0
        by = struct.unpack("<H", block.find("y").data)[0] if block.find("y") is not None else 0
        if mode == 2:
            default_tag = block.find("default")
            values = [
                struct.unpack("<" + value_format, default_tag.data.ljust(size, b"\0"))[0]
                if default_tag is not None
                else 0
            ] * (bs * bs)
        else:
            raw = block.find("values").data
            values = struct.unpack(f"<{len(raw) // size}{value_format}", raw)
        for j in range(bs):
            for i in range(bs):
                if by + j < height and bx + i < width:
                    grid[by + j][bx + i] = values[j * bs + i]
    return grid


def write_sparse(tag: filedb.STag, grid: list[list[int]], value_format: str = "H") -> None:
    """Rewrite a sparse block grid the way the shipped islands hold one: the header block (which
    carries the block size), then one ``x``/``y``/``values`` block per 16x16 cell that has
    anything in it, then the terminator.

    Sparse means sparse: a block for every cell (the empty ones a uniform ``mode 2``) gave the forest
    map 258 blocks where vanilla holds 29, and the game grew no tree at all (2026-09-24).
    """
    width, height = tag.find("x").u32(), tag.find("y").u32()
    blocks = tag.findall("block")
    header = blocks[0]
    bs = struct.unpack("<H", header.find("x").data)[0]
    new_blocks = [header]
    for by in range(0, height, bs):
        for bx in range(0, width, bs):
            values = [
                grid[by + j][bx + i] if by + j < height and bx + i < width else 0 for j in range(bs) for i in range(bs)
            ]
            if not any(values):
                continue
            block = filedb.STag("block")
            if bx:
                block.children.append(filedb.SAttrib("x", struct.pack("<H", bx)))
            if by:
                block.children.append(filedb.SAttrib("y", struct.pack("<H", by)))
            block.children.append(filedb.SAttrib("values", struct.pack(f"<{len(values)}{value_format}", *values)))
            new_blocks.append(block)
    new_blocks.append(filedb.STag("block", [filedb.SAttrib("mode", b"\x00")]))
    tag.children = [child for child in tag.children if child.name != "block"] + new_blocks


def fixed_pairs(nodes) -> list[tuple[float, float]]:
    """2070 polygon/line points: CDATA of int64 x, int64 z (fixed point)."""
    return [
        (struct.unpack_from("<q", node.data, 0)[0] / FIXED, struct.unpack_from("<q", node.data, 8)[0] / FIXED)
        for node in nodes
        if node.data
    ]


def edited_points(edit, least: int) -> list[tuple[float, float]] | None:
    """The (x, z) points of a line or outline edit (``{"points": [[x, z], ...]}``), None when it has fewer than ``least``
    or is no list of pairs: a hand-edited file must not stop a build."""
    try:
        points = [(float(point[0]), float(point[1])) for point in edit["points"]]
    except (TypeError, KeyError, IndexError, ValueError):
        return None
    return points if len(points) >= least else None


def nearest_segments(old: list, new: list) -> list[int]:
    """For each segment of the line ``new`` the index of the segment of the line ``old`` whose middle is nearest to its
    middle (a coast line with another number of points keeps the direction of the nearest old segment)."""
    midpoints = [
        ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2) for start, end in zip(old[:-1], old[1:], strict=False)
    ]
    out = []
    for start, end in zip(new[:-1], new[1:], strict=False):
        new_middle = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        out.append(
            min(
                range(len(midpoints)),
                key=lambda i: (midpoints[i][0] - new_middle[0]) ** 2 + (midpoints[i][1] - new_middle[1]) ** 2,
            )
        )
    return out


def turned_normals(old: list, old_normals: list, points: list) -> tuple[list[int], list[tuple[float, float]]]:
    """A coast line through new (x, z) ``points``: for each the index of the nearest point of the line ``old`` and the
    normal there, turned with the new line and on the side the old normal faced (a coast faces the sea). A point
    where the line has no direction keeps the old normal."""
    near, normals = [], []
    for k, (x, z) in enumerate(points):
        i = min(range(len(old)), key=lambda j: (old[j][0] - x) ** 2 + (old[j][1] - z) ** 2)
        ahead, behind = points[min(k + 1, len(points) - 1)], points[max(k - 1, 0)]
        normal_x, normal_z = ahead[1] - behind[1], behind[0] - ahead[0]  # the line's normal in x, z
        length = math.hypot(normal_x, normal_z)
        was = tuple(old_normals[i])
        near.append(i)
        if length < 1e-9:
            normals.append(was)
            continue
        normal_x, normal_z = normal_x / length, normal_z / length
        normals.append((normal_x, normal_z) if normal_x * was[0] + normal_z * was[1] >= 0 else (-normal_x, -normal_z))
    return near, normals


def dist_to_polygon(x: float, z: float, poly: list[tuple[float, float]]) -> float:
    best = float("inf")
    for k in range(len(poly)):
        (start_x, start_z), (end_x, end_z) = poly[k], poly[(k + 1) % len(poly)]
        vx, vz = end_x - start_x, end_z - start_z
        along = max(0.0, min(1.0, ((x - start_x) * vx + (z - start_z) * vz) / ((vx * vx + vz * vz) or 1.0)))
        best = min(best, math.hypot(x - start_x - along * vx, z - start_z - along * vz))
    return best


def border_connected(mask):
    """The cells of the boolean grid ``mask`` that lie in a region touching the grid's edge: the open sea of a water
    mask, where a lake or a pond does not count."""
    import numpy as np
    from scipy import ndimage

    lab, _ = ndimage.label(mask)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    return np.isin(lab, list(border))


def ground_at(heights, points_x, points_z):
    """A corner grid ``heights`` (rows z) at the points (points_x, points_z), bilinear between its corners, clamped to the grid."""
    import numpy as np

    rows, columns = heights.shape
    x, z = np.clip(points_x, 0, columns - 1.001), np.clip(points_z, 0, rows - 1.001)
    ix, iz = x.astype(int), z.astype(int)
    fx, fz = x - ix, z - iz
    top = heights[iz, ix] * (1 - fx) + heights[iz, ix + 1] * fx
    return top * (1 - fz) + (heights[iz + 1, ix] * (1 - fx) + heights[iz + 1, ix + 1] * fx) * fz


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
