"""Anno 2205's UI movies (``data/ui/*.feu``): a Flash movie with its own signature and its own tags, read.

``read`` gives a ``Movie``: the header and the tag list as stored (a sprite's tags nested in it), every tag's bytes
kept, so ``feuwrite.write`` gives the file back byte for byte. The decoders below turn the tags an editor shows into
records (images, fonts, texts, placements, symbols); ``feushape`` decodes the shapes. The format, the tags of the
game's own exporter and how the game finds the files: docs/formats/anno2205-ui-format.md.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

SIGNATURE = b"UEF"  # the game's; a plain uncompressed Flash movie has b"FWS"
FLASH_SIGNATURES = (b"UEF", b"FWS")

# fmt: off
END, SHOW_FRAME, DEFINE_SHAPE, PLACE_OBJECT, REMOVE_OBJECT = 0, 1, 2, 4, 5
BACKGROUND, DEFINE_SHAPE2, PLACE_OBJECT2, REMOVE_OBJECT2 = 9, 22, 26, 28
DEFINE_SHAPE3, DEFINE_EDIT_TEXT, DEFINE_SPRITE, FRAME_LABEL = 32, 37, 39, 43
EXPORT_ASSETS, FILE_ATTRIBUTES, PLACE_OBJECT3, SYMBOL_CLASS = 56, 69, 70, 76
SCALING_GRID, DO_ABC, DEFINE_SHAPE4, SCENE_LABELS = 78, 82, 83, 86
# the game's exporter (docs/formats/anno2205-ui-format.md, "The game's tags")
ATLAS_IMAGE, ATLAS, IMAGE, EXPORTER, FONT = 243, 244, 246, 249, 251
# fmt: on

SHAPES = (DEFINE_SHAPE, DEFINE_SHAPE2, DEFINE_SHAPE3, DEFINE_SHAPE4)
NAMES = {
    END: "End", SHOW_FRAME: "ShowFrame", DEFINE_SHAPE: "DefineShape", PLACE_OBJECT: "PlaceObject",
    REMOVE_OBJECT: "RemoveObject", BACKGROUND: "SetBackgroundColor", DEFINE_SHAPE2: "DefineShape2",
    PLACE_OBJECT2: "PlaceObject2", REMOVE_OBJECT2: "RemoveObject2", DEFINE_SHAPE3: "DefineShape3",
    DEFINE_EDIT_TEXT: "DefineEditText", DEFINE_SPRITE: "DefineSprite", FRAME_LABEL: "FrameLabel",
    EXPORT_ASSETS: "ExportAssets", FILE_ATTRIBUTES: "FileAttributes", PLACE_OBJECT3: "PlaceObject3",
    SYMBOL_CLASS: "SymbolClass", SCALING_GRID: "DefineScalingGrid", DO_ABC: "DoABC", DEFINE_SHAPE4: "DefineShape4",
    SCENE_LABELS: "DefineSceneAndFrameLabelData", ATLAS_IMAGE: "AtlasImage", ATLAS: "ImageAtlas",
    IMAGE: "ExternalImage", EXPORTER: "ExporterVersion", FONT: "FontReference",
}  # fmt: skip


class FeuError(ValueError):
    """Not a UI movie, or a tag that does not read."""


@dataclass
class Tag:
    """One tag as stored: ``long`` keeps the length form (a short tag may be written long)."""

    code: int
    body: bytes
    long: bool = False
    children: list[Tag] | None = None  # a sprite's own tags; its body then starts with id and frame count

    @property
    def name(self) -> str:
        return NAMES.get(self.code, f"Tag{self.code}")


@dataclass
class Movie:
    signature: bytes
    version: int
    frame: bytes  # the stage rectangle, frame rate and frame count, as stored
    tags: list[Tag] = field(default_factory=list)

    @property
    def stage(self) -> tuple[int, int, int, int]:
        """The stage rectangle in twips (x min, x max, y min, y max)."""
        return read_rect(self.frame, 0)[0]

    @property
    def rate(self) -> float:
        return struct.unpack_from("<H", self.frame, len(self.frame) - 4)[0] / 256

    @property
    def frame_count(self) -> int:
        return struct.unpack_from("<H", self.frame, len(self.frame) - 2)[0]


class Bits:
    """The bit fields of a Flash movie: most significant bit first, byte aligned between records."""

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data, self.pos, self.bit = data, pos, 0

    def u(self, n: int) -> int:
        value = 0
        for _ in range(n):
            if self.pos >= len(self.data):
                raise FeuError("bit field runs past the end of its tag")
            value = (value << 1) | ((self.data[self.pos] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit, self.pos = 0, self.pos + 1
        return value

    def s(self, n: int) -> int:
        value = self.u(n)
        return value - (1 << n) if n and value >> (n - 1) else value

    def align(self) -> int:
        if self.bit:
            self.bit, self.pos = 0, self.pos + 1
        return self.pos


def read_rect(data: bytes, pos: int) -> tuple[tuple[int, int, int, int], int]:
    bits = Bits(data, pos)
    n = bits.u(5)
    rect = (bits.s(n), bits.s(n), bits.s(n), bits.s(n))
    return rect, bits.align()


@dataclass(frozen=True)
class Matrix:
    """A 2x3 transform: ``x' = a*x + c*y + tx``, ``y' = b*x + d*y + ty`` (twips)."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: int = 0
    ty: int = 0


