"""Anno 117's terrain drawn by the game's own terrain shader (``terrain.fx``) into the engine's G-buffer: the island
in square nodes of the engine's 16-byte terrain vertices, each drawn one of three ways - near ones tessellated from
their height page (``TerrainDeferredDetailed``), far ones from the island's stored mesh, the rest (and the edited)
from the view's heights (``TerrainDeferred``) - with the node's constants, pages and the baked ground arrays.

``CTerrain(rhi, programs, size, step, width)`` is the entry: ``set_heights`` marks the nodes a height change touches,
``nodes(eye)`` gives the models for ``gameframe.CGameFrame.render``. Formats: docs/rendering/terrain-geometry.md 1, 2
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

from . import gamedraw, gameshader, gametextures, renderstates, terrainnodes, terrainpages
from .gameinstances import CCommon, stamp_of
from .terrainnodes import SNode

EFFECT = "data/shaders/fx/terrain.fx"
STAGES = {"vs": (EFFECT, "TerrainDeferred", "vsTerrain"), "ps": (EFFECT, "TerrainDeferred", "psTerrainDeferred")}
DETAILED = {stage: (EFFECT, "TerrainDeferredDetailed", function) for stage, function in (("vs", "vsTerrain"), ("hs", "hsTerrain"),
            ("ds", "dsTerrain"), ("ps", "psTerrainDeferred"))}  # fmt: skip
# the sun's shadow: the plain technique's vertex shader displaces by the baked height, the detailed one's domain shader
SHADOW = {"vs": (EFFECT, "TerrainShadow", "vsTerrainShadow"), "ps": (EFFECT, "TerrainShadow", "psTerrainShadow")}
SHADOW_DETAILED = {**{stage: (EFFECT, "TerrainShadowDetailed", function) for stage, function in (("vs", "vsTerrainShadow"),
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
TextureFormat = QRhiTexture.Format


def _bytes(vectors: np.ndarray) -> np.ndarray:
    """A unit vector's components as the engine's unorm bytes (``* 2 - 1`` in the shader)."""
    return np.clip(np.rint((vectors * 0.5 + 0.5) * 255), 0, 255).astype(np.uint8)


def vertex_normals(heights: np.ndarray, step_tiles: float) -> np.ndarray:
    """Per height sample the surface normal in the island's own axes (x, y up, z), from central differences."""
    dz, dx = np.gradient(heights.astype(np.float64), step_tiles)
    normal = np.stack([-dx, np.ones_like(dx), -dz], -1)
    return normal / np.linalg.norm(normal, axis=-1, keepdims=True)


def encode_vertices(
    heights: np.ndarray, step_tiles: float, node: SNode, width_samples: int, normals: np.ndarray
) -> bytes:
    """A node's height samples as ``NuggetsVertex``: x and z in nuggets, y as ``(y - 32768) / 256`` world units, u and
    v over the node's square (v's 16 bits split over the normal's and the tangent's fourth byte), the normal (from
    ``normals``) and the tangent (along the island's -x) as unorm bytes, read back ``.bgra`` (terrain-geometry.md 1.1)."""
    column, row, cols, rows = node.samples(step_tiles, width_samples)
    row_slice, column_slice = slice(row, row + rows), slice(column, column + cols)
    node_heights = heights[row_slice, column_slice].astype(np.float64)
    j, i = np.mgrid[row_slice, column_slice].astype(np.float64)
    x, z = i * step_tiles, j * step_tiles
    u_coordinate, v_coordinate = (x - node.x) / node.tiles, (z - node.z) / node.tiles
    encoded = np.zeros(node_heights.shape + (STRIDE,), np.uint8)
    positions = np.stack(
        [np.rint(x / UNIT), np.rint(node_heights * 256) + 32768, np.rint(z / UNIT), np.rint(u_coordinate * 65535)], -1
    )
    encoded[..., 0:8] = np.clip(positions, 0, 65535).astype("<u2").view(np.uint8).reshape(node_heights.shape + (8,))
    v_code = np.clip(np.rint(v_coordinate * 65535), 0, 65535).astype(np.uint32)
    node_normals = normals[row_slice, column_slice]
    normal_bytes = _bytes(node_normals)
    # the tangent along -x on the surface, as the island's stored meshes hold it (island-117-view "Phase 3")
    tangent = np.stack(
        [
            -np.ones_like(node_normals[..., 0]),
            node_normals[..., 0] / np.maximum(node_normals[..., 1], 1e-6),
            np.zeros_like(node_normals[..., 0]),
        ],
        -1,
    )
    tangent_bytes = _bytes(tangent / np.linalg.norm(tangent, axis=-1, keepdims=True))
    encoded[..., 8], encoded[..., 9], encoded[..., 10], encoded[..., 11] = (
        normal_bytes[..., 2],
        normal_bytes[..., 1],
        normal_bytes[..., 0],
        v_code & 0xFF,
    )
    encoded[..., 12], encoded[..., 13], encoded[..., 14], encoded[..., 15] = (
        tangent_bytes[..., 2],
        tangent_bytes[..., 1],
        tangent_bytes[..., 0],
        v_code >> 8,
    )
    return encoded.tobytes()


