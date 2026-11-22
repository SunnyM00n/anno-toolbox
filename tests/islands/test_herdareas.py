"""Herd areas derived from painted tiles (``islands.herdareas``)."""

import struct

import numpy as np

from annotoolbox.formats.filedb import SAttrib, STag
from annotoolbox.islands import herdareas
from annotoolbox.islands.islandgrid import write_bits

SIZE = 10


def _grid(name: str, cells) -> STag:
    tag = STag(
        name,
        [
            SAttrib("x", struct.pack("<I", SIZE)),
            SAttrib("y", struct.pack("<I", SIZE)),
            SAttrib("bits", bytes(SIZE * 4)),
        ],
    )
    write_bits(tag, np.asarray(cells, int).tolist())
    return tag


def _entry() -> STag:
    return STag(
        "#1", [SAttrib("CellsCount", bytes(4)), SAttrib("BBox", b"\x01" * 8), SAttrib("RandomPoint", b"\x01" * 8)]
    )


def _island(herd=None, areas=(), land=None) -> STag:
    land = np.ones((SIZE, SIZE), int) if land is None else land
    river = np.zeros((SIZE, SIZE), int)
    river[:, 9] = 1
    herd = np.zeros((SIZE, SIZE), int) if herd is None else herd
    animals = STag("AnimalManager", [_grid("HerdGrid", herd), STag("HerdAreas", list(areas))])
    return STag("gm", [STag("WorldManager", [_grid("Water", land), _grid("RiverGrid", river)]), animals])


def _count(entry: STag) -> int:
    return struct.unpack("<I", entry.find("CellsCount").data)[0]


def test_painted_tiles_become_one_area_per_connected_region_with_its_tile_count():
    gm = _island(areas=[_entry()])
    cells = {f"{x},{z}": 1 for x in range(8) for z in range(8)}  # 64 tiles, one region
    cells |= {"9,0": 1, "0,9": 1, "-1,3": 1, "3,x": 1}  # a river tile, a lone tile (dropped), two nonsense keys
    report = herdareas.apply(gm, cells)
    assert report["tiles"] == 65 and report["areas"] == 1 and report["dropped"] == 1
    assert report["unmatched"] == ["herd tile -1,3", "herd tile 3,x", "herd tile 9,0"]
    areas = gm.path("AnimalManager/HerdAreas").children
    assert [_count(a) for a in areas] == [64]
    assert areas[0].find("BBox").data == bytes(8)  # run-time state starts empty
    assert herdareas.read(gm).sum() == 64 and herdareas.read(gm)[7, 7] and not herdareas.read(gm)[9, 0]


def test_a_tile_in_the_water_is_not_a_herd_tile_and_an_erased_patch_leaves_no_area():
    land = np.ones((SIZE, SIZE), int)
    land[0, 0] = 0
    herd = np.zeros((SIZE, SIZE), int)
    herd[2:8, 2:8] = 1  # 36 tiles: under the minimum, kept only because it is the island's own
    gm = _island(herd, [_entry()], land)
    report = herdareas.apply(gm, {"0,0": 1, "4,4": 0})
    assert report["unmatched"] == ["herd tile 0,0"] and report["tiles"] == 1
    assert report["areas"] == 0 and report["dropped"] == 35 and herdareas.read(gm).sum() == 0


def test_an_edit_that_changes_no_tile_leaves_the_islands_own_areas_as_its_file_draws_them():
    herd = np.zeros((SIZE, SIZE), int)
    herd[2:8, 2:8] = 1  # a small patch the island's own file keeps
    gm = _island(herd, [_entry()])
    report = herdareas.apply(gm, {"0,0": 0, "9,9": 1})  # nothing on the tile, and a river tile
    assert report["tiles"] == 0 and report["areas"] == 1 and report["unmatched"] == ["herd tile 9,9"]
    assert herdareas.read(gm).sum() == 36 and len(gm.path("AnimalManager/HerdAreas").children) == 1


def test_the_regions_are_counted_in_one_pass_and_those_under_the_minimum_leave_the_grid():
    roam = np.zeros((40, 40), bool)
    roam[0:7, 0:7] = True  # 49
    roam[20:22, 20:22] = True  # 4
    roam[30:37, 30:37] = True  # 49
    rows, kept = herdareas.entries(roam, _entry())
    assert [_count(r) for r in rows] == [49, 49] and int(kept.sum()) == 98 and not kept[20, 20]


def test_an_island_without_a_grid_or_an_entry_to_copy_reports_the_edit_unmatched():
    assert herdareas.apply(STag("gm", []), {"1,1": 1})["unmatched"] == ["no herd grid on this island"]
    assert herdareas.apply(_island(), {"1,1": 1})["unmatched"] == ["no herd grid on this island"]  # no entry to copy
    gm = _island()
    assert herdareas.apply(gm, {"1,1": 1}, _entry())["tiles"] == 1  # the build hands over the donor's entry
    assert herdareas.apply(gm, {}) == {"tiles": 0, "areas": 0, "unmatched": []}


def test_the_array_reader_agrees_with_the_list_reader_on_widths_off_the_32_grid():
    from annotoolbox.islands.islandgrid import bits_array, read_bits

    rng = np.random.default_rng(3)
    for width in (5, 32, 40):
        cells = rng.integers(0, 2, (width + 2, width))
        tag = STag(
            "g",
            [
                SAttrib("x", struct.pack("<I", width)),
                SAttrib("y", struct.pack("<I", width + 2)),
                SAttrib("bits", bytes(((width + 31) // 32) * 4 * (width + 2))),
            ],
        )
        write_bits(tag, cells.tolist())
        assert (
            bits_array(tag).tolist()
            == [[bool(v) for v in row] for row in read_bits(tag)]
            == cells.astype(bool).tolist()
        )
