"""Edits of a 117 island and its registration (``editor.formats.a7m.edit117``, ``islands.islandedits``)."""

import struct
from pathlib import Path

import pytest
from tests.fakes import FakeFiles
from tests.islands.islandfakes import grid_tag as _grid
from tests.islands.islandfakes import island117_bytes as _island

from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import SAttrib, STag
from annotoolbox.islands import islandedits
from annotoolbox.islands.islandgrid import read_bits

SOURCE = "data/base/provinces/roman/islands/pool/isle/isle.a7m"


def test_a_117_island_takes_object_and_forest_edits():
    a7m, info = _island()
    files = FakeFiles({SOURCE: a7m, SOURCE[:-4] + ".a7minfo": info})
    edits = islandedits.empty()
    edits["objects"][islandedits.key("data/a/rock.cfg", (1, 0, 1))] = {"deleted": True}
    edits["objects"][islandedits.key("data/a/tree.cfg", (2, 0, 2))] = {"position": [3, 0, 3]}
    edits["forest"] = {"1,2": 1}
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    out, report = done.a7m, done.report
    assert done.gamedata == {}  # no ground edit: nothing of the render data is written
    assert report["objects deleted"] == 1 and report["objects changed"] == 1 and report["forest"]["planted"] == 1
    back = a7m_edit117.CIslandFile(out, info)
    (o,) = [o for _, _, o, _ in islandedits._objects(back)]
    assert struct.unpack("<3f", o.find("Position").data) == (3, 0, 3)
    assert read_bits(back.game_session_manager.path("RegrowManager/TreeMap"))[2][1] == 1


def test_a_new_island_is_registered_next_to_its_original():
    assets = (
        "<Assets><Asset><Template>RandomIsland</Template><Values><Standard><GUID>42376</GUID><Name>medium</Name>"
        f"</Standard><RandomIsland><FilePath>{SOURCE}</FilePath></RandomIsland></Values></Asset></Assets>"
    )
    assert "<GUID>42376</GUID>" in a7m_edit117.random_island(assets, SOURCE)
    assert a7m_edit117.random_island(assets, "data/other.a7m") is None
    assert a7m_edit117.edit_name(SOURCE) == "117_isle.json" and a7m_edit117.new_name(SOURCE) == "isle_edit"


class FakeIslandFiles(FakeFiles):
    """``islandview.CFiles`` as the build reads it: ``list`` is ``iter_paths``."""

    def list(self, prefix: str) -> list[str]:
        return self.iter_paths(prefix)


def test_a_blank_island_is_cleared_named_registered_as_set_and_checked(tmp_path):
    a7m, info = _island()
    assets = (
        "<Assets><Asset><Template>RandomIsland</Template><Values><Standard><GUID>42376</GUID><Name>isle</Name>"
        f"</Standard><RandomIsland><FilePath>{SOURCE}</FilePath><IslandType>Normal</IslandType></RandomIsland>"
        "</Values></Asset></Assets>"
    )
    files = FakeIslandFiles(
        {
            SOURCE: a7m,
            SOURCE[:-4] + ".a7minfo": info,
            SOURCE[:-4] + ".a7me": b"<a7me/>",
            "data/base/config/export/assets.xml": assets.encode(),
        }
    )
    edits = tmp_path / "edits"
    meta = {"source": SOURCE, "target": "new", "name": "new128", "clear": True}
    islandedits.save(edits / "117_new128.json", {**meta, "registration": {"IslandType": "Decoration"}})
    out = tmp_path / "anno117-islands"
    lines = []
    (target,) = a7m_edit117.build(files, edits, out, "anno117-islands", "name", lines.append)
    assert target == f"{a7m_edit117.NEW_DIR}/new128/new128.a7m"
    back = a7m_edit117.CIslandFile((out / target).read_bytes(), info)
    assert not list(islandedits._objects(back))  # cleared
    ops = (out / "data/base/config/export/assets.xml").read_text(encoding="utf-8")
    assert "<IslandType>Decoration</IslandType>" in ops and "<IslandType>Normal</IslandType>" not in ops
    assert f"<FilePath>{target}</FilePath>" in ops
    assert "'cleared': {'GameObject': 2" in lines[0] and not [m for m in lines if m.startswith("mod check")]


