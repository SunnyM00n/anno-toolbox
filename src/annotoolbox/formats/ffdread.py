"""Anno 2205's font descriptor (``data/ui/*.ffd``): the family a movie's font names, the glyph picture beside it and the
table of which character sits where in that picture (docs/formats/anno2205-ui-format.md, "Font descriptors").

``read(data)`` gives a ``Descriptor``; a file of another layout raises ``ValueError``. The glyphs are a signed-distance
picture (``<name>_1.png``), the descriptor holds each character's rectangle in it. What the four box numbers mean in
pixels and where the advance widths are is not settled, so nothing here draws a glyph.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

NAME_MARK = b"\xfd\x0f"  # opens the family name: two bytes, a length byte, the name
RECORD = 27  # a glyph's bytes: u16 code, u8 page, four f32, four i16
TAIL = 2  # a u16 after the last glyph (0 or 1)
_PNG = re.compile(rb"[A-Za-z0-9_\-]+\.png\x00$")


@dataclass(frozen=True)
class Glyph:
    code: int  # the Unicode character
    uv: tuple[float, float, float, float]  # its rectangle in the picture, 0 to 1 (left, top, right, bottom)
    box: tuple[int, int, int, int]  # four signed numbers per glyph, unit not settled


@dataclass(frozen=True)
class Descriptor:
    family: str  # as the movies' font references name it (``Roboto Light``)
    picture: str  # the glyph picture's file name, beside the descriptor
    glyphs: tuple[Glyph, ...]

    def has(self, char: str) -> bool:
        return any(g.code == ord(char) for g in self.glyphs)


def read(data: bytes) -> Descriptor:
    """A font descriptor. Raises ``ValueError`` where the file does not follow the layout (it is checked, not trusted)."""
    if len(data) < 16:
        raise ValueError("a font descriptor is longer than this")
    at = data.find(NAME_MARK)
    if at < 0 or at + 7 > len(data):
        raise ValueError("no family name")
    count = struct.unpack_from("<H", data, at + 5 + data[at + 4])[0]  # the header's own count differs in the bold stub
    family = data[at + 5 : at + 5 + data[at + 4]].decode("latin-1")
    start = len(data) - TAIL - count * RECORD
    picture = _PNG.search(data[:start]) if start > 0 else None
    if picture is None:
        raise ValueError("the glyph table does not follow a picture name")
    # after the name: the count again, count + 1 offsets, one word per glyph, then the sorted character codes
    table = at + 5 + data[at + 4] + 2 + (count + 1) * 2 + count * 2
    if table + count * 2 > start:
        raise ValueError("the character table does not fit")
    codes = struct.unpack_from(f"<{count}H", data, table)
    glyphs = []
    for k in range(count):
        o = start + k * RECORD
        if struct.unpack_from("<H", data, o)[0] != codes[k]:
            raise ValueError("a glyph record does not carry its character code")
        uv = tuple(struct.unpack("<f", b"\x00" + data[o + 4 + 4 * j : o + 7 + 4 * j])[0] for j in range(4))
        glyphs.append(Glyph(codes[k], uv, struct.unpack_from("<4h", data, o + 19)))  # type: ignore[arg-type]
    return Descriptor(family, picture.group()[:-1].decode(), tuple(glyphs))
