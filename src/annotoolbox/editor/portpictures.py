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
    for m in regiontables.set_materials(set_text):
        rgb = colour(m.file)
        out[m.id] = tuple(v / 255.0 for v in rgb) if rgb else GREY
    return out


def colour_image(ids, alphas, order: dict[int, int], colours: dict[int, tuple], size: int) -> np.ndarray:
    """The ground's colour per alpha texel ([N, N, 3] floats), each material's colour weighted by how much of it the
    game shows there (``groundedit.layered_weights``), worked out in squares of ``WINDOW`` tiles."""
    per = alphas.shape[0] // size
    block = alphas.shape[0] // ids.shape[0]
    win = math.gcd(WINDOW, size)
    out = np.zeros((alphas.shape[0], alphas.shape[1], 3), np.float32)
    for z0 in range(0, size, win):
        for x0 in range(0, size, win):
            a = alphas[z0 * per : (z0 + win) * per, x0 * per : (x0 + win) * per]
            i = ids[z0 * per // block : (z0 + win) * per // block, x0 * per // block : (x0 + win) * per // block]
            mats, w = groundedit.layered_weights(i, a, order)
            rgb = np.asarray([colours.get(m, GREY) for m in mats], np.float32)
            out[z0 * per : (z0 + win) * per, x0 * per : (x0 + win) * per] = np.tensordot(w, rgb, axes=(0, 0))
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
    isl, gd: groundedit.Gamedata, set_text: str, colour: Callable[[str], tuple | None], keep: bool = False
) -> dict[str, bytes]:
    """The ported island's pictures (``mappictures.PICTURES`` that ``gd`` has) as PNG files and the BC7 ``_0.dds``
    beside each; ``keep``: the island's own pictures were drawn for this window, so they keep their sizes."""
    names = [n for n in mappictures.PICTURES if n in gd.names]
    if not names:
        return {}
    window = islandinfo.read(isl.info)["active_rect"] or (0, 0, isl.size, isl.size)
    ids, alphas = material_maps(isl, gd)
    colours = colour_image(ids, alphas, set_ranks(set_text), material_colours(set_text, colour), isl.size)
    heights = groundedit.coarse_heights(isl)
    pngs = mappictures.paint(heights, colours, window, isl.size, shapes(names, gd.read, window, keep))
    return {**pngs, **mappictures.to_dds(pngs)}


def tint_texels(node) -> np.ndarray:
    """A far tint picture (``BaseTextureData``: ``width`` blocks of 4 texels, BC1 in the tint pages' YCC bytes)."""
    w, h = node.find("width").u32(), node.find("height").u32()
    blocks = np.frombuffer(node.find("map").data, np.uint8).reshape(-1, 8)
    return bc1encode.from_blocks(bc1encode.decode_blocks(blocks), h * 4, w * 4)


def _scaled(img: np.ndarray, n: int) -> np.ndarray:
    """``img`` (square) on ``n`` texels a side: block averages down, repeated texels up (the sides are powers of two
    times each other)."""
    have = img.shape[0]
    if have > n:
        f = have // n
        return img.reshape(n, f, n, f, 3).mean(axis=(1, 3))
    return np.repeat(np.repeat(img, n // have, axis=0), n // have, axis=1).astype(np.float64)


def far_tint(source_rd, donor_rd, place) -> bool:
    """The donor's far tint picture (``Terrain/TintMapLevels/BaseTextureData``, which the game draws the island's
    ground tint from at a distance) made from the source's, at the donor's resolution (117 keeps a texel a tile, 1800
    four); in a larger donor the source's window of it on the source's middle tone. False without one on both."""
    src = source_rd.path("Terrain/TintMapLevels/BaseTextureData")
    dst = donor_rd.path("Terrain/TintMapLevels/BaseTextureData")
    if src is None or dst is None:
        return False
    img = tint_texels(src)
    n = dst.find("width").u32() * 4
    per = n / place.grid
    wide = np.empty((n, n, 3))
    wide[:] = np.median(img.reshape(-1, 3), axis=0)
    wide[place.window(per)] = _scaled(img, round(place.size * per))
    dst.find("map").data = bc1encode.encode_blocks(bc1encode.to_blocks(np.rint(wide).astype(np.uint8))).tobytes()
    return True
