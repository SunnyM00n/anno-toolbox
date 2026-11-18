"""The tint pages: the stored colour, the strokes laid on pages of every level, and what the island build does with them."""

import struct

import numpy as np

from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.islands import islandedits, tintpages
from annotoolbox.models import bc1encode, ddsdecode
from test_groundedit import GAMEDATA, SOURCE, island

SIDE = 64  # a small page: 4 texels a tile on a level-0 page of 16 tiles


def page_file(rgb: np.ndarray) -> bytes:
    """A DXT1 file as a tint page is laid out: the 128-byte header, the one mip."""
    h, w = rgb.shape[:2]
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<7I", header, 4, 124, 0x81007, h, w, w * h // 2, 0, 1)
    struct.pack_into("<II4s", header, 76, 32, 4, b"DXT1")
    struct.pack_into("<I", header, 108, 0x1000)
    return bytes(header) + bc1encode.encode_blocks(bc1encode.to_blocks(rgb)).tobytes()


def neutral(side: int = SIDE) -> bytes:
    return page_file(np.full((side, side, 3), 128, np.uint8))


def tint_of(raw: bytes) -> np.ndarray:
    """The factor the shader multiplies the albedo with, per texel."""
    return (2 * tintpages.to_rgb(ddsdecode.read_dds(raw)[..., :3])) ** tintpages.GAMMA


def stroke(color, at=(8, 8), radius=3.0, strength=1.0) -> dict:
    return {"color": list(color), "radius": radius, "strength": strength, "points": [list(at)]}


def test_the_stored_ycc_colour_decodes_to_the_shaders_factor_and_encodes_back():
    stored = np.array([[[128, 128, 128], [90, 150, 170], [140, 135, 120]]], np.uint8)
    assert abs(float(tint_of(page_file(np.tile(stored[:, :1], (4, 4, 1))))[0, 0, 0]) - 1.0) < 0.05
    back = tintpages.to_stored(tintpages.to_rgb(stored))
    assert np.abs(back.astype(int) - stored).max() <= 3  # the constants of the shader are rounded, so is the way back


def test_a_stroke_multiplies_the_factor_where_it_lies_and_leaves_the_blocks_beside_it_as_they_were():
    raw = neutral()
    out, report = tintpages.apply(lambda n: raw, ["tintmaps/0/0x0.dds"], [stroke((0.5, 0.5, 0.5))])
    page = out["tintmaps/0/0x0.dds"]
    centre, corner = tint_of(page)[32, 32], tint_of(page)[2, 2]
    assert np.allclose(centre, 0.5 * tint_of(raw)[32, 32], atol=0.06) and np.allclose(
        corner, tint_of(raw)[2, 2], atol=0.02
    )
    assert (
        page[:128] == raw[:128] and page[-512:] == raw[-512:]
    )  # the last blocks, far from the stroke, keep their bytes
    assert report == {"pages": 1, "strokes": 1, "unmatched": []}


def test_a_colour_stroke_shifts_the_chroma_and_a_strength_blends_it_in():
    raw = neutral()
    names = ["tintmaps/0/0x0.dds"]
    full = tint_of(tintpages.apply(lambda n: raw, names, [stroke((1.6, 1.0, 0.6))])[0][names[0]])[32, 32]
    half = tint_of(tintpages.apply(lambda n: raw, names, [stroke((1.6, 1.0, 0.6), strength=0.5)])[0][names[0]])[32, 32]
    assert full[0] > full[1] > full[2] and abs(full[0] / full[2] - 1.6 / 0.6) < 0.5
    assert 1.0 < half[0] < full[0] and half[2] < 1.0 and tintpages.MAX_FACTOR > 1.6


def test_a_stroke_goes_into_every_level_it_reaches_at_that_levels_own_scale_and_pages_it_misses_stay():
    raw = neutral()
    names = ["tintmaps/0/0x0.dds", "tintmaps/0/1x0.dds", "tintmaps/1/0x0.dds", "tintmaps/1/1x1.dds"]
    out, report = tintpages.apply(lambda n: raw, names, [stroke((0.5, 0.5, 0.5), at=(8, 8), radius=2.0)])
    assert sorted(out) == ["tintmaps/0/0x0.dds", "tintmaps/1/0x0.dds"]  # level 1's page covers 32 tiles
    near = (tint_of(out["tintmaps/1/0x0.dds"]) < 0.8).sum()
    assert 0 < near < (tint_of(out["tintmaps/0/0x0.dds"]) < 0.8).sum()  # the same dab covers a quarter of the texels
    assert report["pages"] == 2


