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
from ..formats.rdmread import Mesh, RdmFile, RdmReadError, read_rdm

LAYOUTS = rdmlayout.VANILLA_117
MAX_JOINTS = 256  # a bone index is one byte

SAMPLE = 400  # triangles looked at per mesh for the geometry statistics
UNIT = 0.9  # a packed normal this long counts as a direction


def classify(rdm: RdmFile) -> str:
    if rdm.extra_streams:
        return "extra"
    if rdm.animations and not rdm.meshes:
        return "animation"
    if not rdm.meshes:
        return "empty"
    if any(m.elements and m.elements[0].type == 7 for m in rdm.meshes):
        return "cloth"
    return "skinned" if rdm.skeletons else "static"


def _decode(b: bytes) -> tuple[float, float, float]:
    return b[0] / 255 * 2 - 1, b[1] / 255 * 2 - 1, b[2] / 255 * 2 - 1


def _sample(n: int) -> range:
    return range(0, n, max(1, n // SAMPLE))


@dataclass
class _Streams:
    """The vertex streams the geometry check reads (None where the layout has none)."""

    pos: list
    nrm: object = None
    tans: object = None
    bits: object = None
    uvs: object = None


def _streams(m: Mesh) -> _Streams:
    s = _Streams(m.positions())
    for i, e in enumerate(m.elements):
        if e.usage == 1 and e.type == 5 and s.nrm is None:
            s.nrm = m.element(i)
        elif e.usage == 2 and e.type == 5:
            s.tans = m.element(i)
        elif e.usage == 3 and e.type == 5:
            s.bits = m.element(i)
        elif e.usage == 4 and s.uvs is None and e.type in (6, 7):
            s.uvs = m.element(i)
    return s


def _winding(s: _Streams, e1: list, e2: list, tri: tuple[int, int, int]) -> bool | None:
    """Whether the triangle is counter-clockwise with its vertex normals (None for a degenerate one)."""
    n = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
    vn = [sum(_decode(bytes(s.nrm[v]))[k] for v in tri) for k in range(3)]
    d = sum(n[k] * vn[k] for k in range(3))
    return d > 0 if abs(d) > 1e-12 else None


def _frame(s: _Streams, e1: list, e2: list, tri: tuple[int, int, int]) -> dict[str, bool]:
    """Per tangent and bitangent that is a direction: whether it is -dP/du, -dP/dv at the first vertex."""
    a, b, c = tri
    du1, dv1 = s.uvs[b][0] - s.uvs[a][0], s.uvs[b][1] - s.uvs[a][1]
    du2, dv2 = s.uvs[c][0] - s.uvs[a][0], s.uvs[c][1] - s.uvs[a][1]
    det = du1 * dv2 - du2 * dv1
    if abs(det) < 1e-12:
        return {}
    dpdu = [(e1[k] * dv2 - e2[k] * dv1) / det for k in range(3)]
    dpdv = [(e2[k] * du1 - e1[k] * du2) / det for k in range(3)]
    out = {}
    for name, stream, slope in (("tangent", s.tans, dpdu), ("bitangent", s.bits, dpdv)):
        v = _decode(bytes(stream[a]))
        if math.dist(v, (0, 0, 0)) > UNIT:
            out[name] = sum(slope[k] * v[k] for k in range(3)) < 0
    return out


def _geometry(m: Mesh) -> dict[str, float]:
    """Winding against the vertex normals and the tangent frame against the UV derivatives, over a sample of
    triangles: the share that agrees with 117's convention (counter-clockwise with the normal, T = -dP/du,
    B = -dP/dv)."""
    s = _streams(m)
    if not s.pos or s.nrm is None:
        return {}
    framed = s.tans is not None and s.bits is not None and s.uvs is not None
    agree: dict[str, list[int]] = {"ccw": [0, 0], "tangent": [0, 0], "bitangent": [0, 0]}  # [agreeing, counted]
    for t in _sample(len(m.indices) // 3):
        tri = tuple(m.indices[3 * t : 3 * t + 3])
        e1 = [s.pos[tri[1]][k] - s.pos[tri[0]][k] for k in range(3)]
        e2 = [s.pos[tri[2]][k] - s.pos[tri[0]][k] for k in range(3)]
        found = {"ccw": ccw} if (ccw := _winding(s, e1, e2, tri)) is not None else {}
        found |= _frame(s, e1, e2, tri) if framed else {}
        for name, ok in found.items():
            agree[name][0] += ok
            agree[name][1] += 1
    return {name: ok / n for name, (ok, n) in agree.items() if n}


def geometry_stats(data: bytes) -> dict[str, float]:
    """The mean of :func:`_geometry` over a file's meshes (what a relayout must not change)."""
    rows = [g for m in read_rdm(data).meshes if (g := _geometry(m))]
    return {
        k: sum(g[k] for g in rows if k in g) / sum(1 for g in rows if k in g)
        for k in ("ccw", "tangent", "bitangent")
        if any(k in g for g in rows)
    }


@dataclass(frozen=True)
class _Check:
    """What a convention check looks at: the file, the layouts vanilla uses, the box rule."""

    rdm: RdmFile
    layouts: dict[str, Iterable]
    strict: bool


def _layout(m: Mesh, who: str, c: _Check) -> list[str]:
    elements = tuple((e.usage, e.type, e.subtype, e.count) for e in m.elements)
    if elements in c.layouts.get(m.format, ()):
        return []
    return [f"{who}: layout {m.format} {elements} is not a layout of 117's own files"]


def _stride(m: Mesh, who: str, c: _Check) -> list[str]:
    return [f"{who}: vertex stride {m.stride} against {m.format_stride}"] if m.stride != m.format_stride else []


def _index_type(m: Mesh, who: str, c: _Check) -> list[str]:
    out = []
    if m.index_type != (5 if m.vertex_count > 0xFFFF else 3):
        out.append(f"{who}: index type {m.index_type} for {m.vertex_count} vertices")
    if len(m.indices) % 3 or max(m.indices, default=0) >= m.vertex_count:
        out.append(f"{who}: indices are not whole triangles inside the vertices")
    return out


def _submeshes(m: Mesh, who: str, c: _Check) -> list[str]:
    pos = 0
    for s in m.submeshes:
        if s.first_index != pos or s.material >= max(1, len(c.rdm.materials)):
            return [f"{who}: submeshes are not contiguous from 0 over the materials"]
        pos = s.first_index + s.index_count
    return [f"{who}: submeshes do not cover all indices"] if pos != len(m.indices) else []


def _box(m: Mesh, who: str, c: _Check) -> list[str]:
    return _box_deviation(m, who, c.strict)


def _skeleton_name(m: Mesh, who: str, c: _Check) -> list[str]:
    if m.skeleton is not None and m.skeleton != 0:
        return [f"{who}: names skeleton {m.skeleton}; 117 registers the first only"]
    return []


# one (name, check) per convention of a mesh; each returns the deviations it finds
MESH_CHECKS = (
    ("layout", _layout),
    ("stride", _stride),
    ("index type", _index_type),
    ("submeshes", _submeshes),
    ("box", _box),
    ("skeleton", _skeleton_name),
)


def deviations(
    data: bytes, kind: str, layouts: dict[str, Iterable] | None = None, source: bytes | None = None, strict: bool = True
) -> list[str]:
    """How a converted file deviates from 117's conventions (none when it follows them); ``layouts`` holds the
    element lists vanilla uses for each layout name, ``source`` the 2070 file for the statistics that must not change;
    ``strict`` wants the stored box to be the vertices' own (a mesh that was cut or padded may hold a box that
    only contains what is drawn)."""
    out: list[str] = []
    layouts = layouts or {k: {v} for k, v in LAYOUTS.items()}
    if data[:3] != b"RDM" or data[3] != 1 or struct.unpack_from("<4I", data, 4) != (0x14, 0, 4, 0x1C):
        out.append("file header is not 117's (RDM, little-endian, 0x14, 0, 4, header1 at 0x1c)")
    try:
        rdm = read_rdm(data)
    except RdmReadError as exc:
        return [*out, f"unreadable: {exc}"]
    if kind == "animation":
        return out + _animation_deviations(rdm)
    check = _Check(rdm, layouts, strict)
    for m in rdm.meshes:
        for _name, run in MESH_CHECKS:
            out += run(m, f"mesh {m.name!r}", check)
    if len({m.name for m in rdm.meshes}) != len(rdm.meshes):
        out.append("two meshes of the file share a name")
    if kind == "skinned":
        out += _skin_deviations(rdm)
    elif rdm.skeletons or any(m.skeleton is not None for m in rdm.meshes):
        out.append("a static mesh file carries a skeleton")
    if source is not None and kind in ("static", "skinned", "cloth"):
        out += _geometry_changes(geometry_stats(source), geometry_stats(data))
    return out


def _geometry_changes(before: dict[str, float], after: dict[str, float]) -> list[str]:
    return [
        f"{k} agreement changed from {before[k]:.2f} to {v:.2f}: the relayout altered the geometry"
        for k, v in after.items()
        if k in before and abs(v - before[k]) > 0.05
    ]


def _box_deviation(m: Mesh, who: str, strict: bool) -> list[str]:
    if strict:
        box = m.computed_bounds()
        if box is None or any(abs(a - b) > 1e-3 for a, b in zip(box, m.bbox, strict=True)):
            return [f"{who}: stored box {m.bbox} is not the mesh's own {box}"]
        return []
    drawn = set(m.indices)
    pos = [p for i, p in enumerate(m.positions()) if i in drawn]
    if m.bbox == (0.0,) * 6 or not pos:
        return [f"{who}: no stored box"]
    lo, hi = m.bbox[:3], m.bbox[3:]
    if any(not (lo[k] - 1e-3 <= p[k] <= hi[k] + 1e-3) for p in pos for k in range(3)):
        return [f"{who}: stored box {m.bbox} does not contain the vertices it draws"]
    return []


def _skeleton_deviations(s) -> list[str]:
    out = []
    if s.word != 0:
        out.append(f"skeleton +4 word is {s.word}; 117 reads it as an offset")
    if not s.joints or len(s.joints) > MAX_JOINTS:
        out.append(f"{len(s.joints)} joints (a bone index holds {MAX_JOINTS})")
    if any(j.parent is not None and not 0 <= j.parent < i for i, j in enumerate(s.joints)):
        out.append("a joint's parent does not come before it: 117 drops the skeleton")
    names = [j.name for j in s.joints]
    if len(set(names)) != len(names) or not all(names):
        out.append("joint names are not unique and non-empty: tracks find joints by name")
    if any(abs(sum(c * c for c in j.rotation) - 1) > 1e-2 for j in s.joints):
        out.append("a joint rotation is not a unit quaternion")
    return out


def _weight_deviations(m: Mesh, joints: int) -> list[str]:
    """A skinned mesh's bone indices and weights against the skeleton's joints."""
    who = f"mesh {m.name!r}"
    idx = next((i for i, e in enumerate(m.elements) if e.usage == 7), None)
    wgt = next((i for i, e in enumerate(m.elements) if e.usage == 6), None)
    if idx is None or wgt is None:
        return [f"{who}: a skinned mesh needs bone indices and weights"]
    out = []
    if m.bones_per_vertex not in (3, 4):
        out.append(f"{who}: bones per vertex {m.bones_per_vertex}")
    bad = zero = fourth = 0
    for bi, w in zip(m.element(idx), m.element(wgt), strict=True):
        bad += sum(1 for k in range(4) if w[k] and bi[k] >= joints)
        zero += not any(w)
        fourth += bool(w[3])
    if bad:
        out.append(f"{who}: {bad} weighted bone indices past the {joints} joints")
    if zero:
        out.append(f"{who}: {zero} vertices without a weight (117 divides by the sum)")
    if fourth and m.bones_per_vertex < 4:
        out.append(f"{who}: a fourth weight with {m.bones_per_vertex} bones per vertex")
    return out


def _skin_deviations(rdm: RdmFile) -> list[str]:
    if len(rdm.skeletons) != 1:
        return [f"{len(rdm.skeletons)} skeletons; 117 registers the first only"]
    s = rdm.skeletons[0]
    out = _skeleton_deviations(s)
    for m in rdm.meshes:
        out += _weight_deviations(m, len(s.joints))
    return out


def _animation_deviations(rdm: RdmFile) -> list[str]:
    out = []
    for a in rdm.animations:
        who = f"animation {a.name!r}"
        if a.frame_rate <= 0 or a.duration_ms <= 0:
            out.append(f"{who}: frame rate {a.frame_rate}, duration {a.duration_ms} ms")
        last = 0.0
        for t in a.tracks:
            if not t.keys:
                out.append(f"{who}: track {t.joint!r} has no keys")
                continue
            times = [k.time for k in t.keys]
            if abs(times[0]) > 1e-6 or times != sorted(times):
                out.append(f"{who}: track {t.joint!r} keys do not run from time 0 upwards")
            if any(abs(sum(c * c for c in k.rotation) - 1) > 0.05 for k in t.keys):
                out.append(f"{who}: track {t.joint!r} has a key that is not a unit rotation")
            last = max(last, times[-1])
        if a.tracks and abs(last * 1000 - a.duration_ms) > 2:
            out.append(f"{who}: duration {a.duration_ms} ms against a last key at {last * 1000:.0f} ms")
        if not a.tracks:
            out.append(f"{who}: no tracks (the mesh stands still in it)")
    return out


def layouts_of(files: Iterable[bytes]) -> dict[str, set]:
    """Layout name -> the element lists a game's own files use for it (unreadable files are passed over)."""
    found: dict[str, set] = {}
    for data in files:
        try:
            rdm = read_rdm(data)
        except RdmReadError:
            continue
        for m in rdm.meshes:
            found.setdefault(m.format, set()).add(tuple((e.usage, e.type, e.subtype, e.count) for e in m.elements))
    return found
