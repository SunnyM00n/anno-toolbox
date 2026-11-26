"""A map's stored BC blocks as a view's texture, not decoded to RGBA: the viewer's plain look uploads what the file holds,
with the file's own mip levels and an sRGB or linear view of the blocks.

``stored_blocks(data, limit)`` runs on a loading thread and reads the levels out of a DDS; ``block_texture(ctx, blocks,
srgb)`` makes the QRhi texture on the window's thread. Either gives None where the blocks cannot be used (the caller
decodes as before). viewer-performance.md section 4, item 6.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
)

from ..models.ddsdecode import CDdsFile, CUnsupportedDds
from . import mipchain
from .gametextures import BLOCK_FORMATS, SRGB_BLOCKS
from .rhidevice import LINEAR, LINEAR_MIPMAP_LINEAR, CTexture
from .rhipass import CRecorder


@dataclass(frozen=True)
class SBlockMap:
    """A map as stored: its DDS block ``format``, the size of its first level and every level's blocks down to 1 x 1."""

    format: str
    width_px: int
    height_px: int
    levels: list[bytes]


def stored_blocks(data: bytes, limit: int) -> SBlockMap | None:
    """The blocks of a flat DDS in a format an sRGB view exists for (BC1 to BC3, BC7), from the first level at or below
    ``limit`` texels; None for any other file, a file with no mip chain, a level that is not whole blocks or a file cut
    short (``fileresolve.decode`` then reads it or says why not)."""
    if data[:4] != b"DDS ":
        return None
    try:
        dds = CDdsFile(data)
    except (CUnsupportedDds, IndexError, ValueError):
        return None
    info = dds.m_info
    if info.format not in SRGB_BLOCKS or info.faces != 1 or info.depth > 1 or dds.cut_short():
        return None
    first = next((k for k in range(info.mips) if max(dds.size_of(k)) <= limit), info.mips - 1)
    width_px, height_px = dds.size_of(first)
    if (
        info.mips - first < 2 or width_px % 4 or height_px % 4
    ):  # a GPU cannot build the levels of blocks, nor take a part block
        return None
    from_first = replace(info, width_px=width_px, height_px=height_px, mips=info.mips - first)
    levels = mipchain.full_levels(from_first, [dds.stored(k) for k in range(first, info.mips)])
    return SBlockMap(info.format, width_px, height_px, levels[: mipchain.chain_length(width_px, height_px)])


def block_texture(ctx, blocks: SBlockMap, srgb: bool) -> CTexture | None:
    """The texture of ``blocks`` on a QRhi device, its levels uploaded in the frame's batch; None where the device
    cannot take the format (or the sRGB view of it), so the caller decodes instead."""
    rhi = getattr(ctx, "m_rhi", None)
    if rhi is None or (srgb and ctx.m_decode_srgb):  # OpenGL ES samples sRGB blocks undecoded (``CRhiContext.texture``)
        return None
    flags = QRhiTexture.Flag.MipMapped | (QRhiTexture.Flag.sRGB if srgb else QRhiTexture.Flag(0))
    block_format = BLOCK_FORMATS[blocks.format]
    if not rhi.isTextureFormatSupported(block_format, flags):
        return None
    native = rhi.newTexture(block_format, QSize(blocks.width_px, blocks.height_px), 1, flags)
    if not native.create():
        native.destroy()
        return None
    texture = CTexture(
        ctx, (blocks.width_px, blocks.height_px), 4, m_srgb=srgb, m_mipmapped=True, native=native,
        m_serial=CRecorder.next_serial(), m_target=False,
    )  # fmt: skip
    texture.filter = (LINEAR_MIPMAP_LINEAR, LINEAR)
    upload = QRhiTextureUploadDescription()
    upload.setEntries(
        [QRhiTextureUploadEntry(0, level, QRhiTextureSubresourceUploadDescription(raw))
         for level, raw in enumerate(blocks.levels)]
    )  # fmt: skip
    ctx._upload(texture, lambda updates: updates.uploadTexture(native, upload))
    return texture
