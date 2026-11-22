"""``models/bc6hdecode``: our own BC6H decoder, checked byte for byte against ``texture2ddecoder`` on random blocks of
every mode and on the sky maps of an Anno 117 install (skipped without ``ANNO117_DIR``), and signed blocks against
Pillow's decoder. ``texture2ddecoder`` reads an endpoint of exactly half its range (``1 << (bits - 1)``) as the
maximum and has no maximum of its own, which the specification does not; blocks with such an endpoint are left out
of the comparison (``quirk_blocks``). The rest needs nothing but numpy."""

from __future__ import annotations

import io
import struct

import numpy as np
import pytest
from tests.fakes import installed

from annotoolbox.models import bc6hdecode
from annotoolbox.models.bc6hdecode import MODE_OF, PARSED, PRECISION, decode_blocks
from annotoolbox.models.ddsdecode import CDdsFile, to_bytes

CODE = {mode: code for code, mode in MODE_OF.items()}


def random_blocks(mode: int, count: int, seed: int) -> np.ndarray:
    """Random blocks of one mode (0: the reserved mode bits 10011)."""
    blocks = np.random.default_rng(seed).integers(0, 256, (count, 16), dtype=np.uint8)
    code, keep = (19, 0xE0) if mode == 0 else (CODE[mode], 0xFC if mode <= 2 else 0xE0)
    blocks[:, 0] = (blocks[:, 0] & keep) | code
    return blocks


def reference(blocks: np.ndarray) -> np.ndarray:
    """``texture2ddecoder``'s picture of the blocks as ``(n, 16, 3)`` RGB bytes."""
    t2d = pytest.importorskip("texture2ddecoder")
    n = len(blocks)
    bgra = np.frombuffer(t2d.decode_bc6(blocks.tobytes(), 4 * n, 4), np.uint8).reshape(4, n, 4, 4)
    return bgra[..., 2::-1].transpose(1, 0, 2, 3).reshape(n, 16, 3)


def as_bytes(blocks: np.ndarray, signed: bool = False) -> np.ndarray:
    return to_bytes(decode_blocks(blocks, signed).reshape(-1, 1, 3))[:, 0, :3].reshape(len(blocks), 16, 3)


def quirk_blocks(blocks: np.ndarray) -> np.ndarray:
    """Which unsigned blocks have an endpoint of half range or of full range, where ``texture2ddecoder`` differs."""
    bits = np.unpackbits(blocks, axis=1, bitorder="little").astype(np.int64)
    first, five = bits[:, 0] | bits[:, 1] << 1, bits[:, :5] @ (1 << np.arange(5))
    hit = np.zeros(len(blocks), bool)
    for code, mode in MODE_OF.items():
        chosen = (first == code) if mode <= 2 else (five == code)
        base = PRECISION[mode][0]
        if chosen.any() and base < 15:
            ends = bc6hdecode._endpoints(mode, bits[chosen], False)[:, : 4 if mode <= 10 else 2]
            hit[chosen] = ((ends == 1 << (base - 1)) | (ends == (1 << base) - 1)).any(axis=(1, 2))
    return hit


def test_every_layout_fills_the_block_up_to_its_indices():
    for mode, fields in PARSED.items():
        assert sum(len(places) for places in fields.values()) == (82 if mode <= 10 else 65), mode
        assert sorted(at for places in fields.values() for at, _bit in places) == list(range(82 if mode <= 10 else 65))


@pytest.mark.parametrize("mode", range(0, 15))
def test_random_blocks_of_every_mode_match_another_decoder_byte_for_byte(mode):
    blocks = random_blocks(mode, 4000, seed=200 + mode)
    quirk = quirk_blocks(blocks)
    assert mode == 0 or 0.5 < 1 - quirk.mean()  # most blocks are compared
    assert np.array_equal(as_bytes(blocks)[~quirk], reference(blocks)[~quirk])


