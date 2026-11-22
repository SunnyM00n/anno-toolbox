import os
import struct
from pathlib import Path

import pytest
from tests.flashui.flashuifakes import ICON_PATH as ICON
from tests.flashui.flashuifakes import PICTURE, TEXT, abc_bytes, movie_bytes

from annotoolbox.formats import feuabc, feuassets, feuedit, feuread, feushape, feuwrite
from annotoolbox.formats.feuread import SExternalImage, SMatrix
from annotoolbox.formats.feuwrite import encode_matrix, encode_rect


def test_a_movie_comes_back_byte_for_byte_and_reads_as_the_game_lays_it_out():
    raw = movie_bytes()
    movie = feuread.read(raw)
    assert feuwrite.write(movie) == raw
    assert raw[:3] == b"UEF" and movie.version == 12 and movie.stage == (0, 38400, 0, 21600) and movie.rate == 30
    assert movie.tags[1].long  # stored long although short: kept that way
    assert [t.code for t in movie.tags[8].children] == [26, 70, 1, 0]
    defs = feuread.definitions(movie)
    assert sorted(defs) == [1, 2, 3, 4, 5, 6]
    assert feuread.read_image(defs[2]) == SExternalImage(2, 4, 2, PICTURE, 0, 0)
    assert feuread.read_image(defs[3]).atlas_name == ICON
    assert feuread.read_font(defs[1]).languages == (("English", "Roboto_Default.ffd"),)
    assert feuread.read_atlas(movie.tags[5]) == feuread.SAtlasRef(".\\blobs\\icons.bfd", ".\\blobs\\icons.png")
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
    with pytest.raises(feuread.CFeuError, match="signature"):
        feuread.read(b"CWS" + raw[3:])
    with pytest.raises(feuread.CFeuError, match="header says"):
        feuread.read(raw + b"\0")


def test_rects_and_matrices_encode_as_they_decode():
    for rect in ((0, 0, 0, 0), (-40, 7079, -40, 515), (0, 38400, 0, 21600)):
        assert feuread.read_rect(encode_rect(rect), 0) == (rect, len(encode_rect(rect)))
    for m in (SMatrix(), SMatrix(tx=-3, ty=7), SMatrix(1.5, 0.25, -0.5, 0.75, 1000, -20000)):
        assert feuread.read_matrix(feuread.CBits(encode_matrix(m))) == m


def test_edits_change_their_tag_and_nothing_else():
    raw = movie_bytes()
    movie = feuread.read(raw)
    before = [t.body for t in movie.tags]
    feuedit.set_text(movie, 5, "Hallo Welt", (255, 0, 0, 255))
    feuedit.set_image(movie, 2, size_px=(8, 4))
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
    with pytest.raises(feuedit.CEditError):
        feuedit.set_text(movie, 99, "x")


def test_an_edit_list_runs_from_its_json_form():
    movie = feuread.read(movie_bytes())
    edits = [feuedit.SEdit("text", 5, {"text": "A"}), feuedit.SEdit("move", 10, {"sprite": 0, "x": 0, "y": 0}),
             feuedit.SEdit("string", 1, {"text": "OnClose"})]  # fmt: skip
    feuedit.apply(movie, [feuedit.SEdit.from_json(e.to_json()) for e in edits])
    assert feuedit.texts(movie)[0].text == "A"
    assert feuedit.placements(movie)[1][2].matrix == SMatrix()
    with pytest.raises(feuedit.CEditError, match="unknown"):
        feuedit.apply(movie, [feuedit.SEdit("paint", 1, {})])


def test_the_signature_swaps_for_a_flash_tool_and_back():
    raw = movie_bytes()
    swf = feuwrite.resign(raw, b"FWS")
    assert swf[:3] == b"FWS" and swf[3:] == raw[3:]
    assert feuwrite.resign(swf, feuread.SIGNATURE) == raw


def test_a_shape_reads_as_one_closed_outline_with_its_picture():
    shape = feushape.read_shape(feuread.definitions(feuread.read(movie_bytes()))[4])
    assert shape.bounds == (0, 80, 0, 40) and shape.bitmap_ids == {2}
    (fill, loops), *_ = shape.fills
    assert fill.kind == feushape.BITMAP_CLIP_HARD and fill.matrix == SMatrix(20, 0, 0, 20)
    assert [seg[-1] for seg in loops[0]] == [(80, 0), (80, 40), (0, 40), (0, 0)]


def test_the_code_strings_change_and_the_rest_of_the_code_stays():
    abc = abc_bytes("a", "bb")
    pool = feuabc.read_pool(abc)
    assert pool.version == (46, 16) and pool.strings == ("a", "bb")
    new = feuabc.replace_strings(abc, {1: "longer text"})
    assert feuabc.strings(new) == ("longer text", "bb") and new.endswith(abc[pool.end :])
    assert feuabc.replace_strings(abc, {}) == abc


def test_the_size_table_atlas_dependencies_and_textures_round_trip():
    record = struct.Struct("<260sII")
    raw = record.pack(b"data/ui/A.png", 4, 2) + record.pack(b"data/ui/b.png", 8, 8)
    sizes = feuassets.CPngSizes(raw)
    assert sizes.write() == raw and sizes.get("DATA/UI/a.png") == (4, 2)
    sizes.set("data/ui/a.png", (16, 16))
    sizes.set("data/ui/new.png", (1, 1))
    assert feuassets.CPngSizes(sizes.write()).get("data/ui/new.png") == (1, 1)
    assert feuassets.CPngSizes(sizes.write()).m_names["data/ui/a.png"] == "data/ui/A.png"
    entry = ICON.encode() + b"\0" + struct.pack("<HH6f", 2, 2, 20480, 0, 0, 20480, -60, -20)
    atlas_raw = struct.pack("<BH", 1, 1) + entry
    atlas = feuassets.CAtlas(atlas_raw)
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

    ui = ui2205.CUiFiles(GAMES["anno2205"].tree(Path(GAME)))
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
    assert feuassets.CPngSizes(sizes).write() == sizes
    for path in ui.paths(".bfd"):
        assert feuassets.CAtlas(ui.file(path)).write() == ui.file(path)
