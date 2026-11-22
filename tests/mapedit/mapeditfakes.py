"""Stand-ins for the map editor: level files, session files and the game they are read from."""

from __future__ import annotations

import struct

from annotoolbox.formats import filedb, rda
from annotoolbox.formats.filedb import SAttrib
from annotoolbox.formats.filedb import STag as STag_filedb
from annotoolbox.mapedit import levelfile, pool
from annotoolbox.sources import gametree

STEM = "data/base/provinces/roman/templates/pool/t_01/t_01"


ISLAND = "data/base/provinces/roman/islands/pool/i_01/i_01.a7m"


def ints_bytes(*v: int) -> bytes:
    return struct.pack(f"<{len(v)}i", *v)


def u16_bytes(v: int) -> bytes:
    return struct.pack("<H", v)


def slot_tag(x: int, z: int, size: int = 0, kind: int = 0) -> STag_filedb:
    config = STag_filedb(
        "Config", [STag_filedb("Type", [SAttrib("id", u16_bytes(kind))] if kind else []), STag_filedb("Difficulty", [])]
    )
    element = STag_filedb(
        "Element",
        [
            SAttrib("Position", ints_bytes(x, z)),
            *([SAttrib("Size", u16_bytes(size))] if size else []),
            STag_filedb("Difficulty", []),
            config,
        ],
    )
    return STag_filedb("TemplateElement", [SAttrib("ElementType", struct.pack("<I", 1)), element])


def start(x: int, z: int) -> STag_filedb:
    return STag_filedb(
        "TemplateElement",
        [SAttrib("ElementType", struct.pack("<I", 2)), STag_filedb("Element", [SAttrib("Position", ints_bytes(x, z))])],
    )


def fixed_object(x: int, z: int, path: str = ISLAND, rotation: int = 0, kind: int = 0) -> STag_filedb:
    config = STag_filedb(
        "RandomIslandConfig",
        [
            STag_filedb(
                "value",
                [
                    STag_filedb("Type", [SAttrib("id", u16_bytes(kind))]),
                    STag_filedb("Difficulty", [SAttrib("id", u16_bytes(0))]),
                ],
            )
        ],
    )
    element = STag_filedb(
        "Element",
        [
            SAttrib("Position", ints_bytes(x, z)),
            SAttrib("MapFilePath", path.encode("utf-16-le")),
            SAttrib("Rotation90", bytes([rotation])),
            SAttrib("IslandLabel", b"isle"),
            SAttrib("FertilityGuids", b""),
            SAttrib("RandomizeFertilities", b"\x00"),
            STag_filedb("MineSlotActivation", []),
            config,
        ],
    )
    return STag_filedb("TemplateElement", [element])


def template_bytes(version: int = 3, size: int = 2048, elements=None) -> bytes:
    elements = (
        elements
        if elements is not None
        else [fixed_object(800, 800), slot_tag(200, 200, 1), slot_tag(1200, 300), start(600, 1500)]
    )
    mt = STag_filedb(
        "MapTemplate",
        [
            SAttrib("Size", ints_bytes(size, size)),
            SAttrib("PlayableArea", ints_bytes(20, 20, size - 28, size - 28)),
            SAttrib("InitialPlayableArea", ints_bytes(0, 0, 0, 0)),
            STag_filedb("RandomlyPlacedThirdParties", []),
            SAttrib("ElementCount", struct.pack("<I", len(elements))),
            *elements,
        ],
    )
    return filedb.write(STag_filedb("", [mt]), version=version)


# A session of 64 tiles with every kind of absolute reference the inventory lists.
LEVEL_SIZE = 64


