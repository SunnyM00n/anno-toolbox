"""Mesh tooling on synthetic meshes, checked in numbers: tangent frames from UVs, transforms baked into the vertex
elements (normals by the inverse transpose), skeleton-less meshes in a skinned file."""

import math
import struct

import pytest
from tests.rdmfiles import N4B, P4H, SKIN_LAYOUT, T2H, joint, mesh_data, vertex

from annotoolbox.formats import rdmread
from annotoolbox.formats.rdmread import SSkeleton
from annotoolbox.formats.rdmwrite import write_rdm
from annotoolbox.models import meshxform, rdmnative, tangents

Vec = tuple[float, float, float]


def dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def unit_err(v) -> float:
    return abs(math.sqrt(dot(v, v)) - 1.0)


# Tangent frames

QUAD = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 0.0, -1.0), (0.0, 0.0, -1.0)]  # a floor in the xz plane, normal +y
QUAD_UV = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]  # u grows along +x, v along -z
QUAD_IDX = [0, 1, 2, 0, 2, 3]


def test_the_frame_is_orthonormal_and_follows_the_uv_directions():
    n, t, b = tangents.frames(QUAD, QUAD_UV, QUAD_IDX)
    for nn, tt, bb in zip(n, t, b, strict=True):
        assert nn == pytest.approx((0, 1, 0)) and tt == pytest.approx((1, 0, 0))  # tangent = dP/du direction
        assert bb == pytest.approx((0, 0, -1))  # bitangent = dP/dv direction
        assert abs(dot(nn, tt)) < 1e-9 and abs(dot(tt, bb)) < 1e-9 and abs(dot(nn, bb)) < 1e-9
        assert max(unit_err(nn), unit_err(tt), unit_err(bb)) < 1e-9


def test_a_mirrored_mapping_flips_the_bitangent_and_a_given_normal_is_kept():
    mirrored = [(1.0 - u, v) for u, v in QUAD_UV]  # u now runs along -x
    _n, t, b = tangents.frames(QUAD, mirrored, QUAD_IDX)
    assert t[0] == pytest.approx((-1, 0, 0)) and b[0] == pytest.approx((0, 0, -1))
    tilted = (0.0, 0.8, 0.6)  # a smoothing normal that is not the face normal: the tangent turns perpendicular to it
    n, t, b = tangents.frames(QUAD, QUAD_UV, QUAD_IDX, [tilted] * 4)
    assert n[0] == pytest.approx(tilted) and abs(dot(n[0], t[0])) < 1e-9 and abs(dot(t[0], b[0])) < 1e-9
    assert dot(t[0], (1, 0, 0)) > 0.99  # still along u


def test_a_mesh_without_uv_area_still_gets_a_valid_frame():
    n, t, b = tangents.frames(QUAD, [(0.0, 0.0)] * 4, QUAD_IDX)
    assert n[0] == pytest.approx((0, 1, 0))
    assert abs(dot(n[0], t[0])) < 1e-9 and abs(dot(t[0], b[0])) < 1e-9 and unit_err(t[0]) < 1e-9
    assert tangents.frames([(0.0, 0.0, 0.0)] * 3, None, [0, 1, 2])[1][0] != (0.0, 0.0, 0.0)  # a sliver: no zero vector


def plane_rows(normal=(0x80, 0xFF, 0x80, 0), uvs=QUAD_UV, with_normal=True):
    rows = []
    for p, uv in zip(QUAD, uvs, strict=True):
        row = struct.pack("<4e", *p, 0.0) + (bytes(normal) if with_normal else b"") + struct.pack("<2e", *uv)
        rows.append(row)
    return rows


def decode(b: bytes) -> Vec:
    return rdmnative.direction(b)


