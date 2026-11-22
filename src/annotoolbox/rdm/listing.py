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

from ..formats.gltf import SGltf
from ..formats.rdmread import SMesh, SRdmFile
from .gltfdoc import tags


def _num(value: float) -> str:
    return f"{value:.6g}"


def _vec(vector) -> str:
    return "(" + ", ".join(_num(component) for component in vector) + ")"


def _crc(data) -> str:
    raw = data if isinstance(data, (bytes, bytearray)) else repr(data).encode("ascii")
    return f"{zlib.crc32(raw):08x}"


def describe(rdm: SRdmFile, values: bool = False) -> list[str]:
    """The listing of a file (see the module docstring); ``values`` adds the checksums."""
    out = [f"byte order: {'big' if rdm.big_endian else 'little'}-endian"]
    out.append(f"export info: source {rdm.source!r}, exporter {rdm.exporter!r}")
    for k, mesh in enumerate(rdm.meshes):
        out += _mesh(k, mesh, values)
    for k, material in enumerate(rdm.materials):
        out.append(f"material {k}: name {material.name!r}, texture {material.texture!r}")
    for k, skeleton in enumerate(rdm.skeletons):
        out.append(
            f"skeleton {k}: {len(skeleton.joints)} joints, word {skeleton.word}, optional data {len(skeleton.extra)} bytes"
        )
        for i, joint in enumerate(skeleton.joints):
            parent = "-" if joint.parent is None else joint.parent
            out.append(
                f"  joint {i} {joint.name!r}: parent {parent}, position {_vec(joint.position)}, rotation {_vec(joint.rotation)}, "
                f"box {_vec(joint.box_min)} .. {_vec(joint.box_max)}"
            )
    for k, animation in enumerate(rdm.animations):
        out.append(
            f"animation {k} {animation.name!r}: {animation.frame_rate} fps, {animation.duration_ms} ms, {len(animation.tracks)} tracks"
        )
        for i, track in enumerate(animation.tracks):
            span = f", {_num(track.keys[0].time)} .. {_num(track.keys[-1].time)} s" if track.keys else ""
            line = f"  track {i} {track.joint!r}: {len(track.keys)} keys{span}"
            if values and track.keys:
                line += f", keys {_crc([(k.rotation, k.position, k.time) for k in track.keys])}"
            out.append(line)
    for k, stream in enumerate(rdm.extra_streams):
        out.append(
            f"extra stream {k}: {'_'.join(element.code for element in stream.elements)}, {len(stream.data)} bytes, words {stream.words}"
        )
    return out


def _mesh(k: int, mesh: SMesh, values: bool) -> list[str]:
    skeleton = "-" if mesh.skeleton is None else mesh.skeleton
    out = [
        f"mesh {k} {mesh.name!r}: layout {mesh.format}, {mesh.vertex_count} vertices of {mesh.stride} bytes, "
        f"{len(mesh.indices)} indices (type {mesh.index_type}), skeleton {skeleton}, bones per vertex {mesh.bones_per_vertex}",
        f"  box {_vec(mesh.bbox[:3])} .. {_vec(mesh.bbox[3:])}",
    ]
    for i, element in enumerate(mesh.elements):
        line = f"  element {i} {element.code}: usage {element.usage}, type {element.type}, subtype {element.subtype}, count {element.count}"
        if values:
            line += f", values {_crc(mesh.element(i))}"
        out.append(line)
    if values:
        out.append(f"  indices {_crc(mesh.indices)}")
    for i, submesh in enumerate(mesh.submeshes):
        out.append(f"  submesh {i}: indices {submesh.first_index} +{submesh.index_count}, material {submesh.material}")
    return out


def describe_gltf(gltf: SGltf) -> list[str]:
    """What a glTF document holds, item by item, and for each whether the export wrote it (``rdm``), marked it as
    its helper, or it came from elsewhere (``foreign``: what a modelling tool or a hand added)."""
    out = [f"generator {gltf.doc.get('asset', {}).get('generator', '')!r}"]
    for kind in ("scenes", "nodes", "meshes", "materials", "skins", "animations", "cameras", "images"):
        label = "mesh" if kind == "meshes" else kind[:-1]
        for k, item in enumerate(gltf.items(kind)):
            out.append(f"{label} {k} {item.get('name', '')!r}: {_origin(item)}{_detail(kind, item)}")
    return out


def _origin(item: dict) -> str:
    rdm_tags = tags(item)
    if rdm_tags.get("helper"):
        return f"helper ({rdm_tags['helper']})"
    return "rdm" if rdm_tags else "foreign"


def _detail(kind: str, item: dict) -> str:
    if kind == "nodes":
        parts = [f"{key} {item[key]}" for key in ("mesh", "skin", "camera") if key in item]
        if item.get("children"):
            parts.append(f"{len(item['children'])} children")
        return ", " + ", ".join(parts) if parts else ""
    if kind == "meshes":
        primitives = item.get("primitives", [])
        names = sorted({attribute for primitive in primitives for attribute in primitive.get("attributes", {})})
        return f", {len(primitives)} primitives, attributes {' '.join(names)}"
    if kind == "skins":
        return f", {len(item.get('joints', []))} joints"
    if kind == "animations":
        return f", {len(item.get('channels', []))} channels"
    return ""


def diff(first: list[str], second: list[str], names: tuple[str, str] = ("a", "b")) -> list[str]:
    """The lines that differ, as a unified diff (empty when the listings are the same)."""
    return list(difflib.unified_diff(first, second, names[0], names[1], lineterm="", n=1))