def test_two_strokes_multiply_and_one_no_page_lies_under_or_the_format_cannot_hold_is_unmatched():
    raw = neutral()
    names = ["tintmaps/0/0x0.dds"]
    both, _ = tintpages.apply(lambda n: raw, names, [stroke((0.7, 0.7, 0.7)), stroke((0.7, 0.7, 0.7))])
    assert abs(float(tint_of(both[names[0]])[32, 32, 1]) - 0.49) < 0.06
    far = stroke((0.5, 0.5, 0.5), at=(100, 100))
    out, report = tintpages.apply(
        lambda n: raw, names, [far, {"color": [1, 1], "points": [[1, 1]]}, stroke((0.5,) * 3)]
    )
    assert report["unmatched"] == ["tint stroke 0", "tint stroke 1"] and report["strokes"] == 1 and len(out) == 1


def test_the_vanilla_island_build_lays_the_tint_strokes_on_its_pages():
    files = island()
    files.files[GAMEDATA + "tintmaps/0/0x0.dds"] = neutral()
    edits = islandedits.empty()
    edits["tint"] = [stroke((0.5, 0.5, 0.5), at=(8, 8))]
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    assert done.report["tint"]["pages"] == 1 and "tintmaps/0/0x0.dds" in done.gamedata
    assert tint_of(done.gamedata["tintmaps/0/0x0.dds"])[32, 32].mean() < 0.6
    edits["tint"] = [stroke((0.5, 0.5, 0.5), at=(500, 500))]
    assert "tint stroke 0" in a7m_edit117.apply(files, SOURCE, edits, template=None).report["unmatched"]


def test_a_long_line_reaches_the_pages_between_its_ends():
    raw = neutral()
    names = ["tintmaps/0/0x0.dds", "tintmaps/0/1x0.dds", "tintmaps/0/2x0.dds"]
    line = {"color": [0.5] * 3, "radius": 2.0, "strength": 1.0, "points": [[8, 8], [40, 8]]}  # the middle page has none
    out, report = tintpages.apply(lambda n: raw, names, [line])
    assert sorted(out) == names and report["strokes"] == 1


def test_the_tint_brush_records_a_stroke_per_drag_with_the_presets_factor_and_undoes_it():
    from annotoolbox.editor import tintbrush
    from annotoolbox.editor.editsession import EditSession
    from fakes import edit_island

    s = EditSession(edit_island())
    s.tint.begin((2, 0.8), 3.0, 4.0, 4.0)  # "Warmer"
    s.tint.to(4.2, 4.0)  # under a quarter radius: no new point
    s.tint.to(6.0, 4.0)
    s.tint.end()
    (st,) = s.edits["tint"]
    assert (
        st["color"] == list(tintbrush.PRESETS[2][1])
        and st["strength"] == 0.8
        and st["points"] == [[4.0, 4.0], [6.0, 4.0]]
    )
    s.undo.undo()
    assert s.edits["tint"] == []
    s.undo.redo()
    assert len(s.edits["tint"]) == 1 and s.cells("tint") is s.tint
    held = EditSession(edit_island(), readonly=True)
    held.tint.begin((0, 0.5), 3.0, 4.0, 4.0)
    assert held.edits["tint"] == [] and held._brush is None


def test_a_tint_from_the_other_game_keeps_its_pattern_in_the_donors_range():
    rng = np.random.default_rng(3)
    stored = np.stack([rng.integers(90, 170, (SIDE, SIDE)), rng.integers(60, 230, (SIDE, SIDE))], -1)
    rgb = np.dstack([stored[..., 0], stored[..., 1], 256 - stored[..., 0]]).astype(np.uint8)  # r Cb, g Y, b Cr
    raw = page_file(rgb)
    before = ddsdecode.read_dds(raw)[..., :3].astype(float)
    land = np.ones((SIDE, SIDE), bool)
    src, dst = tintpages.land_spread(before, land), (130.0, 12.0)
    after = ddsdecode.read_dds(tintpages.fit_blocks(raw, src, dst))[..., :3].astype(float)
    y = after[..., 1]
    assert abs(y.mean() - 130) < 3 and abs(y.std() - 12) < 2  # the donor's range
    assert np.corrcoef(y.ravel(), before[..., 1].ravel())[0, 1] > 0.95  # the source's pattern
    k = 12 / src[1]
    assert np.abs((after[..., 0] - 128) - (before[..., 0] - 128) * k).max() < 10  # the chroma narrowed alike
    assert tintpages.fit_blocks(raw, (130.0, 12.0), (130.0, 40.0))[128:] == raw[128:]  # never widened, never moved
    bare = raw[128:]
    assert tintpages.fit_blocks(bare, src, dst) == tintpages.fit_blocks(raw, src, dst)[128:]  # a far picture's blocks
