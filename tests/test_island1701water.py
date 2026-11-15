"""1701's rivers, coast lines and properties (the ``.isc`` header), the blank island of the old engine, and the tools each
game's islands offer."""

import struct

import pytest

from annotoolbox.editor import oldenginebuild
from annotoolbox.editor.desktop import desktopeditor
from annotoolbox.editor.formats.isc import edit as isc_edit
from annotoolbox.editor.formats.isc import water as isc_water
from annotoolbox.editor.formats.isd import edit as isd_edit
from annotoolbox.formats import isd2070, rda
from annotoolbox.islands import islandedits
from annotoolbox.othergames import isd1701
from annotoolbox.sources.overlay import SourceTree
from test_isd1701 import isd_bytes, point, t
from test_island2070edit import isd_bytes as isd2070_bytes

TILE = isd1701.TILE


def isc_bytes() -> bytes:
    """A header with a coast of two points, a river of three (one point of each in the other point layout) and a
    clime and a difficulty."""
    coast = t(
        "i", t("m_ID", "1") + t("m_Polygon", t("i", point(0, 0, 32, 96, True)) + t("i", point(64, 0, 32, 64, True)))
    )
    river = t(
        "i",
        t("m_ID", "7")
        + t("m_GUID", "236")
        + t(
            "m_Polygon",
            t("i", point(16, 20, 16, 16, True))
            + t("i", point(32, 20, 32, 16, False))
            + t("i", point(64, 20, 32, 8, True)),
        ),
    )
    return (
        t("Width", "2") + t("Height", "2") + t("Clime", "1") + t("Difficulty", "0")
        + t("Rivers", river) + t("RiverBlockings", t("i", t("x", "1") + t("y", "1")))
        + t("Coastlines", coast)
    )  # fmt: skip


def island() -> isd1701.Island1701:
    return isd1701.Island1701(isd_bytes(), isc_bytes())


def test_the_network_gives_rivers_and_coast_lines_in_tiles():
    net = isc_water.network(island())
    assert net["lakes"] == [] and len(net["rivers"]) == 1 and len(net["coasts"]) == 1
    assert net["rivers"][0]["points"] == [[0.5, 20 / TILE, 0.5], [1.0, 20 / TILE, 1.0], [2.0, 20 / TILE, 1.0]]
    assert net["rivers"][0]["widths"] == [0.5, 0.5, 0.25]
    assert net["coasts"][0]["points"] == [[0.0, 1.0], [2.0, 1.0]]


def test_an_island_without_edits_keeps_its_header_byte_for_byte():
    isl = island()
    report = isc_edit.apply(isl, {})
    assert isl.write_header() == isc_bytes() and report["unmatched"] == []


def test_a_river_and_a_coast_line_are_rewritten_and_read_back_as_edited():
    isl = island()
    edits = {
        "rivers": {
            "0": {
                "points": [[0.5, 0.625, 0.5], [1.5, 0.625, 1.0], [2.0, 0.625, 1.5], [2.0, 0.625, 2.0]],
                "widths": [1, 1, 1, 2],
            }
        },
        "coast": {"0": {"points": [[0.0, 0.5], [1.0, 1.0], [2.0, 0.5]]}},
    }
    report = isc_edit.apply(isl, edits)
    assert report["rivers"] == 1 and report["coasts"] == 1 and report["unmatched"] == []
    back = isd1701.Island1701(isd_bytes(), isl.write_header())
    river, coast = back.rivers()[0], back.coasts()[0]
    assert len(river.points) == 4 and river.points[3] == (2.0, 20.0, 2.0) and river.widths == [1, 1, 1, 2]
    assert [(x, z) for x, _y, z in coast.points] == [(0.0, 0.5), (1.0, 1.0), (2.0, 0.5)]
    assert {p[1] for p in coast.points} == {0.0} and coast.widths == [3.0, 3.0, 2.0]  # the nearest old point's widths
    again = isc_edit.apply(
        isd1701.Island1701(isd_bytes(), isl.write_header()), {"rivers": {"4": {"points": [], "widths": []}}}
    )
    assert again["unmatched"] == ["river 4"]


@pytest.mark.parametrize(
    "change",
    [
        {"points": [[0.5, 0.625, 0.5]], "widths": [1]},  # one point is no line
        {"points": [[0.5, 0.625, 0.5], [1.0, 0.625, 1.0]]},  # no widths
        {"points": [[0.5, 0.625, 0.5], [1.0, 0.625, 1.0]], "widths": [1]},  # one width short
    ],
)
def test_a_river_edit_that_cannot_be_a_line_is_listed_and_leaves_the_river_as_it_was(change):
    isl = island()
    report = isc_edit.apply(isl, {"rivers": {"0": change}})
    assert report["unmatched"] == ["river 0"] and "rivers" not in report
    assert isl.write_header() == isc_bytes()