def read_matrix(bits: Bits) -> Matrix:
    a = d = 1.0
    b = c = 0.0
    if bits.u(1):
        n = bits.u(5)
        a, d = bits.s(n) / 65536, bits.s(n) / 65536
    if bits.u(1):
        n = bits.u(5)
        b, c = bits.s(n) / 65536, bits.s(n) / 65536
    n = bits.u(5)
    tx, ty = bits.s(n), bits.s(n)
    bits.align()
    return Matrix(a, b, c, d, tx, ty)


@dataclass(frozen=True)
class ColorTransform:
    """Multipliers (1.0 = unchanged) and offsets per channel, red, green, blue, alpha."""

    mult: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    add: tuple[int, int, int, int] = (0, 0, 0, 0)


def read_cxform(bits: Bits, alpha: bool) -> ColorTransform:
    has_add, has_mult, n = bits.u(1), bits.u(1), 0
    n = bits.u(4)
    count = 4 if alpha else 3
    mult = tuple(bits.s(n) / 256 for _ in range(count)) if has_mult else (1.0,) * count
    add = tuple(bits.s(n) for _ in range(count)) if has_add else (0,) * count
    bits.align()
    return ColorTransform(tuple(mult) + (1.0,) * (4 - count), tuple(add) + (0,) * (4 - count))


def cstr(data: bytes, pos: int) -> tuple[str, int]:
    """A NUL-terminated string (UTF-8, the movie's own encoding since version 6) and the position after it."""
    end = data.find(b"\0", pos)
    if end < 0:
        raise FeuError("string without its terminator")
    return data[pos:end].decode("utf-8", "surrogateescape"), end + 1


# The container


def _tags(data: bytes, pos: int, end: int) -> list[Tag]:
    out = []
    while pos < end:
        if pos + 2 > end:
            raise FeuError(f"tag header cut off at {pos}")
        code_len = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        code, length, long = code_len >> 6, code_len & 0x3F, False
        if length == 0x3F:
            length, long = struct.unpack_from("<I", data, pos)[0], True
            pos += 4
        if pos + length > end:
            raise FeuError(f"tag {code} at {pos} runs past the end ({length} bytes)")
        body = data[pos : pos + length]
        tag = Tag(code, body, long)
        if code == DEFINE_SPRITE:
            tag.children = _tags(body, 4, len(body))
        out.append(tag)
        pos += length
        if code == END:
            break
    return out


def read(data: bytes) -> Movie:
    """A UI movie (or an uncompressed Flash movie of the same layout) from its bytes."""
    if len(data) < 12 or data[:3] not in FLASH_SIGNATURES:
        raise FeuError(f"not a UI movie: signature {data[:3]!r}")
    length = struct.unpack_from("<I", data, 4)[0]
    if length != len(data):
        raise FeuError(f"the header says {length} bytes, the file has {len(data)}")
    _, pos = read_rect(data, 8)
    frame = data[8 : pos + 4]
    movie = Movie(data[:3], data[3], frame, _tags(data, pos + 4, len(data)))
    return movie


def walk(tags: list[Tag], parent: Tag | None = None):
    """Every tag with the sprite it sits in (None on the main timeline), depth first."""
    for tag in tags:
        yield parent, tag
        if tag.children is not None:
            yield from walk(tag.children, tag)


def char_id(tag: Tag) -> int | None:
    """The character a defining tag defines (shapes, texts, sprites, the game's images), else None."""
    if tag.code in (*SHAPES, DEFINE_EDIT_TEXT, DEFINE_SPRITE, IMAGE, ATLAS_IMAGE, FONT, SCALING_GRID):
        return struct.unpack_from("<H", tag.body)[0]
    return None


def definitions(movie: Movie) -> dict[int, Tag]:
    """Character id -> its defining tag (the scaling grids are not definitions)."""
    return {cid: t for t in movie.tags if t.code != SCALING_GRID and (cid := char_id(t)) is not None}


