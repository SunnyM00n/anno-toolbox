"""Anno 117's distance clusters for the island view, without a GPU: a model config's far-view model (the
``LowPolyData`` of its baked data file, ``.bfg``) read, its tiles and materials placed in the clusters' atlas and
material records, and a region's instances merged into one mesh in the cluster shader's vertex format
(``distance_cluster.fx``). ``clustersnative`` draws them.

``read_far_view(data)`` reads a ``.bfg``; ``CClusterAtlas.add`` places a far-view model's tiles and materials;
``merge(pieces)`` builds a region's mesh. The format and the rules: docs/rendering/distance-clusters.md.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from ..formats import filedb

ATLAS_SIDE = 2048  # DCLUSTER_ATLAS_WIDTH and _HEIGHT, as the engine compiles the effect (``shaderdefines``)
PARTITION_SIDE = 32  # DCLUSTER_ATLAS_PARTITION_SIZE: a tile's texels
PARTITIONS_X = ATLAS_SIDE // PARTITION_SIDE
MAX_PARTITIONS = PARTITIONS_X * PARTITIONS_X
MAX_MATERIALS = 1 << 14  # the vertex's 14 bits of material index
MIP_BLOCKS = (8, 4, 2, 1)  # a tile's blocks a side per mip: 85 blocks, 32 down to 4 texels
TILE_BLOCKS = sum(side * side for side in MIP_BLOCKS)
BC3_BYTES, BC1_BYTES = 16, 8
HAS_MODELS, HAS_DECALS, FULL_MODELS, EXCLUDED = 1, 4, 8, 16  # LowPolyData ``Flags``
DEFAULT_GLOSS = 1.0  # a material without ``GlossFactor`` [I]: the gloss map's value as it is (section 5 check)
VERTEX = np.dtype([("position", "<f4", 3), ("uv", "<u2", 2), ("material", "<u4")])
_SOLID_TILES: dict[str, bytes] = {}  # the packer's solid tiles of a set that has none (``_solid``)


class CFarViewError(ValueError):
    """A ``.bfg`` whose far-view model does not read."""


@dataclass(frozen=True)
class SFarMaterial:
    """One material of a far-view model: its ``Type`` (0 standard, 1 tree, 2 decal, 3 field plant), ``Color`` (gamma,
    0..2), ``GlossFactor`` and ``TexSetIdx``."""

    Type: int
    Color: tuple[float, float, float]
    GlossFactor: float
    TexSetIdx: int


# a vertex's material where the model lists none for it [I]: standard shading, white, the first texture set (the
# engine's own choice is open: distance-clusters.md section 5)
STAND_IN = SFarMaterial(0, (1.0, 1.0, 1.0), DEFAULT_GLOSS, 0)


@dataclass(frozen=True)
class SFarView:
    """A config's far-view model in its own space: per vertex the position, the UV over its texture set's tile (0..1)
    and the material; triangles; per texture set its two tiles (BC3 diffuse and alpha, BC1 gloss and metal, 85
    blocks each)."""

    positions: np.ndarray  # [n, 3] float32
    uvs: np.ndarray  # [n, 2] float32
    materials: np.ndarray  # [n] int: the material of each vertex
    triangles: np.ndarray  # [m, 3] int
    surfaces: tuple[SFarMaterial, ...]
    tiles: tuple[tuple[bytes, bytes], ...]
    Flags: int


def _attrib(tag, name: str) -> bytes | None:
    found = tag.find(name)
    return found.data if isinstance(found, filedb.SAttrib) else None


def _number(tag, name: str, kind: str, default):
    data = _attrib(tag, name)
    if data is None:
        return default
    size = struct.calcsize(f"<{kind}")
    if len(data) < size:
        raise CFarViewError(f"{name} holds {len(data)} bytes")
    return struct.unpack_from(f"<{kind}", data)[0]


def _entries(tag, name: str) -> list:
    found = tag.find(name)
    return (
        [child for child in found.children if isinstance(child, filedb.STag)] if isinstance(found, filedb.STag) else []
    )


def _solid(kind: str) -> bytes:
    """A tile of the packer's stand-ins for missing maps (diffuse 0.5 grey and opaque; gloss and metal 0)."""
    if kind not in _SOLID_TILES:
        if kind == "diff_alpha":  # BC4 alpha 255 (index 0), BC1 colour 0.5 grey as the packer rounds it (16, 32, 16)
            block = bytes((255, 255, 0, 0, 0, 0, 0, 0)) + struct.pack("<HHI", 0x8410, 0x8410, 0)
        else:
            block = bytes(BC1_BYTES)
        _SOLID_TILES[kind] = block * TILE_BLOCKS
    return _SOLID_TILES[kind]


