"""An ``.rdm`` as a glTF 2.0 document: every mesh, submesh, material, skeleton and animation of the source, nothing
added that is not marked as glTF's own.

``export`` is the entry: one or more RDM files (a LOD chain, say) and their animation files become one ``Builder``
(``formats.gltf``). Every vertex element keeps its values exactly; what glTF has no slot for travels in the
``extras`` under ``"rdm"`` and in attributes named ``_RDM_<n>``, so ``gltfrdm`` writes the same file back. The
mapping and the design rule: docs/rdm-gltf.md.
"""

from __future__ import annotations

import base64
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import gltf, rdmlayout
from ..formats.gltf import FLOAT, UBYTE, UINT, USHORT, Builder
from ..formats.rdmread import Animation, Mesh, RdmFile, Skeleton
from ..models.meshxform import qconj
from . import rigmath
from .gltfdoc import EXTRAS

VERSION = 1


class ExportError(ValueError):
    """A source the glTF document cannot hold exactly; the message says what and why."""


@dataclass(frozen=True)
class Options:
    """``axes``: "gltf" mirrors Anno's left-handed space on x into glTF's right-handed one (exactly reversible),
    "anno" keeps the stored values. ``strict``: unit normals and tangents and weights that sum to one, as the glTF
    validator wants, instead of the stored values (a later import then rounds back to within one step)."""

    axes: str = "gltf"
    strict: bool = False

    @property
    def mirror(self) -> bool:
        return self.axes == "gltf"


@dataclass
class Source:
    """One RDM file of the export: its name (the file name, kept for the import) and its contents."""

    name: str
    rdm: RdmFile


@dataclass
class Texture:
    """An image for a material's base colour: its bytes and MIME type, and the material it is for (None: every one)."""

    data: bytes
    mime: str
    material: str | None = None
    fallback: tuple[bytes, str] | None = None  # a PNG beside a DDS that viewers can show


@dataclass
class _Export:
    b: Builder
    opts: Options
    joints: dict[str, int] = field(default_factory=dict)  # joint name -> its node, for the animation channels
    notes: list[str] = field(default_factory=list)


def _tag(item: dict, **values) -> dict:
    item.setdefault("extras", {})[EXTRAS] = values
    return item


def export(
    sources: list[Source], animations: list[Source] = (), textures: list[Texture] = (), opts: Options | None = None
) -> tuple[Builder, list[str]]:
    """The glTF document of ``sources`` (mesh files) and the ``animations`` (animation files) that move them, and
    notes on what a viewer may show differently. Every item is tagged with the file it came from; with several mesh
    files each one's nodes hang under a node marked ``helper: file``, the one structure the export adds (with the
    scene, marked ``helper: scene``), and the import removes both again."""
    if not sources:
        raise ExportError("nothing to export")
    opts = opts or Options()
    x = _Export(Builder(), opts)
    x.b.doc["asset"]["generator"] = "rdm-gltf"
    everything = [*sources, *animations]
    names = [s.name for s in everything]
    if len(set(names)) != len(names):
        raise ExportError("two inputs have the same file name; the import tells the files apart by it")
    for source in everything:
        if source.rdm.extra_streams:
            raise ExportError(f"{source.name}: a per-frame vertex stream has no place in glTF")
    skins = {s.name: [_skin(x, sk, k, s.name) for k, sk in enumerate(s.rdm.skeletons)] for s in everything}
    roots = []
    for source in sources:
        nodes = _meshes(x, source, skins[source.name])
        if len(sources) == 1:
            roots += nodes
        else:
            helper = _tag({"name": source.name, "children": nodes}, helper="file", file=source.name)
            roots.append(x.b.add("nodes", helper))
    roots += [n for s in animations for skin in skins[s.name] for n in skin["roots"]]
    files = [_file_info(s, "mesh") for s in sources] + [_file_info(s, "animation") for s in animations]
    for info, source in zip(files, everything, strict=True):
        unkeyed = [_animation(x, a, k, source) for k, a in enumerate(source.rdm.animations)]
        if unkeyed := [u for u in unkeyed if u is not None]:
            info["unkeyed"] = unkeyed
    for texture in textures:
        _texture(x, texture)
    x.b.doc["scene"] = 0
    scene = {"version": VERSION, "axes": opts.axes, "strict": opts.strict, "files": files}
    x.b.doc["scenes"] = [_tag({"nodes": roots}, helper="scene", **scene)]
    return x.b, x.notes


