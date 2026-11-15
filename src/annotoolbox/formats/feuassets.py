"""The files around Anno 2205's UI movies: the image size table, image atlases, dependency lists, the textures.

``PngSizes`` is ``data/ui/pngsizes.dat`` (every UI image's size, read before a movie is drawn); ``Atlas`` an image
atlas table ``data/ui/blobs/*.bfd`` (where each image sits in the atlas picture); ``read_dependencies`` a movie's
``.dep.xml``; ``rgba_dds`` / ``dds_rgba`` the uncompressed ``_0.dds`` texture beside each PNG. All read and written
byte for byte (docs/formats/anno2205-ui-format.md, "The files around a movie").
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .feuread import FeuError

SIZES_PATH = "data/ui/pngsizes.dat"
_SIZE_RECORD = struct.Struct("<260sII")  # path (NUL padded), width, height


class PngSizes:
    """The size table: lower-case game path -> (width, height), in the file's order."""

    def __init__(self, raw: bytes = b"") -> None:
        if len(raw) % _SIZE_RECORD.size:
            raise FeuError(f"{SIZES_PATH}: {len(raw)} bytes is not a whole number of {_SIZE_RECORD.size}-byte records")
        self.names: dict[str, str] = {}  # lower case -> the spelling stored
        self.sizes: dict[str, tuple[int, int]] = {}
        for name, w, h in _SIZE_RECORD.iter_unpack(raw):
            path = name.split(b"\0", 1)[0].decode("latin-1")
            self.names[path.lower()] = path
            self.sizes[path.lower()] = (w, h)

    def get(self, path: str) -> tuple[int, int] | None:
        return self.sizes.get(path.lower())

    def set(self, path: str, size: tuple[int, int]) -> None:
        """Change an image's size, or add the image at the end."""
        self.names.setdefault(path.lower(), path)
        self.sizes[path.lower()] = size

    def write(self) -> bytes:
        out = bytearray()
        for key, (w, h) in self.sizes.items():
            name = self.names[key].encode("latin-1")
            if len(name) >= 260:
                raise FeuError(f"{SIZES_PATH}: path too long for its record: {self.names[key]}")
            out += _SIZE_RECORD.pack(name, w, h)
        return bytes(out)


@dataclass(frozen=True)
class AtlasEntry:
    """One image of an atlas: its name, size and the six numbers that place it (a 2x3 matrix, twips)."""

    name: str  # ``.\\PNGs\\images\\icon_x.png``, as the movies' atlas images name it
    width: int
    height: int
    matrix: tuple[float, float, float, float, float, float]

    @property
    def origin(self) -> tuple[int, int]:
        """The image's top-left pixel in the atlas picture (the translation is minus twenty times it)."""
        return round(-self.matrix[4] / 20), round(-self.matrix[5] / 20)


class Atlas:
    """An image atlas table (``.bfd``): a version byte, the entry count, the entries."""

    def __init__(self, raw: bytes) -> None:
        if len(raw) < 3:
            raise FeuError("atlas table too short")
        self.version, count = struct.unpack_from("<BH", raw)
        pos, self.entries = 3, []
        for _ in range(count):
            end = raw.find(b"\0", pos)
            if end < 0:
                raise FeuError("atlas entry name without its terminator")
            name = raw[pos:end].decode("latin-1")
            w, h, *m = struct.unpack_from("<HH6f", raw, end + 1)
            self.entries.append(AtlasEntry(name, w, h, tuple(m)))
            pos = end + 1 + 28
        if pos != len(raw):
            raise FeuError(f"atlas table: {len(raw) - pos} bytes after the last entry")

    def find(self, name: str) -> AtlasEntry | None:
        key = name.lower()
        return next((e for e in self.entries if e.name.lower() == key), None)

    def write(self) -> bytes:
        out = bytearray(struct.pack("<BH", self.version, len(self.entries)))
        for e in self.entries:
            out += e.name.encode("latin-1") + b"\0" + struct.pack("<HH6f", e.width, e.height, *e.matrix)
        return bytes(out)


@dataclass(frozen=True)
class Dependencies:
    """A movie's ``.dep.xml``: the textures it loads and, per language, its fonts with their glyph pictures."""

    textures: tuple[str, ...]
    fonts: dict[str, tuple[tuple[str, tuple[str, ...]], ...]]  # language -> (font file, its textures)


def read_dependencies(raw: bytes) -> Dependencies:
    root = ET.fromstring(raw.decode("utf-8-sig"))
    textures = tuple(t.get("FileId", "") for t in root.findall("Texture"))
    fonts = {}
    for lang in root.findall("Language"):
        fonts[lang.get("Name", "")] = tuple(
            (f.get("FileId", ""), tuple(t.get("FileId", "") for t in f.findall("Texture")))
            for f in lang.findall("FontDescriptor")
        )
    return Dependencies(textures, fonts)


# The textures: uncompressed 32-bit, one level, red-green-blue-alpha in memory (the masks below)
_DDS_FLAGS = 0x0002100F  # caps, height, width, pitch, pixel format, mipmap count
_DDS_PIXELS = struct.Struct("<II4sIIIII")  # size, flags, fourcc, bits, r, g, b, a masks
_DDS_RGBA = (32, 0x41, bytes(4), 32, 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000)


def rgba_dds(width: int, height: int, rgba: bytes) -> bytes:
    """An image as the game's UI texture: the header the shipped ones have, then the pixels."""
    if len(rgba) != width * height * 4:
        raise FeuError(f"{len(rgba)} bytes of pixels for a {width}x{height} image")
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, _DDS_FLAGS, height, width, width * 4, 1, 1)  # depth 1, one level
    head += bytes(44) + _DDS_PIXELS.pack(*_DDS_RGBA) + struct.pack("<IIIII", 0x1000, 0, 0, 0, 0)
    return head + bytes(rgba)


def dds_rgba(raw: bytes) -> tuple[int, int, bytes]:
    """The size and RGBA pixels of a UI texture (only the uncompressed layout ``rgba_dds`` writes)."""
    if raw[:4] != b"DDS " or len(raw) < 128:
        raise FeuError("not a DDS texture")
    height, width = struct.unpack_from("<II", raw, 12)
    if _DDS_PIXELS.unpack_from(raw, 76) != _DDS_RGBA:
        raise FeuError("not an uncompressed 32-bit UI texture")
    return width, height, raw[128 : 128 + width * height * 4]
