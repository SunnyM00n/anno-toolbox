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


def _sub(first: Vec3, second: Vec3) -> Vec3:
    return (first[0] - second[0], first[1] - second[1], first[2] - second[2])


def _add(first: Vec3, second: Vec3) -> Vec3:
    return (first[0] + second[0], first[1] + second[1], first[2] + second[2])


def _scale(vector: Vec3, factor: float) -> Vec3:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def _dot(first: Vec3, second: Vec3) -> float:
    return first[0] * second[0] + first[1] * second[1] + first[2] * second[2]


def _cross(first: Vec3, second: Vec3) -> Vec3:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _unit(vector: Vec3) -> Vec3 | None:
    length = math.sqrt(_dot(vector, vector))
    return _scale(vector, 1.0 / length) if length > _EPS else None


def _perpendicular(normal: Vec3) -> Vec3:
    """Some unit vector perpendicular to the unit vector ``normal`` (the frame of a vertex whose UVs say nothing)."""
    axis = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    return _unit(_cross(normal, axis)) or (1.0, 0.0, 0.0)


def _angle(first: Vec3, second: Vec3) -> float:
    """The angle between two edge vectors (0 for a zero edge)."""
    first_length, second_length = math.sqrt(_dot(first, first)), math.sqrt(_dot(second, second))
    if first_length < _EPS or second_length < _EPS:
        return 0.0
    return math.acos(max(-1.0, min(1.0, _dot(first, second) / (first_length * second_length))))


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
    vertex_count = len(positions)
    face_normals = [(0.0, 0.0, 0.0)] * vertex_count
    tangents = [(0.0, 0.0, 0.0)] * vertex_count
    bitangents = [(0.0, 0.0, 0.0)] * vertex_count
    for triangle_start in range(0, len(indices) - 2, 3):
        index_0, index_1, index_2 = indices[triangle_start : triangle_start + 3]
        corners = (positions[index_0], positions[index_1], positions[index_2])
        edge_1, edge_2 = _sub(corners[1], corners[0]), _sub(corners[2], corners[0])
        face_normal = _unit(_cross(edge_1, edge_2))
        if face_normal is None:
            continue  # a sliver has no direction
        tangent = bitangent = None
        if uvs is not None:
            (u_0, v_0), (u_1, v_1), (u_2, v_2) = uvs[index_0], uvs[index_1], uvs[index_2]
            du1, dv1, du2, dv2 = u_1 - u_0, v_1 - v_0, u_2 - u_0, v_2 - v_0
            det = du1 * dv2 - du2 * dv1
            if abs(det) > _EPS:
                inverse_det = 1.0 / det
                tangent = _unit(tuple((edge_1[k] * dv2 - edge_2[k] * dv1) * inverse_det for k in range(3)))  # type: ignore[arg-type]
                bitangent = _unit(tuple((edge_2[k] * du1 - edge_1[k] * du2) * inverse_det for k in range(3)))  # type: ignore[arg-type]
        for k, i in enumerate((index_0, index_1, index_2)):
            corner_weight = _angle(_sub(corners[(k + 1) % 3], corners[k]), _sub(corners[(k + 2) % 3], corners[k]))
            face_normals[i] = _add(face_normals[i], _scale(face_normal, corner_weight))
            if tangent is not None and bitangent is not None:
                tangents[i] = _add(tangents[i], _scale(tangent, corner_weight))
                bitangents[i] = _add(bitangents[i], _scale(bitangent, corner_weight))
    out_normals, out_tangents, out_bitangents = [], [], []
    for i in range(vertex_count):
        unit_normal = _unit(normals[i]) if normals is not None else None
        unit_normal = unit_normal or _unit(face_normals[i]) or (0.0, 1.0, 0.0)
        unit_tangent = _unit(
            _sub(tangents[i], _scale(unit_normal, _dot(tangents[i], unit_normal)))
        )  # perpendicular to the normal
        if unit_tangent is None:
            unit_tangent = _perpendicular(unit_normal)
            unit_bitangent = _cross(unit_normal, unit_tangent)
        else:
            unit_bitangent = _cross(unit_normal, unit_tangent)
            if _dot(unit_bitangent, bitangents[i]) < 0:  # a mirrored mapping: v runs against N x T
                unit_bitangent = _scale(unit_bitangent, -1.0)
        out_normals.append(unit_normal)
        out_tangents.append(unit_tangent)
        out_bitangents.append(unit_bitangent)
    return out_normals, out_tangents, out_bitangents


def stored(vector: Vec3) -> Vec3:
    """A tangent or bitangent as the vertex element keeps it: negated (2070's convention, 117's too)."""
    return (-vector[0], -vector[1], -vector[2])