def _file_info(source: Source, kind: str) -> dict:
    rdm = source.rdm
    return {
        "name": source.name,
        "kind": kind,
        "source": rdm.source,
        "exporter": rdm.exporter,
        "big_endian": rdm.big_endian,
    }


def _meshes(x: _Export, source: Source, skins: list[dict]) -> list[int]:
    """The nodes of a mesh file: its meshes, then its skeletons' root joints."""
    rdm = source.rdm
    materials = [
        x.b.add("materials", _tag({"name": m.name}, file=source.name, index=k, texture=m.texture))
        for k, m in enumerate(rdm.materials)
    ]
    nodes = []
    for k, m in enumerate(rdm.meshes):
        mesh = _mesh(x, m, materials, f"{source.name}: mesh {m.name!r}")
        node = {"name": m.name, "mesh": mesh}
        if m.skeleton is not None:
            if m.skeleton >= len(skins):
                raise ExportError(f"{source.name}: mesh {m.name!r} names skeleton {m.skeleton} of {len(skins)}")
            node["skin"] = skins[m.skeleton]["skin"]
        nodes.append(x.b.add("nodes", _tag(node, file=source.name, mesh=k)))
    return nodes + [n for s in skins for n in s["roots"]]


# Meshes


def _columns(m: Mesh) -> list[list[tuple]]:
    return [m.element(i) for i in range(len(m.elements))]


def _crc(values) -> int:
    return zlib.crc32(repr(values).encode("ascii"))


def _mesh(x: _Export, m: Mesh, materials: list[int], who: str) -> int:
    if m.stride != m.format_stride:
        raise ExportError(f"{who}: vertex block of {m.stride} bytes against a layout of {m.format_stride}")
    if not m.vertex_count or not m.indices:
        raise ExportError(f"{who}: no geometry")
    cols = _columns(m)
    attrs, info = _attributes(x, m, cols)
    info |= {"name": m.name, "bones_per_vertex": m.bones_per_vertex, "index_type": m.index_type}
    info |= {"skeleton": m.skeleton, "box": list(m.bbox), "positions_crc": _crc(cols[0])}
    prims = []
    for first, count, material in _ranges(m, who):
        tri = m.indices[first : first + count]
        if x.opts.mirror:
            tri = _reverse(tri)
        component = UINT if max(tri) >= 0xFFFF else USHORT
        prim = {"attributes": attrs, "indices": x.b.accessor(tri, "SCALAR", component, target=gltf.ELEMENT_BUFFER)}
        prim["mode"] = 4
        if material is not None:
            if material >= len(materials):
                raise ExportError(f"{who}: a submesh names material {material} of {len(materials)}")
            prim["material"] = materials[material]
        prims.append(prim)
    info["submeshes"] = bool(m.submeshes)
    return x.b.add("meshes", _tag({"name": m.name, "primitives": prims}, **info))


def _ranges(m: Mesh, who: str) -> list[tuple[int, int, int | None]]:
    if not m.submeshes:
        return [(0, len(m.indices), None)]
    at = 0
    for s in m.submeshes:
        if s.first_index != at or s.index_count % 3:
            raise ExportError(f"{who}: submeshes do not run in order over whole triangles; glTF primitives cannot")
        at += s.index_count
    if at != len(m.indices):
        raise ExportError(f"{who}: submeshes do not cover every index; glTF primitives cannot")
    return [(s.first_index, s.index_count, s.material) for s in m.submeshes]


def _reverse(indices: list[int]) -> list[int]:
    out = []
    for k in range(0, len(indices) - 2, 3):
        out += [indices[k], indices[k + 2], indices[k + 1]]
    return out


