"""A skinned mesh posed by an animation, on the CPU: the joints' matrices at a time and the vertices moved by them.

A joint stores its inverse bind pose, an animation key the joint's local transform, tracks find their joint by name
and a joint without a track keeps its bind pose (docs/formats/rdm.md "Skeletons and animations",
docs/engine-animation.md). ``CRig(skeleton)`` holds a skeleton, ``CRig.pose(animation, seconds)`` gives the skin
matrices (``CRig.world`` the joints' own), ``skin(mesh, matrices)`` the posed vertices; ``clip_time`` maps a clip's
``Scale`` and ``LoopCount`` onto the time.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from ..formats.rdmread import SAnimation, SSkeleton
from .configtree import fnv1a32
from .rdmmesh import SMeshData

CLIPS_KEPT = 16  # the clips whose tracks a rig keeps as arrays


def quat_matrices(quaternions) -> np.ndarray:
    """The 3x3 rotations [n, 3, 3] of quaternions [n, 4] (x, y, z, w), for column vectors; a zero one gives the identity."""
    x, y, z, w = np.asarray(quaternions, float).reshape(-1, 4).T
    squared_length = x * x + y * y + z * z + w * w
    scale = 2.0 / np.maximum(squared_length, 1e-12)
    out = np.empty((len(x), 3, 3))
    out[:, 0] = np.stack([1 - scale * (y * y + z * z), scale * (x * y - z * w), scale * (x * z + y * w)], axis=1)
    out[:, 1] = np.stack([scale * (x * y + z * w), 1 - scale * (x * x + z * z), scale * (y * z - x * w)], axis=1)
    out[:, 2] = np.stack([scale * (x * z - y * w), scale * (y * z + x * w), 1 - scale * (x * x + y * y)], axis=1)
    return np.where((squared_length < 1e-12)[:, None, None], np.identity(3), out)


def quat_matrix(quaternion) -> np.ndarray:
    """The 3x3 rotation of a unit quaternion (x, y, z, w), for column vectors."""
    return quat_matrices(quaternion)[0]


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


class SClipTracks(NamedTuple):
    """An animation's tracks as arrays, made once per clip (``CRig.clip_tracks``). The keys of every track lie one
    after the other in ``times``, ``rotations`` and ``positions``; ``shifted`` moves each track's times to a range of
    its own, so one search finds the key of every track."""

    animation: SAnimation  # kept, so the cache can compare by identity
    joints: np.ndarray  # [tracks] the joint a track moves; a joint has one track, the last one named
    first: np.ndarray  # [tracks] flat index of a track's first key
    last: np.ndarray  # [tracks] flat index of a track's last key
    times: np.ndarray  # [keys]
    shifted: np.ndarray  # [keys]
    shift: np.ndarray  # [tracks]
    origin: float  # the earliest key time
    rotations: np.ndarray  # [keys, 4] as stored
    positions: np.ndarray  # [keys, 3]
    base: np.ndarray  # [joints, 4, 4] the local bind transforms, an unnamed root at the origin


class CRig:
    """A skeleton ready to pose: per joint its inverse bind matrix and its local bind transform. The tracks of the
    last ``CLIPS_KEPT`` clips posed are kept as arrays, so a clip's keys are read once and a frame is a few numpy steps;
    a clip is not changed once posed."""

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
        self.m_levels = self._levels()
        self.m_clips: list[SClipTracks] = []

    def _levels(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """(joints, their parents) per depth below the roots: a joint is a root without a parent or when its parent
        comes later (every shipped skeleton lists parents first)."""
        parents = [j.parent if j.parent is not None and j.parent < k else -1 for k, j in enumerate(self.m_joints)]
        depth: list[int] = []
        for parent in parents:
            depth.append(depth[parent] + 1 if parent >= 0 else 0)
        found = np.asarray(depth, int)
        return [
            (np.flatnonzero(found == level), np.asarray(parents, int)[found == level])
            for level in range(1, int(found.max(initial=0)) + 1)
        ]

    def pose(self, animation: SAnimation | None, seconds: float, is_looping: bool = True) -> np.ndarray:
        """The skin matrices [joints, 4, 4] (identity in the bind pose) of ``animation`` at ``seconds`` (looped, or
        held at the last key)."""
        return self.world(animation, seconds, is_looping) @ self.m_inverse_bind

    def world(self, animation: SAnimation | None, seconds: float, is_looping: bool = True) -> np.ndarray:
        """Each joint's place in the model [joints, 4, 4] at ``seconds`` of ``animation`` (the bind pose without). A
        joint the animation does not name keeps its bind pose, but a root stands at the origin: 2070's children play
        shared clips without a root track, the height in the pelvis key (docs/viewer.md "Animations")."""
        if animation is None or animation.duration_ms <= 0:
            local = self.m_local_bind.copy()
        else:
            local = self._local(animation, seconds, is_looping)
        world = local.copy()
        for joints, parents in self.m_levels:  # parents first: a level's parents are done
            world[joints] = world[parents] @ local[joints]
        return world

    def clip_tracks(self, animation: SAnimation) -> SClipTracks:
        """The arrays of ``animation``'s tracks for this skeleton, made on the first ask."""
        for kept in self.m_clips:
            if kept.animation is animation:
                return kept
        found = self._read_tracks(animation)
        self.m_clips = [*self.m_clips[1 - CLIPS_KEPT :], found]
        return found

    def _read_tracks(self, animation: SAnimation) -> SClipTracks:
        named = {track.joint for track in animation.tracks if track.keys}
        base = self.m_local_bind.copy()
        for k, j in enumerate(self.m_joints):
            if j.parent is None and j.name not in named:
                base[k] = np.identity(4)
        by_joint = {self.m_names[t.joint]: t for t in animation.tracks if t.keys and t.joint in self.m_names}
        tracks = list(by_joint.values())
        counts = np.asarray([len(track.keys) for track in tracks], int)
        last = np.cumsum(counts) - 1
        keys = [key for track in tracks for key in track.keys]
        times = np.asarray([key.time for key in keys], float)
        origin = float(times.min(initial=0.0))
        shift = np.arange(len(tracks)) * (float(times.max(initial=0.0)) - origin + 1.0)
        return SClipTracks(
            animation,
            np.asarray(list(by_joint), int),
            last - counts + 1,
            last,
            times,
            times - origin + np.repeat(shift, counts),
            shift,
            origin,
            np.asarray([key.rotation for key in keys], float).reshape(-1, 4),
            np.asarray([key.position for key in keys], float).reshape(-1, 3),
            base,
        )

    def _local(self, animation: SAnimation, seconds: float, is_looping: bool) -> np.ndarray:
        """Every joint's local transform at ``seconds``: the tracks' keys blended, the other joints as in the bind pose."""
        clip = self.clip_tracks(animation)
        local = clip.base.copy()
        if not len(clip.joints):
            return local
        time_s = seconds % (animation.duration_ms / 1000.0) if is_looping else seconds
        found = np.searchsorted(clip.shifted, time_s - clip.origin + clip.shift, side="right") - 1
        before = np.clip(found, clip.first, clip.last)  # held before the first key and after the last
        after = np.minimum(before + 1, clip.last)
        span = np.maximum(clip.times[after] - clip.times[before], 1e-9)
        fraction = np.clip((time_s - clip.times[before]) / span, 0.0, 1.0)[:, None]
        first, second = clip.rotations[before], clip.rotations[after]
        second = np.where(np.sum(first * second, axis=1, keepdims=True) < 0, -second, second)
        blend = first + (second - first) * fraction  # normalised linear blend: keys are close in time
        blend = blend / np.maximum(np.linalg.norm(blend, axis=1, keepdims=True), 1e-12)
        blend[:, :3] *= -1.0  # RDM stores the conjugate (``rigid``)
        local[clip.joints, :3, :3] = quat_matrices(blend)
        local[clip.joints, :3, 3] = clip.positions[before] + (clip.positions[after] - clip.positions[before]) * fraction
        return local


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
