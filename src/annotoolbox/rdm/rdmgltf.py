"""An ``.rdm`` as a glTF 2.0 document: every mesh, submesh, material, skeleton and animation of the source, nothing
added that is not marked as glTF's own.

``export`` is the entry: one or more RDM files (a LOD chain, say) and their animation files become one ``CBuilder``
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
from ..formats.gltf import FLOAT, UBYTE, UINT, USHORT, CBuilder
from ..formats.rdmread import SAnimation, SMesh, SRdmFile, SSkeleton
from ..models.meshxform import qconj
from . import rigmath
from .gltfdoc import EXTRAS

VERSION = 1


class CExportError(ValueError):
    """A source the glTF document cannot hold exactly; the message says what and why."""


@dataclass(frozen=True)
class SOptions:
    """``axes``: "gltf" mirrors Anno's left-handed space on x into glTF's right-handed one (exactly reversible),
    "anno" keeps the stored values. ``strict``: unit normals and tangents and weights that sum to one, as the glTF
    validator wants, instead of the stored values (a later import then rounds back to within one step)."""

    axes: str = "gltf"
    strict: bool = False

    @property
    def mirror(self) -> bool:
        return self.axes == "gltf"


@dataclass
class SSource:
    """One RDM file of the export: its name (the file name, kept for the import) and its contents."""

    name: str
    rdm: SRdmFile


@dataclass
class STexture:
    """An image for a material's base colour: its bytes and MIME type, and the material it is for (None: every one)."""

    data: bytes
    mime: str
    material: str | None = None
    fallback: tuple[bytes, str] | None = None  # a PNG beside a DDS that viewers can show


@dataclass
class _SExport:
    builder: CBuilder
    options: SOptions
    joints: dict[str, int] = field(default_factory=dict)  # joint name -> its node, for the animation channels
    notes: list[str] = field(default_factory=list)


def _tag(item: dict, **values) -> dict:
    item.setdefault("extras", {})[EXTRAS] = values
    return item


def export(
    sources: list[SSource],
    animations: list[SSource] = (),
    textures: list[STexture] = (),
    options: SOptions | None = None,
) -> tuple[CBuilder, list[str]]:
    """The glTF document of ``sources`` (mesh files) and the ``animations`` (animation files) that move them, and
    notes on what a viewer may show differently. Every item is tagged with the file it came from; with several mesh
    files each one's nodes hang under a node marked ``helper: file``, the one structure the export adds (with the
    scene, marked ``helper: scene``), and the import removes both again."""
    if not sources:
        raise CExportError("nothing to export")
    options = options or SOptions()
    exporter = _SExport(CBuilder(), options)
    exporter.builder.m_doc["asset"]["generator"] = "rdm-gltf"
    everything = [*sources, *animations]
    names = [file_source.name for file_source in everything]
    if len(set(names)) != len(names):
        raise CExportError("two inputs have the same file name; the import tells the files apart by it")
    for source in everything:
        if source.rdm.extra_streams:
            raise CExportError(f"{source.name}: a per-frame vertex stream has no place in glTF")
    skins = {
        file_source.name: [
            _skin(exporter, skeleton, k, file_source.name) for k, skeleton in enumerate(file_source.rdm.skeletons)
        ]
        for file_source in everything
    }
    roots = []
    for source in sources:
        nodes = _meshes(exporter, source, skins[source.name])
        if len(sources) == 1:
            roots += nodes
        else:
            helper = _tag({"name": source.name, "children": nodes}, helper="file", file=source.name)
            roots.append(exporter.builder.add("nodes", helper))
    roots += [node for file_source in animations for skin in skins[file_source.name] for node in skin["roots"]]
    files = [_file_info(file_source, "mesh") for file_source in sources] + [
        _file_info(file_source, "animation") for file_source in animations
    ]
    for file_info, source in zip(files, everything, strict=True):
        unkeyed = [_animation(exporter, animation, k, source) for k, animation in enumerate(source.rdm.animations)]
        if unkeyed := [unkeyed_animation for unkeyed_animation in unkeyed if unkeyed_animation is not None]:
            file_info["unkeyed"] = unkeyed
    for texture in textures:
        _texture(exporter, texture)
    exporter.builder.m_doc["scene"] = 0
    scene = {"version": VERSION, "axes": options.axes, "strict": options.strict, "files": files}
    exporter.builder.m_doc["scenes"] = [_tag({"nodes": roots}, helper="scene", **scene)]
    return exporter.builder, exporter.notes


