"""Models exported as glTF: an ``.rdm`` through the RDM toolchain (docs/rdm-gltf.md), a Granny ``.gr2`` the same way
once it is RDM records (``rdm.grannygltf``: meshes, skeletons, its animations and those of the animation folder), a
model config (``.cfg``) as the meshes it names.

``.glb`` is the default form. An ``.rdm`` keeps exactly what the file holds; the options choose the axes, ``strict``
values, the LOD files beside it, the textures as PNG and the animation files of a folder that move it. A config
exports each ``.rdm`` it names as a glTF of its own. A mesh that cannot be exported is named in the notes.
docs/exporting.md.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

from ..formats import gltf as gltf_format
from ..formats import rdmread
from ..rdm import images, rdmgltf
from ..sources.overlay import normalize
from .run import Job, Made

RDM_REF = re.compile(r"[\w\-./\\ ]+?\.rdm", re.IGNORECASE)
LOD_LEVELS = 10  # a mesh named at any level stands for the chain of up to this many


def _form(job: Job) -> str:
    return "glb" if "glb" in job.formats else "gltf"


def _lod_chain(job: Job, path: str) -> list[str]:
    from ..models import configtree  # numpy: the light install exports the plain kinds without it

    return configtree.lod_files(path, job.source.exists, any_level=True, count=LOD_LEVELS)


def _folder_files(job: Job, suffix: str) -> list[str]:
    """The files of ``options.anims_folder`` that end in ``suffix``, the exported file left out (the source must
    list a folder: ``under``)."""
    under = getattr(job.source, "under", None)
    if not job.options.anims_folder or under is None:
        return []
    folder = normalize(job.options.anims_folder).rstrip("/") + "/"
    return [p for p in under(folder) if p.endswith(suffix) and p != job.path]


def _moves_only(tracks: set[str], joints: set[str]) -> bool:
    """Whether an animation's keyed tracks all name joints of the mesh (and there is one at least)."""
    return bool(tracks) and tracks <= joints


def _animations(job: Job, rdm: rdmread.RdmFile) -> list[rdmgltf.Source]:
    """The animation files of the folder some entry of which moves only joints of the mesh."""
    joints = {j.name for s in rdm.skeletons for j in s.joints}
    out = []
    for path in _folder_files(job, ".rdm"):
        try:
            found = rdmread.read_rdm(job.source.read(path))
        except (ValueError, rdmread.RdmReadError):
            continue
        if any(_moves_only({t.joint for t in a.tracks if t.keys}, joints) for a in found.animations):
            out.append(rdmgltf.Source(PurePosixPath(path).name, found))
    return out


def _textures(job: Job, sources: list[rdmgltf.Source], notes: list[str]) -> list[rdmgltf.Texture]:
    out, seen = [], set()
    for source in sources:
        for material in source.rdm.materials:
            path = normalize(material.texture)
            if not path or path in seen:
                continue
            seen.add(path)
            tries = [path, re.sub(r"\.\w+$", "", path) + "_0.dds", re.sub(r"\.\w+$", "", path) + ".dds"]
            found = next((p for p in tries if job.source.exists(p)), "")
            if not found:
                notes.append(f"texture {path} of material {material.name} is not in the files")
                continue
            try:
                texture, note = images.texture_of(job.source.read(found), found, material.name)
            except ValueError as exc:
                notes.append(str(exc))
                continue
            out.append(texture)
            notes += [note] if note else []
    return out


def rdm_files(job: Job, path: str) -> tuple[dict[str, bytes], list[str]]:
    """The glTF files (name -> bytes) and notes of one ``.rdm`` game path."""
    paths = _lod_chain(job, path) if job.options.lods else [path]
    sources = [rdmgltf.Source(PurePosixPath(p).name, rdmread.read_rdm(job.source.read(p))) for p in paths]
    if not any(s.rdm.meshes for s in sources):
        return {}, [f"{path}: no mesh (an animation file goes along with the mesh it moves: anims_folder)"]
    animations = _animations(job, sources[0].rdm)
    notes: list[str] = []
    textures = _textures(job, sources, notes) if job.options.with_textures else []
    options = rdmgltf.Options(axes=job.options.axes, strict=job.options.strict)
    builder, more = rdmgltf.export(sources, animations, textures, options)
    target = PurePosixPath(path).with_suffix(".glb" if _form(job) == "glb" else ".gltf")
    files = {p.as_posix(): b for p, b in builder.files(target, _form(job)).items()}
    return files, notes + more