def _attributes(x: _Export, m: Mesh, cols: list[list[tuple]]) -> tuple[dict, dict]:
    """The glTF attributes of a mesh and the extras that complete them: per element its standard attribute, a
    constant 4th component, or the whole element as ``_RDM_<n>``."""
    attrs: dict[str, int] = {}
    fourth: dict[str, float] = {}
    seen: Counter[int] = Counter()
    els = m.elements
    by_usage = {u: [i for i, e in enumerate(els) if e.usage == u] for u in range(8)}
    for i, e in enumerate(els):
        nth = seen[e.usage]
        seen[e.usage] += 1
        name, values, component, normalized = _standard(x, m, cols, i, nth, by_usage)
        if name is None:
            attrs[f"_RDM_{i}"] = _raw(x, e, cols[i])
            continue
        attrs[name] = x.b.accessor(values, _kind(values), component, normalized=normalized, target=gltf.ARRAY_BUFFER)
        rest = _fourth(e, cols[i])
        if rest == "varies":
            attrs[f"_RDM_{i}"] = _raw(x, e, cols[i])
        elif rest is not None:
            fourth[str(i)] = rest
    info: dict = {"elements": [[e.usage, e.type, e.subtype, e.count] for e in els], "fourth": fourth}
    implied = []  # what glTF needs for a skinned mesh and the source does not hold: marked, dropped on import
    if m.skeleton is not None and "JOINTS_0" not in attrs:
        attrs["JOINTS_0"] = x.b.accessor([(0, 0, 0, 0)] * m.vertex_count, "VEC4", UBYTE)
        implied.append("JOINTS_0")  # a rigid part: the whole mesh on its skeleton's first joint
    if "JOINTS_0" in attrs and "WEIGHTS_0" not in attrs:
        attrs["WEIGHTS_0"] = x.b.accessor([(255, 0, 0, 0)] * m.vertex_count, "VEC4", UBYTE, normalized=True)
        implied.append("WEIGHTS_0")  # one bone a vertex
    if implied:
        info["implied"] = implied
    return attrs, info


def _kind(values: list) -> str:
    return {2: "VEC2", 3: "VEC3", 4: "VEC4"}[len(values[0])]


def _fourth(e, col: list[tuple]) -> float | str | None:
    """The 4th component a standard attribute does not carry: a position's w, a packed direction's last byte."""
    if not ((e.usage == 0 and e.count == 4) or (e.usage in (1, 2, 3) and e.type == 5)):
        return None
    values = {v[3] for v in col}
    return values.pop() if len(values) == 1 else "varies"


def _standard(x: _Export, m: Mesh, cols, i: int, nth: int, by_usage) -> tuple:
    """``(attribute, values, component type, normalized)`` of element ``i``, or ``(None, ...)`` for one glTF has
    no attribute for."""
    e, col, mirror = m.elements[i], cols[i], x.opts.mirror
    flip = (lambda v: (-v[0], v[1], v[2])) if mirror else (lambda v: (v[0], v[1], v[2]))
    floats = e.type in (6, 7)
    if e.usage == 0 and nth == 0 and floats and e.count >= 3:
        return "POSITION", [flip(v) for v in col], FLOAT, False
    if e.usage in (1, 2, 3) and nth == 0 and (e.type == 5 or (floats and e.count == 3)):
        vectors = [flip(_vector(e, v)) for v in col]
        if e.usage == 1:
            return "NORMAL", _unit(vectors) if x.opts.strict else vectors, FLOAT, False
        vectors = [(-a, -b, -c) for a, b, c in vectors]  # stored as -dP/du, -dP/dv; glTF wants +dP/du
        if e.usage == 3:
            return "_BITANGENT", vectors, FLOAT, False
        return (
            "TANGENT",
            _tangents(m, cols, by_usage, _unit(vectors) if x.opts.strict else vectors, mirror),
            FLOAT,
            False,
        )
    if e.usage == 4 and floats and e.count == 2:
        return f"TEXCOORD_{nth}", col, FLOAT, False
    if e.usage == 5 and e.type == 5:
        return f"COLOR_{nth}", col, UBYTE, True
    if e.usage == 7 and e.type == 5 and nth == 0:
        return "JOINTS_0", col, UBYTE, False
    if e.usage == 6 and e.type == 5 and nth == 0:
        return "WEIGHTS_0", _weights(col) if x.opts.strict else col, UBYTE, True
    return None, None, None, None


