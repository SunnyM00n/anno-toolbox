"""Rigid transforms for skeletons: 4x4 matrices from a rotation and a translation and back, and the mirror on x.

Matrices are row-major 4x4 tuples acting on column vectors (glTF stores them column-major: ``to_columns``).
Quaternions are (x, y, z, w) and turn a vector as ``q v q*``, glTF's convention; RDM's joints and keys store the
conjugate, ``meshxform.qconj`` (docs/rdm-gltf.md "Skeletons").
"""

from __future__ import annotations

import math

from ..models.meshxform import qnormalize

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
Matrix = tuple[tuple[float, float, float, float], ...]

IDENTITY: Matrix = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def from_rotation_translation(q: Quat, t: Vec3, scale: Vec3 = (1.0, 1.0, 1.0)) -> Matrix:
    """The matrix that scales, then turns by ``q`` (normalized), then moves by ``t``."""
    x, y, z, w = qnormalize(q)
    r = (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )
    rows = tuple((r[i][0] * scale[0], r[i][1] * scale[1], r[i][2] * scale[2], t[i]) for i in range(3))
    return (*rows, (0.0, 0.0, 0.0, 1.0))


def multiply(a: Matrix, b: Matrix) -> Matrix:
    """``a`` after ``b``."""
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)) for i in range(4))


def inverse(m: Matrix) -> Matrix:
    """The inverse of an affine matrix (its 3x3 part by the cofactors)."""
    a = [row[:3] for row in m[:3]]
    det = (
        a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
        - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
        + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
    )
    if abs(det) < 1e-12:
        raise ValueError("a transform that flattens space has no inverse")
    inv = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            r = [k for k in range(3) if k != j]
            c = [k for k in range(3) if k != i]
            minor = a[r[0]][c[0]] * a[r[1]][c[1]] - a[r[0]][c[1]] * a[r[1]][c[0]]
            inv[i][j] = (-1) ** (i + j) * minor / det
    t = [m[k][3] for k in range(3)]
    moved = [-sum(inv[i][k] * t[k] for k in range(3)) for i in range(3)]
    return (*(tuple(inv[i]) + (moved[i],) for i in range(3)), (0.0, 0.0, 0.0, 1.0))


def decompose(m: Matrix) -> tuple[Quat, Vec3, Vec3]:
    """``(rotation, translation, scale)`` of an affine matrix without shear (a mirror is a negative x scale)."""
    cols = [tuple(m[i][j] for i in range(3)) for j in range(3)]
    scale = [math.sqrt(sum(c * c for c in col)) for col in cols]
    det = sum(
        m[0][i] * (m[1][(i + 1) % 3] * m[2][(i + 2) % 3] - m[1][(i + 2) % 3] * m[2][(i + 1) % 3]) for i in range(3)
    )
    if det < 0:
        scale[0] = -scale[0]
    r = [[m[i][j] / scale[j] if scale[j] else 0.0 for j in range(3)] for i in range(3)]
    return quaternion(r), (m[0][3], m[1][3], m[2][3]), (scale[0], scale[1], scale[2])


def quaternion(r: list[list[float]]) -> Quat:
    """The unit quaternion of a rotation matrix (the largest-diagonal method), with w >= 0."""
    trace = r[0][0] + r[1][1] + r[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        q = ((r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s, 0.25 * s)
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        q = (0.25 * s, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s, (r[2][1] - r[1][2]) / s)
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        q = ((r[0][1] + r[1][0]) / s, 0.25 * s, (r[1][2] + r[2][1]) / s, (r[0][2] - r[2][0]) / s)
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
        q = ((r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, 0.25 * s, (r[1][0] - r[0][1]) / s)
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    q = tuple(c / n for c in q)
    return q if q[3] >= 0 else tuple(-c for c in q)


def to_columns(m: Matrix) -> tuple[float, ...]:
    """The 16 numbers of ``m`` in glTF's column-major order."""
    return tuple(m[i][j] for j in range(4) for i in range(4))


def from_columns(values) -> Matrix:
    v = list(values)
    return tuple(tuple(float(v[j * 4 + i]) for j in range(4)) for i in range(4))


def from_node(node: dict) -> Matrix:
    """A glTF node's local matrix (``matrix``, or ``translation`` / ``rotation`` / ``scale``)."""
    if "matrix" in node:
        return from_columns(node["matrix"])
    return from_rotation_translation(
        tuple(node.get("rotation", (0.0, 0.0, 0.0, 1.0))),
        tuple(node.get("translation", (0.0, 0.0, 0.0))),
        tuple(node.get("scale", (1.0, 1.0, 1.0))),
    )


def apply(m: Matrix, p: Vec3) -> Vec3:
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3))


def is_identity(m: Matrix, tolerance: float = 1e-6) -> bool:
    return all(abs(m[i][j] - IDENTITY[i][j]) <= tolerance for i in range(4) for j in range(4))


MIRROR_X: Matrix = ((-1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def mirrored(m: Matrix) -> Matrix:
    """``m`` seen in the space mirrored on x (``S m S``): what a joint's transform becomes when the mesh is."""
    return multiply(MIRROR_X, multiply(m, MIRROR_X))


def mirrored_rotation(q: Quat) -> Quat:
    """A rotation seen in the space mirrored on x: exact, a sign change of y and z."""
    return (q[0], -q[1], -q[2], q[3])


def mirrored_point(p: Vec3) -> Vec3:
    return (-p[0], p[1], p[2])
