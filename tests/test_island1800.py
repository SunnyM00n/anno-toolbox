import math
import struct
from pathlib import Path

import numpy as np
import pytest

from annotoolbox.editor.assetmodels import ASSETS_1800, AssetModels
from annotoolbox.editor.formats.a7m import reader as a7m_reader
from annotoolbox.formats import filedb, rda
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.othergames import installs
from fakes import FakeFiles

PATH = "data/sessions/islands/pool/moderate/isle/isle.a7m"
MATERIAL_SET = "data/config/engine/material_sets/test.xml"
SIZE = 4  # tiles across; 2 material texels per tile, ids in 2 x 2 blocks, as 1800 stores them


def _doc() -> Tag:
    """Nested tags, list elements without a name (``#1``, ``#32768``), a long and an empty attribute."""
    return Tag(
        "",
        [
            Attrib("FileVersion", struct.pack("<I", 8)),
            Tag(
                "GameSessionManager",
                [
                    Tag("List", [Attrib("#32768", b"\x02"), Tag("#1", [Attrib("v", b"abc")]), Tag("#1", [])]),
                    Attrib("Big", bytes(range(256)) * 80),  # a three-byte varint size in v1
                    Attrib("Empty", b""),
                ],
            ),
        ],
    )


@pytest.mark.parametrize("version", [1, 2, 3])
def test_filedb_round_trips_in_every_version(version):
    raw = filedb.write(_doc(), version=version)
    assert filedb.version(raw) == version
    back = filedb.read(raw)
    assert filedb.dump(back) == filedb.dump(_doc())
    assert back.path("GameSessionManager/Big").data == bytes(range(256)) * 80
    assert filedb.write(back, *filedb.name_ids(raw), version=version) == raw


def test_filedb_v1_keeps_the_name_table_order_of_the_original():
    # a v1 file as 1800 writes it: tables in no particular order (here: descending ids)
    nodes = struct.pack("<HH", 3, 2) + struct.pack("<H", 0x8002) + b"\x01x" + struct.pack("<HHH", 0, 0, 0)
    tables = b"\x02" + b"B\0" + struct.pack("<H", 3) + b"C\0" + struct.pack("<H", 2)
    tables += b"\x01" + b"y\0" + struct.pack("<H", 0x8002)
    raw = nodes + tables + struct.pack("<I", len(nodes))
    doc = filedb.read(raw)
    assert doc.path("B/C/y").data == b"x" and filedb.version(raw) == 1
    assert filedb.write(doc, *filedb.name_ids(raw), version=1) == raw


def test_filedb_v3_layout_is_unchanged():
    raw = filedb.write(Tag("", [Tag("T", [Attrib("a", b"\x01")])]))
    expected = struct.pack("<iI", 0, 2) + struct.pack("<iI", 1, 0x8001) + b"\x01" + bytes(7)
    expected += struct.pack("<iIiI", 0, 0, 0, 0)
    tags = struct.pack("<IH", 1, 2) + b"T\0"
    attrs = struct.pack("<IH", 1, 0x8001) + b"a\0"
    expected += tags + bytes(-len(tags) % 8) + attrs + bytes(-len(attrs) % 8)
    expected += struct.pack("<II", 0, 3) + struct.pack("<IIIi", 40, 48, 8, -3)
    assert raw == expected


def test_1800_archives_are_layered_in_number_order(tmp_path: Path):
    maindata = tmp_path / "maindata"
    maindata.mkdir()
    for n in (0, 2, 9, 10):
        files = {"data/x.txt": f"data{n}".encode(), f"data/only{n}.txt": b"1"}
        (maindata / f"data{n}.rda").write_bytes(rda.write_v22(files))
    tree = installs.GAMES["anno1800"].tree(tmp_path)
    assert [a.name for a in tree.archives] == ["data0", "data2", "data9", "data10"]
    assert tree.read_bytes("data/x.txt") == b"data10"  # a text sort would let data9 win
    assert tree.exists("data/only0.txt") and tree.exists("data/only10.txt")


def _grid(name: str, bits: bytes) -> Tag:
    return Tag(name, [Attrib("x", struct.pack("<I", SIZE)), Attrib("y", struct.pack("<I", SIZE)), Attrib("bits", bits)])