def _file_info(source: SSource, kind: str) -> dict:
    rdm = source.rdm
    return {
        "name": source.name,
        "kind": kind,
        "source": rdm.source,
        "exporter": rdm.exporter,
        "big_endian": rdm.big_endian,
    }


def _meshes(exporter: _SExport, source: SSource, skins: list[dict]) -> list[int]:
    """The nodes of a mesh file: its meshes, then its skeletons' root joints."""
    rdm = source.rdm
    materials = [
        exporter.builder.add(
            "materials", _tag({"name": material.name}, file=source.name, index=k, texture=material.texture)
        )
        for k, material in enumerate(rdm.materials)
    ]
    nodes = []
    for k, rdm_mesh in enumerate(rdm.meshes):
        mesh = _mesh(exporter, rdm_mesh, materials, f"{source.name}: mesh {rdm_mesh.name!r}")
        node = {"name": rdm_mesh.name, "mesh": mesh}
        if rdm_mesh.skeleton is not None:
            if rdm_mesh.skeleton >= len(skins):
                raise CExportError(
                    f"{source.name}: mesh {rdm_mesh.name!r} names skeleton {rdm_mesh.skeleton} of {len(skins)}"
                )
            node["skin"] = skins[rdm_mesh.skeleton]["skin"]
        nodes.append(exporter.builder.add("nodes", _tag(node, file=source.name, mesh=k)))
    return nodes + [root for skin in skins for root in skin["roots"]]


# Meshes


def _columns(mesh: SMesh) -> list[list[tuple]]:
    return [mesh.element(i) for i in range(len(mesh.elements))]


def _crc(values) -> int:
    return zlib.crc32(repr(values).encode("ascii"))


def _mesh(exporter: _SExport, mesh: SMesh, materials: list[int], who: str) -> int:
    if mesh.stride != mesh.format_stride:
        raise CExportError(f"{who}: vertex block of {mesh.stride} bytes against a layout of {mesh.format_stride}")
    if not mesh.vertex_count or not mesh.indices:
        raise CExportError(f"{who}: no geometry")
    columns = _columns(mesh)
    attributes, mesh_tags = _attributes(exporter, mesh, columns)
    mesh_tags |= {"name": mesh.name, "bones_per_vertex": mesh.bones_per_vertex, "index_type": mesh.index_type}
    mesh_tags |= {"skeleton": mesh.skeleton, "box": list(mesh.bbox), "positions_crc": _crc(columns[0])}
    primitives = []
    for first, count, material in _ranges(mesh, who):
        triangles = mesh.indices[first : first + count]
        if exporter.options.mirror:
            triangles = _reverse(triangles)
        component = UINT if max(triangles) >= 0xFFFF else USHORT
        primitive = {
            "attributes": attributes,
            "indices": exporter.builder.accessor(triangles, "SCALAR", component, target=gltf.ELEMENT_BUFFER),
        }
        primitive["mode"] = 4
        if material is not None:
            if material >= len(materials):
                raise CExportError(f"{who}: a submesh names material {material} of {len(materials)}")
            primitive["material"] = materials[material]
        primitives.append(primitive)
    mesh_tags["submeshes"] = bool(mesh.submeshes)
    return exporter.builder.add("meshes", _tag({"name": mesh.name, "primitives": primitives}, **mesh_tags))


def _ranges(mesh: SMesh, who: str) -> list[tuple[int, int, int | None]]:
    if not mesh.submeshes:
        return [(0, len(mesh.indices), None)]
    expected_first = 0
    for submesh in mesh.submeshes:
        if submesh.first_index != expected_first or submesh.index_count % 3 or not submesh.index_count:
            raise CExportError(f"{who}: submeshes do not run in order over whole triangles; glTF primitives cannot")
        expected_first += submesh.index_count
    if expected_first != len(mesh.indices):
        raise CExportError(f"{who}: submeshes do not cover every index; glTF primitives cannot")
    return [(submesh.first_index, submesh.index_count, submesh.material) for submesh in mesh.submeshes]


def _reverse(indices: list[int]) -> list[int]:
    out = []
    for k in range(0, len(indices) - 2, 3):
        out += [indices[k], indices[k + 2], indices[k + 1]]
    return out


