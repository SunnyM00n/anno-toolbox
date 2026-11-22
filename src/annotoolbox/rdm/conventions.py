"""How an ``.rdm`` deviates from the conventions of Anno 117's own files: header, layouts, indices, submeshes,
boxes, skeletons, weights and animations (docs/formats/rdm.md "117 conventions").

``deviations`` is the entry (``rdm validate``); ``classify`` names a
file's kind, ``layouts_of`` reads the layout table from a game's own files instead of the built-in copy.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable
from dataclasses import dataclass

from ..formats import rdmlayout
from ..formats.rdmread import CRdmReadError, SMesh, SRdmFile, read_rdm

LAYOUTS = rdmlayout.VANILLA_117
MAX_JOINTS = 256  # a bone index is one byte

SAMPLE = 400  # triangles looked at per mesh for the geometry statistics
UNIT = 0.9  # a packed normal this long counts as a direction


def classify(rdm: SRdmFile) -> str:
    if rdm.extra_streams:
        return "extra"
    if rdm.animations and not rdm.meshes:
        return "animation"
    if not rdm.meshes:
        return "empty"
    if any(mesh.elements and mesh.elements[0].type == 7 for mesh in rdm.meshes):
        return "cloth"
    return "skinned" if rdm.skeletons else "static"


def _decode(data: bytes) -> tuple[float, float, float]:
    return data[0] / 255 * 2 - 1, data[1] / 255 * 2 - 1, data[2] / 255 * 2 - 1


def _sample(count: int) -> range:
    return range(0, count, max(1, count // SAMPLE))


@dataclass
class _SStreams:
    """The vertex streams the geometry check reads (None where the layout has none)."""

    pos: list
    nrm: object = None
    tans: object = None
    bits: object = None
    uvs: object = None


def _streams(mesh: SMesh) -> _SStreams:
    streams = _SStreams(mesh.positions())
    for i, element in enumerate(mesh.elements):
        if element.usage == 1 and element.type == 5 and streams.nrm is None:
            streams.nrm = mesh.element(i)
        elif element.usage == 2 and element.type == 5:
            streams.tans = mesh.element(i)
        elif element.usage == 3 and element.type == 5:
            streams.bits = mesh.element(i)
        elif element.usage == 4 and streams.uvs is None and element.type in (6, 7):
            streams.uvs = mesh.element(i)
    return streams


def _winding(streams: _SStreams, first_edge: list, second_edge: list, triangle: tuple[int, int, int]) -> bool | None:
    """Whether the triangle is counter-clockwise with its vertex normals (None for a degenerate one)."""
    face_normal = (
        first_edge[1] * second_edge[2] - first_edge[2] * second_edge[1],
        first_edge[2] * second_edge[0] - first_edge[0] * second_edge[2],
        first_edge[0] * second_edge[1] - first_edge[1] * second_edge[0],
    )
    vertex_normal = [sum(_decode(bytes(streams.nrm[corner]))[k] for corner in triangle) for k in range(3)]
    alignment = sum(face_normal[k] * vertex_normal[k] for k in range(3))
    return alignment > 0 if abs(alignment) > 1e-12 else None


def _frame(streams: _SStreams, first_edge: list, second_edge: list, triangle: tuple[int, int, int]) -> dict[str, bool]:
    """Per tangent and bitangent that is a direction: whether it is -dP/du, -dP/dv at the first vertex."""
    first, second, third = triangle
    du1, dv1 = streams.uvs[second][0] - streams.uvs[first][0], streams.uvs[second][1] - streams.uvs[first][1]
    du2, dv2 = streams.uvs[third][0] - streams.uvs[first][0], streams.uvs[third][1] - streams.uvs[first][1]
    det = du1 * dv2 - du2 * dv1
    if abs(det) < 1e-12:
        return {}
    dpdu = [(first_edge[k] * dv2 - second_edge[k] * dv1) / det for k in range(3)]
    dpdv = [(second_edge[k] * du1 - first_edge[k] * du2) / det for k in range(3)]
    out = {}
    for name, stream, slope in (("tangent", streams.tans, dpdu), ("bitangent", streams.bits, dpdv)):
        direction = _decode(bytes(stream[first]))
        if math.dist(direction, (0, 0, 0)) > UNIT:
            out[name] = sum(slope[k] * direction[k] for k in range(3)) < 0
    return out


def _geometry(mesh: SMesh) -> dict[str, float]:
    """Winding against the vertex normals and the tangent frame against the UV derivatives, over a sample of
    triangles: the share that agrees with 117's convention (counter-clockwise with the normal, T = -dP/du,
    B = -dP/dv)."""
    streams = _streams(mesh)
    if not streams.pos or streams.nrm is None:
        return {}
    framed = streams.tans is not None and streams.bits is not None and streams.uvs is not None
    agree: dict[str, list[int]] = {"ccw": [0, 0], "tangent": [0, 0], "bitangent": [0, 0]}  # [agreeing, counted]
    for triangle_index in _sample(len(mesh.indices) // 3):
        triangle = tuple(mesh.indices[3 * triangle_index : 3 * triangle_index + 3])
        if max(triangle) >= len(
            streams.pos
        ):  # an index past the vertices is reported by the index check; not read here
            continue
        first_edge = [streams.pos[triangle[1]][k] - streams.pos[triangle[0]][k] for k in range(3)]
        second_edge = [streams.pos[triangle[2]][k] - streams.pos[triangle[0]][k] for k in range(3)]
        found = {"ccw": ccw} if (ccw := _winding(streams, first_edge, second_edge, triangle)) is not None else {}
        found |= _frame(streams, first_edge, second_edge, triangle) if framed else {}
        for name, ok in found.items():
            agree[name][0] += ok
            agree[name][1] += 1
    return {name: ok / counted for name, (ok, counted) in agree.items() if counted}


def geometry_stats(data: bytes) -> dict[str, float]:
    """The mean of :func:`_geometry` over a file's meshes (what a relayout must not change)."""
    rows = [agreement for mesh in read_rdm(data).meshes if (agreement := _geometry(mesh))]
    return {
        k: sum(agreement[k] for agreement in rows if k in agreement) / sum(1 for agreement in rows if k in agreement)
        for k in ("ccw", "tangent", "bitangent")
        if any(k in agreement for agreement in rows)
    }


