"""Anno 2205's UI movies (``data/ui/*.feu``): a Flash movie with its own signature and its own tags, read.

``read`` gives a ``SMovie``: the header and the tag list as stored (a sprite's tags nested in it), every tag's bytes
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


class CFeuError(ValueError):
    """Not a UI movie, or a tag that does not read."""


@dataclass
class STag:
    """One tag as stored: ``long`` keeps the length form (a short tag may be written long)."""

    code: int
    body: bytes
    long: bool = False
    children: list[STag] | None = None  # a sprite's own tags; its body then starts with id and frame count

    @property
    def name(self) -> str:
        return NAMES.get(self.code, f"Tag{self.code}")


@dataclass
class SMovie:
    signature: bytes
    version: int
    frame: bytes  # the stage rectangle, frame rate and frame count, as stored
    tags: list[STag] = field(default_factory=list)

    @property
    def stage(self) -> tuple[int, int, int, int]:
        """The stage rectangle in twips (min x, max x, min y, max y)."""
        return read_rect(self.frame, 0)[0]

    @property
    def rate(self) -> float:
        return struct.unpack_from("<H", self.frame, len(self.frame) - 4)[0] / 256

    @property
    def frame_count(self) -> int:
        return struct.unpack_from("<H", self.frame, len(self.frame) - 2)[0]


class CBits:
    """The bit fields of a Flash movie: most significant bit first, byte aligned between records."""

    def __init__(self, data: bytes, position: int = 0) -> None:
        self.m_data, self.m_position, self.m_bit = data, position, 0

    def read_unsigned(self, bit_count: int) -> int:
        value = 0
        for _ in range(bit_count):
            if self.m_position >= len(self.m_data):
                raise CFeuError("bit field runs past the end of its tag")
            value = (value << 1) | ((self.m_data[self.m_position] >> (7 - self.m_bit)) & 1)
            self.m_bit += 1
            if self.m_bit == 8:
                self.m_bit, self.m_position = 0, self.m_position + 1
        return value

    def read_signed(self, bit_count: int) -> int:
        value = self.read_unsigned(bit_count)
        return value - (1 << bit_count) if bit_count and value >> (bit_count - 1) else value

    def align(self) -> int:
        if self.m_bit:
            self.m_bit, self.m_position = 0, self.m_position + 1
        return self.m_position


def read_rect(data: bytes, position: int) -> tuple[tuple[int, int, int, int], int]:
    bits = CBits(data, position)
    bit_count = bits.read_unsigned(5)
    rect = (
        bits.read_signed(bit_count),
        bits.read_signed(bit_count),
        bits.read_signed(bit_count),
        bits.read_signed(bit_count),
    )
    return rect, bits.align()


@dataclass(frozen=True)
class SMatrix:
    """A 2x3 transform: ``x' = a*x + c*y + tx``, ``y' = b*x + d*y + ty`` (twips)."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: int = 0
    ty: int = 0


def read_matrix(bits: CBits) -> SMatrix:
    a = d = 1.0
    b = c = 0.0
    if bits.read_unsigned(1):
        bit_count = bits.read_unsigned(5)
        a, d = bits.read_signed(bit_count) / 65536, bits.read_signed(bit_count) / 65536
    if bits.read_unsigned(1):
        bit_count = bits.read_unsigned(5)
        b, c = bits.read_signed(bit_count) / 65536, bits.read_signed(bit_count) / 65536
    bit_count = bits.read_unsigned(5)
    translate_x, translate_y = bits.read_signed(bit_count), bits.read_signed(bit_count)
    bits.align()
    return SMatrix(a, b, c, d, translate_x, translate_y)


@dataclass(frozen=True)
class SColorTransform:
    """Multipliers (1.0 = unchanged) and offsets per channel, red, green, blue, alpha."""

    multipliers: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    offsets: tuple[int, int, int, int] = (0, 0, 0, 0)


def read_cxform(bits: CBits, has_alpha: bool) -> SColorTransform:
    has_add, has_mult, bit_count = bits.read_unsigned(1), bits.read_unsigned(1), 0
    bit_count = bits.read_unsigned(4)
    count = 4 if has_alpha else 3
    mult = tuple(bits.read_signed(bit_count) / 256 for _ in range(count)) if has_mult else (1.0,) * count
    add = tuple(bits.read_signed(bit_count) for _ in range(count)) if has_add else (0,) * count
    bits.align()
    return SColorTransform(tuple(mult) + (1.0,) * (4 - count), tuple(add) + (0,) * (4 - count))


def cstr(data: bytes, position: int) -> tuple[str, int]:
    """A NUL-terminated string (UTF-8, the movie's own encoding since version 6) and the position after it."""
    end = data.find(b"\0", position)
    if end < 0:
        raise CFeuError("string without its terminator")
    return data[position:end].decode("utf-8", "surrogateescape"), end + 1


# The container