PROP = "data/graphics/props/furniture/chair_01.prp"


def _river() -> Tag:
    curve = Tag(
        "BezierCurve",
        [
            Tag("#1", [Attrib("p", struct.pack("<3f", x, 0.0, 1.0)), Attrib("w", struct.pack("<f", 2.0))])
            for x in (0, 3)
        ],
    )
    return Tag(
        "#1",
        [
            Attrib("guid", struct.pack("<I", 5)),
            Attrib("Position", bytes(12)),
            Tag("River", []),
            Tag("BezierPath", [Tag("Path", [curve])]),
        ],
    )


def _prop_grid() -> Tag:
    """One prop (the chair) in the render data's prop grid."""
    prop = Tag(
        "#1",
        [
            Attrib("Index", struct.pack("<I", 0)),
            Attrib("Position", struct.pack("<3f", 2.0, 0.5, 2.0)),
            Attrib("Rotation", struct.pack("<4f", 0, 0, 0, 1)),
            Attrib("Scale", struct.pack("<3f", 1, 1, 1)),
            Attrib("Color", struct.pack("<4f", 1, 1, 1, 1)),
        ],
    )
    names = Tag("FileNames", [Attrib("#32768", PROP.replace("/", "\\").encode("utf-16-le"))])
    return Tag("PropGrid", [names, Tag("Instances", [prop])])