def _vector(e, v: tuple) -> tuple[float, float, float]:
    return rdmlayout.direction(v) if e.type == 5 else (v[0], v[1], v[2])


def _unit(vectors: list) -> list:
    out = []
    for v in vectors:
        n = sum(c * c for c in v) ** 0.5
        out.append(tuple(c / n for c in v) if n > 1e-12 else (0.0, 1.0, 0.0))
    return out


def _tangents(m: Mesh, cols, by_usage, xyz: list, mirror: bool) -> list:
    """glTF's VEC4 tangents: ``xyz``, and in w the sign that turns cross(normal, tangent) towards the stored
    bitangent (computed in the stored space; a mirror turns the frame's handedness)."""

    def vectors(usage: int) -> list | None:
        if not by_usage[usage]:
            return None
        i = by_usage[usage][0]
        return [_vector(m.elements[i], v) for v in cols[i]]

    normals, tangents, bitangents = vectors(1), vectors(2), vectors(3)
    out = []
    for k, t in enumerate(xyz):
        w = 1.0
        if normals and bitangents:
            n, g, b = normals[k], tangents[k], bitangents[k]  # stored: -dP/du, -dP/dv; their signs cancel
            c = (n[1] * g[2] - n[2] * g[1], n[2] * g[0] - n[0] * g[2], n[0] * g[1] - n[1] * g[0])
            w = 1.0 if c[0] * b[0] + c[1] * b[1] + c[2] * b[2] >= 0 else -1.0
        out.append((*t, -w if mirror else w))
    return out


def _weights(col: list[tuple]) -> list[tuple]:
    """Weights scaled to sum to 255 (largest remainder), as glTF wants; the game divides by the sum anyway."""
    out = []
    for w in col:
        total = sum(w)
        if not total:
            out.append((255, 0, 0, 0))
            continue
        exact = [c * 255 / total for c in w]
        base = [int(c) for c in exact]
        for k in sorted(range(4), key=lambda k: base[k] - exact[k])[: 255 - sum(base)]:
            base[k] += 1
        out.append(tuple(base))
    return out


def _raw(x: _Export, e, col: list[tuple]) -> int:
    """The whole element as stored: bytes for a packed vector, floats for half and float, the integers otherwise."""
    if e.type == 5:
        return x.b.accessor(col, "VEC4", UBYTE)
    kind = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}[e.count]
    values = col if e.count > 1 else [v[0] for v in col]
    component = FLOAT if e.type in (6, 7) else {0: UBYTE, 2: USHORT}.get(e.type, UINT)
    if component == FLOAT:
        return x.b.accessor(values, kind, FLOAT)
    return x.b.accessor(values, kind, component)


# Skeletons


def inverse_bind(position, rotation) -> rigmath.Matrix:
    """A joint's stored inverse bind pose as a matrix (its quaternion is the conjugate of glTF's)."""
    return rigmath.from_rotation_translation(qconj(rotation), position)


def _skin(x: _Export, s: Skeleton, index: int, file: str) -> dict:
    """A skin of joint nodes in the skeleton's order, at the rest pose its inverse bind poses imply; each joint
    keeps its stored values in the extras."""
    ibms = [inverse_bind(j.position, j.rotation) for j in s.joints]
    if x.opts.mirror:
        ibms = [rigmath.mirrored(m) for m in ibms]
    binds = [rigmath.inverse(m) for m in ibms]
    nodes, roots = [], []
    for k, j in enumerate(s.joints):
        local = binds[k] if j.parent is None else rigmath.multiply(ibms[j.parent], binds[k])
        q, t, scale = rigmath.decompose(local)
        node = {"name": j.name, "rotation": list(q), "translation": list(t)}
        if any(abs(c - 1) > 1e-5 for c in scale):
            node["scale"] = list(scale)
        tags = {"file": file, "skeleton": index, "joint": k, "position": list(j.position)}
        tags |= {"rotation": list(j.rotation), "box_min": list(j.box_min), "box_max": list(j.box_max)}
        nodes.append(x.b.add("nodes", _tag(node, **tags)))
        x.joints.setdefault(j.name, nodes[-1])
    for k, j in enumerate(s.joints):
        if j.parent is None:
            roots.append(nodes[k])
        else:
            x.b.doc["nodes"][nodes[j.parent]].setdefault("children", []).append(nodes[k])
    ibm = x.b.accessor([rigmath.to_columns(m) for m in ibms], "MAT4", FLOAT)
    tags = {"file": file, "skeleton": index, "word": s.word}
    if s.extra:
        tags["extra"] = base64.b64encode(s.extra).decode("ascii")
    skin = x.b.add("skins", _tag({"name": f"skeleton {index}", "joints": nodes, "inverseBindMatrices": ibm}, **tags))
    return {"skin": skin, "roots": roots}


