"""Anno 2205's UI movies, written: a ``feuread.Movie`` back to bytes, and the encoders of the tags an editor changes.

``write`` puts every tag back as it was read (its length form too), so an unchanged movie is the game's file byte
for byte; a changed tag is re-encoded by ``encode_image``, ``encode_font``, ``encode_edit_text`` or
``encode_placement``. ``resign`` swaps the signature for a Flash tool and back (docs/formats/anno2205-ui-format.md).
"""

from __future__ import annotations

import struct

from .feuread import (
    ATLAS_IMAGE,
    EDIT_HAS_COLOR,
    EDIT_HAS_FONT,
    EDIT_HAS_FONT_CLASS,
    EDIT_HAS_LAYOUT,
    EDIT_HAS_MAX_LENGTH,
    EDIT_HAS_TEXT,
    FLASH_SIGNATURES,
    PLACE_HAS_MATRIX,
    EditText,
    ExternalImage,
    FeuError,
    FontRef,
    Matrix,
    Movie,
    Placement,
    Tag,
)


class BitWriter:
    """Bit fields, most significant bit first (``feuread.Bits`` read backwards)."""

    def __init__(self) -> None:
        self.out, self.acc, self.n = bytearray(), 0, 0

    def u(self, value: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            self.acc = (self.acc << 1) | ((value >> i) & 1)
            self.n += 1
            if self.n == 8:
                self.out.append(self.acc)
                self.acc = self.n = 0

    def bytes(self) -> bytes:
        if self.n:
            self.out.append(self.acc << (8 - self.n))
            self.acc = self.n = 0
        return bytes(self.out)


def signed_bits(*values: int) -> int:
    """The fewest bits a signed field needs for every value (the exporter's choice)."""
    return max((v.bit_length() + 1 if v >= 0 else (~v).bit_length() + 1) for v in values) if values else 0


def encode_rect(rect: tuple[int, int, int, int]) -> bytes:
    n = max(signed_bits(*rect), 1) if any(rect) else 0
    w = BitWriter()
    w.u(n, 5)
    for v in rect:
        w.u(v & ((1 << n) - 1), n)
    return w.bytes()


def encode_matrix(m: Matrix) -> bytes:
    w = BitWriter()
    for present, pair in ((m.a != 1.0 or m.d != 1.0, (m.a, m.d)), (m.b != 0.0 or m.c != 0.0, (m.b, m.c))):
        w.u(int(present), 1)
        if present:
            fixed = [round(v * 65536) for v in pair]
            n = signed_bits(*fixed)
            w.u(n, 5)
            for v in fixed:
                w.u(v & ((1 << n) - 1), n)
    n = signed_bits(m.tx, m.ty) if m.tx or m.ty else 0
    w.u(n, 5)
    for v in (m.tx, m.ty):
        w.u(v & ((1 << n) - 1), n)
    return w.bytes()


def _cstr(text: str) -> bytes:
    data = text.encode("utf-8", "surrogateescape")
    if b"\0" in data:
        raise FeuError(f"a movie's string cannot hold a NUL: {text!r}")
    return data + b"\0"


def encode_image(img: ExternalImage, code: int) -> bytes:
    body = struct.pack("<HHH", img.char_id, img.width, img.height) + _cstr(img.path)
    body += struct.pack("<HB", img.flags, img.extra)
    if code == ATLAS_IMAGE:
        body += _cstr(img.atlas_name or img.path)
    return body


def encode_font(font: FontRef) -> bytes:
    body = struct.pack("<H", font.font_id) + _cstr(font.file) + _cstr(font.name)
    body += struct.pack("<BH", font.flags, len(font.languages))
    return body + b"".join(_cstr(lang) + _cstr(file) for lang, file in font.languages)


def encode_edit_text(t: EditText, rect_raw: bytes | None = None) -> bytes:
    """A text field; the flags follow the optional parts that are set (the text, the font, the colour, ...)."""
    flags = t.flags & ~(EDIT_HAS_TEXT | EDIT_HAS_COLOR | EDIT_HAS_MAX_LENGTH | EDIT_HAS_FONT)
    flags &= ~(EDIT_HAS_FONT_CLASS | EDIT_HAS_LAYOUT)
    for present, bit in (
        (t.text is not None, EDIT_HAS_TEXT),
        (t.color is not None, EDIT_HAS_COLOR),
        (t.max_length is not None, EDIT_HAS_MAX_LENGTH),
        (t.font_id is not None, EDIT_HAS_FONT),
        (t.font_class is not None, EDIT_HAS_FONT_CLASS),
        (t.layout is not None, EDIT_HAS_LAYOUT),
    ):
        flags |= bit if present else 0
    body = struct.pack("<H", t.char_id) + (rect_raw if rect_raw is not None else encode_rect(t.bounds))
    body += struct.pack(">H", flags)
    if t.font_id is not None:
        body += struct.pack("<H", t.font_id)
    if t.font_class is not None:
        body += _cstr(t.font_class)
    if t.font_id is not None or t.font_class is not None:
        body += struct.pack("<H", t.height or 0)
    if t.color is not None:
        body += bytes(t.color)
    if t.max_length is not None:
        body += struct.pack("<H", t.max_length)
    if t.layout is not None:
        body += struct.pack("<BHHHh", *t.layout)
    body += _cstr(t.variable)
    if t.text is not None:
        body += _cstr(t.text)
    return body


def encode_placement(p: Placement, matrix: Matrix) -> bytes:
    """A placement with a new matrix (one is added when it had none); everything else as stored."""
    head = bytearray(p.head)
    head[0] |= PLACE_HAS_MATRIX
    return bytes(head) + encode_matrix(matrix) + p.tail


# The container


def _tag(tag: Tag) -> bytes:
    body = tag.body
    if tag.children is not None:
        body = tag.body[:4] + b"".join(_tag(t) for t in tag.children)
    if tag.long or len(body) >= 0x3F:
        return struct.pack("<HI", (tag.code << 6) | 0x3F, len(body)) + body
    return struct.pack("<H", (tag.code << 6) | len(body)) + body


def write(movie: Movie) -> bytes:
    """The movie's bytes: header, then the tags; the length in the header is the file's."""
    tags = b"".join(_tag(t) for t in movie.tags)
    size = 8 + len(movie.frame) + len(tags)
    return movie.signature + bytes([movie.version]) + struct.pack("<I", size) + movie.frame + tags


def resign(data: bytes, signature: bytes) -> bytes:
    """The same movie with another signature: ``b"FWS"`` opens it in a Flash tool, ``b"UEF"`` gives it back."""
    if data[:3] not in FLASH_SIGNATURES or signature not in FLASH_SIGNATURES:
        raise FeuError(f"only an uncompressed movie can be re-signed: {data[:3]!r} -> {signature!r}")
    return signature + data[3:]
