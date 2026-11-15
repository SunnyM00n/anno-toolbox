"""A skinned mesh posed by an animation, on the CPU: the joints' matrices at a time and the vertices moved by them.

A joint stores its inverse bind pose, an animation key the joint's local transform, tracks find their joint by name
and a joint without a track keeps its bind pose (docs/formats/rdm.md "Skeletons and animations",
docs/engine-animation.md). ``Rig(skeleton)`` holds a skeleton, ``Rig.pose(animation, seconds)`` gives the skin
matrices (``Rig.world`` the joints' own), ``skin(mesh, matrices)`` the posed vertices; ``clip_time`` maps a clip's
``Scale`` and ``LoopCount`` onto the time.
"""

from __future__ import annotations

import numpy as np

from ..formats.rdmread import Animation, Skeleton
from .configtree import fnv1a32
from .rdmmesh import MeshData


def quat_matrix(q) -> np.ndarray:
    """The 3x3 rotation of a unit quaternion (x, y, z, w), for column vectors."""
    x, y, z, w = (float(v) for v in q)
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        return np.identity(3)
    s = 2.0 / n
    return np.array([
        [1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
        [s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w)],
        [s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y)],
    ])  # fmt: skip


def matrix_quat(m: np.ndarray) -> tuple[float, float, float, float]:
    """The unit quaternion (x, y, z, w) of a 3x3 rotation for column vectors, w not negative."""
    m = np.asarray(m, float)
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = 2.0 * np.sqrt(trace + 1.0)
        q = ((m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s)
    else:
        i = int(np.argmax(np.diag(m)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = 2.0 * np.sqrt(max(1.0 + m[i, i] - m[j, j] - m[k, k], 1e-12))
        q = [0.0, 0.0, 0.0, (m[k, j] - m[j, k]) / s]
        q[i], q[j], q[k] = 0.25 * s, (m[j, i] + m[i, j]) / s, (m[k, i] + m[i, k]) / s
    q = np.asarray(q) / max(float(np.linalg.norm(q)), 1e-12)
    return tuple(float(v) for v in (q if q[3] >= 0 else -q))


def rigid(rotation, position) -> np.ndarray:
    """The 4x4 matrix of a joint or key: turn by the stored quaternion's conjugate (RDM stores the conjugate of the
    usual one: docs/rdm-gltf.md "Skeletons"), then move."""
    x, y, z, w = (float(v) for v in rotation)
    m = np.identity(4)
    m[:3, :3] = quat_matrix((-x, -y, -z, w))
    m[:3, 3] = position
    return m


def _slerp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    if float(np.dot(a, b)) < 0:
        b = -b
    q = a + (b - a) * t  # normalised linear blend: keys are close in time
    return q / max(float(np.linalg.norm(q)), 1e-12)


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
    a, b = keys[i], keys[i + 1]
    t = (seconds - a.time) / max(b.time - a.time, 1e-9)
    rot = _slerp(np.asarray(a.rotation, float), np.asarray(b.rotation, float), t)
    pos = np.asarray(a.position, float) + (np.asarray(b.position, float) - np.asarray(a.position, float)) * t
    return rot, pos


def clip_time(seconds: float, duration_ms: int, scale: float = 1.0, loops: int = 0) -> float | None:
    """The time into an animation ``seconds`` after its start: slowed by ``scale``, looped ``loops`` times (0: for
    ever) and then held at its end; None for a scale of 0, which plays nothing (engine-animation "Sequences")."""
    if scale <= 0:
        return None
    length = max(duration_ms, 1) / 1000.0
    t = max(0.0, seconds) / scale
    if loops > 0 and t >= loops * length:
        return length * (1 - 1e-6)
    return t % length


class Rig:
    """A skeleton ready to pose: per joint its inverse bind matrix and its local bind transform."""

    def __init__(self, skeleton: Skeleton) -> None:
        self.joints = skeleton.joints
        self.names = {j.name: k for k, j in enumerate(self.joints)}
        self.hashes = {fnv1a32(j.name): k for k, j in enumerate(self.joints)}
        self.inverse_bind = (
            np.stack([rigid(j.rotation, j.position) for j in self.joints]) if self.joints else np.zeros((0, 4, 4))
        )
        bind = np.linalg.inv(self.inverse_bind) if len(self.joints) else self.inverse_bind
        self.local_bind = np.stack([
            bind[k] if j.parent is None else np.linalg.inv(bind[j.parent]) @ bind[k] for k, j in enumerate(self.joints)
        ]) if self.joints else bind  # fmt: skip

    def pose(self, animation: Animation | None, seconds: float, loop: bool = True) -> np.ndarray:
        """The skin matrices [joints, 4, 4] (identity in the bind pose) of ``animation`` at ``seconds`` (looped, or
        held at the last key)."""
        return self.world(animation, seconds, loop) @ self.inverse_bind

    def world(self, animation: Animation | None, seconds: float, loop: bool = True) -> np.ndarray:
        """Each joint's place in the model [joints, 4, 4] at ``seconds`` of ``animation`` (the bind pose without). A
        joint the animation does not name keeps its bind pose, but a root stands at the origin: 2070's children play
        shared clips without a root track, the height in the pelvis key (docs/viewer.md "Animations")."""
        local = self.local_bind.copy()
        if animation is not None and animation.duration_ms > 0:
            t = seconds % (animation.duration_ms / 1000.0) if loop else seconds
            named = {track.joint for track in animation.tracks if track.keys}
            for k, j in enumerate(self.joints):
                if j.parent is None and j.name not in named:
                    local[k] = np.identity(4)
            for track in animation.tracks:
                k = self.names.get(track.joint)
                if k is not None and track.keys:
                    local[k] = rigid(*sample(track.keys, t))
        world = np.zeros_like(local)
        for k, j in enumerate(self.joints):  # parent-first, as every shipped skeleton is
            world[k] = local[k] if j.parent is None or j.parent >= k else world[j.parent] @ local[k]
        return world


def skin(mesh: MeshData, matrices: np.ndarray) -> np.ndarray:
    """The mesh's vertices [n, 8] moved by the skin matrices: positions and normals blended by the weights."""
    if not mesh.skinned or not len(matrices):
        return mesh.vertices
    blend = blended(mesh, matrices)
    out = mesh.vertices.copy()
    out[:, :3] = np.einsum("nij,nj->ni", blend[:, :3, :3], mesh.vertices[:, :3]) + blend[:, :3, 3]
    out[:, 3:6] = turned(blend, mesh.vertices[:, 3:6])
    return out


def blended(mesh: MeshData, matrices: np.ndarray) -> np.ndarray:
    """Each vertex's skin matrix [n, 4, 4]: its joints' matrices blended by its weights."""
    joints = np.clip(mesh.joints, 0, len(matrices) - 1)
    return np.einsum("nk,nkij->nij", mesh.weights, matrices[joints])


def turned(blend: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """Directions [n, 3] (normals, tangents) turned by each vertex's skin matrix, unit length again."""
    out = np.einsum("nij,nj->ni", blend[:, :3, :3], vectors)
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)
