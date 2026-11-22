"""The map pictures of a ported island, drawn from the island as it now is: its heights and the ground colours of the
target region's materials, in the pictures' shapes (a port changes the ground, so the source's colours are wrong).

``redraw(isl, gd, set_text, colour)`` is the entry (``isl`` the ported island, ``gd`` its final ``_gamedata``,
``colour(material file)`` a material's mean colour); it returns the pictures as PNG and BC7 files; one the island
has not is not made. docs/island-regions.md "The pictures after a port".
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable

import numpy as np

from ..islands import islandinfo, mappictures, regiontables
from ..islands.materialorder import set_ranks
from ..models import bc1encode
from . import groundedit
from .regionconvert import material_maps

GREY = (0.5, 0.5, 0.5)  # a material whose colour is not known
WINDOW = 32  # tiles: the colours are worked out in squares of this side (memory)


def material_colours(set_text: str, colour: Callable[[str], tuple | None]) -> dict[int, tuple[float, float, float]]:
    """Material ID -> colour (0..1) of a material set's entries through ``colour(material file)``."""
    out = {}
    for material in regiontables.set_materials(set_text):
        rgb = colour(material.file)
        out[material.id] = tuple(channel / 255.0 for channel in rgb) if rgb else GREY
    return out


def colour_image(ids, alphas, order: dict[int, int], colours: dict[int, tuple], size_tiles: int) -> np.ndarray:
    """The ground's colour per alpha texel ([N, N, 3] floats), each material's colour weighted by how much of it the
    game shows there (``groundedit.layered_weights``), worked out in squares of ``WINDOW`` tiles."""
    per = alphas.shape[0] // size_tiles
    block = alphas.shape[0] // ids.shape[0]
    window_size = math.gcd(WINDOW, size_tiles)
    out = np.zeros((alphas.shape[0], alphas.shape[1], 3), np.float32)
    for start_z in range(0, size_tiles, window_size):
        for start_x in range(0, size_tiles, window_size):
            alpha_block = alphas[
                start_z * per : (start_z + window_size) * per, start_x * per : (start_x + window_size) * per
            ]
            i = ids[
                start_z * per // block : (start_z + window_size) * per // block,
                start_x * per // block : (start_x + window_size) * per // block,
            ]
            materials, weights = groundedit.layered_weights(i, alpha_block, order)
            rgb = np.asarray([colours.get(material_id, GREY) for material_id in materials], np.float32)
            out[start_z * per : (start_z + window_size) * per, start_x * per : (start_x + window_size) * per] = (
                np.tensordot(weights, rgb, axes=(0, 0))
            )
    return out


def shapes(names: list[str], read: Callable[[str], bytes], window: tuple, keep: bool) -> dict[str, tuple]:
    """The pixel size of each picture: the one the island's own picture has (``keep``: the window did not change),
    else the island build's (``mappictures.picture_shape``)."""
    from PIL import Image

    out = {}
    for name in names:
        try:
            out[name] = Image.open(io.BytesIO(read(name))).size if keep else mappictures.picture_shape(name, window)
        except (KeyError, OSError, ValueError):
            out[name] = mappictures.picture_shape(name, window)
    return out


def redraw(
    island, gamedata: groundedit.SGamedata, set_text: str, colour: Callable[[str], tuple | None], keep: bool = False
) -> dict[str, bytes]:
    """The ported island's pictures (``mappictures.PICTURES`` that ``gd`` has) as PNG files and the BC7 ``_0.dds``
    beside each; ``keep``: the island's own pictures were drawn for this window, so they keep their sizes."""
    names = [name for name in mappictures.PICTURES if name in gamedata.names]
    if not names:
        return {}
    window = islandinfo.read(island.a7minfo)["active_rect"] or (0, 0, island.size, island.size)
    ids, alphas = material_maps(island, gamedata)
    colours = colour_image(ids, alphas, set_ranks(set_text), material_colours(set_text, colour), island.size)
    heights = groundedit.coarse_heights(island)
    pngs = mappictures.paint(heights, colours, window, island.size, shapes(names, gamedata.read, window, keep))
    return {**pngs, **mappictures.to_dds(pngs)}


def tint_texels(node) -> np.ndarray:
    """A far tint picture (``BaseTextureData``: ``width`` blocks of 4 texels, BC1 in the tint pages' YCC bytes)."""
    width, height = node.find("width").u32(), node.find("height").u32()
    blocks = np.frombuffer(node.find("map").data, np.uint8).reshape(-1, 8)
    return bc1encode.from_blocks(bc1encode.decode_blocks(blocks), height * 4, width * 4)


def _scaled(image: np.ndarray, side: int) -> np.ndarray:
    """``img`` (square) on ``n`` texels a side: block averages down, repeated texels up (the sides are powers of two
    times each other)."""
    have = image.shape[0]
    if have > side:
        factor = have // side
        return image.reshape(side, factor, side, factor, 3).mean(axis=(1, 3))
    return np.repeat(np.repeat(image, side // have, axis=0), side // have, axis=1).astype(np.float64)


def far_tint(source_rd, donor_rd, place) -> bool:
    """The donor's far tint picture (``Terrain/TintMapLevels/BaseTextureData``, which the game draws the island's
    ground tint from at a distance) made from the source's, at the donor's resolution (117 keeps a texel a tile, 1800
    four); in a larger donor the source's window of it on the source's middle tone. False without one on both."""
    source_tint = source_rd.path("Terrain/TintMapLevels/BaseTextureData")
    donor_tint = donor_rd.path("Terrain/TintMapLevels/BaseTextureData")
    if source_tint is None or donor_tint is None:
        return False
    texels = tint_texels(source_tint)
    side = donor_tint.find("width").u32() * 4
    per = side / place.grid
    wide = np.empty((side, side, 3))
    wide[:] = np.median(texels.reshape(-1, 3), axis=0)
    wide[place.window(per)] = _scaled(texels, round(place.size_tiles * per))
    donor_tint.find("map").data = bc1encode.encode_blocks(bc1encode.to_blocks(np.rint(wide).astype(np.uint8))).tobytes()
    return True