def _granny_animations(job: Job, joints: set[str]) -> list:
    """The animations of the folder's ``.gr2`` files that move only joints the mesh has."""
    from ..formats import grannyanim, grannyread

    out = []
    for path in _folder_files(job, ".gr2"):
        try:
            found = grannyanim.read_animations(grannyread.GrannyFile(job.source.read(path)).root())
        except (grannyread.GrannyError, KeyError):
            continue
        out += [a for a in found if _moves_only({t.name for g in a.groups for t in g.tracks}, joints)]
    return out


def granny_files(job: Job) -> tuple[dict[str, bytes], list[str]]:
    """The glTF files and notes of one ``.gr2``: its meshes, skeletons and animations through the RDM toolchain, in
    1404's units (1701's divided by 32: one unit a tile)."""
    from ..formats import grannyanim, grannyread
    from ..rdm import grannygltf

    joints = {b.name for s in grannyanim.read_skeletons(grannyread.GrannyFile(job.data).root()) for b in s.bones}
    units = grannygltf.GAME_UNITS.get(getattr(job.source, "game", ""), grannygltf.GrannyOptions())
    rdm = grannygltf.granny_to_rdm(job.data, _granny_animations(job, joints), units.scale)
    if not rdm.meshes and not rdm.animations:
        return {}, [f"{job.path}: no mesh and no animation"]
    name = PurePosixPath(job.path).with_suffix(".rdm").name
    options = rdmgltf.Options(axes=job.options.axes, strict=job.options.strict)
    builder, notes = rdmgltf.export([rdmgltf.Source(name, rdm)], [], [], options)
    target = PurePosixPath(job.path).with_suffix(".glb" if _form(job) == "glb" else ".gltf")
    return {p.as_posix(): b for p, b in builder.files(target, _form(job)).items()}, notes


def export(job: Job) -> Made:
    """glTF of a model file."""
    suffix = PurePosixPath(job.path).suffix.lower()
    made = Made()
    try:
        if suffix == ".rdm":
            made.files, made.notes = rdm_files(job, job.path)
        elif suffix == ".gr2":
            made.files, made.notes = granny_files(job)
        elif suffix in (".cfg", ".mcf", ".ocf"):
            _config(job, made)
        else:
            made.notes.append(f"no glTF form for {suffix} files")
    except (rdmgltf.ExportError, rdmread.RdmReadError, gltf_format.GltfError, ValueError, KeyError) as exc:
        made.notes.append(f"not exported: {exc}")
        made.status = "failed"
    if not made.files and made.status == "written" and not made.notes:
        made.notes.append("nothing to export")
    return made


def _mesh_refs(job: Job) -> list[str]:
    """The path of every mesh a config names: the file its game loads for the name (``mesh_file``: a
    117 config names the ``.fbx``, the game ships the ``.rdm``), else the name as a path. A config that does not
    parse is searched for ``.rdm`` names."""
    from ..models import configtree, fileresolve  # numpy, as above

    text = job.data.decode("utf-8", "replace")
    try:
        names = [n.get("FileName") for n in configtree.read_tree(text).walk()]
    except ET.ParseError:
        names = [m.group(0).strip() for m in RDM_REF.finditer(text)]
    names = [n for n in names if re.search(r"\.(rdm|fbx)$", n.strip(), re.IGNORECASE)]
    return list(dict.fromkeys(fileresolve.mesh_file(n, job.source.exists) or normalize(n, studio=True) for n in names))


def _config(job: Job, made: Made) -> None:
    """Every mesh a config names that the files hold, each as a glTF of its own."""
    refs = _mesh_refs(job)
    exported, skipped = 0, []
    for ref in refs:
        if not job.source.exists(ref):
            skipped.append(ref)
            continue
        try:
            files, notes = rdm_files(job, ref)
        except (rdmgltf.ExportError, rdmread.RdmReadError, ValueError) as exc:
            skipped.append(f"{ref} ({exc})")
            continue
        made.files.update(files)
        made.notes += notes
        exported += bool(files)
    made.notes.append(f"{exported} of {len(refs)} meshes the config names exported as glTF")
    if skipped:
        made.notes.append(
            f"not exported (not in the files, or no mesh): {', '.join(skipped[:8])}"
            + (" ..." if len(skipped) > 8 else "")
        )
