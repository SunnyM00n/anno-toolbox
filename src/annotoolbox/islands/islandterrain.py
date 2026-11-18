"""The terrain of a built 117 island: heights, the coarse height map, the stored LOD meshes, the height
and tint pages and their deltas (island-terrain, "The holes in the ground")."""

from __future__ import annotations

import math
import struct
import zlib

from ..formats import a7mread, filedb
from . import seabed
from .islandgrid import AREA_ISLAND, BUILD_FLOOR, HEIGHT_UNITS, UNDERWATER_BUILD, read_bits, read_sparse, write_bits

# Levels below FINE_PAGE_LEVELS get a page only where the coarser page misses the ground by more than
# PAGE_ERROR heightmap units, as vanilla picks them (island-terrain, "The holes in the ground");
# HEIGHT_PAGE_FINEST (diagnostic) leaves every level below it to the coarser pages.
FINE_PAGE_LEVELS = 2
PAGE_ERROR = 0.0  # every node with any relief: the finest pages there are (user, 2026-10-16)
HEIGHT_PAGE_FINEST = 0
# On 2070's build ground of the island area (BUILD_FLOOR: where roads and land buildings stand) the pages are the
# smooth cubic held within PAGE_ROAD_TOLERANCE of the coarse map's bilinear surface, which roads and buildings are laid
# on; PAGE_FLAT_NEAR keeps the weight 1 to a held tile's edges, then it fades to the free cubic over PAGE_FLAT_FADE
# tiles. Held on the beach too, the slopes drew in half-tile stair steps (in game 2026-10-28; island-terrain "Pages").
PAGE_FLAT_NEAR = 1.0
PAGE_FLAT_FADE = 3.0
PAGE_ROAD_TOLERANCE = 0.03  # world units
LOD_MESH_CELLS = 64  # how many cells across a terrain node is drawn at the coarse LODs, at most
# (and never finer than the heightmap's half tile, so the stored meshes and the height pages show the
# same ground where they meet: island-terrain, "The holes in the ground"). The mesh was tied to the 8-tile active map cell, which gave a
# node at LOD 6 an 8x8 grid where a vanilla island draws 523 vertices over the same ground, and
# 846 over the whole island at LOD 8 against vanilla's 2,899. A sheet that coarse cuts the corner
# off every rise, and where the ground it stands for was higher the island opens up - which is why
# the holes grow as the camera pulls back and the coarser levels come in (user, 2026-09-24).

SKIRT_DROP = 0.64  # heightmap units a skirt hangs below a LOD 2 mesh's edge, doubling per level (vanilla's)


def with_skirts(rec, tris, keep, vid, level: int):
    """A terrain node mesh with vanilla's skirts: every vertex on the edge of the drawn cells again,
    ``SKIRT_DROP * 2^(level - 2)`` lower, and a strip down to it facing both ways. Without them a
    neighbour drawn at another level left a crack along the node edge: thin straight lines through
    the island (in game, 2026-10-15)."""
    import numpy as np

    rows, cols = keep.shape
    pad = np.zeros((rows + 2, cols + 2), bool)
    pad[1:-1, 1:-1] = keep
    qj, qi = np.nonzero(keep)
    edges = []  # (p, q) along the outline of the kept cells
    for dj, di, (p0, p1), (q0, q1) in (
        (-1, 0, (0, 0), (0, 1)),  # the cell above is not drawn: the top edge
        (1, 0, (1, 0), (1, 1)),
        (0, -1, (0, 0), (1, 0)),
        (0, 1, (0, 1), (1, 1)),
    ):
        open_ = ~pad[qj + 1 + dj, qi + 1 + di]
        sj, si = qj[open_], qi[open_]
        edges.append(np.stack([vid[sj + p0, si + p1], vid[sj + q0, si + q1]], 1))
    edges = np.concatenate(edges)
    if not len(edges):
        return rec, tris
    rim = np.unique(edges)
    low = np.empty(int(rim.max()) + 1, np.int64)
    low[rim] = len(rec) + np.arange(len(rim))
    skirt = rec[rim].copy()
    drop = int(round(SKIRT_DROP * 2 ** (level - 2) * 64))  # stored heights are 1/64 heightmap unit
    skirt["y"] = np.maximum(skirt["y"].astype(np.int64) - drop, 0)
    p, q = edges[:, 0], edges[:, 1]
    lp, lq = low[p], low[q]
    strip = np.concatenate(
        [np.stack([p, q, lq], 1), np.stack([p, lq, lp], 1), np.stack([q, p, lq], 1), np.stack([lq, p, lp], 1)]
    )
    return np.concatenate([rec, skirt]), np.concatenate([tris, strip.reshape(-1)])