# Animations


def _animation(x: _Export, a: Animation, entry: int, source: Source) -> dict | None:
    """Entry ``entry`` of a file's animations as a glTF animation; None, or the extras of an entry without a key
    (glTF wants a channel in every animation, so that one lives in the scene's extras)."""
    many = len(source.rdm.animations) > 1
    label = Path(source.name).stem + (f":{entry}" if many else "")
    channels, samplers, empty, bound = [], [], [], set()
    for k, track in enumerate(a.tracks):
        if not track.keys:
            empty.append(k)
            continue
        times = x.b.accessor([key.time for key in track.keys], "SCALAR", FLOAT)
        rot = [qconj(key.rotation) for key in track.keys]
        pos = [key.position for key in track.keys]
        if x.opts.mirror:
            rot, pos = [rigmath.mirrored_rotation(q) for q in rot], [rigmath.mirrored_point(p) for p in pos]
        node = x.joints.get(track.joint)
        if node in bound:  # glTF allows one channel per joint and path: a second track on the joint stays unbound
            x.notes.append(f"{label}: a second track on joint {track.joint!r} (kept, unbound)")
            node = None
        bound.add(node)
        for path, values, kind in (("rotation", rot, "VEC4"), ("translation", pos, "VEC3")):
            samplers.append({"input": times, "output": x.b.accessor(values, kind, FLOAT), "interpolation": "LINEAR"})
            target = {"path": path} if node is None else {"node": node, "path": path}
            channels.append(_tag({"sampler": len(samplers) - 1, "target": target}, joint=track.joint, track=k))
        if node is None and track.joint not in x.joints:
            x.notes.append(f"{label}: track {track.joint!r} names no joint of the exported skeletons (kept, unbound)")
    tags = {"file": source.name, "entry": entry, "name": a.name, "frame_rate": a.frame_rate}
    tags |= {"duration_ms": a.duration_ms, "tracks": [t.joint for t in a.tracks], "empty": empty}
    if not channels:
        x.notes.append(f"{label}: no keyed track; kept in the scene's extras only")
        return tags
    x.b.add("animations", _tag({"name": label, "channels": channels, "samplers": samplers}, **tags))
    return None


# Textures


def _texture(x: _Export, t: Texture) -> None:
    images = [x.b.add("images", {"bufferView": x.b.view(t.data), "mimeType": t.mime})]
    if t.fallback is not None:
        images.append(x.b.add("images", {"bufferView": x.b.view(t.fallback[0]), "mimeType": t.fallback[1]}))
    tex: dict = {"source": images[-1]}
    if t.mime == "image/vnd-ms.dds":
        tex = {"extensions": {"MSFT_texture_dds": {"source": images[0]}}}
        if t.fallback is not None:
            tex["source"] = images[1]
        used = x.b.doc.setdefault("extensionsUsed", [])
        if "MSFT_texture_dds" not in used:
            used.append("MSFT_texture_dds")
    index = x.b.add("textures", tex)
    for mat in x.b.doc.get("materials", []):
        if t.material is None or mat.get("name") == t.material:
            mat.setdefault("pbrMetallicRoughness", {})["baseColorTexture"] = {"index": index}
