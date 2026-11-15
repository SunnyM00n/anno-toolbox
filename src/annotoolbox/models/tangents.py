"""Tangent frames from positions and UVs: the normal, tangent and bitangent a mesh that lacks them gets.

Per triangle the tangent is the direction of growing ``u`` and the bitangent that of growing ``v`` on the surface
(``dP/du``, ``dP/dv``, solved from the two edges and the UV differences). A vertex sums its triangles' vectors,
each weighted by the triangle's angle at the vertex, then the frame is made orthonormal: the tangent is turned
perpendicular to the normal (Gram-Schmidt) and the bitangent is ``N x T`` with the sign the UV mapping has
(a mirrored mapping flips it). The packed element stores the negated vectors, as 2070 and vanilla 117 have
them (docs/formats/rdm.md, "Vertex elements").
"""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]

_EPS = 1e-12


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v: Vec3) -> Vec3 | None:
    n = math.sqrt(_dot(v, v))
    return _scale(v, 1.0 / n) if n > _EPS else None


def _perpendicular(n: Vec3) -> Vec3:
    """Some unit vector perpendicular to the unit vector ``n`` (the frame of a vertex whose UVs say nothing)."""
    axis = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    return _unit(_cross(n, axis)) or (1.0, 0.0, 0.0)


def _angle(a: Vec3, b: Vec3) -> float:
    """The angle between two edge vectors (0 for a zero edge)."""
    na, nb = math.sqrt(_dot(a, a)), math.sqrt(_dot(b, b))
    if na < _EPS or nb < _EPS:
        return 0.0
    return math.acos(max(-1.0, min(1.0, _dot(a, b) / (na * nb))))


def frames(
    positions: list[Vec3],
    uvs: list[tuple[float, float]] | None,
    indices: list[int],
    normals: list[Vec3] | None = None,
) -> tuple[list[Vec3], list[Vec3], list[Vec3]]:
    """Unit normal, tangent (``dP/du``) and bitangent (``dP/dv`` up to sign convention: ``N x T`` times the mapping's
    handedness) per vertex, mutually perpendicular.

    ``normals`` are the mesh's own where it has them (they are kept, only normalised); without, they are the
    angle-weighted face normals. ``uvs`` None or a degenerate mapping (all triangles of a vertex have no UV area)
    gives an arbitrary but valid frame around the normal."""
    n = len(positions)
    face_n = [(0.0, 0.0, 0.0)] * n
    tan = [(0.0, 0.0, 0.0)] * n
    bit = [(0.0, 0.0, 0.0)] * n
    for t in range(0, len(indices) - 2, 3):
        i0, i1, i2 = indices[t : t + 3]
        p = (positions[i0], positions[i1], positions[i2])
        e1, e2 = _sub(p[1], p[0]), _sub(p[2], p[0])
        fn = _unit(_cross(e1, e2))
        if fn is None:
            continue  # a sliver has no direction
        tangent = bitangent = None
        if uvs is not None:
            (u0, v0), (u1, v1), (u2, v2) = uvs[i0], uvs[i1], uvs[i2]
            du1, dv1, du2, dv2 = u1 - u0, v1 - v0, u2 - u0, v2 - v0
            det = du1 * dv2 - du2 * dv1
            if abs(det) > _EPS:
                r = 1.0 / det
                tangent = _unit(tuple((e1[k] * dv2 - e2[k] * dv1) * r for k in range(3)))  # type: ignore[arg-type]
                bitangent = _unit(tuple((e2[k] * du1 - e1[k] * du2) * r for k in range(3)))  # type: ignore[arg-type]
        for k, i in enumerate((i0, i1, i2)):
            w = _angle(_sub(p[(k + 1) % 3], p[k]), _sub(p[(k + 2) % 3], p[k]))
            face_n[i] = _add(face_n[i], _scale(fn, w))
            if tangent is not None and bitangent is not None:
                tan[i] = _add(tan[i], _scale(tangent, w))
                bit[i] = _add(bit[i], _scale(bitangent, w))
    out_n, out_t, out_b = [], [], []
    for i in range(n):
        nn = _unit(normals[i]) if normals is not None else None
        nn = nn or _unit(face_n[i]) or (0.0, 1.0, 0.0)
        tt = _unit(_sub(tan[i], _scale(nn, _dot(tan[i], nn))))  # perpendicular to the normal
        if tt is None:
            tt = _perpendicular(nn)
            bb = _cross(nn, tt)
        else:
            bb = _cross(nn, tt)
            if _dot(bb, bit[i]) < 0:  # a mirrored mapping: v runs against N x T
                bb = _scale(bb, -1.0)
        out_n.append(nn)
        out_t.append(tt)
        out_b.append(bb)
    return out_n, out_t, out_b


def stored(vector: Vec3) -> Vec3:
    """A tangent or bitangent as the vertex element keeps it: negated (2070's convention, 117's too)."""
    return (-vector[0], -vector[1], -vector[2])
