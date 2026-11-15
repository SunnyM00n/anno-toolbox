"""``models/bc7decode``: our own BC7 decoder, checked byte for byte against ``texture2ddecoder`` on random blocks of
every mode (all partitions, rotations and index selections) and on the real files of an Anno 117 install (skipped
without ``ANNO117_DIR``); a solid block and the ``DdsFile`` route need nothing but numpy."""

from __future__ import annotations

import numpy as np
import pytest
from tests.fakes import installed

from annotoolbox.models import bc7decode, bc7encode
from annotoolbox.models.ddsdecode import DdsFile


def reference(data: bytes, width: int, height: int) -> np.ndarray:
    """``texture2ddecoder``'s picture of the blocks, as RGBA."""
    t2d = pytest.importorskip("texture2ddecoder")
    bw, bh = -(-width // 4) * 4, -(-height // 4) * 4
    bgra = np.frombuffer(t2d.decode_bc7(data, bw, bh), np.uint8).reshape(bh, bw, 4)
    return bgra[:height, :width, [2, 1, 0, 3]]


def random_blocks(mode: int, count: int, seed: int) -> bytes:
    """Random blocks of one mode (8: no mode bit set, a reserved block)."""
    blocks = np.random.default_rng(seed).integers(0, 256, (count, 16), dtype=np.uint8)
    head = np.uint8((0xFF << (mode + 1)) & 0xFF) if mode < 8 else np.uint8(0)
    blocks[:, 0] = (blocks[:, 0] & head) | (np.uint8(1 << mode) if mode < 8 else np.uint8(0))
    return blocks.tobytes()


def test_a_solid_block_decodes_to_its_colour_without_any_extra_package():
    for rgba in ((255, 0, 126, 255), (2, 255, 66, 7), (0, 0, 0, 0)):
        block = bc7encode.solid_block(rgba)
        assert (bc7decode.decode_bc7(block, 4, 4) == np.array(rgba, np.uint8)).all()
    assert (bc7decode.decode_bc7(bytes(16), 4, 4) == 0).all()  # a reserved block is transparent black


def test_a_dds_in_bc7_reads_its_levels_and_cuts_the_edge_blocks(bc7_dds):
    head = bc7_dds(6, 5)[:-16]
    block = bc7encode.solid_block((10, 20, 30, 40))
    dds = DdsFile(head + block * 4)
    assert dds.info.format == "BC7" and dds.level(0).shape == (5, 6, 4) and (dds.level(0) == (10, 20, 30, 40)).all()


@pytest.mark.parametrize("mode", range(9))
def test_random_blocks_of_every_mode_match_another_decoder_byte_for_byte(mode):
    data = random_blocks(mode, 64 * 64, seed=100 + mode)
    assert np.array_equal(bc7decode.decode_bc7(data, 256, 256), reference(data, 256, 256))


def test_an_encoded_picture_reads_back_as_the_encoder_meant():
    etcpak = pytest.importorskip("etcpak")
    y, x = np.mgrid[0:64, 0:48]
    picture = np.stack([x * 5, y * 4, (x ^ y) * 3, 255 - x], -1).astype(np.uint8)
    data = bytes(etcpak.compress_bc7(np.ascontiguousarray(picture).tobytes(), 48, 64, etcpak.BC7CompressBlockParams()))
    decoded = bc7decode.decode_bc7(data, 48, 64)
    assert np.abs(decoded.astype(int) - picture).mean() < 3.0
    assert np.array_equal(decoded, reference(data, 48, 64))


@pytest.mark.skipif(installed("anno117") is None, reason="needs an Anno 117 install (ANNO117_DIR)")
def test_the_games_own_bc7_textures_match_another_decoder_byte_for_byte():
    files = installed("anno117")
    checked = 0
    for path in files.iter_paths("data/base/graphics/roman/buildings/"):
        if not path.endswith(".dds") or checked >= 40:
            continue
        dds = DdsFile(files.read(path))
        if dds.info.format != "BC7":
            continue
        mip = max(0, dds.info.mips - 4)  # a small level: every block of it, quickly
        width, height = dds.size_of(mip)
        raw = dds.data[dds._offsets[mip] : dds._offsets[mip] + -(-width // 4) * -(-height // 4) * 16]
        assert np.array_equal(dds.level(mip), reference(raw, width, height)), path
        checked += 1
    assert checked >= 10
