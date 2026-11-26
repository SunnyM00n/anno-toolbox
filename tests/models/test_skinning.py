"""A skeleton posed in numpy steps (``models.skinning.CRig``, viewer-performance.md section 4, item 10): the matrices are
those of the per-track, per-joint way it replaced, which these tests keep as ``reference_world``."""

import numpy as np

from annotoolbox.formats.rdmread import SAnimation, SJoint, SKeyframe, SSkeleton, STrack
from annotoolbox.models import skinning


def reference_sample(keys, seconds: float):
    """A track's (rotation, position) at a time, one track at a time (the way ``CRig`` posed before item 10)."""
    times = [k.time for k in keys]
    if seconds <= times[0] or len(keys) == 1:
        return np.asarray(keys[0].rotation, float), np.asarray(keys[0].position, float)
    if seconds >= times[-1]:
        return np.asarray(keys[-1].rotation, float), np.asarray(keys[-1].position, float)
    i = int(np.searchsorted(times, seconds, side="right")) - 1
    before, after = keys[i], keys[i + 1]
    fraction = (seconds - before.time) / max(after.time - before.time, 1e-9)
    first, second = np.asarray(before.rotation, float), np.asarray(after.rotation, float)
    if float(np.dot(first, second)) < 0:
        second = -second
    rotation = first + (second - first) * fraction
    rotation = rotation / max(float(np.linalg.norm(rotation)), 1e-12)
    position = np.asarray(before.position, float) + (np.asarray(after.position, float) - before.position) * fraction
    return rotation, position


def reference_world(rig: skinning.CRig, animation, seconds: float, is_looping: bool = True) -> np.ndarray:
    local = rig.m_local_bind.copy()
    if animation is not None and animation.duration_ms > 0:
        time_s = seconds % (animation.duration_ms / 1000.0) if is_looping else seconds
        named = {track.joint for track in animation.tracks if track.keys}
        for k, j in enumerate(rig.m_joints):
            if j.parent is None and j.name not in named:
                local[k] = np.identity(4)
        for track in animation.tracks:
            k = rig.m_names.get(track.joint)
            if k is not None and track.keys:
                local[k] = skinning.rigid(*reference_sample(track.keys, time_s))
    world = np.zeros_like(local)
    for k, j in enumerate(rig.m_joints):
        world[k] = local[k] if j.parent is None or j.parent >= k else world[j.parent] @ local[k]
    return world


def unit(x: float, y: float, z: float, w: float) -> tuple[float, float, float, float]:
    length = (x * x + y * y + z * z + w * w) ** 0.5
    return (x / length, y / length, z / length, w / length)


def skeleton() -> SSkeleton:
    """Two roots, a chain of three under the first, two children of its middle joint, a child of the second root."""
    parents = [None, 0, 1, 1, 3, None, 5]
    joints = [
        SJoint(f"j{k}", (0.1 * k, 0.2, -0.05 * k), unit(0.1 * k, 0.3, -0.2, 1.0), parent, (0, 0, 0), (0, 0, 0))
        for k, parent in enumerate(parents)
    ]
    return SSkeleton(joints, 0)


def keys(count: int, seed: float, flip: bool = False) -> list[SKeyframe]:
    return [
        SKeyframe(
            unit(np.sin(seed + k), np.cos(seed * k), 0.2 * k, -1.0 if flip and k == 1 else 1.0),
            (0.1 * k, seed, -0.1 * k),
            0.25 * k + 0.1 * seed,
        )
        for k in range(count)
    ]


def animation() -> SAnimation:
    """Tracks of 1, 2, 5 and 3 keys (the last of 3 joins a joint twice: the later one counts), a track of a joint the
    skeleton lacks, a track without keys and a second root left unnamed."""
    tracks = [
        STrack("j0", keys(1, 0.3)),
        STrack("j1", keys(2, 0.7, flip=True)),
        STrack("j2", keys(5, 1.1)),
        STrack("j4", keys(4, 0.2)),
        STrack("nothere", keys(3, 0.9)),
        STrack("j3", []),
        STrack("j4", keys(3, 1.7, flip=True)),
        STrack("j6", keys(2, 0.5)),
    ]
    return SAnimation("m", 1500, 30, tracks)


def test_a_pose_matches_the_per_track_way_at_every_time():
    rig = skinning.CRig(skeleton())
    clip = animation()
    for seconds in [-0.2, 0.0, 0.05, 0.1, 0.33, 0.6, 0.999, 1.3, 1.5, 2.9, 7.123]:
        for is_looping in (True, False):
            wanted = reference_world(rig, clip, seconds, is_looping) @ rig.m_inverse_bind
            assert np.allclose(rig.pose(clip, seconds, is_looping), wanted, atol=1e-9), (seconds, is_looping)


def test_the_bind_pose_and_a_clip_without_duration_or_tracks_match_too():
    rig = skinning.CRig(skeleton())
    assert np.allclose(rig.pose(None, 0.3), np.identity(4), atol=1e-9)
    for clip in (
        SAnimation("m", 0, 30, animation().tracks),
        SAnimation("m", 1000, 30, []),
        SAnimation("m", 1000, 30, [STrack("j2", [])]),
    ):
        assert np.allclose(rig.world(clip, 0.4), reference_world(rig, clip, 0.4), atol=1e-9)


def test_a_rig_keeps_the_arrays_of_a_clip_and_drops_the_oldest():
    rig = skinning.CRig(skeleton())
    clip = animation()
    assert rig.clip_tracks(clip) is rig.clip_tracks(clip)
    for _ in range(skinning.CLIPS_KEPT):
        rig.clip_tracks(SAnimation("m", 1000, 30, []))
    assert len(rig.m_clips) == skinning.CLIPS_KEPT and all(kept.animation is not clip for kept in rig.m_clips)


def test_quaternion_matrices_match_one_at_a_time_and_a_zero_one_is_the_identity():
    quaternions = np.array([unit(0.1, 0.2, 0.3, 0.9), unit(-1, 0.5, 0.2, 0.1), (0.0, 0.0, 0.0, 0.0)])
    many = skinning.quat_matrices(quaternions)
    assert np.allclose(many[2], np.identity(3))
    for k in range(2):
        assert np.allclose(many[k] @ many[k].T, np.identity(3)) and np.allclose(
            skinning.quat_matrix(quaternions[k]), many[k]
        )
