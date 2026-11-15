import struct
import zlib

import numpy as np

from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.islands import gameterrain, islandterrain
from annotoolbox.islands.islandgrid import HEIGHT_UNITS


def _u32(v):
    return struct.pack("<I", v)


def _page(ground, node: float) -> bytes:
    """A height page file as the island build writes one: Downsampled plus the three deltas, encoded
    against the game's rebuild so it lands on ``ground`` (heightmap units at page positions)."""
    pos = (np.arange(66) - 1 + 0.5) * node / 64
    down = np.clip(np.rint(ground(pos) * 64) + 32768, 0, 65535).astype("<u2")
    rebuilt = (down.astype(np.float64) - 32768) / 256
    deltas = []
    for width, bits, scale in ((132, 8, 512.0), (264, 7, 1024.0), (528, 6, 2048.0)):
        data = Tag(
            "Data", [Attrib("width", _u32(width)), Attrib("height", _u32(width)), Attrib("map", bytes(width * width))]
        )
        delta = Tag("#1", [data, Attrib("ScaleFactor", struct.pack("<f", scale)), Attrib("QuantBits", _u32(bits))])
        rebuilt = islandterrain.page_delta(delta, rebuilt, ground, node)
        deltas.append(delta)
    hm = Tag("HeightMap", [Tag("Downsampled", [Attrib("width", _u32(66)), Attrib("height", _u32(66)), Attrib("map", down.tobytes())]),
                           Tag("Deltas", deltas)])  # fmt: skip
    raw = filedb.write(Tag("root", [hm]))
    return b"_CTT" + struct.pack("<I", len(raw)) + zlib.compress(raw, 1)


def _hill(pos):  # heightmap units over a page, the same along x and z
    x = np.asarray(pos, float)
    return 8 + 6 * np.outer(np.sin(x / 5), np.cos(x / 7))


def test_page_rebuilds_as_the_game_does():
    f = gameterrain.rebuild_page(_page(_hill, 16.0))
    fine = (np.arange(528) - 8 + 0.5) * 16 / 512
    assert f.shape == (528, 528)
    assert np.abs(f - _hill(fine) / HEIGHT_UNITS).max() <= 0.5 / 256 + 1e-9


def test_near_terrain_takes_the_page_over_the_heightmap():
    class View:
        size = 16
        gamedata_dir = "isl/_gamedata/isl/"
        hm = np.zeros((33, 33))  # a flat coarse heightmap, heightmap units
        files = type("F", (), {
            "list": staticmethod(lambda prefix: ["isl/_gamedata/isl/normalmaps/0/0x0.ctt"]),
            "read": staticmethod(lambda path: _page(_hill, 16.0)),
        })  # fmt: skip

    h, level = gameterrain.near_heights(View(), 0.5)
    assert h.shape == (33, 33) and (level == 0).all()
    g = np.arange(33) * 0.5
    want = np.clip(_hill(g) / HEIGHT_UNITS, -gameterrain.PAGE_RANGE, gameterrain.PAGE_RANGE)
    assert np.abs(h - want).max() < 0.01  # the page, clamped to one unit off the flat heightmap


def _coarse_hill(size: int = 16):
    """A coarse heightmap (two samples per tile, heightmap units) with a curved hill: the cubic through its samples
    bulges away from the bilinear surface between them by more than 0.05 world units."""
    g = np.arange(2 * size + 1) / 2
    return 4 * np.outer(np.sin(g * np.pi / 2), np.cos(g * np.pi / 2))


def test_coarse_weight_is_one_on_the_area_and_fades_to_nothing():
    area = np.zeros((24, 24), bool)
    area[10:14, 10:14] = True
    w = islandterrain.coarse_weight(area)
    assert (w[area] == 1).all() and w[9, 10] == 1  # the area and the tile round it
    assert w[5, 10] == 0 and w[0, 0] == 0  # beyond the fade
    assert 0 < w[7, 10] < 1 and w[7, 10] < w[8, 10]


def test_pages_are_the_cubic_held_near_the_coarse_heightmap_on_the_area_and_free_elsewhere():
    hm = _coarse_hill()
    everywhere = np.ones((16, 16), bool)
    nowhere = np.zeros((16, 16), bool)
    pos = (np.arange(528) - 8 + 0.5) * 16 / 512
    zz, xx = np.meshgrid(*[np.clip(pos * 2, 0, 32)] * 2, indexing="ij")  # the page border clamps to the edge
    from scipy.ndimage import map_coordinates

    bilinear = map_coordinates(hm, [zz, xx], order=1)
    on = islandterrain.page_ground_sampler(hm, islandterrain.coarse_weight(everywhere))(pos, pos)
    off = islandterrain.page_ground_sampler(hm, islandterrain.coarse_weight(nowhere))(pos, pos)
    cubic = islandterrain.smooth_sample(hm, np.clip(pos * 2, 0, 32), np.clip(pos * 2, 0, 32))
    tol = islandterrain.PAGE_ROAD_TOLERANCE * HEIGHT_UNITS
    assert np.abs(on - (bilinear + np.clip(cubic - bilinear, -tol, tol))).max() < 1e-9  # roads lie on the bilinear
    assert np.abs(off - cubic).max() < 1e-9
    assert np.abs(cubic - bilinear).max() / HEIGHT_UNITS > 0.05  # the hill is curved enough to tell the two apart


