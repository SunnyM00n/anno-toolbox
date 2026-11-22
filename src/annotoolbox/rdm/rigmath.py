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


def from_rotation_translation(rotation: Quat, translation: Vec3, scale: Vec3 = (1.0, 1.0, 1.0)) -> Matrix:
    """The matrix that scales, then turns by ``q`` (normalized), then moves by ``t``."""
    x, y, z, w = qnormalize(rotation)
    rotation_rows = (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )
    rows = tuple(
        (rotation_rows[i][0] * scale[0], rotation_rows[i][1] * scale[1], rotation_rows[i][2] * scale[2], translation[i])
        for i in range(3)
    )
    return (*rows, (0.0, 0.0, 0.0, 1.0))


def multiply(left: Matrix, right: Matrix) -> Matrix:
    """``a`` after ``b``."""
    return tuple(tuple(sum(left[i][k] * right[k][j] for k in range(4)) for j in range(4)) for i in range(4))


def inverse(matrix: Matrix) -> Matrix:
    """The inverse of an affine matrix (its 3x3 part by the cofactors)."""
    linear = [row[:3] for row in matrix[:3]]
    det = (
        linear[0][0] * (linear[1][1] * linear[2][2] - linear[1][2] * linear[2][1])
        - linear[0][1] * (linear[1][0] * linear[2][2] - linear[1][2] * linear[2][0])
        + linear[0][2] * (linear[1][0] * linear[2][1] - linear[1][1] * linear[2][0])
    )
    if abs(det) < 1e-12:
        raise ValueError("a transform that flattens space has no inverse")
    inverse_linear = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            row_indices = [k for k in range(3) if k != j]
            column_indices = [k for k in range(3) if k != i]
            minor = (
                linear[row_indices[0]][column_indices[0]] * linear[row_indices[1]][column_indices[1]]
                - linear[row_indices[0]][column_indices[1]] * linear[row_indices[1]][column_indices[0]]
            )
            inverse_linear[i][j] = (-1) ** (i + j) * minor / det
    move = [matrix[k][3] for k in range(3)]
    moved = [-sum(inverse_linear[i][k] * move[k] for k in range(3)) for i in range(3)]
    return (*(tuple(inverse_linear[i]) + (moved[i],) for i in range(3)), (0.0, 0.0, 0.0, 1.0))


def decompose(matrix: Matrix) -> tuple[Quat, Vec3, Vec3]:
    """``(rotation, translation, scale)`` of an affine matrix without shear (a mirror is a negative x scale)."""
    columns = [tuple(matrix[i][j] for i in range(3)) for j in range(3)]
    scale = [math.sqrt(sum(component * component for component in column)) for column in columns]
    det = sum(
        matrix[0][i]
        * (matrix[1][(i + 1) % 3] * matrix[2][(i + 2) % 3] - matrix[1][(i + 2) % 3] * matrix[2][(i + 1) % 3])
        for i in range(3)
    )
    if det < 0:
        scale[0] = -scale[0]
    rotation_rows = [[matrix[i][j] / scale[j] if scale[j] else 0.0 for j in range(3)] for i in range(3)]
    return quaternion(rotation_rows), (matrix[0][3], matrix[1][3], matrix[2][3]), (scale[0], scale[1], scale[2])


def quaternion(rotation_rows: list[list[float]]) -> Quat:
    """The unit quaternion of a rotation matrix (the largest-diagonal method), with w >= 0."""
    trace = rotation_rows[0][0] + rotation_rows[1][1] + rotation_rows[2][2]
    if trace > 0:
        divisor = math.sqrt(trace + 1.0) * 2
        result = (
            (rotation_rows[2][1] - rotation_rows[1][2]) / divisor,
            (rotation_rows[0][2] - rotation_rows[2][0]) / divisor,
            (rotation_rows[1][0] - rotation_rows[0][1]) / divisor,
            0.25 * divisor,
        )
    elif rotation_rows[0][0] > rotation_rows[1][1] and rotation_rows[0][0] > rotation_rows[2][2]:
        divisor = math.sqrt(1.0 + rotation_rows[0][0] - rotation_rows[1][1] - rotation_rows[2][2]) * 2
        result = (
            0.25 * divisor,
            (rotation_rows[0][1] + rotation_rows[1][0]) / divisor,
            (rotation_rows[0][2] + rotation_rows[2][0]) / divisor,
            (rotation_rows[2][1] - rotation_rows[1][2]) / divisor,
        )
    elif rotation_rows[1][1] > rotation_rows[2][2]:
        divisor = math.sqrt(1.0 + rotation_rows[1][1] - rotation_rows[0][0] - rotation_rows[2][2]) * 2
        result = (
            (rotation_rows[0][1] + rotation_rows[1][0]) / divisor,
            0.25 * divisor,
            (rotation_rows[1][2] + rotation_rows[2][1]) / divisor,
            (rotation_rows[0][2] - rotation_rows[2][0]) / divisor,
        )
    else:
        divisor = math.sqrt(1.0 + rotation_rows[2][2] - rotation_rows[0][0] - rotation_rows[1][1]) * 2
        result = (
            (rotation_rows[0][2] + rotation_rows[2][0]) / divisor,
            (rotation_rows[1][2] + rotation_rows[2][1]) / divisor,
            0.25 * divisor,
            (rotation_rows[1][0] - rotation_rows[0][1]) / divisor,
        )
    length = math.sqrt(sum(component * component for component in result)) or 1.0
    result = tuple(component / length for component in result)
    return result if result[3] >= 0 else tuple(-component for component in result)


def to_columns(matrix: Matrix) -> tuple[float, ...]:
    """The 16 numbers of ``m`` in glTF's column-major order."""
    return tuple(matrix[i][j] for j in range(4) for i in range(4))


def from_columns(values) -> Matrix:
    flat = list(values)
    return tuple(tuple(float(flat[j * 4 + i]) for j in range(4)) for i in range(4))


def from_node(node: dict) -> Matrix:
    """A glTF node's local matrix (``matrix``, or ``translation`` / ``rotation`` / ``scale``)."""
    if "matrix" in node:
        return from_columns(node["matrix"])
    return from_rotation_translation(
        tuple(node.get("rotation", (0.0, 0.0, 0.0, 1.0))),
        tuple(node.get("translation", (0.0, 0.0, 0.0))),
        tuple(node.get("scale", (1.0, 1.0, 1.0))),
    )


def apply(matrix: Matrix, point: Vec3) -> Vec3:
    return tuple(
        matrix[i][0] * point[0] + matrix[i][1] * point[1] + matrix[i][2] * point[2] + matrix[i][3] for i in range(3)
    )


def is_identity(matrix: Matrix, tolerance: float = 1e-6) -> bool:
    return all(abs(matrix[i][j] - IDENTITY[i][j]) <= tolerance for i in range(4) for j in range(4))


MIRROR_X: Matrix = ((-1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def mirrored(matrix: Matrix) -> Matrix:
    """``m`` seen in the space mirrored on x (``S m S``): what a joint's transform becomes when the mesh is."""
    return multiply(MIRROR_X, multiply(matrix, MIRROR_X))


def mirrored_rotation(rotation: Quat) -> Quat:
    """A rotation seen in the space mirrored on x: exact, a sign change of y and z."""
    return (rotation[0], -rotation[1], -rotation[2], rotation[3])


def mirrored_point(point: Vec3) -> Vec3:
    return (-point[0], point[1], point[2])
