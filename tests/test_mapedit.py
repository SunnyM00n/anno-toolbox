"""The map editor on synthetic maps: a template read and written back byte for byte, edits, the level header, a session
grown with every reference moved, the checks, the older games' worlds, the mod's files, the project share and the
window offscreen (docs/map-editor.md)."""

from __future__ import annotations

import json
import struct

import pytest

from annotoolbox.formats import filedb, rda
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.mapedit import (
    grow,
    levelfile,
    mapdoc,
    mapmod,
    pool,
    projectpart,
    rules,
    sessionfile,
    template,
    worlds,
)
from annotoolbox.mapedit.references import KEPT, MOVED, Report
from annotoolbox.mapedit.template import KIND_FIXED, KIND_SLOT, KIND_START
from annotoolbox.sources import gametree
from annotoolbox.suite.project import Project

STEM = "data/base/provinces/roman/templates/pool/t_01/t_01"
ISLAND = "data/base/provinces/roman/islands/pool/i_01/i_01.a7m"


def _i(*v: int) -> bytes:
    return struct.pack(f"<{len(v)}i", *v)


def _h(v: int) -> bytes:
    return struct.pack("<H", v)


def slot(x: int, z: int, size: int = 0, kind: int = 0) -> Tag:
    config = Tag("Config", [Tag("Type", [Attrib("id", _h(kind))] if kind else []), Tag("Difficulty", [])])
    element = Tag(
        "Element",
        [Attrib("Position", _i(x, z)), *([Attrib("Size", _h(size))] if size else []), Tag("Difficulty", []), config],
    )
    return Tag("TemplateElement", [Attrib("ElementType", struct.pack("<I", 1)), element])


def start(x: int, z: int) -> Tag:
    return Tag(
        "TemplateElement", [Attrib("ElementType", struct.pack("<I", 2)), Tag("Element", [Attrib("Position", _i(x, z))])]
    )


def fixed(x: int, z: int, path: str = ISLAND, rotation: int = 0, kind: int = 0) -> Tag:
    config = Tag(
        "RandomIslandConfig",
        [Tag("value", [Tag("Type", [Attrib("id", _h(kind))]), Tag("Difficulty", [Attrib("id", _h(0))])])],
    )
    element = Tag(
        "Element",
        [
            Attrib("Position", _i(x, z)),
            Attrib("MapFilePath", path.encode("utf-16-le")),
            Attrib("Rotation90", bytes([rotation])),
            Attrib("IslandLabel", b"isle"),
            Attrib("FertilityGuids", b""),
            Attrib("RandomizeFertilities", b"\x00"),
            Tag("MineSlotActivation", []),
            config,
        ],
    )
    return Tag("TemplateElement", [element])


def template_bytes(version: int = 3, size: int = 2048, elements=None) -> bytes:
    elements = (
        elements if elements is not None else [fixed(800, 800), slot(200, 200, 1), slot(1200, 300), start(600, 1500)]
    )
    mt = Tag(
        "MapTemplate",
        [
            Attrib("Size", _i(size, size)),
            Attrib("PlayableArea", _i(20, 20, size - 28, size - 28)),
            Attrib("InitialPlayableArea", _i(0, 0, 0, 0)),
            Tag("RandomlyPlacedThirdParties", []),
            Attrib("ElementCount", struct.pack("<I", len(elements))),
            *elements,
        ],
    )
    return filedb.write(Tag("", [mt]), version=version)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_a_template_reads_and_writes_back_byte_for_byte(version):
    raw = template_bytes(version)
    t = template.read(raw)
    assert t.size == (2048, 2048) and t.playable == (20, 20, 2020, 2020)
    kinds = [(e.kind, e.x, e.z, e.size) for e in t.elements]
    assert kinds == [
        (KIND_FIXED, 800, 800, 0),
        (KIND_SLOT, 200, 200, 1),
        (KIND_SLOT, 1200, 300, 0),
        (KIND_START, 600, 1500, 0),
    ]
    assert t.elements[0].map_file_path == ISLAND and t.elements[0].island_label == "isle"
    assert t.render() == raw


@pytest.mark.parametrize("version", [1, 3])
def test_edits_change_only_what_was_edited(version):
    t = template.read(template_bytes(version))
    t.elements[1].x, t.elements[1].size, t.elements[2].type = 264, 0, 4
    t.elements[0].rotation90 = 3
    del t.elements[3]
    t.elements.append(template.new_element(KIND_START, 999, 1001))
    t.elements.append(template.new_element(KIND_SLOT, 1000, 1000, size=2, type=1))
    back = template.read(t.render())
    assert [(e.kind, e.x, e.z, e.size, e.type) for e in back.elements] == [
        (KIND_FIXED, 800, 800, 0, 0),
        (KIND_SLOT, 264, 200, 0, 0),
        (KIND_SLOT, 1200, 300, 0, 4),
        (KIND_START, 1000, 1000, 0, 0),
        (KIND_SLOT, 1000, 1000, 2, 1),
    ]
    assert back.elements[0].rotation90 == 3
    assert struct.unpack("<I", back.root.find("ElementCount").data)[0] == 5
    assert back.elements[1].tag.path("Element/Size") is None  # Small is written as no Size, as vanilla does


