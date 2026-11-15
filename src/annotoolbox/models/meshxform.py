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


def qmul(a: Vec4, b: Vec4) -> Vec4:
    """The Hamilton product of two quaternions: ``a`` applied after ``b``."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def qrotate(q: Vec4, v: Vec3) -> Vec3:
    """The vector ``v`` turned by the unit quaternion ``q``."""
    x, y, z, w = q
    tx, ty, tz = 2 * (y * v[2] - z * v[1]), 2 * (z * v[0] - x * v[2]), 2 * (x * v[1] - y * v[0])
    return (v[0] + w * tx + (y * tz - z * ty), v[1] + w * ty + (z * tx - x * tz), v[2] + w * tz + (x * ty - y * tx))


def qconj(q: Vec4) -> Vec4:
    """The conjugate: the turn the other way round (the inverse of a unit quaternion)."""
    return (-q[0], -q[1], -q[2], q[3])


def qnormalize(q: Vec4) -> Vec4:
    """The unit quaternion along ``q``; the identity for a zero one."""
    length = math.sqrt(sum(c * c for c in q))
    if length == 0.0:
        return (0.0, 0.0, 0.0, 1.0)
    return (q[0] / length, q[1] / length, q[2] / length, q[3] / length)


def qaxis(axis: Vec3, angle: float) -> Vec4:
    """The quaternion that turns by ``angle`` radians about ``axis`` (any length; none: no turn)."""
    length = math.sqrt(sum(a * a for a in axis)) or 1.0
    s = math.sin(angle / 2) / length
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(angle / 2))


def euler_quat(rx: float, ry: float, rz: float) -> Vec4:
    """Euler radians as a quaternion, x applied first."""
    out: Vec4 = (0.0, 0.0, 0.0, 1.0)
    for axis, angle in (((1.0, 0.0, 0.0), rx), ((0.0, 1.0, 0.0), ry), ((0.0, 0.0, 1.0), rz)):
        out = qmul(qaxis(axis, angle), out)
    return out


# Matrices


def determinant(m: Matrix) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def cofactor(m: Matrix) -> Matrix:
    """The cofactor matrix: ``det(M)`` times the inverse transpose, and still a direction for a flat ``M``."""

    def minor(r: int, c: int) -> float:
        a, b = [i for i in range(3) if i != r], [i for i in range(3) if i != c]
        return m[a[0]][b[0]] * m[a[1]][b[1]] - m[a[0]][b[1]] * m[a[1]][b[0]]

    return tuple(  # type: ignore[return-value]
        tuple((-1) ** (r + c) * minor(r, c) for c in range(3)) for r in range(3)
    )


def apply(m: Matrix, v: Vec3) -> Vec3:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def normal_matrix(m: Matrix) -> Matrix:
    """The matrix that carries normals: the inverse transpose up to a positive factor (the cofactor matrix, its
    sign made positive for a mirroring ``m``; a normal is renormalised afterwards)."""
    c = cofactor(m)
    if determinant(m) >= 0:
        return c
    return tuple(tuple(-x for x in row) for row in c)  # type: ignore[return-value]


def transformed_direction(m: Matrix, b: bytes) -> bytes:
    """A packed direction moved by ``m`` and made a unit vector again; a direction ``m`` collapses stays as it was."""
    v = apply(m, direction(b))
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n < _EPS:
        return b
    return pack_direction(v[0] / n, v[1] / n, v[2] / n)


def transformed_position(m: Matrix, b: bytes) -> bytes:
    """A packed ``4h`` position moved by ``m``."""
    return struct.pack("<4e", *apply(m, struct.unpack("<3e", b[:6])), 0.0)


def column(usage: int, make: Callable[[bytes, int], bytes], m: Matrix) -> Callable[[bytes, int], bytes]:
    """``make`` (a vertex element's bytes from a source row) with ``m`` applied to what it makes: positions (usage 0),
    normals (1) or tangents and bitangents (2, 3); any other element is returned as it is."""
    if usage == 0:
        return lambda x, v: transformed_position(m, make(x, v))
    if usage == 1:
        nm = normal_matrix(m)
        return lambda x, v: transformed_direction(nm, make(x, v))
    if usage in (2, 3):
        return lambda x, v: transformed_direction(m, make(x, v))
    return make


def mirrored(m: Matrix) -> bool:
    """Whether ``m`` turns the surface inside out, so a triangle's winding (front face) must be reversed."""
    return determinant(m) < 0


def reversed_winding(indices: list[int]) -> list[int]:
    """Every triangle's second and third corner swapped."""
    out = list(indices)
    for t in range(0, len(out) - 2, 3):
        out[t + 1], out[t + 2] = out[t + 2], out[t + 1]
    return out
