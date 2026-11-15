"""The preview's checks place the height pages and the terrain nodes as 117 does on a grid that is not a power of two:
a level-p page covers 16 * 2^p tiles on every grid (vanilla's 192, 320, 384, 448 and 768 islands, 2026-11-07), so
the last page of such a level reaches past the island (island-terrain "The grid")."""

import struct
import zlib

import numpy as np

from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.islands import islandterrain, islandview
from annotoolbox.islands.islandgrid import write_bits
from annotoolbox.islands.terrainbuild import encode_page

SIZE = 48  # pages: three of 16 tiles at level 0, two of 32 at level 1 (the second half past the island), one of 64
GAMEDATA = "isl/_gamedata/isl/"


def _u32(v):
    return struct.pack("<I", v)


def _grid(name: str, width: int, data: bytes) -> Tag:
    return Tag(name, [Attrib("width", _u32(width)), Attrib("height", _u32(width)), Attrib("map", data)])


def _heightmap() -> np.ndarray:
    """Two samples per tile, heightmap units: a hill rising towards +x out of a sea bed (where the build's floored
    node bottoms differ from vanilla's truncated ones)."""
    g = np.arange(2 * SIZE + 1) / 2
    return 5 + 30 * np.outer(np.sin(g / 7), np.cos(g / 11)) + 1.5 * g[None, :]


def _page(ground, x0: float, z0: float, tiles: float) -> bytes:
    deltas = [
        Tag("#1", [_grid("Data", w, bytes(w * w)), Attrib("ScaleFactor", struct.pack("<f", s)), Attrib("QuantBits", _u32(b))])
        for w, b, s in ((132, 8, 512.0), (264, 7, 1024.0), (528, 6, 2048.0))
    ]  # fmt: skip
    doc = Tag("root", [Tag("HeightMap", [_grid("Downsampled", 66, bytes(66 * 66 * 2)), Tag("Deltas", deltas)])])
    encode_page(doc, ground, x0, z0, tiles)
    raw = filedb.write(doc)
    return b"_CTT" + _u32(len(raw)) + zlib.compress(raw, 1)


class _Island(islandterrain.TerrainMixin):
    """The build's node bounds (``apply_levels``) over a bare rd3d."""

    def __init__(self, rd: Tag) -> None:
        self.rd, self.size = rd, SIZE


class _View(islandview.IslandView):
    def __init__(self, hm: np.ndarray, rd: Tag, pages: dict[str, bytes]) -> None:
        self.size, self.hm, self.rd, self.gamedata_dir = SIZE, hm.astype(np.float32), rd, GAMEDATA
        self.files = type("F", (), {"read": staticmethod(lambda key: pages[key])})


def _island() -> tuple[np.ndarray, Tag, dict[str, bytes]]:
    """A 48-tile island as the build lays it out: pages of every level on the game's 16 * 2^p tiles, the node bounds
    from the build's own ``apply_levels``."""
    hm = np.rint(_heightmap() * 64) / 64  # as stored: int16 at 1/64 heightmap unit
    ground = islandterrain.page_ground_sampler(hm)
    pages, page_levels = {}, []
    for level in range(3):
        tiles = 16 * 2**level
        n = -(-SIZE // tiles)
        for nz in range(n):
            for nx in range(n):
                pages[f"{GAMEDATA}normalmaps/{level}/{nx}x{nz}.ctt"] = _page(ground, nx * tiles, nz * tiles, tiles)
        page_levels.append(Tag("#1", [_grid("Flags", n, bytes([1]) * (n * n))]))
    node_levels = []
    n = SIZE
    while n >= 1:
        node_levels.append(
            Tag("#1", [_grid("MinYArray", n, bytes(2 * n * n)), _grid("MaxYArray", n, bytes(2 * n * n))])
        )
        n = n // 2 if n > 3 else n - 1  # 48, 24, 12, 6, 3, 2, 1 as vanilla's 192 grid halves
    active = Tag("ActiveMap", [Attrib("x", _u32(6)), Attrib("y", _u32(6)), Attrib("bits", bytes(4 * 6))])
    write_bits(active, [[1] * 6 for _ in range(6)])
    coarse = _grid("CoarseHeightMap", 2 * SIZE + 1, np.rint(hm * 64).astype("<i2").tobytes())
    terrain = Tag("Terrain", [coarse, active, Tag("Levels", node_levels),
                              Tag("AllNormalMapLevels", [Tag("Levels", page_levels)])])  # fmt: skip
    rd = Tag("rd3d", [terrain])
    _Island(rd).apply_levels()
    return hm, rd, pages


def test_the_page_check_lays_pages_on_16_times_2_to_the_level_tiles_on_an_odd_grid():
    hm, rd, pages = _island()
    rep = _View(hm, rd, pages).check_height_pages()
    assert len(rep) == 3
    assert all(line.startswith("OK") for line in rep), rep


def test_the_node_bounds_check_reads_the_nodes_the_build_wrote_on_an_odd_grid():
    hm, rd, pages = _island()
    rep = _View(hm, rd, pages).check_node_bounds()
    assert rep[0].startswith("OK"), rep
