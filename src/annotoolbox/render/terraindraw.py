"""Anno 117's terrain drawn by the game's own terrain shader (``terrain.fx``) into the engine's G-buffer: the island
in square nodes of the engine's 16-byte terrain vertices, each drawn one of three ways - near ones tessellated from
their height page (``TerrainDeferredDetailed``), far ones from the island's stored mesh, the rest (and the edited)
from the view's heights (``TerrainDeferred``) - with the node's constants, pages and the baked ground arrays.

``Terrain(rhi, programs, size, step, width)`` is the entry: ``set_heights`` marks the nodes a height change touches,
``nodes(eye)`` gives the models for ``gameframe.GameFrame.render``. Formats: docs/rendering/terrain-geometry.md 1, 2
and 5; what is ours: docs/rendering/island-117-view.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
)

from . import gamedraw, renderstates, terrainnodes, terrainpages
from . import gameshader as gs
from .gameinstances import Common, stamp_of
from .terrainnodes import Node

EFFECT = "data/shaders/fx/terrain.fx"
STAGES = {"vs": (EFFECT, "TerrainDeferred", "vsTerrain"), "ps": (EFFECT, "TerrainDeferred", "psTerrainDeferred")}
DETAILED = {k: (EFFECT, "TerrainDeferredDetailed", f) for k, f in (("vs", "vsTerrain"), ("hs", "hsTerrain"),
            ("ds", "dsTerrain"), ("ps", "psTerrainDeferred"))}  # fmt: skip
# the sun's shadow: the plain technique's vertex shader displaces by the baked height, the detailed one's domain shader
SHADOW = {"vs": (EFFECT, "TerrainShadow", "vsTerrainShadow"), "ps": (EFFECT, "TerrainShadow", "psTerrainShadow")}
SHADOW_DETAILED = {**{k: (EFFECT, "TerrainShadowDetailed", f) for k, f in (("vs", "vsTerrainShadow"),
                   ("hs", "hsTerrainShadow"), ("ds", "dsTerrainShadow"))}, "ps": SHADOW["ps"]}  # fmt: skip
DETAILED_LEVEL = 3  # nodes of up to 8 tiles are tessellated from their page (the engine's: 1 and 2 tiles) [ours]
# a patch's corners with u along z: the hull's clockwise triangles (``triangle_cw``) then face up under the island's
# mirrored matrix, as the stored meshes' do (island-1800-2205-view.md 8: in 117's order they faced down)
PATCH_CORNERS = [0, 3, 2, 1]
GRID_SKIRT = 1.0  # world units the skirt of a node drawn from the view's heights hangs (a page departs <= 1) [ours]
UNIT = 1 / 32  # a nugget: Terrain/UnitScale, cTrnUnitScale
STRIDE = 16  # NuggetsVertex: x, y, z, u as u16; normal and V's low byte; tangent and V's high byte
# the vertex as the shader's three inputs read it (COLOR0..2): u16 x 4, then two packed byte vectors (rdm.md types)
ELEMENTS = ((5, 2, 4, 0), (5, 5, 1, 8), (5, 5, 1, 12))
SLICES = 128  # nodes baked at once: the baked arrays' layers
SLICE_SIZE = 256  # a node's bake, texels a side, whatever its size (the engine's: 512) [ours]
NODE_BUFFER, NODE_SLOT = "cbTerrainNode", 256  # a node's own constants, one slot each: a dynamic offset's step
# the session's maps that read "none" as 0 (a white stand-in would lay grit and cliff over all the ground, wet it, burn
# it): the grit and cliff coverage, the wetness, the wildfire; the session fills them, an island view has none
ZERO = ("cTerrainGritTex", "cWetnessMap", "cWildfireMapRO")
F = QRhiTexture.Format


def _bytes(v: np.ndarray) -> np.ndarray:
    """A unit vector's components as the engine's unorm bytes (``* 2 - 1`` in the shader)."""
    return np.clip(np.rint((v * 0.5 + 0.5) * 255), 0, 255).astype(np.uint8)


def vertex_normals(heights: np.ndarray, step: float) -> np.ndarray:
    """Per height sample the surface normal in the island's own axes (x, y up, z), from central differences."""
    dz, dx = np.gradient(heights.astype(np.float64), step)
    n = np.stack([-dx, np.ones_like(dx), -dz], -1)
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def encode_vertices(heights: np.ndarray, step: float, node: Node, width: int, normals: np.ndarray) -> bytes:
    """A node's height samples as ``NuggetsVertex``: x and z in nuggets, y as ``(y - 32768) / 256`` world units, u and
    v over the node's square (v's 16 bits split over the normal's and the tangent's fourth byte), the normal (from
    ``normals``) and the tangent (along the island's -x) as unorm bytes, read back ``.bgra`` (terrain-geometry.md 1.1)."""
    col, row, cols, rows = node.samples(step, width)
    rs, cs = slice(row, row + rows), slice(col, col + cols)
    h = heights[rs, cs].astype(np.float64)
    j, i = np.mgrid[rs, cs].astype(np.float64)
    x, z = i * step, j * step
    u, v = (x - node.x) / node.tiles, (z - node.z) / node.tiles
    out = np.zeros(h.shape + (STRIDE,), np.uint8)
    pos = np.stack([np.rint(x / UNIT), np.rint(h * 256) + 32768, np.rint(z / UNIT), np.rint(u * 65535)], -1)
    out[..., 0:8] = np.clip(pos, 0, 65535).astype("<u2").view(np.uint8).reshape(h.shape + (8,))
    vcode = np.clip(np.rint(v * 65535), 0, 65535).astype(np.uint32)
    n = normals[rs, cs]
    nb = _bytes(n)
    # the tangent along -x on the surface, as the island's stored meshes hold it (island-117-view "Phase 3")
    t = np.stack([-np.ones_like(n[..., 0]), n[..., 0] / np.maximum(n[..., 1], 1e-6), np.zeros_like(n[..., 0])], -1)
    tb = _bytes(t / np.linalg.norm(t, axis=-1, keepdims=True))
    out[..., 8], out[..., 9], out[..., 10], out[..., 11] = nb[..., 2], nb[..., 1], nb[..., 0], vcode & 0xFF
    out[..., 12], out[..., 13], out[..., 14], out[..., 15] = tb[..., 2], tb[..., 1], tb[..., 0], vcode >> 8
    return out.tobytes()


