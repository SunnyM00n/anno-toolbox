"""A glTF 2.0 document back to ``.rdm`` files: what the source held, and nothing a modelling tool added on its own.

``import_gltf`` is the entry: it finds the files of the document (one, or one per ``helper: file`` node of
``rdmgltf``), their meshes (``gltfmesh``), materials and skeletons (``gltfskin``) and the animation files, and
returns them as ``SRdmFile``s with notes on what it dropped, filled or could not keep. The extras ``rdmgltf`` wrote
restore the stored values exactly; a document from elsewhere is read by the glTF rules alone (docs/rdm-gltf.md).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..formats.gltf import SGltf
from ..formats.rdmread import SMaterial, SRdmFile
from . import gltfmesh, gltfskin
from .gltfdoc import CGltfImportError, SDoc, SOptions, tags


@dataclass
class SImported:
    files: list[tuple[str, SRdmFile]] = field(default_factory=list)  # (file name, contents), mesh files first
    notes: list[str] = field(default_factory=list)


def import_gltf(gltf: SGltf, default_name: str, options: SOptions | None = None) -> SImported:
    """The RDM files of a glTF document (``default_name``: the file name of a document that does not say its own)."""
    options = options or SOptions()
    scenes = gltf.items("scenes")
    scene = scenes[gltf.doc.get("scene", 0)] if scenes else {"nodes": _unparented(gltf)}
    axes = options.axes or tags(scene).get("axes") or "gltf"
    if axes not in ("gltf", "anno"):
        raise CGltfImportError(f"axes {axes!r}: gltf or anno")
    document = SDoc(gltf, options, axes == "gltf", scene)
    file_infos = {
        entry["name"]: entry for entry in tags(scene).get("files", []) if isinstance(entry, dict) and "name" in entry
    }
    out = SImported(notes=document.notes)
    groups = _mesh_files(document, default_name)
    used: set[int] = set()
    for name, nodes in groups:
        out.files.append((name, _mesh_file(document, name, nodes, file_infos.get(name, {}), used)))
    for name, rdm, joints in gltfskin.animation_files(document, file_infos, default_name):
        used |= joints
        target = next((file for file_name, file in out.files if file_name == name), None)
        if target is not None:  # a mesh file's own animations
            target.animations = rdm.animations
        else:
            out.files.append((name, rdm))
    _foreign(document, used)
    return out


def _unparented(gltf: SGltf) -> list[int]:
    children = {child for node in gltf.items("nodes") for child in node.get("children", [])}
    return [i for i in range(len(gltf.items("nodes"))) if i not in children]


def _mesh_files(document: SDoc, default_name: str) -> list[tuple[str, list[int]]]:
    """(file name, its mesh nodes in file order) per file: one per ``helper: file`` root, or the one file."""
    roots = document.scene.get("nodes", [])
    helpers = [root for root in roots if tags(document.node(root)).get("helper") == "file"]
    if helpers:
        groups = [
            (tags(document.node(helper)).get("file") or document.name(helper), document.walk([helper]))
            for helper in helpers
        ]
    else:
        files = [
            file_entry
            for file_entry in tags(document.scene).get("files", [])
            if isinstance(file_entry, dict) and file_entry.get("kind") == "mesh"
        ]
        groups = [(files[0]["name"] if len(files) == 1 else default_name, document.walk(roots))]
    out = []
    for name, nodes in groups:
        meshes = [node for node in nodes if "mesh" in document.node(node) and _wanted(document, node)]
        order = [tags(document.node(node)).get("mesh") for node in meshes]
        if all(isinstance(k, int) for k in order) and len(set(order)) == len(order):
            meshes = [node for _k, node in sorted(zip(order, meshes, strict=True))]
        if meshes:
            out.append((name, meshes))
    if not out:
        raise CGltfImportError(
            "the scene holds no mesh" + (f" named or numbered {document.options.mesh}" if document.options.mesh else "")
        )
    return out


def _wanted(document: SDoc, node: int) -> bool:
    want = document.options.mesh
    if want is None:
        return True
    mesh = document.node(node)["mesh"]
    return str(mesh) == want or document.gltf.items("meshes")[mesh].get("name") == want


def _mesh_file(document: SDoc, name: str, nodes: list[int], file_info: dict, used: set[int]) -> SRdmFile:
    like = document.options.like
    source = file_info.get("source", like.source if like else "")
    exporter = file_info.get("exporter", like.exporter if like else "")
    rdm = SRdmFile(False, source, exporter)
    materials = _materials(document, name, nodes)
    rdm.materials = [material for _i, material in materials]
    skeletons = gltfskin.skeletons(document, name, nodes)
    rdm.skeletons = [skin.skeleton for skin in skeletons]
    names: Counter[str] = Counter()
    for node in nodes:
        used.add(node)
        mesh = gltfmesh.mesh(document, node, [i for i, _m in materials], skeletons)
        names[mesh.name] += 1
        rdm.meshes.append(mesh)
    used.update(j for skin in skeletons for j in skin.nodes)
    if any(count > 1 for count in names.values()):
        document.notes.append(f"{name}: two meshes share a name")
    return rdm


def _first_use(document: SDoc, nodes: list[int]) -> list[int]:
    out: list[int] = []
    for node in nodes:
        for k, prim in enumerate(document.gltf.items("meshes")[document.node(node)["mesh"]].get("primitives", [])):
            if "material" not in prim:
                raise CGltfImportError(
                    f"mesh {document.name(node)!r}, primitive {k}: no material (an RDM submesh names one: assign one in the "
                    "modelling tool)"
                )
            if prim["material"] not in out:
                out.append(prim["material"])
    return out


def _materials(document: SDoc, file: str, nodes: list[int]) -> list[tuple[int, SMaterial]]:
    """(glTF material, RDM material) in the file's order: the tagged order, else ``material_order`` or the ``like``
    file's names, else the order of first use. A primitive without a material is refused: a submesh names one."""
    first_use = _first_use(document, nodes)
    gltf_materials = document.gltf.items("materials")
    tagged = [
        i
        for i, material in enumerate(gltf_materials)
        if tags(material).get("file") == file and isinstance(tags(material).get("index"), int)
    ]
    order = sorted(tagged, key=lambda i: tags(gltf_materials[i])["index"]) + [i for i in first_use if i not in tagged]
    wanted = list(document.options.material_order) or (
        [material.name for material in document.options.like.materials] if document.options.like else []
    )
    if not tagged and wanted:
        rank = {name: k for k, name in enumerate(wanted)}
        order = sorted(
            first_use, key=lambda i: (rank.get(gltf_materials[i].get("name", ""), len(rank)), first_use.index(i))
        )
    out = []
    for i in order:
        material = gltf_materials[i]
        texture = tags(material).get("texture")
        if texture is None and document.options.like:
            texture = next(
                (x.texture for x in document.options.like.materials if x.name == material.get("name", "")), ""
            )
        out.append((i, SMaterial(material.get("name", ""), texture or "")))
    return out


def _foreign(document: SDoc, used: set[int]) -> None:
    """Note every node of the scene the files did not take: cameras, lights, empties, an armature object."""
    for i in document.walk(document.scene.get("nodes", [])):
        if i in used or tags(document.node(i)).get("helper"):
            continue
        node = document.node(i)
        if "camera" in node:
            kind = "a camera"
        elif "KHR_lights_punctual" in node.get("extensions", {}):
            kind = "a light"
        elif "mesh" in node:
            kind = "a mesh left out (--mesh)"
        else:
            kind = "an empty or the armature object"
        document.notes.append(f"dropped node {document.name(i)!r}: {kind}, not part of an RDM file")
