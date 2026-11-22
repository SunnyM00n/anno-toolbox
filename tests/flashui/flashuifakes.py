"""Builders of flash UI movies and their shapes."""

from __future__ import annotations

import struct

from annotoolbox.formats import feuread, feushape, feuwrite
from annotoolbox.formats.feuread import SEditText, SExternalImage, SFontRef, SMatrix, STag
from annotoolbox.formats.feuwrite import CBitWriter, encode_matrix, encode_rect

PICTURE = "ExtractedFireFiles\\pngs\\UIAssets\\Button_Up.png"


ICON_PATH = ".\\PNGs\\icons\\icon_a.png"


def _place(char: int | None, depth: int, matrix: SMatrix | None = None, name: str | None = None) -> bytes:
    flags = (0x02 if char is not None else 0x01) | (0x04 if matrix else 0) | (0x20 if name else 0)
    body = bytes([flags]) + struct.pack("<H", depth) + (struct.pack("<H", char) if char is not None else b"")
    return body + (encode_matrix(matrix) if matrix else b"") + (name.encode() + b"\0" if name else b"")


def _shape(cid: int, bitmap: int) -> bytes:
    """A 4 x 2 pixel rectangle filled with a picture (clipped, unsmoothed)."""
    body = struct.pack("<H", cid) + encode_rect((0, 80, 0, 40))
    body += bytes([1, feushape.BITMAP_CLIP_HARD]) + struct.pack("<H", bitmap) + encode_matrix(SMatrix(20, 0, 0, 20))
    w = CBitWriter()
    w.u(0, 8)  # no line styles
    w.u(1, 4)  # fill index bits
    w.u(0, 4)
    w.u(0, 1)  # style change: fill 1, move to 0,0
    w.u(0b00101, 5)
    w.u(1, 5)
    w.u(0, 1)
    w.u(0, 1)
    w.u(1, 1)
    for vertical, delta in ((0, 80), (1, 40), (0, -80), (1, -40)):
        w.u(1, 1)  # edge, straight, 9 bits, not general
        w.u(1, 1)
        w.u(7, 4)
        w.u(0, 1)
        w.u(vertical, 1)
        w.u(delta & 0x1FF, 9)
    w.u(0, 6)  # end of shape
    return body + w.bytes()


def abc_bytes(*strings: str) -> bytes:
    pool = bytes([0, 0, 0, len(strings) + 1]) + b"".join(bytes([len(s)]) + s.encode() for s in strings)
    return struct.pack("<HH", 16, 46) + pool + b"\x00\x01\x02rest of the code"


TEXT = SEditText(5, (0, 2000, 0, 400), 0x8000 | 0x0400 | 0x0100 | 0x20, 1, None, 240, (10, 20, 30, 255), None,
                (0, 0, 0, 0, 40), "", "Hello", b"")  # fmt: skip


def movie_bytes() -> bytes:
    """A small movie in the game's layout: a font, a picture, an atlas icon, a shape, a text, a sprite, code."""
    image = SExternalImage(2, 4, 2, PICTURE, 0, 0)
    icon = SExternalImage(3, 2, 2, ICON_PATH, 1, 0, ICON_PATH)
    font = SFontRef(1, "Roboto_Default.ffd", "Roboto", 0, (("English", "Roboto_Default.ffd"),))
    sprite = [
        STag(feuread.PLACE_OBJECT2, _place(4, 1, SMatrix(tx=100), "mcButton")),
        STag(feuread.PLACE_OBJECT3, bytes([0x06, 0x10]) + struct.pack("<HH", 2, 2) + encode_matrix(SMatrix())),
        STag(feuread.SHOW_FRAME, b""),
        STag(feuread.END, b""),
    ]
    tags = [
        STag(feuread.FILE_ATTRIBUTES, bytes([8, 0, 0, 0])),
        STag(feuread.EXPORTER, struct.pack("<I", 2), long=True),
        STag(feuread.FONT, feuwrite.encode_font(font)),
        STag(feuread.IMAGE, feuwrite.encode_image(image, feuread.IMAGE)),
        STag(feuread.ATLAS_IMAGE, feuwrite.encode_image(icon, feuread.ATLAS_IMAGE)),
        STag(feuread.ATLAS, b".\\blobs\\icons.bfd\0.\\blobs\\icons.png\0"),
        STag(feuread.DEFINE_SHAPE, _shape(4, 2)),
        STag(feuread.DEFINE_EDIT_TEXT, feuwrite.encode_edit_text(TEXT)),
        STag(feuread.DEFINE_SPRITE, struct.pack("<HH", 6, 1), children=sprite),
        STag(feuread.PLACE_OBJECT2, _place(6, 1, SMatrix(tx=200, ty=300), "mcRoot")),
        STag(feuread.PLACE_OBJECT2, _place(5, 2, SMatrix(tx=20, ty=20))),
        STag(feuread.DO_ABC, struct.pack("<I", 1) + b"\0" + abc_bytes("OnBackClicked", "Title")),
        STag(feuread.SYMBOL_CLASS, struct.pack("<HH", 1, 6) + b"menu:Button\0"),
        STag(feuread.SHOW_FRAME, b""),
        STag(feuread.END, b""),
    ]
    frame = encode_rect((0, 38400, 0, 21600)) + struct.pack("<HH", 30 << 8, 1)
    return feuwrite.write(feuread.SMovie(feuread.SIGNATURE, 12, frame, tags))