def test_building_only_ever_clears_the_mods_own_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    keep = tmp_path / "precious"
    keep.mkdir()
    (keep / "file.txt").write_text("mine", encoding="utf-8")
    for out in (keep, tmp_path / "other-name", Path("anno117-islands")):  # not the mod, or relative
        try:
            a7m_edit117.build(FakeFiles({}), tmp_path / "edits", out, "anno117-islands", "name")
            raise AssertionError(f"rebuilt {out}")
        except ValueError as exc:
            assert "refusing" in str(exc)
    for mod_id in ("..", "."):  # a path ending in ".." names its parent's parent: the id would pass a name check
        with pytest.raises(ValueError, match="refusing"):
            a7m_edit117.build(FakeFiles({}), tmp_path / "edits", keep / "mods" / mod_id, mod_id, "name")
    (keep / "precious").mkdir()  # a folder of the mod's name that is not the mod
    (keep / "precious" / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(ValueError, match="no mod of this id"):
        a7m_edit117.build(FakeFiles({}), tmp_path / "edits", keep / "precious", "precious", "name")
    assert (keep / "file.txt").read_text(encoding="utf-8") == "mine"


def test_an_unnamed_new_island_is_registered_under_the_originals_name_marked_edited(tmp_path):
    a7m, info = _island()
    assets = (
        "<Assets><Asset><Template>RandomIsland</Template><Values><Standard><GUID>42376</GUID><Name>isle</Name>"
        f"</Standard><RandomIsland><FilePath>{SOURCE}</FilePath></RandomIsland></Values></Asset></Assets>"
    )
    files = FakeIslandFiles(
        {
            SOURCE: a7m,
            SOURCE[:-4] + ".a7minfo": info,
            SOURCE[:-4] + ".a7me": b"<a7me/>",
            "data/base/config/export/assets.xml": assets.encode(),
        }
    )
    edits = tmp_path / "edits"
    islandedits.save(edits / "117_x.json", {"source": SOURCE, "target": "new"})
    out = tmp_path / "anno117-islands"
    (target,) = a7m_edit117.build(files, edits, out, "anno117-islands", "name", lambda _m: None)
    ops = (out / "data/base/config/export/assets.xml").read_text(encoding="utf-8")
    assert "isle (edited)</Name>" in ops and f"<FilePath>{target}</FilePath>" in ops


def test_the_build_applies_an_area_edit_once_and_lists_a_moved_slot_in_the_info_file():
    from annotoolbox.islands import slotlist
    from annotoolbox.islands.islandgrid import AREA_ISLAND, AREA_NONE, read_sparse, write_sparse

    header = STag("block", [SAttrib("x", (16).to_bytes(2, "little"))])
    ids = STag("AreaIDs", [SAttrib("x", (4).to_bytes(4, "little")), SAttrib("y", (4).to_bytes(4, "little")), header])
    write_sparse(ids, [[AREA_ISLAND] * 4 for _ in range(4)])
    listing = STag("ObjectMetaInfo", [STag("RandomSlotObjects", [])])
    slot = struct.pack("<I", 1) + bytes([1, 0, 0, 0])  # the id of the island's first object, its one slot
    assert slotlist.write(STag("holder", [listing]), {37676: [(slot, (1.0, 0.0, 1.0))]})
    a7m, info = _island((ids,), (listing,))
    files = FakeFiles({SOURCE: a7m, SOURCE[:-4] + ".a7minfo": info})
    edits = {**islandedits.empty(), "area": {"0,0": 0, "9,9": 1}}
    edits["objects"][islandedits.key("data/a/rock.cfg", (1, 0, 1))] = {"position": [3, 0, 2]}
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    assert done.report["area tiles"] == 1 and done.report["unmatched"] == ["area tile 9,9"]  # once, not twice
    assert done.report["slot list"] == {"moved": 1, "added": 1}  # the tree object shares the asset: a slot too
    assert read_sparse(a7m_edit117.CIslandFile(done.a7m, info).game_session_manager.path("AreaIDs"))[0][0] == AREA_NONE
    written = filedb.read(done.info)  # the info file is written because the list changed
    tree = struct.pack("<I", 2) + bytes([1, 0, 0, 0])
    assert slotlist.read(written) == {37676: [(slot, (3.0, 0.0, 2.0)), (tree, (2.0, 0.0, 2.0))]}


def test_herd_tiles_painted_in_a_patch_under_the_minimum_are_named_in_the_report():
    from annotoolbox.islands import herdareas

    entry = STag("#1", [SAttrib("CellsCount", bytes(4)), SAttrib("BBox", bytes(8)), SAttrib("RandomPoint", bytes(8))])
    animals = STag("AnimalManager", [_grid("HerdGrid", 4, bytes(16)), STag("HerdAreas", [entry])])
    a7m, info = _island((animals,))
    files = FakeFiles({SOURCE: a7m, SOURCE[:-4] + ".a7minfo": info})
    edits = {**islandedits.empty(), "herd": {"1,1": 1, "2,1": 1}}
    report = a7m_edit117.apply(files, SOURCE, edits, template=None).report
    assert report["herd tiles"] == 2
    assert report["unmatched"] == [f"2 herd tiles in patches under {herdareas.HERD_AREA_MIN} tiles were not kept"]