def mesh_rect(vertices: bytes) -> tuple[float, float, float, float]:
    """(x0, z0, width, depth) in tiles of a stored mesh's vertices: its u and v run over this rectangle, not over its
    node's square (a node at the island's edge is trimmed; island-117-view.md "Round 12")."""
    positions = (
        np.frombuffer(vertices, np.uint8).reshape(-1, STRIDE)[:, 0:6].copy().view("<u2").astype(np.float64) * UNIT
    )
    min_x, min_z = float(positions[:, 0].min()), float(positions[:, 2].min())
    return (
        min_x,
        min_z,
        max(float(positions[:, 0].max()) - min_x, UNIT),
        max(float(positions[:, 2].max()) - min_z, UNIT),
    )


def patch_indices(cols: int, rows: int) -> np.ndarray:
    """A quad patch per cell of a ``cols`` x ``rows`` sample grid, its corners in the domain shader's order: (x0, z0),
    (x0, z1), (x1, z1), (x1, z0) (``MIX_QUAD_UV``)."""
    j, i = np.mgrid[0 : rows - 1, 0 : cols - 1]
    corner_index = (j * cols + i).ravel()
    return (
        np.stack([corner_index, corner_index + cols, corner_index + cols + 1, corner_index + 1], -1)
        .astype(np.uint32)
        .ravel()
    )


def with_skirts(vertices: bytes, cols: int, rows: int, drop_tiles: float = 0.0) -> tuple[bytes, np.ndarray]:
    """A grid's vertices with a skirt around it: each edge vertex again with its normal's y turned over (the
    engine's skirt flag: ``PushSkirtVertex`` hangs a tessellated one below the page surface) and ``drop_tiles`` world units
    lower, a quad between each edge pair and its copies; the grid's quad patches first (terrain-geometry.md 2.2)."""
    grid_vertices = np.frombuffer(vertices, np.uint8).reshape(rows * cols, STRIDE)
    grid = np.arange(rows * cols).reshape(rows, cols)
    edges = [grid[0], grid[:, -1], grid[-1, ::-1], grid[::-1, 0]]  # around, each edge corner to corner
    ring = np.concatenate([edge[:-1] for edge in edges] + [grid[:1, 0]])
    skirt = grid_vertices[ring].copy()
    skirt[:, 9] = 255 - skirt[:, 9]  # -y: the skirt flag
    skirt_y = skirt[:, 2:4].copy().view("<u2").astype(np.int64) - round(drop_tiles * 256)
    skirt[:, 2:4] = np.clip(skirt_y, 0, 65535).astype("<u2").view(np.uint8)
    first = rows * cols + np.arange(len(ring))
    quads = np.stack([ring[:-1], first[:-1], first[1:], ring[1:]], -1).astype(np.uint32).ravel()
    return np.concatenate([grid_vertices, skirt]).tobytes(), np.concatenate([patch_indices(cols, rows), quads])


def quad_triangles(quads: np.ndarray) -> np.ndarray:
    """Quads (four corners each, in ``patch_indices``' order) as two triangles each."""
    corners = quads.reshape(-1, 4)
    return (
        np.stack([corners[:, 0], corners[:, 1], corners[:, 2], corners[:, 0], corners[:, 2], corners[:, 3]], -1)
        .astype(np.uint32)
        .ravel()
    )


def grid_indices(cols: int, rows: int) -> np.ndarray:
    """Two triangles per cell of a ``cols`` x ``rows`` sample grid, row by row."""
    j, i = np.mgrid[0 : rows - 1, 0 : cols - 1]
    corner_index = (j * cols + i).ravel()
    quads = np.stack(
        [
            corner_index,
            corner_index + cols,
            corner_index + 1,
            corner_index + 1,
            corner_index + cols,
            corner_index + cols + 1,
        ],
        -1,
    )
    return quads.astype(np.uint32).ravel()


