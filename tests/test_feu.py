import os
import struct
from pathlib import Path

import pytest

from annotoolbox.formats import feuabc, feuassets, feuedit, feuread, feushape, feuwrite
from annotoolbox.formats.feuread import EditText, ExternalImage, FontRef, Matrix, Tag
from annotoolbox.formats.feuwrite import BitWriter, encode_matrix, encode_rect

PICTURE = "ExtractedFireFiles\\pngs\\UIAssets\\Button_Up.png"
ICON = ".\\PNGs\\icons\\icon_a.png"


def _place(char: int | None, depth: int, matrix: Matrix | None = None, name: str | None = None) -> bytes:
    flags = (0x02 if char is not None else 0x01) | (0x04 if matrix else 0) | (0x20 if name else 0)
    body = bytes([flags]) + struct.pack("<H", depth) + (struct.pack("<H", char) if char is not None else b"")
    return body + (encode_matrix(matrix) if matrix else b"") + (name.encode() + b"\0" if name else b"")


def _shape(cid: int, bitmap: int) -> bytes:
    """A 4 x 2 pixel rectangle filled with a picture (clipped, unsmoothed)."""
    body = struct.pack("<H", cid) + encode_rect((0, 80, 0, 40))
    body += bytes([1, feushape.BITMAP_CLIP_HARD]) + struct.pack("<H", bitmap) + encode_matrix(Matrix(20, 0, 0, 20))
    w = BitWriter()
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


def _abc(*strings: str) -> bytes:
    pool = bytes([0, 0, 0, len(strings) + 1]) + b"".join(bytes([len(s)]) + s.encode() for s in strings)
    return struct.pack("<HH", 16, 46) + pool + b"\x00\x01\x02rest of the code"


TEXT = EditText(5, (0, 2000, 0, 400), 0x8000 | 0x0400 | 0x0100 | 0x20, 1, None, 240, (10, 20, 30, 255), None,
                (0, 0, 0, 0, 40), "", "Hello", b"")  # fmt: skip


def movie_bytes() -> bytes:
    """A small movie in the game's layout: a font, a picture, an atlas icon, a shape, a text, a sprite, code."""
    image = ExternalImage(2, 4, 2, PICTURE, 0, 0)
    icon = ExternalImage(3, 2, 2, ICON, 1, 0, ICON)
    font = FontRef(1, "Roboto_Default.ffd", "Roboto", 0, (("English", "Roboto_Default.ffd"),))
    sprite = [
        Tag(feuread.PLACE_OBJECT2, _place(4, 1, Matrix(tx=100), "mcButton")),
        Tag(feuread.PLACE_OBJECT3, bytes([0x06, 0x10]) + struct.pack("<HH", 2, 2) + encode_matrix(Matrix())),
        Tag(feuread.SHOW_FRAME, b""),
        Tag(feuread.END, b""),
    ]
    tags = [
        Tag(feuread.FILE_ATTRIBUTES, bytes([8, 0, 0, 0])),
        Tag(feuread.EXPORTER, struct.pack("<I", 2), long=True),
        Tag(feuread.FONT, feuwrite.encode_font(font)),
        Tag(feuread.IMAGE, feuwrite.encode_image(image, feuread.IMAGE)),
        Tag(feuread.ATLAS_IMAGE, feuwrite.encode_image(icon, feuread.ATLAS_IMAGE)),
        Tag(feuread.ATLAS, b".\\blobs\\icons.bfd\0.\\blobs\\icons.png\0"),
        Tag(feuread.DEFINE_SHAPE, _shape(4, 2)),
        Tag(feuread.DEFINE_EDIT_TEXT, feuwrite.encode_edit_text(TEXT)),
        Tag(feuread.DEFINE_SPRITE, struct.pack("<HH", 6, 1), children=sprite),
        Tag(feuread.PLACE_OBJECT2, _place(6, 1, Matrix(tx=200, ty=300), "mcRoot")),
        Tag(feuread.PLACE_OBJECT2, _place(5, 2, Matrix(tx=20, ty=20))),
        Tag(feuread.DO_ABC, struct.pack("<I", 1) + b"\0" + _abc("OnBackClicked", "Title")),
        Tag(feuread.SYMBOL_CLASS, struct.pack("<HH", 1, 6) + b"menu:Button\0"),
        Tag(feuread.SHOW_FRAME, b""),
        Tag(feuread.END, b""),
    ]
    frame = encode_rect((0, 38400, 0, 21600)) + struct.pack("<HH", 30 << 8, 1)
    return feuwrite.write(feuread.Movie(feuread.SIGNATURE, 12, frame, tags))


