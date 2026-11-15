import math
import struct
from pathlib import Path

import numpy as np
import pytest

from annotoolbox.editor import a7mviewer, editsession
from annotoolbox.editor.assetmodels import ASSETS_2205, AssetModels
from annotoolbox.editor.formats.a7m import session as a7m_session
from annotoolbox.formats import filedb, rda
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.islands import islandedits
from annotoolbox.othergames import installs
from fakes import FakeFiles

PATH = "data/sessions/session_test/session_test.a6m"
LOOSE_RD3D = "data/sessions/session_test/_a6mdata/session_test/rd3d.data"
MATERIAL_SET = "data/config/engine/material_sets/test.xml"
SIZE = 4  # tiles across; 2 material texels per tile, one id texel per 4 x 4 of them, as 2205 stores them
WEST, EAST = 31 | 31 << 5 | 31 << 10, 31 << 5 | 31 << 10  # alphas of slots 0..2: material 3 only in the west


def _fixed(v: float) -> bytes:
    return struct.pack("<q", round(v * a7m_session.FIXED))


KIND = 0x80000000  # the high word of an object's ID: the asset's kind; the low word counts them


def _id(serial: int, high: int = KIND) -> Attrib:
    return Attrib("ID", struct.pack("<II", serial, high))


def _river() -> Tag:
    curve = [
        Tag("#1", [Attrib("p", struct.pack("<3f", x, 0.5, 1.0)), Attrib("w", struct.pack("<f", 2.0))]) for x in (0, 3)
    ]
    return Tag(
        "#1",
        [
            Attrib("guid", struct.pack("<I", 1608)),
            _id(3),
            Tag("BezierPath", [Tag("Path", [Tag("BezierCurve", curve)])]),
            Tag("River", []),
        ],
    )


def _folder(folder: int, *inside: Tag) -> Tag:
    return Tag("#1", [Attrib("FolderID", struct.pack("<I", folder)), Tag("Filter", list(inside))])


def _objects() -> Tag:
    turned = Tag(
        "#1",
        [
            Attrib("guid", struct.pack("<I", 1001)),
            _id(1),
            Attrib("Variation", struct.pack("<I", 1)),
            Attrib("Direction", _fixed(90)),
            Tag("Position", [Attrib("x", _fixed(1.5)), Attrib("y", _fixed(2.0)), Attrib("z", _fixed(2.5))]),
        ],
    )
    free = Tag(
        "#1",
        [
            Attrib("guid", struct.pack("<I", 1002)),
            _id(2),
            Attrib("ObjectFolderID", struct.pack("<I", 7)),
            Tag("Position", [Attrib("x", _fixed(3.0)), Attrib("z", _fixed(0.25))]),
            Tag(
                "FreeOrientation",
                [Attrib("Orientation", struct.pack("<4f", 0, 0, 1, 0)), Attrib("Scale", struct.pack("<f", 2.5))],
            ),
        ],
    )
    past = Tag("#1", [Attrib("guid", struct.pack("<I", 1001)), Attrib("Variation", struct.pack("<I", 9))])
    own = Tag(
        "#1",
        [
            Attrib("guid", struct.pack("<I", 4)),
            Tag("DynamicVariation", [Attrib("ConfigFilename", r"data\g\a1.cfg".encode("utf-16-le"))]),
        ],
    )
    return Tag(
        "GameObjectManager",
        [
            Tag("GameObjectLabelMap", [Attrib("#32768", b"harbour"), Attrib("#32768", struct.pack("<II", 1, KIND))]),
            Tag("ObjectGroupFilterCollection", [Tag("Root", [Tag("Filter", [_folder(1, _folder(7))])])]),
            Tag("GameObject", [Tag("objects", [turned])]),
            Tag("EditorObject", [Tag("objects", [free, _river()])]),
            Tag("NaturePreset", [Tag("objects", [past, own])]),
        ],
    )


