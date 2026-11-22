"""Models, data files, language texts, UI files, baked particles and islands exported as glTF, JSON and CSV."""

from __future__ import annotations

import csv
import io
import json
import struct
import uuid
from pathlib import Path

import pytest
from tests.exporting.exportingfakes import doc_bytes, folder
from tests.formats.formatfakes import rdp_file as baked_file
from tests.rdmfiles import animation_file, skinned_file, static_file

from annotoolbox.exporting import run
from annotoolbox.formats import filedb, isd2070, phoenix, phoenixedit, phoenixwrite, rda, rdp117
from annotoolbox.formats.filedb import SAttrib, STag
from annotoolbox.formats.gltf import read_gltf


def entry_of(tmp_path, files, path, formats, options=None):
    source = folder(tmp_path, files)
    (entry,) = run.export_path(source, path, formats, tmp_path / "out", options)
    return entry, tmp_path / "out"


def test_an_rdm_becomes_a_glb_its_lods_and_animations_go_along_when_asked(tmp_path):
    files = {
        "m/house_lod0.rdm": static_file(),
        "m/house_lod1.rdm": static_file(),
        "m/crane_lod0.rdm": skinned_file(),
        "m/anim/crane_work.rdm": animation_file(),
    }
    entry, out = entry_of(tmp_path, files, "m/house_lod0.rdm", {"glb"})
    assert entry.status == "written" and entry.files == ["m/house_lod0.rdm", "m/house_lod0.glb"]
    doc = read_gltf(out / "m/house_lod0.glb")
    assert doc.doc["meshes"]
    entry, out = entry_of(tmp_path, files, "m/house_lod0.rdm", {"gltf"}, run.SOptions(lods=True))
    assert {"m/house_lod0.gltf", "m/house_lod0.bin"} <= set(entry.files)
    skinned, out = entry_of(tmp_path, files, "m/crane_lod0.rdm", {"glb"}, run.SOptions(anims_folder="m/anim"))
    assert skinned.status == "written" and read_gltf(out / "m/crane_lod0.glb").doc.get("animations")


def test_a_config_exports_the_meshes_it_names_and_says_which_it_could_not(tmp_path):
    parts = b"<ConfigType>MODEL</ConfigType><FileName>%s</FileName>"
    cfg = (
        b"<Config><Models><Config>" + parts % b"m/house_lod0.rdm" + b"</Config>"
        b"<Config>" + parts % b"m\\gone.rdm" + b"</Config></Models></Config>"
    )
    entry, out = entry_of(tmp_path, {"c/house.cfg": cfg, "m/house_lod0.rdm": static_file()}, "c/house.cfg", {"glb"})
    assert "m/house-house_lod0.glb" in entry.files and (out / "m/house-house_lod0.glb").is_file()
    assert any("1 of 2 meshes" in n for n in entry.notes) and any("m/gone.rdm" in n for n in entry.notes)


@pytest.mark.parametrize("compress", [False, True])
def test_a_granny_file_becomes_a_gltf_of_its_meshes(tmp_path, compress):
    from tests.formats.formatfakes import granny_file

    entry, out = entry_of(tmp_path, {"m/a.gr2": granny_file(compress=compress)}, "m/a.gr2", {"glb"})
    assert entry.status == "written", entry.notes
    glb = (out / "m/a.glb").read_bytes()
    doc = json.loads(glb[20 : 20 + struct.unpack_from("<I", glb, 12)[0]])
    assert glb[:4] == b"glTF" and doc["meshes"] and any(a.get("count") == 4 for a in doc["accessors"])
    entry, out = entry_of(tmp_path / "gltf", {"m/a.gr2": granny_file(compress=compress)}, "m/a.gr2", {"gltf"})
    assert "m/a.gltf" in entry.files  # through the RDM toolchain: both forms


def test_a_damaged_granny_file_is_a_failed_entry_and_still_written_as_stored(tmp_path):
    entry, out = entry_of(tmp_path, {"m/b.gr2": b"not a granny file" * 4}, "m/b.gr2", {"glb"})
    assert entry.status == "failed" and "m/b.gr2" in entry.files
    assert (out / "m/b.gr2").is_file() and not (out / "m/b.glb").exists()


def test_a_broken_model_is_a_failed_entry(tmp_path):
    entry, out = entry_of(tmp_path, {"m/bad.rdm": b"not an rdm"}, "m/bad.rdm", {"glb"})
    assert entry.status == "failed" and (out / "m/bad.rdm").is_file()