def normal_page(heights: np.ndarray, step_tiles: float) -> np.ndarray:
    """The island's normal page as the engine's ``csComputeNormalMap`` makes one from its heights: one texel per
    cell of the height grid (cell-centred), the normal's x and z as two unorm bytes [rows, cols, 2]; the shader takes
    y as ``sqrt(1 - x^2 - z^2)`` (terrain-geometry.md 5)."""
    samples = heights.astype(np.float64)
    dx = ((samples[:-1, 1:] - samples[:-1, :-1]) + (samples[1:, 1:] - samples[1:, :-1])) / (2 * step_tiles)
    dz = ((samples[1:, :-1] - samples[:-1, :-1]) + (samples[1:, 1:] - samples[:-1, 1:])) / (2 * step_tiles)
    normal = np.stack([-dx, np.ones_like(dx), -dz], -1)
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    return _bytes(normal[..., [0, 2]])


def island_values(size_tiles: float, is_mirrored: bool = True) -> dict:
    """``cbTerrainRenderParams``: the island's place (x turned over into the view's mirrored axes when ``is_mirrored``),
    its size, the nugget, the height page's border correction: texel ``border`` at the page's edge, as the
    compaction placed it (terrain-geometry.md 1.2, open question 4; island-117-view.md "Phase 2")."""
    matrix_row = [-1.0, 0.0, size_tiles, 0.0] if is_mirrored else [1.0, 0.0, 0.0, 0.0]
    inner, border = terrainpages.INNER, terrainpages.BORDER
    return {"cTrnIslandMatrix0": matrix_row, "cTrnIslandMatrix1": [0.0, 1.0, 0.0, 0.0], "cTrnIslandSize": [size_tiles, size_tiles],
            "cTrnUnitScale": UNIT, "cTrnHeightPageUVFactor": inner / (inner + 2 * border),
            "cTrnHeightPageUVOffset": (border + 0.5) / (inner + 2 * border),
            # the session's coarse height texture (cbPerFrame): the island's alone, no terraforming
            "cTerrainSize": [size_tiles, size_tiles, 1 / size_tiles, 1 / size_tiles], "cTerrainHeightTexIsValid": 1, "cTerraformFlags": 0}  # fmt: skip


def node_values(level: int, layer: int, page=(0.0, 0.0, 1.0), page_level: int = 0, tint=None) -> dict:
    """``cbTerrainNode``: no mirrored patch, its page (``cTrnNormalPageParams``: default the island's one normal
    page) and the page's level, its tint page (``tint``: its page parameters; None: no tint), fully faded in, its
    level and its slice of the baked arrays."""
    tinted = [*tint, 1.0] if tint is not None else [0.0, 0.0, 1.0, 0.0]
    return {"cTrnChunkOffsetFlip": [0.0, 0.0, 1.0, 1.0], "cTrnNormalPageParams": list(page),
            "cTrnPackedLevels": level | page_level << 8, "cTrnTintPageParams": tinted, "cTrnFading": [1.0, 1.0],
            "cTrnBakedTexArrayIndex": layer, "cTrnNodeDebugFlags": 0}  # fmt: skip


def upload(
    rhi,
    batch,
    texture_format,
    size_px: tuple[int, int],
    layers: list[bytes],
    is_array: bool = True,
    is_srgb: bool = False,
):
    """A texture (an array of ``layers`` when ``is_array``) of ``texture_format`` with each layer's level 0, uploaded in ``batch``."""
    flags = QRhiTexture.Flag.sRGB if is_srgb else QRhiTexture.Flag(0)
    if is_array:
        texture = rhi.newTextureArray(
            texture_format, len(layers), QSize(*size_px), 1, flags | QRhiTexture.Flag.UsedAsTransferSource
        )
    else:
        texture = rhi.newTexture(texture_format, QSize(*size_px), 1, flags)
    texture.create()
    description = QRhiTextureUploadDescription()
    description.setEntries([QRhiTextureUploadEntry(k, 0, QRhiTextureSubresourceUploadDescription(raw))
                     for k, raw in enumerate(layers)])  # fmt: skip
    batch.uploadTexture(texture, description)
    return texture


