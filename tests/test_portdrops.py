"""What an island port leaves out or swaps is said by name and count (docs/island-regions.md "What a port does not
carry"): the ledger, props and objects the target game lacks, source-only island data, and the area ids a donor has no
area for (the Cinis crash)."""

import struct

import numpy as np

from annotoolbox.editor import gametransfer, portdrops, regionconvert
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.islands.regiontables import Tables


def u32(n: int) -> bytes:
    return struct.pack("<I", n)


def session_with_areas(keys: list[int]) -> Tag:
    """A session whose ``AreaManagerData`` lists areas ``keys`` as the games write them: a key, then the area."""
    kids = []
    for k in keys:
        kids += [Attrib("#32768", struct.pack("<H", k)), Tag("#1", [Attrib("ByteCount", u32(0))])]
    return Tag("GameSessionManager", [Tag("AreaManagerData", kids)])


def test_tiles_of_an_area_the_donor_lacks_go_to_its_first_area_and_are_reported():
    gm = session_with_areas([0x2001, 0x1])
    values = np.array([[0, 1, 0x2001], [0x4001, 0x4001, 1]], np.uint16)
    ledger = portdrops.Ledger()
    fitted = gametransfer.fit_areas(gm, values, ledger)
    assert set(np.unique(fitted).tolist()) == {0, 1, 0x2001}  # every id names an area the island has (0: none)
    assert fitted[1, 0] == fitted[1, 1] == 0x2001
    assert values[1, 0] == 0x4001  # the caller's array is not changed
    (entry,) = ledger.entries
    assert entry["category"] == "area" and entry["count"] == 2 and "0x4001" in entry["what"]


def test_area_ids_the_island_has_are_left_as_they_are():
    gm = session_with_areas([0x2001, 0x4001, 0x1])
    values = np.array([[0, 1, 0x2001, 0x4001]], np.uint16)
    ledger = portdrops.Ledger()
    assert (gametransfer.fit_areas(gm, values, ledger) == values).all()
    assert not ledger.entries


def prop_tree(files: list[str], used: list[int]) -> Tag:
    names = Tag("FileNames", [Attrib("#1", f.encode("utf-16-le")) for f in files])
    inst = Tag("Instances", [Tag("#1", [Attrib("Index", u32(i))]) for i in used])
    return Tag("", [Tag("PropGrid", [names, inst])])


def test_a_prop_file_the_target_game_lacks_is_removed_and_named_with_its_count():
    rd = prop_tree(["data/a/there.cfg", "data\\a\\gone.cfg", "data/a/renamed.cfg"], [0, 1, 1, 1, 2, 2])
    ledger = portdrops.Ledger()
    exists = lambda path: path != "data/a/gone.cfg"  # noqa: E731
    report = regionconvert.apply_props(rd, {"data/a/renamed.cfg": "data/b/new.cfg"}, ledger, exists)
    assert report["props removed"] == 3
    found = {(e["what"].split(" (")[0], e["fate"]): e for e in ledger.entries}
    assert found["a/gone.cfg", portdrops.LEFT_OUT]["count"] == 3
    assert "no such file" in next(e["what"] for e in ledger.entries if e["fate"] == portdrops.LEFT_OUT)
    assert found["a/renamed.cfg", portdrops.MAPPED]["to"] == "b/new.cfg"
    assert not any(e["what"].startswith("a/there") for e in ledger.entries)  # carried as it is: nothing to say


def test_an_object_no_table_names_stays_only_where_the_target_knows_the_same_asset():
    src = {7: ("Slot", "Slot Roman Mountain"), 8: ("Coastline", "Coastline")}.get
    dst = {7: ("Cart", "another asset with the number 7"), 8: ("Coastline", "Coastline")}.get
    assert portdrops.object_kept(src, dst, 8, same_game=False)
    assert not portdrops.object_kept(src, dst, 7, same_game=False)  # a GUID is one asset only within a game
    assert not portdrops.object_kept(src, dst, 99, same_game=False)  # unknown on the source's side too
    assert portdrops.object_kept(src, dst, 7, same_game=True)