TMC_VERTEX = [("x", "<u2"), ("y", "<u2"), ("z", "<u2"), ("u", "<u2"), ("n", "u1", 6), ("v", "<u2")]
# a .tmc vertex, 16 bytes: x and z in 1/32 tile, island coordinates in every .tmc tile (vanilla 0x1.tmc starts at
# z = 128 at LOD 2); height + 32768 in 1/64 unit; u, v the position inside the node (0..65535); normal bytes z, y, x
# (128 + 127 n), 0xff, 0x80, a tangent byte (the normal's x byte in vanilla data)


def _sample_half(arr, x, z):
    """A heightmap-resolution grid (two samples per tile) bilinear at tile coordinates (arrays)."""
    import numpy as np

    w = arr.shape[0]
    xs, zs = np.clip(x * 2, 0, w - 1.001), np.clip(z * 2, 0, w - 1.001)
    x0, z0 = xs.astype(int), zs.astype(int)
    tx, tz = xs - x0, zs - z0
    return (arr[z0, x0] * (1 - tx) + arr[z0, x0 + 1] * tx) * (1 - tz) + (
        arr[z0 + 1, x0] * (1 - tx) + arr[z0 + 1, x0 + 1] * tx
    ) * tz


def _node_cell(hm, active, acell: int, at: tuple[int, int], node: int, cell: float) -> float:
    """Tiles per mesh cell of one node: ``cell``, or half the node where the node is flat and wholly active (the sea
    bed's floor costs four cells, not thousands; island-maps "The sea bed")."""
    ni, nj = at
    a0, a1 = nj * node // acell, -(-(nj + 1) * node // acell)
    b0, b1 = ni * node // acell, -(-(ni + 1) * node // acell)
    if node / 2 <= cell or not active[a0:a1, b0:b1].all():
        return cell
    block = hm[nj * node * 2 : (nj + 1) * node * 2 + 1, ni * node * 2 : (ni + 1) * node * 2 + 1]
    return node / 2 if seabed.is_flat(block) else cell


def _node_mesh(grids, active, acell: int, at: tuple[int, int], node: int, cell: float, level: int):
    """One terrain node's stored mesh as (vertex bytes, index bytes), or None when no cell of it lies in the active
    map. ``grids``: the coarse heightmap and its gradients; ``at``: the node's (x, z) index at this level; ``node``
    tiles per node, ``cell`` tiles per mesh cell. Triangles are counter-clockwise seen from above."""
    import numpy as np

    hm, gx, gz = grids
    ni, nj = at
    cells = round(node / cell)
    ci = np.arange(cells)
    cx = (ni * node + (ci + 0.5) * cell) // acell
    cz = (nj * node + (ci + 0.5) * cell) // acell
    cx, cz = cx.astype(int), cz.astype(int)
    # [z, x]: the cells inside the active map; a node at the island's edge may reach past it (node_tiles)
    keep = active[np.ix_(np.minimum(cz, active.shape[0] - 1), np.minimum(cx, active.shape[1] - 1))]
    keep &= np.outer(cz < active.shape[0], cx < active.shape[1])
    if not keep.any():
        return None
    used = np.zeros((cells + 1, cells + 1), bool)
    for dj in (0, 1):
        for di in (0, 1):
            used[dj : cells + dj, di : cells + di] |= keep
    vid = np.full(used.shape, -1, np.int64)
    vid[used] = np.arange(int(used.sum()))
    vj, vi = np.nonzero(used)
    wx, wz = (ni * node + vi * cell).astype(float), (nj * node + vj * cell).astype(float)
    h = _sample_half(hm, wx, wz)
    norm = np.stack([-_sample_half(gx, wx, wz), np.ones_like(wx), -_sample_half(gz, wx, wz)], 1)
    norm /= np.linalg.norm(norm, axis=1, keepdims=True)
    nb = np.clip(np.rint(128 + 127 * norm), 0, 255).astype(np.uint8)
    rec = np.zeros(len(vi), dtype=TMC_VERTEX)
    rec["x"] = np.rint(wx * 32)
    rec["z"] = np.rint(wz * 32)
    rec["y"] = np.clip(np.rint(h * 64) + 32768, 0, 65535)
    rec["u"] = np.rint(vi / cells * 65535)
    rec["v"] = np.rint(vj / cells * 65535)
    rec["n"] = np.stack(
        [nb[:, 2], nb[:, 1], nb[:, 0], np.full(len(vi), 0xFF, np.uint8), np.full(len(vi), 0x80, np.uint8), nb[:, 0]], 1
    )
    qj, qi = np.nonzero(keep)
    a, b = vid[qj, qi], vid[qj, qi + 1]
    c, d = vid[qj + 1, qi], vid[qj + 1, qi + 1]
    # x right, z down the rows: (a, c, b) and (b, c, d) are counter-clockwise from above
    tris = np.stack([np.stack([a, c, b], 1), np.stack([b, c, d], 1)], 1).reshape(-1)
    rec, tris = with_skirts(rec, tris, keep, vid, level)
    return rec.tobytes(), tris.astype("<u2").tobytes()


