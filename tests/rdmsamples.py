"""Synthetic RDM files for the toolchain's tests: every list of the format filled with values that are not trivial.

``static_file`` (two meshes, two materials, two UV sets, colours), ``skinned_file`` (turned joints, four weights,
two skeletons, a rigid part), ``animation_file`` (two entries, an unbound and an empty track). Built with
``rdmwrite`` from numbers only; no game file.
"""

from __future__ import annotations

import math
import struct

from annotoolbox.formats.rdmread import Animation, Joint, Keyframe, Skeleton, Track, VertexElement
from annotoolbox.formats.rdmwrite import RdmMeshData, RdmSubmesh, pack_direction, write_animation_rdm, write_rdm

P4H, N4B, G4B, B4B, T2H = (0, 6, 0, 4), (1, 5, 6, 1), (2, 5, 6, 1), (3, 5, 6, 1), (4, 6, 0, 2)
C4B, C4B_2, I4B, W4B = (5, 5, 2, 1), (5, 5, 6, 1), (7, 5, 0, 1), (6, 5, 2, 1)


def layout(*rows) -> tuple[VertexElement, ...]:
    return tuple(VertexElement(*r) for r in rows)


def _grid(n: int) -> tuple[list[tuple[float, float, float]], list[int]]:
    """An n x n sheet of quads, a little bent so its normals differ."""
    pos = [(x * 0.5, 0.1 * math.sin(x + z), z * 0.5) for z in range(n) for x in range(n)]
    pos[0] = (-0.0, pos[0][1], -0.0)  # a negative zero, which a mirror must keep
    tri = []
    for z in range(n - 1):
        for x in range(n - 1):
            a, b, c, d = z * n + x, z * n + x + 1, (z + 1) * n + x, (z + 1) * n + x + 1
            tri += [a, c, b, b, c, d]
    return pos, tri


def _row(p, k: int, *, colours: int = 0, uv2: bool = False, bones=None, weights=None) -> bytes:
    out = struct.pack("<4e", *p, 1.0 if k % 2 else 0.0)  # a 4th position value that varies
    out += pack_direction(0.1, 0.9734, 0.2059)[:3] + bytes((k % 3,))  # a unit normal; a 4th byte that varies
    out += pack_direction(0.99, -0.05, 0.1) + pack_direction(0.0, 0.2, 0.98)
    out += struct.pack("<2e", p[0] / 4, p[2] / 4)
    if uv2:
        out += struct.pack("<2e", 0.5 + p[0], 0.25 - p[2])
    for c in range(colours):
        out += bytes(((k * 7 + c) % 256, 128, 255 - k % 256, 200 + c))
    if bones is not None:
        out += bytes(bones) + bytes(weights)
    return out


def static_meshes() -> list[RdmMeshData]:
    pos, tri = _grid(4)
    lay_a = layout(P4H, N4B, G4B, B4B, T2H, C4B, C4B_2)
    rows_a = [_row(p, k, colours=2) for k, p in enumerate(pos)]
    a = RdmMeshData("house_lod0", b"".join(rows_a), 32, lay_a, tri, ["roof", "wall"], [], (0.0,) * 6, "scene.max")
    a.submeshes = [RdmSubmesh(0, 30, 1), RdmSubmesh(30, len(tri) - 30, 0)]
    a.bbox = (-0.5, -0.25, -0.5, 2.0, 0.5, 2.0)  # looser than the vertices', as some shipped boxes are
    a.export_material, a.textures = "Tangent.rmp", ["data/roof_diff.psd", ""]
    pos_b, tri_b = _grid(3)
    lay_b = layout(P4H, N4B, G4B, B4B, T2H, (4, 6, 0, 2))
    rows_b = [_row(p, k, uv2=True) for k, p in enumerate(pos_b)]
    box_b = (0.0, -0.1, 0.0, 1.0, 0.1, 1.0)
    b = RdmMeshData("", b"".join(rows_b), 28, lay_b, tri_b, [], [RdmSubmesh(0, len(tri_b), 0)], box_b)
    return [a, b]


