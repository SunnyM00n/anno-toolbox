"""The viewer's plain look takes a map's stored blocks (``render.blocktexture``) instead of an RGBA decode, as far as a
device can; viewer-performance.md section 4, item 6."""

from __future__ import annotations

from types import SimpleNamespace as N

import pytest

pytest.importorskip("PySide6")

from tests.ddsfiles import dx10, header
from tests.fakes import FakeFiles

from annotoolbox.render import blocktexture, mipchain
from annotoolbox.viewer.modelfiles import CModelFiles

BC7, BC5 = 98, 83


def block_file(code: int, width: int, height: int, mips: int, block_bytes: int = 16) -> bytes:
    """A DX10 file of ``mips`` levels of blank blocks, each level laid out as Direct3D stores it."""
    levels = (max(1, -(-(width >> k) // 4)) * max(1, -(-(height >> k) // 4)) * block_bytes for k in range(mips))
    return header(width, height, mips) + dx10(code) + b"".join(bytes(size) for size in levels)


def test_a_bc7_file_comes_as_its_stored_levels_from_the_first_one_that_fits():
    found = blocktexture.stored_blocks(block_file(BC7, 64, 64, 7), 32)
    assert (found.format, found.width_px, found.height_px) == ("BC7", 32, 32)
    assert len(found.levels) == mipchain.chain_length(32, 32) == 6
    assert [len(level) for level in found.levels] == [64 * 16, 16 * 16, 4 * 16, 16, 16, 16]


def test_a_chain_that_stops_short_is_completed_to_one_texel():
    found = blocktexture.stored_blocks(block_file(BC7, 64, 64, 3), 1024)
    assert len(found.levels) == mipchain.chain_length(64, 64) == 7


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x89PNG not a texture",
        block_file(BC7, 64, 64, 1),  # no chain: a GPU cannot build one of blocks
        block_file(BC5, 64, 64, 7, 16),  # no sRGB view of it, and it decodes to other channels
        block_file(BC7, 30, 30, 4),  # not whole blocks
        block_file(BC7, 64, 64, 7)[:-40],  # cut short
    ],
)
def test_a_file_whose_blocks_a_view_cannot_take_is_left_to_the_decoder(data):
    assert blocktexture.stored_blocks(data, 1024) is None


def test_the_model_files_hand_over_blocks_only_when_asked():
    files = CModelFiles(FakeFiles({"a.dds": block_file(BC7, 16, 16, 5)}))
    assert isinstance(files.texture("a.dds", 0, True), blocktexture.SBlockMap)
    decoded = files.texture("a.dds")
    assert decoded[:3] == (16, 16, 4) and len(decoded[3]) == 16 * 16 * 4


def test_a_device_that_cannot_take_the_format_or_the_view_gives_no_texture():
    blocks = blocktexture.stored_blocks(block_file(BC7, 16, 16, 5), 1024)
    refusing = N(m_rhi=N(isTextureFormatSupported=lambda *_args: False), m_decode_srgb=False)
    assert blocktexture.block_texture(refusing, blocks, True) is None
    assert blocktexture.block_texture(N(m_decode_srgb=False), blocks, False) is None  # moderngl: no QRhi
    gles = N(m_rhi=N(isTextureFormatSupported=lambda *_args: True), m_decode_srgb=True)
    assert blocktexture.block_texture(gles, blocks, True) is None  # OpenGL ES samples sRGB blocks undecoded