def test_values_pass_one_and_a_signed_block_mirrors_an_unsigned_one():
    def flat(mode_value: int) -> np.ndarray:  # mode 11, both endpoints of every channel the value, indices 0
        word = CODE[11]
        for channel in ("rw", "gw", "bw", "rx", "gx", "bx"):
            for at, bit in PARSED[11][channel]:
                word |= ((mode_value >> bit) & 1) << at
        return np.frombuffer(word.to_bytes(16, "little"), np.uint8)[None]

    bright = decode_blocks(flat(900))[0, 0]
    assert (bright > 1.0).all() and np.isfinite(bright).all()
    plus, minus = decode_blocks(flat(24), signed=True)[0, 0], decode_blocks(flat(1024 - 24), signed=True)[0, 0]
    assert (plus > 0).all() and np.array_equal(minus, -plus)
    assert np.array_equal(decode_blocks(flat(24))[0, 0], decode_blocks(flat(24), signed=False)[0, 0])


def test_a_reserved_mode_is_black():
    assert not decode_blocks(random_blocks(0, 50, 1)).any()


def dds_of(blocks: bytes, width: int, height: int, dxgi: int) -> bytes:
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, 0x1007, height, width, len(blocks), 0, 1) + bytes(44)
    pixel = struct.pack("<II4s5I", 32, 4, b"DX10", 0, 0, 0, 0, 0)
    return head + pixel + struct.pack("<5I", 0x1000, 0, 0, 0, 0) + struct.pack("<5I", dxgi, 3, 0, 1, 0) + blocks


def test_a_bc6h_dds_reads_clipped_and_unclipped_and_cuts_the_edge_blocks():
    flat = bytes(random_blocks(11, 1, 5)[0])
    unsigned = CDdsFile(dds_of(flat * 4, 6, 5, 95))
    assert unsigned.m_info.format == "BC6H" and unsigned.m_hdr and not unsigned.m_signed
    assert unsigned.level(0).shape == (5, 6, 4) and unsigned.level_float(0).shape == (5, 6, 4)
    assert (unsigned.level(0)[..., 3] == 255).all() and (unsigned.level_float(0)[..., 3] == 1.0).all()
    assert CDdsFile(dds_of(flat * 4, 6, 5, 96)).m_signed


def pillow_image(blocks: np.ndarray, signed: bool) -> np.ndarray:
    image_mod = pytest.importorskip("PIL.Image")
    n = len(blocks)
    data = dds_of(blocks.tobytes(), 4 * n, 4, 96 if signed else 95)
    try:
        image = image_mod.open(io.BytesIO(data))
        image.load()
    except (OSError, NotImplementedError, ValueError):
        pytest.skip("this Pillow does not read BC6H")
    return np.asarray(image).reshape(4, n, 4, 3).transpose(1, 0, 2, 3).reshape(n, 16, 3).astype(int)


@pytest.mark.parametrize("mode", (10, 11))
@pytest.mark.parametrize("signed", (False, True))
def test_whole_endpoint_modes_match_pillow_signed_and_unsigned(mode, signed):
    blocks = random_blocks(mode, 3000, seed=300 + mode)
    ours = np.floor(np.clip(decode_blocks(blocks, signed), 0.0, 1.0) * 255.0).astype(int)  # Pillow truncates
    equal = (ours == pillow_image(blocks, signed)).all(axis=(1, 2))
    assert equal.mean() > 0.995


@pytest.mark.skipif(installed("anno117") is None, reason="needs an Anno 117 install (ANNO117_DIR)")
def test_the_games_own_hdr_textures_match_another_decoder_byte_for_byte():
    files = installed("anno117")  # the install's BC6H files: the night sky's cube (the wave map is a volume)
    names = ("data/base/graphics/library/ambient/sky/panorama_night_0.dds",)
    compared = 0
    for path in names:
        dds = CDdsFile(files.read(path))
        assert dds.m_info.format == "BC6H", path
        for mip in range(4, dds.m_info.mips):  # the levels from 128 square down: every block, quickly
            width, height = dds.size_of(mip)
            at = dds._m_offsets[mip]
            blocks = np.frombuffer(dds.m_data, np.uint8, -(-width // 4) * -(-height // 4) * 16, at).reshape(-1, 16)
            quirk = quirk_blocks(blocks)
            assert np.array_equal(as_bytes(blocks, dds.m_signed)[~quirk], reference(blocks)[~quirk]), (path, mip)
            compared += int((~quirk).sum())
        assert np.isfinite(dds.level_float(0)).all(), path
    assert compared > 1000