def mesh_rect(vertices: bytes) -> tuple[float, float, float, float]:
    """(x0, z0, width, depth) in tiles of a stored mesh's vertices: its u and v run over this rectangle, not over its
    node's square (a node at the island's edge is trimmed; island-117-view.md "Round 12")."""
    p = np.frombuffer(vertices, np.uint8).reshape(-1, STRIDE)[:, 0:6].copy().view("<u2").astype(np.float64) * UNIT
    x0, z0 = float(p[:, 0].min()), float(p[:, 2].min())
    return x0, z0, max(float(p[:, 0].max()) - x0, UNIT), max(float(p[:, 2].max()) - z0, UNIT)


def patch_indices(cols: int, rows: int) -> np.ndarray:
    """A quad patch per cell of a ``cols`` x ``rows`` sample grid, its corners in the domain shader's order: (x0, z0),
    (x0, z1), (x1, z1), (x1, z0) (``MIX_QUAD_UV``)."""
    j, i = np.mgrid[0 : rows - 1, 0 : cols - 1]
    a = (j * cols + i).ravel()
    return np.stack([a, a + cols, a + cols + 1, a + 1], -1).astype(np.uint32).ravel()


def with_skirts(vertices: bytes, cols: int, rows: int, drop: float = 0.0) -> tuple[bytes, np.ndarray]:
    """A grid's vertices with a skirt around it: each edge vertex again with its normal's y turned over (the
    engine's skirt flag: ``PushSkirtVertex`` hangs a tessellated one below the page surface) and ``drop`` world units
    lower, a quad between each edge pair and its copies; the grid's quad patches first (terrain-geometry.md 2.2)."""
    v = np.frombuffer(vertices, np.uint8).reshape(rows * cols, STRIDE)
    grid = np.arange(rows * cols).reshape(rows, cols)
    edges = [grid[0], grid[:, -1], grid[-1, ::-1], grid[::-1, 0]]  # around, each edge corner to corner
    ring = np.concatenate([e[:-1] for e in edges] + [grid[:1, 0]])
    skirt = v[ring].copy()
    skirt[:, 9] = 255 - skirt[:, 9]  # -y: the skirt flag
    y = skirt[:, 2:4].copy().view("<u2").astype(np.int64) - round(drop * 256)
    skirt[:, 2:4] = np.clip(y, 0, 65535).astype("<u2").view(np.uint8)
    first = rows * cols + np.arange(len(ring))
    quads = np.stack([ring[:-1], first[:-1], first[1:], ring[1:]], -1).astype(np.uint32).ravel()
    return np.concatenate([v, skirt]).tobytes(), np.concatenate([patch_indices(cols, rows), quads])