def test_a_filedb_document_becomes_json_with_small_values_and_blobs_summarised(tmp_path):
    entry, out = entry_of(tmp_path, {"d/level.ifo": doc_bytes()}, "d/level.ifo", {"json"})
    tree = json.loads((out / "d/level.json").read_text(encoding="utf-8"))
    attributes = {a["name"]: a for a in tree["children"][0]["children"]}
    assert attributes["Name"]["text"] == "Moon" and attributes["Count"]["u32"] == 7
    assert attributes["Blob"]["size"] == 200 and attributes["Blob"]["truncated"] and entry.status == "written"


ASSETS = b"""<?xml version="1.0" encoding="utf-8"?>
<AssetList><Groups><Group><Assets>
<Asset><Template>Residence</Template><Values><Standard><GUID>100</GUID><Name>Hut</Name></Standard><Cost><Gold>5</Gold></Cost></Values></Asset>
<Asset><Template>Farm</Template><Values><Standard><GUID>101</GUID><Name>Barn</Name></Standard></Values></Asset>
</Assets></Group></Groups></AssetList>"""


def test_an_xml_file_becomes_json_and_its_asset_table_a_csv(tmp_path):
    entry, out = entry_of(tmp_path, {"data/assets.xml": ASSETS}, "data/assets.xml", {"json", "csv"})
    tree = json.loads((out / "data/assets.json").read_text(encoding="utf-8"))
    assert tree["tag"] == "AssetList" and tree["children"][0]["tag"] == "Groups"
    rows = list(csv.DictReader(io.StringIO((out / "data/assets.csv").read_text(encoding="utf-8"))))
    assert [(r["GUID"], r["Template"], r["Name"]) for r in rows] == [
        ("100", "Residence", "Hut"),
        ("101", "Farm", "Barn"),
    ]
    assert rows[0]["Values.Cost.Gold"] == "5" and any("2 rows" in n for n in entry.notes)


def test_an_info_or_feedback_file_in_xml_text_becomes_json_like_any_xml(tmp_path):
    entry, out = entry_of(tmp_path, {"d/x.ifo": b"\xef\xbb\xbf<Info><Name>box</Name></Info>"}, "d/x.ifo", {"json"})
    assert entry.status == "written", entry.notes
    assert json.loads((out / "d/x.json").read_text(encoding="utf-8"))["tag"] == "Info"


def test_a_text_file_has_no_other_form_and_a_broken_xml_fails_alone(tmp_path):
    entry, out = entry_of(tmp_path, {"a/s.lua": b"x = 1"}, "a/s.lua", {"json"})
    assert entry.status == "written" and entry.files == ["a/s.lua"] and "no converted form" in entry.notes[0]
    entry, out = entry_of(tmp_path / "b", {"a/bad.xml": b"<a><b></a>"}, "a/bad.xml", {"json"})
    assert entry.status == "failed" and (out / "a/bad.xml").is_file()


def test_language_texts_become_a_csv_per_file_and_the_language_option_filters(tmp_path):
    english = '# comment\n1=Hello\n2=World, "quoted"\n'.encode("utf-16")
    files = {"data/loca/english/txt/a.txt": english, "data/loca/german/txt/a.txt": "1=Hallo\n".encode("utf-16")}
    entry, out = entry_of(tmp_path, files, "data/loca/english/txt/a.txt", {"csv"})
    rows = list(csv.reader(io.StringIO((out / "data/loca/english/txt/a.csv").read_text(encoding="utf-8"))))
    assert rows == [["guid", "text"], ["1", "Hello"], ["2", 'World, "quoted"']]
    other, out2 = entry_of(
        tmp_path / "x", files, "data/loca/german/txt/a.txt", {"csv"}, run.SOptions(language="english")
    )
    assert not (out2 / "data/loca/german/txt/a.csv").exists() and "skipped" in other.notes[0]


def phoenix_file() -> bytes:
    schema = phoenix.load_schema()
    versions = {h: str(uuid.uuid5(uuid.NAMESPACE_URL, h)) for h in phoenix.full_schema(schema)}
    label = phoenixedit.new_object(schema, "41a404e5", guid=uuid.uuid4().bytes_le)
    phoenixedit.set_name(label, "Value")
    root = phoenixedit.new_object(schema, phoenixedit.WIDGET_TEMPLATE, guid=bytes(range(16)), root=True)
    wf = phoenixedit.CWidgetFile(schema, root, versions=versions)
    wf.add_instance(label)
    wf.finish("export")
    return phoenixwrite.write_file(schema, "WI", root, versions)