PAGE_TAPS = (  # the game's 2x page upsampling (Catmull-Rom): fine 2X and 2X+1 from coarse X-2..X+2
    ((-2, -3 / 128), (-1, 29 / 128), (0, 111 / 128), (1, -9 / 128)),
    ((-1, -9 / 128), (0, 111 / 128), (1, 29 / 128), (2, -3 / 128)),
)


def page_up2(a):
    """A page level twice as fine, as the game rebuilds it (rendering/terrain-geometry.md 1.4): separable,
    mirrored at the edges."""
    import numpy as np

    for axis in (0, 1):
        a = np.moveaxis(a, axis, 0)
        n = len(a)
        x = np.arange(n)
        out = np.empty((2 * n,) + a.shape[1:])
        for parity, taps in enumerate(PAGE_TAPS):
            out[parity::2] = sum(w * a[_mirror(x + o, n)] for o, w in taps)
        a = np.moveaxis(out, 0, axis)
    return a


def _mirror(i, n: int):
    import numpy as np

    i = np.abs(i)
    return np.where(i >= n, 2 * (n - 1) - i, i)


def page_delta(delta, coarse, ground, node: float):
    """Fill one of a height page's refinement levels so the game's rebuild (``page_up2`` of the
    coarser level plus this delta) lands on ``ground(pos)`` (heightmap units at page positions in
    tiles) at its texel centres; returns the rebuilt level in world units."""
    import numpy as np

    data = delta.path("Data")
    width = data.find("width").u32()
    q = delta.find("QuantBits")
    bits = struct.unpack("<I", q.data)[0] if q is not None else 8
    scale = struct.unpack("<f", delta.find("ScaleFactor").data)[0]
    border = width // 66  # 2, 4, 8 texels: the Downsampled level's one, doubled per step
    pos = (np.arange(width) - border + 0.5) * node / (width - 2 * border)
    want = ground(pos) / HEIGHT_UNITS
    up = page_up2(coarse)
    half = 2 ** (bits - 1)
    step = 256 / (scale * half)  # world units per code (1/256 with vanilla's constants)
    codes = np.clip(np.rint((want - up) / step) + half, 0, 2 * half - 1)
    data.find("map").data = codes.astype(np.uint8).tobytes()
    return up + (codes - half) * step


def page_error(sample, nx: int, nz: int, node: float) -> float:
    """How far a page one level coarser (texels twice as wide, bilinear between their centres)
    misses the ground at this node's own texel centres and on its edges, where the stored mesh of
    the next node takes over; ``sample(x, z)`` in tile coordinates."""
    import numpy as np
    from scipy.ndimage import map_coordinates

    fine_t, coarse_t = node / 64, 2 * node / 64
    fine = np.concatenate([[0.0], (np.arange(64) + 0.5) * fine_t, [float(node)]])
    coarse = (np.arange(-1, 33) + 0.5) * coarse_t  # one texel beyond each side
    cv = sample(nx * node + coarse, nz * node + coarse)
    want = sample(nx * node + fine, nz * node + fine)
    idx = fine / coarse_t + 0.5  # fine centres in the coarse texels' index (which starts at -1)
    zz, xx = np.meshgrid(idx, idx, indexing="ij")
    return float(np.abs(map_coordinates(cv, [zz, xx], order=1) - want).max())


CUBIC_PAD = 12  # texels scipy adds round a grid before it prefilters it for mode "nearest" (_prepad_for_spline_filter)


def cubic_filter(grid):
    """``grid`` padded and prefiltered as ``map_coordinates(order=3, mode="nearest")`` does on every call, for a grid
    sampled many times (``page_ground_sampler``: about 1,500 calls an island); None where this scipy lacks the call
    that takes it, and every sample filters again."""
    import numpy as np
    from scipy import ndimage

    if not hasattr(ndimage, "_nd_image") or not hasattr(ndimage._nd_image, "geometric_transform"):
        return None
    padded = np.pad(np.asarray(grid), CUBIC_PAD, mode="edge")
    return ndimage.spline_filter(padded, 3, output=np.float64, mode="nearest")


