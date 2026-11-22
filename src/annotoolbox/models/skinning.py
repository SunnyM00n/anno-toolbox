"""A skinned mesh posed by an animation, on the CPU: the joints' matrices at a time and the vertices moved by them.

A joint stores its inverse bind pose, an animation key the joint's local transform, tracks find their joint by name
and a joint without a track keeps its bind pose (docs/formats/rdm.md "Skeletons and animations",
docs/engine-animation.md). ``CRig(skeleton)`` holds a skeleton, ``CRig.pose(animation, seconds)`` gives the skin
matrices (``CRig.world`` the joints' own), ``skin(mesh, matrices)`` the posed vertices; ``clip_time`` maps a clip's
``Scale`` and ``LoopCount`` onto the time.
"""

from __future__ import annotations

import numpy as np

from ..formats.rdmread import SAnimation, SSkeleton
from .configtree import fnv1a32
from .rdmmesh import SMeshData


def quat_matrix(quaternion) -> np.ndarray:
    """The 3x3 rotation of a unit quaternion (x, y, z, w), for column vectors."""
    x, y, z, w = (float(value) for value in quaternion)
    squared_length = x * x + y * y + z * z + w * w
    if squared_length < 1e-12:
        return np.identity(3)
    scale = 2.0 / squared_length
    return np.array([
        [1 - scale * (y * y + z * z), scale * (x * y - z * w), scale * (x * z + y * w)],
        [scale * (x * y + z * w), 1 - scale * (x * x + z * z), scale * (y * z - x * w)],
        [scale * (x * z - y * w), scale * (y * z + x * w), 1 - scale * (x * x + y * y)],
    ])  # fmt: skip


def matrix_quat(matrix: np.ndarray) -> tuple[float, float, float, float]:
    """The unit quaternion (x, y, z, w) of a 3x3 rotation for column vectors, w not negative."""
    matrix = np.asarray(matrix, float)
    trace = matrix[0, 0] + matrix[1, 1] + matrix[2, 2]
    if trace > 0:
        scale = 2.0 * np.sqrt(trace + 1.0)
        quaternion = (
            (matrix[2, 1] - matrix[1, 2]) / scale,
            (matrix[0, 2] - matrix[2, 0]) / scale,
            (matrix[1, 0] - matrix[0, 1]) / scale,
            0.25 * scale,
        )
    else:
        i = int(np.argmax(np.diag(matrix)))
        j, k = (i + 1) % 3, (i + 2) % 3
        scale = 2.0 * np.sqrt(max(1.0 + matrix[i, i] - matrix[j, j] - matrix[k, k], 1e-12))
        quaternion = [0.0, 0.0, 0.0, (matrix[k, j] - matrix[j, k]) / scale]
        quaternion[i], quaternion[j], quaternion[k] = (
            0.25 * scale,
            (matrix[j, i] + matrix[i, j]) / scale,
            (matrix[k, i] + matrix[i, k]) / scale,
        )
    quaternion = np.asarray(quaternion) / max(float(np.linalg.norm(quaternion)), 1e-12)
    return tuple(float(value) for value in (quaternion if quaternion[3] >= 0 else -quaternion))


def rigid(rotation, position) -> np.ndarray:
    """The 4x4 matrix of a joint or key: turn by the stored quaternion's conjugate (RDM stores the conjugate of the
    usual one: docs/rdm-gltf.md "Skeletons"), then move."""
    x, y, z, w = (float(value) for value in rotation)
    matrix = np.identity(4)
    matrix[:3, :3] = quat_matrix((-x, -y, -z, w))
    matrix[:3, 3] = position
    return matrix


def _slerp(first: np.ndarray, second: np.ndarray, blend: float) -> np.ndarray:
    if float(np.dot(first, second)) < 0:
        second = -second
    quaternion = first + (second - first) * blend  # normalised linear blend: keys are close in time
    return quaternion / max(float(np.linalg.norm(quaternion)), 1e-12)


def sample(keys, seconds: float) -> tuple[np.ndarray, np.ndarray]:
    """A track's (rotation, position) at a time, blended between its keys (held before the first and after the last)."""
    times = [k.time for k in keys]
    if seconds <= times[0] or len(keys) == 1:
        k = keys[0]
        return np.asarray(k.rotation, float), np.asarray(k.position, float)
    if seconds >= times[-1]:
        k = keys[-1]
        return np.asarray(k.rotation, float), np.asarray(k.position, float)
    i = int(np.searchsorted(times, seconds, side="right")) - 1
    before, after = keys[i], keys[i + 1]
    fraction = (seconds - before.time) / max(after.time - before.time, 1e-9)
    rot = _slerp(np.asarray(before.rotation, float), np.asarray(after.rotation, float), fraction)
    position = (
        np.asarray(before.position, float)
        + (np.asarray(after.position, float) - np.asarray(before.position, float)) * fraction
    )
    return rot, position