def test_a_phoenix_ui_file_becomes_json_of_its_decoded_tree(tmp_path):
    entry, out = entry_of(tmp_path, {"ui/a.ui": phoenix_file()}, "ui/a.ui", {"json"})
    assert entry.status == "written", entry.notes
    doc = json.loads((out / "ui/a.json").read_text(encoding="utf-8"))
    assert (
        doc["file"] == "ui/a.ui"
        and doc["root"]["type"]
        and any("Label" in name or name for name in doc["types"].values())
    )
    bad, _out = entry_of(tmp_path / "bad", {"ui/b.ui": b"PHX12 not a file"}, "ui/b.ui", {"json"})
    assert bad.status == "failed"


def test_a_baked_particle_file_becomes_json(tmp_path):
    entry, out = entry_of(tmp_path, {"fx/a.rdp": rdp117.write_rdp(baked_file())}, "fx/a.rdp", {"json"})
    doc = json.loads((out / "fx/a.json").read_text(encoding="utf-8"))
    assert doc["frame_count"] == 8 and len(doc["particles"]) == 1 and doc["particles"][0]["ticks"] == 4
    assert entry.status == "written"


def test_a_2070_island_gives_its_height_map_and_object_list(tmp_path):
    pytest.importorskip("numpy")
    island = isd2070.flat_island(64, 5.0)
    entry, out = entry_of(
        tmp_path, {"maps/flat.isd": isd2070.serialize(island.m_root)}, "maps/flat.isd", {"json", "csv", "png16", "exr"}
    )
    assert entry.status == "written", entry.notes
    assert {"maps/flat.json", "maps/flat_objects.csv", "maps/flat_height.png", "maps/flat_height.exr"} <= set(
        entry.files
    )
    doc = json.loads((out / "maps/flat.json").read_text(encoding="utf-8"))
    assert doc["width"] == 64 and doc["objects"] == []
    width, height = struct.unpack_from(">II", (out / "maps/flat_height.png").read_bytes(), 16)
    assert (width, height) == (65, 65) and any("height png" in n for n in entry.notes)


def test_a_117_island_archive_lists_its_members_and_dumps_its_filedb_members(tmp_path):
    dest = tmp_path / "pack" / "i.a7m"
    with rda.CRdaWriter(dest) as w:
        w.add_block([("data/gamedata.data", doc_bytes()), ("data/notes.bin", b"plain bytes")])
    entry, out = entry_of(tmp_path, {"maps/i.a7m": dest.read_bytes()}, "maps/i.a7m", {"json", "png16"})
    assert entry.status == "written", entry.notes
    members = json.loads((out / "maps/i/members.json").read_text(encoding="utf-8"))
    assert {m["name"] for m in members} == {"data/gamedata.data", "data/notes.bin"}
    assert (out / "maps/i/data/gamedata.data.json").is_file() and any("height map" in n for n in entry.notes)


def synthetic_island(tmp_path) -> bytes:
    """A 117 island archive with a 3 x 3 coarse height map (steps 0, 256, 512 ...) and two objects in one area."""
    steps = struct.pack("<9h", *(256 * i for i in range(9)))
    rd3d = STag(
        "",
        [STag("Terrain", [STag("CoarseHeightMap", [SAttrib("width", struct.pack("<I", 3)), SAttrib("map", steps)])])],
    )
    cfg = "data/graphics/a.cfg".encode("utf-16-le")
    house = STag(
        "#1",
        [
            SAttrib("guid", struct.pack("<I", 1000)),
            SAttrib("Variation", struct.pack("<I", 2)),
            SAttrib("Position", struct.pack("<3f", 1.5, 2.0, 3.5)),
            STag(
                "Mesh",
                [SAttrib("Orientation", struct.pack("<4f", 0, 1, 0, 0)), SAttrib("Scale", struct.pack("<f", 1.25))],
            ),
        ],
    )
    bush = STag(
        "#1",
        [
            SAttrib("guid", struct.pack("<I", 2000)),
            SAttrib("Position", struct.pack("<3f", 9, 8, 7)),
            STag("DynamicVariation", [SAttrib("ConfigFilename", cfg)]),
        ],
    )
    area = filedb.write(STag("", [STag("AreaObjectManager", [STag("GameObject", [STag("objects", [house, bush])])])]))
    manager = STag("GameSessionManager", [STag("AreaManagerData", [STag("#1", [SAttrib("Data", area)])])])
    gamedata = STag("", [manager])
    dest = tmp_path / "pack" / "j.a7m"
    with rda.CRdaWriter(dest) as w:
        w.add_block([("data/rd3d.data", filedb.write(rd3d)), ("data/gamedata.data", filedb.write(gamedata))])
    return dest.read_bytes()


