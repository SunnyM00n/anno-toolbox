"""Builders of the other games' files: 1701 island files and 2205's UI archive."""

from __future__ import annotations

import math
import struct
from pathlib import Path

from tests.flashui.flashuifakes import ICON_PATH, movie_bytes
from tests.formats.formatfakes import cdata
from tests.formats.isdfakes import archive_entry, u16

from annotoolbox.formats import feuassets, filedb
from annotoolbox.formats.filedb import SAttrib
from annotoolbox.formats.filedb import STag as STag_filedb
from annotoolbox.formats.rda import write_v22
from annotoolbox.othergames import isd1701, ui2205
from annotoolbox.othergames.index2205 import FILE_DB


def t(name: str, inner: bytes | str = b"", close: bytes = b"</>") -> bytes:
    """A node as 1701 writes it: no line break after the close."""
    inner = inner.encode() if isinstance(inner, str) else inner
    return b"<%s>" % name.encode() + inner + close


HEIGHTS = [-100.0, 0.0, -100.0, 0.0, 20.0, 0.0, -100.0, 0.0, -100.0]


TILES = [82, 82, 78, 82]  # rows = z


def isd1701_bytes() -> bytes:
    verts = b"".join(
        isd1701.VERTEX.pack(0, h, 0, 0, 1, 0, 0xFFFF, 1, 0xFFFFFF if k else 0x102030) for k, h in enumerate(HEIGHTS)
    )
    tiles = b"".join(isd1701.TILE_RECORD.pack(20.0, 0, tex, 0, 0, 1, 0, 0, 1, 0) for tex in TILES)
    settings = t("Settings", t("Dim_X", "4") + t("Dim_Z", "4") + t("UsedSizeX", "2") + t("UsedSizeZ", "2"))
    terrain = t("Terrain", settings + t("HeightMap", cdata(verts)) + t("TileMap", cdata(tiles)))
    hid = t(
        "003C002F",
        t("m_GUID", "4010")
        + t("m_Variation", "2")
        + t("m_Position", t("data", cdata(struct.pack("<3i", 4096, 20 * 128, 2048))))
        + t("m_Direction", str(round(math.pi / 2 * 4096)))
        + t("m_DynGridPosition.x", "1"),
    )
    old_hid = t("003C0030", t("m_GUID", "21") + t("m_Position", t("m_x", "8192") + t("m_y", "0") + t("m_z", "0")))
    pid = t(
        "00000001",
        t("m_GUID", "270")
        + t("m_Position.x", "32.000000")
        + t("m_Position.y", "5.000000")
        + t("m_Position.z", "16.000000")
        + t("m_Direction", "3.141593")
        + b"".join(t(f"m_Size.{k}", "1.500000") for k in "xyz"),
    )
    gop = t(
        "m_GOPManager",
        t("m_GRIDManager", t("m_WaterLevel", "0"))
        + t("m_PIDManager", t("m_LastPID", "1") + pid)
        + t("m_HIDManager", t("m_DummyOccupationMap", t("mapsize", "0")) + t("m_LastHID", "48") + hid + old_hid),
    )
    return terrain + gop


def point(x: float, y: float, z: float, width: float, binary: bool) -> bytes:
    pos = t("data", cdata(struct.pack("<3f", x, y, z))) if binary else t("x", str(x)) + t("y", str(y)) + t("z", str(z))
    return t("position", pos) + t("width", f"{width:.6f}") + t("distance", "0.000000")


MOVIE = "data/ui/test_menu.feu"


PNG_PATH = "data/ui/extractedfirefiles/uiassets/button_up.png"


ATLAS_TABLE = "data/ui/blobs/icons.bfd"


def atlas_table() -> bytes:
    """One 2 x 2 icon at pixel 1, 1 of a 4 x 4 atlas."""
    return struct.pack("<BH", 1, 1) + ICON_PATH.encode() + b"\0" + struct.pack("<HH6f", 2, 2, 40, 0, 0, 40, -20, -20)


def ui_files(png: bytes = b"png") -> dict[str, bytes]:
    record = struct.Struct("<260sII")
    return {
        MOVIE: movie_bytes(),
        PNG_PATH: png,
        ui2205.texture_path(PNG_PATH): b"dds",
        feuassets.SIZES_PATH: record.pack(PNG_PATH.encode(), 4, 2),
        ATLAS_TABLE: atlas_table(),
        "data/ui/blobs/icons.png": b"atlas png",
        "data/ui/roboto_default.ffd": b"font",
    }


def index_for(paths) -> bytes:
    """A ``file.db`` in the game's shape listing ``paths`` (all in ``data0.rda``)."""
    entries = [archive_entry(p, 0, 100 + 10 * i, 10, flags=0) for i, p in enumerate(paths)]
    files = STag_filedb("FileMap", [x for e in entries for x in (SAttrib("#32768", u16("n")), e)])
    tree = STag_filedb(
        "FileTree", [STag_filedb("PathMap", [SAttrib("#32768", u16("data")), STag_filedb("#1", [files])])]
    )
    archives = STag_filedb(
        "ArchiveFiles", [STag_filedb("#1", [SAttrib("#32768", u16("maindata/data0.rda")), SAttrib("#32768", b"\0")])]
    )
    last = SAttrib("LastArchiveFile", u16("maindata/data0.rda"))
    return filedb.write(STag_filedb("", [STag_filedb("ArchiveMap", [tree, archives, last])]), version=1)


def make_game(tmp_path: Path, files: dict[str, bytes]) -> Path:
    """An install as the editor finds one: the executable, one archive with ``files``, the index of them."""
    game = tmp_path / "Anno 2205"
    (game / "Bin" / "Win64").mkdir(parents=True)
    (game / "Bin" / "Win64" / "Anno2205.exe").write_bytes(b"")
    (game / "maindata").mkdir()
    (game / "maindata" / "data0.rda").write_bytes(write_v22(files))
    (game / FILE_DB).write_bytes(index_for(sorted(files)))
    return game
