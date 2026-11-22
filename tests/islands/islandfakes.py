"""Stand-ins for the island pipeline: edit documents, ground and page data, the island batch's stand-ins and the island
file listings."""

from __future__ import annotations

import struct
import types
import zlib
from pathlib import Path

import numpy as np
import pytest
from tests.fakes import FakeFiles, FakeTree

from annotoolbox.editor import islandprops
from annotoolbox.editor.formats.a7m.edit117 import CVanillaEditor
from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import SAttrib
from annotoolbox.formats.filedb import STag as STag_filedb
from annotoolbox.formats.rda import write_v22
from annotoolbox.islands.islandterrain import TMC_VERTEX


def u32_bytes(v):
    return struct.pack("<I", v)


ISLAND117_SOURCE = "data/base/provinces/roman/islands/pool/isle/isle.a7m"


ISLAND117_GAMEDATA = "data/base/provinces/roman/islands/pool/isle/_gamedata/isle/"


SET = "data/base/graphics/material_sets/test.xml"


ISLAND117_SIZE = 32  # tiles: 2 x 2 height pages of level 0


SEA = -1.0  # world units: the whole island starts under the sea


TEXELS = 4  # material texels per tile; ids per 2 x 2 block


def map_tag(name: str, values: np.ndarray) -> STag_filedb:
    return STag_filedb(name, [SAttrib("width", u32_bytes(values.shape[1])), SAttrib("height", u32_bytes(values.shape[0])),
                      SAttrib("map", values.tobytes())])  # fmt: skip


