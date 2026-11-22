"""Models only the source game or region has (rocks, cliffs, ground decorations), kept and drawn with the target's own
ground textures, so they fit the new ground: the option of an island port (``Request.reskin``, ``island port
--reskin``, the Converter's "Reskin to match the target game"). Rules: docs/island-regions.md "Reskinning".

``plan`` is the entry: it writes, for each prop file a port would remove or keep as it is, a model file of the target
game with the target region's ground material's maps, and returns the files and the prop table entries. Only
ground models are reskinned (``family_of``); a tree or a bush keeps its leaves and stays with the prop table."""

from __future__ import annotations

import re
import struct
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from ..islands import regiontables
from ..models import fileresolve
from ..suite import features
from .othermaterials import imported_path

ROCK_WORDS = ("rock", "cliff", "boulder", "stone", "pebble", "mountain")
ROCK_SHAPES = ("cliff", "mountain", "pebble", "skirt")
STATIC_FORMAT = "P4h_N4b_G4b_B4b_T2h"  # the layout both games draw a static mesh in
RESKIN_FOLDER = "data/reskin"  # below it, per source game, the reskinned files keep the path they had
DIFFUSE_FIELDS = ("cModelDiffTex", "c2ndModelDiffTex", "cPropDiffuseTex", "cPropTerrainDiffuseTex")
NORMAL_FIELDS = ("cModelNormalTex", "c2ndModelNormalTex", "cPropNormalTex", "cPropTerrainNormalTex")
HEIGHT_FIELDS = ("cHeightMap",)
MAPS = {"diffuse": "DiffuseFileName", "normal": "NormalFileName", "height": "HeightFileName"}
LODS = 5


@dataclass(frozen=True)
class SLook:
    """The target's ground material a model is drawn with: its file and its three maps (as the material names them)."""

    material: str
    diffuse: str
    normal: str
    height: str


@dataclass
class SReskinned:
    files: dict[str, bytes] = field(default_factory=dict)  # game path -> bytes, for the target game's mod
    table: dict[str, str] = field(default_factory=dict)  # source prop file (normalised) -> the target game's new one
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (prop file, why it was left to the prop table)

    @property
    def count(self) -> int:
        return len(self.table)


def family_of(path: str) -> str | None:
    """The ground family a prop file's model is part of, None for one that is not (trees, bushes, grass, water
    plants and units keep their own textures). A rock or cliff is rock, a mud decal soil."""
    layer, shape = regiontables.prop_kind(path)
    low = regiontables.norm(path)
    if layer in ("trees", "bushes", "grass", "marshveg", "water", "units", "portrait", "quay"):
        return None
    if layer == "mud_decal":
        return "soil"
    if layer == "rocks" or shape in ROCK_SHAPES or any(word in low for word in ROCK_WORDS):
        return "rock"
    return None


def look_of(material_text: str, file: str) -> SLook | None:
    """A ground material file's maps; None when it names no diffuse map."""
    found = {}
    for key, tag in MAPS.items():
        match = re.search(rf"<{tag}>([^<]*)</(?:{tag})?>", material_text)
        found[key] = match[1].replace("\\", "/") if match else ""
    return SLook(file, **found) if found["diffuse"] else None


def candidates(target_text: str, family: str) -> list[regiontables.SMaterial]:
    """The target set's materials of a family (its fallback families when it has none), in the set's list order."""
    return regiontables.family_candidates(
        family, [material for material in regiontables.set_materials(target_text) if material.file]
    )


def pick(options: list, rank: int):
    """The ``k``-th prop of a kind takes the ``k``-th option round (the rule of the prop table): the most used prop
    gets the first material, the next the second, and so on [I: our own rule, there is no 2070 or studio rule]."""
    return options[rank % len(options)] if options else None


def retexture(text: str, look: SLook) -> str:
    """A prop or model file with its diffuse, normal and height maps the material's; the maps that name an engine
    default, and every other map (metal, ambient occlusion, splat, wind), stay."""

    def swap(names: tuple[str, ...], value: str, body: str) -> str:
        for name in names:
            body = re.sub(
                rf"<{name}>((?:(?!default)[^<])+)(</{name}>|</>)",
                lambda m, n=name, v=value: f"<{n}>{v}{m[2]}" if v else m[0],
                body,
            )
        return body

    return swap(HEIGHT_FIELDS, look.height, swap(NORMAL_FIELDS, look.normal, swap(DIFFUSE_FIELDS, look.diffuse, text)))


def moved(path: str, game: str) -> str:
    """Where a reskinned file lives in the mod: ``data/reskin/<game number>/<its own path below data/>``."""
    return f"{RESKIN_FOLDER}/{game.removeprefix('anno')}/{regiontables.norm(path).removeprefix('data/')}"


