"""Builders of the other games' island and session files: 1800 islands, 2070 islands and 2205 sessions."""

from __future__ import annotations

import math
import struct

import numpy as np
from tests.fakes import FakeFiles, FakeTree

from annotoolbox.editor.formats.a7m import reader as a7m_reader
from annotoolbox.editor.formats.a7m import session as a7m_session
from annotoolbox.editor.formats.a7m import sessionground as a7m_sessionground
from annotoolbox.editor.formats.a7m.reader import archive_members
from annotoolbox.editor.formats.isd import edit as isd_edit
from annotoolbox.formats import filedb, isd2070, rda
from annotoolbox.formats.feuread import STag
from annotoolbox.formats.filedb import SAttrib
from annotoolbox.formats.filedb import STag as STag_filedb
from annotoolbox.formats.isd2070 import FIXED, SNode
from annotoolbox.formats.rdmwrite import FORMAT_P4H_N4B_G4B_B4B_T2H, SRdmMeshData, SRdmSubmesh, write_rdm
from annotoolbox.islands import islandedits, objects2070
from annotoolbox.islands.islandterrain import TMC_VERTEX

ISLAND1800_PATH = "data/sessions/islands/pool/moderate/isle/isle.a7m"


MATERIAL_SET = "data/config/engine/material_sets/test.xml"


ISLAND1800_SIZE = 4  # tiles across; 2 material texels per tile, ids in 2 x 2 blocks, as 1800 stores them


def grid1800_tag(name: str, bits: bytes) -> STag_filedb:
    return STag_filedb(
        name,
        [
            SAttrib("x", struct.pack("<I", ISLAND1800_SIZE)),
            SAttrib("y", struct.pack("<I", ISLAND1800_SIZE)),
            SAttrib("bits", bits),
        ],
    )


CHAIR_PATH = "data/graphics/props/furniture/chair_01.prp"


def river1800_tag() -> STag_filedb:
    curve = STag_filedb(
        "BezierCurve",
        [
            STag_filedb("#1", [SAttrib("p", struct.pack("<3f", x, 0.0, 1.0)), SAttrib("w", struct.pack("<f", 2.0))])
            for x in (0, 3)
        ],
    )
    return STag_filedb(
        "#1",
        [
            SAttrib("guid", struct.pack("<I", 5)),
            SAttrib("Position", bytes(12)),
            STag_filedb("River", []),
            STag_filedb("BezierPath", [STag_filedb("Path", [curve])]),
        ],
    )


def _prop_grid() -> STag_filedb:
    """One prop (the chair) in the render data's prop grid."""
    prop = STag_filedb(
        "#1",
        [
            SAttrib("Index", struct.pack("<I", 0)),
            SAttrib("Position", struct.pack("<3f", 2.0, 0.5, 2.0)),
            SAttrib("Rotation", struct.pack("<4f", 0, 0, 0, 1)),
            SAttrib("Scale", struct.pack("<3f", 1, 1, 1)),
            SAttrib("Color", struct.pack("<4f", 1, 1, 1, 1)),
        ],
    )
    names = STag_filedb("FileNames", [SAttrib("#32768", CHAIR_PATH.replace("/", "\\").encode("utf-16-le"))])
    return STag_filedb("PropGrid", [names, STag_filedb("Instances", [prop])])