def quad_triangles(quads: np.ndarray) -> np.ndarray:
    """Quads (four corners each, in ``patch_indices``' order) as two triangles each."""
    q = quads.reshape(-1, 4)
    return np.stack([q[:, 0], q[:, 1], q[:, 2], q[:, 0], q[:, 2], q[:, 3]], -1).astype(np.uint32).ravel()


def grid_indices(cols: int, rows: int) -> np.ndarray:
    """Two triangles per cell of a ``cols`` x ``rows`` sample grid, row by row."""
    j, i = np.mgrid[0 : rows - 1, 0 : cols - 1]
    a = (j * cols + i).ravel()
    quads = np.stack([a, a + cols, a + 1, a + 1, a + cols, a + cols + 1], -1)
    return quads.astype(np.uint32).ravel()


def normal_page(heights: np.ndarray, step: float) -> np.ndarray:
    """The island's normal page as the engine's ``csComputeNormalMap`` makes one from its heights: one texel per
    cell of the height grid (cell-centred), the normal's x and z as two unorm bytes [rows, cols, 2]; the shader takes
    y as ``sqrt(1 - x^2 - z^2)`` (terrain-geometry.md 5)."""
    h = heights.astype(np.float64)
    dx = ((h[:-1, 1:] - h[:-1, :-1]) + (h[1:, 1:] - h[1:, :-1])) / (2 * step)
    dz = ((h[1:, :-1] - h[:-1, :-1]) + (h[1:, 1:] - h[:-1, 1:])) / (2 * step)
    n = np.stack([-dx, np.ones_like(dx), -dz], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return _bytes(n[..., [0, 2]])


def island_values(size: float, mirror: bool = True) -> dict:
    """``cbTerrainRenderParams``: the island's place (x turned over into the view's mirrored axes when ``mirror``),
    its size, the nugget, the height page's border correction: texel ``border`` at the page's edge, as the
    compaction placed it (terrain-geometry.md 1.2, open question 4; island-117-view.md "Phase 2")."""
    m0 = [-1.0, 0.0, size, 0.0] if mirror else [1.0, 0.0, 0.0, 0.0]
    inner, border = terrainpages.INNER, terrainpages.BORDER
    return {"cTrnIslandMatrix0": m0, "cTrnIslandMatrix1": [0.0, 1.0, 0.0, 0.0], "cTrnIslandSize": [size, size],
            "cTrnUnitScale": UNIT, "cTrnHeightPageUVFactor": inner / (inner + 2 * border),
            "cTrnHeightPageUVOffset": (border + 0.5) / (inner + 2 * border),
            # the session's coarse height texture (cbPerFrame): the island's alone, no terraforming
            "cTerrainSize": [size, size, 1 / size, 1 / size], "cTerrainHeightTexIsValid": 1, "cTerraformFlags": 0}  # fmt: skip


def node_values(level: int, layer: int, page=(0.0, 0.0, 1.0), page_level: int = 0, tint=None) -> dict:
    """``cbTerrainNode``: no mirrored patch, its page (``cTrnNormalPageParams``: default the island's one normal
    page) and the page's level, its tint page (``tint``: its page parameters; None: no tint), fully faded in, its
    level and its slice of the baked arrays."""
    tinted = [*tint, 1.0] if tint is not None else [0.0, 0.0, 1.0, 0.0]
    return {"cTrnChunkOffsetFlip": [0.0, 0.0, 1.0, 1.0], "cTrnNormalPageParams": list(page),
            "cTrnPackedLevels": level | page_level << 8, "cTrnTintPageParams": tinted, "cTrnFading": [1.0, 1.0],
            "cTrnBakedTexArrayIndex": layer, "cTrnNodeDebugFlags": 0}  # fmt: skip


def upload(rhi, batch, fmt, size: tuple[int, int], layers: list[bytes], array: bool = True, srgb: bool = False):
    """A texture (an array of ``layers`` when ``array``) of ``fmt`` with each layer's level 0, uploaded in ``batch``."""
    flags = QRhiTexture.Flag.sRGB if srgb else QRhiTexture.Flag(0)
    if array:
        tex = rhi.newTextureArray(fmt, len(layers), QSize(*size), 1, flags | QRhiTexture.Flag.UsedAsTransferSource)
    else:
        tex = rhi.newTexture(fmt, QSize(*size), 1, flags)
    tex.create()
    desc = QRhiTextureUploadDescription()
    desc.setEntries([QRhiTextureUploadEntry(k, 0, QRhiTextureSubresourceUploadDescription(raw))
                     for k, raw in enumerate(layers)])  # fmt: skip
    batch.uploadTexture(tex, desc)
    return tex


class NodeSlots:
    """Every node's ``cbTerrainNode`` in one uniform buffer, a slot per baked slice (a node's layer, unique while it
    is drawn), bound with a dynamic offset per draw: the nodes and their casters make no buffer of their own (each a
    driver allocation, and a turning camera makes nodes every frame). Made with the first node that binds it."""

    def __init__(self, rhi, count: int = SLICES) -> None:
        self.rhi, self.count = rhi, count
        self.buffer = None
        self.sent: dict[int, bytes] = {}  # slot -> the bytes it holds

    def binding(self, number: int, stages):
        if self.buffer is None:
            self.buffer = gamedraw.uniform_buffer(self.rhi, self.count * NODE_SLOT)
        return gamedraw.B.uniformBufferWithDynamicOffset(number, stages, self.buffer, NODE_SLOT)

    def send(self, batch, slot: int, data: bytes) -> None:
        if self.sent.get(slot) != data:
            batch.updateDynamicBuffer(self.buffer, slot * NODE_SLOT, data)
            self.sent[slot] = data

    def release(self) -> None:
        if self.buffer is not None:
            self.buffer.destroy()
        self.buffer, self.sent = None, {}


@dataclass(frozen=True)
class Owner:
    """What the nodes of one terrain (or one bake) share: the device, the textures bound by name, the buffers of a
    program that hold no node value (``common``; None: a node's own) and the nodes' ``cbTerrainNode`` slots (None:
    a buffer of its own each)."""

    rhi: object
    textures: dict = field(default_factory=dict)
    common: Common | None = None
    slots: NodeSlots | None = None


class TerrainModel(gamedraw.GameModel):
    """One node drawn by the terrain shader: its own constants (``node_values``) written by name once per buffer
    (not ``GameModel.values``, which are written every frame: 100 names a node, a fifth of a moving frame), the
    terrain's textures (``owner.textures``, shared by the nodes) and its own (``own``: its pages) bound by name;
    ``slot``: its ``cbTerrainNode``'s place in the owner's ``NodeSlots``."""

    def __init__(self, program, mesh, values: dict, owner: Owner, own: dict | None = None, slot: int = 0,
                 state: renderstates.PassState = renderstates.GBUFFER_STATE,
                 share: TerrainModel | None = None) -> None:  # fmt: skip
        super().__init__(owner.rhi, program, mesh, gamedraw.Material({}, {}), share, state)
        self.node_values, self.textures, self.own = values, owner.textures, own or {}
        self.common = owner.common
        self.slots, self.slot = owner.slots, slot
        self.slot_binding: int | None = None  # ``cbTerrainNode``'s binding when it is at a dynamic offset
        self.kind: tuple = ("grid", None, 0)
        self.rect: tuple | None = None  # (x0, z0, width, depth) in tiles its u and v span; the slice is baked over it
        self._valued: dict[int, tuple] = {}  # a buffer's id -> the value objects written into it
        self._shared: dict[str, str] = {}  # a constant buffer of ``common`` -> its key there
        self._now: tuple | None = None  # this pass's camera and inputs (``gameinstances.stamp_of``)

    def _cbuffer(self, name: str, number: int, stages):
        if name != NODE_BUFFER or self.slots is None:
            return super()._cbuffer(name, number, stages)
        c = self.constants[name] = gs.Constants(self.program.cbuffers[name])
        if len(c.data) > NODE_SLOT:
            raise RuntimeError(f"{name} is larger than a node's slot ({NODE_SLOT} bytes)")
        self.slot_binding = number
        return self.slots.binding(number, stages)

    def _inputs(self, batch, fill: bytes) -> tuple:
        """The vertex buffer of the inputs the mesh lacks and the white texture: the owner's ``common`` ones when the
        mesh lacks none (a terrain vertex has all the shader reads)."""
        if self.common is None or fill:
            return super()._inputs(batch, fill)
        vertex = gamedraw.QRhiBuffer.UsageFlag.VertexBuffer
        empty = self.common.once("empty", lambda: gamedraw.immutable_buffer(self.rhi, batch, vertex, b""))
        return empty, self.common.once("white", lambda: gamedraw.white_texture(self.rhi, batch))

    def _uniform(self, name: str, size: int):
        """The owner's ``common`` buffer for one that holds none of the node's values: every node writes the same
        frame values into it, and a node made while the camera moves makes only its own (each buffer a driver
        allocation of about a millisecond)."""
        c, alike = self.constants[name], self.common.alike if self.common is not None else ()
        if self.common is None or any(c.find(k) is not None for k in self.node_values if k not in alike):
            return super()._uniform(name, size)
        self._shared[name] = key = f"{id(self.program)}:{name}"
        return self.common.buffer(key, size)

    def _send(self, batch, name: str, data: bytes) -> None:
        """A buffer of the owner's ``common`` is compared with what any node last sent there: a node's own record
        of it says nothing of the nodes drawn since (a camera back where it was kept another camera's values)."""
        key = self._shared.get(name)
        if name == NODE_BUFFER and self.slot_binding is not None:
            self.slots.send(batch, self.slot, data)
        elif key is None:
            super()._send(batch, name, data)
        elif self.common.sent.get(key) != data:
            batch.updateDynamicBuffer(self.buffers[name], 0, data)
            self.common.sent[key] = data

    def _write(self, batch, name: str, c, camera, fill) -> None:
        """A buffer of the owner's ``common`` is filled by the first node of a pass for its camera and inputs; the
        others skip it (the same bytes: 88 nodes and their casters filled each one every frame)."""
        key = self._shared.get(name)
        if key is not None and self._now is not None and self.common.stamps.get(key) == self._now:
            return
        super()._write(batch, name, c, camera, fill)
        if key is not None:
            self.common.stamps[key] = self._now

    def draw(self, cb, size: tuple[int, int], instances: int = 1) -> None:
        if self.pipeline is None:
            return
        offset = (self.slot_binding, self.slot * NODE_SLOT) if self.slot_binding is not None else None
        self.mesh.draw(cb, self.pipeline, self.srb, size, (self.vbufs, self.ibuf), instances, offset)

    def prepare(self, rp, signature, batch, camera, fill=None, shared=None) -> None:
        self._now = stamp_of(camera, fill, "common_key") if self.common is not None else None

        def own(c) -> None:
            written = tuple(map(id, self.node_values.values()))
            if self._valued.get(id(c)) != written:  # a node's values stay: written once (the bake's change by object)
                for name, value in self.node_values.items():
                    c.set(name, value)
                self._valued[id(c)] = written
            if fill is not None:
                fill(c)

        super().prepare(rp, signature, batch, camera, own, {**self.textures, **self.own, **(shared or {})})


@dataclass
class IslandSource:
    """What the island's files give the terrain beyond its heights (its owner reads them): the height pages' parts
    by (level, x, z), the stored meshes by level, then node (column, row): (vertices as stored, indices), the tint
    pages' files by (level, x, z) and ``read`` (a file's bytes by path)."""

    pages: dict
    meshes: dict
    tints: dict = field(default_factory=dict)
    read: Callable | None = None


class Terrain:
    """The island's nodes on the device (module docstring): ``nodes(eye, visible)`` picks them for a camera
    (``terrainnodes``) and gives each its slice; ``textures`` are the shader's inputs by name, the baked colour and
    normal arrays (``cBakedDiffTexArray``, ``cBakedNormalTexArray``, ``SLICES`` of ``SLICE_SIZE``) its owner's to fill
    for the nodes in ``cache.fresh``."""

    def __init__(self, rhi, program: gamedraw.Program, size: float, step: float, width: int,
                 detailed: gamedraw.Program | None = None) -> None:  # fmt: skip
        self.rhi, self.program, self.size, self.step, self.width = rhi, program, size, step, width
        self.detailed = detailed  # the tessellated program, None: no node is drawn tessellated
        self.shadow: tuple | None = None  # the shadow programs (plain, tessellated or None): the owner's
        self._casters: dict[Node, TerrainModel] = {}
        self.pages: terrainpages.Pages | None = None  # the owner's
        self.source: IslandSource | None = None
        self.files_shown = False  # the island files' own geometry (pages, stored meshes) where nothing was edited
        self.edited: np.ndarray | None = None  # per block of the finest nodes: its heights differ from the file's
        self.cache = terrainnodes.SliceCache(SLICES)
        self.models: dict[Node, TerrainModel] = {}
        self._drawn: set[Node] = set()  # the nodes the last frame drew (``shown``)
        self._visible = None  # the last ``nodes`` call's test of a node in view
        self.textures: dict = {}
        self.page = None
        self.keep: list = []
        self.stand_ins: dict = {}  # what reads as "nothing baked" where no bake hands its own (``uploads``)
        # the nodes' buffers without node values (the island's are every node's), per program (``TerrainModel``)
        self.common = Common(rhi, island_values(size))
        self.slots = NodeSlots(rhi)  # every node's cbTerrainNode, at its slice's slot
        self.tints: dict = {}  # the tint pages made, by (level, x, z); None where one does not read
        self._tint_uploads: list = []
        self.island = island_values(size)
        self.heights: np.ndarray | None = None
        self._normals: np.ndarray | None = None
        self._page_dirty = True

    def set_heights(self, heights: np.ndarray, box=None) -> None:
        """New heights (world units, [row][column] over the island); ``box`` (tiles x0, z0, x1, z1): only the nodes
        it touches are made and baked again, None: all of them."""
        self.heights, self._normals, self._page_dirty = heights, None, True
        hit = self.cache.spoil(box if box is not None else (0.0, 0.0, self.size, self.size))
        for node in list(self.models) if box is None else hit:
            model = self.models.pop(node, None)
            if model is not None:
                model.release()

    def nodes(self, eye, visible=None) -> list[TerrainModel]:
        """The models of the nodes ``terrainnodes.select`` picks for ``eye`` (the island's tiles), made where
        missing (on the CPU: their resources come with their pass); a node whose slice was given away goes."""
        if self.heights is None:
            return []
        out = []
        for node in terrainnodes.select(self.size, eye, SLICES, visible=visible):
            layer = self.cache.get(node)
            kind = self._kind(node)
            model = self.models.get(node)
            if model is not None and model.kind != kind:
                model.release()
                model = None
            if model is None:
                model = self.models[node] = self._make(node, layer, kind)
            out.append(model)
        for node in self.cache.dropped:
            model = self.models.pop(node, None)
            if model is not None and all(model is not m for m in out):
                model.release()
        self.cache.dropped.clear()
        self._visible = visible
        return out

    def shown(self, models: list[TerrainModel]) -> list[TerrainModel]:
        """What draws for this frame's ``models`` (``nodes``) once the frame's bake is recorded: a node not baked yet
        stands in as the nodes drawn before over its square (``terrainnodes.stand_ins``), kept in the slice cache."""
        pages = self.pages.ready if self.pages is not None else {}

        def ready(node) -> bool:  # baked, and its model's page still on the device
            model = self.models.get(node)
            page = model.kind[1] if model is not None else None
            current = page is None or (page in pages and id(pages[page]) == model.kind[2])
            return model is not None and current and node in self.cache.slices and node not in self.cache.fresh

        wanted = {m.node: m for m in models}
        nodes = terrainnodes.stand_ins(list(wanted), ready, self._drawn, self.size, self._visible)
        for node in nodes:
            if node not in wanted:  # standing in: not given away before its replacement is baked
                self.cache.slices.move_to_end(node)
        self._drawn = set(nodes)
        return [wanted[n] if n in wanted else self.models[n] for n in nodes]

    def casters(self, models: list[TerrainModel]) -> list[TerrainModel]:
        """The nodes drawn into the sun's cascades by the terrain's shadow technique: per node model a caster that
        draws its vertices with its constants and textures (the tessellated technique for a tessellated node)."""
        if self.shadow is None:
            return []
        out = []
        for m in models:
            caster = self._casters.get(m.node)
            if caster is None or caster.share is not m:
                if caster is not None:
                    caster.release()
                program = self.shadow[1] if m.program is self.detailed else self.shadow[0]
                if program is None:
                    self._casters.pop(m.node, None)
                    continue
                owner = Owner(self.rhi, m.textures, self.common, self.slots)
                caster = TerrainModel(program, m.mesh, m.node_values, owner, m.own, m.slot, share=m)
                caster.node, caster.kind = m.node, m.kind
                self._casters[m.node] = caster
            out.append(caster)
        shown = {m.node for m in models}
        for node in [n for n, c in self._casters.items() if n not in shown and c.share is not self.models.get(n)]:
            self._casters.pop(node).release()
        return out

    def _edited(self, node: Node) -> bool:
        if self.edited is None:
            return False
        per = 2**terrainnodes.MIN_LEVEL
        i0, j0, n = int(node.x // per), int(node.z // per), max(1, int(node.tiles // per))
        return bool(self.edited[j0 : j0 + n, i0 : i0 + n].any())

    def _kind(self, node: Node) -> tuple:
        """How a node draws: ("detailed", its page, ..) when it is near and its page is rebuilt (asked for when not),
        ("stored", the page for its normals or None, ..) when the island stores its mesh, else ("grid", None, ..):
        the view's heights; the last item tells the page's textures apart (a page rebuilt makes the model again)."""
        src = self.source
        if src is None or not self.files_shown or self._edited(node):
            return ("grid", None, 0)
        ready = self.pages.ready if self.pages is not None else {}
        page = terrainpages.finest_page(src.pages, node.level, node.x, node.z)
        if self.detailed is not None and self.pages is not None and page is not None and node.level <= DETAILED_LEVEL:
            self.pages.want(page, src.pages[page])
            if page in ready:
                return ("detailed", page, id(ready[page]))
        tiles = 2**node.level
        if (int(node.x // tiles), int(node.z // tiles)) in src.meshes.get(node.level, {}):
            return ("stored", page, id(ready[page])) if page in ready else ("stored", None, 0)
        return ("grid", None, 0)

    def normals(self) -> np.ndarray:
        """The vertex normals of the heights (``vertex_normals``), made once per height change."""
        if self._normals is None:
            self._normals = vertex_normals(self.heights, self.step)
        return self._normals

    def _make(self, node: Node, layer: int, kind: tuple) -> TerrainModel:
        how, page, _ = kind
        normals = self.normals()
        program, own = self.program, {}
        rect = (node.x, node.z, node.tiles, node.tiles)
        if how == "stored":
            tiles = 2**node.level
            vertices, indices = self.source.meshes[node.level][(int(node.x // tiles), int(node.z // tiles))]
            mesh = gamedraw.Mesh(vertices, STRIDE, ELEMENTS, indices, len(vertices) // STRIDE)
            rect = mesh_rect(vertices)
        else:
            _col, _row, cols, rows = node.samples(self.step, self.width)
            data = encode_vertices(self.heights, self.step, node, self.width, normals)
            if how == "detailed":
                data, indices = with_skirts(data, cols, rows)
                indices = indices.reshape(-1, 4)[:, PATCH_CORNERS].ravel()  # the tessellator's triangles face up
                mesh, program = gamedraw.Mesh(data, STRIDE, ELEMENTS, indices, len(data) // STRIDE), self.detailed
            else:  # a skirt that covers the steps to the tessellated and stored nodes beside it [ours]
                data, quads = with_skirts(data, cols, rows, GRID_SKIRT)
                mesh = gamedraw.Mesh(data, STRIDE, ELEMENTS, quad_triangles(quads), len(data) // STRIDE)
        if page is not None:
            height, normal = self.pages.ready[page]
            own = {"cNodeHeightMap": height, "cNodeNormalMap": normal, "cTerrainHeightTex": self.pages.coarse}
        params = terrainpages.page_params(page, self.size) if page is not None else (0.0, 0.0, 1.0)
        tint = self._tint(node)
        if tint is not None:
            own["cNodeTintMap"] = tint[0]
        values = {**self.island, **node_values(node.level, layer, params, page[0] if page is not None else 0,
                                               tint[1] if tint is not None else None)}  # fmt: skip
        owner = Owner(self.rhi, self.textures, self.common, self.slots)
        model = TerrainModel(program, mesh, values, owner, own, layer)
        model.node, model.kind, model.rect = node, kind, rect
        return model

    def _tint(self, node: Node):
        """The node's tint page (the finest that holds it whole: as its height page [ours]) and its page parameters,
        read and made on first use (uploaded with ``uploads``); None for an island without tint pages."""
        from ..models import ddsdecode

        src = self.source
        key = terrainpages.finest_page(src.tints, node.level, node.x, node.z) if src is not None and src.read else None
        if key is None:
            return None
        if key not in self.tints:
            try:
                dds = ddsdecode.DdsFile(src.read(src.tints[key]))
            except (ddsdecode.UnsupportedDds, KeyError, FileNotFoundError):
                self.tints[key] = None
            else:
                mips = QRhiTexture.Flag.MipMapped if dds.info.mips > 1 else QRhiTexture.Flag(0)
                tex = self.rhi.newTexture(gamedraw.BLOCK_FORMATS.get(dds.info.format, F.BC1),
                                          QSize(dds.info.width, dds.info.height), 1, mips)  # fmt: skip
                tex.create()
                self._tint_uploads.append((tex, gamedraw.stored_levels(dds)))
                self.tints[key] = tex
        tex = self.tints[key]
        return (tex, terrainpages.page_params(key, self.size)) if tex is not None else None

    def uploads(self, batch) -> None:
        """Before the nodes draw (``batch`` of a pass ahead of the G-buffer's): the normal page when the heights
        changed, written into the same texture so that the nodes' bindings stay; the first time also the inputs
        the owner does not fill: gloss 0 (no highlight), the material height 0.5 (the middle of the shader's -2..2:
        no displacement), one slice each (the shader clamps the array index), and the session maps of ``ZERO``."""
        if not self.keep:
            gloss = upload(self.rhi, batch, F.RGBA8, (4, 4), [bytes(64)])
            height = upload(self.rhi, batch, F.RGBA16F, (4, 4), [np.full(64, 0.5, np.float16).tobytes()])
            black = upload(self.rhi, batch, F.RGBA8, (4, 4), [bytes(64)], array=False)
            self.keep += [gloss, height, black]
            self.stand_ins = {"cBakedSpecTexArray": gloss, "cBakedHeightTexArray": height,
                              "cBakedGranularityTexArray": height, **dict.fromkeys(ZERO, black)}  # fmt: skip
        for name, tex in self.stand_ins.items():  # where the game's bake does not hand its own
            self.textures.setdefault(name, tex)
        for tex, levels in self._tint_uploads:  # the tint pages the nodes made this frame read (BC1, linear)
            desc = QRhiTextureUploadDescription()
            desc.setEntries([QRhiTextureUploadEntry(0, k, QRhiTextureSubresourceUploadDescription(raw))
                             for k, raw in enumerate(levels)])  # fmt: skip
            batch.uploadTexture(tex, desc)
        self._tint_uploads.clear()
        if self.heights is None or not self._page_dirty:
            return
        page = normal_page(self.heights, self.step)
        if self.page is None:
            self.page = upload(self.rhi, batch, F.RG8, (page.shape[1], page.shape[0]), [page.tobytes()], False)
            self.textures["cNodeNormalMap"] = self.page
        else:
            gamedraw.upload_level(batch, self.page, page.tobytes())
        self._page_dirty = False

    def release(self) -> None:
        for m in (*self._casters.values(), *self.models.values()):
            m.release()
        self._casters = {}
        for t in (*self.keep, self.page, *self.tints.values()):
            if t is not None:
                t.destroy()
        self.tints, self._tint_uploads = {}, []
        self.models, self.keep, self.page, self.stand_ins, self._drawn = {}, [], None, {}, set()
        self.common.release()
        self.slots.release()
        self.cache.clear()
        self.cache.dropped.clear()
        self._page_dirty = True