def test_a_movie_comes_back_byte_for_byte_and_reads_as_the_game_lays_it_out():
    raw = movie_bytes()
    movie = feuread.read(raw)
    assert feuwrite.write(movie) == raw
    assert raw[:3] == b"UEF" and movie.version == 12 and movie.stage == (0, 38400, 0, 21600) and movie.rate == 30
    assert movie.tags[1].long  # stored long although short: kept that way
    assert [t.code for t in movie.tags[8].children] == [26, 70, 1, 0]
    defs = feuread.definitions(movie)
    assert sorted(defs) == [1, 2, 3, 4, 5, 6]
    assert feuread.read_image(defs[2]) == ExternalImage(2, 4, 2, PICTURE, 0, 0)
    assert feuread.read_image(defs[3]).atlas_name == ICON
    assert feuread.read_font(defs[1]).languages == (("English", "Roboto_Default.ffd"),)
    assert feuread.read_atlas(movie.tags[5]) == feuread.AtlasRef(".\\blobs\\icons.bfd", ".\\blobs\\icons.png")
    text = feuread.read_edit_text(defs[5])
    assert (text.text, text.color, text.font_id, text.height, text.layout) == (
        "Hello",
        (10, 20, 30, 255),
        1,
        240,
        TEXT.layout,
    )
    placed = [(o, p.char_id, p.name, p.matrix.tx if p.matrix else None) for o, _, p in feuedit.placements(movie)]
    assert placed == [(0, 6, "mcRoot", 200), (0, 5, None, 20), (6, 4, "mcButton", 100), (6, 2, None, 0)]
    assert feuread.read_symbols(movie.tags[12]) == [(6, "menu:Button")]


def test_a_bad_file_is_refused():
    raw = movie_bytes()
    with pytest.raises(feuread.FeuError, match="signature"):
        feuread.read(b"CWS" + raw[3:])
    with pytest.raises(feuread.FeuError, match="header says"):
        feuread.read(raw + b"\0")


def test_rects_and_matrices_encode_as_they_decode():
    for rect in ((0, 0, 0, 0), (-40, 7079, -40, 515), (0, 38400, 0, 21600)):
        assert feuread.read_rect(encode_rect(rect), 0) == (rect, len(encode_rect(rect)))
    for m in (Matrix(), Matrix(tx=-3, ty=7), Matrix(1.5, 0.25, -0.5, 0.75, 1000, -20000)):
        assert feuread.read_matrix(feuread.Bits(encode_matrix(m))) == m


def test_edits_change_their_tag_and_nothing_else():
    raw = movie_bytes()
    movie = feuread.read(raw)
    before = [t.body for t in movie.tags]
    feuedit.set_text(movie, 5, "Hallo Welt", (255, 0, 0, 255))
    feuedit.set_image(movie, 2, size=(8, 4))
    feuedit.set_font(movie, 1, "Roboto_Light_Default.ffd", "Roboto Light")
    feuedit.move(movie, 6, 0, 500, -60)
    feuedit.set_strings(movie, {2: "Titel"})
    changed = [i for i, t in enumerate(movie.tags) if t.body != before[i]]
    assert changed == [2, 3, 7, 11]  # the sprite's own body is unchanged: its tags are written from its children
    again = feuread.read(feuwrite.write(movie))
    defs = feuread.definitions(again)
    assert feuread.read_edit_text(defs[5]).text == "Hallo Welt"
    assert feuread.read_edit_text(defs[5]).color == (255, 0, 0, 255)
    assert (feuread.read_image(defs[2]).width, feuread.read_image(defs[2]).height) == (8, 4)
    assert feuread.read_font(defs[1]).languages == (("English", "Roboto_Light_Default.ffd"),)
    assert feuread.read_placement(defs[6].children[0]).matrix.tx == 500
    assert feuread.read_placement(defs[6].children[0]).name == "mcButton"
    assert feuabc.strings(feuread.read_abc(again.tags[11]).abc) == ("OnBackClicked", "Titel")
    with pytest.raises(feuedit.EditError):
        feuedit.set_text(movie, 99, "x")


def test_an_edit_list_runs_from_its_json_form():
    movie = feuread.read(movie_bytes())
    edits = [feuedit.Edit("text", 5, {"text": "A"}), feuedit.Edit("move", 10, {"sprite": 0, "x": 0, "y": 0}),
             feuedit.Edit("string", 1, {"text": "OnClose"})]  # fmt: skip
    feuedit.apply(movie, [feuedit.Edit.from_json(e.to_json()) for e in edits])
    assert feuedit.texts(movie)[0].text == "A"
    assert feuedit.placements(movie)[1][2].matrix == Matrix()
    with pytest.raises(feuedit.EditError, match="unknown"):
        feuedit.apply(movie, [feuedit.Edit("paint", 1, {})])