def _island(version: int, material_set: bool) -> dict[str, bytes]:
    """A 4 x 4 island as 1800 stores one: heights, land, one object, a river, a prop, material maps in rd3d."""
    w = 2 * SIZE + 1
    game = (np.arange(w * w, dtype="<i2") * 4).reshape(w, w)  # 1/128 world unit
    half = math.sqrt(0.5)
    obj = Tag(
        "#1",
        [
            Attrib("guid", struct.pack("<I", 100849)),
            Attrib("ID", struct.pack("<2I", 7, 0x1234)),  # serial, type
            Attrib("Position", struct.pack("<3f", 1.5, 2.0, 2.5)),
            Tag("Mesh", [Attrib("Orientation", struct.pack("<4f", 0, half, 0, half))]),
        ],
    )
    area = Tag(
        "",
        [
            Tag(
                "AreaObjectManager",
                [Tag("GameObject", [Tag("objects", [obj])]), Tag("EditorObject", [Tag("objects", [_river()])])],
            )
        ],
    )
    area_raw = filedb.write(area, version=version)
    gm = Tag(
        "GameSessionManager",
        [
            Tag(
                "TerrainManager",
                [
                    Tag(
                        "HeightMap",
                        [
                            Attrib("Width", struct.pack("<I", w)),
                            Attrib("Height", struct.pack("<I", w)),
                            Attrib("HeightMap", game.tobytes()),
                        ],
                    )
                ],
            ),
            Tag("WorldManager", [_grid("Water", b"\x03\0\0\0" * SIZE)]),  # the west half is land
            Tag(
                "AreaManagerData",
                [Tag("#1", [Attrib("ByteCount", struct.pack("<I", len(area_raw))), Attrib("Data", area_raw)])],
            ),
        ],
    )
    n = 2 * SIZE  # material texels across
    ids = np.full((n // 2, n // 2), 3 | 7 << 5, "<u4")  # slot 0: material 3, slot 1: material 7
    alphas = np.full((n, n), 31 | 31 << 5, "<u4")
    alphas[:, : n // 2] = 31  # material 7 only on the east half
    terrain = [
        Attrib("MaterialMapWidth", struct.pack("<I", n)),
        Attrib("MaterialIDBlockSize", struct.pack("<I", 2)),
        Attrib("MaterialIDs2", ids.tobytes()),
        Attrib("MaterialAlphas2", alphas.tobytes()),
        Tag(
            "CoarseHeightMap",
            [
                Attrib("width", struct.pack("<I", w)),
                Attrib("height", struct.pack("<I", w)),
                Attrib("map", (game * 2).astype("<i2").tobytes()),
            ],
        ),
    ]
    if material_set:
        terrain.insert(0, Attrib("MaterialSetFileName", MATERIAL_SET.encode("utf-16-le")))
    rd3d = Tag("", [Tag("Terrain", terrain), _prop_grid()])
    info = Tag(
        "", [Attrib("MapSize", struct.pack("<2i", SIZE, SIZE)), Attrib("ActiveMapRect", struct.pack("<4i", 0, 0, 4, 4))]
    )
    a7m = rda.write_v22(
        {
            "gamedata.data": filedb.write(Tag("", [gm]), version=version),
            "rd3d.data": filedb.write(rd3d, version=version),
        }
    )
    sets = "<MaterialSet><Material><ID>3</>\n<FileName>data/m/a.xml</>\n</>\n<Material><ID>7</>\n<FileName>data/m/b.xml</>\n</>\n"
    return {
        PATH: a7m,
        PATH[:-4] + ".a7minfo": filedb.write(info, version=version),
        MATERIAL_SET: sets.encode(),
        a7m_reader.DEFAULT_MATERIAL_SET: sets.encode(),
        "data/m/a.xml": b"<Material><DiffuseFileName>data/m/a_diff.psd</>\n</>",
        "data/m/b.xml": b"<Material><DiffuseFileName>data/m/b_diff.psd</>\n</>",
    }


@pytest.mark.parametrize("version", [1, 2, 3])
def test_an_1800_island_reads_as_the_117_editor_needs_it(version):
    isl = a7m_reader.Island1800(FakeFiles(_island(version, material_set=version != 1)), PATH)
    assert isl.versions == {"gamedata.data": version, "rd3d.data": version}
    assert isl.size == SIZE and isl.texels == 2 and isl.size_class() == "Small"
    # render heights are twice the game's, both 1/128 world unit per game step
    np.testing.assert_allclose(isl.heights(), isl.game_heights())
    assert isl.heights()[0, 1] == pytest.approx(4 / 128)
    assert isl.land[:, :2].all() and not isl.land[:, 2:].any()
    assert isl.material_set == (MATERIAL_SET if version != 1 else a7m_reader.DEFAULT_MATERIAL_SET)
    assert sorted(isl.materials()) == [3, 7] and isl.materials()[7]["diffuse"] == "data/m/b_diff.psd"
    mids, wts = isl.weights()
    assert mids.shape == (6, 8, 8) and (mids[0] == 3).all() and (mids[1] == 7).all()
    assert (wts[1][:, 4:] == 1).all() and (wts[0][:, 4:] == 0).all() and (wts[0][:, :4] == 1).all()
    obj, river = isl.objects()
    assert river["group"] == "EditorObject" and obj["name"] == "guid:100849"
    assert obj["group"] == "GameObject" and obj["guid"] == 100849 and obj["position"] == [1.5, 2.0, 2.5]
    assert obj["rotation"] == pytest.approx([0, math.sqrt(0.5), 0, math.sqrt(0.5)]) and obj["scale"] == 1.0


@pytest.mark.parametrize("version", [1, 2, 3])
def test_an_1800_island_is_written_back_in_its_own_layout(version):
    files = _island(version, material_set=True)
    out = a7m_reader.IslandFile1800(files[PATH], files[PATH[:-4] + ".a7minfo"]).write()
    before, after = a7m_reader.archive_members(files[PATH])[1], a7m_reader.archive_members(out)[1]
    assert after == before


def test_asset_models_finds_only_the_guids_asked_for():
    xml = (
        b"<Asset><Values><Standard><GUID>1</GUID></Standard><Object><Variations><Item>"
        b"<Filename>data\\graphics\\a.cfg</Filename></Item></Variations></Object></Values></Asset>"
        b"<Asset><Values><Standard><GUID>2</GUID></Standard></Values></Asset>"
        b"<Asset><Values><Standard><GUID>3</GUID></Standard><Object><Variations><Item>"
        b"<Filename>data/graphics/c.cfg</Filename></Item></Variations></Object></Values></Asset>"
    )
    models = AssetModels(xml, game=ASSETS_1800)
    assert {g: models.first_model(g) for g in (1, 2, 3, 4)} == {
        1: "data/graphics/a.cfg",
        2: "",
        3: "data/graphics/c.cfg",
        4: "",
    }  # an asset without a model, or the game without the asset, names none