def read_far_view(data: bytes) -> SFarView | None:
    """The far-view model of a ``.bfg`` (``CFarViewError`` on one that does not read); None where the config has
    none to merge: no ``LowPolyData`` geometry, or its flags say "draw the full models" or "kept out"."""
    try:
        root = filedb.read(data)
    except (filedb.CFileDBError, ValueError, IndexError, struct.error) as exc:
        raise CFarViewError(f"not a baked data file: {exc}") from exc
    low = next((tag for tag in root.iter() if isinstance(tag, filedb.STag) and tag.name == "LowPolyData"), None)
    if low is None:
        return None
    flags = _number(low, "Flags", "I", 0)
    vertices, indices = _attrib(low, "Vertices") or b"", _attrib(low, "Indices") or b""
    if not flags & (HAS_MODELS | HAS_DECALS) or flags & (FULL_MODELS | EXCLUDED) or not vertices or not indices:
        return None
    if len(vertices) % VERTEX.itemsize or len(indices) % 6:
        raise CFarViewError(f"{len(vertices)} bytes of vertices, {len(indices)} of indices")
    rows = np.frombuffer(vertices, VERTEX)
    triangles = np.frombuffer(indices, "<u2").astype(np.int64).reshape(-1, 3)
    if triangles.max() >= len(rows):
        raise CFarViewError("an index past the vertices")
    tiles = tuple(
        (
            _tile(_attrib(entry, "DiffAlphaData"), BC3_BYTES, "diff_alpha"),
            _tile(_attrib(entry, "GlossMetalData"), BC1_BYTES, "gloss_metal"),
        )
        for entry in _entries(low, "TextureSets")
    )
    surfaces = tuple(_surface(entry, len(tiles)) for entry in _entries(low, "Materials"))
    materials = (rows["material"] & 0xFFFF).astype(np.int64)  # a few files use the high bits
    if materials.max() >= len(surfaces):  # 0xFFFF and no Materials (Egypt's water plants): the stand-in
        if not tiles:
            raise CFarViewError("a vertex names a material the model lacks, and it has no texture set")
        materials = np.where(materials >= len(surfaces), len(surfaces), materials)
        surfaces += (STAND_IN,)
    uvs = rows["uv"].astype(np.float32) / 65535.0
    return SFarView(np.array(rows["position"], np.float32), uvs, materials, triangles, surfaces, tiles, flags)


def _tile(data: bytes | None, block_bytes: int, kind: str) -> bytes:
    if data is None:
        return _solid(kind)
    if len(data) != TILE_BLOCKS * block_bytes:
        raise CFarViewError(f"a tile of {len(data)} bytes")
    return data


def _surface(entry, tile_count: int) -> SFarMaterial:
    color = _attrib(entry, "Color")
    if color is None or len(color) < 12:
        raise CFarViewError("a material without its colour")
    texture_set = _number(entry, "TexSetIdx", "I", 0)
    if texture_set >= tile_count:
        raise CFarViewError("a material names a texture set the model lacks")
    kind = _number(entry, "Type", "B", 0)
    return SFarMaterial(kind, struct.unpack_from("<3f", color), float(_number(entry, "GlossFactor", "f", DEFAULT_GLOSS)),
                        texture_set)  # fmt: skip


