"""A glTF 2.0 document back to ``.rdm`` files: what the source held, and nothing a modelling tool added on its own.

``import_gltf`` is the entry: it finds the files of the document (one, or one per ``helper: file`` node of
``rdmgltf``), their meshes (``gltfmesh``), materials and skeletons (``gltfskin``) and the animation files, and
returns them as ``RdmFile``s with notes on what it dropped, filled or could not keep. The extras ``rdmgltf`` wrote
restore the stored values exactly; a document from elsewhere is read by the glTF rules alone (docs/rdm-gltf.md).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..formats.gltf import Gltf
from ..formats.rdmread import Material, RdmFile
from . import gltfmesh, gltfskin
from .gltfdoc import Doc, GltfImportError, Options, tags


@dataclass
class Imported:
    files: list[tuple[str, RdmFile]] = field(default_factory=list)  # (file name, contents), mesh files first
    notes: list[str] = field(default_factory=list)


def import_gltf(g: Gltf, default_name: str, opts: Options | None = None) -> Imported:
    """The RDM files of a glTF document (``default_name``: the file name of a document that does not say its own)."""
    opts = opts or Options()
    scenes = g.items("scenes")
    scene = scenes[g.doc.get("scene", 0)] if scenes else {"nodes": _unparented(g)}
    axes = opts.axes or tags(scene).get("axes") or "gltf"
    if axes not in ("gltf", "anno"):
        raise GltfImportError(f"axes {axes!r}: gltf or anno")
    d = Doc(g, opts, axes == "gltf", scene)
    infos = {f["name"]: f for f in tags(scene).get("files", []) if isinstance(f, dict) and "name" in f}
    out = Imported(notes=d.notes)
    groups = _mesh_files(d, default_name)
    used: set[int] = set()
    for name, nodes in groups:
        out.files.append((name, _mesh_file(d, name, nodes, infos.get(name, {}), used)))
    for name, rdm, joints in gltfskin.animation_files(d, infos, default_name):
        used |= joints
        target = next((f for n, f in out.files if n == name), None)
        if target is not None:  # a mesh file's own animations
            target.animations = rdm.animations
        else:
            out.files.append((name, rdm))
    _foreign(d, used)
    return out


def _unparented(g: Gltf) -> list[int]:
    children = {c for n in g.items("nodes") for c in n.get("children", [])}
    return [i for i in range(len(g.items("nodes"))) if i not in children]


def _mesh_files(d: Doc, default_name: str) -> list[tuple[str, list[int]]]:
    """(file name, its mesh nodes in file order) per file: one per ``helper: file`` root, or the one file."""
    roots = d.scene.get("nodes", [])
    helpers = [r for r in roots if tags(d.node(r)).get("helper") == "file"]
    if helpers:
        groups = [(tags(d.node(h)).get("file") or d.name(h), d.walk([h])) for h in helpers]
    else:
        files = [f for f in tags(d.scene).get("files", []) if isinstance(f, dict) and f.get("kind") == "mesh"]
        groups = [(files[0]["name"] if len(files) == 1 else default_name, d.walk(roots))]
    out = []
    for name, nodes in groups:
        meshes = [n for n in nodes if "mesh" in d.node(n) and _wanted(d, n)]
        order = [tags(d.node(n)).get("mesh") for n in meshes]
        if all(isinstance(k, int) for k in order) and len(set(order)) == len(order):
            meshes = [n for _k, n in sorted(zip(order, meshes, strict=True))]
        if meshes:
            out.append((name, meshes))
    if not out:
        raise GltfImportError("the scene holds no mesh" + (f" named or numbered {d.opts.mesh}" if d.opts.mesh else ""))
    return out


def _wanted(d: Doc, node: int) -> bool:
    want = d.opts.mesh
    if want is None:
        return True
    mesh = d.node(node)["mesh"]
    return str(mesh) == want or d.g.items("meshes")[mesh].get("name") == want


def _mesh_file(d: Doc, name: str, nodes: list[int], info: dict, used: set[int]) -> RdmFile:
    like = d.opts.like
    source = info.get("source", like.source if like else "")
    exporter = info.get("exporter", like.exporter if like else "")
    rdm = RdmFile(False, source, exporter)
    materials = _materials(d, name, nodes)
    rdm.materials = [m for _i, m in materials]
    skeletons = gltfskin.skeletons(d, name, nodes)
    rdm.skeletons = [s.skeleton for s in skeletons]
    names: Counter[str] = Counter()
    for n in nodes:
        used.add(n)
        mesh = gltfmesh.mesh(d, n, [i for i, _m in materials], skeletons)
        names[mesh.name] += 1
        rdm.meshes.append(mesh)
    used.update(j for s in skeletons for j in s.nodes)
    if any(c > 1 for c in names.values()):
        d.notes.append(f"{name}: two meshes share a name")
    return rdm


def _first_use(d: Doc, nodes: list[int]) -> list[int]:
    out: list[int] = []
    for n in nodes:
        for k, prim in enumerate(d.g.items("meshes")[d.node(n)["mesh"]].get("primitives", [])):
            if "material" not in prim:
                raise GltfImportError(
                    f"mesh {d.name(n)!r}, primitive {k}: no material (an RDM submesh names one: assign one in the "
                    "modelling tool)"
                )
            if prim["material"] not in out:
                out.append(prim["material"])
    return out


def _materials(d: Doc, file: str, nodes: list[int]) -> list[tuple[int, Material]]:
    """(glTF material, RDM material) in the file's order: the tagged order, else ``material_order`` or the ``like``
    file's names, else the order of first use. A primitive without a material is refused: a submesh names one."""
    first_use = _first_use(d, nodes)
    mats = d.g.items("materials")
    tagged = [i for i, m in enumerate(mats) if tags(m).get("file") == file and isinstance(tags(m).get("index"), int)]
    order = sorted(tagged, key=lambda i: tags(mats[i])["index"]) + [i for i in first_use if i not in tagged]
    wanted = list(d.opts.material_order) or ([m.name for m in d.opts.like.materials] if d.opts.like else [])
    if not tagged and wanted:
        rank = {name: k for k, name in enumerate(wanted)}
        order = sorted(first_use, key=lambda i: (rank.get(mats[i].get("name", ""), len(rank)), first_use.index(i)))
    out = []
    for i in order:
        m = mats[i]
        texture = tags(m).get("texture")
        if texture is None and d.opts.like:
            texture = next((x.texture for x in d.opts.like.materials if x.name == m.get("name", "")), "")
        out.append((i, Material(m.get("name", ""), texture or "")))
    return out


def _foreign(d: Doc, used: set[int]) -> None:
    """Note every node of the scene the files did not take: cameras, lights, empties, an armature object."""
    for i in d.walk(d.scene.get("nodes", [])):
        if i in used or tags(d.node(i)).get("helper"):
            continue
        node = d.node(i)
        if "camera" in node:
            kind = "a camera"
        elif "KHR_lights_punctual" in node.get("extensions", {}):
            kind = "a light"
        elif "mesh" in node:
            kind = "a mesh left out (--mesh)"
        else:
            kind = "an empty or the armature object"
        d.notes.append(f"dropped node {d.name(i)!r}: {kind}, not part of an RDM file")
