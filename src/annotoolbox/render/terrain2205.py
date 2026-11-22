"""Anno 2205's terrain drawn by its own terrain shader (``terrain.fx``, ``TerrainDefault``) on Direct3D 11: 1800's
forward node set (``terrain1800``) with 2205's node constants - every node a mirrored patch (the view's axes are
the session's turned over in x), no island matrix - each node's own baked textures (2205 bakes a texture per node),
an unedited node from the session's stored levels (``Terrain/Levels``) with the session's own normal page that holds
it, the rest from the view's heights with a world-normal page of them. ``CTerrain2205(ctx, program, state, size,
step, width)`` is the node set; ``slot_textures(layer)`` a slice's textures to bake into; ``wanted`` / ``add_page``
the session's pages it asks for (its owner reads them: ``session_page``); docs/rendering/island-1800-2205-view.md 3.
"""

from __future__ import annotations

from collections import OrderedDict

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from . import terraindraw, terrainpages
from .fxcdraw import mirrored_x
from .terrain1800 import RENDER_PARAMS, CTerrain1800
from .terrainnodes import SNode

TextureFormat = QRhiTexture.Format
OWN = ("cBakedDiffTex", "cBakedNormalXTex", "cBakedNormalYTex")  # what a node's bake writes, a texture each
# the same bytes as 117's and 1800's vertex, read as POSITION, COLOR0 and COLOR1 (2205's ``VSInputTerrain``)
ELEMENTS = ((0, 2, 4, 0), (5, 5, 1, 8), (5, 5, 1, 12))
FLAT = {"cBakedSpecTex": 0, "cBakedHeightTex": 128}  # what no bake writes yet: gloss 0, the height's middle
FIRST_LEVEL = 2  # the stored levels' first: 4 tiles a node, ours 2^2 (anno2205-map-format "Terrain")
PAGES_KEPT = 192  # normal pages kept on the device, the longest unused let go first [ours]


def island_values(size_tiles: float) -> dict:
    """2205's ``cbTerrainRenderParams`` and the frame's terrain size."""
    return {"cTrnRenderParams": RENDER_PARAMS, "cTrnUnitScale": terraindraw.UNIT,
            "cTerrainSize": [size_tiles, size_tiles, 1 / size_tiles, 1 / size_tiles]}  # fmt: skip


def page_params(size_tiles: float, page: tuple | None) -> list[float]:
    """``cTrnGlobalNormalParams`` (offset x, z, scale, on): ``psTerrain`` reads ``positionWS.xz / w * scale -
    offset`` with ``w`` (``node_width``) the node's ``cTrnBakeChunkPos.w`` (size and a nugget). ``page`` (level, x, z):
    the session's page of ``16 << level`` tiles, flipped into the view's axes (``session_page``); None: the view's
    page of the whole map."""
    node_width = size_tiles + terraindraw.UNIT
    if page is None:
        return [0.0, 0.0, node_width / size_tiles, 1.0]
    level, px, pz = page
    tiles = terrainpages.page_tiles(level)
    return [size_tiles / tiles - px - 1, float(pz), node_width / tiles, 1.0]


def node_values(node: SNode, size_tiles: float, page: tuple | None = None) -> dict:
    """2205's ``cbTerrainNode`` for ``node`` (the session's tiles): a mirrored patch, ``x -> size_tiles - x``
    (``vsTerrain``: the real size is ``cTrnBakeChunkPos.w`` less a nugget), its corner in the view's axes and its
    level, fully faded in, its normal page (``page_params``)."""
    return {"cTrnBakeChunkPos": [size_tiles - node.x - node.tiles, node.z, float(node.level), size_tiles + terraindraw.UNIT],
            "cTrnBakeChunkOffstFLip": [0.0, 0.0, -1.0, 1.0], "cTrnFading": [1.0, 1.0],
            "cTrnGlobalNormalParams": page_params(size_tiles, page)}  # fmt: skip


def view_page(xzy: np.ndarray) -> np.ndarray:
    """1800's page of the heights (``terrain1800.normal_page_xzy``: x, z, y in the island's axes) as the world normal
    2205's ``psTerrain`` reads from ``cGlobalNormalMap`` (``xyz * 2 - 1``) in the view's axes: the columns turned over,
    x negated."""
    out = np.ascontiguousarray(xzy[:, ::-1][..., [0, 2, 1, 3]])
    out[..., 0] = 255 - out[..., 0]
    return out


