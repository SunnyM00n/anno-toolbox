"""One height field, pages generated last: the single place an island's terrain data is written from its final
heights. Shaping steps (the island build's rules, the editor's brushes) change only the heights and their protected
masks; ``build_terrain`` then writes, in this order, the game's and the coarse heightmap, the sea maps, the node
bounds, the stored LOD meshes and the height pages, and nothing edits them afterwards. ``encode_page`` is the one page
encoder: the built islands' pages and the editor's re-encoded pages both come from it (island-build "The build",
island-editor "Ground edits of vanilla islands")."""

from __future__ import annotations

import struct
import zlib

from ..formats import filedb
from . import islandfog, islandsurf
from .islandgrid import HEIGHT_UNITS

PAGE_BORDER = 1  # the Downsampled level's texels beyond the page on each side


def build_terrain(island, gamedata_files: dict[str, bytes], levels: bool = True, pages: bool = True) -> dict:
    """Write ``island``'s terrain from its final heights (``island``: a builder with the terrain writers,
    ``islandterrain.CTerrainMixin`` and ``islandwater.CWaterMixin``): heightmaps, sea maps, and with ``levels`` the node
    bounds and the stored LOD meshes, with ``pages`` the height pages. Returns the new ``_gamedata`` files."""
    island.apply_heights()
    island.apply_water_maps()
    islandsurf.apply(island)  # the coast-wave byte from 2070's surf lines (the sea map's byte 1)
    islandfog.apply(island)  # 2070's fog banks, kept as records (117's placed bank is not known yet)
    out: dict[str, bytes] = {}
    if levels:
        island.apply_levels()
        out.update(island.generate_lod_meshes(gamedata_files))
    if pages:
        out.update(island.generate_height_pages(gamedata_files))
    return out


def encode_page(doc, ground, min_x: float, min_z: float, tiles: float) -> None:
    """Fill a height page (``doc``: its FileDB, ``HeightMap/Downsampled`` and ``Deltas``) so the game's rebuild lands
    on ``ground(xs, zs)`` (heightmap units, tile coordinates, outer product with rows z) over the page at
    (``min_x``, ``min_z``), ``tiles`` wide: the 66x66 samples, then each refinement level coarsest first
    (``islandterrain.page_delta``)."""
    import numpy as np

    from .islandterrain import page_delta

    downsampled = doc.path("HeightMap/Downsampled")
    sample_count = downsampled.find("width").u32()
    index = (np.arange(sample_count) - PAGE_BORDER + 0.5) * tiles / (sample_count - 2 * PAGE_BORDER)
    down = np.clip(np.rint(ground(min_x + index, min_z + index) * 64) + 32768, 0, 65535).astype("<u2")
    downsampled.find("map").data = down.tobytes()
    rebuilt = (down.astype(np.float64) - 32768) / 256  # world units, as the game reads it
    for delta in sorted(
        doc.path("HeightMap/Deltas").findall("#1"), key=lambda candidate: candidate.path("Data").find("width").u32()
    ):
        rebuilt = page_delta(delta, rebuilt, lambda position: ground(min_x + position, min_z + position), tiles)


def write_page(data: bytes, ground, min_x: float, min_z: float, tiles: float, template: bytes | None = None) -> bytes:
    """A page file (``_CTT``, size, zlib FileDB) encoded from ``ground`` (``encode_page``) on the layout of the page
    ``template`` (default: ``data`` itself)."""
    base = template if template is not None else data
    raw = zlib.decompress(base[8:])
    doc, ids = filedb.read(raw), filedb.name_ids(raw)
    encode_page(doc, ground, min_x, min_z, tiles)
    body = filedb.write(doc, *ids, version=filedb.version(raw))
    return base[:4] + struct.pack("<I", len(body)) + zlib.compress(body, 1)


def page_field(data: bytes, lift, min_x: float, min_z: float, tiles: float):
    """The ground of an existing page (``data``) plus ``lift(xs, zs)`` (world units, outer product) as ``ground`` for
    ``encode_page``: the page's own surface - the coarse map with its detail, the residual - with the edit of the
    coarse map added. The encoder asks for each level's texel centres in turn; each is answered with the page's own
    rebuilt level there, so a page re-encoded where ``lift`` is zero (or a whole number of height codes) keeps its
    codes, and elsewhere lands within one height code of its old surface plus the edit."""
    import numpy as np

    from .gameterrain import rebuild_levels

    levels = rebuild_levels(data)

    def ground(xs, zs):
        xs = np.asarray(xs, float)
        old = levels[len(xs)]  # the encoder samples exactly one level's texel centres per call
        return (old + lift(xs, zs)) * HEIGHT_UNITS

    return ground