def test_source_data_the_target_keeps_from_its_donor_or_lacks_is_listed_by_node_and_size():
    src = Tag("S", [Tag("Own", [Attrib("v", b"\x01\x02")]), Tag("OnlyHere", [Attrib("v", b"\x01")]),
                    Tag("Empty", [Attrib("v", b"\x00\x00")]), Tag("Done", [Attrib("v", b"\x09")])])  # fmt: skip
    dst = Tag("D", [Tag("Own", [Attrib("v", b"\x05\x05")]), Tag("Empty", [Attrib("v", b"\x00")]),
                    Tag("Done", [Attrib("v", b"\x00")])])  # fmt: skip
    ledger = portdrops.Ledger()
    portdrops.untaken(src, dst, {"Done"}, "", ledger)
    found = {e["what"]: (e["fate"], e["count"]) for e in ledger.entries}
    assert found == {"Own": (portdrops.DONOR, 2), "OnlyHere": (portdrops.LEFT_OUT, 1)}


def test_a_graft_names_what_only_the_source_has():
    src = Tag(
        "TerrainManager",
        [Tag("HeightMap", [Attrib("v", b"\x01")]), Tag("DisplacementHeightMap", [Attrib("v", b"\x07\x07")])],
    )
    dst = Tag("TerrainManager", [Tag("HeightMap", [Attrib("v", b"\x00")])])
    ledger = portdrops.Ledger()
    done = gametransfer.graft_paths(Tag("", [src]), Tag("", [dst]), ("TerrainManager",), ledger)
    assert done == ["TerrainManager"] and dst.find("HeightMap").find("v").data == b"\x01"
    (entry,) = ledger.entries
    assert entry["what"] == "/TerrainManager/DisplacementHeightMap" or entry["what"].endswith("DisplacementHeightMap")
    assert entry["fate"] == portdrops.LEFT_OUT and entry["count"] == 2


def test_the_ledger_adds_up_one_entry_per_case_and_the_log_shows_the_first_of_each_kind():
    ledger = portdrops.Ledger()
    for _ in range(3):
        ledger.add("object", "Slot/x", portdrops.LEFT_OUT)
    for k in range(20):
        ledger.add("prop", f"p{k}", portdrops.LEFT_OUT, 20 - k)
    entries = ledger.entries
    assert entries[0] == {"category": "object", "what": "Slot/x", "fate": "left out", "count": 3, "to": ""}
    assert portdrops.totals(entries) == {"object left out": 3, "prop left out": sum(range(1, 21))}
    shown = portdrops.lines(entries)
    assert len([x for x in shown if x.startswith("prop ")]) == portdrops.SHOWN
    assert len(portdrops.lines(entries, None)) == 21  # the report file lists all of them


def test_tables_with_no_change_report_nothing():
    ledger = portdrops.Ledger()
    isl = type("Isl", (), {"_areas_data": lambda self: [], "rd": Tag("", [])})()
    assert regionconvert.apply_objects(isl, {}, ledger, None) == {"objects changed": 0, "objects removed": 0}
    assert not ledger.entries and Tables().props == {}


def test_an_1800_port_mod_has_the_modinfo_1800s_loader_reads_and_a_117_one_keeps_117s_fields(tmp_path):
    import json

    from annotoolbox.editor import islandconvert

    old = json.loads(islandconvert._modinfo(tmp_path, "p", "P", "anno1800"))
    assert old["ModID"] == "p" and old["Version"] == "0.1.0"
    assert not {"Anno", "Difficulty", "GameSetup", "Scripts"} & set(old)  # 1800 has none of 117's game fields
    new = json.loads(islandconvert._modinfo(tmp_path, "p", "P", "anno117"))
    assert new["Anno"] == 8 and "GameSetup" in new