def _referenced(text: str) -> list[str]:
    """The game files a model file names (meshes, nested models), as written."""
    return sorted(set(re.findall(r"data[/\\][^<>\"\s|]+?\.(?:rdm|fbx|cfg|prp)", text, re.I)))


def _copy_meshes(read: Callable[[str], bytes], exists: Callable[[str], bool], mesh: str, game: str, files: dict) -> str:
    """A mesh of the source game and its levels copied below ``othermaterials.imported_path``; returns the new path."""
    found = fileresolve.mesh_file(mesh, exists)
    if not found:
        return ""
    new = imported_path(game, found)
    files[new] = read(found)
    stem = re.sub(r"_lod\d+\.rdm$|\.rdm$", "", found)
    for lod in range(1, LODS):
        level = f"{stem}_lod{lod}.rdm"
        if exists(level):
            files[imported_path(game, level)] = read(level)
    return new


def cfg_from_prop(text: str, mesh: str, raw_mesh: bytes, look: SLook, levels: int) -> str | None:
    """117's model config for an 1800 ``.prp`` (its layout is in the file), by the extension that writes them
    (``modelreskin.model_cfg``); None where there is none or the layout is not drawn."""
    layout = re.search(r"<VertexFormat>([^<]*)</", text)
    hook = next(iter(features.extensions(features.MODEL_OPTIONS).values()), None)  # the writer of 117's configs
    return hook.model_cfg(raw_mesh, layout[1] if layout else "", mesh, look, levels) if hook else None


def model_look(read: Callable[[str], bytes], set_text: str, name: str) -> tuple[SLook | None, str]:
    """The look a single model ``name`` (a file name) gets from a game's material set (``set_text``): the first material
    of its ground family, ``read`` giving the material's file; and why there is none ("": there is one)."""
    family = family_of(name)
    if family is None:
        return None, "it is not a ground model (a rock, cliff or decal)"
    options = candidates(set_text, family)
    found = look_of(read(options[0].file).decode("utf-8", "ignore"), options[0].file) if options else None
    return found, "" if found else "the material set has no such material"


def prop_from_cfg(template: str, mesh: str, look: SLook) -> str:
    """1800's ``.prp`` for a 117 model: a vanilla prop of the target region (``template``) with the model's mesh and the
    look's maps."""
    swapped = re.sub(r"<MeshFileName>[^<]*</>", f"<MeshFileName>{mesh}</>", template, count=1)
    return retexture(swapped, look)


def _first_model(text: str) -> tuple[str, str]:
    """The mesh name and vertex layout of the first MODEL block of a 117 config."""
    block = text.split("<ConfigType>MODEL</ConfigType>", 1)[-1]
    mesh = re.search(r"<FileName>([^<]*)</FileName>", block)
    layout = re.search(r"<VertexFormat>([^<]*)</VertexFormat>", block)
    return (mesh[1] if mesh else "", layout[1] if layout else "")


@dataclass(frozen=True)
class SSide:
    """One game of a port as the reskin reads it: its key, ``read`` and ``exists`` of its files, its prop template."""

    key: str
    read: Callable[[str], bytes]
    exists: Callable[[str], bool]


def plan(
    source_side: SSide,
    target_side: SSide,
    wanted: Counter,
    target_set: str,
    template: Callable[[], str] = lambda: "",
) -> SReskinned:
    """Reskin every prop file in ``wanted`` (normalised file -> instances; the ones a port would remove or keep as they
    are) that is part of the ground: its model written for the target game with the target set's materials, the
    material by ``pick``. ``template``: a vanilla ``.prp`` of the target region (1800 as target). What cannot be done
    is in ``skipped`` with its reason, and stays with the prop table."""
    out = SReskinned()
    groups: dict[str, list[str]] = {}
    for key, _n in sorted(wanted.items(), key=lambda kn: (-kn[1], kn[0])):
        if (family := family_of(key)) is None:
            continue
        groups.setdefault(family, []).append(key)
    for family, keys in groups.items():
        options = candidates(target_set, family)
        for rank, key in enumerate(keys):
            material = pick(options, rank)
            look = (
                look_of(target_side.read(material.file).decode("utf-8", "ignore"), material.file)
                if material is not None
                else None
            )
            why = (
                _one(source_side, target_side, key, look, out, template)
                if look is not None
                else "the target set has no such material"
            )
            if why:
                out.skipped.append((key, why))
    return out


