"""The ground as Anno 117 draws it near the camera, for the island editor's game view.

The engine draws the near terrain from the height pages (``normalmaps/<level>/<x>x<z>.ctt``), not from the
heightmap: each page is rebuilt from its 66x66 samples by three Catmull-Rom upsamplings with a delta added at each
step, a vertex takes the coarse heightmap plus the page's difference from it, clamped to one world unit
(rendering/terrain-geometry.md 1.2-1.4); the far terrain from the stored meshes. ``near_heights`` reproduces that on
the CPU (island-editor-view "Drawn by 117's rules"); ``read_page``, ``page_files`` and ``stored_meshes`` hand the
files to the view that rebuilds the pages by the game's own shaders (rendering/island-117-view.md).
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

from ..formats import filedb
from .islandgrid import HEIGHT_UNITS
from .islandterrain import page_up2

PAGE_TEXELS = 528  # a rebuilt page, border included
PAGE_BORDER = 8
PAGE_TILES = 16  # tiles a level-0 page covers; doubles per level
PAGE_RANGE = 1.0  # world units the page may depart from the coarse heightmap (TERRAIN_COMPACTED_HEIGHT_MAP_RANGE)


def read_page(data: bytes) -> tuple[np.ndarray, list[tuple[np.ndarray, float, int]]]:
    """A page file's parts: the ``Downsampled`` heights (u16, [rows, columns]) and its deltas coarsest first, each
    (codes u8 [rows, columns], ``ScaleFactor``, ``QuantBits``; 8 where the tag is left out)."""
    document = filedb.read(zlib.decompress(data[8:]))
    downsampled = document.path("HeightMap/Downsampled")
    side = downsampled.find("width").u32()
    down = np.frombuffer(downsampled.find("map").data, "<u2").reshape(side, side)
    deltas = []
    for delta in sorted(
        document.path("HeightMap/Deltas").findall("#1"), key=lambda d: d.path("Data").find("width").u32()
    ):
        width = delta.path("Data").find("width").u32()
        quant_bits = delta.find("QuantBits")
        bits = struct.unpack("<I", quant_bits.data)[0] if quant_bits is not None else 8
        scale = struct.unpack("<f", delta.find("ScaleFactor").data)[0]
        deltas.append((np.frombuffer(delta.path("Data/map").data, np.uint8).reshape(width, width), scale, bits))
    return down, deltas


def rebuild_levels(data: bytes) -> dict[int, np.ndarray]:
    """A page file's surface at each level as the game rebuilds it (world units, texel centres), keyed by the level's
    width: the 66x66 samples first, then each refinement level."""
    down, deltas = read_page(data)
    heights = (down.astype(np.float64) - 32768) / 256
    out = {heights.shape[0]: heights}
    for codes, scale, bits in deltas:
        half = 2 ** (bits - 1)
        heights = page_up2(heights) + (codes.astype(np.float64) - half) * 256 / (scale * half)
        out[heights.shape[0]] = heights
    return out


def rebuild_page(data: bytes) -> np.ndarray:
    """A page file rebuilt as the game rebuilds it: 528x528 heights in world units."""
    return list(rebuild_levels(data).values())[-1]


def page_files(view, folder: str = "normalmaps", suffix: str = ".ctt") -> dict[tuple[int, int, int], str]:
    """The island's pages by (level, x, z): ``<folder>/<level>/<x>x<z><suffix>`` (the height pages; the tint pages
    are ``tintmaps`` of ``.dds``)."""
    pages = {}
    for path in view.files.list(f"{view.gamedata_dir}{folder}/"):
        if path.endswith(suffix):
            level, name = path.rsplit("/", 2)[-2:]
            nx, nz = (int(number) for number in name[: -len(suffix)].split("x"))
            pages[(int(level), nx, nz)] = path
    return pages


CLUSTER = 32  # nodes a side of one ``.tmc`` file (rd3d ``NodesPerMeshCluster``)


def stored_meshes(view, level: int) -> dict[tuple[int, int], tuple[bytes, np.ndarray]]:
    """The stored terrain meshes of a level (``meshes/<level>/<x>x<z>.tmc``, 32 x 32 nodes each, row by row) by node
    (column, row): the 16-byte vertices and the triangle indices as stored (u16: the device copy widens them); a node
    without vertices is left out. All read at once: read per cluster when a node asks, the nodes drawn meanwhile from
    the heights made the moving frame slower (island-117-view.md "Audit B2")."""
    out = {}
    for path in view.files.list(f"{view.gamedata_dir}meshes/{level}/"):
        if not path.endswith(".tmc"):
            continue
        tx, tz = (int(vertices) for vertices in path.rsplit("/", 1)[1][:-4].split("x"))
        entries = [
            entry
            for entry in filedb.read(view.files.read(path)).path("Meshes/map").children
            if isinstance(entry, filedb.STag)
        ]
        for k, entry in enumerate(entries):
            vertices, indices = entry.find("Vertices"), entry.find("Indices")
            if vertices is not None and vertices.data and indices is not None and indices.data:
                key = (tx * CLUSTER + k % CLUSTER, tz * CLUSTER + k // CLUSTER)
                out[key] = (bytes(vertices.data), np.frombuffer(indices.data, "<u2").copy())
    return out


def coarse(heightmap: np.ndarray, x, z):
    """The coarse heightmap (world units, sample i at world i/2) bilinear at tile positions."""
    size = heightmap.shape[0]
    xs, zs = np.clip(np.asarray(x, float) * 2, 0, size - 1.001), np.clip(np.asarray(z, float) * 2, 0, size - 1.001)
    x0, z0 = xs.astype(int), zs.astype(int)
    tx, tz = xs - x0, zs - z0
    return (heightmap[z0, x0] * (1 - tx) + heightmap[z0, x0 + 1] * tx) * (1 - tz) + (
        heightmap[z0 + 1, x0] * (1 - tx) + heightmap[z0 + 1, x0 + 1] * tx
    ) * tz


def near_heights(view, step_tiles: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """The near terrain on a grid of ``step`` tiles over the island: (heights in world units, the
    page level each point was drawn from, -1 where no page covers it and the heightmap stands)."""
    heightmap = view.height_map / HEIGHT_UNITS
    size = view.size
    sample_count = int(round(size / step_tiles)) + 1
    grid = np.arange(sample_count) * step_tiles
    gz, gx = np.meshgrid(grid, grid, indexing="ij")
    heights = coarse(heightmap, gx, gz)
    level_of = np.full((sample_count, sample_count), -1, np.int8)
    pages = page_files(view)
    for (level, nx, nz), path in sorted(pages.items(), key=lambda item: -item[0][0]):  # coarse first, finer over it
        tiles = PAGE_TILES * 2**level
        x0, z0 = nx * tiles, nz * tiles
        inside = (gx >= x0) & (gx <= x0 + tiles) & (gz >= z0) & (gz <= z0 + tiles)
        if not inside.any():
            continue
        page = rebuild_page(view.files.read(path))
        texel = tiles / (PAGE_TEXELS - 2 * PAGE_BORDER)
        centres = x0 + (np.arange(PAGE_TEXELS) - PAGE_BORDER + 0.5) * texel
        cz, cx = np.meshgrid(z0 - x0 + centres, centres, indexing="ij")
        residual = np.clip(page - coarse(heightmap, cx, cz), -PAGE_RANGE, PAGE_RANGE)
        px, pz = gx[inside], gz[inside]
        u = (px - x0) / texel + PAGE_BORDER - 0.5
        v = (pz - z0) / texel + PAGE_BORDER - 0.5
        u0, v0 = (
            np.clip(np.floor(u).astype(int), 0, PAGE_TEXELS - 2),
            np.clip(np.floor(v).astype(int), 0, PAGE_TEXELS - 2),
        )
        tu, tv = u - u0, v - v0
        residual_height = (residual[v0, u0] * (1 - tu) + residual[v0, u0 + 1] * tu) * (1 - tv) + (
            residual[v0 + 1, u0] * (1 - tu) + residual[v0 + 1, u0 + 1] * tu
        ) * tv
        heights[inside] = coarse(heightmap, px, pz) + residual_height
        level_of[inside] = level
    return heights.astype(np.float32), level_of
