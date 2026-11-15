"""``ddsdecode.DdsFile``: every level and face of synthetic DDS files, the DX10 header, the layouts by hand."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from annotoolbox.models.ddsdecode import DdsFile, UnsupportedDds, read_dds


def header(
    width: int, height: int, mips: int, *, fourcc: bytes = b"DX10", cube: bool = False, rgb_bits: int = 0
) -> bytes:
    """A DDS header (and the DX10 one is the caller's); ``rgb_bits``: an uncompressed BGRA layout of that many bits."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000
    pf = struct.pack("<II4s5I", 32, 0x40 | 0x1 if rgb_bits else 0x4, b"" if rgb_bits else fourcc, rgb_bits,
                     0xFF0000, 0xFF00, 0xFF, 0xFF000000)  # fmt: skip
    caps2 = 0xFE00 if cube else 0
    body = struct.pack("<7I", flags, height, width, 0, 0, mips, 0) + b"\0" * 40 + pf
    return b"DDS " + struct.pack("<I", 124) + body + struct.pack("<4I", 0x1000, caps2, 0, 0) + b"\0" * 4


def dx10(code: int, array: int = 1, cube: bool = False) -> bytes:
    return struct.pack("<5I", code, 3, 0x4 if cube else 0, array, 0)


def test_an_rgba8_file_reads_every_mip_level():
    levels = [np.full((4 >> k, 8 >> k, 4), 10 * k + 1, np.uint8) for k in range(3)]
    data = header(8, 4, 3) + dx10(28) + b"".join(lv.tobytes() for lv in levels)
    dds = DdsFile(data)
    assert (dds.info.width, dds.info.height, dds.info.mips, dds.info.faces, dds.info.format) == (8, 4, 3, 1, "RGBA8")
    for k in range(3):
        assert dds.level(k).shape == (max(1, 4 >> k), 8 >> k, 4) and int(dds.level(k)[0, 0, 0]) == 10 * k + 1
    with pytest.raises(IndexError):
        dds.level(3)


def test_bgra_srgb_and_the_faces_of_a_cube_in_order():
    faces = [bytes([k, 2 * k, 3 * k, 255]) * 4 for k in range(6)]  # 2 x 2, one mip, B G R A
    dds = DdsFile(header(2, 2, 1) + dx10(91, cube=True) + b"".join(faces))
    assert dds.info.faces == 6 and dds.info.srgb
    assert dds.level(0, 5)[0, 0].tolist() == [15, 10, 5, 255]  # as R G B A


def test_sixteen_bit_and_float_layouts_are_scaled_to_bytes():
    r16 = np.array([[0, 65535]], "<u2").tobytes() + np.array([[32768, 0]], "<u2").tobytes()
    px = DdsFile(header(2, 2, 1) + dx10(56) + r16).level(0)
    assert px[..., 0].tolist() == [[0, 255], [128, 0]] and px[0, 0, 3] == 255
    half = np.array([0.5, 2.0, -1.0, 1.0], "<f2").tobytes()
    assert DdsFile(header(1, 1, 1) + dx10(10) + half).level(0)[0, 0].tolist() == [128, 255, 0, 255]


def test_a_legacy_dxt1_and_an_uncompressed_file_read_as_read_dds_reads_them():
    block = struct.pack("<HHI", 0xF800, 0x001F, 0)  # every texel the first colour: red
    data = header(4, 4, 2, fourcc=b"DXT1") + block + block
    dds = DdsFile(data)
    assert dds.info.mips == 2 and (dds.level(0) == read_dds(header(4, 4, 1, fourcc=b"DXT1") + block)).all()
    assert dds.level(1).shape == (2, 2, 4) and dds.level(1)[0, 0, :3].tolist() == [255, 0, 0]
    rgb = DdsFile(header(1, 1, 1, rgb_bits=32) + bytes([30, 20, 10, 255]))
    assert rgb.level(0)[0, 0].tolist() == [10, 20, 30, 255]


def test_a_short_file_and_an_unknown_format_are_refused():
    with pytest.raises(UnsupportedDds):
        DdsFile(header(8, 8, 1) + dx10(28) + bytes(10)).level(0)
    with pytest.raises(UnsupportedDds):
        DdsFile(header(4, 4, 1) + dx10(777))


def test_a_volume_reads_its_first_slice_and_knows_its_depth():
    red = struct.pack("<HHI", 0xF800, 0xF800, 0)  # one DXT1 block, both colours pure red
    volume = bytearray(header(4, 4, 1, fourcc=b"DXT1") + red + bytes(8))  # two slices of one block each
    volume[112:116] = struct.pack("<I", 0x200000)
    volume[24:28] = struct.pack("<I", 2)
    dds = DdsFile(bytes(volume))
    assert dds.info.depth == 2 and not dds.cut_short() and dds.level(0)[0, 0].tolist() == [255, 0, 0, 255]
    assert "cut short" in DdsFile(bytes(volume[:-4])).cut_short()


def test_a_legacy_header_naming_a_float_format_by_number_reads():
    data = header(2, 1, 1, fourcc=bytes([114, 0, 0, 0])) + struct.pack("<2f", 0.5, 2.0)  # D3DFMT_R32F
    dds = DdsFile(data)
    assert dds.info.format == "R32F" and dds.hdr and dds.level_float(0)[0, 1, 0] == 2.0
    assert DdsFile(header(1, 1, 1) + dx10(36) + struct.pack("<2H", 65535, 0)).level(0)[0, 0].tolist() == [
        255,
        0,
        0,
        255,
    ]


def test_bc7_reads_through_the_numpy_decoder():
    from annotoolbox.models import bc7encode

    if not bc7encode.available():
        pytest.skip("no BC7 encoder to make the file")
    px = np.zeros((8, 8, 4), np.uint8)
    px[..., 0], px[..., 3] = 200, 255
    dds = DdsFile(bc7encode.encode_dds([px], srgb=True, perceptual=False))
    assert dds.info.format == "BC7" and dds.info.srgb
    got = dds.level(0)
    assert got.shape == (8, 8, 4) and abs(int(got[..., 0].mean()) - 200) <= 2 and int(got[..., 1].max()) <= 2