@dataclass(frozen=True)
class _SCheck:
    """What a convention check looks at: the file, the layouts vanilla uses, the box rule."""

    rdm: SRdmFile
    layouts: dict[str, Iterable]
    strict: bool


def _layout(mesh: SMesh, who: str, check: _SCheck) -> list[str]:
    elements = tuple((element.usage, element.type, element.subtype, element.count) for element in mesh.elements)
    if elements in check.layouts.get(mesh.format, ()):
        return []
    return [f"{who}: layout {mesh.format} {elements} is not a layout of 117's own files"]


def _stride(mesh: SMesh, who: str, check: _SCheck) -> list[str]:
    return (
        [f"{who}: vertex stride {mesh.stride} against {mesh.format_stride}"]
        if mesh.stride != mesh.format_stride
        else []
    )


def _index_type(mesh: SMesh, who: str, check: _SCheck) -> list[str]:
    out = []
    if mesh.index_type != (5 if mesh.vertex_count > 0xFFFF else 3):
        out.append(f"{who}: index type {mesh.index_type} for {mesh.vertex_count} vertices")
    if len(mesh.indices) % 3 or max(mesh.indices, default=0) >= mesh.vertex_count:
        out.append(f"{who}: indices are not whole triangles inside the vertices")
    return out


def _submeshes(mesh: SMesh, who: str, check: _SCheck) -> list[str]:
    index_offset = 0
    for submesh in mesh.submeshes:
        if submesh.first_index != index_offset or submesh.material >= max(1, len(check.rdm.materials)):
            return [f"{who}: submeshes are not contiguous from 0 over the materials"]
        index_offset = submesh.first_index + submesh.index_count
    return [f"{who}: submeshes do not cover all indices"] if index_offset != len(mesh.indices) else []