def test_the_converter_derives_the_tangent_frame_a_layout_lacks_from_the_uvs():
    mesh = mesh_data("floor", layout=(P4H, N4B, T2H), rows=plane_rows())
    mesh.indices = QUAD_IDX
    done = rdmnative.convert_static(write_rdm(mesh))
    out = rdmread.read_rdm(done.data).meshes[0]
    row = out.vertices[: out.stride]
    n, t, b = (decode(row[o : o + 4]) for o in (8, 12, 16))
    assert n == pytest.approx((0, 1, 0), abs=0.01)  # the source normal is kept
    assert t == pytest.approx((-1, 0, 0), abs=0.01)  # stored as 2070 stores it: -dP/du
    assert b == pytest.approx((0, 0, 1), abs=0.01)  # -dP/dv
    assert any("computed from the UVs" in x for x in done.notes.m_filled)


def test_a_mesh_without_normals_gets_them_from_its_triangles():
    mesh = mesh_data("floor", layout=(P4H, T2H), rows=plane_rows(with_normal=False))
    mesh.indices = QUAD_IDX
    done = rdmnative.convert_static(write_rdm(mesh))
    out = rdmread.read_rdm(done.data).meshes[0]
    n = decode(out.vertices[8:12])
    assert n == pytest.approx((0, 1, 0), abs=0.01) and any(
        "computed from the triangles" in x for x in done.notes.m_filled
    )


# Transforms baked into the vertices


def slope_mesh():
    """A plane through the origin tilted 45 degrees about z: normal (-1, 1, 0)/sqrt(2), the surface runs along (1, 1, 0)."""
    s = math.sqrt(0.5)
    pts = [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.0, 1.0)]
    normal = bytes(round((c + 1) * 127.5) for c in (-s, s, 0.0)) + b"\0"
    rows = [
        struct.pack("<4e", *p, 0.0) + normal + struct.pack("<2e", u, v) for p, (u, v) in zip(pts, QUAD_UV, strict=True)
    ]
    mesh = mesh_data("slope", layout=(P4H, N4B, T2H), rows=rows)
    mesh.indices = QUAD_IDX
    return write_rdm(mesh)


def baked(matrix):
    out = rdmread.read_rdm(rdmnative.convert_static(slope_mesh(), matrix=matrix).data).meshes[0]
    row = out.vertices[: out.stride]
    return out, [decode(row[o : o + 4]) for o in (8, 12, 16)]


def test_a_baked_scale_moves_normals_by_the_inverse_transpose_and_tangents_by_the_matrix():
    stretch = ((2.0, 0, 0), (0, 1.0, 0), (0, 0, 1.0))  # twice as wide in x: the slope gets flatter
    out, (n, t, b) = baked(stretch)
    assert out.positions()[1] == pytest.approx((2.0, 1.0, 0.0))
    assert n == pytest.approx((-0.5 / math.sqrt(1.25), 1.0 / math.sqrt(1.25), 0.0), abs=0.01)  # not (-2, 1, 0)
    assert unit_err(n) < 0.02 and abs(dot(n, (2.0, 1.0, 0.0))) < 0.02  # still perpendicular to the stretched surface
    assert abs(dot(n, t)) < 0.03 and abs(dot(t, b)) < 0.03  # the frame stays perpendicular (computed tangents)


def test_a_mirror_reverses_the_winding_and_a_turn_keeps_normals_perpendicular():
    flip = ((-1.0, 0, 0), (0, 1.0, 0), (0, 0, 1.0))
    out, (n, *_rest) = baked(flip)
    assert out.indices[:6] == [0, 2, 1, 0, 3, 2]  # each triangle's second and third corner swapped
    assert n == pytest.approx((math.sqrt(0.5), math.sqrt(0.5), 0.0), abs=0.01)
    quarter = ((0.0, 0, 1.0), (0, 1.0, 0), (-1.0, 0, 0))  # a quarter turn about y
    out, (n, *_rest) = baked(quarter)
    assert out.indices[:6] == [0, 1, 2, 0, 2, 3] and out.bbox == pytest.approx((0, 0, -1, 1, 1, 0), abs=0.01)
    assert n == pytest.approx((0.0, math.sqrt(0.5), math.sqrt(0.5)), abs=0.01)