def static_file() -> bytes:
    """Two meshes (the second without a name), two materials, one with a texture path; written without placeholders."""
    return write_rdm(*static_meshes(), placeholder="")


def one_mesh_file() -> bytes:
    """The first mesh of ``static_file`` alone (the tests' byte-order helper swaps a file of one mesh)."""
    return write_rdm(static_meshes()[0], placeholder="")


def _turned(angle: float, axis=(0.0, 1.0, 0.0)) -> tuple[float, float, float, float]:
    s = math.sin(angle / 2)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(angle / 2))


def skeletons() -> list[Skeleton]:
    def joint(name, parent, pos, angle, box=0.2):
        return Joint(name, pos, _turned(angle), parent, (-box, -box, -box), (box, 2 * box, box))

    first = Skeleton([joint("root", None, (0.1, 0.0, -0.2), 0.3), joint("arm", 0, (-1.0, -0.5, 0.0), -0.7)], 0)
    first.joints.append(joint("hand", 1, (-2.0, -0.5, 0.1), 1.1))
    second = Skeleton([joint("door", None, (3.0, 0.0, 0.0), 0.0, 0.0)], 1)
    return [first, second]


def skinned_meshes() -> list[RdmMeshData]:
    pos, tri = _grid(3)
    lay = layout(P4H, N4B, G4B, B4B, T2H, I4B, W4B)
    rows = []
    for k, p in enumerate(pos):
        bones = (k % 3, (k + 1) % 3, 0, 2)
        weights = (120, 100, 0, 35) if k % 2 else (255, 0, 0, 0)
        rows.append(_row(p, k, bones=bones, weights=weights))
    body = RdmMeshData("crane_lod0", b"".join(rows), 32, lay, tri, ["crane"], [RdmSubmesh(0, len(tri), 0)], (0.0,) * 6)
    body.skeleton, body.bones_per_vertex, body.export_name = 0, 4, "crane.max"
    body.bbox = (0.0, -0.1, 0.0, 1.0, 0.1, 1.0)
    rigid_pos, rigid_tri = _grid(2)
    rigid_lay = layout(P4H, N4B, T2H)
    rigid_rows = [
        struct.pack("<4e", *p, 0.0) + pack_direction(0, 1, 0) + struct.pack("<2e", 0.5, 0.5) for p in rigid_pos
    ]
    rigid = RdmMeshData(
        "door_lod0", b"".join(rigid_rows), 16, rigid_lay, rigid_tri, [], [RdmSubmesh(0, 6, 0)], (0.0,) * 6
    )
    rigid.skeleton = 1
    rigid.bbox = (0.0, 0.0, 0.0, 0.5, 0.0, 0.5)
    return [body, rigid]


def skinned_file() -> bytes:
    return write_rdm(*skinned_meshes(), skeletons=skeletons(), placeholder="")


def animations() -> list[Animation]:
    def keys(times, angle, move):
        return [Keyframe(_turned(angle * t, (0.0, 0.6, 0.8)), (move * t, 0.25, -move), t) for t in times]

    work = Animation("crane_lod0", 2000, 30, [Track("root", keys((0.0, 1.0, 2.0), 0.4, 0.5))])
    work.tracks += [Track("arm", keys((0.0, 0.5, 2.0), -0.9, 0.0)), Track("Point001", keys((0.0, 2.0), 0.1, 1.0))]
    work.tracks.append(Track("hand", []))  # an empty track: kept
    work.tracks.append(Track("arm", keys((0.0, 2.0), 0.2, 0.1)))  # a second track on one joint, as 117 ships
    door = Animation("door_lod0", 1000, 60, [Track("door", keys((0.0, 1.0), 1.5, 0.0))])
    return [work, door]


def animation_file() -> bytes:
    """Two entries, one per model it moves (as 2070 ships), one track on no joint of the mesh, one without keys."""
    return write_animation_rdm(animations(), "crane.max", "anim.rmp", placeholder="")