def _one(source_side: SSide, target_side: SSide, key: str, look: SLook, out: SReskinned, template) -> str:
    """Reskin one prop file into ``out``; returns why it could not be (""): the reason names the layout or file."""
    try:
        text = source_side.read(key).decode("utf-8", "ignore")
    except (KeyError, OSError):
        return "the source game has no such file"
    if source_side.key == target_side.key:
        path = moved(key, source_side.key)
        out.files[path] = retexture(text, look).encode("utf-8")
        out.table[key] = path
        return ""
    if source_side.key == "anno1800":  # a .prp into 117's config
        mesh = re.search(r"<MeshFileName>([^<]*)</", text)
        copies: dict[str, bytes] = {}
        new = _copy_meshes(source_side.read, source_side.exists, mesh[1], source_side.key, copies) if mesh else ""
        if not new:
            return "its mesh is not in the source game"
        layout = re.search(r"<VertexFormat>([^<]*)</", text)
        if not features.extensions(features.MODEL_OPTIONS):
            return "this edition does not write 117's model configs"
        cfg = cfg_from_prop(text, new, copies[new], look, 1 + sum(1 for mesh_path in copies if mesh_path != new))
        if cfg is None:
            return f"its mesh layout is {layout[1] if layout else 'not given'}, not {STATIC_FORMAT}, which 117 draws"
        path = moved(key, source_side.key).rsplit(".", 1)[0] + ".cfg"
        out.files.update(copies)
        out.files[path] = cfg.encode("utf-8")
        out.table[key] = path
        return ""
    name, layout = _first_model(text)  # a 117 config into 1800's .prp
    if layout != STATIC_FORMAT:
        return f"its mesh layout is {layout or 'not given'}, not {STATIC_FORMAT}"
    prototype = template()
    if not prototype:
        return "the target region has no vanilla rock prop to copy the file from"
    copies = {}
    new = _copy_meshes(source_side.read, source_side.exists, name, source_side.key, copies)
    if not new:
        return "its mesh is not in the source game"
    path = moved(key, source_side.key).rsplit(".", 1)[0] + ".prp"
    out.files.update(copies)
    out.files[path] = prop_from_cfg(prototype, new, look).encode("utf-8")
    out.table[key] = path
    return ""


def wanted_props(
    rd, table: dict, own_only: set[str], exists: Callable[[str], bool] | None, other_game: bool
) -> Counter:
    """The prop files of an island (``rd``: its render data) a port would remove or leave in the source's own look, with
    their instance counts: removed by the table (None) or by the missing-file test between games, and, within a game,
    the ones only the source region's islands place that no table entry takes."""
    names = regiontables.prop_files(rd)
    used: Counter = Counter()
    instances = rd.path("PropGrid/Instances")
    for prop in instances.children if instances is not None else []:
        index = prop.find("Index") if hasattr(prop, "find") else None
        if index is not None and (i := struct.unpack_from("<I", index.data)[0]) < len(names):
            used[regiontables.norm(names[i])] += 1
    found: Counter = Counter()
    for key, instance_count in used.items():
        if key in table:
            if table[key] is None:
                found[key] = instance_count
        elif other_game and exists is not None and not exists(key):
            found[key] = instance_count
        elif not other_game and key in own_only:
            found[key] = instance_count
    return found


NOT_RESKINNED = "not reskinned"


def for_port(source_side, target_side, rd, tables, own, target, target_set: str, ledger=None) -> SReskinned:
    """The reskin of one port (``source_side`` / ``target_side``: ``islandconvert.CGameSide``; ``rd``: the source island's render data;
    ``own`` / ``target``: the two regions): ``tables.props`` takes the new files, the result carries them for the mod.
    What cannot be reskinned is named in ``ledger`` with the reason."""
    other = source_side.m_key != target_side.m_key
    own_only = set(source_side.inventory(own).props) - set(target_side.inventory(target).props)
    wanted = wanted_props(rd, tables.props, own_only, target_side.m_files.exists if other else None, other)

    def template() -> str:
        rocks = (
            prop
            for prop in target_side.inventory(target).props
            if prop.endswith(".prp") and regiontables.prop_kind(prop)[0] == "rocks"
        )
        return target_side.text(next(iter(sorted(rocks)), ""))

    found = plan(
        SSide(source_side.m_key, source_side.read, source_side.m_files.exists),
        SSide(target_side.m_key, target_side.read, target_side.m_files.exists),
        wanted,
        target_set,
        template,
    )
    tables.props.update(found.table)
    for key, why in found.skipped if ledger is not None else []:
        ledger.add("prop", f"{key.split('data/', 1)[-1]} (reskin: {why})", NOT_RESKINNED, wanted[key])
    return found