def test_the_length_along_a_river_is_written_where_the_file_has_one():
    isl = island()
    for node in isl.header.find("Rivers").children[0].find("m_Polygon").children:
        node.find("distance").text = "5.000000"
    isc_edit.apply(isl, {"rivers": {"0": {"points": [[0.0, 0.5, 0.0], [1.0, 0.5, 0.0]], "widths": [1, 1]}}})
    distances = [
        p.float("distance") for p in isc_water.list_items(isl.header.find("Rivers").children[0].find("m_Polygon"))
    ]
    assert distances == [0.0, 32.0]


def test_properties_are_set_in_the_header_within_1701s_ranges():
    isl = island()
    report = isc_edit.apply(isl, {"properties": {"clime": 0, "difficulty": 2, "sea_level": -5}})
    assert report["properties"] == 2  # a sea level is not a property of a 1701 island
    assert (isl.header.int("Clime"), isl.header.int("Difficulty")) == (0, 2) and isl.header.find("SeaLevel") is None
    assert isc_edit.apply(island(), {"properties": {"difficulty": 3}})["properties"] == 0


def test_a_blank_island_has_no_object_prop_or_river_and_keeps_its_terrain_and_coast():
    isl = island()
    report = isc_edit.apply(isl, {"clear": True})
    assert report["cleared"] == {"objects": 3, "rivers": 1} and isl.objects() == []
    assert isl.rivers() == [] and len(isl.coasts()) == 1
    assert isl.header.find("RiverBlockings").children == [] and len(isl.heightmap()) == 3
    assert isc_edit.apply(isl, {})["objects deleted"] == 0


def test_an_edit_of_the_header_goes_into_the_build_and_an_unedited_header_does_not(tmp_path):
    isle = "data/levels/islands/north/isle.isd"
    path = tmp_path / "data0.rda"
    path.write_bytes(rda.write_v11({isle: isd_bytes(), isle[:-4] + ".isc": isc_bytes()}, 1000))
    tree = SourceTree.from_archives([path])
    edits = tmp_path / "e"
    edits.mkdir()
    islandedits.save(edits / "1701_isle.json", {**islandedits.empty(), "source": isle, "properties": {"clime": 0}})
    files, written = oldenginebuild.edited_1701(tree, edits, lambda _m: None)
    assert set(files) == {isle, isle[:-4] + ".isc"} and written == [isle]
    assert isd1701.Island1701(files[isle], files[isle[:-4] + ".isc"]).clime == 0
    islandedits.save(edits / "1701_isle.json", {**islandedits.empty(), "source": isle})
    assert sorted(oldenginebuild.edited_1701(tree, edits, lambda _m: None)[0]) == [
        isle
    ]  # nothing in the header changed


def test_a_blank_island_of_the_2070_family_has_no_object_or_river_and_keeps_its_terrain():
    isl = isd_edit.Island2070(isd2070_bytes())
    before = len(isl.island.objects())
    assert before > 0
    report = isl.apply({"clear": True}, {})
    assert report["cleared"]["objects"] == before and isl.island.objects() == []
    assert isl.root.find("Terrain") is not None and isd2070.Island(isl.write()).objects() == []


def test_each_games_island_offers_the_tools_its_files_allow():
    def tools(kind: str) -> set[str]:
        return desktopeditor.modes({"kind": kind}, {})["tools"]

    assert {"height", "paint", "water", "spray"} <= tools("anno2205") and not {"forest", "blocker"} & tools("anno2205")
    assert {"spray", "height", "paint"} <= tools("anno1404") and "water" in tools("anno1404")
    assert {"spray", "water", "height", "paint"} <= tools("anno1701") and "forest" not in tools("anno1701")


@pytest.mark.parametrize("key", ["anno1701", "anno1404"])
def test_the_old_games_islands_are_blankable_and_2205_is_not(key):
    from annotoolbox.editor import editorapp

    assert key in editorapp.BLANKABLE and "anno2205" not in editorapp.BLANKABLE


def _installed(key: str):
    import os
    from pathlib import Path

    from annotoolbox.othergames import installs
    from fakes import INSTALL_VARS

    folder = os.environ.get(INSTALL_VARS[key])
    if not folder or not installs.GAMES[key].is_install(Path(folder)):
        pytest.skip(f"needs the installed {key}")
    return installs.GAMES[key].tree(Path(folder))