def cells_bits(w: int, h: int, cells: set[tuple[int, int]]) -> bytes:
    stride = (w + 31) // 32 * 32
    out = bytearray(stride * h // 8)
    for x, z in cells:
        k = z * stride + x
        out[k >> 3] |= 1 << (k & 7)
    return bytes(out)


def session_bytes() -> bytes:
    heights = [0] * (2 * LEVEL_SIZE + 1) ** 2
    heights[5 * (2 * LEVEL_SIZE + 1) + 7] = 999  # one marked sample at row 5, column 7
    hm = STag_filedb("HeightMap", [SAttrib("Width", struct.pack("<I", 2 * LEVEL_SIZE + 1)), SAttrib("Height", struct.pack("<I", 2 * LEVEL_SIZE + 1)),
                           SAttrib("HeightMap", struct.pack(f"<{len(heights)}H", *heights))])  # fmt: skip
    water = STag_filedb(
        "Water",
        [
            SAttrib("x", struct.pack("<I", LEVEL_SIZE)),
            SAttrib("y", struct.pack("<I", LEVEL_SIZE)),
            SAttrib("bits", cells_bits(LEVEL_SIZE, LEVEL_SIZE, {(3, 4)})),
        ],
    )
    env = STag_filedb("EnvironmentGRid", [SAttrib("x", struct.pack("<I", LEVEL_SIZE // 4)), SAttrib("y", struct.pack("<I", LEVEL_SIZE // 4)),
                                  SAttrib("val", bytes(range((LEVEL_SIZE // 4) ** 2)))])  # fmt: skip
    blocks = [
        STag_filedb(
            "block",
            [
                SAttrib("mode", b"\x01"),
                SAttrib("x", u16_bytes(16)),
                SAttrib("y", u16_bytes(16)),
                SAttrib("default", u16_bytes(0)),
            ],
        )
    ]
    for by in range(0, LEVEL_SIZE, 16):
        for bx in range(0, LEVEL_SIZE, 16):
            b = STag_filedb("block", [SAttrib("mode", b"\x02")])
            b.children += ([SAttrib("x", u16_bytes(bx))] if bx else []) + ([SAttrib("y", u16_bytes(by))] if by else [])
            b.children.append(SAttrib("default", u16_bytes(1)))
            blocks.append(b)
    blocks.append(STag_filedb("block", [SAttrib("mode", b"\x00")]))
    area_ids = STag_filedb(
        "AreaIDs",
        [
            SAttrib("SparseEnabled", b"\x01"),
            SAttrib("x", struct.pack("<I", LEVEL_SIZE)),
            SAttrib("y", struct.pack("<I", LEVEL_SIZE)),
            *blocks,
        ],
    )
    obj = STag_filedb("#1", [SAttrib("guid", struct.pack("<I", 5)), SAttrib("Position", struct.pack("<3f", 10.5, 2.0, 20.0)),
                     SAttrib("QuestAreaRect", ints_bytes(1, 2, 3, 4))])  # fmt: skip
    inner = filedb.write(
        STag_filedb(
            "", [STag_filedb("AreaObjectManager", [STag_filedb("GameObject", [STag_filedb("objects", [obj])])])]
        ),
        version=3,
    )
    amd = STag_filedb(
        "AreaManagerData",
        [
            SAttrib("#32768", u16_bytes(1)),
            STag_filedb("#1", [SAttrib("ByteCount", struct.pack("<I", len(inner))), SAttrib("Data", inner)]),
        ],
    )
    view = STag_filedb("View", [SAttrib("From", struct.pack("<3f", 30.0, 50.0, 40.0))])
    cameras = STag_filedb("CameraManager", [STag_filedb("EditorSavedCameraStates", [STag_filedb("#1", [view])])])
    channels = [
        STag_filedb("#1", [STag_filedb("#1", [SAttrib("Value", struct.pack("<f", float(10 * c)))])]) for c in range(7)
    ]
    flights = STag_filedb(
        "CameraSequenceManager",
        [STag_filedb("Sequence", [STag_filedb("Cam", [STag_filedb("AllKeyframes", channels)])])],
    )
    gsm = STag_filedb(
        "GameSessionManager",
        [
            STag_filedb("SessionSettings", [SAttrib("PlayableArea", ints_bytes(4, 4, 60, 60))]),
            area_ids,
            amd,
            STag_filedb("TerrainManager", [SAttrib("WorldSize", ints_bytes(LEVEL_SIZE, LEVEL_SIZE)), hm]),
            cameras,
            STag_filedb("WorldManager", [water, STag_filedb("EnvironmentGrid", [env])]),
            flights,
            STag_filedb("WeatherManager", [SAttrib("WindDirection", struct.pack("<f", 1.0))]),
            STag_filedb("MysteryManager", [SAttrib("Thing", ints_bytes(7))]),
        ],
    )
    return rda.write_v22(
        {"gamedata.data": filedb.write(STag_filedb("", [SAttrib("FileVersion", ints_bytes(8)), gsm]), version=3)}
    )


def mapedit_asset() -> str:
    return (
        "<Asset><Template>MapTemplate</Template><Values><Standard><GUID>3377</GUID><Name>t</Name></Standard><MapTemplate>"
        f"<TemplateFilename>{STEM}.a7t</TemplateFilename><TemplateRegion>Roman</TemplateRegion>"
        "<EnlargedTemplateFilename>x.a7t</EnlargedTemplateFilename>"
        "<HorizonIslands><Item><Position><X>3600</X><Y>-40</Y><Z>900</Z></Position></Item></HorizonIslands>"
        "</MapTemplate></Values></Asset>"
    )


def mapedit_game(tmp_path):
    files = {
        STEM + ".a7tinfo": template_bytes(),
        STEM + ".a7t": session_bytes(),
        STEM + ".a7te": levelfile.editor_level(LEVEL_SIZE),
        pool.ASSET_FILES["anno117"]: (
            "<Assets>"
            + mapedit_asset()
            + "<Asset><Template>RandomIsland</Template><Values><Standard><GUID>1</GUID><Name>i</Name>"
            f"</Standard><RandomIsland><FilePath>{ISLAND}</FilePath><IslandRegion>Roman</IslandRegion>"
            "<IslandType>Normal</IslandType><IslandPlaceholderSize>Medium</IslandPlaceholderSize></RandomIsland></Values></Asset>"
            "</Assets>"
        ).encode(),
        ISLAND.replace(".a7m", ".a7minfo"): filedb.write(
            STag_filedb(
                "", [SAttrib("MapSize", ints_bytes(192, 192)), SAttrib("ActiveMapRect", ints_bytes(16, 16, 176, 176))]
            )
        ),
        ISLAND: b"x",
    }
    for path, data in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(data)
    return gametree.loose_tree(tmp_path, "117")