def clip_time(seconds: float, duration_ms: int, scale: float = 1.0, loops: int = 0) -> float | None:
    """The time into an animation ``seconds`` after its start: slowed by ``scale``, looped ``loops`` times (0: for
    ever) and then held at its end; None for a scale of 0, which plays nothing (engine-animation "Sequences")."""
    if scale <= 0:
        return None
    length = max(duration_ms, 1) / 1000.0
    clip_seconds = max(0.0, seconds) / scale
    if loops > 0 and clip_seconds >= loops * length:
        return length * (1 - 1e-6)
    return clip_seconds % length


class CRig:
    """A skeleton ready to pose: per joint its inverse bind matrix and its local bind transform."""

    def __init__(self, skeleton: SSkeleton) -> None:
        self.m_joints = skeleton.joints
        self.m_names = {j.name: k for k, j in enumerate(self.m_joints)}
        self.m_hashes = {fnv1a32(j.name): k for k, j in enumerate(self.m_joints)}
        self.m_inverse_bind = (
            np.stack([rigid(j.rotation, j.position) for j in self.m_joints]) if self.m_joints else np.zeros((0, 4, 4))
        )
        bind = np.linalg.inv(self.m_inverse_bind) if len(self.m_joints) else self.m_inverse_bind
        self.m_local_bind = np.stack([
            bind[k] if j.parent is None else np.linalg.inv(bind[j.parent]) @ bind[k] for k, j in enumerate(self.m_joints)
        ]) if self.m_joints else bind  # fmt: skip

    def pose(self, animation: SAnimation | None, seconds: float, is_looping: bool = True) -> np.ndarray:
        """The skin matrices [joints, 4, 4] (identity in the bind pose) of ``animation`` at ``seconds`` (looped, or
        held at the last key)."""
        return self.world(animation, seconds, is_looping) @ self.m_inverse_bind

    def world(self, animation: SAnimation | None, seconds: float, is_looping: bool = True) -> np.ndarray:
        """Each joint's place in the model [joints, 4, 4] at ``seconds`` of ``animation`` (the bind pose without). A
        joint the animation does not name keeps its bind pose, but a root stands at the origin: 2070's children play
        shared clips without a root track, the height in the pelvis key (docs/viewer.md "Animations")."""
        local = self.m_local_bind.copy()
        if animation is not None and animation.duration_ms > 0:
            time_s = seconds % (animation.duration_ms / 1000.0) if is_looping else seconds
            named = {track.joint for track in animation.tracks if track.keys}
            for k, j in enumerate(self.m_joints):
                if j.parent is None and j.name not in named:
                    local[k] = np.identity(4)
            for track in animation.tracks:
                k = self.m_names.get(track.joint)
                if k is not None and track.keys:
                    local[k] = rigid(*sample(track.keys, time_s))
        world = np.zeros_like(local)
        for k, j in enumerate(self.m_joints):  # parent-first, as every shipped skeleton is
            world[k] = local[k] if j.parent is None or j.parent >= k else world[j.parent] @ local[k]
        return world


def skin(mesh: SMeshData, matrices: np.ndarray) -> np.ndarray:
    """The mesh's vertices [n, 8] moved by the skin matrices: positions and normals blended by the weights."""
    if not mesh.skinned or not len(matrices):
        return mesh.vertices
    blend = blended(mesh, matrices)
    out = mesh.vertices.copy()
    out[:, :3] = np.einsum("nij,nj->ni", blend[:, :3, :3], mesh.vertices[:, :3]) + blend[:, :3, 3]
    out[:, 3:6] = turned(blend, mesh.vertices[:, 3:6])
    return out


def blended(mesh: SMeshData, matrices: np.ndarray) -> np.ndarray:
    """Each vertex's skin matrix [n, 4, 4]: its joints' matrices blended by its weights."""
    joints = np.clip(mesh.joints, 0, len(matrices) - 1)
    return np.einsum("nk,nkij->nij", mesh.weights, matrices[joints])


def turned(blend: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """Directions [n, 3] (normals, tangents) turned by each vertex's skin matrix, unit length again."""
    out = np.einsum("nij,nj->ni", blend[:, :3, :3], vectors)
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)