class CNodeSlots:
    """Every node's ``cbTerrainNode`` in one uniform buffer, a slot per baked slice (a node's layer, unique while it
    is drawn), bound with a dynamic offset per draw: the nodes and their casters make no buffer of their own (each a
    driver allocation, and a turning camera makes nodes every frame). Made with the first node that binds it."""

    def __init__(self, rhi, count: int = SLICES) -> None:
        self.m_rhi, self.m_count = rhi, count
        self.m_buffer = None
        self.m_sent: dict[int, bytes] = {}  # slot -> the bytes it holds

    def binding(self, number: int, stages):
        if self.m_buffer is None:
            self.m_buffer = gamedraw.uniform_buffer(self.m_rhi, self.m_count * NODE_SLOT)
        return gamedraw.Binding.uniformBufferWithDynamicOffset(number, stages, self.m_buffer, NODE_SLOT)

    def send(self, batch, slot: int, data: bytes) -> None:
        if self.m_sent.get(slot) != data:
            batch.updateDynamicBuffer(self.m_buffer, slot * NODE_SLOT, data)
            self.m_sent[slot] = data

    def release(self) -> None:
        if self.m_buffer is not None:
            self.m_buffer.destroy()
        self.m_buffer, self.m_sent = None, {}


@dataclass(frozen=True)
class SOwner:
    """What the nodes of one terrain (or one bake) share: the device, the textures bound by name, the buffers of a
    program that hold no node value (``common``; None: a node's own) and the nodes' ``cbTerrainNode`` slots (None:
    a buffer of its own each)."""

    rhi: object
    textures: dict = field(default_factory=dict)
    common: CCommon | None = None
    slots: CNodeSlots | None = None


class CTerrainModel(gamedraw.CGameModel):
    """One node drawn by the terrain shader: its own constants (``node_values``) written by name once per buffer
    (not ``CGameModel.values``, which are written every frame: 100 names a node, a fifth of a moving frame), the
    terrain's textures (``owner.textures``, shared by the nodes) and its own (``own``: its pages) bound by name;
    ``slot``: its ``cbTerrainNode``'s place in the owner's ``CNodeSlots``."""

    def __init__(self, program, mesh, values: dict, owner: SOwner, own: dict | None = None, slot: int = 0,
                 state: renderstates.SPassState = renderstates.GBUFFER_STATE,
                 share: CTerrainModel | None = None) -> None:  # fmt: skip
        super().__init__(owner.rhi, program, mesh, gamedraw.SMaterial({}, {}), share, state)
        self.m_node_values, self.m_textures, self.m_own = values, owner.textures, own or {}
        self.m_common = owner.common
        self.m_slots, self.m_slot = owner.slots, slot
        self.m_slot_binding: int | None = None  # ``cbTerrainNode``'s binding when it is at a dynamic offset
        self.m_kind: tuple = ("grid", None, 0)
        self.m_rect: tuple | None = None  # (x0, z0, width, depth) in tiles its u and v span; the slice is baked over it
        self._m_valued: dict[int, tuple] = {}  # a buffer's id -> the value objects written into it
        self._m_shared: dict[str, str] = {}  # a constant buffer of ``common`` -> its key there
        self._m_now: tuple | None = None  # this pass's camera and inputs (``gameinstances.stamp_of``)

    def _cbuffer(self, name: str, number: int, stages):
        if name != NODE_BUFFER or self.m_slots is None:
            return super()._cbuffer(name, number, stages)
        constants = self.m_constants[name] = gameshader.CConstants(self.m_program.m_cbuffers[name])
        if len(constants.m_data) > NODE_SLOT:
            raise RuntimeError(f"{name} is larger than a node's slot ({NODE_SLOT} bytes)")
        self.m_slot_binding = number
        return self.m_slots.binding(number, stages)

    def _inputs(self, batch, fill: bytes) -> tuple:
        """The vertex buffer of the inputs the mesh lacks and the white texture: the owner's ``common`` ones when the
        mesh lacks none (a terrain vertex has all the shader reads)."""
        if self.m_common is None or fill:
            return super()._inputs(batch, fill)
        vertex = gamedraw.QRhiBuffer.UsageFlag.VertexBuffer
        empty = self.m_common.once("empty", lambda: gamedraw.immutable_buffer(self.m_rhi, batch, vertex, b""))
        return empty, self.m_common.once("white", lambda: gamedraw.white_texture(self.m_rhi, batch))

    def _uniform(self, name: str, size_bytes: int):
        """The owner's ``common`` buffer for one that holds none of the node's values: every node writes the same
        frame values into it, and a node made while the camera moves makes only its own (each buffer a driver
        allocation of about a millisecond)."""
        constants, alike = self.m_constants[name], self.m_common.m_alike if self.m_common is not None else ()
        if self.m_common is None or any(
            constants.find(field_name) is not None for field_name in self.m_node_values if field_name not in alike
        ):
            return super()._uniform(name, size_bytes)
        self._m_shared[name] = key = f"{id(self.m_program)}:{name}"
        return self.m_common.buffer(key, size_bytes)

    def _send(self, batch, name: str, data: bytes) -> None:
        """A buffer of the owner's ``common`` is compared with what any node last sent there: a node's own record
        of it says nothing of the nodes drawn since (a camera back where it was kept another camera's values)."""
        key = self._m_shared.get(name)
        if name == NODE_BUFFER and self.m_slot_binding is not None:
            self.m_slots.send(batch, self.m_slot, data)
        elif key is None:
            super()._send(batch, name, data)
        elif self.m_common.m_sent.get(key) != data:
            batch.updateDynamicBuffer(self.m_buffers[name], 0, data)
            self.m_common.m_sent[key] = data

    def _write(self, batch, name: str, constants, camera, fill) -> None:
        """A buffer of the owner's ``common`` is filled by the first node of a pass for its camera and inputs; the
        others skip it (the same bytes: 88 nodes and their casters filled each one every frame)."""
        key = self._m_shared.get(name)
        if key is not None and self._m_now is not None and self.m_common.m_stamps.get(key) == self._m_now:
            return
        super()._write(batch, name, constants, camera, fill)
        if key is not None:
            self.m_common.m_stamps[key] = self._m_now

    def draw(self, command_buffer, size_px: tuple[int, int], instances: int = 1) -> None:
        if self.m_pipeline is None:
            return
        offset = (self.m_slot_binding, self.m_slot * NODE_SLOT) if self.m_slot_binding is not None else None
        self.m_mesh.draw(
            command_buffer, self.m_pipeline, self.m_srb, size_px, (self.m_vbufs, self.m_ibuf), instances, offset
        )

    def prepare(self, render_pass, signature, batch, camera, fill=None, shared=None) -> None:
        self._m_now = stamp_of(camera, fill, "common_key") if self.m_common is not None else None

        def own(constants) -> None:
            written = tuple(map(id, self.m_node_values.values()))
            if (
                self._m_valued.get(id(constants)) != written
            ):  # a node's values stay: written once (the bake's change by object)
                for name, value in self.m_node_values.items():
                    constants.set(name, value)
                self._m_valued[id(constants)] = written
            if fill is not None:
                fill(constants)

        super().prepare(render_pass, signature, batch, camera, own, {**self.m_textures, **self.m_own, **(shared or {})})


