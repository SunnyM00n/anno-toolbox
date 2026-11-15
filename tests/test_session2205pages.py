import os
import struct
from pathlib import Path

import numpy as np
import pytest

from annotoolbox.editor.formats.a7m import edit2205 as a7m_edit2205
from annotoolbox.editor.formats.a7m import session as a7m_session
from annotoolbox.editor.formats.a7m import sessionpages as a7m_sessionpages
from annotoolbox.models import bc1encode, ddsdecode
from fakes import INSTALL_VARS
from test_session2205ground import PATH, _files

PAGE = "data/sessions/session_test/_a6mdata/session_test/normalmaps/0/0_0.dds"
SIDE = 64  # a small page: 4 texels a tile on the page's 16 tiles


def dds(rgb: np.ndarray) -> bytes:
    """A DXT1 file of an image the way a 2205 page is laid out: the 128-byte header, the top mip."""
    h, w = rgb.shape[:2]
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<7I", header, 4, 124, 0x81007, h, w, w * h // 2, 0, 1)
    struct.pack_into("<II4s", header, 76, 32, 4, b"DXT1")
    struct.pack_into("<I", header, 108, 0x1000)
    return bytes(header) + bc1encode.encode_blocks(bc1encode.to_blocks(rgb)).tobytes()


def flat_page() -> np.ndarray:
    return np.tile(np.array([128, 255, 128], np.uint8), (SIDE, SIDE, 1))


def test_bc1_blocks_of_a_smooth_image_decode_close_and_read_back_through_the_dds_reader():
    y, x = np.mgrid[0:SIDE, 0:SIDE]
    img = np.stack([x * 3, 255 - y * 3, (x + y) * 2], axis=-1).astype(np.uint8)
    blocks = bc1encode.encode_blocks(bc1encode.to_blocks(img))
    back = bc1encode.from_blocks(bc1encode.decode_blocks(blocks), SIDE, SIDE)
    assert np.abs(back.astype(int) - img).mean() < 4
    assert np.array_equal(ddsdecode.read_dds(dds(img))[..., :3], back)  # the file reader agrees with ours


def test_a_block_of_one_colour_keeps_that_colour_and_a_bad_size_is_refused():
    block = bc1encode.encode_blocks(np.full((1, 16, 3), (128, 255, 128), np.uint8))
    assert (bc1encode.decode_blocks(block)[0] == bc1encode.decode_blocks(block)[0][0]).all()
    with pytest.raises(ValueError, match="multiples of 4"):
        bc1encode.to_blocks(np.zeros((6, 8, 3), np.uint8))


def hill(n: int = 20) -> np.ndarray:
    deltas = np.zeros((n + 1, n + 1))
    deltas[6:9, 6:9] = [[0, 1, 0], [1, 3, 1], [0, 1, 0]]
    return deltas


def slope_x(rgb: np.ndarray) -> np.ndarray:
    n = rgb.astype(float) / 255 * 2 - 1
    return -n[..., 0] / np.maximum(n[..., 1], 0.2)


def test_a_hill_tilts_the_page_where_it_stands_and_leaves_the_other_blocks_byte_for_byte():
    raw = dds(flat_page())
    out = a7m_sessionpages.rewrite_page(raw, 0, 0, 0, hill())
    assert out is not None and len(out) == len(raw) and out[:128] == raw[:128]
    old, new = ddsdecode.read_dds(raw)[..., :3], ddsdecode.read_dds(out)[..., :3]
    sx = slope_x(new)
    west, east = sx[:, 4 * 6 : 4 * 7].mean(), sx[:, 4 * 8 : 4 * 9].mean()  # the hill's west flank rises, its east falls
    assert west > 0.1 and east < -0.1
    far = (slice(0, 8), slice(40, 64))  # nowhere near the hill
    assert np.array_equal(old[far], new[far])
    assert a7m_sessionpages.rewrite_page(raw, 0, 0, 0, np.zeros((21, 21))) is None


def test_the_slope_of_a_delta_is_the_delta_per_tile():
    deltas = np.zeros((20, 20))
    deltas[:, 10:] = np.arange(10)[None, :] * 0.5  # rising 0.5 a tile east of tile 10
    gx, gz = a7m_sessionpages.delta_slopes(deltas, 0, 0, 16, SIDE)
    assert gx[32, 4 * 13] == pytest.approx(0.5, abs=0.05) and abs(gz[32, 4 * 13]) < 0.05


def test_a_height_edit_of_a_session_rewrites_its_pages_and_the_build_carries_them():
    files = _files()
    files.files[PAGE] = dds(flat_page())
    isl = a7m_session.Session2205(files, PATH)
    report = a7m_edit2205.apply(isl, {"heights": {"2,2": 3.0}})
    assert report["normal pages"] == 1 and PAGE in isl.new_files
    plain = a7m_session.Session2205(files, PATH)
    assert a7m_edit2205.apply(plain, {"objects": {}})["members"] == [] and plain.new_files == {}


def test_the_pages_of_an_installed_session_follow_a_hill():
    folder = os.environ.get(INSTALL_VARS["anno2205"])
    if not folder or not (Path(folder) / "maindata").is_dir():
        pytest.skip("needs the installed Anno 2205")
    from annotoolbox.othergames.index2205 import ArchiveIndex
    from annotoolbox.sources.gametree import open_game

    tree = open_game("anno2205", Path(folder), None)
    path = "data/sessions/session_earth_02/session_earth_02.a6m"
    if not tree.exists(path):
        pytest.skip("no session_earth_02")
    isl = a7m_session.Session2205(tree, path)
    deltas = np.zeros((isl.size + 1,) * 2)
    deltas[688:693, 643:648] = [[0, 2, 4, 2, 0], [2, 4, 6, 4, 2], [4, 6, 8, 6, 4], [2, 4, 6, 4, 2], [0, 2, 4, 2, 0]]
    pages = a7m_sessionpages.rewrite(isl, deltas)
    assert pages
    index = ArchiveIndex((Path(folder) / "maindata/file.db").read_bytes())
    for name, data in pages.items():
        assert index.entry(name) is not None  # the build can point the index at it
        assert len(data) == len(tree.read(name))
