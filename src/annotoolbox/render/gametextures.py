"""A material's maps on QRhi as the game's shaders read them: the DDS block formats a map uploads as, the table that
spreads a material's maps over the bindless stand-in's texture arrays, and the arrays made and uploaded as the files
store them (BC blocks unchanged).

``CTextureTable(maps)`` is the entry (``add`` one more map, ``create(rhi, batch)`` the arrays, ``slots`` and ``words``
what the shaders read); ``class_array`` makes one array, ``map_kind`` says how a map goes into one, ``stored_levels``
a flat map's levels. ``render.gamedraw`` draws with them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
)

from . import gameshader, mipchain

TextureFormat = QRhiTexture.Format
# a DDS's block kind -> the QRhi format it uploads as
BLOCK_FORMATS = {
    "DXT1": TextureFormat.BC1,
    "DXT3": TextureFormat.BC2,
    "DXT5": TextureFormat.BC3,
    "ATI1": TextureFormat.BC4,
    "ATI2": TextureFormat.BC5,
    "BC6H": TextureFormat.BC6H,
    "BC7": TextureFormat.BC7,
}
SRGB_BLOCKS = ("DXT1", "DXT3", "DXT5", "BC7")


@dataclass
class _SClass:
    """One texture array of the bindless stand-in: maps of one size, format and level count."""

    key: tuple
    maps: list = field(default_factory=list)  # (dds or rgba array) per layer


class CTextureTable:
    """A material's maps spread over ``gameshader.CLASSES`` texture arrays; ``slots`` gives each parameter its
    bindless index (0 is the engine's null texture, white), ``words`` the table the stand-in reads."""

    def __init__(self, maps: dict) -> None:
        self.m_classes: list[_SClass] = [_SClass(("null",), [np.full((4, 4, 4), 255, np.uint8)])]
        self.m_slots: dict[str, int] = {}
        self.m_words = np.zeros(gameshader.SLOTS, np.uint32)
        for name, dds in maps.items():
            self.add(name, dds)

    def add(self, name: str, dds) -> int:
        """One more map under ``name``, before the arrays are made (a cloth's player logo, ``render.clothlogo``);
        its bindless index, 0 (the null texture) when every array is taken."""
        stored, key = map_kind(dds)
        found = next((array_class for array_class in self.m_classes if array_class.key == key), None)
        if found is None:
            if len(self.m_classes) == gameshader.CLASSES:
                return 0  # more kinds of map than arrays: the parameter reads the null texture
            found = _SClass(key)
            self.m_classes.append(found)
        found.maps.append(dds if stored else dds.level(0))
        slot = len(self.m_slots) + 1
        self.m_slots[name] = slot
        self.m_words[slot] = (self.m_classes.index(found) << 16) | (len(found.maps) - 1)
        return slot

    def drop_pixels(self) -> None:
        """Forget the maps' pixels once their arrays are made (``slots`` and ``words`` stay): an owner that makes
        the arrays once for all its draws (``gameinstances.CPartPass``) keeps no second copy."""
        self.m_classes = []

    def create(self, rhi, batch) -> list:
        """The texture arrays (one per class, a white layer for the unused), uploaded in ``batch``."""
        if not self.m_classes:
            raise RuntimeError("the maps' pixels were dropped: their arrays are made already")
        null = self.m_classes[0].maps
        return [class_array(rhi, batch, self.m_classes[k].maps if k < len(self.m_classes) else null)
                for k in range(gameshader.CLASSES)]  # fmt: skip


def class_array(rhi, batch, maps: list):
    """One texture array of maps alike (``models.ddsdecode.CDdsFile``s of one size, format and level count, stored
    as they are; or RGBA arrays of one size), uploaded in ``batch``."""
    first = maps[0]
    if isinstance(first, np.ndarray):
        height, width = first.shape[:2]
        texture = rhi.newTextureArray(TextureFormat.RGBA8, len(maps), QSize(width, height))
        levels = [[np.ascontiguousarray(map_image).tobytes()] for map_image in maps]
    else:
        dds_info = first.m_info
        flags = QRhiTexture.Flag.MipMapped if dds_info.mips > 1 else QRhiTexture.Flag(0)
        if dds_info.srgb and dds_info.format in SRGB_BLOCKS:
            flags |= QRhiTexture.Flag.sRGB
        texture = rhi.newTextureArray(
            BLOCK_FORMATS[dds_info.format], len(maps), QSize(dds_info.width_px, dds_info.height_px), 1, flags
        )
        levels = [stored_levels(map_image) for map_image in maps]
    texture.create()
    entries = [QRhiTextureUploadEntry(layer, level, QRhiTextureSubresourceUploadDescription(raw))
               for layer, layer_levels in enumerate(levels) for level, raw in enumerate(layer_levels)]  # fmt: skip
    upload = QRhiTextureUploadDescription()
    upload.setEntries(entries)
    batch.uploadTexture(texture, upload)
    return texture


def map_kind(dds):
    """How a map goes into a texture array: as stored (BC blocks, one face) or as its first level decoded to RGBA,
    and the key of the maps an array of it can hold."""
    dds_info = dds.m_info
    stored = dds_info.format in BLOCK_FORMATS and dds_info.faces == 1
    key = (
        (dds_info.width_px, dds_info.height_px, dds_info.mips, dds_info.format, dds_info.srgb)
        if stored
        else (dds_info.width_px, dds_info.height_px)
    )
    return stored, key


def stored_levels(dds) -> list[bytes]:
    """A flat map's levels as stored, and those its file stops short of down to 1 x 1 (``mipchain``: a mipmapped
    QRhi texture has every level, and one never written reads what the memory held, coloured speckles)."""
    levels = [dds.stored(level) for level in range(dds.m_info.mips)]
    return mipchain.full_levels(dds.m_info, levels) if dds.m_info.mips > 1 else levels