def _attributes(exporter: _SExport, mesh: SMesh, columns: list[list[tuple]]) -> tuple[dict, dict]:
    """The glTF attributes of a mesh and the extras that complete them: per element its standard attribute, a
    constant 4th component, or the whole element as ``_RDM_<n>``."""
    attributes: dict[str, int] = {}
    fourth: dict[str, float] = {}
    seen: Counter[int] = Counter()
    elements = mesh.elements
    by_usage = {usage: [i for i, element in enumerate(elements) if element.usage == usage] for usage in range(8)}
    for i, element in enumerate(elements):
        nth = seen[element.usage]
        seen[element.usage] += 1
        name, values, component, normalized = _standard(exporter, mesh, columns, i, nth, by_usage)
        if name is None:
            attributes[f"_RDM_{i}"] = _raw(exporter, element, columns[i])
            continue
        attributes[name] = exporter.builder.accessor(
            values, _kind(values), component, normalized=normalized, target=gltf.ARRAY_BUFFER
        )
        rest = _fourth(element, columns[i])
        if rest == "varies":
            attributes[f"_RDM_{i}"] = _raw(exporter, element, columns[i])
        elif rest is not None:
            fourth[str(i)] = rest
    mesh_tags: dict = {
        "elements": [[element.usage, element.type, element.subtype, element.count] for element in elements],
        "fourth": fourth,
    }
    implied = []  # what glTF needs for a skinned mesh and the source does not hold: marked, dropped on import
    if mesh.skeleton is not None and "JOINTS_0" not in attributes:
        attributes["JOINTS_0"] = exporter.builder.accessor([(0, 0, 0, 0)] * mesh.vertex_count, "VEC4", UBYTE)
        implied.append("JOINTS_0")  # a rigid part: the whole mesh on its skeleton's first joint
    if "JOINTS_0" in attributes and "WEIGHTS_0" not in attributes:
        attributes["WEIGHTS_0"] = exporter.builder.accessor(
            [(255, 0, 0, 0)] * mesh.vertex_count, "VEC4", UBYTE, normalized=True
        )
        implied.append("WEIGHTS_0")  # one bone a vertex
    if implied:
        mesh_tags["implied"] = implied
    return attributes, mesh_tags


def _kind(values: list) -> str:
    return {2: "VEC2", 3: "VEC3", 4: "VEC4"}[len(values[0])]


def _fourth(element, column: list[tuple]) -> float | str | None:
    """The 4th component a standard attribute does not carry: a position's w, a packed direction's last byte."""
    if not ((element.usage == 0 and element.count == 4) or (element.usage in (1, 2, 3) and element.type == 5)):
        return None
    values = {row[3] for row in column}
    return values.pop() if len(values) == 1 else "varies"


def _standard(exporter: _SExport, mesh: SMesh, columns, i: int, nth: int, by_usage) -> tuple:
    """``(attribute, values, component type, normalized)`` of element ``i``, or ``(None, ...)`` for one glTF has
    no attribute for."""
    element, column, mirror = mesh.elements[i], columns[i], exporter.options.mirror
    flip = (lambda row: (-row[0], row[1], row[2])) if mirror else (lambda row: (row[0], row[1], row[2]))
    floats = element.type in (6, 7)
    if element.usage == 0 and nth == 0 and floats and element.count >= 3:
        return "POSITION", [flip(row) for row in column], FLOAT, False
    if element.usage in (1, 2, 3) and nth == 0 and (element.type == 5 or (floats and element.count == 3)):
        vectors = [flip(_vector(element, row)) for row in column]
        if element.usage == 1:
            return "NORMAL", _unit(vectors) if exporter.options.strict else vectors, FLOAT, False
        vectors = [
            (-first, -second, -third) for first, second, third in vectors
        ]  # stored as -dP/du, -dP/dv; glTF wants +dP/du
        if element.usage == 3:
            return "_BITANGENT", vectors, FLOAT, False
        return (
            "TANGENT",
            _tangents(mesh, columns, by_usage, _unit(vectors) if exporter.options.strict else vectors, mirror),
            FLOAT,
            False,
        )
    if element.usage == 4 and floats and element.count == 2:
        return f"TEXCOORD_{nth}", column, FLOAT, False
    if element.usage == 5 and element.type == 5:
        return f"COLOR_{nth}", column, UBYTE, True
    if element.usage == 7 and element.type == 5 and nth == 0:
        return "JOINTS_0", column, UBYTE, False
    if element.usage == 6 and element.type == 5 and nth == 0:
        return "WEIGHTS_0", _weights(column) if exporter.options.strict else column, UBYTE, True
    return None, None, None, None


