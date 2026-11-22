"""Anno 2205's UI movies, written: a ``feuread.SMovie`` back to bytes, and the encoders of the tags an editor changes.

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
    CFeuError,
    SEditText,
    SExternalImage,
    SFontRef,
    SMatrix,
    SMovie,
    SPlacement,
    STag,
)


class CBitWriter:
    """Bit fields, most significant bit first (``feuread.CBits`` read backwards)."""

    def __init__(self) -> None:
        self.m_out, self.m_acc, self.m_n = bytearray(), 0, 0

    def u(self, value: int, bit_count: int) -> None:
        for i in range(bit_count - 1, -1, -1):
            self.m_acc = (self.m_acc << 1) | ((value >> i) & 1)
            self.m_n += 1
            if self.m_n == 8:
                self.m_out.append(self.m_acc)
                self.m_acc = self.m_n = 0

    def bytes(self) -> bytes:
        if self.m_n:
            self.m_out.append(self.m_acc << (8 - self.m_n))
            self.m_acc = self.m_n = 0
        return bytes(self.m_out)


def signed_bits(*values: int) -> int:
    """The fewest bits a signed field needs for every value (the exporter's choice)."""
    return (
        max((value.bit_length() + 1 if value >= 0 else (~value).bit_length() + 1) for value in values) if values else 0
    )


def encode_rect(rect: tuple[int, int, int, int]) -> bytes:
    bit_count = max(signed_bits(*rect), 1) if any(rect) else 0
    writer = CBitWriter()
    writer.u(bit_count, 5)
    for value in rect:
        writer.u(value & ((1 << bit_count) - 1), bit_count)
    return writer.bytes()


def encode_matrix(matrix: SMatrix) -> bytes:
    writer = CBitWriter()
    for present, pair in (
        (matrix.a != 1.0 or matrix.d != 1.0, (matrix.a, matrix.d)),
        (matrix.b != 0.0 or matrix.c != 0.0, (matrix.b, matrix.c)),
    ):
        writer.u(int(present), 1)
        if present:
            fixed = [round(value * 65536) for value in pair]
            bit_count = signed_bits(*fixed)
            writer.u(bit_count, 5)
            for value in fixed:
                writer.u(value & ((1 << bit_count) - 1), bit_count)
    bit_count = signed_bits(matrix.tx, matrix.ty) if matrix.tx or matrix.ty else 0
    writer.u(bit_count, 5)
    for value in (matrix.tx, matrix.ty):
        writer.u(value & ((1 << bit_count) - 1), bit_count)
    return writer.bytes()


def _cstr(text: str) -> bytes:
    data = text.encode("utf-8", "surrogateescape")
    if b"\0" in data:
        raise CFeuError(f"a movie's string cannot hold a NUL: {text!r}")
    return data + b"\0"


def encode_image(image: SExternalImage, code: int) -> bytes:
    body = struct.pack("<HHH", image.char_id, image.width, image.height) + _cstr(image.path)
    body += struct.pack("<HB", image.flags, image.extra)
    if code == ATLAS_IMAGE:
        body += _cstr(image.atlas_name or image.path)
    return body


def encode_font(font: SFontRef) -> bytes:
    body = struct.pack("<H", font.font_id) + _cstr(font.file) + _cstr(font.name)
    body += struct.pack("<BH", font.flags, len(font.languages))
    return body + b"".join(_cstr(lang) + _cstr(file) for lang, file in font.languages)


def encode_edit_text(edit_text: SEditText, rect_raw: bytes | None = None) -> bytes:
    """A text field; the flags follow the optional parts that are set (the text, the font, the colour, ...)."""
    flags = edit_text.flags & ~(EDIT_HAS_TEXT | EDIT_HAS_COLOR | EDIT_HAS_MAX_LENGTH | EDIT_HAS_FONT)
    flags &= ~(EDIT_HAS_FONT_CLASS | EDIT_HAS_LAYOUT)
    for present, bit in (
        (edit_text.text is not None, EDIT_HAS_TEXT),
        (edit_text.color is not None, EDIT_HAS_COLOR),
        (edit_text.max_length is not None, EDIT_HAS_MAX_LENGTH),
        (edit_text.font_id is not None, EDIT_HAS_FONT),
        (edit_text.font_class is not None, EDIT_HAS_FONT_CLASS),
        (edit_text.layout is not None, EDIT_HAS_LAYOUT),
    ):
        flags |= bit if present else 0
    body = struct.pack("<H", edit_text.char_id) + (rect_raw if rect_raw is not None else encode_rect(edit_text.bounds))
    body += struct.pack(">H", flags)
    if edit_text.font_id is not None:
        body += struct.pack("<H", edit_text.font_id)
    if edit_text.font_class is not None:
        body += _cstr(edit_text.font_class)
    if edit_text.font_id is not None or edit_text.font_class is not None:
        body += struct.pack("<H", edit_text.height or 0)
    if edit_text.color is not None:
        body += bytes(edit_text.color)
    if edit_text.max_length is not None:
        body += struct.pack("<H", edit_text.max_length)
    if edit_text.layout is not None:
        body += struct.pack("<BHHHh", *edit_text.layout)
    body += _cstr(edit_text.variable)
    if edit_text.text is not None:
        body += _cstr(edit_text.text)
    return body


def encode_placement(placement: SPlacement, matrix: SMatrix) -> bytes:
    """A placement with a new matrix (one is added when it had none); everything else as stored."""
    head = bytearray(placement.head)
    head[0] |= PLACE_HAS_MATRIX
    return bytes(head) + encode_matrix(matrix) + placement.tail


# The container


def _tag(tag: STag) -> bytes:
    body = tag.body
    if tag.children is not None:
        body = tag.body[:4] + b"".join(_tag(child) for child in tag.children)
    if tag.long or len(body) >= 0x3F:
        return struct.pack("<HI", (tag.code << 6) | 0x3F, len(body)) + body
    return struct.pack("<H", (tag.code << 6) | len(body)) + body


def write(movie: SMovie) -> bytes:
    """The movie's bytes: header, then the tags; the length in the header is the file's."""
    tags = b"".join(_tag(tag) for tag in movie.tags)
    size = 8 + len(movie.frame) + len(tags)
    return movie.signature + bytes([movie.version]) + struct.pack("<I", size) + movie.frame + tags


def resign(data: bytes, signature: bytes) -> bytes:
    """The same movie with another signature: ``b"FWS"`` opens it in a Flash tool, ``b"UEF"`` gives it back."""
    if data[:3] not in FLASH_SIGNATURES or signature not in FLASH_SIGNATURES:
        raise CFeuError(f"only an uncompressed movie can be re-signed: {data[:3]!r} -> {signature!r}")
    return signature + data[3:]