def _cubic(grid, zz, xx, filtered):
    """``map_coordinates(grid, [zz, xx], order=3, mode="nearest")``, from ``cubic_filter(grid)`` when given: the same
    call scipy makes after filtering, so the same bytes."""
    import numpy as np
    from scipy import ndimage

    if filtered is None:
        return ndimage.map_coordinates(grid, [zz, xx], order=3, mode="nearest")
    coordinates = np.asarray([zz, xx])
    output = np.zeros(coordinates.shape[1:], dtype=np.asarray(grid).dtype.name)
    mode = ndimage._ni_support._extend_mode_to_code("nearest")
    ndimage._nd_image.geometric_transform(
        filtered, None, coordinates, None, None, output, 3, mode, 0.0, CUBIC_PAD, None, None
    )
    return output


def smooth_sample(grid, xs, zs, filtered=None):
    """Heights of ``grid`` (numpy, rows = z) at column positions ``xs`` and row positions ``zs``
    (outer product), cubic B-spline interpolation clamped to the four surrounding samples. Linear
    interpolation of 2070's one height per tile left slopes made of flat facets whose creases
    showed in-game as straight lines along the tile grid; the clamp keeps cliffs and coasts from
    overshooting. ``filtered``: ``cubic_filter(grid)``, made once for many calls."""
    import numpy as np

    zz, xx = np.meshgrid(zs, xs, indexing="ij")
    cubic = _cubic(grid, zz, xx, filtered)
    h, w = grid.shape
    z0 = np.clip(np.floor(zz).astype(int), 0, h - 2)
    x0 = np.clip(np.floor(xx).astype(int), 0, w - 2)
    quad = np.stack([grid[z0, x0], grid[z0, x0 + 1], grid[z0 + 1, x0], grid[z0 + 1, x0 + 1]])
    return np.clip(cubic, quad.min(axis=0), quad.max(axis=0))


def coarse_weight(area, near: float = PAGE_FLAT_NEAR, fade: float = PAGE_FLAT_FADE):
    """Per tile (centres), 1 on the boolean tile grid ``area`` and ``near`` tiles round it, then a smoothstep down to
    0 over ``fade`` tiles."""
    import numpy as np
    from scipy import ndimage

    area = np.asarray(area, bool)
    if not area.any():
        return np.zeros(area.shape)
    dist = ndimage.distance_transform_edt(~area)
    t = np.clip((dist - near) / fade, 0.0, 1.0)
    return 1.0 - t * t * (3.0 - 2.0 * t)


def node_tiles(size: int, n: int) -> int:
    """Tiles per node of a level of ``n`` nodes on a grid of ``size`` tiles: the level's own power of two. A grid that
    is not a multiple of it (320, 448, 768) ends in a node that reaches past the island."""
    node = 1
    while node * n < size:
        node *= 2
    return node


def node_flags(active_tiles, n: int, node: int):
    """Per node of an ``n`` x ``n`` level whether any of its tiles is active (``active_tiles``: one boolean per tile;
    the tiles past the island's edge are not)."""
    import numpy as np

    edge = n * node
    tiles = np.zeros((edge, edge), bool)
    tiles[: active_tiles.shape[0], : active_tiles.shape[1]] = active_tiles[:edge, :edge]
    return tiles.reshape(n, node, n, node).any(axis=(1, 3))


def page_ground_sampler(coarse, weight=None):
    """``sample(x, z)`` for the height pages: heights (heightmap units) at tile coordinates (outer product, rows z),
    the coarse heightmap ``coarse`` (two samples per tile) smoothed by a cubic, and held within ``PAGE_ROAD_TOLERANCE``
    of its plain bilinear surface, the one roads and buildings are laid on, where the tile grid ``weight``
    (``coarse_weight``) says 1."""
    import numpy as np
    from scipy.ndimage import map_coordinates

    w = coarse.shape[0]
    filtered = cubic_filter(coarse)  # the grid stays as it is: filtered once, not once a sample

    def sample(x, z):
        xs, zs = np.clip(np.asarray(x, float) * 2, 0, w - 1), np.clip(np.asarray(z, float) * 2, 0, w - 1)
        smooth = smooth_sample(coarse, xs, zs, filtered)
        if weight is None:
            return smooth
        zz, xx = np.meshgrid(zs, xs, indexing="ij")
        flat = map_coordinates(coarse, [zz, xx], order=1)
        tw = np.asarray(weight).shape[0] - 1  # tile centres: tile i at i + 0.5
        pz, px = np.meshgrid(np.clip(zs / 2 - 0.5, 0, tw), np.clip(xs / 2 - 0.5, 0, tw), indexing="ij")
        pull = map_coordinates(np.asarray(weight, float), [pz, px], order=1)
        tol = PAGE_ROAD_TOLERANCE * HEIGHT_UNITS
        held = flat + np.clip(smooth - flat, -tol, tol)
        return smooth + pull * (held - smooth)

    return sample


