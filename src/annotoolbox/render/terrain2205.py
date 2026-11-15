"""Anno 2205's terrain drawn by its own terrain shader (``terrain.fx``, ``TerrainDefault``) on Direct3D 11: 1800's
forward node set (``terrain1800``) with 2205's node constants - every node a mirrored patch (the view's axes are
the session's turned over in x), no island matrix - each node's own baked textures (2205 bakes a texture per node),
an unedited node from the session's stored levels (``Terrain/Levels``) with the session's own normal page that holds
it, the rest from the view's heights with a world-normal page of them. ``Terrain2205(ctx, program, state, size,
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
from .terrain1800 import RENDER_PARAMS, Terrain1800
from .terrainnodes import Node

F = QRhiTexture.Format
OWN = ("cBakedDiffTex", "cBakedNormalXTex", "cBakedNormalYTex")  # what a node's bake writes, a texture each
# the same bytes as 117's and 1800's vertex, read as POSITION, COLOR0 and COLOR1 (2205's ``VSInputTerrain``)
ELEMENTS = ((0, 2, 4, 0), (5, 5, 1, 8), (5, 5, 1, 12))
FLAT = {"cBakedSpecTex": 0, "cBakedHeightTex": 128}  # what no bake writes yet: gloss 0, the height's middle
FIRST_LEVEL = 2  # the stored levels' first: 4 tiles a node, ours 2^2 (anno2205-map-format "Terrain")
PAGES_KEPT = 192  # normal pages kept on the device, the longest unused let go first [ours]


def island_values(size: float) -> dict:
    """2205's ``cbTerrainRenderParams`` and the frame's terrain size."""
    return {"cTrnRenderParams": RENDER_PARAMS, "cTrnUnitScale": terraindraw.UNIT,
            "cTerrainSize": [size, size, 1 / size, 1 / size]}  # fmt: skip


def page_params(size: float, page: tuple | None) -> list[float]:
    """``cTrnGlobalNormalParams`` (offset x, z, scale, on): ``psTerrain`` reads ``positionWS.xz / w * scale -
    offset`` with ``w`` the node's ``cTrnBakeChunkPos.w`` (size and a nugget). ``page`` (level, x, z): the session's
    page of ``16 << level`` tiles, flipped into the view's axes (``session_page``); None: the view's page of the whole
    map."""
    w = size + terraindraw.UNIT
    if page is None:
        return [0.0, 0.0, w / size, 1.0]
    level, px, pz = page
    tiles = terrainpages.page_tiles(level)
    return [size / tiles - px - 1, float(pz), w / tiles, 1.0]


def node_values(node: Node, size: float, page: tuple | None = None) -> dict:
    """2205's ``cbTerrainNode`` for ``node`` (the session's tiles): a mirrored patch, ``x -> size - x``
    (``vsTerrain``: the real size is ``cTrnBakeChunkPos.w`` less a nugget), its corner in the view's axes and its
    level, fully faded in, its normal page (``page_params``)."""
    return {"cTrnBakeChunkPos": [size - node.x - node.tiles, node.z, float(node.level), size + terraindraw.UNIT],
            "cTrnBakeChunkOffstFLip": [0.0, 0.0, -1.0, 1.0], "cTrnFading": [1.0, 1.0],
            "cTrnGlobalNormalParams": page_params(size, page)}  # fmt: skip


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
    b = np.frombuffer(raw, "<u2").reshape(-1, 4).copy()
    c0, c1 = b[:, 0], b[:, 1]
    n0, n1 = c0 ^ 0xF800, c1 ^ 0xF800
    swap = (c0 > c1) != (n0 > n1)
    b[:, 0], b[:, 1] = np.where(swap, n1, n0), np.where(swap, n0, n1)
    idx = b[:, 2].astype(np.uint32) | b[:, 3].astype(np.uint32) << 16
    low = np.uint32(0x55555555)
    flip = np.where(c0 > c1, low, ~(idx >> 1) & low)  # four colours: every code ^ 1; three: codes 0 and 1 alone
    idx = np.where(swap, idx ^ flip, idx)
    b[:, 2], b[:, 3] = (idx & 0xFFFF).astype(np.uint16), (idx >> 16).astype(np.uint16)
    return b.tobytes()


def session_page(dds: bytes) -> tuple[int, bytes]:
    """A session normal page's top level as the view samples it: (its side, its BC1 blocks flipped left to right with
    the normal's x negated: the view's x is the session's turned over); in the background. The pages are 512, 256 or
    128 texels a side."""
    from ..models import ddsdecode

    page = ddsdecode.DdsFile(dds)
    side = page.info.width
    if page.info.format != "DXT1" or page.info.height != side or side % 4:
        raise ValueError(f"a normal page of {page.info.format} {page.info.width} x {page.info.height}")
    return side, negated_red(mirrored_x(page.stored(0), "DXT1", side, side))


def stored_levels(rd) -> dict[int, dict[tuple[int, int], tuple[bytes, np.ndarray]]]:
    """The session's stored meshes (rd3d ``Terrain/Levels``: per level its nodes row by row, ``4 << k`` tiles each) by
    our node level, then node (column, row): the vertices as stored and the triangle indices; a node without vertices
    is left out (the levels past 128 tiles hold none). In the background."""
    from ..formats import filedb

    out: dict = {}
    levels = [t for t in rd.path("Terrain/Levels").children if isinstance(t, filedb.Tag)]
    for k, level in enumerate(levels):
        across, nodes = level.find("LevelSize"), level.find("Nodes")
        if across is None or nodes is None:
            continue
        n, meshes = across.u32(), {}
        for i, node in enumerate(t for t in nodes.children if isinstance(t, filedb.Tag)):
            v, ix = node.find("Vertices"), node.find("Indices")
            if v is not None and v.data and ix is not None and ix.data:
                meshes[(i % n, i // n)] = (bytes(v.data), np.frombuffer(ix.data, "<u2").astype(np.uint32))
        if meshes:
            out[k + FIRST_LEVEL] = meshes
    return out


def tint_map(rd) -> tuple[int, bytes] | None:
    """The session's tint map (rd3d ``Terrain/Tints``: ``TintMapWidth`` texels a side as BC1 blocks row by row, in
    the session's axes, the luminance in green and the chroma squared about the middle: ``TINT_YCC_COMPRESSED``,
    ``TINT_POW2``): (its side, the blocks); None where the session has none (anno2205-map-format "Terrain")."""
    t = rd.path("Terrain")
    tints, side = (t.find("Tints"), t.find("TintMapWidth")) if t is not None else (None, None)
    if tints is None or side is None:
        return None
    n = side.u32()
    raw = bytes(tints.data)
    return (n, raw) if n and n % 4 == 0 and len(raw) == n * n // 2 else None


class Terrain2205(Terrain1800):
    """The session's nodes on the device (module docstring)."""

    PAGE = "cGlobalNormalMap"
    ELEMENTS = ELEMENTS
    CASCADED = False

    def __init__(self, ctx, program, state, size: float, step: float, width: int) -> None:
        super().__init__(ctx, program, state, size, step, width)
        self.island = island_values(size)
        self.node_maps: dict[int, dict] = {}  # a slice -> its node's baked textures by name
        self.levels = 1  # their mip levels (the game's bake makes every level: ``use_levels``)
        self.normal_pages: dict[tuple, str] = {}  # the session's normal pages: (level, x, z) -> path (the owner's)
        self.ready: OrderedDict[tuple, object] = OrderedDict()  # those on the device, the latest used last
        self.wanted: dict[tuple, None] = {}  # those this frame's nodes asked for (nearest first), not on the device
        self.failed: set[tuple] = set()  # those that did not read (the node keeps the view's page)

    def nodes(self, eye, visible=None) -> list:
        """117's nodes; ``wanted`` holds only what this frame's nodes ask for (a page of a node no longer shown
        was read all the same and kept the session's pages busy)."""
        self.wanted.clear()
        return super().nodes(eye, visible)

    def _kind(self, node: Node) -> tuple:
        """1800's choice (a stored node where nothing was edited), a stored node with the session's page that holds
        it once that is on the device (asked for until then)."""
        how, _page, _ = super()._kind(node)
        if how != "stored":
            return (how, None, 0)
        key = terrainpages.finest_page(self.normal_pages, node.level, node.x, node.z)
        if key is None or key in self.failed:
            return (how, None, 0)
        if key not in self.ready:
            self.wanted.setdefault(key)
            return (how, None, 0)
        self.ready.move_to_end(key)
        return (how, key, id(self.ready[key]))

    def _values(self, node: Node, layer: int, kind: tuple, tint) -> dict:
        return {**self.island, **node_values(node, self.size, kind[1])}

    def _own(self, node: Node, layer: int, kind: tuple) -> dict:
        own = dict(self.slot_textures(layer))
        if kind[1] is not None:
            own[self.PAGE] = self.ready[kind[1]]
        return own

    def add_page(self, key: tuple, page: tuple[int, bytes] | None) -> None:
        """A normal page read (``session_page``'s side and bytes; None: it did not read): on the device with the next
        uploads, the longest unused page no shown node draws with let go when too many are kept."""
        self.wanted.pop(key, None)
        if page is None:
            self.failed.add(key)
            return
        side, raw = page
        tex = self.rhi.newTexture(F.BC1, QSize(side, side), 1, QRhiTexture.Flag(0))
        tex.create()
        self._tint_uploads.append((tex, [raw]))  # 117's queue of block-compressed pages to upload
        self.ready[key] = tex
        used = {m.kind[1] for m in self.models.values()}
        for old in [k for k in self.ready if k not in used][: max(0, len(self.ready) - PAGES_KEPT)]:
            self.ctx.later(self.ready.pop(old))

    def slot_textures(self, layer: int) -> dict:
        """The baked textures of slice ``layer`` (made on first use, RGBA8 of ``SLICE_SIZE``, ``levels`` mips)."""
        if layer not in self.node_maps:
            n, made = terraindraw.SLICE_SIZE, {}
            flags = QRhiTexture.Flag.MipMapped if self.levels > 1 else QRhiTexture.Flag(0)
            for name in OWN:
                made[name] = self.rhi.newTexture(F.RGBA8, QSize(n, n), 1, flags)
                made[name].create()
            self.node_maps[layer] = made
        return self.node_maps[layer]

    def use_levels(self, count: int) -> None:
        """The baked textures with ``count`` mip levels from now on (the ones made so far let go: every node is
        baked again)."""
        if count == self.levels:
            return
        for made in self.node_maps.values():
            for tex in made.values():
                self.ctx.later(tex)
        self.node_maps, self.levels = {}, count
        self.remake_nodes()  # they bind the textures let go

    def page_region(self) -> tuple[np.ndarray, tuple[int, int]] | None:
        """1800's page region in the view's axes (``view_page``): its columns from the other side."""
        texels, (x, y) = super().page_region()
        return view_page(texels), (self._page.value.shape[1] - x - texels.shape[1], y)

    def uploads(self, batch) -> None:
        """1800's uploads, and the 2D stand-ins of what no bake writes (2205 reads a texture per node)."""
        first = not self.keep
        super().uploads(batch)
        if first:
            for name, gray in FLAT.items():
                tex = terraindraw.upload(self.rhi, batch, F.RGBA8, (4, 4), [bytes([gray, gray, gray, 255]) * 16], False)
                self.keep.append(tex)
                self.textures[name] = tex

    def pages_on_device(self) -> dict:
        """The session's normal pages (a stored node's kind names one of them, not a height page)."""
        return self.ready

    def release(self) -> None:
        for made in self.node_maps.values():
            for tex in made.values():
                self.ctx.later(tex)
        for tex in self.ready.values():
            self.ctx.later(tex)
        self.node_maps, self.ready = {}, OrderedDict()
        self.wanted.clear()
        super().release()