def test_an_installed_1701_island_with_rivers_is_edited_and_blanked_and_read_again():
    tree = _installed("anno1701")
    source = "data/levels/islands/addon01/campaign/mis01_s_k07.isd"
    isl = isd1701.load(tree, source)
    assert isc_edit.apply(isd1701.load(tree, source), {})["unmatched"] == []
    assert isl.write_header() == tree.read_bytes(source[:-4] + ".isc")  # unedited: the game's own bytes
    river = isc_water.network(isl)["rivers"][0]
    points = [[x + 1, y, z] for x, y, z in river["points"]]
    isc_edit.apply(
        isl, {"rivers": {"0": {"points": points, "widths": river["widths"]}}, "properties": {"difficulty": 1}}
    )
    back = isd1701.Island1701(isl.write(), isl.write_header())
    assert (
        back.rivers()[0].points[0][0] == pytest.approx(river["points"][0][0] + 1) and back.header.int("Difficulty") == 1
    )
    blank = isd1701.load(tree, source)
    report = isc_edit.apply(blank, {"clear": True})
    blank2 = isd1701.Island1701(blank.write(), blank.write_header())
    assert report["cleared"]["objects"] > 0 and blank2.objects() == [] and blank2.rivers() == []
    assert len(blank2.coasts()) == len(isl.coasts())


def test_an_installed_1404_island_is_blanked_and_shaped_into_a_plateau_island():
    import numpy as np

    from annotoolbox.editor import blankisland

    tree = _installed("anno1404")
    source = "data/levels/islands/north/n_l01.isd"
    isl = isd_edit.Island2070(tree.read_bytes(source))
    corners = np.asarray(isl.island.heightmap(), float)
    edits = blankisland.blank_edits(corners, isl.size, source=source)
    report = isl.apply(edits, {})
    back = isd2070.Island(isl.write())
    assert report["cleared"]["objects"] > 0 and back.objects() == [] and report["heights"] > 0
    assert np.asarray(back.heightmap())[isl.size // 2, isl.size // 2] > 0


def _blocked(rows: list[tuple[int, int]], numbered: bool) -> bytes:
    """A ``RiverBlockings`` list of those tiles, in either list generation."""
    if numbered:
        items = b"".join(t(f"i{n:04d}", t("x", str(x)) + t("y", str(y))) for n, (x, y) in enumerate(rows))
        return t("RiverBlockings", t("arraysize", str(len(rows))) + items)
    return t("RiverBlockings", b"".join(t("i", t("x", str(x)) + t("y", str(y))) for x, y in rows))


def _path_cells(rows: list[tuple[int, int]]) -> bytes:
    """A ``RiverPathBlockings`` list of those half-tile cells (fixed point at the cell's centre)."""
    from test_isd1701 import cdata

    items = b"".join(
        t("i", t("data", cdata(struct.pack("<ii", (2 * x + 1) * 1024, (2 * z + 1) * 1024)))) for x, z in rows
    )
    return t("RiverPathBlockings", items)


@pytest.mark.parametrize("numbered", [False, True])
def test_a_moved_river_frees_the_tiles_it_left_and_takes_the_tiles_it_covers_and_keeps_the_rest(numbered):
    import numpy as np

    from annotoolbox.editor.formats.isc import blockings

    def ribbon(z: float):
        return np.array([[20.0, z], [230.0, z]]), np.array([20.0, 20.0])

    old, new = ribbon(100.0), ribbon(200.0)
    tiles = sorted(blockings.ribbon_gaps(old, TILE, blockings.TILE_REACH, 8))
    on_old = [c for c, g in blockings.ribbon_gaps(old, TILE, blockings.TILE_REACH, 8).items() if g <= 0]
    elsewhere = (7, 0)  # a tile of some other river: far from this one, it stays
    cells = [(x, 6) for x in range(2, 14)]
    header = isd1701.Island1701(
        isd_bytes(), t("Rivers") + _blocked(on_old + [elsewhere], numbered) + _path_cells(cells)
    ).header
    report = blockings.redraw(header, 8, old, new, [])
    left = {(int(i.value("x")), int(i.value("y"))) for i in isd1701.list_items(header.find("RiverBlockings"))}
    assert tiles and report["RiverBlockings"]["removed"] == len(on_old) and elsewhere in left
    assert all(y != 3 for _x, y in left - {elsewhere}) and {(x, 6) for x in range(1, 7)} <= left
    assert report["RiverPathBlockings"]["removed"] == len(cells) and report["RiverPathBlockings"]["added"] > 0
    count = header.find("RiverBlockings").find("arraysize")
    assert (count is not None) == numbered and (count is None or int(count.text) == len(left))
    # the same ribbon again changes nothing
    assert blockings.redraw(header, 8, new, new, []) == {
        "RiverBlockings": {"removed": 0, "added": 0},
        "RiverPathBlockings": {"removed": 0, "added": 0},
    }


def test_a_river_edit_redraws_the_island_headers_lists_and_an_edit_of_another_river_leaves_them():
    isl = island()
    before = isl.write_header()
    isc_edit.apply(isl, {"coast": {"0": {"points": [[0.0, 0.5], [2.0, 0.5]]}}})
    assert isl.header.find("RiverBlockings") is not None
    move = {"rivers": {"0": {"points": [[0.5, 0.6, 0.0], [1.5, 0.6, 0.0]], "widths": [0.5, 0.5]}}}
    report = isc_edit.apply(isl, move)
    assert report["RiverBlockings removed"] + report["RiverBlockings added"] > 0 and before != isl.write_header()