def set_i16(attr: filedb.Attrib, values: list[float], scale: float) -> None:
    raw = struct.pack(f"<{len(values)}h", *[max(-32768, min(32767, round(v * scale))) for v in values])
    if len(raw) != len(attr.data):
        raise ValueError(f"{attr.name}: {len(attr.data)} bytes in the donor, {len(raw)} generated")
    attr.data = raw


def bilinear(grid, gx, gz):
    """Sample a (rows=z, cols=x) numpy grid at x coordinates gx and z coordinates gz (outer
    product); coordinates are clamped to the grid."""
    import numpy as np

    h, w = grid.shape
    x = np.clip(gx, 0, w - 1.001)
    z = np.clip(gz, 0, h - 1.001)
    x0 = x.astype(int)
    z0 = z.astype(int)
    tx = (x - x0)[None, :]
    tz = (z - z0)[:, None]
    g00 = grid[np.ix_(z0, x0)]
    g01 = grid[np.ix_(z0, x0 + 1)]
    g10 = grid[np.ix_(z0 + 1, x0)]
    g11 = grid[np.ix_(z0 + 1, x0 + 1)]
    return (g00 * (1 - tx) + g01 * tx) * (1 - tz) + (g10 * (1 - tx) + g11 * tx) * tz


def replace_dds_pixels(dds: bytes, arr) -> bytes:
    """Same header (DX10, R16G16_UNORM), new 4-byte-per-texel pixels of the same size."""
    import numpy as np

    header = 148 if dds[84:88] == b"DX10" else 128
    raw = np.ascontiguousarray(arr, dtype="<u4").tobytes()
    if len(raw) != len(dds) - header:
        raise ValueError(f"DDS size mismatch: {len(dds) - header} bytes of pixels, {len(raw)} generated")
    return dds[:header] + raw


def set_bytes(attr: filedb.Attrib, arr) -> None:
    import numpy as np

    raw = np.ascontiguousarray(arr, dtype="<u4").tobytes()
    if len(raw) != len(attr.data):
        raise ValueError(f"{attr.name}: {len(attr.data)} bytes in the donor, {len(raw)} generated")
    attr.data = raw


def lod_grids(rd, size: int) -> tuple:
    """What a stored node mesh is made from: the coarse heightmap (heightmap units) with its gradients (world units a
    tile, as the stored normals are), the active map and its cell in tiles."""
    import numpy as np

    w, steps = a7mread.coarse_steps(rd)
    hm = np.frombuffer(steps, np.int16).reshape(w, w).astype(np.float64) / a7mread.HEIGHT_STEPS
    gz, gx = np.gradient(hm / HEIGHT_UNITS, 0.5)
    am = rd.path("Terrain/ActiveMap")
    return (hm, gx, gz), np.asarray(read_bits(am), dtype=bool), size // am.find("x").u32()


def node_mesh(grids: tuple, at: tuple[int, int], node: int, level: int):
    """One node's stored mesh from ``lod_grids``: a regular grid of half-tile cells up to ``LOD_MESH_CELLS`` across,
    the active cells only, with vanilla's skirts; (vertex bytes, index bytes), None outside the active map."""
    heights, active, acell = grids
    cell = _node_cell(heights[0], active, acell, at, node, max(0.5, node / LOD_MESH_CELLS))
    return _node_mesh(heights, active, acell, at, node, cell, level)