def negated_red(raw: bytes) -> bytes:
    """BC1 blocks with each colour's red turned over (``31 - r`` of the 5 bits, ``255 - r`` decoded): where that
    turns the end points' order (which decides the block's mode) they swap, and the indices with them (0 and 1; in
    four-colour mode 2 and 3 too). A three-colour block's black stays black (a normal page has none)."""
    texels = np.frombuffer(raw, "<u2").reshape(-1, 4).copy()
    c0, c1 = texels[:, 0], texels[:, 1]
    n0, n1 = c0 ^ 0xF800, c1 ^ 0xF800
    swap = (c0 > c1) != (n0 > n1)
    texels[:, 0], texels[:, 1] = np.where(swap, n1, n0), np.where(swap, n0, n1)
    index = texels[:, 2].astype(np.uint32) | texels[:, 3].astype(np.uint32) << 16
    low = np.uint32(0x55555555)
    flip = np.where(c0 > c1, low, ~(index >> 1) & low)  # four colours: every code ^ 1; three: codes 0 and 1 alone
    index = np.where(swap, index ^ flip, index)
    texels[:, 2], texels[:, 3] = (index & 0xFFFF).astype(np.uint16), (index >> 16).astype(np.uint16)
    return texels.tobytes()


def session_page(dds: bytes) -> tuple[int, bytes]:
    """A session normal page's top level as the view samples it: (its side, its BC1 blocks flipped left to right with
    the normal's x negated: the view's x is the session's turned over); in the background. The pages are 512, 256 or
    128 texels a side."""
    from ..models import ddsdecode

    page = ddsdecode.CDdsFile(dds)
    side = page.m_info.width_px
    if page.m_info.format != "DXT1" or page.m_info.height_px != side or side % 4:
        raise ValueError(f"a normal page of {page.m_info.format} {page.m_info.width_px} x {page.m_info.height_px}")
    return side, negated_red(mirrored_x(page.stored(0), "DXT1", side, side))