def _prop(cfg: str, x: float) -> Tag:
    return Tag(
        "#1",
        [
            Attrib("FileName", cfg.encode("utf-16-le")),
            Attrib("Position", struct.pack("<3f", x, 0.5, 2.0)),
            Attrib("Rotation", struct.pack("<4f", 0, 0, 0, 1)),
            Attrib("Scale", struct.pack("<3f", 1, 1, 1)),
            Attrib("Color", struct.pack("<4f", 1, 1, 1, 0)),
        ],
    )


def _session(*, portrait: bool = False) -> dict[str, bytes]:
    """A 4 x 4 session as the last patch ships one: the map without rd3d.data, the loose rd3d.data an
    RDA archive around the tree file; material 3 in slot 0 but laid after material 7 by the set. A portrait scene
    has no render heights and no water, and names a material set the game does not ship (``fpp_test``)."""
    w = 2 * SIZE + 1
    game = (np.arange(w * w, dtype="<i2") * 4).reshape(w, w)
    water = Tag(
        "Water",
        [
            Attrib("x", struct.pack("<I", SIZE)),
            Attrib("y", struct.pack("<I", SIZE)),
            Attrib("bits", b"\x03\0\0\0" * SIZE),
        ],
    )
    height = Tag(
        "HeightMap",
        [
            Attrib("Width", struct.pack("<I", w)),
            Attrib("Height", struct.pack("<I", w)),
            Attrib("HeightMap", game.tobytes()),
        ],
    )
    gm = Tag(
        "GameSessionManager",
        [
            Tag("SessionSettings", [Attrib("HasWater", b"\0")] if portrait else []),
            Tag("TerrainManager", [height]),
            Tag("WorldManager", [water]),
            _objects(),
        ],
    )
    n = 2 * SIZE
    ids = np.full((n // 4, n // 4), 3 | 7 << 5 | 9 << 10, "<u4")  # 9 is a ground-object entry
    alphas = np.full((n, n), EAST, "<u4")
    alphas[:, : n // 2] = WEST
    alphas[0, : n // 2] = 15 | 31 << 5  # material 3 half covering: the rest stays material 7
    terrain = [
        Attrib("MaterialMapWidth", struct.pack("<I", n)),
        Attrib(
            "MaterialSetFileName",
            MATERIAL_SET.replace("/test", "/fpp_test" if portrait else "/test").encode("utf-16-le"),
        ),
        Attrib("MaterialIDs2", ids.tobytes()),
        Attrib("MaterialAlphas2", alphas.tobytes()),
    ]
    if not portrait:
        coarse = [
            Attrib("width", struct.pack("<I", w)),
            Attrib("height", struct.pack("<I", w)),
            Attrib("map", (game * 2).astype("<i2").tobytes()),
        ]
        terrain.append(Tag("CoarseHeightMap", coarse))
    props = Tag("Props", [_prop("data/p/tree.prp", 1.0), _prop("data/p/rock.prp", 1.0), _prop("data/p/tree.prp", 3.0)])
    rd3d = filedb.write(Tag("", [Tag("Terrain", terrain), Tag("PropGrid", [props])]), version=1)
    info = Tag("", [Attrib("MapSize", struct.pack("<2i", SIZE, SIZE))])
    a6m = rda.write_v22(
        {"gamedata.data": filedb.write(Tag("", [gm]), version=1), "mapinfo.info": filedb.write(info, version=1)}
    )
    sets = (
        "<MaterialSet><ShaderType>0</>\n<Material><ID>7</>\n<Type>0</>\n<FileName>data/m/b.xml</>\n</>\n"
        "<Material><ID>3</>\n<Type>0</>\n<FileName>data/m/a.xml</>\n</>\n"
        "<Material><ID>9</>\n<Type>1</>\n<FileName></>\n<ObjectFileName>data/p/grass.prp</>\n</>\n"
    )
    return {
        PATH: a6m,
        LOOSE_RD3D: rda.write_v22({"rd3d.data": rd3d}),
        MATERIAL_SET: sets.encode(),
        "data/m/a.xml": b"<Material><DiffuseFileName>data/m/a_diff.psd</>\n</>",
        "data/m/b.xml": b"<Material><DiffuseFileName>data/m/b_diff.psd</>\n</>",
    }


def _asset(guid: int, body: bytes) -> bytes:
    return (
        b"<Asset>" + body.replace(b"@", b"<Values><Standard><GUID>%d</GUID></Standard>" % guid) + b"</Values></Asset>"
    )


def _variations(*cfgs: str) -> bytes:
    items = b"".join(b"<Item><Filename>%s</Filename></Item>" % c.encode() for c in cfgs)
    return b"<Object><Variations>" + items + b"</Variations></Object>"


def _assets() -> dict[str, bytes]:
    """assets.xml including two files; the game reads the later include first, so the earlier one's
    definition of 1001 is the one that stays."""
    first, second = "data/config/game/asset/first.xml", "data/dlc01/config/game/asset/asset.xml"
    groups = b"".join(b"<Group><FileName>%s</FileName></Group>" % p.encode() for p in (first, second))
    return {
        ASSETS_2205.assets: b"<AssetList><Groups>" + groups + b"</Groups></AssetList>",
        first: _asset(1001, b"@" + _variations("data/g/a0.cfg", "data/g/a1.cfg"))
        + _asset(1003, b"<Template>Dock</Template>@"),
        second: _asset(1001, b"@" + _variations("data/g/old.cfg"))
        + _asset(1002, b"<BaseAssetGUID>1001</BaseAssetGUID>@"),
        ASSETS_2205.templates: b"<Template><Name>Dock</Name><Properties>"
        + _variations("data/g/dock.cfg")
        + b"</Properties></Template>",
    }


def test_a_2205_session_reads_its_ground_as_the_bake_lays_it():
    s = a7m_session.Session2205(FakeFiles(_session()), PATH)
    assert s.size == SIZE and s.texels == 2 and s.material_set == MATERIAL_SET
    ids, alphas = s.material_maps()
    assert ids.shape == (2, 2) and alphas.shape == (8, 8)
    mats = s.materials()
    assert sorted(mats) == [3, 7] and mats[7]["order"] < mats[3]["order"]  # 9 places grass, nothing baked
    mids, wts = s.weights()
    assert mids.shape == (6, 8, 8) and (mids[0] == 3).all() and (mids[1] == 7).all()
    np.testing.assert_allclose(wts.sum(axis=0), 1, atol=1e-6)
    assert (wts[0][1:, :4] == 1).all() and (wts[1][1:, :4] == 0).all()  # 3 laid over 7 though in slot 0
    assert (wts[1][:, 4:] == 1).all() and (wts[2] == 0).all()
    assert wts[0][0, 0] == pytest.approx(15 / 31) and wts[1][0, 0] == pytest.approx(16 / 31)


def test_a_2205_session_lists_its_objects():
    s = a7m_session.Session2205(FakeFiles(_session()), PATH)
    turned, free, river, past, own = s.objects()
    assert river["group"] == "EditorObject" and river["name"] == "guid:1608"
    assert past["group"] == "NaturePreset" and past["variation"] == 9 and own["cfg"] == "data/g/a1.cfg"
    assert turned["group"] == "GameObject" and turned["guid"] == 1001 and turned["variation"] == 1
    assert turned["position"] == [1.5, 2.0, 2.5] and turned["scale"] == 1.0 and turned["folder"] == 1
    a = -math.radians(90) / 2  # Direction turns against the quaternion's sense
    assert turned["rotation"] == pytest.approx([0, math.sin(a), 0, math.cos(a)])
    assert free["group"] == "EditorObject" and free["position"] == [3.0, 0.0, 0.25] and free["folder"] == 7
    assert free["rotation"] == [0, 0, 1, 0] and free["scale"] == 2.5 and free["variation"] == 0


def test_2205_assets_resolve_bases_templates_and_the_games_order():
    files = _assets()
    assets = AssetModels.read(lambda p: files[p], ASSETS_2205)
    assert assets.asset(1001).variations == ["data/g/a0.cfg", "data/g/a1.cfg"]
    assert assets.asset(1002).variations == ["data/g/a0.cfg", "data/g/a1.cfg"]  # BaseAssetGUID
    assert assets.asset(1003).variations == ["data/g/dock.cfg"]  # the template's list
    assert assets.asset(4) is None


def _viewer(tmp_path: Path, **session) -> a7mviewer.A6mViewer:
    files = {**_session(**session), **_assets(), "data/g/a1.cfg": b"<Config />"}
    (tmp_path / "maindata").mkdir(parents=True)
    (tmp_path / "maindata" / "data0.rda").write_bytes(rda.write_v22(files))
    game = installs.GAMES["anno2205"]
    return a7mviewer.A6mViewer(game, game.tree(tmp_path), PATH, tmp_path / "cache")


def test_the_2205_viewer_draws_each_object_with_its_variation(tmp_path: Path):
    turned, free, river, past, own = _viewer(tmp_path).objects()
    assert past["cfg"] == "" and own["cfg"] == "data/g/a1.cfg"  # past the list: the game's placeholder
    assert turned["cfg"] == "data/g/a1.cfg" and turned["marker"] is None
    assert free["cfg"] == "" and free["marker"] == "EditorObject"  # its variation 0, a0.cfg, is not in the archives
    assert river["marker"] == "EditorObject" and river["key"] == islandedits.key("guid:1608", [0.0, 0.0, 0.0])


def test_a_2205_session_reads_rivers_props_and_object_folders():
    s = a7m_session.Session2205(FakeFiles(_session()), PATH)
    assert s.splines() == [("River", [(0.0, 0.5, 1.0), (3.0, 0.5, 1.0)], [2.0, 2.0])]
    props = s.props()
    assert [p["cfg"] for p in props] == ["data/p/tree.prp", "data/p/rock.prp", "data/p/tree.prp"]
    assert props[2]["position"] == [3.0, 0.5, 2.0] and props[2]["scale"] == [1.0, 1.0, 1.0]
    assert s.folder_paths() == {1: [1], 7: [1, 7]}
    assert s.has_sea
    seen: dict[str, int] = {}
    assert [a7m_session.unique_key("k", seen) for _ in range(3)] == ["k", "k#2", "k#3"]


def test_a_portrait_scene_opens_without_render_heights_water_or_its_material_set():
    files = _session(portrait=True)
    s, normal = a7m_session.Session2205(FakeFiles(files), PATH), a7m_session.Session2205(FakeFiles(_session()), PATH)
    assert s.size == SIZE and not s.has_sea
    np.testing.assert_array_equal(s.hm, normal.hm)  # the game's own heights stand in at the same scale
    assert s.material_set == MATERIAL_SET and sorted(s.materials()) == [3, 7]  # fpp_test is the shipped test
    del files[MATERIAL_SET]
    assert a7m_session.Session2205(FakeFiles(files), PATH).materials() == {}  # nothing ships: the bake's grey


def test_the_2205_viewer_hands_the_editor_the_sea_water_props_and_folders(tmp_path: Path):
    viewer = _viewer(tmp_path)
    island = viewer.island()
    assert island["sea"] is True and island["game"] == "anno2205"
    assert [(s["kind"], len(s["points"])) for s in island["splines"]] == [("River", 2)]
    assert [p["key"] for p in island["props"]] == [
        islandedits.key("data/p/tree.prp", [1.0, 0.5, 2.0]),
        islandedits.key("data/p/rock.prp", [1.0, 0.5, 2.0]),
        islandedits.key("data/p/tree.prp", [3.0, 0.5, 2.0]),
    ]
    assert [o["folder_path"] for o in island["objects"]][:2] == ["folder 1", "folder 1 / 7"]
    assert editsession.type_of(island["objects"][1]) == "folder 1 / 7"  # the object list's category
    assert _viewer(tmp_path / "portrait", portrait=True).island()["sea"] is False