def _box(mesh: SMesh, who: str, check: _SCheck) -> list[str]:
    return _box_deviation(mesh, who, check.strict)


def _skeleton_name(mesh: SMesh, who: str, check: _SCheck) -> list[str]:
    if mesh.skeleton is not None and mesh.skeleton != 0:
        return [f"{who}: names skeleton {mesh.skeleton}; 117 registers the first only"]
    return []


# one check per convention of a mesh (layout, stride, index type, submeshes, box, skeleton); each returns the
# deviations it finds
MESH_CHECKS = (_layout, _stride, _index_type, _submeshes, _box, _skeleton_name)


def deviations(
    data: bytes, kind: str, layouts: dict[str, Iterable] | None = None, source: bytes | None = None, strict: bool = True
) -> list[str]:
    """How a converted file deviates from 117's conventions (none when it follows them); ``layouts`` holds the
    element lists vanilla uses for each layout name, ``source`` the 2070 file for the statistics that must not change;
    ``strict`` wants the stored box to be the vertices' own (a mesh that was cut or padded may hold a box that
    only contains what is drawn)."""
    out: list[str] = []
    layouts = layouts or {k: {layout} for k, layout in LAYOUTS.items()}
    if data[:4] != b"RDM\x01" or len(data) < 20 or struct.unpack_from("<4I", data, 4) != (0x14, 0, 4, 0x1C):
        out.append("file header is not 117's (RDM, little-endian, 0x14, 0, 4, header1 at 0x1c)")
    try:
        rdm = read_rdm(data)
    except CRdmReadError as error:
        return [*out, f"unreadable: {error}"]
    if kind == "animation":
        return out + _animation_deviations(rdm)
    check = _SCheck(rdm, layouts, strict)
    for mesh in rdm.meshes:
        for run in MESH_CHECKS:
            out += run(mesh, f"mesh {mesh.name!r}", check)
    if len({mesh.name for mesh in rdm.meshes}) != len(rdm.meshes):
        out.append("two meshes of the file share a name")
    if kind == "skinned":
        out += _skin_deviations(rdm)
    elif rdm.skeletons or any(mesh.skeleton is not None for mesh in rdm.meshes):
        out.append("a static mesh file carries a skeleton")
    if source is not None and kind in ("static", "skinned", "cloth"):
        out += _geometry_changes(geometry_stats(source), geometry_stats(data))
    return out


def _geometry_changes(before: dict[str, float], after: dict[str, float]) -> list[str]:
    return [
        f"{k} agreement changed from {before[k]:.2f} to {agreement_after:.2f}: the relayout altered the geometry"
        for k, agreement_after in after.items()
        if k in before and abs(agreement_after - before[k]) > 0.05
    ]


def _box_deviation(mesh: SMesh, who: str, strict: bool) -> list[str]:
    if strict:
        box = mesh.computed_bounds()
        if box is None or any(
            abs(box_value - bbox_value) > 1e-3 for box_value, bbox_value in zip(box, mesh.bbox, strict=True)
        ):
            return [f"{who}: stored box {mesh.bbox} is not the mesh's own {box}"]
        return []
    drawn = set(mesh.indices)
    drawn_positions = [position for i, position in enumerate(mesh.positions()) if i in drawn]
    if mesh.bbox == (0.0,) * 6 or not drawn_positions:
        return [f"{who}: no stored box"]
    low, high = mesh.bbox[:3], mesh.bbox[3:]
    if any(not (low[k] - 1e-3 <= position[k] <= high[k] + 1e-3) for position in drawn_positions for k in range(3)):
        return [f"{who}: stored box {mesh.bbox} does not contain the vertices it draws"]
    return []


