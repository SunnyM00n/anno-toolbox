"""``ddsdecode.CDdsFile``: every level and face of synthetic DDS files, the DX10 header, the layouts by hand."""

from __future__ import annotations

import struct

import numpy as np
import pytest
from tests.ddsfiles import dx10, header

from annotoolbox.models.ddsdecode import CDdsFile, CUnsupportedDds, read_dds


def test_an_rgba8_file_reads_every_mip_level():
    levels = [np.full((4 >> k, 8 >> k, 4), 10 * k + 1, np.uint8) for k in range(3)]
    data = header(8, 4, 3) + dx10(28) + b"".join(lv.tobytes() for lv in levels)
    dds = CDdsFile(data)
    assert (dds.m_info.width_px, dds.m_info.height_px, dds.m_info.mips, dds.m_info.faces, dds.m_info.format) == (
        8,
        4,
        3,
        1,
        "RGBA8",
    )
    for k in range(3):
        assert dds.level(k).shape == (max(1, 4 >> k), 8 >> k, 4) and int(dds.level(k)[0, 0, 0]) == 10 * k + 1
    with pytest.raises(IndexError):
        dds.level(3)


def test_bgra_srgb_and_the_faces_of_a_cube_in_order():
    faces = [bytes([k, 2 * k, 3 * k, 255]) * 4 for k in range(6)]  # 2 x 2, one mip, B G R A
    dds = CDdsFile(header(2, 2, 1) + dx10(91, cube=True) + b"".join(faces))
    assert dds.m_info.faces == 6 and dds.m_info.srgb
    assert dds.level(0, 5)[0, 0].tolist() == [15, 10, 5, 255]  # as R G B A


def test_sixteen_bit_and_float_layouts_are_scaled_to_bytes():
    r16 = np.array([[0, 65535]], "<u2").tobytes() + np.array([[32768, 0]], "<u2").tobytes()
    px = CDdsFile(header(2, 2, 1) + dx10(56) + r16).level(0)
    assert px[..., 0].tolist() == [[0, 255], [128, 0]] and px[0, 0, 3] == 255
    half = np.array([0.5, 2.0, -1.0, 1.0], "<f2").tobytes()
    assert CDdsFile(header(1, 1, 1) + dx10(10) + half).level(0)[0, 0].tolist() == [128, 255, 0, 255]


def test_a_legacy_dxt1_and_an_uncompressed_file_read_as_read_dds_reads_them():
    block = struct.pack("<HHI", 0xF800, 0x001F, 0)  # every texel the first colour: red
    data = header(4, 4, 2, fourcc=b"DXT1") + block + block
    dds = CDdsFile(data)
    assert dds.m_info.mips == 2 and (dds.level(0) == read_dds(header(4, 4, 1, fourcc=b"DXT1") + block)).all()
    assert dds.level(1).shape == (2, 2, 4) and dds.level(1)[0, 0, :3].tolist() == [255, 0, 0]
    rgb = CDdsFile(header(1, 1, 1, rgb_bits=32) + bytes([30, 20, 10, 255]))
    assert rgb.level(0)[0, 0].tolist() == [10, 20, 30, 255]


def test_a_short_file_and_an_unknown_format_are_refused():
    with pytest.raises(CUnsupportedDds):
        CDdsFile(header(8, 8, 1) + dx10(28) + bytes(10)).level(0)
    with pytest.raises(CUnsupportedDds):
        CDdsFile(header(4, 4, 1) + dx10(777))


@pytest.mark.parametrize("code", [10, 95])  # half floats, BC6H: the layouts ``level_float`` reads itself
def test_a_float_level_the_file_ends_inside_is_refused_as_a_byte_level_is(code):
    with pytest.raises(CUnsupportedDds):
        CDdsFile(header(4, 4, 1) + dx10(code) + bytes(8)).level_float(0)
    with pytest.raises(IndexError):
        CDdsFile(header(4, 4, 1) + dx10(code) + bytes(128)).level_float(3)


def test_a_header_that_claims_too_much_is_cut_to_what_a_file_can_hold():
    assert CDdsFile(header(4, 4, 5_000_000) + dx10(28) + bytes(84)).m_info.mips == 3  # 4, 2, 1 texels across
    with pytest.raises(CUnsupportedDds):
        CDdsFile(header(4, 4, 1) + dx10(28, array=10_000_000) + bytes(64))


def test_a_volume_reads_its_first_slice_and_knows_its_depth():
    red = struct.pack("<HHI", 0xF800, 0xF800, 0)  # one DXT1 block, both colours pure red
    volume = bytearray(header(4, 4, 1, fourcc=b"DXT1") + red + bytes(8))  # two slices of one block each
    volume[112:116] = struct.pack("<I", 0x200000)
    volume[24:28] = struct.pack("<I", 2)
    dds = CDdsFile(bytes(volume))
    assert dds.m_info.depth == 2 and not dds.cut_short() and dds.level(0)[0, 0].tolist() == [255, 0, 0, 255]
    assert "cut short" in CDdsFile(bytes(volume[:-4])).cut_short()


def test_a_legacy_header_naming_a_float_format_by_number_reads():
    data = header(2, 1, 1, fourcc=bytes([114, 0, 0, 0])) + struct.pack("<2f", 0.5, 2.0)  # D3DFMT_R32F
    dds = CDdsFile(data)
    assert dds.m_info.format == "R32F" and dds.m_hdr and dds.level_float(0)[0, 1, 0] == 2.0
    assert CDdsFile(header(1, 1, 1) + dx10(36) + struct.pack("<2H", 65535, 0)).level(0)[0, 0].tolist() == [
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
    dds = CDdsFile(bc7encode.encode_dds([px], is_srgb=True, perceptual=False))
    assert dds.m_info.format == "BC7" and dds.m_info.srgb
    got = dds.level(0)
    assert got.shape == (8, 8, 4) and abs(int(got[..., 0].mean()) - 200) <= 2 and int(got[..., 1].max()) <= 2
