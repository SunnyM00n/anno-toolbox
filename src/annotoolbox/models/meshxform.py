"""A linear transform baked into a mesh's vertex elements: positions by the matrix, normals by the inverse
transpose, tangents and bitangents by the matrix, all renormalised; a matrix that mirrors reverses the winding.

A normal is perpendicular to the surface, so it must stay perpendicular when the surface is scaled unevenly: it
moves by ``(M^-1)^T``, which the cofactor matrix gives (``cof(M) = det(M) * (M^-1)^T``, so it also exists when M
flattens an axis). A tangent lies in the surface and moves by ``M`` itself. The elements are the packed ones the
writer produces (``rdmwrite``: positions ``4h``, directions ``4b``), so a position keeps half-float precision.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable

from ..formats.rdmlayout import direction
from ..formats.rdmwrite import pack_direction

Matrix = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
Vec3 = tuple[float, float, float]
Vec4 = tuple[float, float, float, float]
_EPS = 1e-12


# Quaternions as (x, y, z, w) tuples


def qmul(first: Vec4, second: Vec4) -> Vec4:
    """The Hamilton product of two quaternions: ``first`` applied after ``second``."""
    ax, ay, az, aw = first
    bx, by, bz, bw = second
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def qrotate(quaternion: Vec4, vector: Vec3) -> Vec3:
    """The vector ``vector`` turned by the unit quaternion ``quaternion``."""
    x, y, z, w = quaternion
    cross_x, cross_y, cross_z = (
        2 * (y * vector[2] - z * vector[1]),
        2 * (z * vector[0] - x * vector[2]),
        2 * (x * vector[1] - y * vector[0]),
    )
    return (
        vector[0] + w * cross_x + (y * cross_z - z * cross_y),
        vector[1] + w * cross_y + (z * cross_x - x * cross_z),
        vector[2] + w * cross_z + (x * cross_y - y * cross_x),
    )


def qconj(quaternion: Vec4) -> Vec4:
    """The conjugate: the turn the other way round (the inverse of a unit quaternion)."""
    return (-quaternion[0], -quaternion[1], -quaternion[2], quaternion[3])


def qnormalize(quaternion: Vec4) -> Vec4:
    """The unit quaternion along ``quaternion``; the identity for a zero one."""
    length = math.sqrt(sum(value * value for value in quaternion))
    if length == 0.0:
        return (0.0, 0.0, 0.0, 1.0)
    return (quaternion[0] / length, quaternion[1] / length, quaternion[2] / length, quaternion[3] / length)


def qaxis(axis: Vec3, angle_rad: float) -> Vec4:
    """The quaternion that turns by ``angle_rad`` radians about ``axis`` (any length; none: no turn)."""
    length = math.sqrt(sum(first * first for first in axis)) or 1.0
    scale = math.sin(angle_rad / 2) / length
    return (axis[0] * scale, axis[1] * scale, axis[2] * scale, math.cos(angle_rad / 2))


def euler_quat(rotation_x_rad: float, rotation_y_rad: float, rotation_z_rad: float) -> Vec4:
    """Euler radians as a quaternion, x applied first."""
    out: Vec4 = (0.0, 0.0, 0.0, 1.0)
    for axis, angle in (
        ((1.0, 0.0, 0.0), rotation_x_rad),
        ((0.0, 1.0, 0.0), rotation_y_rad),
        ((0.0, 0.0, 1.0), rotation_z_rad),
    ):
        out = qmul(qaxis(axis, angle), out)
    return out


# Matrices


def determinant(matrix: Matrix) -> float:
    return (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
    )


def cofactor(matrix: Matrix) -> Matrix:
    """The cofactor matrix: ``det(M)`` times the inverse transpose, and still a direction for a flat ``M``."""

    def minor(skipped_row: int, skipped_column: int) -> float:
        rows, columns = [i for i in range(3) if i != skipped_row], [i for i in range(3) if i != skipped_column]
        return (
            matrix[rows[0]][columns[0]] * matrix[rows[1]][columns[1]]
            - matrix[rows[0]][columns[1]] * matrix[rows[1]][columns[0]]
        )

    return tuple(  # type: ignore[return-value]
        tuple((-1) ** (row_index + column_index) * minor(row_index, column_index) for column_index in range(3))
        for row_index in range(3)
    )


def apply(matrix: Matrix, vector: Vec3) -> Vec3:
    return (
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
        matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
    )


def normal_matrix(matrix: Matrix) -> Matrix:
    """The matrix that carries normals: the inverse transpose up to a positive factor (the cofactor matrix, its
    sign made positive for a mirroring ``matrix``; a normal is renormalised afterwards)."""
    cofactors = cofactor(matrix)
    if determinant(matrix) >= 0:
        return cofactors
    return tuple(tuple(-x for x in row) for row in cofactors)  # type: ignore[return-value]


def transformed_direction(matrix: Matrix, data: bytes) -> bytes:
    """A packed direction moved by ``matrix`` and made a unit vector again; a direction ``matrix`` collapses stays as it was."""
    vector = apply(matrix, direction(data))
    length = math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2])
    if length < _EPS:
        return data
    return pack_direction(vector[0] / length, vector[1] / length, vector[2] / length)


def transformed_position(matrix: Matrix, data: bytes) -> bytes:
    """A packed ``4h`` position moved by ``matrix``."""
    return struct.pack("<4e", *apply(matrix, struct.unpack("<3e", data[:6])), 0.0)


def column(usage: int, make: Callable[[bytes, int], bytes], matrix: Matrix) -> Callable[[bytes, int], bytes]:
    """``make`` (a vertex element's bytes from a source row) with ``matrix`` applied to what it makes: positions (usage 0),
    normals (1) or tangents and bitangents (2, 3); any other element is returned as it is."""
    if usage == 0:
        return lambda x, vector: transformed_position(matrix, make(x, vector))
    if usage == 1:
        normals = normal_matrix(matrix)
        return lambda x, vector: transformed_direction(normals, make(x, vector))
    if usage in (2, 3):
        return lambda x, vector: transformed_direction(matrix, make(x, vector))
    return make


def mirrored(matrix: Matrix) -> bool:
    """Whether ``matrix`` turns the surface inside out, so a triangle's winding (front face) must be reversed."""
    return determinant(matrix) < 0


def reversed_winding(indices: list[int]) -> list[int]:
    """Every triangle's second and third corner swapped."""
    out = list(indices)
    for triangle in range(0, len(out) - 2, 3):
        out[triangle + 1], out[triangle + 2] = out[triangle + 2], out[triangle + 1]
    return out