def test_a_new_element_of_a_kind_the_template_lacks_takes_the_vanilla_shape():
    t = template.read(template_bytes(elements=[slot(100, 100)]))
    t.elements.append(
        template.new_element(KIND_FIXED, 400, 400, map_file_path=ISLAND, rotation90=1, island_label="new")
    )
    t.elements.append(template.new_element(KIND_START, 50, 50))
    back = template.read(t.render())
    assert (back.elements[1].map_file_path, back.elements[1].rotation90, back.elements[1].island_label) == (
        ISLAND,
        1,
        "new",
    )
    assert back.elements[2].kind == KIND_START


def test_the_level_header_keeps_its_own_lines_and_lists_every_chunk():
    vanilla = levelfile.editor_level(2048).replace(b"<Dimensions>", b"<EditorRegion>2</EditorRegion>\r\n  <Dimensions>")
    assert levelfile.regrown(vanilla, 2048, 2048) == vanilla
    grown = levelfile.regrown(vanilla, 2304, 2112)
    assert levelfile.dimensions(grown) == (2304, 2112) and b"<EditorRegion>2</EditorRegion>" in grown
    assert grown.count(b"<Chunk>") == (2304 // 64) * (2112 // 64) and b"<Chunk>35x32</Chunk>" in grown


# A session of 64 tiles with every kind of absolute reference the inventory lists.
SIZE = 64


def _bits(w: int, h: int, cells: set[tuple[int, int]]) -> bytes:
    stride = (w + 31) // 32 * 32
    out = bytearray(stride * h // 8)
    for x, z in cells:
        k = z * stride + x
        out[k >> 3] |= 1 << (k & 7)
    return bytes(out)


def session_bytes() -> bytes:
    heights = [0] * (2 * SIZE + 1) ** 2
    heights[5 * (2 * SIZE + 1) + 7] = 999  # one marked sample at row 5, column 7
    hm = Tag("HeightMap", [Attrib("Width", struct.pack("<I", 2 * SIZE + 1)), Attrib("Height", struct.pack("<I", 2 * SIZE + 1)),
                           Attrib("HeightMap", struct.pack(f"<{len(heights)}H", *heights))])  # fmt: skip
    water = Tag(
        "Water",
        [
            Attrib("x", struct.pack("<I", SIZE)),
            Attrib("y", struct.pack("<I", SIZE)),
            Attrib("bits", _bits(SIZE, SIZE, {(3, 4)})),
        ],
    )
    env = Tag("EnvironmentGRid", [Attrib("x", struct.pack("<I", SIZE // 4)), Attrib("y", struct.pack("<I", SIZE // 4)),
                                  Attrib("val", bytes(range((SIZE // 4) ** 2)))])  # fmt: skip
    blocks = [
        Tag("block", [Attrib("mode", b"\x01"), Attrib("x", _h(16)), Attrib("y", _h(16)), Attrib("default", _h(0))])
    ]
    for by in range(0, SIZE, 16):
        for bx in range(0, SIZE, 16):
            b = Tag("block", [Attrib("mode", b"\x02")])
            b.children += ([Attrib("x", _h(bx))] if bx else []) + ([Attrib("y", _h(by))] if by else [])
            b.children.append(Attrib("default", _h(1)))
            blocks.append(b)
    blocks.append(Tag("block", [Attrib("mode", b"\x00")]))
    area_ids = Tag(
        "AreaIDs",
        [
            Attrib("SparseEnabled", b"\x01"),
            Attrib("x", struct.pack("<I", SIZE)),
            Attrib("y", struct.pack("<I", SIZE)),
            *blocks,
        ],
    )
    obj = Tag("#1", [Attrib("guid", struct.pack("<I", 5)), Attrib("Position", struct.pack("<3f", 10.5, 2.0, 20.0)),
                     Attrib("QuestAreaRect", _i(1, 2, 3, 4))])  # fmt: skip
    inner = filedb.write(Tag("", [Tag("AreaObjectManager", [Tag("GameObject", [Tag("objects", [obj])])])]), version=3)
    amd = Tag(
        "AreaManagerData",
        [
            Attrib("#32768", _h(1)),
            Tag("#1", [Attrib("ByteCount", struct.pack("<I", len(inner))), Attrib("Data", inner)]),
        ],
    )
    view = Tag("View", [Attrib("From", struct.pack("<3f", 30.0, 50.0, 40.0))])
    cameras = Tag("CameraManager", [Tag("EditorSavedCameraStates", [Tag("#1", [view])])])
    channels = [Tag("#1", [Tag("#1", [Attrib("Value", struct.pack("<f", float(10 * c)))])]) for c in range(7)]
    flights = Tag("CameraSequenceManager", [Tag("Sequence", [Tag("Cam", [Tag("AllKeyframes", channels)])])])
    gsm = Tag(
        "GameSessionManager",
        [
            Tag("SessionSettings", [Attrib("PlayableArea", _i(4, 4, 60, 60))]),
            area_ids,
            amd,
            Tag("TerrainManager", [Attrib("WorldSize", _i(SIZE, SIZE)), hm]),
            cameras,
            Tag("WorldManager", [water, Tag("EnvironmentGrid", [env])]),
            flights,
            Tag("WeatherManager", [Attrib("WindDirection", struct.pack("<f", 1.0))]),
            Tag("MysteryManager", [Attrib("Thing", _i(7))]),
        ],
    )
    return rda.write_v22({"gamedata.data": filedb.write(Tag("", [Attrib("FileVersion", _i(8)), gsm]), version=3)})


def test_a_grown_session_moves_every_reference_it_knows_and_lists_the_rest():
    import numpy as np

    session = sessionfile.read(session_bytes())
    report = Report((64, 0, 0, 64))
    sessionfile.grow(session, (64, 0, 0, 64), report, "t.a7t")
    g = session.manager
    assert session.size == (128, 128)
    assert struct.unpack("<4i", g.path("SessionSettings/PlayableArea").data) == (4, 4, 124, 124)
    hm = g.path("TerrainManager/HeightMap")
    w = struct.unpack("<I", hm.find("Width").data)[0]
    heights = np.frombuffer(hm.find("HeightMap").data, "<u2").reshape(-1, w)
    assert heights.shape == (257, 257) and heights[5, 7 + 128] == 999 and heights[5, 7] == 0
    water = g.path("WorldManager/Water")
    stride = 128
    bits = np.unpackbits(np.frombuffer(water.find("bits").data, np.uint8), bitorder="little").reshape(128, stride)
    assert bits[4, 3 + 64] == 1 and bits.sum() == 1
    env = g.path("WorldManager/EnvironmentGrid/EnvironmentGRid")
    assert struct.unpack("<I", env.find("x").data)[0] == 32 and env.find("val").data[16:17] == bytes([0])
    blocks = g.find("AreaIDs").findall("block")
    corners = [(struct.unpack("<H", b.find("x").data)[0] if b.find("x") else 0, struct.unpack("<H", b.find("y").data)[0] if b.find("y") else 0)
               for b in blocks[1:-1]]  # fmt: skip
    assert (
        len(corners) == 64
        and (64, 0) in corners
        and (0, 112) in corners
        and corners == sorted(corners, key=lambda c: c[::-1])
    )
    data = g.path("AreaManagerData/#1/Data").data
    pos = filedb.read(data).path("AreaObjectManager/GameObject/objects/#1/Position")
    assert struct.unpack("<3f", pos.data) == (74.5, 2.0, 20.0)
    assert struct.unpack("<I", g.path("AreaManagerData/#1/ByteCount").data)[0] == len(data)
    assert struct.unpack("<3f", g.path("CameraManager/EditorSavedCameraStates/#1/View/From").data) == (94.0, 50.0, 40.0)
    channels = g.path("CameraSequenceManager/Sequence/Cam/AllKeyframes").children
    values = [struct.unpack("<f", c.children[0].find("Value").data)[0] for c in channels]
    assert values == [64.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]  # x moved, z (south) by 0, the rest kept
    kept = {r.where for r in report.kept()}
    assert "MysteryManager" in kept and "AreaManagerData objects' QuestAreaRect" in kept
    assert "WeatherManager" not in kept
    assert sessionfile.read(session.render()).size == (128, 128)


def _asset() -> str:
    return (
        "<Asset><Template>MapTemplate</Template><Values><Standard><GUID>3377</GUID><Name>t</Name></Standard><MapTemplate>"
        f"<TemplateFilename>{STEM}.a7t</TemplateFilename><TemplateRegion>Roman</TemplateRegion>"
        "<EnlargedTemplateFilename>x.a7t</EnlargedTemplateFilename>"
        "<HorizonIslands><Item><Position><X>3600</X><Y>-40</Y><Z>900</Z></Position></Item></HorizonIslands>"
        "</MapTemplate></Values></Asset>"
    )


def test_growing_a_map_moves_the_template_session_level_and_horizon_and_lists_script_numbers():
    parts = grow.GrowParts(
        template.read(template_bytes(size=SIZE * 32)),
        STEM + ".a7tinfo",
        session_bytes(),
        levelfile.editor_level(SIZE * 32),
        _asset(),
        3377,
        {"data/scripts/t_01.lua": "local spot = {1200, 300}\nreturn 1\n"},
    )
    out = grow.grow_map(parts, (256, 128, 0, 64))
    t = template.read(out.files[".a7tinfo"])
    assert t.size == (2304, 2240) and [(e.x, e.z) for e in t.elements][:2] == [(1056, 928), (456, 328)]
    assert levelfile.dimensions(out.files[".a7te"]) == (2304, 2240)
    assert "<X>3856</X>" in out.asset_ops[0] and "<Z>1028</Z>" in out.asset_ops[0] and "<Y>-40</Y>" in out.asset_ops[0]
    assert any(r.file == "data/scripts/t_01.lua" and r.status == KEPT for r in out.report.references)
    assert any(
        r.where == "MapTemplate/TemplateElement/Element/Position" and r.status == MOVED for r in out.report.references
    )
    assert json.loads(out.report.to_json())["size"] == [2304, 2240]
    with pytest.raises(ValueError):
        grow.grow_map(parts, (100, 0, 0, 0))


def _facts(elements, playable=(20, 20, 2020, 2020), pool_counts=None) -> rules.Facts:
    sizes = {0: 256, 1: 320, 2: 432, 3: 432, 4: 2048}

    def land(e):
        return None if e.kind == KIND_START else (e.x, e.z, e.x + sizes[e.size], e.z + sizes[e.size])

    from collections import Counter

    return rules.Facts("anno117", (2048, 2048), playable, elements, land, lambda p: p == ISLAND, Counter(pool_counts or {}),
                       rules.is_continental)  # fmt: skip


def test_the_checks_name_overlaps_places_outside_starts_pools_and_missing_files():
    els = [
        template.new_element(KIND_SLOT, 100, 100),
        template.new_element(KIND_SLOT, 200, 200),  # overlaps the first
        template.new_element(KIND_SLOT, 1900, 1900),  # its centre outside the playable area
        template.new_element(KIND_FIXED, 1000, 1000, map_file_path="missing.a7m"),
    ]
    texts = [f.text for f in rules.check(_facts(els, pool_counts={(0, 0): 2}))]
    assert texts[0] == "the map has no start position: no player's ships can start" or any(
        "its file" in t for t in texts
    )
    assert any("overlap" in t for t in texts) and any("outside the playable area" in t for t in texts)
    assert any("no start position" in t for t in texts) and any("missing.a7m" in t for t in texts)
    assert any("the pool holds 2" in t for t in texts)
    assert rules.check(_facts(els))[0].severity == rules.ERROR


def test_two_continental_islands_are_flagged():
    els = [template.new_element(KIND_SLOT, 0, 0, size=4), template.new_element(KIND_SLOT, 0, 0, size=4)]
    assert any("continental" in f.text for f in rules.check(_facts(els)))


WORLD = (
    b"<WorldConfig>\r\n<Width>512</Width>\r\n<Height>512</Height>\r\n<Islands>\r\n"
    b"<k>1</k>\r\n<v>\r\n<hasValue>1</hasValue>\r\n<IslandConfig>\r\n<Name>A</Name>\r\n<Filename>data\\levels\\islands\\n_s01.isd</Filename>\r\n"
    b"<Direction>0</Direction>\r\n<PositionX>16</PositionX>\r\n<PositionZ>32</PositionZ>\r\n<Width>128</Width>\r\n<Height>128</Height>\r\n"
    b"<Type>0</Type>\r\n</IslandConfig>\r\n</v>\r\n"
    b"<k>2</k>\r\n<v>\r\n<hasValue>1</hasValue>\r\n<IslandConfig>\r\n<Name>B</Name>\r\n<Filename>data\\levels\\islands\\n_m01.isd</Filename>\r\n"
    b"<Direction>1</Direction>\r\n<PositionX>256</PositionX>\r\n<PositionZ>256</PositionZ>\r\n<Width>192</Width>\r\n<Height>192</Height>\r\n"
    b"<Type>2</Type>\r\n</IslandConfig>\r\n</v>\r\n</Islands>\r\n"
    b"<ArkSlots>\r\n<i>\r\n<x>200</x>\r\n<y>100</y>\r\n</i>\r\n</ArkSlots>\r\n</WorldConfig>\r\n"
)


def test_a_world_reads_edits_and_writes_back():
    w = worlds.read(WORLD, "anno2070")
    assert w.render() == WORLD
    assert [(e.kind, e.x, e.z, e.rotation90, e.type) for e in w.elements] == [
        (KIND_FIXED, 16, 32, 0, 0),
        (KIND_FIXED, 256, 256, 1, 2),
        (KIND_START, 200, 100, 0, 0),
    ]
    assert w.extent(w.elements[1]) == 192 and w.elements[0].map_file_path == "data/levels/islands/n_s01.isd"
    w.elements[0].x, w.elements[1].rotation90 = 48, 3
    w.elements.pop(1)
    w.elements.append(
        template.new_element(KIND_FIXED, 320, 320, map_file_path="data/levels/islands/n_l01.isd", island_label="C")
    )
    w.elements.append(template.new_element(KIND_START, 10, 10))
    back = worlds.read(w.render(), "anno2070")
    assert [(e.kind, e.x, e.z, e.island_label) for e in back.elements] == [
        (KIND_FIXED, 48, 32, "A"), (KIND_FIXED, 320, 320, "C"), (KIND_START, 200, 100, ""), (KIND_START, 8, 8, "")
    ]  # fmt: skip
    assert back.elements[1].map_file_path == "data/levels/islands/n_l01.isd"


def _game(tmp_path):
    files = {
        STEM + ".a7tinfo": template_bytes(),
        STEM + ".a7t": session_bytes(),
        STEM + ".a7te": levelfile.editor_level(SIZE),
        pool.ASSET_FILES["anno117"]: (
            "<Assets>"
            + _asset()
            + "<Asset><Template>RandomIsland</Template><Values><Standard><GUID>1</GUID><Name>i</Name>"
            f"</Standard><RandomIsland><FilePath>{ISLAND}</FilePath><IslandRegion>Roman</IslandRegion>"
            "<IslandType>Normal</IslandType><IslandPlaceholderSize>Medium</IslandPlaceholderSize></RandomIsland></Values></Asset>"
            "</Assets>"
        ).encode(),
        ISLAND.replace(".a7m", ".a7minfo"): filedb.write(
            Tag("", [Attrib("MapSize", _i(192, 192)), Attrib("ActiveMapRect", _i(16, 16, 176, 176))])
        ),
        ISLAND: b"x",
    }
    for path, data in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(data)
    return gametree.loose_tree(tmp_path, "117")


def test_a_map_document_reads_the_pool_sizes_and_undoes_every_edit(tmp_path):
    tree = _game(tmp_path)
    maps = pool.GameMaps.read(tree, "anno117")
    assert maps.pool[0].size_class == 1 and maps.classes[1] == 320 and maps.asset_of(STEM + ".a7tinfo").guid == 3377
    doc = mapdoc.open_map(tree, "anno117", STEM + ".a7tinfo", maps)
    assert doc.extent(doc.elements[0]) == 192 and doc.land(doc.elements[0]) == (816, 816, 976, 976)
    assert not doc.changed()
    doc.move([1], 8, -8)
    doc.rotate([0])
    doc.remove([3])
    assert doc.changed() and len(doc.elements) == 3
    doc.undo(), doc.undo(), doc.undo()
    assert not doc.changed() and doc.render() == doc.original
    doc.redo()
    assert (doc.elements[1].x, doc.elements[1].z) == (208, 192)
    grown = doc.grow((64, 0, 0, 0))
    assert doc.size == (2112, 2048) and ".a7t" in grown.files
    doc.undo()
    assert doc.size == (2048, 2048) and doc.grown is None


def test_the_mod_files_replace_the_vanilla_map_or_add_a_new_one(tmp_path):
    tree = _game(tmp_path / "game")
    maps = pool.GameMaps.read(tree, "anno117")
    doc = mapdoc.open_map(tree, "anno117", STEM + ".a7tinfo", maps)
    doc.move([2], 64, 0)
    assert set(mapmod.map_files(doc)) == {STEM + ".a7tinfo"}
    doc.grow((0, 0, 64, 64))
    replaced = mapmod.map_files(doc)
    assert set(replaced) == {
        STEM + ".a7tinfo",
        STEM + ".a7t",
        STEM + ".a7te",
    }  # growing east and north moves no horizon
    new = mapmod.map_files(doc, mapmod.Choice(True, "my_map", 2_100_999_999, "my-mod"))
    target = "data/mods/my-mod/maps/my_map/my_map"
    assert {target + s for s in (".a7tinfo", ".a7t", ".a7te")} <= set(new)
    ops = new[pool.ASSET_FILES["anno117"]].decode()
    assert "<GUID>2100999999</GUID>" in ops and f"<TemplateFilename>{target}.a7t</TemplateFilename>" in ops
    assert "Enlarged" not in ops and "<ModOp" in ops
    with pytest.raises(ValueError):
        mapmod.map_files(doc, mapmod.Choice(True, "Bad Name", 1))
    out = mapmod.write_mod(tmp_path / "mods", replaced, "my-map", "My map", doc.grown.report.to_json())
    assert (out / "modinfo.json").is_file() and (tmp_path / "mods" / "my-map.grow.json").is_file()
    assert (
        mapmod.suggest_guid((10, 20), {20}, "a", {}) == 19
        and mapmod.suggest_guid((10, 20), set(), "a", {"a": 12}) == 12
    )


def test_the_project_keeps_the_files_and_drops_what_an_undo_took_back(tmp_path):
    project = Project(tmp_path / "p", mod_id="m", game="anno117")
    projectpart.save(project, "anno117", {"a/x.a7tinfo": b"1", "a/x.a7t": b"2"}, report="{}")
    assert projectpart.PART.has_work(project)
    assert (tmp_path / "p" / "map-edits" / "reports" / "x.grow.json").is_file()
    projectpart.save(project, "anno117", {"a/x.a7tinfo": b"3"}, earlier={"a/x.a7tinfo", "a/x.a7t"})
    assert projectpart.PART.render(project, None, lambda _t: None) == {"a/x.a7tinfo": b"3"}


# Faults the audit of 2026-11-13 found, one test each (docs/audit-2026-11.md).
def test_a_quarter_turn_turns_the_active_area_as_the_game_does():
    from annotoolbox.mapedit import placing

    # Rotation90 1 takes (u, v) to (edge - v, u): counter-clockwise from above (map-editor "What the data settles")
    assert placing.turned((24, 0, 312, 320), 320, 1) == (0, 24, 320, 312)
    assert placing.turned((8, 16, 100, 40), 128, 2) == (28, 88, 120, 112)
    assert placing.turned((8, 16, 100, 40), 128, 4) == (8, 16, 100, 40)


def test_a_horizon_position_that_leaves_out_x_is_moved_too():
    asset = _asset().replace("<X>3600</X><Y>-40</Y>", "")  # vanilla leaves a 0 out (one Position holds only Z)
    report = Report((64, 32, 0, 0))
    ops, text = grow.grow_horizons(asset, 3377, 64, 32, report)
    assert "<Position><X>64</X><Z>932</Z></Position>" in ops[0] and "<X>64</X>" in text


def test_growing_an_enlarged_template_moves_the_enlarged_maps_horizons():
    asset = _asset().replace(
        "</HorizonIslands>",
        "</HorizonIslands><EnlargedHorizonIslands><Item><Position><X>10</X><Z>20</Z></Position></Item>"
        "</EnlargedHorizonIslands>",
    )
    report = Report((64, 0, 0, 0))
    ops, text = grow.grow_horizons(asset, 3377, 64, 0, report, enlarged=True)
    assert ops[0].count("EnlargedHorizonIslands") >= 1 and "<X>74</X>" in ops[0] and "<X>3600</X>" in text
    assert any(r.status == KEPT and r.where.endswith("HorizonIslands") for r in report.references)


def _quest(guid: int, named: int, items: str) -> str:
    return (
        f"<Asset><Template>Quest</Template><Values><Standard><GUID>{guid}</GUID><Name>q</Name></Standard>"
        f"<Quest><Map>{named}</Map><Targets>{items}</Targets></Quest></Values></Asset>"
    )


def _assets_with_positions() -> str:
    two = "<Item><Position><X>100</X><Z>200</Z></Position></Item><Item><Position><Z>50</Z></Position></Item>"
    return (
        "<Assets>"
        + _quest(9001, 3377, two)  # names the grown map's template
        + _quest(9002, 3378, "<Item><Position><X>5</X><Z>6</Z></Position></Item>")  # another map's
        + _quest(9003, 3377, "<Item><Position><X>7</X><Z>8</Z></Position></Item>").replace(
            "</Map>", "</Map><Other>3378</Other>"
        )  # names both
        + _quest(9004, 7, "<Item><Position><X>9</X><Z>9</Z></Position></Item>")  # names no map
        + "<Asset><Template>SessionModerate</Template><Values><Standard><GUID>8001</GUID></Standard>"
        "<Session><MapTemplate>3378</MapTemplate></Session></Values></Asset>"
        + _quest(9005, 8001, "<Item><Position><X>1</X><Z>2</Z></Position></Item>")  # a session of the other map
        + _asset()  # the map's own asset: its horizons are moved by their own step
        + "</Assets>"
    )


def test_assets_that_hold_the_grown_maps_positions_are_moved_and_the_others_listed():
    found = grow.scan_assets([_assets_with_positions()])
    assert sorted(a.guid for a in found.assets) == [9001, 9002, 9003, 9004, 9005] and found.sessions == {8001: 3378}
    report = Report((64, 32, 0, 0))
    ops, moved = grow.grow_assets(found, 3377, frozenset({3377, 3378}), 64, 32, report)
    assert len(ops) == 2 and "Asset[Values/Standard/GUID='9001']/Values/Quest/Targets/Item[1]/Position" in ops[0]
    assert (
        "<X>164</X>" in ops[0] and "<Z>232</Z>" in ops[0] and "<X>64</X><Z>82</Z>" in ops[1]
    )  # a left-out X is written
    assert "<X>164</X>" in moved[9001] and 9002 not in moved
    kept = {r.where: r.detail for r in report.kept()}
    assert kept["Quest 9002"] == "belongs to another map" and kept["Quest 9005"] == "belongs to another map"
    assert "other maps too" in kept["Quest 9003"] and "no map" in kept["Quest 9004"]
    assert [r.detail for r in report.moved()][0] == "X 100, Z 200 -> X 164, Z 232"


def test_an_asset_in_two_lists_is_moved_once():
    one = _quest(9001, 3377, "<Item><Position><X>100</X><Z>200</Z></Position></Item>")
    found = grow.scan_assets([one, one])
    ops, moved = grow.grow_assets(found, 3377, frozenset({3377}), 64, 0, Report((64, 0, 0, 0)))
    assert len(ops) == 1 and "<X>164</X>" in ops[0] and "<X>164</X>" in moved[9001]


def test_a_growth_moves_the_assets_once_and_the_moves_add_up_over_growths(tmp_path):
    tree = _game(tmp_path)
    doc = mapdoc.open_map(tree, "anno117", STEM + ".a7tinfo", pool.GameMaps.read(tree, "anno117"))
    doc.maps.positions = grow.scan_assets([_assets_with_positions()])
    doc.grow((64, 0, 0, 0))
    doc.grow((0, 64, 0, 0))
    ops = "".join(doc.grown.asset_ops)
    assert "<X>164</X>" in ops and "<Z>264</Z>" in ops and "<X>3664</X>" in ops  # both moves, horizons too
    doc.grow((0, 0, 64, 0))  # east: the earlier moves still hold
    assert "<Z>264</Z>" in "".join(doc.grown.asset_ops)
    assert any(r.file == "assets" and r.status == MOVED for r in doc.grown.report.references) is False
    new = mapmod.map_files(doc, mapmod.Choice(True, "grown", 2_100_000_000, "m"))
    assert "<Z>264</Z>" not in new[pool.ASSET_FILES["anno117"]].decode()  # a new map takes none of the old map's quests


def test_a_second_growth_keeps_the_first_ones_horizon_move(tmp_path):
    tree = _game(tmp_path)
    doc = mapdoc.open_map(tree, "anno117", STEM + ".a7tinfo", pool.GameMaps.read(tree, "anno117"))
    doc.grow((64, 0, 0, 0))
    doc.grow((64, 0, 0, 0))
    assert "<X>3728</X>" in doc.grown.asset_ops[0]
    doc.grow((0, 0, 64, 0))  # east: no horizon moves, the earlier move still holds
    assert "<X>3728</X>" in doc.grown.asset_ops[0]
    new = mapmod.map_files(doc, mapmod.Choice(True, "grown", 2_100_000_000, "m"))
    assert "<X>3728</X>" in new[pool.ASSET_FILES["anno117"]].decode()


def test_a_duplicated_fixed_island_keeps_its_difficulty_and_fields(tmp_path):
    hard = fixed(800, 800)
    hard.find("Element").path("RandomIslandConfig/value/Difficulty/id").data = _h(2)
    t = template.read(template_bytes(elements=[hard, start(10, 10)]))
    copy_ = t.elements[0].copy()
    copy_.x += 64
    t.elements.append(copy_)
    back = template.read(t.render())
    assert back.elements[2].tag.path("Element/RandomIslandConfig/value/Difficulty/id").data == _h(2)
    assert (back.elements[2].x, back.elements[2].island_label) == (864, "isle")


def test_a_template_without_a_playable_area_reads_and_writes_back():
    mt = Tag("MapTemplate", [Attrib("Size", _i(512, 512)), Attrib("ElementCount", struct.pack("<I", 1)), start(8, 8)])
    raw = filedb.write(Tag("", [mt]))
    t = template.read(raw)
    assert t.playable == (0, 0, 512, 512) and t.render() == raw


def test_a_world_island_removed_and_brought_back_by_undo_is_written_again(tmp_path):
    (tmp_path / "w.www").write_bytes(WORLD)
    tree = gametree.loose_tree(tmp_path, "2070")
    doc = mapdoc.open_map(tree, "anno2070", "w.www", pool.GameMaps(game="anno2070"))
    doc.remove([0])
    assert b"n_s01" not in doc.render()
    doc.undo()
    assert doc.render() == WORLD


def test_a_world_island_added_or_copied_takes_its_own_size():
    w = worlds.read(WORLD, "anno2070")
    w.edge_of = lambda e: 160
    w.elements.append(
        template.new_element(KIND_FIXED, 320, 320, map_file_path="data/levels/islands/n_m02.isd", island_label="C")
    )
    copied = w.elements[1].copy()
    copied.x = 600
    w.elements.append(copied)
    back = worlds.read(w.render(), "anno2070")
    sizes = [back.extents[id(e.tag)] for e in back.elements if e.kind == KIND_FIXED]
    assert sizes == [(128, 128), (192, 192), (160, 160), (192, 192)]
    assert [e.island_label for e in back.elements if e.kind == KIND_FIXED] == ["A", "B", "C", "B"]
    keys = [c.text for c in back.config.find("Islands").children if c.name == "k"]
    assert keys == ["1", "2", "3", "4"]


def test_a_session_area_with_nothing_to_move_keeps_its_bytes():
    raw = session_bytes()
    session = sessionfile.read(raw)
    still = filedb.write(Tag("", [Tag("AreaObjectManager", [Attrib("Count", _i(0))])]), version=3)
    amd = session.manager.find("AreaManagerData")
    amd.children.append(Tag("#1", [Attrib("ByteCount", struct.pack("<I", len(still))), Attrib("Data", still)]))
    sessionfile.grow(session, (64, 0, 0, 0), Report((64, 0, 0, 0)))
    assert amd.children[-1].find("Data").data == still


def test_a_new_maps_guid_must_lie_in_the_range_and_be_free(tmp_path):
    project = Project(tmp_path / "p", mod_id="m", game="anno117", guid_range=(100, 200))
    project.asset_edits.mkdir(parents=True)
    (project.asset_edits / "anno117.json").write_text(json.dumps({"new": {"150": {}}}), encoding="utf-8")
    taken = mapmod.taken_guids(project)
    assert taken == {150}
    assert mapmod.guid_problem(99, (100, 200), taken, {}, "a")
    assert mapmod.guid_problem(150, (100, 200), taken, {}, "a")
    assert mapmod.guid_problem(190, (100, 200), taken, {"other": 190}, "a")
    assert not mapmod.guid_problem(190, (100, 200), taken, {"a": 190}, "a")
    assert mapmod.suggest_guid((100, 200), taken | {200}, "b", {}) == 199


def test_a_fixed_island_shows_its_own_size_class_not_the_stored_small():
    cinis = "data/dlc01/provinces/roman/islands/pool/roman_dlc01_island_continental_01/roman_dlc01_island_continental_01.a7m"
    pooled = "data/base/provinces/roman/islands/pool/m_01/m_01.a7m"
    maps = pool.GameMaps(game="anno117", classes=dict(pool.DEFAULT_CLASSES["anno117"]))
    maps.pool = [pool.PoolIsland(1, "m", pooled, "Roman", "Normal", 1)]
    maps.islands = {ISLAND: pool.IslandInfo(size=432)}
    doc = mapdoc.MapDoc("anno117", "t.a7tinfo", template.read(template_bytes()), b"", maps, tree=None)
    sizes = [doc.size_index(template.new_element(KIND_FIXED, 0, 0, map_file_path=p)) for p in (cinis, pooled, ISLAND)]
    assert sizes == [template.SIZE_CONTINENTAL, 1, template.SIZE_LARGE]  # the file's name, the pool, the grid
    assert doc.size_index(template.new_element(KIND_SLOT, 0, 0, size=2)) == 2  # a slot keeps its stored size


def test_an_island_picture_loses_the_black_around_the_land_and_the_stripes_below_it():
    import numpy as np

    picture = np.zeros((40, 8, 4), np.uint8)
    picture[..., 3] = 255
    picture[5:20, 2:6, :3] = np.arange(15 * 4 * 3, dtype=np.uint8).reshape(15, 4, 3) + 40  # land, every row differs
    picture[20:, 2:6, :3] = picture[19, 2:6, :3]  # the game's clamp: the last row repeated down to the edge
    clean = pool.clean_picture(picture)
    assert (clean[5:20, 2:6, 3] == 255).all()  # the land stays
    assert (clean[20:, :, 3] == 0).all() and (clean[:5, :, 3] == 0).all()  # stripes and black go


# Faults the audit of 2026-11-17 found, one test each (the window's are in test_mapedit_window.py).
def test_an_island_path_spelled_another_way_is_the_same_island_and_keeps_its_mine_slots(tmp_path):
    island = fixed(800, 800, ISLAND.replace("/pool/", chr(92) + "Pool" + chr(92)))  # as a hand-made template may
    slots = [Attrib(template.NONE, struct.pack("<Q", 7)), Attrib(template.NONE, bytes([1]))]
    island.find("Element").find("MineSlotActivation").children = slots
    raw = template_bytes(elements=[island, start(10, 10)])
    doc = mapdoc.MapDoc("anno117", "t.a7tinfo", template.read(raw), raw, pool.GameMaps(game="anno117"), tree=None)
    doc.set_values(0, {"map_file_path": ISLAND, "island_label": "isle"})  # the panel's text: lower case, slashes
    assert doc.elements[0].mine_slot_activation == ((7, 1),) and not doc.undos and doc.render() == raw
    doc.set_values(0, {"map_file_path": ISLAND.replace("i_01", "i_02")})  # another island: its slots are not this one's
    assert doc.elements[0].mine_slot_activation == ()


def test_a_growth_that_fails_leaves_the_map_as_it_was(tmp_path):
    tree = _game(tmp_path)
    (tmp_path / (STEM + ".a7t")).write_bytes(b"no archive")
    doc = mapdoc.open_map(tree, "anno117", STEM + ".a7tinfo", pool.GameMaps.read(tree, "anno117"))
    before = (doc.size, doc.playable, [(e.x, e.z) for e in doc.elements])
    with pytest.raises(ValueError, match="not an RDA archive"):
        doc.grow((64, 64, 0, 0))
    assert (doc.size, doc.playable, [(e.x, e.z) for e in doc.elements]) == before
    assert not doc.undos and doc.grown is None and not doc.changed()


def test_an_area_set_to_what_it_is_makes_no_undo_step(tmp_path):
    tree = _game(tmp_path)
    doc = mapdoc.open_map(tree, "anno117", STEM + ".a7tinfo", pool.GameMaps.read(tree, "anno117"))
    doc.set_area(size=doc.size, playable=doc.playable, initial=doc.initial)
    assert not doc.undos
    doc.set_area(playable=(8, 8, 2000, 2000))
    assert doc.playable == (8, 8, 2000, 2000) and len(doc.undos) == 1
    assert doc.undo() and doc.playable != (8, 8, 2000, 2000)


@pytest.mark.parametrize("text", ['{"a": null}', "[1, 2]", "not json", '{"a": "x"}'])
def test_a_guid_list_that_is_none_reads_as_empty(tmp_path, text):
    (tmp_path / mapmod.GUIDS).write_text(text, encoding="utf-8")
    assert mapmod.read_guids(tmp_path) == {}
    mapmod.keep_guid(tmp_path, "a", 5)
    assert mapmod.read_guids(tmp_path) == {"a": 5}


def test_an_island_whose_size_field_is_cut_short_has_no_grid(tmp_path):
    (tmp_path / "i.a7minfo").write_bytes(filedb.write(Tag("", [Attrib("MapSize", b"\x01\x02")])))
    (tmp_path / "i.a7m").write_bytes(b"x")
    assert pool.read_island_info(gametree.loose_tree(tmp_path, "117"), "i.a7m") == pool.IslandInfo()