def bits_tag(name: str, n: int, value: int) -> STag_filedb:
    return STag_filedb(
        name,
        [
            SAttrib("x", u32_bytes(n)),
            SAttrib("y", u32_bytes(n)),
            SAttrib("bits", bytes([value]) * (n * (n + 31) // 32 * 4)),
        ],
    )


def river_tag() -> STag_filedb:
    points = [
        STag_filedb("#1", [SAttrib("p", struct.pack("<3f", x, 0.5, 20.0)), SAttrib("i", bytes(12)), SAttrib("o", bytes(12)),
                   SAttrib("w", struct.pack("<f", 2.0)), SAttrib("u0", struct.pack("<3f", 1, 3 + x, 1))])
        for x in (4.0, 8.0, 12.0)
    ]  # fmt: skip
    path = STag_filedb(
        "Path", [SAttrib("Minimum", bytes(12)), SAttrib("Maximum", bytes(12)), STag_filedb("BezierCurve", points)]
    )
    return STag_filedb("#1", [SAttrib("guid", u32_bytes(5)), SAttrib("ID", u32_bytes(3) + u32_bytes(9)), SAttrib("Position", bytes(12)),
                      STag_filedb("BezierPath", [path]), STag_filedb("River", [])])  # fmt: skip


def _coast() -> STag_filedb:
    """A coast line of three points running east at z 6, facing south (d = (0, 1)... as the vanilla ones: the normal)."""
    pts = [
        STag_filedb("#1", [SAttrib("p", struct.pack("<3f", x, 0.4, 6.0)), SAttrib("i", bytes(12)), SAttrib("o", bytes(12)),
                   SAttrib("d", struct.pack("<2f", 0.0, 1.0))])
        for x in (4.0, 8.0, 12.0)
    ]  # fmt: skip
    path = STag_filedb(
        "Path", [SAttrib("Minimum", bytes(12)), SAttrib("Maximum", bytes(12)), STag_filedb("BezierCurve", pts)]
    )
    return STag_filedb("#1", [SAttrib("guid", u32_bytes(7)), SAttrib("ID", u32_bytes(5) + u32_bytes(9)),
                      SAttrib("Position", struct.pack("<3f", 4, 0.4, 6)), STag_filedb("BezierPath", [path]), STag_filedb("CoastLine", [])])  # fmt: skip


def lake_tag() -> STag_filedb:
    pts = [STag_filedb("#1", [SAttrib("p", struct.pack("<3f", x, 0.2, z))]) for x, z in ((20, 20), (24, 20), (22, 24))]
    path = STag_filedb(
        "Path", [SAttrib("Minimum", bytes(12)), SAttrib("Maximum", bytes(12)), STag_filedb("BezierCurve", pts)]
    )
    return STag_filedb("#1", [SAttrib("guid", u32_bytes(6)), SAttrib("ID", u32_bytes(4) + u32_bytes(9)),
                      SAttrib("Position", struct.pack("<3f", 20, 0.2, 20)), STag_filedb("BezierPath", [path]), STag_filedb("Lake", [])])  # fmt: skip


def _mesh() -> bytes:
    """The level-2 meshes of the island: one node (0, 0) of four tiles, its four corners."""
    rec = np.zeros(4, TMC_VERTEX)
    rec["x"], rec["z"] = [0, 128, 0, 128], [0, 0, 128, 128]  # 1/32 tile
    rec["y"] = 32768 + int(SEA * 256)
    rec["n"] = [128, 255, 128, 255, 128, 128]
    nodes = [STag_filedb("#1", [SAttrib("Indices", struct.pack("<6H", 0, 2, 1, 1, 2, 3)), SAttrib("Vertices", rec.tobytes()),
                        SAttrib("UsedMaterialBits", u32_bytes(1 << 3))])]  # fmt: skip
    nodes += [
        STag_filedb(
            "#1", [SAttrib("Indices", b""), SAttrib("Vertices", b""), SAttrib("UsedMaterialBits", u32_bytes(0))]
        )
    ] * 3
    return filedb.write(
        STag_filedb(
            "",
            [
                STag_filedb(
                    "Meshes",
                    [
                        SAttrib("width", u32_bytes(32)),
                        SAttrib("height", u32_bytes(32)),
                        STag_filedb("map", nodes),
                    ],
                )
            ],
        )
    )


def _dds(arr: np.ndarray) -> bytes:
    header = bytearray(128)
    header[:4], header[12:16], header[16:20] = b"DDS ", u32_bytes(arr.shape[0]), u32_bytes(arr.shape[1])
    return bytes(header) + arr.astype("<u4").tobytes()


def _material_maps(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Material 3 everywhere, material 7 laid over it on the east half (the set draws 3 first)."""
    ids = np.full((n // 2, n // 2), 3 | 7 << 5, np.uint32)
    alphas = np.full((n, n), 31, np.uint32)
    alphas[:, n // 2 :] |= 31 << 5
    return ids, alphas


def island_object_tag(serial: int, cfg: str, pos) -> STag_filedb:
    return STag_filedb(
        "#1",
        [
            SAttrib("guid", struct.pack("<I", 37676)),
            SAttrib("ID", struct.pack("<I", serial) + b"\x01\x00\x00\x00"),
            SAttrib("Position", struct.pack("<3f", *pos)),
            STag_filedb(
                "Mesh", [SAttrib("Orientation", struct.pack("<4f", 0, 0, 0, 1)), SAttrib("Scale", struct.pack("<f", 1))]
            ),
            STag_filedb("DynamicVariation", [SAttrib("ConfigFilename", cfg.encode("utf-16-le"))]),
        ],
    )


def grid_tag(name: str, size: int, bits: bytes) -> STag_filedb:
    return STag_filedb(
        name, [SAttrib("x", struct.pack("<I", size)), SAttrib("y", struct.pack("<I", size)), SAttrib("bits", bits)]
    )


def island117_bytes(gm_extra=(), info_extra=()) -> tuple[bytes, bytes]:
    """A 4x4 island: two objects, all land, no trees; ``gm_extra`` and ``info_extra``: more nodes for the two files."""
    area = STag_filedb(
        "doc",
        [
            STag_filedb(
                "GameObject",
                [
                    STag_filedb(
                        "objects",
                        [
                            island_object_tag(1, "data/a/rock.cfg", (1, 0, 1)),
                            island_object_tag(2, "data/a/tree.cfg", (2, 0, 2)),
                        ],
                    )
                ],
            )
        ],
    )
    gm = STag_filedb(
        "GameSessionManager",
        [
            STag_filedb("WorldManager", [grid_tag("Water", 4, b"\xff" * 16)]),
            STag_filedb("RegrowManager", [grid_tag("TreeMap", 4, bytes(16))]),
            STag_filedb("AreaManagerData", [STag_filedb("#1", [SAttrib("Data", filedb.write(area))])]),
            *gm_extra,
        ],
    )
    gamedata = filedb.write(STag_filedb("root", [gm]))
    a7m = write_v22(
        {"gamedata.data": gamedata, "rd3d.data": filedb.write(STag_filedb("root", [STag_filedb("PropGrid", [])]))}
    )
    return a7m, filedb.write(STag_filedb("root", [STag_filedb("Info", []), *info_extra]))


class FakeArchive:
    """One archive of either game: 2070's tree lists ``SArchive`` records (``path``), 117's files ``CRdaArchive``
    objects (``m_path``)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.m_path = path


def _no_file(_path):
    raise KeyError(_path)


def fake_convert(calls: list, shared_bytes=lambda island: b"the same model"):
    def convert(opts, session_world, island, _parts, edits=None, suffix="", stats=None):
        calls.append((island, opts.out, opts.shared))
        if island == "n_bad":
            raise SystemExit("no SimpleCfg template object in the donor")
        folder = opts.out / f"data/anno2070/islands/pool/a2070_{island}"
        folder.mkdir(parents=True)
        (folder / "x.a7m").write_bytes(island.encode())
        model = opts.out / "data/anno2070/graphics/rock.rdm"  # what every island converts again for itself
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(shared_bytes(island))
        stats.update(width=128, height=64, objects={}, trees={}, builder={"land": 500, "rivers": []})
        return f"data/anno2070/islands/pool/a2070_{island}/x.a7m"

    return convert


def object_tag(serial: int, cfg: str, pos) -> STag_filedb:
    return STag_filedb(
        "#1",
        [
            SAttrib("ID", struct.pack("<I", serial) + b"\x01\x00\x00\x00"),
            SAttrib("Position", struct.pack("<3f", *pos)),
            SAttrib("Direction", struct.pack("<f", 0.0)),
            STag_filedb(
                "Mesh", [SAttrib("Orientation", struct.pack("<4f", 0, 0, 0, 1)), SAttrib("Scale", struct.pack("<f", 1))]
            ),
            STag_filedb("DynamicVariation", [SAttrib("ConfigFilename", cfg.replace("/", "\\").encode("utf-16-le"))]),
        ],
    )


def prop_tag(index: int, pos) -> STag_filedb:
    return STag_filedb(
        "#1",
        [
            SAttrib("Index", struct.pack("<I", index)),
            SAttrib("Position", struct.pack("<3f", *pos)),
            SAttrib("Rotation", struct.pack("<4f", 0, 0, 0, 1)),
            SAttrib("Scale", struct.pack("<3f", 1, 1, 1)),
        ],
    )


class FakeBuilder:
    """The parts of the island builder that islandedits touches."""

    def __init__(self):
        self.objs = STag_filedb(
            "objects",
            [
                object_tag(1, "data/a/rock.cfg", (10, 2, 20)),
                object_tag(2, "data/a/cliff.cfg", (30, 4, 40)),
            ],
        )
        self.doc = STag_filedb("doc", [STag_filedb("GameObject", [self.objs])])
        names = STag_filedb("FileNames", [SAttrib("#32768", "data\\b\\waterfall.cfg".encode("utf-16-le"))])
        self.inst = STag_filedb("Instances", [prop_tag(0, (50, 1, 60))])
        self.rd3d = STag_filedb("rd", [STag_filedb("PropGrid", [names, self.inst])])

    def _areas_data(self):
        return [(None, self.doc, None)]

    def _objects(self, doc, group):
        return doc.path(f"{group}/objects")


GRID = 32  # tiles: 4 x 4 cells of 8


def _rows(cells: list[list[int]]) -> bytes:
    """Cell rows as the file stores them: one row after the other, a row padded to 32 bits."""
    out = bytearray(4 * len(cells))
    for z, row in enumerate(cells):
        for x, v in enumerate(row):
            if v:
                out[z * 4 + x // 8] |= 1 << (x % 8)
    return bytes(out)


CELLS = [[0, 1, 1, 0], [0, 1, 1, 0], [0, 1, 1, 0], [0, 0, 0, 0]]


def island_files(coast: bool = True, version: int = 3):
    gm = STag_filedb(
        "GameSessionManager",
        [
            STag_filedb("WorldManager", [grid_tag("Water", GRID, bytes(GRID * GRID // 8))]),
            STag_filedb("AreaManagerData", []),
        ],
    )
    rd = STag_filedb(
        "root", [STag_filedb("Terrain", [grid_tag("ActiveMap", 4, _rows(CELLS))]), STag_filedb("PropGrid", [])]
    )
    info = STag_filedb(
        "",
        [
            SAttrib("MapSize", struct.pack("<2i", GRID, GRID)),
            grid_tag("ActiveMapGrid", 4, _rows(CELLS)),
            SAttrib("ActiveMapRect", struct.pack("<4i", 8, 0, 24, 24)),
            STag_filedb(
                "ObjectMetaInfo",
                [STag_filedb("StartCoastDirection", [SAttrib("value", struct.pack("<f", 1.0))] if coast else [])],
            ),
        ],
    )
    a7m = write_v22(
        {
            "gamedata.data": filedb.write(STag_filedb("root", [gm]), version=version),
            "rd3d.data": filedb.write(rd, version=version),
        }
    )
    return FakeFiles({ISLAND117_SOURCE: a7m, ISLAND117_SOURCE[:-4] + ".a7minfo": filedb.write(info, version=version)})


PROPS_ASSETS = (
    "<Assets><Asset><Template>RandomIsland</Template><Values><Standard><GUID>7</GUID><Name>isle</Name></Standard>"
    f"<RandomIsland><FilePath>{ISLAND117_SOURCE}</FilePath><IslandRegion>Roman</IslandRegion>"
    "<IslandType>Normal</IslandType></RandomIsland></Values></Asset>"
    "<Asset><Template>RandomIsland</Template><Values><Standard><GUID>8</GUID><Name>other</Name></Standard>"
    "<RandomIsland><FilePath>data/other.a7m</FilePath><IslandRegion>Celtic</IslandRegion>"
    "<IslandType>Decoration;Starter</IslandType><IslandPlaceholderSize>Medium</IslandPlaceholderSize>"
    "</RandomIsland></Values></Asset></Assets>"
)


def vanilla_editor() -> CVanillaEditor:
    files = island_files()
    files.files[islandprops.ASSETS_117] = PROPS_ASSETS.encode()
    editor = CVanillaEditor.__new__(CVanillaEditor)
    editor.m_files, editor.m_a7m_path = files, ISLAND117_SOURCE
    editor.m_view = type("View", (), {"a7minfo": filedb.read(files.read(ISLAND117_SOURCE[:-4] + ".a7minfo"))})()
    return editor