def _tags(data: bytes, position: int, end: int) -> list[STag]:
    out = []
    while position < end:
        if position + 2 > end:
            raise CFeuError(f"tag header cut off at {position}")
        code_and_length = struct.unpack_from("<H", data, position)[0]
        position += 2
        code, length, is_long = code_and_length >> 6, code_and_length & 0x3F, False
        if length == 0x3F:
            length, is_long = struct.unpack_from("<I", data, position)[0], True
            position += 4
        if position + length > end:
            raise CFeuError(f"tag {code} at {position} runs past the end ({length} bytes)")
        body = data[position : position + length]
        tag = STag(code, body, is_long)
        if code == DEFINE_SPRITE:
            tag.children = _tags(body, 4, len(body))
        out.append(tag)
        position += length
        if code == END:
            break
    return out


def read(data: bytes) -> SMovie:
    """A UI movie (or an uncompressed Flash movie of the same layout) from its bytes."""
    if len(data) < 12 or data[:3] not in FLASH_SIGNATURES:
        raise CFeuError(f"not a UI movie: signature {data[:3]!r}")
    length = struct.unpack_from("<I", data, 4)[0]
    if length != len(data):
        raise CFeuError(f"the header says {length} bytes, the file has {len(data)}")
    _, position = read_rect(data, 8)
    frame = data[8 : position + 4]
    movie = SMovie(data[:3], data[3], frame, _tags(data, position + 4, len(data)))
    return movie


def walk(tags: list[STag], parent: STag | None = None):
    """Every tag with the sprite it sits in (None on the main timeline), depth first."""
    for tag in tags:
        yield parent, tag
        if tag.children is not None:
            yield from walk(tag.children, tag)


def char_id(tag: STag) -> int | None:
    """The character a defining tag defines (shapes, texts, sprites, the game's images), else None."""
    if tag.code in (*SHAPES, DEFINE_EDIT_TEXT, DEFINE_SPRITE, IMAGE, ATLAS_IMAGE, FONT, SCALING_GRID):
        return struct.unpack_from("<H", tag.body)[0]
    return None


def definitions(movie: SMovie) -> dict[int, STag]:
    """Character id -> its defining tag (the scaling grids are not definitions)."""
    return {
        character_id: tag
        for tag in movie.tags
        if tag.code != SCALING_GRID and (character_id := char_id(tag)) is not None
    }


# The game's tags


@dataclass(frozen=True)
class SExternalImage:
    """Tags 246 and 243: an image the movie draws from a file of the game, not from its own bytes."""

    char_id: int
    width: int
    height: int
    path: str  # as the movie names it: ``ExtractedFireFiles\\pngs\\...png`` or ``.\\PNGs\\icons\\...png``
    flags: int  # u16: 1 on a few hundred images, meaning not known
    extra: int  # u8, always 0
    atlas_name: str | None = None  # 243 only: the entry of the image atlas it is cut from


def read_image(tag: STag) -> SExternalImage:
    character_id, width, height = struct.unpack_from("<HHH", tag.body)
    path, position = cstr(tag.body, 6)
    flags, extra = struct.unpack_from("<HB", tag.body, position)
    name = cstr(tag.body, position + 3)[0] if tag.code == ATLAS_IMAGE else None
    return SExternalImage(character_id, width, height, path, flags, extra, name)


@dataclass(frozen=True)
class SAtlasRef:
    """Tag 244: an image atlas the movie uses (its table ``.bfd`` and its picture)."""

    table: str
    picture: str


def read_atlas(tag: STag) -> SAtlasRef:
    table, position = cstr(tag.body, 0)
    return SAtlasRef(table, cstr(tag.body, position)[0])


@dataclass(frozen=True)
class SFontRef:
    """Tag 251: a font the movie's texts use, by id; a font file per language."""

    font_id: int
    file: str  # e.g. ``Roboto_Light_Default.ffd``
    name: str  # the family name the texts were laid out with
    flags: int  # u8: 0, or 128 on two fonts
    languages: tuple[tuple[str, str], ...]  # (language, font file)


def read_font(tag: STag) -> SFontRef:
    font_id = struct.unpack_from("<H", tag.body)[0]
    file, position = cstr(tag.body, 2)
    name, position = cstr(tag.body, position)
    flags, count = struct.unpack_from("<BH", tag.body, position)
    position += 3
    languages = []
    for _ in range(count):
        language, position = cstr(tag.body, position)
        language_font_file, position = cstr(tag.body, position)
        languages.append((language, language_font_file))
    return SFontRef(font_id, file, name, flags, tuple(languages))


# Texts, placements, symbols


@dataclass(frozen=True)
class SEditText:
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


