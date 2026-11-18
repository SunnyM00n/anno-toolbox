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
from .rdmread import Mesh, RdmReadError, read_rdm

FLOAT, USHORT, UINT = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_BUFFER = 34962, 34963


def unit(v: tuple) -> tuple[float, float, float]:
    """``v`` at length 1; up for a vector without a length."""
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-9 else (0.0, 1.0, 0.0)


def normals_of(m: Mesh) -> list[tuple[float, float, float]]:
    """The mesh's unit vertex normals (up for a mesh without any)."""
    i = m.element_at(1)
    if i is None:
        return [(0.0, 1.0, 0.0)] * m.vertex_count
    if m.elements[i].type == 5:  # a packed direction: bytes x * 2 - 1
        return [unit(direction(b)) for b in m.element(i)]
    return [unit((v[0], v[1], v[2])) for v in m.element(i)]


def uvs_of(m: Mesh) -> list[tuple[float, float]]:
    """The mesh's first UV set per vertex (zeros for a mesh without one)."""
    i = next((i for i, e in enumerate(m.elements) if e.usage == 4 and e.type in (6, 7) and e.count >= 2), None)
    return [(0.0, 0.0)] * m.vertex_count if i is None else [(v[0], v[1]) for v in m.element(i)]


def rdm_to_glb(data: bytes, material: int | None = None) -> bytes:
    """The binary glTF of every mesh of the file, or only of the submeshes drawn with ``material`` (an index into
    the model's materials); raises ``RdmReadError`` for a file with none."""
    meshes = [m for m in read_rdm(data).meshes if m.indices and m.vertex_count and m.positions()]
    out = [(m.name, m.positions(), normals_of(m), uvs_of(m), material_indices(m, material)) for m in meshes]
    out = [m for m in out if m[4]]
    if not out:
        raise RdmReadError("no mesh with geometry in the file")
    return write_glb(out)


def material_indices(m: Mesh, material: int | None) -> list[int]:
    """The mesh's triangles, or those of its submeshes drawn with ``material`` (all of them for a mesh without
    submeshes, which draws with the first)."""
    if material is None or (not m.submeshes and material == 0):
        return m.indices
    return [
        i
        for s in m.submeshes
        if s.material == material
        for i in m.indices[s.first_index : s.first_index + s.index_count]
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
    for name, pos, normals, uvs, indices in meshes:
        n = len(pos)
        flat = [c for p in pos for c in p]
        lo = [min(p[k] for p in pos) for k in range(3)]
        hi = [max(p[k] for p in pos) for k in range(3)]
        attributes = {
            "POSITION": add(
                struct.pack(f"<{3 * n}f", *flat),
                ARRAY_BUFFER,
                componentType=FLOAT,
                count=n,
                type="VEC3",
                min=lo,
                max=hi,
            ),
            "NORMAL": add(
                struct.pack(f"<{3 * n}f", *(c for v in normals for c in v)),
                ARRAY_BUFFER,
                componentType=FLOAT,
                count=n,
                type="VEC3",
            ),
            "TEXCOORD_0": add(
                struct.pack(f"<{2 * n}f", *(c for v in uvs for c in v)),
                ARRAY_BUFFER,
                componentType=FLOAT,
                count=n,
                type="VEC2",
            ),
        }
        wide = max(indices) > 0xFFFF
        idx = struct.pack(f"<{len(indices)}{'I' if wide else 'H'}", *indices)
        at = add(idx, ELEMENT_BUFFER, componentType=UINT if wide else USHORT, count=len(indices), type="SCALAR")
        gltf_meshes.append({"name": name, "primitives": [{"attributes": attributes, "indices": at, "mode": 4}]})
        nodes.append({"name": name, "mesh": len(gltf_meshes) - 1})
    doc = {
        "asset": {"version": "2.0", "generator": "anno-toolbox"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": gltf_meshes,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    text = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    blob.extend(bytes(-len(blob) % 4))
    body = struct.pack("<I4s", len(text), b"JSON") + text + struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob)
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body