def stored_levels(rdm_tag) -> dict[int, dict[tuple[int, int], tuple[bytes, np.ndarray]]]:
    """The session's stored meshes (rd3d ``Terrain/Levels``: per level its nodes row by row, ``4 << k`` tiles each) by
    our node level, then node (column, row): the vertices as stored and the triangle indices; a node without vertices
    is left out (the levels past 128 tiles hold none). In the background."""
    from ..formats import filedb

    out: dict = {}
    levels = [tag for tag in rdm_tag.path("Terrain/Levels").children if isinstance(tag, filedb.STag)]
    for k, level in enumerate(levels):
        across, nodes = level.find("LevelSize"), level.find("Nodes")
        if across is None or nodes is None:
            continue
        node_count, meshes = across.u32(), {}
        for i, node in enumerate(tag for tag in nodes.children if isinstance(tag, filedb.STag)):
            vertices, ix = node.find("Vertices"), node.find("Indices")
            if vertices is not None and vertices.data and ix is not None and ix.data:
                meshes[(i % node_count, i // node_count)] = (
                    bytes(vertices.data),
                    np.frombuffer(ix.data, "<u2").astype(np.uint32),
                )
        if meshes:
            out[k + FIRST_LEVEL] = meshes
    return out


def tint_map(rdm_tag) -> tuple[int, bytes] | None:
    """The session's tint map (rd3d ``Terrain/Tints``: ``TintMapWidth`` texels a side as BC1 blocks row by row, in
    the session's axes, the luminance in green and the chroma squared about the middle: ``TINT_YCC_COMPRESSED``,
    ``TINT_POW2``): (its side, the blocks); None where the session has none (anno2205-map-format "Terrain")."""
    terrain = rdm_tag.path("Terrain")
    tints, side = (terrain.find("Tints"), terrain.find("TintMapWidth")) if terrain is not None else (None, None)
    if tints is None or side is None:
        return None
    side_count = side.u32()
    raw = bytes(tints.data)
    return (
        (side_count, raw) if side_count and side_count % 4 == 0 and len(raw) == side_count * side_count // 2 else None
    )


class CTerrain2205(CTerrain1800):
    """The session's nodes on the device (module docstring)."""

    PAGE = "cGlobalNormalMap"
    ELEMENTS = ELEMENTS
    CASCADED = False

    def __init__(self, ctx, program, state, size_tiles: float, step_tiles: float, width_samples: int) -> None:
        super().__init__(ctx, program, state, size_tiles, step_tiles, width_samples)
        self.m_island = island_values(size_tiles)
        self.m_node_maps: dict[int, dict] = {}  # a slice -> its node's baked textures by name
        self.m_levels = 1  # their mip levels (the game's bake makes every level: ``use_levels``)
        self.m_normal_pages: dict[tuple, str] = {}  # the session's normal pages: (level, x, z) -> path (the owner's)
        self.m_ready: OrderedDict[tuple, object] = OrderedDict()  # those on the device, the latest used last
        self.m_wanted: dict[tuple, None] = {}  # those this frame's nodes asked for (nearest first), not on the device
        self.m_failed: set[tuple] = set()  # those that did not read (the node keeps the view's page)

    def nodes(self, eye, visible=None) -> list:
        """117's nodes; ``wanted`` holds only what this frame's nodes ask for (a page of a node no longer shown
        was read all the same and kept the session's pages busy)."""
        self.m_wanted.clear()
        return super().nodes(eye, visible)

    def _kind(self, node: SNode) -> tuple:
        """1800's choice (a stored node where nothing was edited), a stored node with the session's page that holds
        it once that is on the device (asked for until then)."""
        how, _page, _ = super()._kind(node)
        if how != "stored":
            return (how, None, 0)
        key = terrainpages.finest_page(self.m_normal_pages, node.level, node.x, node.z)
        if key is None or key in self.m_failed:
            return (how, None, 0)
        if key not in self.m_ready:
            self.m_wanted.setdefault(key)
            return (how, None, 0)
        self.m_ready.move_to_end(key)
        return (how, key, self.m_ready[key])

    def _values(self, node: SNode, layer: int, kind: tuple, tint) -> dict:
        return {**self.m_island, **node_values(node, self.m_size_tiles, kind[1])}

    def _own(self, node: SNode, layer: int, kind: tuple) -> dict:
        own = dict(self.slot_textures(layer))
        if kind[1] is not None:
            own[self.PAGE] = self.m_ready[kind[1]]
        return own

    def add_page(self, key: tuple, page: tuple[int, bytes] | None) -> None:
        """A normal page read (``session_page``'s side and bytes; None: it did not read): on the device with the next
        uploads, the longest unused page no shown node draws with let go when too many are kept."""
        self.m_wanted.pop(key, None)
        if page is None:
            self.m_failed.add(key)
            return
        side, raw = page
        texture = self.m_rhi.newTexture(TextureFormat.BC1, QSize(side, side), 1, QRhiTexture.Flag(0))
        texture.create()
        self._m_tint_uploads.append((texture, [raw]))  # 117's queue of block-compressed pages to upload
        self.m_ready[key] = texture
        used = {model.m_kind[1] for model in self.m_models.values()}
        for old in [k for k in self.m_ready if k not in used][: max(0, len(self.m_ready) - PAGES_KEPT)]:
            self.m_ctx.later(self.m_ready.pop(old))

    def slot_textures(self, layer: int) -> dict:
        """The baked textures of slice ``layer`` (made on first use, RGBA8 of ``SLICE_SIZE``, ``levels`` mips)."""
        if layer not in self.m_node_maps:
            slice_size, made = terraindraw.SLICE_SIZE, {}
            flags = QRhiTexture.Flag.MipMapped if self.m_levels > 1 else QRhiTexture.Flag(0)
            for name in OWN:
                made[name] = self.m_rhi.newTexture(TextureFormat.RGBA8, QSize(slice_size, slice_size), 1, flags)
                made[name].create()
            self.m_node_maps[layer] = made
        return self.m_node_maps[layer]

    def use_levels(self, count: int) -> None:
        """The baked textures with ``count`` mip levels from now on (the ones made so far let go: every node is
        baked again)."""
        if count == self.m_levels:
            return
        for made in self.m_node_maps.values():
            for texture in made.values():
                self.m_ctx.later(texture)
        self.m_node_maps, self.m_levels = {}, count
        self.remake_nodes()  # they bind the textures let go

    def page_region(self) -> tuple[np.ndarray, tuple[int, int]] | None:
        """1800's page region in the view's axes (``view_page``): its columns from the other side."""
        texels, (x, y) = super().page_region()
        return view_page(texels), (self._m_page.m_value.shape[1] - x - texels.shape[1], y)

    def uploads(self, batch) -> None:
        """1800's uploads, and the 2D stand-ins of what no bake writes (2205 reads a texture per node)."""
        first = not self.m_keep
        super().uploads(batch)
        if first:
            for name, gray in FLAT.items():
                texture = terraindraw.upload(
                    self.m_rhi, batch, TextureFormat.RGBA8, (4, 4), [bytes([gray, gray, gray, 255]) * 16], False
                )
                self.m_keep.append(texture)
                self.m_textures[name] = texture

    def pages_on_device(self) -> dict:
        """The session's normal pages (a stored node's kind names one of them, not a height page)."""
        return self.m_ready

    def release(self) -> None:
        for made in self.m_node_maps.values():
            for texture in made.values():
                self.m_ctx.later(texture)
        for texture in self.m_ready.values():
            self.m_ctx.later(texture)
        self.m_node_maps, self.m_ready = {}, OrderedDict()
        self.m_wanted.clear()
        super().release()
