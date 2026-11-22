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


def with_skirts(vertex_records, triangles, keep, vid, level: int):
    """A terrain node mesh with vanilla's skirts: every vertex on the edge of the drawn cells again,
    ``SKIRT_DROP * 2^(level - 2)`` lower, and a strip down to it facing both ways. Without them a
    neighbour drawn at another level left a crack along the node edge: thin straight lines through
    the island (in game, 2026-10-15)."""
    import numpy as np

    rows, columns = keep.shape
    pad = np.zeros((rows + 2, columns + 2), bool)
    pad[1:-1, 1:-1] = keep
    cell_row, cell_column = np.nonzero(keep)
    edges = []  # (p, q) along the outline of the kept cells
    for row_step, column_step, (start_row, start_column), (end_row, end_column) in (
        (-1, 0, (0, 0), (0, 1)),  # the cell above is not drawn: the top edge
        (1, 0, (1, 0), (1, 1)),
        (0, -1, (0, 0), (1, 0)),
        (0, 1, (0, 1), (1, 1)),
    ):
        open_ = ~pad[cell_row + 1 + row_step, cell_column + 1 + column_step]
        open_row, open_column = cell_row[open_], cell_column[open_]
        edges.append(
            np.stack(
                [
                    vid[open_row + start_row, open_column + start_column],
                    vid[open_row + end_row, open_column + end_column],
                ],
                1,
            )
        )
    edges = np.concatenate(edges)
    if not len(edges):
        return vertex_records, triangles
    rim = np.unique(edges)
    low = np.empty(int(rim.max()) + 1, np.int64)
    low[rim] = len(vertex_records) + np.arange(len(rim))
    skirt = vertex_records[rim].copy()
    drop = int(round(SKIRT_DROP * 2 ** (level - 2) * 64))  # stored heights are 1/64 heightmap unit
    skirt["y"] = np.maximum(skirt["y"].astype(np.int64) - drop, 0)
    start_vertex, end_vertex = edges[:, 0], edges[:, 1]
    low_start, low_end = low[start_vertex], low[end_vertex]
    strip = np.concatenate(
        [
            np.stack([start_vertex, end_vertex, low_end], 1),
            np.stack([start_vertex, low_end, low_start], 1),
            np.stack([end_vertex, start_vertex, low_end], 1),
            np.stack([low_end, start_vertex, low_start], 1),
        ]
    )
    return np.concatenate([vertex_records, skirt]), np.concatenate([triangles, strip.reshape(-1)])


TMC_VERTEX = [("x", "<u2"), ("y", "<u2"), ("z", "<u2"), ("u", "<u2"), ("n", "u1", 6), ("v", "<u2")]
# a .tmc vertex, 16 bytes: x and z in 1/32 tile, island coordinates in every .tmc tile (vanilla 0x1.tmc starts at
# z = 128 at LOD 2); height + 32768 in 1/64 unit; u, v the position inside the node (0..65535); normal bytes z, y, x
# (128 + 127 n), 0xff, 0x80, a tangent byte (the normal's x byte in vanilla data)


def _sample_half(grid, x, z):
    """A heightmap-resolution grid (two samples per tile) bilinear at tile coordinates (arrays)."""
    import numpy as np

    sample_count = grid.shape[0]
    sample_x, sample_z = np.clip(x * 2, 0, sample_count - 1.001), np.clip(z * 2, 0, sample_count - 1.001)
    cell_x, cell_z = sample_x.astype(int), sample_z.astype(int)
    fraction_x, fraction_z = sample_x - cell_x, sample_z - cell_z
    return (grid[cell_z, cell_x] * (1 - fraction_x) + grid[cell_z, cell_x + 1] * fraction_x) * (1 - fraction_z) + (
        grid[cell_z + 1, cell_x] * (1 - fraction_x) + grid[cell_z + 1, cell_x + 1] * fraction_x
    ) * fraction_z