def fill_empty_meshes(rd, size: int, files: dict[str, bytes]) -> dict[str, int]:
    """The stored meshes of the nodes whose ``HasMeshArray`` bit is set but whose ``.tmc`` entry holds none, written
    in ``files`` as ``node_mesh`` makes them; returns the nodes filled per level. Anno 1800 keeps no mesh above level 8
    (a 384- or 768-tile island flags the top level all the same); 117 draws those nodes from afar (island-regions
    "The empty top level")."""
    grids, filled = None, {}
    for level, lv in enumerate(rd.path("Terrain/Levels").findall("#1")):
        if level < 2 or lv.path("HasMeshArray") is None:
            continue
        has = read_bits(lv.path("HasMeshArray"))
        node = node_tiles(size, len(has))
        for key in sorted(k for k in files if k.startswith(f"meshes/{level}/") and k.endswith(".tmc")):
            tx, tz = (int(v) for v in key.rsplit("/", 1)[1][:-4].split("x"))
            doc, made = filedb.read(files[key]), 0
            for k, e in enumerate(c for c in doc.path("Meshes/map").children if isinstance(c, filedb.Tag)):
                ni, nj = tx * 32 + k % 32, tz * 32 + k // 32
                verts, indices = e.find("Vertices"), e.find("Indices")
                if verts is None or indices is None or verts.data or nj >= len(has) or ni >= len(has[nj]):
                    continue
                if not has[nj][ni]:
                    continue
                grids = grids or lod_grids(rd, size)
                mesh = node_mesh(grids, (ni, nj), node, level)
                if mesh is not None:
                    verts.data, indices.data = mesh
                    made += 1
            if made:
                files[key] = filedb.write(doc, *filedb.name_ids(files[key]), version=filedb.version(files[key]))
                filled[level] = filled.get(level, 0) + made
    return filled