def _skeleton_deviations(skeleton) -> list[str]:
    out = []
    if skeleton.word != 0:
        out.append(f"skeleton +4 word is {skeleton.word}; 117 reads it as an offset")
    if not skeleton.joints or len(skeleton.joints) > MAX_JOINTS:
        out.append(f"{len(skeleton.joints)} joints (a bone index holds {MAX_JOINTS})")
    if any(j.parent is not None and not 0 <= j.parent < i for i, j in enumerate(skeleton.joints)):
        out.append("a joint's parent does not come before it: 117 drops the skeleton")
    names = [j.name for j in skeleton.joints]
    if len(set(names)) != len(names) or not all(names):
        out.append("joint names are not unique and non-empty: tracks find joints by name")
    if any(abs(sum(component * component for component in j.rotation) - 1) > 1e-2 for j in skeleton.joints):
        out.append("a joint rotation is not a unit quaternion")
    return out


def _weight_deviations(mesh: SMesh, joints: int) -> list[str]:
    """A skinned mesh's bone indices and weights against the skeleton's joints."""
    who = f"mesh {mesh.name!r}"
    index_element = mesh.element_at(7)
    weight_element = mesh.element_at(6)
    if index_element is None or weight_element is None:
        return [f"{who}: a skinned mesh needs bone indices and weights"]
    out = []
    if mesh.bones_per_vertex not in (3, 4):
        out.append(f"{who}: bones per vertex {mesh.bones_per_vertex}")
    bad = zero = fourth = 0
    for joint_indices, weights in zip(mesh.element(index_element), mesh.element(weight_element), strict=True):
        bad += sum(1 for k in range(4) if weights[k] and joint_indices[k] >= joints)
        zero += not any(weights)
        fourth += bool(weights[3])
    if bad:
        out.append(f"{who}: {bad} weighted bone indices past the {joints} joints")
    if zero:
        out.append(f"{who}: {zero} vertices without a weight (117 divides by the sum)")
    if fourth and mesh.bones_per_vertex < 4:
        out.append(f"{who}: a fourth weight with {mesh.bones_per_vertex} bones per vertex")
    return out


def _skin_deviations(rdm: SRdmFile) -> list[str]:
    if len(rdm.skeletons) != 1:
        return [f"{len(rdm.skeletons)} skeletons; 117 registers the first only"]
    skeleton = rdm.skeletons[0]
    out = _skeleton_deviations(skeleton)
    for mesh in rdm.meshes:
        out += _weight_deviations(mesh, len(skeleton.joints))
    return out


def _animation_deviations(rdm: SRdmFile) -> list[str]:
    out = []
    for animation in rdm.animations:
        who = f"animation {animation.name!r}"
        if animation.frame_rate <= 0 or animation.duration_ms <= 0:
            out.append(f"{who}: frame rate {animation.frame_rate}, duration {animation.duration_ms} ms")
        last = 0.0
        for track in animation.tracks:
            if not track.keys:
                out.append(f"{who}: track {track.joint!r} has no keys")
                continue
            times = [k.time for k in track.keys]
            if abs(times[0]) > 1e-6 or times != sorted(times):
                out.append(f"{who}: track {track.joint!r} keys do not run from time 0 upwards")
            if any(abs(sum(component * component for component in k.rotation) - 1) > 0.05 for k in track.keys):
                out.append(f"{who}: track {track.joint!r} has a key that is not a unit rotation")
            last = max(last, times[-1])
        if animation.tracks and abs(last * 1000 - animation.duration_ms) > 2:
            out.append(f"{who}: duration {animation.duration_ms} ms against a last key at {last * 1000:.0f} ms")
        if not animation.tracks:
            out.append(f"{who}: no tracks (the mesh stands still in it)")
    return out


def layouts_of(files: Iterable[bytes]) -> dict[str, set]:
    """Layout name -> the element lists a game's own files use for it (unreadable files are passed over)."""
    found: dict[str, set] = {}
    for data in files:
        try:
            rdm = read_rdm(data)
        except CRdmReadError:
            continue
        for mesh in rdm.meshes:
            found.setdefault(mesh.format, set()).add(
                tuple((element.usage, element.type, element.subtype, element.count) for element in mesh.elements)
            )
    return found