def test_a_117_island_gives_its_height_map_and_its_object_list(tmp_path):
    pytest.importorskip("numpy")
    data = synthetic_island(tmp_path)
    entry, out = entry_of(tmp_path, {"maps/j.a7m": data}, "maps/j.a7m", {"json", "csv", "png16", "exr"})
    assert entry.status == "written", entry.notes
    assert {"maps/j_height.png", "maps/j_height.exr", "maps/j_objects.csv", "maps/j_objects.json"} <= set(entry.files)
    width, height = struct.unpack_from(">II", (out / "maps/j_height.png").read_bytes(), 16)
    assert (width, height) == (3, 3) and any("height map 3 x 3" in n for n in entry.notes)
    exr = (out / "maps/j_height.exr").read_bytes()
    assert struct.pack("<f", 8.0) in exr  # step 2048 / 64 / 4 world units
    rows = list(csv.DictReader(io.StringIO((out / "maps/j_objects.csv").read_text(encoding="utf-8"))))
    assert [r["guid"] for r in rows] == ["1000", "2000"] and rows[0]["scale"] == "1.25"
    assert rows[0]["rotation"] == "0 1 0 0" and rows[1]["config"] == "data/graphics/a.cfg"
    doc = json.loads((out / "maps/j_objects.json").read_text(encoding="utf-8"))
    assert doc["objects"][0]["variation"] == 2 and doc["objects"][0]["x"] == 1.5


def test_a_117_island_without_a_height_block_says_so_and_still_lists_its_objects(tmp_path):
    dest = tmp_path / "pack" / "k.a7m"
    with rda.CRdaWriter(dest) as w:
        w.add_block([("data/rd3d.data", filedb.write(STag("", [STag("Terrain", [])])))])
    entry, _ = entry_of(tmp_path, {"maps/k.a7m": dest.read_bytes()}, "maps/k.a7m", {"png16", "csv"})
    assert any("no height map" in n for n in entry.notes) and any("no object list" in n for n in entry.notes)


def test_an_unconverted_kind_exports_as_stored_only(tmp_path):
    entry, out = entry_of(tmp_path, {"f/a.ttf": b"font"}, "f/a.ttf", {"json", "png"})
    assert entry.files == ["f/a.ttf"] and Path(out / "f/a.ttf").read_bytes() == b"font"


def test_the_island_reader_finds_members_by_path_then_by_base_name_from_bytes_or_an_archive(tmp_path):
    from annotoolbox.formats import a7mread

    data = synthetic_island(tmp_path)
    by_name, exact = a7mread.members(data, "RD3D.data", "data/gamedata.data")
    assert by_name == a7mread.members(rda.CRdaArchive(tmp_path / "pack" / "j.a7m"), "data/rd3d.data")[0]
    assert exact[:1] != b"" and a7mread.members(data) == []
    with pytest.raises(a7mread.CIslandError):
        a7mread.members(data, "nothing.data")


def test_the_island_reader_walks_the_areas_groups_and_the_coarse_steps(tmp_path):
    from annotoolbox.formats import a7mread

    data = synthetic_island(tmp_path)
    rd3d, gamedata = (filedb.read(b) for b in a7mread.members(data, "rd3d.data", "gamedata.data"))
    width, steps = a7mread.coarse_steps(rd3d)
    assert (width, list(steps)) == (3, [256 * i for i in range(9)])
    session = gamedata.path("GameSessionManager")
    ((_area, doc),) = list(a7mread.area_docs(session))
    assert a7mread.area_objects(doc, "GameObject") is not None and a7mread.area_objects(doc, "Prop") is None
    found = list(a7mread.object_tags(session))
    assert [group for group, _ in found] == ["GameObject", "GameObject"]
    assert list(a7mread.object_tags(session, ("Prop",))) == []
    assert [o.guid for o in a7mread.objects(data)] == [1000, 2000]
    with pytest.raises(a7mread.CIslandError):
        a7mread.coarse_steps(filedb.STag("", [STag("Terrain", [])]))