def material_record(kind: int, partition: int, color, gloss: float) -> bytes:
    """A ``DClusterMaterial``: type and atlas partition, the colour as R11G11B10 over 0..2, the gloss over 0..2."""
    red, green, blue = (float(np.clip(channel / 2.0, 0.0, 1.0)) for channel in color)
    packed_color = round(red * 2047) | round(green * 2047) << 11 | round(blue * 1023) << 22
    params = round(float(np.clip(gloss / 2.0, 0.0, 1.0)) * 255)
    return struct.pack("<4I", (kind & 0xF) | (partition & 0xFFFF) << 4, packed_color, params, 0)


class CClusterAtlas:
    """The clusters' two atlases on the CPU (BC3 diffuse and alpha, BC1 gloss and metal, four mips each) and the
    material records: ``add`` places a far-view model's tiles (each distinct tile once) and materials; ``serial``
    counts the changes the device copy follows."""

    def __init__(self) -> None:
        self.m_diff_alpha = [
            np.zeros((PARTITIONS_X * side, PARTITIONS_X * side, BC3_BYTES), np.uint8) for side in MIP_BLOCKS
        ]
        self.m_gloss_metal = [
            np.zeros((PARTITIONS_X * side, PARTITIONS_X * side, BC1_BYTES), np.uint8) for side in MIP_BLOCKS
        ]
        self.m_partitions: dict[tuple[bytes, bytes], int] = {}
        self.m_records = bytearray()
        self._m_materials: dict[bytes, int] = {}
        self.m_serial = 0

    def add(self, far: SFarView) -> np.ndarray | None:
        """The material indices of ``far``'s materials in the records, its tiles in the atlas; None when the atlas or
        the records are full (the model then stays individual)."""
        new_tiles = {tile for tile in far.tiles if tile not in self.m_partitions}
        if len(self.m_partitions) + len(new_tiles) > MAX_PARTITIONS:
            return None
        records = [material_record(surface.Type, 0, surface.Color, surface.GlossFactor) for surface in far.surfaces]
        if len(self._m_materials) + len(records) > MAX_MATERIALS:
            return None
        for tile in far.tiles:
            if tile not in self.m_partitions:
                self._place(tile, len(self.m_partitions))
        out = []
        for surface in far.surfaces:
            partition = self.m_partitions[far.tiles[surface.TexSetIdx]]
            record = material_record(surface.Type, partition, surface.Color, surface.GlossFactor)
            index = self._m_materials.get(record)
            if index is None:
                index = self._m_materials[record] = len(self._m_materials)
                self.m_records += record
            out.append(index)
        self.m_serial += 1
        return np.asarray(out, np.int64)

    def _place(self, tile: tuple[bytes, bytes], partition: int) -> None:
        self.m_partitions[tile] = partition
        row, column = divmod(partition, PARTITIONS_X)
        for levels, data, block_bytes in (
            (self.m_diff_alpha, tile[0], BC3_BYTES),
            (self.m_gloss_metal, tile[1], BC1_BYTES),
        ):
            at = 0
            for level, side in zip(levels, MIP_BLOCKS, strict=True):
                blocks = np.frombuffer(data, np.uint8, side * side * block_bytes, at).reshape(side, side, block_bytes)
                level[row * side : (row + 1) * side, column * side : (column + 1) * side] = blocks
                at += side * side * block_bytes

    def levels(self, kind: str) -> list[tuple[bytes, int]]:
        """The mips of one atlas (``diff_alpha`` or ``gloss_metal``) as (bytes, row pitch), finest first."""
        levels = self.m_diff_alpha if kind == "diff_alpha" else self.m_gloss_metal
        return [(level.tobytes(), level.shape[1] * level.shape[2]) for level in levels]