def _vector(element, value: tuple) -> tuple[float, float, float]:
    return rdmlayout.direction(value) if element.type == 5 else (value[0], value[1], value[2])


def _unit(vectors: list) -> list:
    out = []
    for vector in vectors:
        length = sum(component * component for component in vector) ** 0.5
        out.append(tuple(component / length for component in vector) if length > 1e-12 else (0.0, 1.0, 0.0))
    return out


def _tangents(mesh: SMesh, columns, by_usage, xyz: list, mirror: bool) -> list:
    """glTF's VEC4 tangents: ``xyz``, and in w the sign that turns cross(normal, tangent) towards the stored
    bitangent (computed in the stored space; a mirror turns the frame's handedness)."""

    def vectors(usage: int) -> list | None:
        if not by_usage[usage]:
            return None
        i = by_usage[usage][0]
        return [_vector(mesh.elements[i], row) for row in columns[i]]

    normals, tangents, bitangents = vectors(1), vectors(2), vectors(3)
    out = []
    for k, direction in enumerate(xyz):
        sign = 1.0
        if normals and bitangents:
            normal, tangent, bitangent = (
                normals[k],
                tangents[k],
                bitangents[k],
            )  # stored: -dP/du, -dP/dv; their signs cancel
            cross = (
                normal[1] * tangent[2] - normal[2] * tangent[1],
                normal[2] * tangent[0] - normal[0] * tangent[2],
                normal[0] * tangent[1] - normal[1] * tangent[0],
            )
            sign = 1.0 if cross[0] * bitangent[0] + cross[1] * bitangent[1] + cross[2] * bitangent[2] >= 0 else -1.0
        out.append((*direction, -sign if mirror else sign))
    return out


def _weights(column: list[tuple]) -> list[tuple]:
    """Weights scaled to sum to 255 (largest remainder), as glTF wants; the game divides by the sum anyway."""
    out = []
    for vertex_weights in column:
        total = sum(vertex_weights)
        if not total:
            out.append((255, 0, 0, 0))
            continue
        exact = [component * 255 / total for component in vertex_weights]
        base = [int(component) for component in exact]
        for k in sorted(range(4), key=lambda k: base[k] - exact[k])[: 255 - sum(base)]:
            base[k] += 1
        out.append(tuple(base))
    return out


def _raw(exporter: _SExport, element, column: list[tuple]) -> int:
    """The whole element as stored: bytes for a packed vector, floats for half and float, the integers otherwise."""
    if element.type == 5:
        return exporter.builder.accessor(column, "VEC4", UBYTE)
    kind = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}[element.count]
    values = column if element.count > 1 else [row[0] for row in column]
    component = FLOAT if element.type in (6, 7) else {0: UBYTE, 2: USHORT}.get(element.type, UINT)
    return exporter.builder.accessor(values, kind, component)


# Skeletons


def inverse_bind(position, rotation) -> rigmath.Matrix:
    """A joint's stored inverse bind pose as a matrix (its quaternion is the conjugate of glTF's)."""
    return rigmath.from_rotation_translation(qconj(rotation), position)


def _skin(exporter: _SExport, skeleton: SSkeleton, index: int, file: str) -> dict:
    """A skin of joint nodes in the skeleton's order, at the rest pose its inverse bind poses imply; each joint
    keeps its stored values in the extras."""
    inverse_binds = [inverse_bind(joint.position, joint.rotation) for joint in skeleton.joints]
    if exporter.options.mirror:
        inverse_binds = [rigmath.mirrored(matrix) for matrix in inverse_binds]
    binds = [rigmath.inverse(matrix) for matrix in inverse_binds]
    nodes, roots = [], []
    for k, joint in enumerate(skeleton.joints):
        local = binds[k] if joint.parent is None else rigmath.multiply(inverse_binds[joint.parent], binds[k])
        rotation, translation, scale = rigmath.decompose(local)
        node = {"name": joint.name, "rotation": list(rotation), "translation": list(translation)}
        if any(abs(component - 1) > 1e-5 for component in scale):
            node["scale"] = list(scale)
        tags = {"file": file, "skeleton": index, "joint": k, "position": list(joint.position)}
        tags |= {"rotation": list(joint.rotation), "box_min": list(joint.box_min), "box_max": list(joint.box_max)}
        nodes.append(exporter.builder.add("nodes", _tag(node, **tags)))
        exporter.joints.setdefault(joint.name, nodes[-1])
    for k, joint in enumerate(skeleton.joints):
        if joint.parent is None:
            roots.append(nodes[k])
        else:
            exporter.builder.m_doc["nodes"][nodes[joint.parent]].setdefault("children", []).append(nodes[k])
    inverse_bind_accessor = exporter.builder.accessor(
        [rigmath.to_columns(matrix) for matrix in inverse_binds], "MAT4", FLOAT
    )
    tags = {"file": file, "skeleton": index, "word": skeleton.word}
    if skeleton.extra:
        tags["extra"] = base64.b64encode(skeleton.extra).decode("ascii")
    skin = exporter.builder.add(
        "skins",
        _tag({"name": f"skeleton {index}", "joints": nodes, "inverseBindMatrices": inverse_bind_accessor}, **tags),
    )
    return {"skin": skin, "roots": roots}


