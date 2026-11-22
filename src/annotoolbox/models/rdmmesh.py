"""An ``.rdm`` mesh file as numpy arrays for drawing: positions, unit normals, the first UV set, the joints and weights
of a skin, and the triangles per material; its skeletons and animations as they are read (``formats/rdmread``).

``read_meshes(data)`` is the entry; ``groups`` keeps the file's own material numbers (a submesh names one), which a
config maps to its materials (``models.configtree.material_lods``). Vertex elements: docs/formats/rdm.md "Vertices".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..formats.rdmread import SMesh, SRdmFile, read_rdm

POSITION, NORMAL, UV, WEIGHTS, JOINTS = 0, 1, 4, 6, 7  # the element usages (PNGBTCWI)
_TYPES = {6: "<f2", 7: "<f4", 2: "<u2", 3: "<i2", 5: "u1"}


@dataclass
class SMeshData:
    """One mesh: ``vertices`` [n, 8] (position, normal, uv), the skin (``joints`` [n, 4] indices into its skeleton,
    ``weights`` [n, 4] summing to 1; None for a rigid mesh), ``groups`` material -> triangle indices [m] (u4)."""

    name: str
    vertices: np.ndarray
    groups: dict[int, np.ndarray]
    skeleton: int | None = None
    joints: np.ndarray | None = None
    weights: np.ndarray | None = None
    bones: int = 0
    stored: SMesh | None = None  # the file's mesh it was read from (its vertices as stored, for the game's shaders)

    @property
    def skinned(self) -> bool:
        return self.joints is not None


@dataclass
class SMeshFile:
    """A file's meshes and what else it holds (its skeletons and animations)."""

    meshes: list[SMeshData] = field(default_factory=list)
    rdm: SRdmFile | None = None

    def materials(self) -> list[int]:
        """Every material number a submesh names, in order."""
        return sorted({k for mesh in self.meshes for k in mesh.groups})


def _element(mesh: SMesh, usage: int, kinds=(5, 6, 7)) -> np.ndarray | None:
    """The values of the first element of a usage, [n, count] as floats (a packed vector as its four bytes)."""
    offset_bytes = 0
    for element in mesh.elements:
        if element.usage == usage and element.type in kinds:
            raw = np.frombuffer(mesh.vertices, np.uint8).reshape(mesh.vertex_count, mesh.stride)[
                :, offset_bytes : offset_bytes + element.size
            ]
            count = 4 if element.type == 5 else element.count
            return (
                np.ascontiguousarray(raw)
                .view(_TYPES[element.type])
                .reshape(mesh.vertex_count, count)
                .astype(np.float32)
            )
        offset_bytes += element.size
    return None


def _normals(mesh: SMesh) -> np.ndarray:
    raw = _element(mesh, NORMAL)
    if raw is None:
        return np.tile(np.array([[0.0, 1.0, 0.0]], np.float32), (mesh.vertex_count, 1))
    packed = next(e.type == 5 for e in mesh.elements if e.usage == NORMAL)
    unpacked = raw[:, :3] / 127.5 - 1.0 if packed else raw[:, :3]
    length = np.linalg.norm(unpacked, axis=1, keepdims=True)
    return np.where(length > 1e-9, unpacked / np.maximum(length, 1e-9), np.array([0.0, 1.0, 0.0], np.float32))


def _skin(mesh: SMesh) -> tuple[np.ndarray, np.ndarray] | None:
    """Joints and weights: the bytes of ``I4b`` and ``W4b``; one bone a vertex takes its first joint whole, three or
    four divide their weights by the sum (docs/engine-animation.md "Skinned meshes")."""
    joints = _element(mesh, JOINTS, (5,))
    if joints is None:
        return None
    weights = _element(mesh, WEIGHTS, (5,))
    count = max(1, min(4, mesh.bones_per_vertex or (4 if weights is not None else 1)))
    skin_weights = np.zeros((mesh.vertex_count, 4), np.float32)
    if weights is None or count == 1:
        skin_weights[:, 0] = 1.0
    else:
        skin_weights[:, :count] = weights[:, :count]
        total = skin_weights.sum(axis=1, keepdims=True)
        skin_weights = np.where(total > 0, skin_weights / np.maximum(total, 1e-9), np.array([1.0, 0, 0, 0], np.float32))
    return joints.astype(np.int64), skin_weights


def mesh_data(mesh: SMesh) -> SMeshData:
    """One mesh of a file as arrays."""
    positions = _element(mesh, POSITION, (6, 7))
    if positions is None:
        raise ValueError(f"{mesh.name}: no positions")
    uv = _element(mesh, UV, (6, 7))
    uv = np.zeros((mesh.vertex_count, 2), np.float32) if uv is None else uv[:, :2]
    vertices = np.concatenate([positions[:, :3], _normals(mesh), uv], axis=1).astype(np.float32)
    indices = np.asarray(mesh.indices, np.uint32)
    groups: dict[int, list[np.ndarray]] = {}
    for submesh in mesh.submeshes or []:
        groups.setdefault(submesh.material, []).append(
            indices[submesh.first_index : submesh.first_index + submesh.index_count]
        )
    merged = {material: np.concatenate(arrays) for material, arrays in groups.items()} if groups else {0: indices}
    skin = _skin(mesh)
    return SMeshData(
        mesh.name,
        vertices,
        merged,
        mesh.skeleton,
        *(skin if skin else (None, None)),
        mesh.bones_per_vertex if skin else 0,
        mesh,
    )


def read_meshes(data: bytes) -> SMeshFile:
    """Every mesh with geometry of an ``.rdm``, and the file as read; ``CRdmReadError`` for one that does not read."""
    rdm = read_rdm(data)
    meshes = [
        mesh_data(mesh)
        for mesh in rdm.meshes
        if mesh.vertex_count and mesh.indices and _element(mesh, POSITION, (6, 7)) is not None
    ]
    return SMeshFile(meshes, rdm)
