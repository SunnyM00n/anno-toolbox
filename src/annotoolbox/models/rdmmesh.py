"""An ``.rdm`` mesh file as numpy arrays for drawing: positions, unit normals, the first UV set, the joints and weights
of a skin, and the triangles per material; its skeletons and animations as they are read (``formats/rdmread``).

``read_meshes(data)`` is the entry; ``groups`` keeps the file's own material numbers (a submesh names one), which a
config maps to its materials (``models.configtree.material_lods``). Vertex elements: docs/formats/rdm.md "Vertices".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..formats.rdmread import Mesh, RdmFile, read_rdm

POSITION, NORMAL, UV, WEIGHTS, JOINTS = 0, 1, 4, 6, 7  # the element usages (PNGBTCWI)
_TYPES = {6: "<f2", 7: "<f4", 2: "<u2", 3: "<i2", 5: "u1"}


@dataclass
class MeshData:
    """One mesh: ``vertices`` [n, 8] (position, normal, uv), the skin (``joints`` [n, 4] indices into its skeleton,
    ``weights`` [n, 4] summing to 1; None for a rigid mesh), ``groups`` material -> triangle indices [m] (u4)."""

    name: str
    vertices: np.ndarray
    groups: dict[int, np.ndarray]
    skeleton: int | None = None
    joints: np.ndarray | None = None
    weights: np.ndarray | None = None
    bones: int = 0
    stored: Mesh | None = None  # the file's mesh it was read from (its vertices as stored, for the game's shaders)

    @property
    def skinned(self) -> bool:
        return self.joints is not None


@dataclass
class MeshFile:
    """A file's meshes and what else it holds (its skeletons and animations)."""

    meshes: list[MeshData] = field(default_factory=list)
    rdm: RdmFile | None = None

    def materials(self) -> list[int]:
        """Every material number a submesh names, in order."""
        return sorted({k for m in self.meshes for k in m.groups})


def _element(m: Mesh, usage: int, kinds=(5, 6, 7)) -> np.ndarray | None:
    """The values of the first element of a usage, [n, count] as floats (a packed vector as its four bytes)."""
    at = 0
    for e in m.elements:
        if e.usage == usage and e.type in kinds:
            raw = np.frombuffer(m.vertices, np.uint8).reshape(m.vertex_count, m.stride)[:, at : at + e.size]
            count = 4 if e.type == 5 else e.count
            return np.ascontiguousarray(raw).view(_TYPES[e.type]).reshape(m.vertex_count, count).astype(np.float32)
        at += e.size
    return None


def _normals(m: Mesh) -> np.ndarray:
    raw = _element(m, NORMAL)
    if raw is None:
        return np.tile(np.array([[0.0, 1.0, 0.0]], np.float32), (m.vertex_count, 1))
    packed = next(e.type == 5 for e in m.elements if e.usage == NORMAL)
    n = raw[:, :3] / 127.5 - 1.0 if packed else raw[:, :3]
    length = np.linalg.norm(n, axis=1, keepdims=True)
    return np.where(length > 1e-9, n / np.maximum(length, 1e-9), np.array([0.0, 1.0, 0.0], np.float32))


def _skin(m: Mesh) -> tuple[np.ndarray, np.ndarray] | None:
    """Joints and weights: the bytes of ``I4b`` and ``W4b``; one bone a vertex takes its first joint whole, three or
    four divide their weights by the sum (docs/engine-animation.md "Skinned meshes")."""
    joints = _element(m, JOINTS, (5,))
    if joints is None:
        return None
    weights = _element(m, WEIGHTS, (5,))
    count = max(1, min(4, m.bones_per_vertex or (4 if weights is not None else 1)))
    w = np.zeros((m.vertex_count, 4), np.float32)
    if weights is None or count == 1:
        w[:, 0] = 1.0
    else:
        w[:, :count] = weights[:, :count]
        total = w.sum(axis=1, keepdims=True)
        w = np.where(total > 0, w / np.maximum(total, 1e-9), np.array([1.0, 0, 0, 0], np.float32))
    return joints.astype(np.int64), w


def mesh_data(m: Mesh) -> MeshData:
    """One mesh of a file as arrays."""
    pos = _element(m, POSITION, (6, 7))
    if pos is None:
        raise ValueError(f"{m.name}: no positions")
    uv = _element(m, UV, (6, 7))
    uv = np.zeros((m.vertex_count, 2), np.float32) if uv is None else uv[:, :2]
    vertices = np.concatenate([pos[:, :3], _normals(m), uv], axis=1).astype(np.float32)
    indices = np.asarray(m.indices, np.uint32)
    groups: dict[int, list[np.ndarray]] = {}
    for s in m.submeshes or []:
        groups.setdefault(s.material, []).append(indices[s.first_index : s.first_index + s.index_count])
    merged = {k: np.concatenate(v) for k, v in groups.items()} if groups else {0: indices}
    skin = _skin(m)
    return MeshData(
        m.name, vertices, merged, m.skeleton, *(skin if skin else (None, None)), m.bones_per_vertex if skin else 0, m
    )


def read_meshes(data: bytes) -> MeshFile:
    """Every mesh with geometry of an ``.rdm``, and the file as read; ``RdmReadError`` for one that does not read."""
    rdm = read_rdm(data)
    meshes = [
        mesh_data(m) for m in rdm.meshes if m.vertex_count and m.indices and _element(m, POSITION, (6, 7)) is not None
    ]
    return MeshFile(meshes, rdm)