def _node_cell(
    heightmap, active, active_cell_tiles: int, node_index: tuple[int, int], tiles_per_node: int, cell_tiles: float
) -> float:
    """Tiles per mesh cell of one node: ``cell``, or half the node where the node is flat and wholly active (the sea
    bed's floor costs four cells, not thousands; island-maps "The sea bed")."""
    node_x, node_z = node_index
    row_start, row_end = (
        node_z * tiles_per_node // active_cell_tiles,
        -(-(node_z + 1) * tiles_per_node // active_cell_tiles),
    )
    column_start, column_end = (
        node_x * tiles_per_node // active_cell_tiles,
        -(-(node_x + 1) * tiles_per_node // active_cell_tiles),
    )
    if tiles_per_node / 2 <= cell_tiles or not active[row_start:row_end, column_start:column_end].all():
        return cell_tiles
    block = heightmap[
        node_z * tiles_per_node * 2 : (node_z + 1) * tiles_per_node * 2 + 1,
        node_x * tiles_per_node * 2 : (node_x + 1) * tiles_per_node * 2 + 1,
    ]
    return tiles_per_node / 2 if seabed.is_flat(block) else cell_tiles


def _node_mesh(
    grids,
    active,
    active_cell_tiles: int,
    node_index: tuple[int, int],
    tiles_per_node: int,
    cell_tiles: float,
    level: int,
):
    """One terrain node's stored mesh as (vertex bytes, index bytes), or None when no cell of it lies in the active
    map. ``grids``: the coarse heightmap and its gradients; ``at``: the node's (x, z) index at this level; ``node``
    tiles per node, ``cell`` tiles per mesh cell. Triangles are counter-clockwise seen from above."""
    import numpy as np

    heightmap, gradient_x, gradient_z = grids
    node_x, node_z = node_index
    cells = round(tiles_per_node / cell_tiles)
    cell_indices = np.arange(cells)
    active_x = (node_x * tiles_per_node + (cell_indices + 0.5) * cell_tiles) // active_cell_tiles
    active_z = (node_z * tiles_per_node + (cell_indices + 0.5) * cell_tiles) // active_cell_tiles
    active_x, active_z = active_x.astype(int), active_z.astype(int)
    # [z, x]: the cells inside the active map; a node at the island's edge may reach past it (node_tiles)
    keep = active[np.ix_(np.minimum(active_z, active.shape[0] - 1), np.minimum(active_x, active.shape[1] - 1))]
    keep &= np.outer(active_z < active.shape[0], active_x < active.shape[1])
    if not keep.any():
        return None
    used = np.zeros((cells + 1, cells + 1), bool)
    for row_step in (0, 1):
        for column_step in (0, 1):
            used[row_step : cells + row_step, column_step : cells + column_step] |= keep
    vid = np.full(used.shape, -1, np.int64)
    vid[used] = np.arange(int(used.sum()))
    vertex_row, vertex_column = np.nonzero(used)
    world_x, world_z = (
        (node_x * tiles_per_node + vertex_column * cell_tiles).astype(float),
        (node_z * tiles_per_node + vertex_row * cell_tiles).astype(float),
    )
    heights = _sample_half(heightmap, world_x, world_z)
    normals = np.stack(
        [
            -_sample_half(gradient_x, world_x, world_z),
            np.ones_like(world_x),
            -_sample_half(gradient_z, world_x, world_z),
        ],
        1,
    )
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    normal_bytes = np.clip(np.rint(128 + 127 * normals), 0, 255).astype(np.uint8)
    vertex_records = np.zeros(len(vertex_column), dtype=TMC_VERTEX)
    vertex_records["x"] = np.rint(world_x * 32)
    vertex_records["z"] = np.rint(world_z * 32)
    vertex_records["y"] = np.clip(np.rint(heights * 64) + 32768, 0, 65535)
    vertex_records["u"] = np.rint(vertex_column / cells * 65535)
    vertex_records["v"] = np.rint(vertex_row / cells * 65535)
    vertex_records["n"] = np.stack(
        [
            normal_bytes[:, 2],
            normal_bytes[:, 1],
            normal_bytes[:, 0],
            np.full(len(vertex_column), 0xFF, np.uint8),
            np.full(len(vertex_column), 0x80, np.uint8),
            normal_bytes[:, 0],
        ],
        1,
    )
    cell_row, cell_column = np.nonzero(keep)
    top_left, top_right = vid[cell_row, cell_column], vid[cell_row, cell_column + 1]
    bottom_left, bottom_right = vid[cell_row + 1, cell_column], vid[cell_row + 1, cell_column + 1]
    # x right, z down the rows: (a, c, b) and (b, c, d) are counter-clockwise from above
    triangles = np.stack(
        [np.stack([top_left, bottom_left, top_right], 1), np.stack([top_right, bottom_left, bottom_right], 1)], 1
    ).reshape(-1)
    vertex_records, triangles = with_skirts(vertex_records, triangles, keep, vid, level)
    return vertex_records.tobytes(), triangles.astype("<u2").tobytes()


PAGE_TAPS = (  # the game's 2x page upsampling (Catmull-Rom): fine 2X and 2X+1 from coarse X-2..X+2
    ((-2, -3 / 128), (-1, 29 / 128), (0, 111 / 128), (1, -9 / 128)),
    ((-1, -9 / 128), (0, 111 / 128), (1, 29 / 128), (2, -3 / 128)),
)


def page_up2(page):
    """A page level twice as fine, as the game rebuilds it (rendering/terrain-geometry.md 1.4): separable,
    mirrored at the edges."""
    import numpy as np

    for axis in (0, 1):
        page = np.moveaxis(page, axis, 0)
        texel_count = len(page)
        texels = np.arange(texel_count)
        out = np.empty((2 * texel_count,) + page.shape[1:])
        for parity, taps in enumerate(PAGE_TAPS):
            out[parity::2] = sum(
                weight * page[_mirror(texels + tap_offset, texel_count)] for tap_offset, weight in taps
            )
        page = np.moveaxis(out, 0, axis)
    return page


def _mirror(indices, texel_count: int):
    import numpy as np

    indices = np.abs(indices)
    return np.where(indices >= texel_count, 2 * (texel_count - 1) - indices, indices)


def page_delta(delta, coarse, ground, tiles_per_node: float):
    """Fill one of a height page's refinement levels so the game's rebuild (``page_up2`` of the
    coarser level plus this delta) lands on ``ground(pos)`` (heightmap units at page positions in
    tiles) at its texel centres; returns the rebuilt level in world units."""
    import numpy as np

    data = delta.path("Data")
    width_texels = data.find("width").u32()
    quant_bits_tag = delta.find("QuantBits")
    bits = struct.unpack("<I", quant_bits_tag.data)[0] if quant_bits_tag is not None else 8
    scale = struct.unpack("<f", delta.find("ScaleFactor").data)[0]
    border = width_texels // 66  # 2, 4, 8 texels: the Downsampled level's one, doubled per step
    positions = (np.arange(width_texels) - border + 0.5) * tiles_per_node / (width_texels - 2 * border)
    want = ground(positions) / HEIGHT_UNITS
    upsampled = page_up2(coarse)
    half = 2 ** (bits - 1)
    step = 256 / (scale * half)  # world units per code (1/256 with vanilla's constants)
    codes = np.clip(np.rint((want - upsampled) / step) + half, 0, 2 * half - 1)
    data.find("map").data = codes.astype(np.uint8).tobytes()
    return upsampled + (codes - half) * step


def page_error(sample, node_x: int, node_z: int, tiles_per_node: float) -> float:
    """How far a page one level coarser (texels twice as wide, bilinear between their centres)
    misses the ground at this node's own texel centres and on its edges, where the stored mesh of
    the next node takes over; ``sample(x, z)`` in tile coordinates."""
    import numpy as np
    from scipy.ndimage import map_coordinates

    fine_texel_tiles, coarse_texel_tiles = tiles_per_node / 64, 2 * tiles_per_node / 64
    fine = np.concatenate([[0.0], (np.arange(64) + 0.5) * fine_texel_tiles, [float(tiles_per_node)]])
    coarse = (np.arange(-1, 33) + 0.5) * coarse_texel_tiles  # one texel beyond each side
    coarse_values = sample(node_x * tiles_per_node + coarse, node_z * tiles_per_node + coarse)
    want = sample(node_x * tiles_per_node + fine, node_z * tiles_per_node + fine)
    fine_in_coarse = fine / coarse_texel_tiles + 0.5  # fine centres in the coarse texels' index (which starts at -1)
    index_z, index_x = np.meshgrid(fine_in_coarse, fine_in_coarse, indexing="ij")
    return float(np.abs(map_coordinates(coarse_values, [index_z, index_x], order=1) - want).max())


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


def _cubic(grid, grid_z, grid_x, filtered):
    """``map_coordinates(grid, [zz, xx], order=3, mode="nearest")``, from ``cubic_filter(grid)`` when given: the same
    call scipy makes after filtering, so the same bytes."""
    import numpy as np
    from scipy import ndimage

    if filtered is None:
        return ndimage.map_coordinates(grid, [grid_z, grid_x], order=3, mode="nearest")
    coordinates = np.asarray([grid_z, grid_x])
    output = np.zeros(coordinates.shape[1:], dtype=np.asarray(grid).dtype.name)
    mode = ndimage._ni_support._extend_mode_to_code("nearest")
    ndimage._nd_image.geometric_transform(
        filtered, None, coordinates, None, None, output, 3, mode, 0.0, CUBIC_PAD, None, None
    )
    return output


def smooth_sample(grid, column_positions, row_positions, filtered=None):
    """Heights of ``grid`` (numpy, rows = z) at column positions ``xs`` and row positions ``zs``
    (outer product), cubic B-spline interpolation clamped to the four surrounding samples. Linear
    interpolation of 2070's one height per tile left slopes made of flat facets whose creases
    showed in-game as straight lines along the tile grid; the clamp keeps cliffs and coasts from
    overshooting. ``filtered``: ``cubic_filter(grid)``, made once for many calls."""
    import numpy as np

    grid_z, grid_x = np.meshgrid(row_positions, column_positions, indexing="ij")
    cubic = _cubic(grid, grid_z, grid_x, filtered)
    row_count, column_count = grid.shape
    row_floor = np.clip(np.floor(grid_z).astype(int), 0, row_count - 2)
    column_floor = np.clip(np.floor(grid_x).astype(int), 0, column_count - 2)
    quad = np.stack(
        [
            grid[row_floor, column_floor],
            grid[row_floor, column_floor + 1],
            grid[row_floor + 1, column_floor],
            grid[row_floor + 1, column_floor + 1],
        ]
    )
    return np.clip(cubic, quad.min(axis=0), quad.max(axis=0))


def coarse_weight(area, near_tiles: float = PAGE_FLAT_NEAR, fade_tiles: float = PAGE_FLAT_FADE):
    """Per tile (centres), 1 on the boolean tile grid ``area`` and ``near`` tiles round it, then a smoothstep down to
    0 over ``fade`` tiles."""
    import numpy as np
    from scipy import ndimage

    area = np.asarray(area, bool)
    if not area.any():
        return np.zeros(area.shape)
    distance_tiles = ndimage.distance_transform_edt(~area)
    blend = np.clip((distance_tiles - near_tiles) / fade_tiles, 0.0, 1.0)
    return 1.0 - blend * blend * (3.0 - 2.0 * blend)


def node_tiles(grid_tiles: int, node_count: int) -> int:
    """Tiles per node of a level of ``n`` nodes on a grid of ``size`` tiles: the level's own power of two. A grid that
    is not a multiple of it (320, 448, 768) ends in a node that reaches past the island."""
    tiles_per_node = 1
    while tiles_per_node * node_count < grid_tiles:
        tiles_per_node *= 2
    return tiles_per_node


def node_flags(active_tiles, node_count: int, tiles_per_node: int):
    """Per node of an ``n`` x ``n`` level whether any of its tiles is active (``active_tiles``: one boolean per tile;
    the tiles past the island's edge are not)."""
    import numpy as np

    edge = node_count * tiles_per_node
    tiles = np.zeros((edge, edge), bool)
    tiles[: active_tiles.shape[0], : active_tiles.shape[1]] = active_tiles[:edge, :edge]
    return tiles.reshape(node_count, tiles_per_node, node_count, tiles_per_node).any(axis=(1, 3))


def page_ground_sampler(coarse, weight=None):
    """``sample(x, z)`` for the height pages: heights (heightmap units) at tile coordinates (outer product, rows z),
    the coarse heightmap ``coarse`` (two samples per tile) smoothed by a cubic, and held within ``PAGE_ROAD_TOLERANCE``
    of its plain bilinear surface, the one roads and buildings are laid on, where the tile grid ``weight``
    (``coarse_weight``) says 1."""
    import numpy as np
    from scipy.ndimage import map_coordinates

    sample_count = coarse.shape[0]
    filtered = cubic_filter(coarse)  # the grid stays as it is: filtered once, not once a sample

    def sample(x, z):
        sample_x, sample_z = (
            np.clip(np.asarray(x, float) * 2, 0, sample_count - 1),
            np.clip(np.asarray(z, float) * 2, 0, sample_count - 1),
        )
        smooth = smooth_sample(coarse, sample_x, sample_z, filtered)
        if weight is None:
            return smooth
        grid_z, grid_x = np.meshgrid(sample_z, sample_x, indexing="ij")
        flat = map_coordinates(coarse, [grid_z, grid_x], order=1)
        last_tile = np.asarray(weight).shape[0] - 1  # tile centres: tile i at i + 0.5
        weight_z, weight_x = np.meshgrid(
            np.clip(sample_z / 2 - 0.5, 0, last_tile), np.clip(sample_x / 2 - 0.5, 0, last_tile), indexing="ij"
        )
        pull = map_coordinates(np.asarray(weight, float), [weight_z, weight_x], order=1)
        tolerance = PAGE_ROAD_TOLERANCE * HEIGHT_UNITS
        held = flat + np.clip(smooth - flat, -tolerance, tolerance)
        return smooth + pull * (held - smooth)

    return sample


def set_i16(attribute: filedb.SAttrib, values: list[float], scale: float) -> None:
    raw = struct.pack(f"<{len(values)}h", *[max(-32768, min(32767, round(value * scale))) for value in values])
    if len(raw) != len(attribute.data):
        raise ValueError(f"{attribute.name}: {len(attribute.data)} bytes in the donor, {len(raw)} generated")
    attribute.data = raw


def bilinear(grid, x_coordinates, z_coordinates):
    """Sample a (rows=z, cols=x) numpy grid at x coordinates gx and z coordinates gz (outer
    product); coordinates are clamped to the grid."""
    import numpy as np

    row_count, column_count = grid.shape
    x = np.clip(x_coordinates, 0, column_count - 1.001)
    z = np.clip(z_coordinates, 0, row_count - 1.001)
    column_floor = x.astype(int)
    row_floor = z.astype(int)
    fraction_x = (x - column_floor)[None, :]
    fraction_z = (z - row_floor)[:, None]
    g00 = grid[np.ix_(row_floor, column_floor)]
    g01 = grid[np.ix_(row_floor, column_floor + 1)]
    g10 = grid[np.ix_(row_floor + 1, column_floor)]
    g11 = grid[np.ix_(row_floor + 1, column_floor + 1)]
    return (g00 * (1 - fraction_x) + g01 * fraction_x) * (1 - fraction_z) + (
        g10 * (1 - fraction_x) + g11 * fraction_x
    ) * fraction_z


def replace_dds_pixels(dds: bytes, pixels) -> bytes:
    """Same header (DX10, R16G16_UNORM), new 4-byte-per-texel pixels of the same size."""
    import numpy as np

    header = 148 if dds[84:88] == b"DX10" else 128
    raw = np.ascontiguousarray(pixels, dtype="<u4").tobytes()
    if len(raw) != len(dds) - header:
        raise ValueError(f"DDS size mismatch: {len(dds) - header} bytes of pixels, {len(raw)} generated")
    return dds[:header] + raw


def set_bytes(attribute: filedb.SAttrib, pixels) -> None:
    import numpy as np

    raw = np.ascontiguousarray(pixels, dtype="<u4").tobytes()
    if len(raw) != len(attribute.data):
        raise ValueError(f"{attribute.name}: {len(attribute.data)} bytes in the donor, {len(raw)} generated")
    attribute.data = raw


def lod_grids(rd3d, grid_tiles: int) -> tuple:
    """What a stored node mesh is made from: the coarse heightmap (heightmap units) with its gradients (world units a
    tile, as the stored normals are), the active map and its cell in tiles."""
    import numpy as np

    heightmap_width, steps = a7mread.coarse_steps(rd3d)
    heightmap = (
        np.frombuffer(steps, np.int16).reshape(heightmap_width, heightmap_width).astype(np.float64)
        / a7mread.HEIGHT_STEPS
    )
    gradient_z, gradient_x = np.gradient(heightmap / HEIGHT_UNITS, 0.5)
    active_map = rd3d.path("Terrain/ActiveMap")
    return (
        (heightmap, gradient_x, gradient_z),
        np.asarray(read_bits(active_map), dtype=bool),
        grid_tiles // active_map.find("x").u32(),
    )


def node_mesh(grids: tuple, node_index: tuple[int, int], tiles_per_node: int, level: int):
    """One node's stored mesh from ``lod_grids``: a regular grid of half-tile cells up to ``LOD_MESH_CELLS`` across,
    the active cells only, with vanilla's skirts; (vertex bytes, index bytes), None outside the active map."""
    heights, active, active_cell_tiles = grids
    cell_tiles = _node_cell(
        heights[0], active, active_cell_tiles, node_index, tiles_per_node, max(0.5, tiles_per_node / LOD_MESH_CELLS)
    )
    return _node_mesh(heights, active, active_cell_tiles, node_index, tiles_per_node, cell_tiles, level)


def fill_empty_meshes(rd3d, grid_tiles: int, files: dict[str, bytes]) -> dict[str, int]:
    """The stored meshes of the nodes whose ``HasMeshArray`` bit is set but whose ``.tmc`` entry holds none, written
    in ``files`` as ``node_mesh`` makes them; returns the nodes filled per level. Anno 1800 keeps no mesh above level 8
    (a 384- or 768-tile island flags the top level all the same); 117 draws those nodes from afar (island-regions
    "The empty top level")."""
    grids, filled = None, {}
    for level, level_tag in enumerate(rd3d.path("Terrain/Levels").findall("#1")):
        if level < 2 or level_tag.path("HasMeshArray") is None:
            continue
        has = read_bits(level_tag.path("HasMeshArray"))
        tiles_per_node = node_tiles(grid_tiles, len(has))
        for key in sorted(k for k in files if k.startswith(f"meshes/{level}/") and k.endswith(".tmc")):
            tile_x, tile_z = (int(number) for number in key.rsplit("/", 1)[1][:-4].split("x"))
            doc, made = filedb.read(files[key]), 0
            for k, mesh_entry in enumerate(
                child for child in doc.path("Meshes/map").children if isinstance(child, filedb.STag)
            ):
                node_x, node_z = tile_x * 32 + k % 32, tile_z * 32 + k // 32
                vertices, indices = mesh_entry.find("Vertices"), mesh_entry.find("Indices")
                if (
                    vertices is None
                    or indices is None
                    or vertices.data
                    or node_z >= len(has)
                    or node_x >= len(has[node_z])
                ):
                    continue
                if not has[node_z][node_x]:
                    continue
                grids = grids or lod_grids(rd3d, grid_tiles)
                mesh = node_mesh(grids, (node_x, node_z), tiles_per_node, level)
                if mesh is not None:
                    vertices.data, indices.data = mesh
                    made += 1
            if made:
                files[key] = filedb.write(doc, *filedb.name_ids(files[key]), version=filedb.version(files[key]))
                filled[level] = filled.get(level, 0) + made
    return filled


class CTerrainMixin:
    """The methods of ``CIslandBuilder`` that build the terrain."""

    # Heights
    def tile_height(self, x: int, z: int) -> float:
        heights = self.heights
        return (heights[z][x] + heights[z][x + 1] + heights[z + 1][x] + heights[z + 1][x + 1]) / 4

    def build_ground(self):
        """The island area's tiles at 2070's build height (``BUILD_FLOOR``, under water ``UNDERWATER_BUILD``), where
        roads and land buildings stand: the ground the height pages hold to the coarse map (numpy, rows z)."""
        import numpy as np

        area = (
            np.asarray(read_sparse(self.game_session_manager.path("AreaIDs")), np.int64)[: self.size, : self.size]
            == AREA_ISLAND
        )
        heights = np.asarray(self.heights, np.float64) / HEIGHT_UNITS
        mean = (heights[:-1, :-1] + heights[:-1, 1:] + heights[1:, :-1] + heights[1:, 1:])[: self.size, : self.size] / 4
        low, high = UNDERWATER_BUILD if self.island.m_clime == 1 else (BUILD_FLOOR, np.inf)
        return area & (mean > low) & (mean <= high)

    def apply_heights(self) -> None:
        height_map_node = self.game_session_manager.path("TerrainManager/HeightMap")
        heightmap_width = height_map_node.find("Width").u32()
        import numpy as np

        corners = np.asarray(self.heights, np.float64)
        sample_positions = np.arange(heightmap_width) * (corners.shape[0] - 1) / (heightmap_width - 1)
        grid = smooth_sample(corners, sample_positions, sample_positions)
        values = grid.ravel().tolist()
        set_i16(height_map_node.find("HeightMap"), values, 32)
        set_i16(self.rd3d.path("Terrain/CoarseHeightMap").find("map"), values, 64)
        # the detail displacement layers on top of both heightmaps: neutral (the donor's detail
        # made buildings float or sink against the drawn terrain)
        displacement = self.game_session_manager.path("TerrainManager/DisplacementHeightMap").find("HeightMap")
        displacement.data = bytes(len(displacement.data))
        coarse_displacement = self.rd3d.path("Terrain/CoarseDisplacementMap").find("map")
        coarse_displacement.data = bytes([128]) * len(coarse_displacement.data)

    def _ground(self, x: float, z: float, radius_tiles: float = 0.0) -> float:
        """Lowest terrain (world units) within ``radius`` tiles of a point."""
        reach = int(math.ceil(radius_tiles))
        best = None
        corner_count = len(self.heights)
        for delta_z in range(-reach, reach + 1):
            for delta_x in range(-reach, reach + 1):
                if delta_x * delta_x + delta_z * delta_z > radius_tiles * radius_tiles + 0.5:
                    continue
                corner_z, corner_x = int(round(z)) + delta_z, int(round(x)) + delta_x
                if 0 <= corner_z < corner_count and 0 <= corner_x < corner_count:
                    corner_height = self.heights[corner_z][corner_x]
                    best = corner_height if best is None else min(best, corner_height)
        return (best if best is not None else 0.0) / HEIGHT_UNITS

    def apply_levels(self) -> None:
        """Per-LOD terrain node data in ``rd3d`` ``Terrain/Levels``: MinY/MaxY per node (int16 in
        world units: coarse height / 256, floor/ceil) and HasMeshArray (nodes that get a
        terrain mesh: the active map at the level's resolution; stale bits leave holes where the
        water shows through)."""
        import numpy as np

        terrain = self.rd3d.path("Terrain")
        heightmap_width, steps = a7mread.coarse_steps(self.rd3d)
        heightmap = np.frombuffer(steps, np.int16).reshape(heightmap_width, heightmap_width).astype(np.int32)
        active_map = terrain.find("ActiveMap")
        active_width = active_map.find("x").u32()
        active = np.asarray(read_bits(active_map), dtype=bool)
        cell_tiles = self.size // active_width
        active_tiles = np.repeat(np.repeat(active, cell_tiles, 0), cell_tiles, 1)  # [size, size]
        for level_tag in terrain.path("Levels").findall("#1"):
            min_y_array, max_y_array = level_tag.path("MinYArray"), level_tag.path("MaxYArray")
            node_count = min_y_array.find("width").u32()
            samples_per_node = (
                (heightmap_width - 1) // self.size * node_tiles(self.size, node_count)
            )  # heightmap samples per node
            low_codes = np.empty((node_count, node_count), dtype=np.int16)
            high_codes = np.empty((node_count, node_count), dtype=np.int16)
            for j in range(node_count):
                rows = heightmap[j * samples_per_node : (j + 1) * samples_per_node + 1]
                for i in range(node_count):
                    block = rows[:, i * samples_per_node : (i + 1) * samples_per_node + 1]
                    # floor, not vanilla's trunc: under the sea a box truncated upwards clips the node seen from below
                    low_codes[j, i] = int(np.floor(block.min() / 256))
                    high_codes[j, i] = int(np.ceil(block.max() / 256))
            min_y_array.find("map").data = low_codes.astype("<i2").tobytes()
            max_y_array.find("map").data = high_codes.astype("<i2").tobytes()
            has_mesh_array = level_tag.path("HasMeshArray")
            if has_mesh_array is not None:
                mesh_node_count = has_mesh_array.find("x").u32()
                has = node_flags(active_tiles, mesh_node_count, node_tiles(self.size, mesh_node_count))
                write_bits(has_mesh_array, has.astype(int).tolist())

    def generate_lod_meshes(self, gamedata_files: dict[str, bytes]) -> dict[str, bytes]:
        """Stored terrain meshes for the coarse LODs (island-terrain "The holes in the ground"). LOD 0 and 1 are
        built from the heightmap at runtime, LOD 2 and up are drawn from ``meshes/<lod>/<x>x<z>.tmc`` for every node
        whose ``HasMeshArray`` bit is set (a set bit without a mesh draws nothing); each .tmc tile holds 32x32 nodes.
        Vanilla meshes cover only the active area; a mesh over the full node draws the sea as a flat terrain sheet.
        Here: a regular grid per node (half-tile cells up to ``LOD_MESH_CELLS`` across), only the cells inside the
        active map, with vanilla's skirts. Returns the new .tmc files."""
        terrain = self.rd3d.path("Terrain")
        grids = lod_grids(self.rd3d, self.size)
        out: dict[str, bytes] = {}
        counts: dict[int, int] = {}
        for level, level_tag in enumerate(terrain.path("Levels").findall("#1")):
            if level < 2:
                continue
            has = read_bits(level_tag.path("HasMeshArray"))
            node_count = len(has)
            tiles_per_node = node_tiles(self.size, node_count)  # tiles per node
            docs = {
                k: (filedb.read(donor_bytes), filedb.name_ids(donor_bytes))
                for k, donor_bytes in gamedata_files.items()
                if k.startswith(f"meshes/{level}/") and k.endswith(".tmc")
            }
            made = 0
            for key, (doc, _) in docs.items():
                tile_x, tile_z = [int(number) for number in key.rsplit("/", 1)[1][:-4].split("x")]
                ents = [child for child in doc.path("Meshes/map").children if isinstance(child, filedb.STag)]
                for k, mesh_entry in enumerate(ents):
                    node_x, node_z = tile_x * 32 + k % 32, tile_z * 32 + k // 32
                    vertices = indices = b""
                    if node_x < node_count and node_z < node_count and has[node_z][node_x]:
                        mesh = node_mesh(grids, (node_x, node_z), tiles_per_node, level)
                        if mesh is not None:
                            vertices, indices = mesh
                            made += 1
                    mesh_entry.find("Vertices").data = vertices
                    mesh_entry.find("Indices").data = indices
            for key, (doc, ids) in docs.items():
                out[key] = filedb.write(doc, *ids)
            counts[level] = made
        self.report["lod_meshes"] = counts
        return out

    def _page_nodes(self, levels_key: str):
        """(level, flags node, node grid size, tiles per node, active nodes) for the per-level
        page flags under ``Terrain/<levels_key>/Levels``."""
        import numpy as np

        active_map = self.rd3d.path("Terrain/ActiveMap")
        active = np.asarray(read_bits(active_map), dtype=bool)
        cell_tiles = self.size // active.shape[0]
        active_tiles = np.repeat(np.repeat(active, cell_tiles, 0), cell_tiles, 1)
        for level, level_tag in enumerate(self.rd3d.path(f"Terrain/{levels_key}/Levels").findall("#1")):
            flags_tag = level_tag.find("Flags")
            node_count = flags_tag.find("width").u32()
            tiles_per_node = node_tiles(self.size, node_count)
            nodes = node_flags(active_tiles, node_count, tiles_per_node)
            yield level, flags_tag, node_count, tiles_per_node, nodes

    @staticmethod
    def _set_flags(flags_tag: filedb.STag, nodes) -> None:
        node_count = nodes.shape[0]
        flag_bytes = bytearray(flags_tag.find("map").data)
        flag_bytes[: node_count * node_count] = bytes(nodes.astype("u1").ravel())
        flags_tag.find("map").data = bytes(flag_bytes)

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
        heightmap_width, steps = a7mread.coarse_steps(self.rd3d)
        heightmap = (
            np.frombuffer(steps, np.int16).reshape(heightmap_width, heightmap_width).astype(np.float64)
            / a7mread.HEIGHT_STEPS
        )

        from .terrainbuild import encode_page  # the one page encoder (also the editor's)

        sample = page_ground_sampler(heightmap, coarse_weight(self.build_ground()))

        for k in [k for k in gamedata_files if k.startswith("normalmaps/")]:
            del gamedata_files[k]
        out: dict[str, bytes] = {}
        counts = {}
        levels = list(self._page_nodes("AllNormalMapLevels"))
        chosen = {}
        for level, _flags_tag, _node_count, tiles_per_node, nodes in reversed(
            levels
        ):  # coarse first: a fine page needs its parent
            if level < HEIGHT_PAGE_FINEST:  # diagnostic: leave the fine levels to the coarser pages
                nodes = np.zeros_like(nodes)
            elif level < FINE_PAGE_LEVELS:
                parent = chosen.get(level + 1)
                nodes = nodes.copy()
                for node_z, node_x in zip(*np.nonzero(nodes), strict=False):
                    has_parent = parent is None or parent[node_z // 2, node_x // 2]
                    nodes[node_z, node_x] = (
                        has_parent and page_error(sample, node_x, node_z, tiles_per_node) > PAGE_ERROR
                    )
            chosen[level] = nodes
        for level, flags_tag, _node_count, tiles_per_node, _active in levels:
            nodes = chosen[level]
            for node_z, node_x in zip(*np.nonzero(nodes), strict=False):
                doc = filedb.read(template_raw)
                encode_page(doc, sample, node_x * tiles_per_node, node_z * tiles_per_node, tiles_per_node)
                raw = filedb.write(doc, *template_ids)
                out[f"normalmaps/{level}/{node_x}x{node_z}.ctt"] = (
                    template[:4] + struct.pack("<I", len(raw)) + zlib.compress(raw, 1)
                )
            self._set_flags(flags_tag, nodes)
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
        for level, flags_tag, _node_count, _node, nodes in self._page_nodes("TintMapLevels"):
            for node_z, node_x in zip(*np.nonzero(nodes), strict=False):
                out[f"tintmaps/{level}/{node_x}x{node_z}.dds"] = page
            self._set_flags(flags_tag, nodes)
            counts[level] = int(nodes.sum())
        base = self.rd3d.path("Terrain/TintMapLevels/BaseTextureData")
        if base is not None:
            base_map = base.find("map")
            base_map.data = neutral(base_map.data)
        self.report["tint_pages"] = counts
        return out
