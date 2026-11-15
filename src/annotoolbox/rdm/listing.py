"""Every element of an ``.rdm`` as lines of text, and the difference of two files in those lines.

``describe`` lists a file: its header, export strings, each mesh with its layout, element by element, its submeshes
and box, each material, skeleton and joint, each animation and track (with ``values``: a checksum of every vertex
element, the indices and each track's keys, so two files that list the same hold the same). ``describe_gltf``
lists a glTF document the same way and marks what is the export's helper and what came from elsewhere. ``diff``
compares two listings (``rdm inspect`` and ``rdm diff``).
"""

from __future__ import annotations

import difflib
import zlib

from ..formats.gltf import Gltf
from ..formats.rdmread import Mesh, RdmFile
from .gltfdoc import tags


def _num(v: float) -> str:
    return f"{v:.6g}"


def _vec(v) -> str:
    return "(" + ", ".join(_num(c) for c in v) + ")"


def _crc(data) -> str:
    raw = data if isinstance(data, (bytes, bytearray)) else repr(data).encode("ascii")
    return f"{zlib.crc32(raw):08x}"


def describe(rdm: RdmFile, values: bool = False) -> list[str]:
    """The listing of a file (see the module docstring); ``values`` adds the checksums."""
    out = [f"byte order: {'big' if rdm.big_endian else 'little'}-endian"]
    out.append(f"export info: source {rdm.source!r}, exporter {rdm.exporter!r}")
    for k, m in enumerate(rdm.meshes):
        out += _mesh(k, m, values)
    for k, mat in enumerate(rdm.materials):
        out.append(f"material {k}: name {mat.name!r}, texture {mat.texture!r}")
    for k, s in enumerate(rdm.skeletons):
        out.append(f"skeleton {k}: {len(s.joints)} joints, word {s.word}, optional data {len(s.extra)} bytes")
        for i, j in enumerate(s.joints):
            parent = "-" if j.parent is None else j.parent
            out.append(
                f"  joint {i} {j.name!r}: parent {parent}, position {_vec(j.position)}, rotation {_vec(j.rotation)}, "
                f"box {_vec(j.box_min)} .. {_vec(j.box_max)}"
            )
    for k, a in enumerate(rdm.animations):
        out.append(f"animation {k} {a.name!r}: {a.frame_rate} fps, {a.duration_ms} ms, {len(a.tracks)} tracks")
        for i, t in enumerate(a.tracks):
            span = f", {_num(t.keys[0].time)} .. {_num(t.keys[-1].time)} s" if t.keys else ""
            line = f"  track {i} {t.joint!r}: {len(t.keys)} keys{span}"
            if values and t.keys:
                line += f", keys {_crc([(k.rotation, k.position, k.time) for k in t.keys])}"
            out.append(line)
    for k, x in enumerate(rdm.extra_streams):
        out.append(f"extra stream {k}: {'_'.join(e.code for e in x.elements)}, {len(x.data)} bytes, words {x.words}")
    return out


def _mesh(k: int, m: Mesh, values: bool) -> list[str]:
    skeleton = "-" if m.skeleton is None else m.skeleton
    out = [
        f"mesh {k} {m.name!r}: layout {m.format}, {m.vertex_count} vertices of {m.stride} bytes, "
        f"{len(m.indices)} indices (type {m.index_type}), skeleton {skeleton}, bones per vertex {m.bones_per_vertex}",
        f"  box {_vec(m.bbox[:3])} .. {_vec(m.bbox[3:])}",
    ]
    for i, e in enumerate(m.elements):
        line = f"  element {i} {e.code}: usage {e.usage}, type {e.type}, subtype {e.subtype}, count {e.count}"
        if values:
            line += f", values {_crc(m.element(i))}"
        out.append(line)
    if values:
        out.append(f"  indices {_crc(m.indices)}")
    for i, s in enumerate(m.submeshes):
        out.append(f"  submesh {i}: indices {s.first_index} +{s.index_count}, material {s.material}")
    return out


def describe_gltf(g: Gltf) -> list[str]:
    """What a glTF document holds, item by item, and for each whether the export wrote it (``rdm``), marked it as
    its helper, or it came from elsewhere (``foreign``: what a modelling tool or a hand added)."""
    out = [f"generator {g.doc.get('asset', {}).get('generator', '')!r}"]
    for kind in ("scenes", "nodes", "meshes", "materials", "skins", "animations", "cameras", "images"):
        label = "mesh" if kind == "meshes" else kind[:-1]
        for k, item in enumerate(g.items(kind)):
            out.append(f"{label} {k} {item.get('name', '')!r}: {_origin(item)}{_detail(kind, item)}")
    return out


def _origin(item: dict) -> str:
    t = tags(item)
    if t.get("helper"):
        return f"helper ({t['helper']})"
    return "rdm" if t else "foreign"


def _detail(kind: str, item: dict) -> str:
    if kind == "nodes":
        parts = [f"{key} {item[key]}" for key in ("mesh", "skin", "camera") if key in item]
        if item.get("children"):
            parts.append(f"{len(item['children'])} children")
        return ", " + ", ".join(parts) if parts else ""
    if kind == "meshes":
        prims = item.get("primitives", [])
        names = sorted({a for p in prims for a in p.get("attributes", {})})
        return f", {len(prims)} primitives, attributes {' '.join(names)}"
    if kind == "skins":
        return f", {len(item.get('joints', []))} joints"
    if kind == "animations":
        return f", {len(item.get('channels', []))} channels"
    return ""


def diff(a: list[str], b: list[str], names: tuple[str, str] = ("a", "b")) -> list[str]:
    """The lines that differ, as a unified diff (empty when the listings are the same)."""
    return list(difflib.unified_diff(a, b, names[0], names[1], lineterm="", n=1))
