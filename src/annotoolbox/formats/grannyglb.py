"""Granny ``.gr2`` meshes (``grannyread``) as binary glTF, for the island editor's views of 1404's and 1701's
models: positions as stored (the file's own axes; the editor mirrors on load), unit normals, the first UV
set, the triangles of each material group.

``granny_to_glb(data, material)`` is the entry; ``read_meshes`` gives the meshes as plain lists (``meshes_of`` from
a root read already).
docs/formats/granny.md, "Meshes".
"""

from __future__ import annotations

from dataclasses import dataclass

from . import grannyanim
from . import grannyread as gr
from .rdmglb import unit, write_glb

_SCALE = {gr.BINORMAL_INT8: 127.0, gr.BINORMAL_INT16: 32767.0, gr.NORMAL_UINT8: 255.0, gr.NORMAL_UINT16: 65535.0}


@dataclass(frozen=True)
class GrannyMesh:
    """One mesh: its vertices and, per material group, the material's name and the group's indices; its vertex
    colours (r, g, b, a from 0 to 1, None without), skin (per vertex its bone numbers and weights, None for a rigid
    mesh) and ``bones``, the bone names those numbers count (a rigid mesh: the one it moves with)."""

    name: str
    positions: list[tuple[float, float, float]]
    normals: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    groups: list[tuple[str, list[int]]]
    colors: list[tuple[float, ...]] | None = None
    bone_indices: list[tuple[int, ...]] | None = None
    bone_weights: list[tuple[float, ...]] | None = None
    bones: tuple[str, ...] = ()


def _column(vertices: gr.GrannyArray, name: str, size: int) -> list[tuple] | None:
    """A vertex field as float tuples of ``size``, packed integers scaled to their range; None without it."""
    field = next((f for f in vertices.fields if f[0] == name), None)
    if field is None or max(field[2], 1) < size:
        return None
    scale = _SCALE.get(field[1], 1.0)
    return [tuple(c / scale for c in v[:size]) for v in vertices.column(name)]


def _indices(topology) -> list[int]:
    wide = topology["Indices"]
    if isinstance(wide, gr.GrannyArray) and wide.count:
        return [r[0] for r in wide.records()]
    narrow = topology["Indices16"]
    return [r[0] & 0xFFFF for r in narrow.records()] if isinstance(narrow, gr.GrannyArray) else []


def _material_names(mesh) -> list[str]:
    out = []
    for binding in mesh["MaterialBindings"] or []:
        material = binding["Material"] if binding is not None else None
        out.append(material["Name"] if material is not None else "")
    return out


def _mesh(mesh) -> GrannyMesh | None:
    data, topology = mesh["PrimaryVertexData"], mesh["PrimaryTopology"]
    vertices = data["Vertices"] if data is not None else None
    if not isinstance(vertices, gr.GrannyArray) or topology is None:
        return None
    positions = _column(vertices, "Position", 3)
    if not positions:
        return None
    normals = [unit(v) for v in _column(vertices, "Normal", 3) or [(0.0, 1.0, 0.0)] * len(positions)]
    uvs = _column(vertices, "TextureCoordinates0", 2) or [(0.0, 0.0)] * len(positions)
    indices, names = _indices(topology), _material_names(mesh)
    groups = []
    raw_groups = topology["Groups"]
    for material, first, count in raw_groups.records() if isinstance(raw_groups, gr.GrannyArray) else []:
        name = names[material] if 0 <= material < len(names) else ""
        groups.append((name, indices[3 * first : 3 * (first + count)]))
    if not groups:
        groups = [(names[0] if names else "", indices)]
    colors = _column(vertices, "DiffuseColor0", 4)
    skin = vertices.column("BoneIndices") if "BoneIndices" in vertices.names() else None
    weights = _column(vertices, "BoneWeights", len(skin[0])) if skin else None  # 1404's one bone a vertex: none
    bones = tuple(grannyanim.bone_names(mesh))
    return GrannyMesh(mesh["Name"], positions, normals, uvs, groups, colors, skin, weights, bones)


def read_meshes(data: bytes) -> list[GrannyMesh]:
    """Every mesh of a ``.gr2`` with vertices and triangles; raises ``GrannyError`` for a file it cannot read."""
    return meshes_of(gr.GrannyFile(data).root())


def meshes_of(root: gr.GrannyObject) -> list[GrannyMesh]:
    """``read_meshes`` of a file's root already read (one file read once for its meshes, skeletons and animations)."""
    meshes = root["Meshes"] or []
    return [m for m in (_mesh(x) for x in meshes if x is not None) if m and any(g[1] for g in m.groups)]


def granny_to_glb(data: bytes, material: str | None = None) -> bytes:
    """The binary glTF of a ``.gr2``'s meshes; with ``material``, only the triangles of the groups that use
    a material of that name (case blind). Raises ``GrannyError`` when nothing is left to draw."""
    return meshes_to_glb(read_meshes(data), material)


def meshes_to_glb(meshes: list[GrannyMesh], material: str | None = None) -> bytes:
    """``granny_to_glb`` for meshes already read (one file's materials written one by one)."""
    out = []
    for m in meshes:
        tris = [i for name, idx in m.groups if material is None or name.lower() == material.lower() for i in idx]
        if tris and max(tris) < len(m.positions):
            out.append((m.name, m.positions, m.normals, m.uvs, tris))
    if not out:
        raise gr.GrannyError("no mesh with triangles" + (f" of material {material!r}" if material else ""))
    return write_glb(out)