# The game's tags


@dataclass(frozen=True)
class ExternalImage:
    """Tags 246 and 243: an image the movie draws from a file of the game, not from its own bytes."""

    char_id: int
    width: int
    height: int
    path: str  # as the movie names it: ``ExtractedFireFiles\\pngs\\...png`` or ``.\\PNGs\\icons\\...png``
    flags: int  # u16: 1 on a few hundred images, meaning not known
    extra: int  # u8, always 0
    atlas_name: str | None = None  # 243 only: the entry of the image atlas it is cut from


def read_image(tag: Tag) -> ExternalImage:
    cid, width, height = struct.unpack_from("<HHH", tag.body)
    path, pos = cstr(tag.body, 6)
    flags, extra = struct.unpack_from("<HB", tag.body, pos)
    name = cstr(tag.body, pos + 3)[0] if tag.code == ATLAS_IMAGE else None
    return ExternalImage(cid, width, height, path, flags, extra, name)


@dataclass(frozen=True)
class AtlasRef:
    """Tag 244: an image atlas the movie uses (its table ``.bfd`` and its picture)."""

    table: str
    picture: str


def read_atlas(tag: Tag) -> AtlasRef:
    table, pos = cstr(tag.body, 0)
    return AtlasRef(table, cstr(tag.body, pos)[0])


@dataclass(frozen=True)
class FontRef:
    """Tag 251: a font the movie's texts use, by id; a font file per language."""

    font_id: int
    file: str  # e.g. ``Roboto_Light_Default.ffd``
    name: str  # the family name the texts were laid out with
    flags: int  # u8: 0, or 128 on two fonts
    languages: tuple[tuple[str, str], ...]  # (language, font file)


def read_font(tag: Tag) -> FontRef:
    fid = struct.unpack_from("<H", tag.body)[0]
    file, pos = cstr(tag.body, 2)
    name, pos = cstr(tag.body, pos)
    flags, count = struct.unpack_from("<BH", tag.body, pos)
    pos += 3
    langs = []
    for _ in range(count):
        lang, pos = cstr(tag.body, pos)
        lfile, pos = cstr(tag.body, pos)
        langs.append((lang, lfile))
    return FontRef(fid, file, name, flags, tuple(langs))


def read_exporter(tag: Tag) -> int:
    return struct.unpack_from("<I", tag.body)[0]


# Texts, placements, symbols


@dataclass(frozen=True)
class EditText:
    """DefineEditText: a text field, its box, font and colour, its variable name and the text it starts with."""

    char_id: int
    bounds: tuple[int, int, int, int]
    flags: int  # the 16 flag bits as stored (``EDIT_*``)
    font_id: int | None
    font_class: str | None
    height: int | None  # twips
    color: tuple[int, int, int, int] | None  # RGBA
    max_length: int | None
    layout: tuple[int, int, int, int, int] | None  # align, left margin, right margin, indent, leading
    variable: str
    text: str | None
    rect_raw: bytes  # the box as stored (kept when the box is not changed)


# fmt: off
EDIT_HAS_TEXT, EDIT_WORD_WRAP, EDIT_MULTILINE, EDIT_PASSWORD = 0x8000, 0x4000, 0x2000, 0x1000
EDIT_READ_ONLY, EDIT_HAS_COLOR, EDIT_HAS_MAX_LENGTH, EDIT_HAS_FONT = 0x0800, 0x0400, 0x0200, 0x0100
EDIT_HAS_FONT_CLASS, EDIT_AUTO_SIZE, EDIT_HAS_LAYOUT, EDIT_NO_SELECT = 0x80, 0x40, 0x20, 0x10
EDIT_BORDER, EDIT_WAS_STATIC, EDIT_HTML, EDIT_USE_OUTLINES = 0x08, 0x04, 0x02, 0x01
# fmt: on


def read_edit_text(tag: Tag) -> EditText:
    body = tag.body
    cid = struct.unpack_from("<H", body)[0]
    bounds, pos = read_rect(body, 2)
    rect_raw = body[2:pos]
    flags = struct.unpack_from(">H", body, pos)[0]
    pos += 2
    font_id = font_class = height = color = max_length = layout = None
    if flags & EDIT_HAS_FONT:
        font_id = struct.unpack_from("<H", body, pos)[0]
        pos += 2
    if flags & EDIT_HAS_FONT_CLASS:
        font_class, pos = cstr(body, pos)
    if flags & (EDIT_HAS_FONT | EDIT_HAS_FONT_CLASS):
        height = struct.unpack_from("<H", body, pos)[0]
        pos += 2
    if flags & EDIT_HAS_COLOR:
        color = tuple(body[pos : pos + 4])
        pos += 4
    if flags & EDIT_HAS_MAX_LENGTH:
        max_length = struct.unpack_from("<H", body, pos)[0]
        pos += 2
    if flags & EDIT_HAS_LAYOUT:
        layout = struct.unpack_from("<BHHHh", body, pos)
        pos += 9
    variable, pos = cstr(body, pos)
    text = cstr(body, pos)[0] if flags & EDIT_HAS_TEXT else None
    return EditText(
        cid, bounds, flags, font_id, font_class, height, color, max_length, layout, variable, text, rect_raw
    )


