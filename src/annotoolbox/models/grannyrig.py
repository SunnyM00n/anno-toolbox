"""Granny skeletons, skins and animations (``formats.grannyanim``) as the RDM records the view's rig and the RDM
toolchain take: a skeleton's bones as joints (the rigid part of each inverse bind), an animation sampled every time
step into keys (rigid locals from the posed places, so a parent's scale moves its children right), a mesh's bone
numbers through its bindings into its model's skeleton (a rigid mesh as a one-bone skin).

``granny_skeleton``, ``granny_animation`` and ``granny_skin`` are the entries; ``best_skeleton`` picks the skeleton an
animation file plays on. docs/formats/granny.md "Skeletons, animations and curves".
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from ..formats import grannyanim
from ..formats.rdmread import Animation, Joint, Keyframe, Mesh, Skeleton, Track, VertexElement
from .meshxform import qconj
from .skinning import matrix_quat, quat_matrix

PIN_BIT = 0x80  # a cloth vertex is pinned when its colour's blue byte has the top bit (granny.md "Cloth")
FRAME_RATE = 30  # a Granny animation sampled this often when it names no time step


# Granny skeletons, skins and animations in the view's rig records


def granny_local(position, orientation, scale_shear) -> np.ndarray:
    """A Granny bone transform for column vectors: move x turn x scale-shear (stored row by row, so transposed)."""
    m = np.identity(4)
    m[:3, :3] = quat_matrix(orientation) @ np.asarray(scale_shear, float).reshape(3, 3).T
    m[:3, 3] = position
    return m


def rigid_part(m: np.ndarray) -> np.ndarray:
    """Places [..., 4, 4] without their scale and shear: the nearest rotation (polar decomposition), the move."""
    m = np.asarray(m, float)
    u, _s, vt = np.linalg.svd(m[..., :3, :3])
    u[..., :, -1] *= np.sign(np.linalg.det(u @ vt))[..., None]  # a mirrored place: keep it a rotation
    out = np.broadcast_to(np.identity(4), m.shape).copy()
    out[..., :3, :3], out[..., :3, 3] = u @ vt, m[..., :3, 3]
    return out


def granny_skeleton(skeleton: grannyanim.Skeleton) -> Skeleton:
    """A Granny skeleton as an RDM skeleton: per bone the rigid part of its inverse bind (scale and shear left out:
    exact while a bone's scale stays as bound, granny.md "Skeletons, animations and curves")."""
    joints = []
    for bone in skeleton.bones:
        bind = rigid_part(np.linalg.inv(np.asarray(bone.inverse_world, float).reshape(4, 4).T))
        inverse = np.linalg.inv(bind)
        joints.append(Joint(bone.name, tuple(inverse[:3, 3]), qconj(matrix_quat(inverse[:3, :3])), bone.parent,
                            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))  # fmt: skip
    return Skeleton(joints, 0)


def posed_places(skeleton: grannyanim.Skeleton, local: dict[str, tuple]) -> list[np.ndarray]:
    """Each bone's place in the model, scale and shear included: ``local`` the posed (position, orientation,
    scale-shear) by bone name, a bone without one at its rest transform."""
    world: list[np.ndarray] = []
    for bone in skeleton.bones:
        m = granny_local(*local.get(bone.name, (bone.position, bone.orientation, bone.scale_shear)))
        world.append(m if bone.parent is None or bone.parent >= len(world) else world[bone.parent] @ m)
    return world


def _rigid_locals(skeleton: grannyanim.Skeleton, world: list[np.ndarray]) -> np.ndarray:
    places = rigid_part(np.stack(world))
    parents = np.array([b.parent if b.parent is not None and b.parent < k else -1
                        for k, b in enumerate(skeleton.bones)])  # fmt: skip
    local = np.linalg.inv(places)[np.maximum(parents, 0)] @ places
    return np.where((parents < 0)[:, None, None], places, local)


def granny_animation(animation: grannyanim.Animation, skeleton: grannyanim.Skeleton | None) -> Animation:
    """An animation sampled every time step into RDM keys. Against ``skeleton`` every bone gets a key per step,
    its rigid local taken from the posed places (a parent's scale moves its children right); without one each track
    is its own curves with scale and shear left out."""
    step = animation.time_step if animation.time_step > 1e-4 else 1.0 / FRAME_RATE
    count = max(1, math.ceil(animation.duration / step - 1e-6))  # the last key at the end, not a step short of it
    times = [min(k * step, animation.duration) for k in range(count + 1)]
    group = animation.group_for(skeleton)
    tracks = group.by_name() if group is not None else {}
    keys: dict[str, list[Keyframe]] = {}
    for t in times:
        local = {name: track.at(t) for name, track in tracks.items()}
        if skeleton is None:
            pairs = [(name, granny_local(p, q, (1, 0, 0, 0, 1, 0, 0, 0, 1))) for name, (p, q, _s) in local.items()]
        else:
            rigid = _rigid_locals(skeleton, posed_places(skeleton, local))
            pairs = [(b.name, m) for b, m in zip(skeleton.bones, rigid, strict=True)]
        for name, m in pairs:
            keys.setdefault(name, []).append(Keyframe(qconj(matrix_quat(m[:3, :3])), tuple(m[:3, 3]), t))
    rate = max(1, round(1.0 / step))
    return Animation(animation.name, round(animation.duration * 1000), rate, [Track(n, k) for n, k in keys.items()])


def granny_skin(m, skeletons: list[grannyanim.Skeleton], owner: int | None) -> tuple | None:
    """(skeleton number, joints [n, 4], weights [n, 4]) of a Granny mesh: its bone numbers through its bindings'
    names into its model's skeleton (the first that names them all without one); a rigid mesh moves whole with
    its first binding's bone. None when no skeleton has its bones."""
    names = [n for n in m.bones if n]
    if not names:
        return None
    candidates = [owner] if owner is not None and owner < len(skeletons) else []
    candidates += [k for k, s in enumerate(skeletons) if set(names) <= set(s.index())]
    if not candidates:
        return None
    at = candidates[0]
    index = skeletons[at].index()
    lookup = np.array([index.get(n, 0) for n in m.bones] or [0], np.int64)
    count = len(m.positions)
    joints, weights = np.zeros((count, 4), np.int64), np.zeros((count, 4), np.float32)
    if m.bone_indices is None:
        joints[:, 0], weights[:, 0] = index.get(names[0], 0), 1.0
        return at, joints, weights
    raw = np.clip(np.asarray(m.bone_indices, np.int64)[:, :4], 0, len(lookup) - 1)
    joints[:, : raw.shape[1]] = lookup[raw]
    if m.bone_weights is None:
        weights[:, 0] = 1.0
    else:
        w = np.asarray(m.bone_weights, np.float32)[:, :4]
        weights[:, : w.shape[1]] = w
        total = weights.sum(axis=1, keepdims=True)
        weights = np.where(total > 0, weights / np.maximum(total, 1e-9), np.array([1.0, 0, 0, 0], np.float32))
    return at, joints, weights


def pinned(colors) -> bool:
    """Whether vertex colours pin a mesh as cloth (a vertex's blue byte with the top bit)."""
    return colors is not None and any(_pinned(c) for c in colors)


def subdivided(m, rounds: int):
    """A cloth mesh (``grannyglb.GrannyMesh`` with colours) with every triangle split into four, ``rounds`` times:
    a new vertex at each edge's middle, shared by both triangles of the edge, its values the ends' mean; it is pinned
    only when both ends are (granny.md "Cloth": 1701 subdivides its coarse cloth, inferred)."""
    for _ in range(rounds):
        m = _split(m)
    return m


def _split(m):
    """One round of ``subdivided``."""
    columns = [list(m.positions), list(m.normals), list(m.uvs), list(m.colors)]
    middle: dict[tuple[int, int], int] = {}

    def mid(a: int, b: int) -> int:
        key = (min(a, b), max(a, b))
        if key not in middle:
            middle[key] = len(columns[0])
            for values in columns:
                values.append(tuple((x + y) / 2 for x, y in zip(values[a], values[b], strict=True)))
            normal, colors = columns[1][-1], columns[3]
            columns[1][-1] = tuple(v / (math.sqrt(sum(n * n for n in normal)) or 1.0) for v in normal)
            c, pin = colors[-1], _pinned(colors[a]) and _pinned(colors[b])
            colors[-1] = (c[0], c[1], max(c[2], 128 / 255) if pin else min(c[2], 127 / 255), c[3])
        return middle[key]

    groups = []
    for name, tris in m.groups:
        out = []
        for a, b, c in zip(tris[0::3], tris[1::3], tris[2::3], strict=True):
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            out += [a, ab, ca, ab, b, bc, ca, bc, c, ab, bc, ca]
        groups.append((name, out))
    positions, normals, uvs, colors = columns
    return replace(m, positions=positions, normals=normals, uvs=uvs, groups=groups, colors=colors,
                   bone_indices=None, bone_weights=None)  # fmt: skip


def _pinned(c) -> bool:
    return bool(round(c[2] * 255) & PIN_BIT)


def colour_record(m, indices: list[int]) -> Mesh:
    """A Granny mesh's colours as an RDM mesh of one colour element, bytes B, G, R, A as 2070 stores them, for the
    cloth's pins (``clothsim.vertex_pins``)."""
    raw = bytes(round(min(max(c[k], 0.0), 1.0) * 255) for c in m.colors for k in (2, 1, 0, 3))
    return Mesh(m.name, (VertexElement(5, 5, 0, 1),), 0, 1, len(m.colors), 4, raw, indices, [], None, (0.0,) * 6, 0)


def best_skeleton(animation: grannyanim.Animation, skeletons: list[grannyanim.Skeleton]):
    """The skeleton naming the most of an animation's tracks (the first of equals), None when none names any."""
    names = {t.name for g in animation.groups for t in g.tracks}
    scored = [(sum(b.name in names for b in s.bones), -k, s) for k, s in enumerate(skeletons)]
    best = max(scored, default=None, key=lambda x: x[:2])
    return best[2] if best is not None and best[0] else None