def read_edit_text(tag: STag) -> SEditText:
    body = tag.body
    character_id = struct.unpack_from("<H", body)[0]
    bounds, position = read_rect(body, 2)
    rect_raw = body[2:position]
    flags = struct.unpack_from(">H", body, position)[0]
    position += 2
    font_id = font_class = height = color = max_length = layout = None
    if flags & EDIT_HAS_FONT:
        font_id = struct.unpack_from("<H", body, position)[0]
        position += 2
    if flags & EDIT_HAS_FONT_CLASS:
        font_class, position = cstr(body, position)
    if flags & (EDIT_HAS_FONT | EDIT_HAS_FONT_CLASS):
        height = struct.unpack_from("<H", body, position)[0]
        position += 2
    if flags & EDIT_HAS_COLOR:
        color = tuple(body[position : position + 4])
        position += 4
    if flags & EDIT_HAS_MAX_LENGTH:
        max_length = struct.unpack_from("<H", body, position)[0]
        position += 2
    if flags & EDIT_HAS_LAYOUT:
        layout = struct.unpack_from("<BHHHh", body, position)
        position += 9
    variable, position = cstr(body, position)
    text = cstr(body, position)[0] if flags & EDIT_HAS_TEXT else None
    return SEditText(
        character_id, bounds, flags, font_id, font_class, height, color, max_length, layout, variable, text, rect_raw
    )


@dataclass(frozen=True)
class SPlacement:
    """PlaceObject2 / PlaceObject3: a character put on (or changed at) a depth of a timeline."""

    code: int
    flags: int  # PlaceObject2's flag byte; PlaceObject3's second flag byte in the high byte
    depth: int
    char_id: int | None
    class_name: str | None
    matrix: SMatrix | None
    name: str | None
    head: bytes  # the bytes before the matrix (flags, depth, class name, character id)
    matrix_raw: bytes  # the matrix as stored, empty when there is none
    tail: bytes  # everything after the matrix
    clip_depth: int = 0  # the last depth a mask placement covers (flag 0x40)
    blend: int = 0  # PlaceObject3's blend mode (flag 0x0200), 0 when none or when filters come first


# fmt: off
PLACE_MOVE, PLACE_HAS_CHAR, PLACE_HAS_MATRIX, PLACE_HAS_CXFORM = 0x01, 0x02, 0x04, 0x08
PLACE_HAS_RATIO, PLACE_HAS_NAME, PLACE_HAS_CLIP_DEPTH, PLACE_HAS_ACTIONS = 0x10, 0x20, 0x40, 0x80
PLACE3_HAS_FILTERS, PLACE3_HAS_BLEND = 0x0100, 0x0200  # PlaceObject3's second byte
PLACE3_HAS_CLASS_NAME = 0x0800  # PlaceObject3's second byte, bit 3 (in the high byte here)
# fmt: on


def read_placement(tag: STag) -> SPlacement:
    body = tag.body
    flags, position = body[0], 1
    if tag.code == PLACE_OBJECT3:
        flags |= body[1] << 8
        position = 2
    depth = struct.unpack_from("<H", body, position)[0]
    position += 2
    class_name = character_id = matrix = name = None
    if flags & PLACE3_HAS_CLASS_NAME:  # the "has image" bit alone brings no name in these movies
        class_name, position = cstr(body, position)
    if flags & PLACE_HAS_CHAR:
        character_id = struct.unpack_from("<H", body, position)[0]
        position += 2
    head_end = position
    if flags & PLACE_HAS_MATRIX:
        bits = CBits(body, position)
        matrix = read_matrix(bits)
        position = bits.m_position
    matrix_end = position
    if flags & PLACE_HAS_CXFORM:
        position = _skip_cxform(body, position)
    if flags & PLACE_HAS_RATIO:
        position += 2
    if flags & PLACE_HAS_NAME:
        name, position = cstr(body, position)
    clip_depth = blend = 0
    if flags & PLACE_HAS_CLIP_DEPTH:
        clip_depth = struct.unpack_from("<H", body, position)[0]
        position += 2
    if flags & PLACE3_HAS_BLEND and not flags & PLACE3_HAS_FILTERS and position < len(body):
        blend = body[position]
    return SPlacement(
        tag.code, flags, depth, character_id, class_name, matrix, name, body[:head_end], body[head_end:matrix_end],
        body[matrix_end:], clip_depth, blend,
    )  # fmt: skip


def _skip_cxform(body: bytes, position: int) -> int:
    bits = CBits(body, position)
    read_cxform(bits, has_alpha=True)
    return bits.m_position


def read_symbols(tag: STag) -> list[tuple[int, str]]:
    """SymbolClass / ExportAssets: (character id, class or export name); id 0 is the movie's own class."""
    count, position, out = struct.unpack_from("<H", tag.body)[0], 2, []
    for _ in range(count):
        character_id = struct.unpack_from("<H", tag.body, position)[0]
        name, position = cstr(tag.body, position + 2)
        out.append((character_id, name))
    return out


@dataclass(frozen=True)
class SAbcBlock:
    """DoABC: the movie's ActionScript 3 byte code (``feuabc`` reads its constants)."""

    flags: int
    name: str
    abc: bytes
    offset: int  # where the byte code starts in the tag


def read_abc(tag: STag) -> SAbcBlock:
    flags = struct.unpack_from("<I", tag.body)[0]
    name, position = cstr(tag.body, 4)
    return SAbcBlock(flags, name, tag.body[position:], position)