def test_the_signature_swaps_for_a_flash_tool_and_back():
    raw = movie_bytes()
    swf = feuwrite.resign(raw, b"FWS")
    assert swf[:3] == b"FWS" and swf[3:] == raw[3:]
    assert feuwrite.resign(swf, feuread.SIGNATURE) == raw


def test_a_shape_reads_as_one_closed_outline_with_its_picture():
    shape = feushape.read_shape(feuread.definitions(feuread.read(movie_bytes()))[4])
    assert shape.bounds == (0, 80, 0, 40) and shape.bitmap_ids == {2}
    (fill, loops), *_ = shape.fills
    assert fill.kind == feushape.BITMAP_CLIP_HARD and fill.matrix == Matrix(20, 0, 0, 20)
    assert [seg[-1] for seg in loops[0]] == [(80, 0), (80, 40), (0, 40), (0, 0)]


def test_the_code_strings_change_and_the_rest_of_the_code_stays():
    abc = _abc("a", "bb")
    pool = feuabc.read_pool(abc)
    assert pool.version == (46, 16) and pool.strings == ("a", "bb")
    new = feuabc.replace_strings(abc, {1: "longer text"})
    assert feuabc.strings(new) == ("longer text", "bb") and new.endswith(abc[pool.end :])
    assert feuabc.replace_strings(abc, {}) == abc


def test_the_size_table_atlas_dependencies_and_textures_round_trip():
    record = struct.Struct("<260sII")
    raw = record.pack(b"data/ui/A.png", 4, 2) + record.pack(b"data/ui/b.png", 8, 8)
    sizes = feuassets.PngSizes(raw)
    assert sizes.write() == raw and sizes.get("DATA/UI/a.png") == (4, 2)
    sizes.set("data/ui/a.png", (16, 16))
    sizes.set("data/ui/new.png", (1, 1))
    assert feuassets.PngSizes(sizes.write()).get("data/ui/new.png") == (1, 1)
    assert feuassets.PngSizes(sizes.write()).names["data/ui/a.png"] == "data/ui/A.png"
    entry = ICON.encode() + b"\0" + struct.pack("<HH6f", 2, 2, 20480, 0, 0, 20480, -60, -20)
    atlas_raw = struct.pack("<BH", 1, 1) + entry
    atlas = feuassets.Atlas(atlas_raw)
    assert atlas.write() == atlas_raw and atlas.find(ICON.upper()).origin == (3, 1)
    deps = feuassets.read_dependencies(
        '﻿<Dependencies><Texture FileId="a.png" /><Language Name="English"><FontDescriptor FileId="f.ffd">'
        '<Texture FileId="f_1.png" /></FontDescriptor></Language></Dependencies>'.encode()
    )
    assert deps.textures == ("a.png",) and deps.fonts == {"English": (("f.ffd", ("f_1.png",)),)}
    pixels = bytes(range(32))
    dds = feuassets.rgba_dds(4, 2, pixels)
    assert len(dds) == 128 + 32 and feuassets.dds_rgba(dds) == (4, 2, pixels)


GAME = os.environ.get("ANNO2205_DIR")


@pytest.mark.skipif(not GAME, reason="needs the installed game: ANNO2205_DIR")
def test_every_ui_file_of_the_install_reads_and_writes_back_byte_for_byte():
    from annotoolbox.othergames import ui2205
    from annotoolbox.othergames.installs import GAMES

    ui = ui2205.UiFiles(GAMES["anno2205"].tree(Path(GAME)))
    for path in ui.movies():
        raw = ui.file(path)
        movie = feuread.read(raw)
        assert feuwrite.write(movie) == raw, path
        for _, tag in feuread.walk(movie.tags):
            if tag.code in (feuread.IMAGE, feuread.ATLAS_IMAGE):
                assert feuwrite.encode_image(feuread.read_image(tag), tag.code) == tag.body
            elif tag.code == feuread.FONT:
                assert feuwrite.encode_font(feuread.read_font(tag)) == tag.body
            elif tag.code == feuread.DEFINE_EDIT_TEXT:
                assert feuwrite.encode_edit_text(feuread.read_edit_text(tag)) == tag.body
            elif tag.code in (feuread.PLACE_OBJECT2, feuread.PLACE_OBJECT3):
                p = feuread.read_placement(tag)
                assert p.matrix is None or feuwrite.encode_placement(p, p.matrix) == tag.body
            elif tag.code in feuread.SHAPES:
                feushape.read_shape(tag)
            elif tag.code == feuread.DO_ABC:
                abc = feuread.read_abc(tag).abc
                assert feuabc.replace_strings(abc, {1: feuabc.strings(abc)[0]}) == abc
    sizes = ui.file(feuassets.SIZES_PATH)
    assert feuassets.PngSizes(sizes).write() == sizes
    for path in ui.paths(".bfd"):
        assert feuassets.Atlas(ui.file(path)).write() == ui.file(path)
