"""The block maps' mip chains completed (``render.mipchain``): a file's chain that stops short gets the levels a
mipmapped texture has past it, sampled from its last level and encoded again (BC1 to BC5) or copied by block (BC7);
island-1800-2205-view.md 19."""

from __future__ import annotations

from unittest import mock

import numpy as np

from annotoolbox.models import ddsdecode
from annotoolbox.models.ddsdecode import SDdsInfo
from annotoolbox.render import mipchain


def _decode(fmt: str, raw: bytes, w: int, h: int) -> np.ndarray:
    return ddsdecode._compressed(fmt.encode("latin-1"), raw, w, h, 0)


def _leafy(size: int) -> np.ndarray:
    """An RGBA picture of green leaves on a clear ground, alpha 0 or 255."""
    rng = np.random.default_rng(3)
    img = np.zeros((size, size, 4), np.uint8)
    img[..., 1] = rng.integers(90, 160, (size, size))
    img[..., 0] = img[..., 1] // 2
    img[..., 3] = np.where(rng.random((size, size)) < 0.4, 255, 0)
    return img


def test_a_chain_that_stops_at_16_texels_gets_every_level_down_to_one():
    info = SDdsInfo(64, 64, 3, 1, "DXT5", False, 1)
    stored = [mipchain.encode_blocks("DXT5", mipchain.sample_level(_leafy(64 >> k).astype(np.float32), (64 >> k,) * 2))
              for k in range(3)]  # fmt: skip
    levels = mipchain.full_levels(info, stored)
    assert len(levels) == mipchain.chain_length(64, 64) == 7
    assert levels[:3] == stored
    for k, raw in enumerate(levels):
        assert len(raw) == max(1, (64 >> k) // 4) ** 2 * 16  # a block at least, as Direct3D lays a level out


def test_a_full_chain_or_a_file_of_one_level_stays_as_it_is():
    full = SDdsInfo(4, 4, 3, 1, "DXT1", False, 1)
    assert mipchain.full_levels(full, [b"a", b"b", b"c"]) == [b"a", b"b", b"c"]
    plain = SDdsInfo(8, 8, 1, 1, "RGBA8", False, 1)
    assert mipchain.full_levels(plain, [b"x"]) == [b"x"]


def test_a_plain_map_that_stops_short_gets_its_levels_in_its_own_layout():
    """A mipmapped plain file went up as it was: the levels past its last one read what the memory held."""
    top = np.zeros((8, 8, 4), np.uint8)
    top[:, :4] = 200  # the left half bright
    half = np.full((4, 4, 4), 100, np.uint8)
    levels = mipchain.full_levels(SDdsInfo(8, 8, 2, 1, "BGRA8"), [top.tobytes(), half.tobytes()])
    assert [len(raw) for raw in levels] == [256, 64, 16, 4] and levels[3] == bytes([100]) * 4
    words = np.full((4, 4, 2), 40000, "<u2")
    wide = mipchain.full_levels(SDdsInfo(4, 4, 2, 1, "RG16"), [words.tobytes(), words[:2, :2].tobytes()])
    assert np.frombuffer(wide[2], "<u2").tolist() == [40000, 40000]  # two 16-bit channels a texel, not clipped
    floats = np.full((2, 2, 4), 2.5, "<f2")
    assert np.frombuffer(mipchain.plain_levels([floats.tobytes()], (2, 2), "<f2")[1], "<f2").tolist() == [2.5] * 4


def test_a_volume_that_stops_short_gets_every_level_down_to_one_texel():
    """The clouds' noise volumes: each level half as wide, high and deep, the missing ones the last one sampled."""
    first = np.zeros((4, 8, 8), np.uint8)
    first[:2] = 80  # the near slices
    stored = [first, np.full((2, 4, 4), 40, np.uint8)]
    levels = mipchain.full_array_levels(stored, volume=True)
    assert [lv.shape for lv in levels] == [(4, 8, 8), (2, 4, 4), (1, 2, 2), (1, 1, 1)]
    assert levels[0] is first and levels[2].dtype == np.uint8 and levels[3].tolist() == [[[40]]]
    ramp = np.stack([np.full((2, 2, 4), v, np.float16) for v in (1.0, 3.0)])
    assert mipchain.full_array_levels([ramp], volume=True)[1].tolist() == [[[[2.0] * 4]]]  # between the two slices
    table = [np.full((4, 2, 4), 0.5, np.float16)]  # a flat table: (height, width, channels)
    assert [lv.shape for lv in mipchain.full_array_levels(table)] == [(4, 2, 4), (2, 1, 4), (1, 1, 4)]
    assert mipchain.full_array_levels(levels, volume=True) == levels  # nothing missing


def test_a_missing_level_is_the_last_level_sampled_at_its_centres():
    texels = np.zeros((4, 4, 4), np.float32)
    texels[:, :2] = 200.0  # the left half bright
    got = mipchain.sample_level(texels, (2, 2))
    assert got[:, 0, 0].tolist() == [200, 200] and got[:, 1, 0].tolist() == [0, 0]
    assert mipchain.sample_level(texels, (1, 1))[0, 0, 0] == 100  # the centre between the halves


def test_the_encoded_colour_and_alpha_read_back_close_to_what_was_sampled():
    img = _leafy(16)
    for fmt in ("DXT5", "DXT3"):
        back = _decode(fmt, mipchain.encode_blocks(fmt, img), 16, 16).astype(int)
        assert np.abs(back[..., 3] - img[..., 3]).max() <= 9  # DXT3: 4 bits
        leaves = img[..., 3] > 128
        assert np.abs(back[..., :3][leaves] - img[..., :3][leaves].astype(int)).mean() < 12


def test_dxt1_keeps_a_clear_texel_clear_and_a_solid_block_solid():
    img = np.zeros((4, 4, 4), np.uint8)
    img[..., 1], img[..., 3] = 120, 255
    img[0, 0, 3] = 0
    back = _decode("DXT1", mipchain.encode_blocks("DXT1", img), 4, 4)
    assert back[0, 0, 3] == 0 and (back[..., 3] == 255).sum() == 15
    assert np.abs(back[1:, :, 1].astype(int) - 120).max() <= 2
    one = np.full((4, 4, 4), 77, np.uint8)
    one[..., 3] = 255
    assert np.abs(_decode("DXT5", mipchain.encode_blocks("DXT5", one), 4, 4).astype(int) - one).max() <= 4  # 5 bits


def test_two_channels_go_into_two_bc4_blocks():
    img = np.zeros((8, 8, 4), np.uint8)
    img[..., 0], img[..., 1] = np.arange(64).reshape(8, 8) * 4, 255 - np.arange(64).reshape(8, 8) * 4
    back = _decode("ATI2", mipchain.encode_blocks("ATI2", img), 8, 8).astype(int)
    assert (
        np.abs(back[..., 0] - img[..., 0]).max() <= 8 and np.abs(back[..., 1] - img[..., 1]).max() <= 8
    )  # half a step


def test_a_large_last_level_is_copied_by_block_not_decoded(monkeypatch):
    monkeypatch.setattr(mipchain, "ENCODE_MOST", 8)
    info = SDdsInfo(32, 32, 2, 1, "DXT5", False, 1)
    blocks = np.repeat(np.arange(16, dtype=np.uint8), 16)
    with mock.patch.object(mipchain.ddsdecode, "_compressed", side_effect=AssertionError("decoded")):
        levels = mipchain.full_levels(info, [bytes(64 * 16), blocks.tobytes()])
    assert np.frombuffer(levels[2], np.uint8).reshape(-1, 16)[:, 0].tolist() == [5, 7, 13, 15]


def test_an_old_games_cube_that_stops_short_gets_every_level_of_each_face():
    """The old engines' cube maps (``fxcdraw.cube_faces``: the viewer's and the island seas' sky cubes) go up with the
    full chain too: block faces through ``full_levels``, plain and half-float faces sampled from their last level."""
    import pytest

    pytest.importorskip("PySide6")
    from types import SimpleNamespace as N

    from annotoolbox.render import fxcdraw

    def cube(fmt: str, stored, decoded=None):
        info = SDdsInfo(16, 16, 2, 6, fmt)
        return N(m_info=info, size_of=lambda level: (16 >> level, 16 >> level), stored=stored,
                 level=decoded or (lambda level, face: None))  # fmt: skip

    def per_face(faces) -> dict:
        out: dict = {}
        for face, level, raw in faces.levels:
            out.setdefault(face, {})[level] = raw
        return out

    dxt = cube("DXT1", lambda level, face: bytes(((16 >> level) // 4) ** 2 * 8))
    got = per_face(fxcdraw.cube_faces(dxt))
    assert sorted(got) == list(range(6)) and all(sorted(levels) == list(range(5)) for levels in got.values())
    assert all(len(got[0][k]) == 8 for k in (2, 3, 4))  # one block each, made, not left to the memory
    rgba = cube("RGB8", None, lambda level, face: np.full((16 >> level, 16 >> level, 4), 40 * face, np.uint8))
    plain = per_face(fxcdraw.cube_faces(rgba))
    assert np.frombuffer(plain[3][4], np.uint8).tolist() == [120] * 4  # the 1 x 1 level of face 3: its colour
    half = cube("RGBA16F", lambda level, face: np.full((16 >> level, 16 >> level, 4), 2.5, np.float16).tobytes())
    hdr = per_face(fxcdraw.cube_faces(half))
    assert np.frombuffer(hdr[5][3], np.float16).tolist() == [2.5] * 16  # 2 x 2: the range kept, not clipped


def test_bc7_levels_are_the_stored_blocks_under_each_new_blocks_centre():
    blocks = np.repeat(np.arange(16, dtype=np.uint8), 16)  # a 16 x 16 level: 4 x 4 blocks, block k all k
    info = SDdsInfo(32, 32, 2, 1, "BC7", False, 1)
    levels = mipchain.full_levels(info, [bytes(64 * 16), blocks.tobytes()])
    assert len(levels) == 6
    eight = np.frombuffer(levels[2], np.uint8).reshape(-1, 16)
    assert eight[:, 0].tolist() == [5, 7, 13, 15]  # the 2 x 2 blocks of 8 x 8 texels: centres in blocks 5, 7, 13, 15
    assert all(len(raw) == 16 for raw in levels[3:])