def test_the_normal_matrix_is_the_inverse_transpose_and_survives_a_flat_matrix():
    m = ((2.0, 0.0, 0.0), (1.0, 3.0, 0.0), (0.0, 0.0, 0.5))
    inverse_t = meshxform.normal_matrix(m)
    # (M^-1)^T up to a positive factor: M^T * N is a multiple of the identity
    product = [[sum(m[k][i] * inverse_t[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    assert product[0][1] == pytest.approx(0) and product[1][0] == pytest.approx(0) and product[0][0] > 0
    assert product[0][0] == pytest.approx(product[1][1]) == pytest.approx(product[2][2])
    flat = ((1.0, 0, 0), (0, 0.0, 0), (0, 0, 1.0))  # y collapsed: the normal along y survives
    assert meshxform.apply(meshxform.normal_matrix(flat), (0.0, 1.0, 0.0)) != (0.0, 0.0, 0.0)


# Skeleton-less meshes in a skinned file


def test_a_mesh_without_a_skeleton_hangs_on_a_joint_nothing_animates():
    sk = [SSkeleton([joint("root", None), joint("arm", 0, 1.0)], 0)]
    rows = [vertex(i, bones=(i % 2, 0, 0, 0), weights=(255, 0, 0, 0)) for i in range(4)]
    moving = mesh_data("arm", layout=SKIN_LAYOUT, rows=rows, skeleton=0, bones=1)
    still = mesh_data("prop", layout=(P4H, N4B, T2H), rows=[vertex(i) for i in range(4)], skeleton=None)
    done = rdmnative.convert_skinned(write_rdm(moving, still, skeletons=sk))
    out = rdmread.read_rdm(done.data)
    names = [j.name for j in out.skeletons[0].joints]
    at = names.index(rdmnative.STATIC_JOINT)
    assert names[:2] == ["root", "arm"] and at == 2 and out.skeletons[0].joints[at].parent is None
    a, b = out.meshes
    assert [v[0] for v in a.element(5)] == [0, 1, 0, 1]  # the moving mesh stays on its own skeleton
    assert set(b.element(5)) == {(at, 0, 0, 0)} and set(b.element(6)) == {(255, 0, 0, 0)}  # not welded to joint 0
    assert out.skeletons[0].joints[at].position == (0.0, 0.0, 0.0) and out.skeletons[0].joints[at].rotation[3] == 1.0


def test_quaternions_as_tuples_turn_compose_and_build_from_axes_and_euler_angles():
    quarter_z = meshxform.qaxis((0, 0, 3), math.pi / 2)  # the axis length does not matter
    assert meshxform.qrotate(quarter_z, (1, 0, 0)) == pytest.approx((0, 1, 0))
    assert meshxform.qaxis((0, 0, 0), 1.0) == (0.0, 0.0, 0.0, pytest.approx(math.cos(0.5)))  # no axis: no turn
    half_turn = meshxform.qmul(quarter_z, quarter_z)  # "a after b"
    assert meshxform.qrotate(half_turn, (1, 0, 0)) == pytest.approx((-1, 0, 0))
    quarter_x = meshxform.qaxis((1, 0, 0), math.pi / 2)
    both = meshxform.qmul(quarter_z, quarter_x)
    assert meshxform.qrotate(both, (0, 1, 0)) == pytest.approx(meshxform.qrotate(quarter_z, (0, 0, 1)))
    assert meshxform.euler_quat(math.pi / 2, 0, math.pi / 2) == pytest.approx(both)  # x first, then z


def test_a_conjugate_turns_back_and_a_zero_quaternion_normalises_to_the_identity():
    q = meshxform.qaxis((0.0, 1.0, 0.0), 1.0)
    assert meshxform.qmul(q, meshxform.qconj(q)) == pytest.approx((0.0, 0.0, 0.0, 1.0))
    assert meshxform.qnormalize((0.0, 0.0, 0.0, 2.0)) == (0.0, 0.0, 0.0, 1.0)
    assert meshxform.qnormalize((0.0, 0.0, 0.0, 0.0)) == (0.0, 0.0, 0.0, 1.0)
    assert math.isclose(sum(c * c for c in meshxform.qnormalize((1.0, 2.0, 3.0, 4.0))), 1.0)