def test_the_pages_hold_only_2070s_build_ground_and_the_beach_below_it_bends_freely(monkeypatch):
    """Harbour fix 6: the hold to the coarse map covers the island area's tiles over 2070's build floor (and fades
    out over ``PAGE_FLAT_NEAR + PAGE_FLAT_FADE`` tiles), not the coast band and its sea: the low shore beyond is the
    free cubic, never the bilinear's half-tile creases."""
    from types import SimpleNamespace

    from annotoolbox.islands import islandgrid

    n = 24
    # 2070's one height per tile, as the coarse map takes it: a plateau at 0.7, then a kinked low shore to the sea
    per_tile = [0.7] * 9 + [0.5, 0.3, 0.2, 0.4, 0.1, 0.35, 0.05, 0.3, 0.0, 0.2, -0.3, -0.8, -1, -1, -1, -1]
    beach = np.interp(np.arange(2 * n + 1) / 2, np.arange(len(per_tile)), per_tile)
    hm = np.tile(beach * HEIGHT_UNITS, (2 * n + 1, 1))
    area = np.full((n, n), islandgrid.AREA_ISLAND)  # the whole island area, coast band and its water included
    monkeypatch.setattr(islandterrain, "read_sparse", lambda tag: area)
    fake = SimpleNamespace(
        gm=SimpleNamespace(path=lambda p: None), size=n, heights=hm[::2, ::2], isl=SimpleNamespace(clime=0)
    )
    ground = islandterrain.TerrainMixin.build_ground(fake)
    assert ground[:, :8].all() and not ground[:, 8:].any()  # tile 8 reaches down to 0.5: its mean is 0.6, not over
    pos = 8 + islandterrain.PAGE_FLAT_NEAR + islandterrain.PAGE_FLAT_FADE + np.arange(6 * 32) / 32  # the low shore
    z = np.array([5.0])
    held = islandterrain.page_ground_sampler(hm, islandterrain.coarse_weight(ground))(pos, z)[0]
    whole = islandterrain.page_ground_sampler(hm, islandterrain.coarse_weight(area > 0))(pos, z)[0]
    free = islandterrain.page_ground_sampler(hm)(pos, z)[0]
    assert np.abs(held - free).max() == 0 and np.abs(whole - free).max() > 0

    def crease(h):  # the largest jump of the slope between neighbouring texels
        return np.abs(np.diff(h, 2)).max()

    assert crease(held) <= crease(whole)


def test_pages_on_a_gentle_slope_have_no_half_tile_facets():
    """A bilinear page bends only on the half-tile sample lines: the game's normals light such a slope in stair
    steps (in game 2026-10-28). The cubic bends smoothly, and on a gentle slope stays within the road tolerance."""
    hm = _coarse_hill() / 8
    pos = (np.arange(528) - 8 + 0.5) * 16 / 512
    zz, xx = np.meshgrid(*[np.clip(pos * 2, 0, 32)] * 2, indexing="ij")
    from scipy.ndimage import map_coordinates

    bilinear = map_coordinates(hm, [zz, xx], order=1)
    page = islandterrain.page_ground_sampler(hm, islandterrain.coarse_weight(np.ones((16, 16), bool)))(pos, pos)

    def crease(h):  # the largest jump of the slope between neighbouring texels
        return np.abs(np.diff(h, 2, axis=1)).max()

    assert crease(page) < 0.5 * crease(bilinear)
    assert np.abs(page - bilinear).max() / HEIGHT_UNITS <= islandterrain.PAGE_ROAD_TOLERANCE


def test_the_check_tells_a_page_off_the_coarse_map_from_one_on_it():
    from annotoolbox.islands import islandgrid, islandview

    hm = _coarse_hill() + 2 * HEIGHT_UNITS  # over 2070's build floor everywhere: the ground roads and buildings take
    everywhere = np.ones((16, 16), bool)

    def view(sample):
        class Fake(islandview.IslandView):
            size = 16
            gamedata_dir = "isl/_gamedata/isl/"
            land = everywhere

            def __init__(self):
                self.hm = hm
                self.files = type("F", (), {
                    "list": staticmethod(lambda prefix: ["isl/_gamedata/isl/normalmaps/0/0x0.ctt"]),
                    "read": staticmethod(lambda path: _page(lambda pos: sample(pos, pos), 16.0)),
                })  # fmt: skip

            def island_area(self):
                return np.full((16, 16), islandgrid.AREA_ISLAND)

        return Fake()

    flat = islandterrain.page_ground_sampler(hm, islandterrain.coarse_weight(everywhere))
    cubic = islandterrain.page_ground_sampler(hm)
    assert view(flat).check_page_agreement()[0].startswith("OK")
    assert view(cubic).check_page_agreement()[0].startswith("FAIL")


def test_a_step_over_what_a_page_carries_is_no_buildable_point():
    """A shoreline cliff of the coarse map (40 units in a half tile) is not carried by a page: the grid points beside
    it are left out of the page agreement check, the flat ground elsewhere is not."""
    from annotoolbox.islands.islandchecks import IslandChecks

    row = np.array([-40.0, -40.0, -20.0, 0.7, 0.7, 0.7, 0.9])
    cliffs = IslandChecks.page_cliffs(np.tile(row, (3, 1)))
    assert cliffs[0].tolist() == [False, True, True, True, False, False, False]