def island1800_files(version: int, material_set: bool) -> dict[str, bytes]:
    """A 4 x 4 island as 1800 stores one: heights, land, one object, a river, a prop, material maps in rd3d."""
    w = 2 * ISLAND1800_SIZE + 1
    game = (np.arange(w * w, dtype="<i2") * 4).reshape(w, w)  # 1/128 world unit
    half = math.sqrt(0.5)
    obj = STag_filedb(
        "#1",
        [
            SAttrib("guid", struct.pack("<I", 100849)),
            SAttrib("ID", struct.pack("<2I", 7, 0x1234)),  # serial, type
            SAttrib("Position", struct.pack("<3f", 1.5, 2.0, 2.5)),
            STag_filedb("Mesh", [SAttrib("Orientation", struct.pack("<4f", 0, half, 0, half))]),
        ],
    )
    area = STag_filedb(
        "",
        [
            STag_filedb(
                "AreaObjectManager",
                [
                    STag_filedb("GameObject", [STag_filedb("objects", [obj])]),
                    STag_filedb("EditorObject", [STag_filedb("objects", [river1800_tag()])]),
                ],
            )
        ],
    )
    area_raw = filedb.write(area, version=version)
    gm = STag_filedb(
        "GameSessionManager",
        [
            STag_filedb(
                "TerrainManager",
                [
                    STag_filedb(
                        "HeightMap",
                        [
                            SAttrib("Width", struct.pack("<I", w)),
                            SAttrib("Height", struct.pack("<I", w)),
                            SAttrib("HeightMap", game.tobytes()),
                        ],
                    )
                ],
            ),
            STag_filedb(
                "WorldManager", [grid1800_tag("Water", b"\x03\0\0\0" * ISLAND1800_SIZE)]
            ),  # the west half is land
            STag_filedb(
                "AreaManagerData",
                [
                    STag_filedb(
                        "#1", [SAttrib("ByteCount", struct.pack("<I", len(area_raw))), SAttrib("Data", area_raw)]
                    )
                ],
            ),
        ],
    )
    n = 2 * ISLAND1800_SIZE  # material texels across
    ids = np.full((n // 2, n // 2), 3 | 7 << 5, "<u4")  # slot 0: material 3, slot 1: material 7
    alphas = np.full((n, n), 31 | 31 << 5, "<u4")
    alphas[:, : n // 2] = 31  # material 7 only on the east half
    terrain = [
        SAttrib("MaterialMapWidth", struct.pack("<I", n)),
        SAttrib("MaterialIDBlockSize", struct.pack("<I", 2)),
        SAttrib("MaterialIDs2", ids.tobytes()),
        SAttrib("MaterialAlphas2", alphas.tobytes()),
        STag_filedb(
            "CoarseHeightMap",
            [
                SAttrib("width", struct.pack("<I", w)),
                SAttrib("height", struct.pack("<I", w)),
                SAttrib("map", (game * 2).astype("<i2").tobytes()),
            ],
        ),
    ]
    if material_set:
        terrain.insert(0, SAttrib("MaterialSetFileName", MATERIAL_SET.encode("utf-16-le")))
    rd3d = STag_filedb("", [STag_filedb("Terrain", terrain), _prop_grid()])
    info = STag_filedb(
        "",
        [
            SAttrib("MapSize", struct.pack("<2i", ISLAND1800_SIZE, ISLAND1800_SIZE)),
            SAttrib("ActiveMapRect", struct.pack("<4i", 0, 0, 4, 4)),
        ],
    )
    a7m = rda.write_v22(
        {
            "gamedata.data": filedb.write(STag_filedb("", [gm]), version=version),
            "rd3d.data": filedb.write(rd3d, version=version),
        }
    )
    sets = "<MaterialSet><Material><ID>3</>\n<FileName>data/m/a.xml</>\n</>\n<Material><ID>7</>\n<FileName>data/m/b.xml</>\n</>\n"
    return {
        ISLAND1800_PATH: a7m,
        ISLAND1800_PATH[:-4] + ".a7minfo": filedb.write(info, version=version),
        MATERIAL_SET: sets.encode(),
        a7m_reader.DEFAULT_MATERIAL_SET: sets.encode(),
        "data/m/a.xml": b"<Material><DiffuseFileName>data/m/a_diff.psd</>\n</>",
        "data/m/b.xml": b"<Material><DiffuseFileName>data/m/b_diff.psd</>\n</>",
    }


ISLAND2070_SOURCE = "data/levels/islands/normal/isle.isd"


ISLAND2070_SIZE, CHUNKS = 32, 2


ISLAND2070_ASSETS = """<Assets>
<Asset><Template>Tree</Template><Values><Standard><GUID>40</GUID><Name>palm</Name></Standard>
<Object><Variations><Item><Filename>data/graphics/landscape/trees/palm_01.cfg</Filename></Item>
<Item><Filename>data/graphics/landscape/trees/palm_02.cfg</Filename></Item></Variations></Object></Values></Asset>
<Asset><Template>SimpleObject</Template><Values><Standard><GUID>333</GUID><Name>crate</Name></Standard>
<Object><Variations><Item><Filename>data/graphics/props/crate.cfg</Filename></Item></Variations></Object></Values></Asset>
<Asset><Template>Resource</Template><Values><Standard><GUID>350</GUID><Name>slot</Name></Standard>
<Object><Variations><Item><Filename>data/graphics/props/crate.cfg</Filename></Item></Variations></Object></Values></Asset>
</Assets>"""


GROUND = """<Textures>
<Texture><GUID>0</GUID><Name>water</Name><DiffuseTexture>data/t/water_diff.png</DiffuseTexture><R>20</R><G>60</G><B>90</B></Texture>
<Texture><GUID>52</GUID><Name>gras_01</Name><DiffuseTexture>data/t/gras_diff.png</DiffuseTexture>
<NormalTexture>data/t/gras_norm.png</NormalTexture><R>60</R><G>120</G><B>40</B></Texture>
<Texture><GUID>78</GUID><Name>beach_01</Name><DiffuseTexture>data/t/beach_diff.png</DiffuseTexture><R>200</R><G>180</G><B>120</B></Texture>
</Textures>"""


def _obj(oid: int, guid: int, x: float, z: float, nature: bool = True, var: int | None = None) -> SNode:
    kids = [SNode("m_ID", str(oid)), SNode("m_GUID", str(guid))]
    if var:
        kids.append(SNode("m_Variation", str(var)))
    kids += [
        SNode("m_Position", data=struct.pack("<qqq", round(x * FIXED), round(z * FIXED), round(0.7 * FIXED))),
        SNode("m_PlayerID", "15"),
        SNode("m_Direction", "0"),
    ]
    if nature:
        kids.append(
            SNode(
                "Nature",
                children=[SNode("m_Scale", "1.000000"), SNode("m_Orientation", data=struct.pack("<4f", 0, 0, 0, 1))],
            )
        )
    else:
        render = isd_edit.RENDER_HEAD + isd_edit.RENDER_TRANSFORM.pack(x, 0.7, z, 0, 0, 0, 1, 0.5)
        kids.append(SNode("PropertyMesh", children=[SNode("m_RenderParameters", data=render + isd_edit.RENDER_TINT)]))
    return SNode("Object", children=kids)


def isd2070_bytes() -> bytes:
    """A 32x32 island in 2070's form: 2x2 chunks at height 1, the sea bed under grass, a sand
    rim in the first chunk, three objects (two trees, a prop) and a resource in a group."""
    per = ISLAND2070_SIZE // CHUNKS
    elements = []
    for k in range(CHUNKS * CHUNKS):
        heights = np.ones((per + 1, per + 1), "<f4").tobytes()
        layers = [(0, 255), (52, 200)] + ([(78, 100)] if k == 0 else [])
        el = SNode(
            "Element",
            children=[SNode("HeightMap", children=[SNode("Width", str(per + 1)), SNode("Data", data=heights)])],
        )
        for guid, alpha in layers:
            am = SNode(
                "AlphaMap", children=[SNode("Width", str(per + 1)), SNode("Data", data=bytes([alpha]) * (per + 1) ** 2)]
            )
            el.children.append(SNode("TexIndexData", children=[SNode("TextureIndex", str(guid)), am]))
        elements.append(el)
    chunk_map = SNode("ChunkMap", children=[SNode("Width", str(CHUNKS)), SNode("Height", str(CHUNKS)), *elements])
    terrain = SNode(
        "Terrain",
        short=True,
        children=[
            SNode("TileCountX", str(ISLAND2070_SIZE)),
            SNode("TileCountZ", str(ISLAND2070_SIZE)),
            chunk_map,
        ],
    )
    v2 = np.full((ISLAND2070_SIZE, ISLAND2070_SIZE), 1024, "<i2").tobytes()
    groups = SNode(
        "Objects",
        children=[
            SNode("Handle", children=[SNode("Objects", children=[_obj(10, 350, 5, 5, nature=False)])]),
            SNode("Simple", children=[SNode("Objects", children=[_obj(11, 333, 8, 8, nature=False)])]),
            SNode("Nature", children=[SNode("Objects", children=[_obj(12, 40, 10, 12), _obj(13, 40, 20, 22, var=1)])]),
        ],
    )
    gop = SNode(
        "m_GOPManager",
        children=[
            SNode("m_GRIDManager", children=[SNode("m_HeightMap_v2", data=v2)]),
            groups,
            SNode("ObjectGroups", children=[SNode("i", data=struct.pack("<2I", 12, 13))]),
        ],
    )
    root = SNode(
        "",
        children=[
            SNode("Width", str(ISLAND2070_SIZE)),
            SNode("Height", str(ISLAND2070_SIZE)),
            SNode("Clime", "0"),
            terrain,
            gop,
        ],
    )
    return isd2070.serialize(root)


PALM = r"""<m_Config><m_Models><m_Config><m_FileName>data\graphics\landscape\trees\rdm\palm_lod0.rdm</m_FileName>
<m_ConfigType>MODEL</m_ConfigType><m_Materials>
<m_Config><m_Name>bark</m_Name><m_DiffuseTexture>data\graphics\landscape\trees\maps\palm_diff.png</m_DiffuseTexture>
<m_DiffuseEnabled>1</m_DiffuseEnabled><m_ConfigType>MATERIAL</m_ConfigType></m_Config>
<m_Config><m_Name>hole</m_Name><m_ShaderIndex>4</m_ShaderIndex><m_ConfigType>MATERIAL</m_ConfigType></m_Config>
</m_Materials></m_Config></m_Models><m_ConfigType>ROOT</m_ConfigType></m_Config>"""


def palm_rdm() -> bytes:
    """A mesh of two triangles, one drawn with the bark, one with the terrain cut-out."""
    rows = b"".join(struct.pack("<4e", x, y, z, 1.0) + bytes((128, 255, 128, 0)) * 3 + bytes(4) for x, y, z in
                    ((0, 0, 0), (1, 0, 0), (0, 2, 0), (1, 2, 0)))  # fmt: skip
    subs = [SRdmSubmesh(0, 3, 0), SRdmSubmesh(3, 3, 1)]
    mesh = SRdmMeshData("palm", rows, 24, FORMAT_P4H_N4B_G4B_B4B_T2H, [0, 1, 2, 2, 1, 3], ["bark", "hole"], subs,
                       (0.0, 0.0, 0.0, 1.0, 2.0, 0.0))  # fmt: skip
    return write_rdm(mesh)


def install(extra: dict[str, bytes] | None = None) -> FakeTree:
    """A 2070 install: the island, the assets, the ground textures and a palm model (the other palm draws nothing)."""
    return FakeTree(
        {
            ISLAND2070_SOURCE: isd2070_bytes(),
            objects2070.ASSETS_2070: ISLAND2070_ASSETS.encode(),
            "data/config/engine/groundtextures.xml": GROUND.encode(),
            "data/graphics/landscape/trees/palm_01.cfg": b"<Config/>",
            "data/graphics/landscape/trees/palm_02.cfg": PALM.encode(),
            "data/graphics/landscape/trees/rdm/palm_lod0.rdm": palm_rdm(),
            "data/graphics/landscape/trees/maps/palm_diff_0.dds": b"DDS ",
            **(extra or {}),
        }
    )


def island_node(sea_level: str | None = "0") -> SNode:
    sea = [SNode("SeaLevel", sea_level)] if sea_level is not None else []
    return SNode(
        "",
        children=[
            SNode("Width", "64"),
            SNode("Height", "64"),
            SNode("Clime", "0"),
            SNode("Difficulty", "0"),
            SNode("UsedChunks", children=[]),
            SNode("Rivers", children=[]),
            SNode("RiverGrid", children=[]),
            *sea,
            SNode("Sandbanks", children=[]),
        ],
    )


SESSION2205_PATH = "data/sessions/session_test/session_test.a6m"


SESSION2205_GAMEDATA, MAPINFO = f"{SESSION2205_PATH}|gamedata.data", f"{SESSION2205_PATH}|mapinfo.info"


LOOSE_RD3D = "data/sessions/session_test/_a6mdata/session_test/rd3d.data"


RD3D = f"{LOOSE_RD3D}|rd3d.data"  # the loose rd3d.data's member: what the game reads for a map that has one


OLD_ARCHIVE = "maindata/data3.rda"


def u16(text: str) -> bytes:
    return text.encode("utf-16-le")


def archive_entry(path: str, archive: int, position: int, size: int, flags: int = 1) -> STag_filedb:
    fields = [SAttrib("FileName", u16(path))]
    for name, fmt, value in (
        ("ArchiveFileIndex", "<I", archive),
        ("Position", "<Q", position),
        ("CompressedSize", "<Q", size // 2),
        ("UncompressedSize", "<Q", size),
        ("Flags", "<I", flags),
    ):
        if value:
            fields.append(SAttrib(name, struct.pack(fmt, value)))
    return STag_filedb("#1", fields)


def file_db_index(drop: str = "") -> bytes:
    """A ``file.db`` as the game ships one in shape: a folder tree of file entries, the archive list."""
    entries = [
        archive_entry(SESSION2205_GAMEDATA, 1, 4000, 900),
        archive_entry(MAPINFO, 1, 5000, 57),
        archive_entry(RD3D, 0, 600, 8000),
    ]
    entries = [e for e in entries if e.find("FileName").data != u16(drop)]
    files = STag_filedb("FileMap", [x for e in entries for x in (SAttrib("#32768", u16("n")), e)])
    tree = STag_filedb(
        "FileTree", [STag_filedb("PathMap", [SAttrib("#32768", u16("data")), STag_filedb("#1", [files])])]
    )
    archives = STag_filedb(
        "ArchiveFiles",
        [
            STag_filedb("#1", [SAttrib("#32768", u16(p)), SAttrib("#32768", b"\0")])
            for p in ("maindata/data0.rda", OLD_ARCHIVE)
        ],
    )
    last = SAttrib("LastArchiveFile", u16(OLD_ARCHIVE))
    return filedb.write(STag_filedb("", [STag_filedb("ArchiveMap", [tree, archives, last])]), version=1)


SESSION2205_SIZE = 4  # tiles across; 2 material texels per tile, one id texel per 4 x 4 of them, as 2205 stores them


WEST, EAST = 31 | 31 << 5 | 31 << 10, 31 << 5 | 31 << 10  # alphas of slots 0..2: material 3 only in the west


def _fixed(v: float) -> bytes:
    return struct.pack("<q", round(v * a7m_session.FIXED))


KIND = 0x80000000  # the high word of an object's ID: the asset's kind; the low word counts them


def _id(serial: int, high: int = KIND) -> SAttrib:
    return SAttrib("ID", struct.pack("<II", serial, high))


def session_river_tag() -> STag_filedb:
    curve = [
        STag_filedb("#1", [SAttrib("p", struct.pack("<3f", x, 0.5, 1.0)), SAttrib("w", struct.pack("<f", 2.0))])
        for x in (0, 3)
    ]
    return STag_filedb(
        "#1",
        [
            SAttrib("guid", struct.pack("<I", 1608)),
            _id(3),
            STag_filedb("BezierPath", [STag_filedb("Path", [STag_filedb("BezierCurve", curve)])]),
            STag_filedb("River", []),
        ],
    )


def _folder(folder: int, *inside: STag) -> STag_filedb:
    return STag_filedb("#1", [SAttrib("FolderID", struct.pack("<I", folder)), STag_filedb("Filter", list(inside))])


def session_objects_tag() -> STag_filedb:
    turned = STag_filedb(
        "#1",
        [
            SAttrib("guid", struct.pack("<I", 1001)),
            _id(1),
            SAttrib("Variation", struct.pack("<I", 1)),
            SAttrib("Direction", _fixed(90)),
            STag_filedb("Position", [SAttrib("x", _fixed(1.5)), SAttrib("y", _fixed(2.0)), SAttrib("z", _fixed(2.5))]),
        ],
    )
    free = STag_filedb(
        "#1",
        [
            SAttrib("guid", struct.pack("<I", 1002)),
            _id(2),
            SAttrib("ObjectFolderID", struct.pack("<I", 7)),
            STag_filedb("Position", [SAttrib("x", _fixed(3.0)), SAttrib("z", _fixed(0.25))]),
            STag_filedb(
                "FreeOrientation",
                [SAttrib("Orientation", struct.pack("<4f", 0, 0, 1, 0)), SAttrib("Scale", struct.pack("<f", 2.5))],
            ),
        ],
    )
    past = STag_filedb("#1", [SAttrib("guid", struct.pack("<I", 1001)), SAttrib("Variation", struct.pack("<I", 9))])
    own = STag_filedb(
        "#1",
        [
            SAttrib("guid", struct.pack("<I", 4)),
            STag_filedb("DynamicVariation", [SAttrib("ConfigFilename", r"data\g\a1.cfg".encode("utf-16-le"))]),
        ],
    )
    return STag_filedb(
        "GameObjectManager",
        [
            STag_filedb(
                "GameObjectLabelMap", [SAttrib("#32768", b"harbour"), SAttrib("#32768", struct.pack("<II", 1, KIND))]
            ),
            STag_filedb(
                "ObjectGroupFilterCollection", [STag_filedb("Root", [STag_filedb("Filter", [_folder(1, _folder(7))])])]
            ),
            STag_filedb("GameObject", [STag_filedb("objects", [turned])]),
            STag_filedb("EditorObject", [STag_filedb("objects", [free, session_river_tag()])]),
            STag_filedb("NaturePreset", [STag_filedb("objects", [past, own])]),
        ],
    )


def session_prop_tag(cfg: str, x: float) -> STag_filedb:
    return STag_filedb(
        "#1",
        [
            SAttrib("FileName", cfg.encode("utf-16-le")),
            SAttrib("Position", struct.pack("<3f", x, 0.5, 2.0)),
            SAttrib("Rotation", struct.pack("<4f", 0, 0, 0, 1)),
            SAttrib("Scale", struct.pack("<3f", 1, 1, 1)),
            SAttrib("Color", struct.pack("<4f", 1, 1, 1, 0)),
        ],
    )


def session2205_data(*, portrait: bool = False) -> dict[str, bytes]:
    """A 4 x 4 session as the last patch ships one: the map without rd3d.data, the loose rd3d.data an
    RDA archive around the tree file; material 3 in slot 0 but laid after material 7 by the set. A portrait scene
    has no render heights and no water, and names a material set the game does not ship (``fpp_test``)."""
    w = 2 * SESSION2205_SIZE + 1
    game = (np.arange(w * w, dtype="<i2") * 4).reshape(w, w)
    water = STag_filedb(
        "Water",
        [
            SAttrib("x", struct.pack("<I", SESSION2205_SIZE)),
            SAttrib("y", struct.pack("<I", SESSION2205_SIZE)),
            SAttrib("bits", b"\x03\0\0\0" * SESSION2205_SIZE),
        ],
    )
    height = STag_filedb(
        "HeightMap",
        [
            SAttrib("Width", struct.pack("<I", w)),
            SAttrib("Height", struct.pack("<I", w)),
            SAttrib("HeightMap", game.tobytes()),
        ],
    )
    gm = STag_filedb(
        "GameSessionManager",
        [
            STag_filedb("SessionSettings", [SAttrib("HasWater", b"\0")] if portrait else []),
            STag_filedb("TerrainManager", [height]),
            STag_filedb("WorldManager", [water]),
            session_objects_tag(),
        ],
    )
    n = 2 * SESSION2205_SIZE
    ids = np.full((n // 4, n // 4), 3 | 7 << 5 | 9 << 10, "<u4")  # 9 is a ground-object entry
    alphas = np.full((n, n), EAST, "<u4")
    alphas[:, : n // 2] = WEST
    alphas[0, : n // 2] = 15 | 31 << 5  # material 3 half covering: the rest stays material 7
    terrain = [
        SAttrib("MaterialMapWidth", struct.pack("<I", n)),
        SAttrib(
            "MaterialSetFileName",
            MATERIAL_SET.replace("/test", "/fpp_test" if portrait else "/test").encode("utf-16-le"),
        ),
        SAttrib("MaterialIDs2", ids.tobytes()),
        SAttrib("MaterialAlphas2", alphas.tobytes()),
    ]
    if not portrait:
        coarse = [
            SAttrib("width", struct.pack("<I", w)),
            SAttrib("height", struct.pack("<I", w)),
            SAttrib("map", (game * 2).astype("<i2").tobytes()),
        ]
        terrain.append(STag_filedb("CoarseHeightMap", coarse))
    props = STag_filedb(
        "Props",
        [
            session_prop_tag("data/p/tree.prp", 1.0),
            session_prop_tag("data/p/rock.prp", 1.0),
            session_prop_tag("data/p/tree.prp", 3.0),
        ],
    )
    rd3d = filedb.write(STag_filedb("", [STag_filedb("Terrain", terrain), STag_filedb("PropGrid", [props])]), version=1)
    info = STag_filedb("", [SAttrib("MapSize", struct.pack("<2i", SESSION2205_SIZE, SESSION2205_SIZE))])
    a6m = rda.write_v22(
        {"gamedata.data": filedb.write(STag_filedb("", [gm]), version=1), "mapinfo.info": filedb.write(info, version=1)}
    )
    sets = (
        "<MaterialSet><ShaderType>0</>\n<Material><ID>7</>\n<Type>0</>\n<FileName>data/m/b.xml</>\n</>\n"
        "<Material><ID>3</>\n<Type>0</>\n<FileName>data/m/a.xml</>\n</>\n"
        "<Material><ID>9</>\n<Type>1</>\n<FileName></>\n<ObjectFileName>data/p/grass.prp</>\n</>\n"
    )
    return {
        SESSION2205_PATH: a6m,
        LOOSE_RD3D: rda.write_v22({"rd3d.data": rd3d}),
        MATERIAL_SET: sets.encode(),
        "data/m/a.xml": b"<Material><DiffuseFileName>data/m/a_diff.psd</>\n</>",
        "data/m/b.xml": b"<Material><DiffuseFileName>data/m/b_diff.psd</>\n</>",
    }


def island2205_files() -> FakeFiles:
    return FakeFiles(session2205_data())


def opened_session2205() -> a7m_session.CSession2205:
    return a7m_session.CSession2205(island2205_files(), SESSION2205_PATH)


def session2205_edits(**kinds) -> dict:
    return {**islandedits.empty(), "source": SESSION2205_PATH, "game": "anno2205", **kinds}


def island_objects(isl: a7m_session.CSession2205, group: str) -> list[STag_filedb]:
    return [o for g, o in isl.object_tags() if g == group]


CODES = a7m_sessionground.CODES


def _nodes() -> STag_filedb:
    """Two mesh levels of one node each: vertices at tile (2, 2), (0, 0) and (3, 1), heights 1.0, 0.0 and 0.5."""
    rec = np.zeros(3, TMC_VERTEX)
    rec["x"], rec["z"] = [64, 0, 96], [64, 0, 32]
    rec["y"] = [32768 + CODES, 32768, 32768 + CODES // 2]
    rec["n"] = [128, 255, 128, 0, 127, 127]

    def level(size: int) -> STag_filedb:
        node = STag_filedb(
            "#1",
            [
                SAttrib("BoxMin", struct.pack("<3f", 0, 0, 0)),
                SAttrib("BoxMax", struct.pack("<3f", 4, 1, 4)),
                SAttrib("Vertices", rec.tobytes()),
                SAttrib("UsedMaterialBits", struct.pack("<I", 0b10)),
            ],
        )
        return STag_filedb("#1", [SAttrib("LevelSize", struct.pack("<I", size)), STag_filedb("Nodes", [node])])

    return STag_filedb("Levels", [level(1), level(1)])


def session_lake_tag() -> STag_filedb:
    curve = [STag_filedb("#1", [SAttrib("p", struct.pack("<3f", x, 0.5, z))]) for x, z in ((0, 0), (2, 0), (2, 2))]
    bounds = [SAttrib("Minimum", struct.pack("<3f", 0, 0.5, 0)), SAttrib("Maximum", struct.pack("<3f", 2, 0.5, 2))]
    return STag_filedb(
        "#1",
        [
            SAttrib("guid", struct.pack("<I", 1609)),
            SAttrib("ID", struct.pack("<II", 5, 0x80000001)),
            STag_filedb("Position", [SAttrib("x", _fixed(1.0)), SAttrib("y", _fixed(0.5)), SAttrib("z", _fixed(1.0))]),
            STag_filedb("BezierPath", [STag_filedb("Path", [*bounds, STag_filedb("BezierCurve", curve)])]),
            STag_filedb("Lake", []),
        ],
    )


def session2205_files() -> FakeFiles:
    """The synthetic session with what a ground edit reaches: tile heights, sea depth, mesh levels, a lake, and a
    position and bounds on its river."""
    files = session2205_data()
    loose = archive_members(files[LOOSE_RD3D])[1]["rd3d.data"]
    rd = filedb.read(loose)
    coarse = np.frombuffer(rd.path("Terrain/CoarseHeightMap/map").data, "<i2").reshape(2 * SESSION2205_SIZE + 1, -1)
    centres = (coarse[1::2, 1::2] / CODES).astype("<f4")
    rd.children += [
        STag_filedb(
            "TerrainHeightMap",
            [
                SAttrib("width", struct.pack("<I", SESSION2205_SIZE)),
                SAttrib("height", struct.pack("<I", SESSION2205_SIZE)),
                SAttrib("map", centres.tobytes()),
            ],
        ),
        STag_filedb(
            "WaterInfos",
            [
                SAttrib("width", struct.pack("<I", 2)),
                SAttrib("height", struct.pack("<I", 2)),
                SAttrib("map", bytes([9, 1, 2, 3]) * 4),
            ],
        ),
    ]
    rd.path("Terrain").children.append(_nodes())
    files[LOOSE_RD3D] = rda.write_v22({"rd3d.data": filedb.write(rd, *filedb.name_ids(loose), version=1)})
    members = archive_members(files[SESSION2205_PATH])[1]
    raw = members["gamedata.data"]
    gd = filedb.read(raw)
    objects = gd.path("GameSessionManager/GameObjectManager/EditorObject/objects")
    river = objects.children[1]
    river.children.insert(2, STag_filedb("Position", [SAttrib("x", _fixed(0.0)), SAttrib("z", _fixed(1.0))]))
    river.path("BezierPath/Path").children.insert(0, SAttrib("Minimum", struct.pack("<3f", 0, 0.5, 1)))
    river.path("BezierPath/Path").children.insert(1, SAttrib("Maximum", struct.pack("<3f", 3, 0.5, 1)))
    objects.children.append(session_lake_tag())
    members["gamedata.data"] = filedb.write(gd, *filedb.name_ids(raw), version=1)
    files[SESSION2205_PATH] = rda.write_v22(members)
    return FakeFiles(files)