class TerrainMixin:
    """The methods of ``IslandBuilder`` that build the terrain."""

    # Heights
    def tile_height(self, x: int, z: int) -> float:
        h = self.heights
        return (h[z][x] + h[z][x + 1] + h[z + 1][x] + h[z + 1][x + 1]) / 4

    def build_ground(self):
        """The island area's tiles at 2070's build height (``BUILD_FLOOR``, under water ``UNDERWATER_BUILD``), where
        roads and land buildings stand: the ground the height pages hold to the coarse map (numpy, rows z)."""
        import numpy as np

        area = np.asarray(read_sparse(self.gm.path("AreaIDs")), np.int64)[: self.size, : self.size] == AREA_ISLAND
        h = np.asarray(self.heights, np.float64) / HEIGHT_UNITS
        mean = (h[:-1, :-1] + h[:-1, 1:] + h[1:, :-1] + h[1:, 1:])[: self.size, : self.size] / 4
        low, high = UNDERWATER_BUILD if self.isl.clime == 1 else (BUILD_FLOOR, np.inf)
        return area & (mean > low) & (mean <= high)

    def apply_heights(self) -> None:
        hmn = self.gm.path("TerrainManager/HeightMap")
        w = hmn.find("Width").u32()
        import numpy as np

        corners = np.asarray(self.heights, np.float64)
        c = np.arange(w) * (corners.shape[0] - 1) / (w - 1)
        grid = smooth_sample(corners, c, c)
        vals = grid.ravel().tolist()
        set_i16(hmn.find("HeightMap"), vals, 32)
        set_i16(self.rd.path("Terrain/CoarseHeightMap").find("map"), vals, 64)
        # the detail displacement layers on top of both heightmaps: neutral (the donor's detail
        # made buildings float or sink against the drawn terrain)
        disp = self.gm.path("TerrainManager/DisplacementHeightMap").find("HeightMap")
        disp.data = bytes(len(disp.data))
        cdisp = self.rd.path("Terrain/CoarseDisplacementMap").find("map")
        cdisp.data = bytes([128]) * len(cdisp.data)

    def _ground(self, x: float, z: float, radius: float = 0.0) -> float:
        """Lowest terrain (world units) within ``radius`` tiles of a point."""
        r = int(math.ceil(radius))
        best = None
        n = len(self.heights)
        for dz in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dz * dz > radius * radius + 0.5:
                    continue
                zi, xi = int(round(z)) + dz, int(round(x)) + dx
                if 0 <= zi < n and 0 <= xi < n:
                    v = self.heights[zi][xi]
                    best = v if best is None else min(best, v)
        return (best if best is not None else 0.0) / HEIGHT_UNITS

    def apply_levels(self) -> None:
        """Per-LOD terrain node data in ``rd3d`` ``Terrain/Levels``: MinY/MaxY per node (int16 in
        world units: coarse height / 256, floor/ceil) and HasMeshArray (nodes that get a
        terrain mesh: the active map at the level's resolution; stale bits leave holes where the
        water shows through)."""
        import numpy as np

        t = self.rd.path("Terrain")
        w, steps = a7mread.coarse_steps(self.rd)
        hm = np.frombuffer(steps, np.int16).reshape(w, w).astype(np.int32)
        am = t.find("ActiveMap")
        ax = am.find("x").u32()
        active = np.asarray(read_bits(am), dtype=bool)
        cell = self.size // ax
        active_tiles = np.repeat(np.repeat(active, cell, 0), cell, 1)  # [size, size]
        for lv in t.path("Levels").findall("#1"):
            mn, mx = lv.path("MinYArray"), lv.path("MaxYArray")
            n = mn.find("width").u32()
            s = (w - 1) // self.size * node_tiles(self.size, n)  # heightmap samples per node
            lo = np.empty((n, n), dtype=np.int16)
            hi = np.empty((n, n), dtype=np.int16)
            for j in range(n):
                rows = hm[j * s : (j + 1) * s + 1]
                for i in range(n):
                    block = rows[:, i * s : (i + 1) * s + 1]
                    # floor, not vanilla's trunc: under the sea a box truncated upwards clips the node seen from below
                    lo[j, i] = int(np.floor(block.min() / 256))
                    hi[j, i] = int(np.ceil(block.max() / 256))
            mn.find("map").data = lo.astype("<i2").tobytes()
            mx.find("map").data = hi.astype("<i2").tobytes()
            hb = lv.path("HasMeshArray")
            if hb is not None:
                hx = hb.find("x").u32()
                has = node_flags(active_tiles, hx, node_tiles(self.size, hx))
                write_bits(hb, has.astype(int).tolist())

    def generate_lod_meshes(self, gamedata_files: dict[str, bytes]) -> dict[str, bytes]:
        """Stored terrain meshes for the coarse LODs (island-terrain "The holes in the ground"). LOD 0 and 1 are
        built from the heightmap at runtime, LOD 2 and up are drawn from ``meshes/<lod>/<x>x<z>.tmc`` for every node
        whose ``HasMeshArray`` bit is set (a set bit without a mesh draws nothing); each .tmc tile holds 32x32 nodes.
        Vanilla meshes cover only the active area; a mesh over the full node draws the sea as a flat terrain sheet.
        Here: a regular grid per node (half-tile cells up to ``LOD_MESH_CELLS`` across), only the cells inside the
        active map, with vanilla's skirts. Returns the new .tmc files."""
        t = self.rd.path("Terrain")
        grids = lod_grids(self.rd, self.size)
        out: dict[str, bytes] = {}
        counts: dict[int, int] = {}
        for level, lv in enumerate(t.path("Levels").findall("#1")):
            if level < 2:
                continue
            has = read_bits(lv.path("HasMeshArray"))
            n = len(has)
            node = node_tiles(self.size, n)  # tiles per node
            docs = {
                k: (filedb.read(d), filedb.name_ids(d))
                for k, d in gamedata_files.items()
                if k.startswith(f"meshes/{level}/") and k.endswith(".tmc")
            }
            made = 0
            for key, (doc, _) in docs.items():
                tx, tz = [int(v) for v in key.rsplit("/", 1)[1][:-4].split("x")]
                ents = [c for c in doc.path("Meshes/map").children if isinstance(c, filedb.Tag)]
                for k, e in enumerate(ents):
                    ni, nj = tx * 32 + k % 32, tz * 32 + k // 32
                    verts = indices = b""
                    if ni < n and nj < n and has[nj][ni]:
                        mesh = node_mesh(grids, (ni, nj), node, level)
                        if mesh is not None:
                            verts, indices = mesh
                            made += 1
                    e.find("Vertices").data = verts
                    e.find("Indices").data = indices
            for key, (doc, ids) in docs.items():
                out[key] = filedb.write(doc, *ids)
            counts[level] = made
        self.report["lod_meshes"] = counts
        return out

    def _page_nodes(self, levels_key: str):
        """(level, flags node, node grid size, tiles per node, active nodes) for the per-level
        page flags under ``Terrain/<levels_key>/Levels``."""
        import numpy as np

        am = self.rd.path("Terrain/ActiveMap")
        active = np.asarray(read_bits(am), dtype=bool)
        cell = self.size // active.shape[0]
        active_tiles = np.repeat(np.repeat(active, cell, 0), cell, 1)
        for level, lv in enumerate(self.rd.path(f"Terrain/{levels_key}/Levels").findall("#1")):
            fl = lv.find("Flags")
            n = fl.find("width").u32()
            node = node_tiles(self.size, n)
            nodes = node_flags(active_tiles, n, node)
            yield level, fl, n, node, nodes

    @staticmethod
    def _set_flags(fl: filedb.Tag, nodes) -> None:
        n = nodes.shape[0]
        m = bytearray(fl.find("map").data)
        m[: n * n] = bytes(nodes.astype("u1").ravel())
        fl.find("map").data = bytes(m)

    def generate_height_pages(self, gamedata_files: dict[str, bytes]) -> dict[str, bytes]:
        """The render heightmaps, ``normalmaps/<level>/<x>x<z>.ctt`` (``_CTT`` + u32 size + zlib FileDB;
        the format). Per page the 66x66 ``Downsampled`` level
        is sampled from the coarse map (``page_ground_sampler``) and each ``Deltas`` level filled so the
        game's rebuild lands on the ground (``page_delta``); levels from ``FINE_PAGE_LEVELS`` up cover
        every active node, the finer ones every node whose coarser page misses the ground by more than
        ``PAGE_ERROR`` (island-terrain "The holes in the ground"). The level flags
        (``AllNormalMapLevels/Levels/*/Flags``) are set to match. Returns the new page files; the
        donor's pages are removed from ``gamedata_files``."""
        import numpy as np

        template_key = next(k for k in sorted(gamedata_files) if k.startswith("normalmaps/") and k.endswith(".ctt"))
        template = gamedata_files[template_key]
        template_raw = zlib.decompress(template[8:])
        template_ids = filedb.name_ids(template_raw)
        w, steps = a7mread.coarse_steps(self.rd)
        hm = np.frombuffer(steps, np.int16).reshape(w, w).astype(np.float64) / a7mread.HEIGHT_STEPS

        from .terrainbuild import encode_page  # the one page encoder (also the editor's)

        sample = page_ground_sampler(hm, coarse_weight(self.build_ground()))

        for k in [k for k in gamedata_files if k.startswith("normalmaps/")]:
            del gamedata_files[k]
        out: dict[str, bytes] = {}
        counts = {}
        levels = list(self._page_nodes("AllNormalMapLevels"))
        chosen = {}
        for level, _fl, _n, node, nodes in reversed(levels):  # coarse first: a fine page needs its parent
            if level < HEIGHT_PAGE_FINEST:  # diagnostic: leave the fine levels to the coarser pages
                nodes = np.zeros_like(nodes)
            elif level < FINE_PAGE_LEVELS:
                parent = chosen.get(level + 1)
                nodes = nodes.copy()
                for nz, nx in zip(*np.nonzero(nodes), strict=False):
                    has_parent = parent is None or parent[nz // 2, nx // 2]
                    nodes[nz, nx] = has_parent and page_error(sample, nx, nz, node) > PAGE_ERROR
            chosen[level] = nodes
        for level, fl, _n, node, _active in levels:
            nodes = chosen[level]
            for nz, nx in zip(*np.nonzero(nodes), strict=False):
                doc = filedb.read(template_raw)
                encode_page(doc, sample, nx * node, nz * node, node)
                raw = filedb.write(doc, *template_ids)
                out[f"normalmaps/{level}/{nx}x{nz}.ctt"] = (
                    template[:4] + struct.pack("<I", len(raw)) + zlib.compress(raw, 1)
                )
            self._set_flags(fl, nodes)
            counts[level] = int(nodes.sum())
        self.report["height_pages"] = counts
        return out

    def generate_tint_pages(self, gamedata_files: dict[str, bytes]) -> dict[str, bytes]:
        """Neutral tint pages (``tintmaps/<level>/<x>x<z>.dds``, DXT1, grey 128: vanilla tints
        average 125-132) for every node touching the active area, and a neutral
        ``TintMapLevels/BaseTextureData`` (the donor's drew its coast and erosion lines over the
        new terrain). The donor's pages are removed from ``gamedata_files``."""
        import numpy as np

        from .tintpages import neutral

        template_key = next(k for k in sorted(gamedata_files) if k.startswith("tintmaps/") and k.endswith(".dds"))
        page = neutral(gamedata_files[template_key])
        for k in [k for k in gamedata_files if k.startswith("tintmaps/")]:
            del gamedata_files[k]
        out: dict[str, bytes] = {}
        counts = {}
        for level, fl, _n, _node, nodes in self._page_nodes("TintMapLevels"):
            for nz, nx in zip(*np.nonzero(nodes), strict=False):
                out[f"tintmaps/{level}/{nx}x{nz}.dds"] = page
            self._set_flags(fl, nodes)
            counts[level] = int(nodes.sum())
        base = self.rd.path("Terrain/TintMapLevels/BaseTextureData")
        if base is not None:
            m = base.find("map")
            m.data = neutral(m.data)
        self.report["tint_pages"] = counts
        return out
