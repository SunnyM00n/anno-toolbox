"""An ``.rdm`` mesh file as a binary glTF, for the island editor's views (2070, 117, 1800 and 2205 models alike).

Read with ``rdmread`` (every layout, both byte orders): one glTF mesh per RDM mesh at its stored pose, positions
as they are (Anno's left-handed axes; the editor mirrors on load), unit normals, the first UV set and the
indices in file order. No skin, no materials: the views bring their own.
"""

from __future__ import annotations

import json
import math
import struct

from .rdmlayout import direction
from .rdmread import CRdmReadError, SMesh, read_rdm

FLOAT, USHORT, UINT = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_BUFFER = 34962, 34963


def unit(vector: tuple) -> tuple[float, float, float]:
    """``vector`` at length 1; up for a vector without a length."""
    length = math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)
    return (vector[0] / length, vector[1] / length, vector[2] / length) if length > 1e-9 else (0.0, 1.0, 0.0)


def normals_of(mesh: SMesh) -> list[tuple[float, float, float]]:
    """The mesh's unit vertex normals (up for a mesh without any)."""
    i = mesh.element_at(1)
    if i is None:
        return [(0.0, 1.0, 0.0)] * mesh.vertex_count
    if mesh.elements[i].type == 5:  # a packed direction: bytes x * 2 - 1
        return [unit(direction(packed)) for packed in mesh.element(i)]
    return [unit((vector[0], vector[1], vector[2])) for vector in mesh.element(i)]


def uvs_of(mesh: SMesh) -> list[tuple[float, float]]:
    """The mesh's first UV set per vertex (zeros for a mesh without one)."""
    i = next(
        (
            i
            for i, element in enumerate(mesh.elements)
            if element.usage == 4 and element.type in (6, 7) and element.count >= 2
        ),
        None,
    )
    return [(0.0, 0.0)] * mesh.vertex_count if i is None else [(vector[0], vector[1]) for vector in mesh.element(i)]


def rdm_to_glb(data: bytes, material: int | None = None) -> bytes:
    """The binary glTF of every mesh of the file, or only of the submeshes drawn with ``material`` (an index into
    the model's materials); raises ``CRdmReadError`` for a file with none."""
    meshes = [mesh for mesh in read_rdm(data).meshes if mesh.indices and mesh.vertex_count and mesh.positions()]
    out = [
        (mesh.name, mesh.positions(), normals_of(mesh), uvs_of(mesh), material_indices(mesh, material))
        for mesh in meshes
    ]
    out = [mesh for mesh in out if mesh[4]]
    if not out:
        raise CRdmReadError("no mesh with geometry in the file")
    return write_glb(out)


def material_indices(mesh: SMesh, material: int | None) -> list[int]:
    """The mesh's triangles, or those of its submeshes drawn with ``material`` (all of them for a mesh without
    submeshes, which draws with the first)."""
    if material is None or (not mesh.submeshes and material == 0):
        return mesh.indices
    return [
        i
        for submesh in mesh.submeshes
        if submesh.material == material
        for i in mesh.indices[submesh.first_index : submesh.first_index + submesh.index_count]
    ]


def write_glb(meshes: list[tuple[str, list, list, list, list]]) -> bytes:
    """A binary glTF of meshes given as ``(name, positions, normals, uvs, indices)``, one node each
    (``grannyglb`` writes 1404's and 1701's meshes through it)."""
    blob = bytearray()
    views: list[dict] = []
    accessors: list[dict] = []

    def add(payload: bytes, target: int, **accessor) -> int:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(payload), "target": target})
        blob.extend(payload)
        accessors.append({"bufferView": len(views) - 1, **accessor})
        return len(accessors) - 1

    gltf_meshes, nodes = [], []
    for name, positions, normals, uvs, indices in meshes:
        vertex_count = len(positions)
        flat = [component for position in positions for component in position]
        minimum = [min(position[k] for position in positions) for k in range(3)]
        maximum = [max(position[k] for position in positions) for k in range(3)]
        attributes = {
            "POSITION": add(
                struct.pack(f"<{3 * vertex_count}f", *flat),
                ARRAY_BUFFER,
                componentType=FLOAT,
                count=vertex_count,
                type="VEC3",
                min=minimum,
                max=maximum,
            ),
            "NORMAL": add(
                struct.pack(f"<{3 * vertex_count}f", *(component for vector in normals for component in vector)),
                ARRAY_BUFFER,
                componentType=FLOAT,
                count=vertex_count,
                type="VEC3",
            ),
            "TEXCOORD_0": add(
                struct.pack(f"<{2 * vertex_count}f", *(component for vector in uvs for component in vector)),
                ARRAY_BUFFER,
                componentType=FLOAT,
                count=vertex_count,
                type="VEC2",
            ),
        }
        wide = max(indices) > 0xFFFF
        index_bytes = struct.pack(f"<{len(indices)}{'I' if wide else 'H'}", *indices)
        index_accessor = add(
            index_bytes, ELEMENT_BUFFER, componentType=UINT if wide else USHORT, count=len(indices), type="SCALAR"
        )
        gltf_meshes.append(
            {"name": name, "primitives": [{"attributes": attributes, "indices": index_accessor, "mode": 4}]}
        )
        nodes.append({"name": name, "mesh": len(gltf_meshes) - 1})
    document = {
        "asset": {"version": "2.0", "generator": "anno-toolbox"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": gltf_meshes,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    text = json.dumps(document, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    blob.extend(bytes(-len(blob) % 4))
    body = struct.pack("<I4s", len(text), b"JSON") + text + struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob)
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body