@dataclass(frozen=True)
class SPiece:
    """One far-view model at many places: its model, the instances' matrices ([k, 4, 4], column vectors, the view's
    space) and its materials' indices in the records (``CClusterAtlas.add``)."""

    far: SFarView
    matrices: np.ndarray
    materials: np.ndarray


@dataclass(frozen=True)
class SClusterMesh:
    """A region's merged mesh: its vertices (two words each, ``distance_cluster.fx`` ``VSInDistanceCluster``), its
    triangles' indices (uint32), the box's centre and half extent (``cClusterPositionWS``, ``cClusterScaleWS``)."""

    vertices: bytes
    indices: bytes
    centre: tuple[float, float, float]
    scale: tuple[float, float, float]
    triangle_count: int

    @property
    def radius(self) -> float:
        return float(np.linalg.norm(self.scale))


WHITE = (32, 32, 16)  # the vertex colour 1.0 (``UnpackVertexColor``: 6, 6 and 5 bits over 0..2)


def merge(pieces: list[SPiece]) -> SClusterMesh | None:
    """The pieces' instances as one mesh, positions quantised into its box as the shader unpacks them (8, 6 and 8
    bits), UVs to the tile's 32 steps; None when nothing is left (every instance hidden by a scale of 0)."""
    positions, uvs, materials, triangles, base = [], [], [], [], 0
    for piece in pieces:
        matrices = np.asarray(piece.matrices, np.float32)
        shown = np.abs(np.linalg.det(matrices[:, :3, :3])) > 1e-12
        matrices = matrices[shown]
        if not len(matrices):
            continue
        far = piece.far
        world = np.einsum("kij,nj->kni", matrices[:, :3, :3], far.positions) + matrices[:, None, :3, 3]
        positions.append(world.reshape(-1, 3))
        uvs.append(np.tile(far.uvs, (len(matrices), 1)))
        materials.append(np.tile(piece.materials[far.materials], len(matrices)))
        offsets = base + np.arange(len(matrices))[:, None, None] * len(far.positions)
        triangles.append((far.triangles[None] + offsets).reshape(-1, 3))
        base += len(matrices) * len(far.positions)
    if not positions:
        return None
    every = np.concatenate(positions)
    low, high = every.min(axis=0), every.max(axis=0)
    centre, scale = (low + high) / 2, np.maximum((high - low) / 2, 1e-3)
    steps = np.array([255, 63, 255])
    quantised = np.rint(np.clip((every - centre) / scale * 0.5 + 0.5, 0, 1) * steps).astype(np.uint32)
    texel = np.rint(np.clip(np.concatenate(uvs), 0, 1) * 31).astype(np.uint32)
    packed0 = quantised[:, 0] | quantised[:, 1] << 8 | quantised[:, 2] << 14 | texel[:, 0] << 22 | texel[:, 1] << 27
    material = np.concatenate(materials).astype(np.uint32) & (MAX_MATERIALS - 1)
    packed1 = np.uint32(WHITE[0] | WHITE[1] << 6 | WHITE[2] << 13) | material << 18
    words = np.stack([packed0, packed1], axis=1).astype("<u4")
    indices = np.concatenate(triangles).astype("<u4")
    return SClusterMesh(words.tobytes(), indices.tobytes(), tuple(map(float, centre)), tuple(map(float, scale)),
                        len(indices))  # fmt: skip


def region_of(centres: np.ndarray, cell_m: float) -> np.ndarray:
    """[n, 2] the region cell (x, z) of each centre ([n, 3], the view's space)."""
    return np.floor(np.asarray(centres, np.float64)[:, [0, 2]] / cell_m).astype(np.int64)


def cluster_record(mesh: SClusterMesh, alpha: float) -> bytes:
    """``cbDistanceCluster``: the box's centre, the fade (``cClusterAlpha``), its half extent; 32 bytes."""
    return struct.pack("<8f", *mesh.centre, alpha, *mesh.scale, 0.0)