@dataclass(frozen=True)
class Placement:
    """PlaceObject2 / PlaceObject3: a character put on (or changed at) a depth of a timeline."""

    code: int
    flags: int  # PlaceObject2's flag byte; PlaceObject3's second flag byte in the high byte
    depth: int
    char_id: int | None
    class_name: str | None
    matrix: Matrix | None
    name: str | None
    head: bytes  # the bytes before the matrix (flags, depth, class name, character id)
    matrix_raw: bytes  # the matrix as stored, empty when there is none
    tail: bytes  # everything after the matrix


# fmt: off
PLACE_MOVE, PLACE_HAS_CHAR, PLACE_HAS_MATRIX, PLACE_HAS_CXFORM = 0x01, 0x02, 0x04, 0x08
PLACE_HAS_RATIO, PLACE_HAS_NAME, PLACE_HAS_CLIP_DEPTH, PLACE_HAS_ACTIONS = 0x10, 0x20, 0x40, 0x80
PLACE3_HAS_CLASS_NAME = 0x0800  # PlaceObject3's second byte, bit 3 (in the high byte here)
# fmt: on


def read_placement(tag: Tag) -> Placement:
    body = tag.body
    flags, pos = body[0], 1
    if tag.code == PLACE_OBJECT3:
        flags |= body[1] << 8
        pos = 2
    depth = struct.unpack_from("<H", body, pos)[0]
    pos += 2
    class_name = char = matrix = name = None
    if flags & PLACE3_HAS_CLASS_NAME:  # the "has image" bit alone brings no name in these movies
        class_name, pos = cstr(body, pos)
    if flags & PLACE_HAS_CHAR:
        char = struct.unpack_from("<H", body, pos)[0]
        pos += 2
    head_end = pos
    if flags & PLACE_HAS_MATRIX:
        bits = Bits(body, pos)
        matrix = read_matrix(bits)
        pos = bits.pos
    matrix_end = pos
    if flags & PLACE_HAS_CXFORM:
        pos = _skip_cxform(body, pos)
    if flags & PLACE_HAS_RATIO:
        pos += 2
    if flags & PLACE_HAS_NAME:
        name, pos = cstr(body, pos)
    return Placement(
        tag.code, flags, depth, char, class_name, matrix, name, body[:head_end], body[head_end:matrix_end],
        body[matrix_end:],
    )  # fmt: skip


def _skip_cxform(body: bytes, pos: int) -> int:
    bits = Bits(body, pos)
    read_cxform(bits, alpha=True)
    return bits.pos


def placement_cxform(tag: Tag) -> ColorTransform | None:
    """The colour transform of a placement, when it has one."""
    p = read_placement(tag)
    if not p.flags & PLACE_HAS_CXFORM:
        return None
    return read_cxform(Bits(p.tail, 0), alpha=True)


def read_symbols(tag: Tag) -> list[tuple[int, str]]:
    """SymbolClass / ExportAssets: (character id, class or export name); id 0 is the movie's own class."""
    count, pos, out = struct.unpack_from("<H", tag.body)[0], 2, []
    for _ in range(count):
        cid = struct.unpack_from("<H", tag.body, pos)[0]
        name, pos = cstr(tag.body, pos + 2)
        out.append((cid, name))
    return out


def read_frame_label(tag: Tag) -> str:
    return cstr(tag.body, 0)[0]


@dataclass(frozen=True)
class AbcBlock:
    """DoABC: the movie's ActionScript 3 byte code (``feuabc`` reads its constants)."""

    flags: int
    name: str
    abc: bytes
    offset: int  # where the byte code starts in the tag


def read_abc(tag: Tag) -> AbcBlock:
    flags = struct.unpack_from("<I", tag.body)[0]
    name, pos = cstr(tag.body, 4)
    return AbcBlock(flags, name, tag.body[pos:], pos)


def sprite_frames(tag: Tag) -> int:
    return struct.unpack_from("<H", tag.body, 2)[0]