# Animations


def _animation(exporter: _SExport, animation: SAnimation, entry: int, source: SSource) -> dict | None:
    """Entry ``entry`` of a file's animations as a glTF animation; None, or the extras of an entry without a key
    (glTF wants a channel in every animation, so that one lives in the scene's extras)."""
    many = len(source.rdm.animations) > 1
    label = Path(source.name).stem + (f":{entry}" if many else "")
    channels, samplers, empty, bound = [], [], [], set()
    for k, track in enumerate(animation.tracks):
        if not track.keys:
            empty.append(k)
            continue
        times = exporter.builder.accessor([key.time for key in track.keys], "SCALAR", FLOAT)
        rotations = [qconj(key.rotation) for key in track.keys]
        positions = [key.position for key in track.keys]
        if exporter.options.mirror:
            rotations, positions = (
                [rigmath.mirrored_rotation(rotation) for rotation in rotations],
                [rigmath.mirrored_point(point) for point in positions],
            )
        node = exporter.joints.get(track.joint)
        if node in bound:  # glTF allows one channel per joint and path: a second track on the joint stays unbound
            exporter.notes.append(f"{label}: a second track on joint {track.joint!r} (kept, unbound)")
            node = None
        elif node is not None:
            bound.add(node)
        for path, values, kind in (("rotation", rotations, "VEC4"), ("translation", positions, "VEC3")):
            samplers.append(
                {"input": times, "output": exporter.builder.accessor(values, kind, FLOAT), "interpolation": "LINEAR"}
            )
            target = {"path": path} if node is None else {"node": node, "path": path}
            channels.append(_tag({"sampler": len(samplers) - 1, "target": target}, joint=track.joint, track=k))
        if node is None and track.joint not in exporter.joints:
            exporter.notes.append(
                f"{label}: track {track.joint!r} names no joint of the exported skeletons (kept, unbound)"
            )
    tags = {"file": source.name, "entry": entry, "name": animation.name, "frame_rate": animation.frame_rate}
    tags |= {
        "duration_ms": animation.duration_ms,
        "tracks": [animation_track.joint for animation_track in animation.tracks],
        "empty": empty,
    }
    if not channels:
        exporter.notes.append(f"{label}: no keyed track; kept in the scene's extras only")
        return tags
    exporter.builder.add("animations", _tag({"name": label, "channels": channels, "samplers": samplers}, **tags))
    return None


# Textures


def _texture(exporter: _SExport, texture: STexture) -> None:
    images = [
        exporter.builder.add("images", {"bufferView": exporter.builder.view(texture.data), "mimeType": texture.mime})
    ]
    if texture.fallback is not None:
        images.append(
            exporter.builder.add(
                "images", {"bufferView": exporter.builder.view(texture.fallback[0]), "mimeType": texture.fallback[1]}
            )
        )
    gltf_texture: dict = {"source": images[-1]}
    if texture.mime == "image/vnd-ms.dds":
        gltf_texture = {"extensions": {"MSFT_texture_dds": {"source": images[0]}}}
        if texture.fallback is not None:
            gltf_texture["source"] = images[1]
        used = exporter.builder.m_doc.setdefault("extensionsUsed", [])
        if "MSFT_texture_dds" not in used:
            used.append("MSFT_texture_dds")
    index = exporter.builder.add("textures", gltf_texture)
    for material in exporter.builder.m_doc.get("materials", []):
        if texture.material is None or material.get("name") == texture.material:
            material.setdefault("pbrMetallicRoughness", {})["baseColorTexture"] = {"index": index}