@dataclass
class SIslandSource:
    """What the island's files give the terrain beyond its heights (its owner reads them): the height pages' parts
    by (level, x, z), the stored meshes by level, then node (column, row): (vertices as stored, indices), the tint
    pages' files by (level, x, z) and ``read`` (a file's bytes by path)."""

    pages: dict
    meshes: dict
    tints: dict = field(default_factory=dict)
    read: Callable | None = None


class CTerrain:
    """The island's nodes on the device (module docstring): ``nodes(eye, visible)`` picks them for a camera
    (``terrainnodes``) and gives each its slice; ``textures`` are the shader's inputs by name, the baked colour and
    normal arrays (``cBakedDiffTexArray``, ``cBakedNormalTexArray``, ``SLICES`` of ``SLICE_SIZE``) its owner's to fill
    for the nodes in ``cache.fresh``."""

    def __init__(self, rhi, program: gamedraw.CProgram, size_tiles: float, step_tiles: float, width_samples: int,
                 detailed: gamedraw.CProgram | None = None) -> None:  # fmt: skip
        self.m_rhi, self.m_program, self.m_size_tiles, self.m_step_tiles, self.m_width_samples = (
            rhi,
            program,
            size_tiles,
            step_tiles,
            width_samples,
        )
        self.m_detailed = detailed  # the tessellated program, None: no node is drawn tessellated
        self.m_shadow: tuple | None = None  # the shadow programs (plain, tessellated or None): the owner's
        self._m_casters: dict[SNode, CTerrainModel] = {}
        self.m_pages: terrainpages.CPages | None = None  # the owner's
        self.m_source: SIslandSource | None = None
        self.m_files_shown = False  # the island files' own geometry (pages, stored meshes) where nothing was edited
        self.m_edited: np.ndarray | None = None  # per block of the finest nodes: its heights differ from the file's
        self.m_cache = terrainnodes.CSliceCache(SLICES)
        self.m_models: dict[SNode, CTerrainModel] = {}
        self._m_drawn: set[SNode] = set()  # the nodes the last frame drew (``shown``)
        self._m_visible = None  # the last ``nodes`` call's test of a node in view
        self.m_textures: dict = {}
        self.m_page = None
        self.m_keep: list = []
        self.m_stand_ins: dict = {}  # what reads as "nothing baked" where no bake hands its own (``uploads``)
        # the nodes' buffers without node values (the island's are every node's), per program (``CTerrainModel``)
        self.m_common = CCommon(rhi, island_values(size_tiles))
        self.m_slots = CNodeSlots(rhi)  # every node's cbTerrainNode, at its slice's slot
        self.m_tints: dict = {}  # the tint pages made, by (level, x, z); None where one does not read
        self._m_tint_uploads: list = []
        self.m_island = island_values(size_tiles)
        self.m_heights: np.ndarray | None = None
        self._m_normals: np.ndarray | None = None
        self._m_page_dirty = True

    def set_heights(self, heights: np.ndarray, box=None) -> None:
        """New heights (world units, [row][column] over the island); ``box`` (tiles x0, z0, x1, z1): only the nodes
        it touches are made and baked again, None: all of them."""
        self.m_heights, self._m_normals, self._m_page_dirty = heights, None, True
        hit = self.m_cache.spoil(box if box is not None else (0.0, 0.0, self.m_size_tiles, self.m_size_tiles))
        for node in list(self.m_models) if box is None else hit:
            model = self.m_models.pop(node, None)
            if model is not None:
                model.release()

    def nodes(self, eye, visible=None) -> list[CTerrainModel]:
        """The models of the nodes ``terrainnodes.select`` picks for ``eye`` (the island's tiles), made where
        missing (on the CPU: their resources come with their pass); a node whose slice was given away goes."""
        if self.m_heights is None:
            return []
        visible_models = []
        for node in terrainnodes.select(self.m_size_tiles, eye, SLICES, visible=visible):
            layer = self.m_cache.get(node)
            kind = self._kind(node)
            model = self.m_models.get(node)
            if model is not None and model.m_kind != kind:
                model.release()
                model = None
            if model is None:
                model = self.m_models[node] = self._make(node, layer, kind)
            visible_models.append(model)
        for node in self.m_cache.m_dropped:
            model = self.m_models.pop(node, None)
            if model is not None and all(model is not other for other in visible_models):
                model.release()
        self.m_cache.m_dropped.clear()
        self._m_visible = visible
        return visible_models

    def shown(self, models: list[CTerrainModel]) -> list[CTerrainModel]:
        """What draws for this frame's ``models`` (``nodes``) once the frame's bake is recorded: a node not baked yet
        stands in as the nodes drawn before over its square (``terrainnodes.stand_ins``), kept in the slice cache."""
        pages = self.pages_on_device()

        def ready(node) -> bool:  # baked, and its model's page still on the device
            model = self.m_models.get(node)
            page = model.m_kind[1] if model is not None else None
            current = page is None or (page in pages and pages[page] is model.m_kind[2])
            return model is not None and current and node in self.m_cache.m_slices and node not in self.m_cache.m_fresh

        wanted = {candidate.m_node: candidate for candidate in models}
        nodes = terrainnodes.stand_ins(list(wanted), ready, self._m_drawn, self.m_size_tiles, self._m_visible)
        for node in nodes:
            if node not in wanted:  # standing in: not given away before its replacement is baked
                self.m_cache.m_slices.move_to_end(node)
        self._m_drawn = set(nodes)
        return [wanted[member] if member in wanted else self.m_models[member] for member in nodes]

    def pages_on_device(self) -> dict:
        """The pages a node's kind names (its second item), by key: its third item is the page's entry here (the
        entry itself, held: the ``id`` of a page let go is given again to one made later)."""
        return self.m_pages.m_ready if self.m_pages is not None else {}

    def casters(self, models: list[CTerrainModel]) -> list[CTerrainModel]:
        """The nodes drawn into the sun's cascades by the terrain's shadow technique: per node model a caster that
        draws its vertices with its constants and textures (the tessellated technique for a tessellated node)."""
        if self.m_shadow is None:
            return []
        result = []
        for model in models:
            caster = self._m_casters.get(model.m_node)
            if caster is None or caster.m_share is not model:
                if caster is not None:
                    caster.release()
                program = self.m_shadow[1] if model.m_program is self.m_detailed else self.m_shadow[0]
                if program is None:
                    self._m_casters.pop(model.m_node, None)
                    continue
                owner = SOwner(self.m_rhi, model.m_textures, self.m_common, self.m_slots)
                caster = CTerrainModel(
                    program, model.m_mesh, model.m_node_values, owner, model.m_own, model.m_slot, share=model
                )
                caster.m_node, caster.m_kind = model.m_node, model.m_kind
                self._m_casters[model.m_node] = caster
            result.append(caster)
        shown = {model.m_node for model in models}
        for node in [
            caster_node
            for caster_node, entry in self._m_casters.items()
            if caster_node not in shown and entry.m_share is not self.m_models.get(caster_node)
        ]:
            self._m_casters.pop(node).release()
        return result

    def _edited(self, node: SNode) -> bool:
        if self.m_edited is None:
            return False
        block_tiles = 2**terrainnodes.MIN_LEVEL
        first_column, first_row, block_count = (
            int(node.x // block_tiles),
            int(node.z // block_tiles),
            max(1, int(node.tiles // block_tiles)),
        )
        return bool(self.m_edited[first_row : first_row + block_count, first_column : first_column + block_count].any())

    def _kind(self, node: SNode) -> tuple:
        """How a node draws: ("detailed", its page, ..) when it is near and its page is rebuilt (asked for when not),
        ("stored", the page for its normals or None, ..) when the island stores its mesh, else ("grid", None, ..):
        the view's heights; the last item tells the page's textures apart (a page rebuilt makes the model again)."""
        island_source = self.m_source
        if island_source is None or not self.m_files_shown or self._edited(node):
            return ("grid", None, 0)
        ready = self.m_pages.m_ready if self.m_pages is not None else {}
        page = terrainpages.finest_page(island_source.pages, node.level, node.x, node.z)
        if (
            self.m_detailed is not None
            and self.m_pages is not None
            and page is not None
            and node.level <= DETAILED_LEVEL
        ):
            self.m_pages.want(page, island_source.pages[page])
            if page in ready:
                return ("detailed", page, ready[page])
        tiles = 2**node.level
        if (int(node.x // tiles), int(node.z // tiles)) in island_source.meshes.get(node.level, {}):
            return ("stored", page, ready[page]) if page in ready else ("stored", None, 0)
        return ("grid", None, 0)

    def normals(self) -> np.ndarray:
        """The vertex normals of the heights (``vertex_normals``), made once per height change."""
        if self._m_normals is None:
            self._m_normals = vertex_normals(self.m_heights, self.m_step_tiles)
        return self._m_normals

    def _make(self, node: SNode, layer: int, kind: tuple) -> CTerrainModel:
        how, page, _ = kind
        normals = self.normals()
        program, own = self.m_program, {}
        rect = (node.x, node.z, node.tiles, node.tiles)
        if how == "stored":
            tiles = 2**node.level
            vertices, indices = self.m_source.meshes[node.level][(int(node.x // tiles), int(node.z // tiles))]
            mesh = gamedraw.CMesh(vertices, STRIDE, ELEMENTS, indices, len(vertices) // STRIDE)
            rect = mesh_rect(vertices)
        else:
            _col, _row, cols, rows = node.samples(self.m_step_tiles, self.m_width_samples)
            data = encode_vertices(self.m_heights, self.m_step_tiles, node, self.m_width_samples, normals)
            if how == "detailed":
                data, indices = with_skirts(data, cols, rows)
                indices = indices.reshape(-1, 4)[:, PATCH_CORNERS].ravel()  # the tessellator's triangles face up
                mesh, program = gamedraw.CMesh(data, STRIDE, ELEMENTS, indices, len(data) // STRIDE), self.m_detailed
            else:  # a skirt that covers the steps to the tessellated and stored nodes beside it [ours]
                data, quads = with_skirts(data, cols, rows, GRID_SKIRT)
                mesh = gamedraw.CMesh(data, STRIDE, ELEMENTS, quad_triangles(quads), len(data) // STRIDE)
        if page is not None:
            height, normal = self.m_pages.m_ready[page]
            own = {"cNodeHeightMap": height, "cNodeNormalMap": normal, "cTerrainHeightTex": self.m_pages.m_coarse}
        params = terrainpages.page_params(page, self.m_size_tiles) if page is not None else (0.0, 0.0, 1.0)
        tint = self._tint(node)
        if tint is not None:
            own["cNodeTintMap"] = tint[0]
        values = {**self.m_island, **node_values(node.level, layer, params, page[0] if page is not None else 0,
                                               tint[1] if tint is not None else None)}  # fmt: skip
        owner = SOwner(self.m_rhi, self.m_textures, self.m_common, self.m_slots)
        model = CTerrainModel(program, mesh, values, owner, own, layer)
        model.m_node, model.m_kind, model.m_rect = node, kind, rect
        return model

    def _tint(self, node: SNode):
        """The node's tint page (the finest that holds it whole: as its height page [ours]) and its page parameters,
        read and made on first use (uploaded with ``uploads``); None for an island without tint pages."""
        from ..models import ddsdecode

        island_source = self.m_source
        key = (
            terrainpages.finest_page(island_source.tints, node.level, node.x, node.z)
            if island_source is not None and island_source.read
            else None
        )
        if key is None:
            return None
        if key not in self.m_tints:
            try:
                dds = ddsdecode.CDdsFile(island_source.read(island_source.tints[key]))
            except (ddsdecode.CUnsupportedDds, KeyError, FileNotFoundError):
                self.m_tints[key] = None
            else:
                mip_flag = QRhiTexture.Flag.MipMapped if dds.m_info.mips > 1 else QRhiTexture.Flag(0)
                texture = self.m_rhi.newTexture(gametextures.BLOCK_FORMATS.get(dds.m_info.format, TextureFormat.BC1),
                                          QSize(dds.m_info.width_px, dds.m_info.height_px), 1, mip_flag)  # fmt: skip
                texture.create()
                self._m_tint_uploads.append((texture, gametextures.stored_levels(dds)))
                self.m_tints[key] = texture
        texture = self.m_tints[key]
        return (texture, terrainpages.page_params(key, self.m_size_tiles)) if texture is not None else None

    def uploads(self, batch) -> None:
        """Before the nodes draw (``batch`` of a pass ahead of the G-buffer's): the normal page when the heights
        changed, written into the same texture so that the nodes' bindings stay; the first time also the inputs
        the owner does not fill: gloss 0 (no highlight), the material height 0.5 (the middle of the shader's -2..2:
        no displacement), one slice each (the shader clamps the array index), and the session maps of ``ZERO``."""
        if not self.m_keep:
            gloss = upload(self.m_rhi, batch, TextureFormat.RGBA8, (4, 4), [bytes(64)])
            height = upload(self.m_rhi, batch, TextureFormat.RGBA16F, (4, 4), [np.full(64, 0.5, np.float16).tobytes()])
            black = upload(self.m_rhi, batch, TextureFormat.RGBA8, (4, 4), [bytes(64)], is_array=False)
            self.m_keep += [gloss, height, black]
            self.m_stand_ins = {"cBakedSpecTexArray": gloss, "cBakedHeightTexArray": height,
                              "cBakedGranularityTexArray": height, **dict.fromkeys(ZERO, black)}  # fmt: skip
        for name, texture in self.m_stand_ins.items():  # where the game's bake does not hand its own
            self.m_textures.setdefault(name, texture)
        for texture, levels in self._m_tint_uploads:  # the tint pages the nodes made this frame read (BC1, linear)
            description = QRhiTextureUploadDescription()
            description.setEntries([QRhiTextureUploadEntry(0, k, QRhiTextureSubresourceUploadDescription(raw))
                             for k, raw in enumerate(levels)])  # fmt: skip
            batch.uploadTexture(texture, description)
        self._m_tint_uploads.clear()
        if self.m_heights is None or not self._m_page_dirty:
            return
        page = normal_page(self.m_heights, self.m_step_tiles)
        if self.m_page is None:
            self.m_page = upload(
                self.m_rhi, batch, TextureFormat.RG8, (page.shape[1], page.shape[0]), [page.tobytes()], False
            )
            self.m_textures["cNodeNormalMap"] = self.m_page
        else:
            gamedraw.upload_level(batch, self.m_page, page.tobytes())
        self._m_page_dirty = False

    def release(self) -> None:
        for model in (*self._m_casters.values(), *self.m_models.values()):
            model.release()
        self._m_casters = {}
        for texture in (*self.m_keep, self.m_page, *self.m_tints.values()):
            if texture is not None:
                texture.destroy()
        self.m_tints, self._m_tint_uploads = {}, []
        self.m_models, self.m_keep, self.m_page, self.m_stand_ins, self._m_drawn = {}, [], None, {}, set()
        self.m_common.release()
        self.m_slots.release()
        self.m_cache.clear()
        self.m_cache.m_dropped.clear()
        self._m_page_dirty = True
