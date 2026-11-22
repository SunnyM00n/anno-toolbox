"""Granny ``.gr2`` models (Anno 1404, 1701) as ``.rdm`` files for the RDM games (2070, 2205, 1800, 117), no glTF between.

``convert`` takes the RDM records ``grannygltf.granny_to_rdm`` makes of a ``.gr2`` and writes them with the RDM writer
in the target game's layouts (``TARGETS``): the standard tangent-frame layout, vertex colours where the game ships a
layout for them, cloth for pinned meshes, a skin as the game reads it, each animation as a file of its own. Lengths go
from the source's tile (1701: 32 units) to the target's. docs/rdm-gltf.md "Granny to RDM".
"""

from __future__ import annotations

import re
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

from ..formats import grannyanim, grannyread, rdmwrite
from ..formats.grannyglb import meshes_of
from ..formats.rdmlayout import parse
from ..formats.rdmread import SAnimation, SMesh, SRdmFile, read_rdm
from ..models import rdmnative, tangents
from .grannygltf import SRecordOptions, granny_to_rdm

STD = "P4h_N4b_G4b_B4b_T2h"  # every game's standard static layout
BONES16 = "I4b_I4b_I4b_I4b_W4b_W4b_W4b_W4b"  # 2205's and 1800's skin of more than one bone a vertex
CLOTH_2070 = "P3f_T2f_C4c"
CLOTH_117 = "P3f_N3f_G3f_B3f_T2f_C4c"  # also 1800's
SOURCE_TILE = {"anno1404": 1.0, "anno1701": 32.0}  # units a tile in the source files (granny.md "What the games ship")
TARGET_TILE = 1.0  # one unit a tile in every RDM game (2070, 117: engine-placement; 1800, 2205 taken as the same [I])
PIN_BIT = 0x80  # a cloth vertex is pinned when its colour's blue byte has the top bit (granny.md "Cloth pins")
MIN_SHARE = 0.75  # the share of a clip's tracks that must name the model's bones for the clip to be its
WHITE = (255, 255, 255, 255)  # a vertex colour that says nothing


@dataclass(frozen=True)
class STarget:
    """What a game ships, so what is written for it: the static layout with vertex colours (``""``: none shipped, the
    colours are left out), the cloth family (``"2070"``: ``P3f_T2f_C4c``, ``"117"``: with a tangent frame, ``""``:
    none shipped) and the skin (``"weights"``: ``I4b_W4b``; ``"bones16"``: ``I4b`` alone when no vertex has two bones,
    else the 16-bone layout)."""

    key: str
    name: str
    colour: str = ""
    cloth: str = ""
    skin: str = "weights"


TARGETS = {
    "anno2070": STarget("anno2070", "Anno 2070", STD + "_C4c", "2070"),
    "anno2205": STarget("anno2205", "Anno 2205", skin="bones16"),
    "anno1800": STarget("anno1800", "Anno 1800", cloth="117", skin="bones16"),
    "anno117": STarget("anno117", "Anno 117: Pax Romana", STD + "_C4b", "117"),
}  # layouts: formats/rdmlayout.KNOWN (what each game ships)


@dataclass(frozen=True)
class SOptions:
    """``target`` a key of ``TARGETS``; ``source`` the source game (``SOURCE_TILE``), "" to read it off the file;
    ``scale`` divides every length instead of the tiles' ratio; ``axes``: "same" (both are y up, back = -z) or "turn"
    (half a turn about y)."""

    target: str = "anno117"
    source: str = ""
    scale: float = 0.0
    axes: str = "same"


@dataclass
class SAnimationFile:
    """The animations of one other ``.gr2`` (``name``: its stem) for the mesh."""

    name: str
    animations: list[grannyanim.SAnimation]


@dataclass
class SMade:
    files: dict[str, bytes] = field(default_factory=dict)  # output name -> bytes
    notes: list[str] = field(default_factory=list)
    kind: str = ""  # static, skinned, cloth or animation
    layout: str = ""  # the layout of the mesh file's meshes


def source_game(data: bytes) -> str:
    """The game a ``.gr2`` came from, by the units its art tool info names (1701: 39.37 a metre, 1404: 1)."""
    art_tool_info = grannyread.CGrannyFile(data).root().get("ArtToolInfo")
    units = (
        float(art_tool_info["UnitsPerMeter"]) if art_tool_info is not None and art_tool_info["UnitsPerMeter"] else 1.0
    )
    return "anno1701" if units > 10 else "anno1404"


def bone_names(data: bytes) -> set[str]:
    """The bones of every skeleton of a ``.gr2``."""
    return {
        bone.name
        for skeleton in grannyanim.read_skeletons(grannyread.CGrannyFile(data).root())
        for bone in skeleton.bones
    }


def animation_files(candidates: Iterable[tuple[str, bytes]], bones: set[str]) -> list[SAnimationFile]:
    """From ``(name, bytes)`` of ``.gr2`` files, the animations of which at least ``MIN_SHARE`` of the tracks name bones
    in ``bones`` (a clip is made for the model's biggest skeleton, and the levels of detail lack a few bones, whose tracks
    are left out), grouped by file; files that do not read are left out."""
    found = []
    for name, data in candidates:
        try:
            read = grannyanim.read_animations(grannyread.CGrannyFile(data).root())
        except grannyread.CGrannyError:
            continue
        moving = [animation for animation in read if _share(animation, bones) >= MIN_SHARE]
        if moving:
            found.append(SAnimationFile(name, moving))
    return found


def _share(animation: grannyanim.SAnimation, bones: set[str]) -> float:
    """The most of one track group's tracks that name bones of ``bones`` (1404's clips have a group per level of
    detail, each naming that level's bones)."""
    return max(
        (
            len({track.name for track in group.tracks} & bones) / len(group.tracks)
            for group in animation.groups
            if group.tracks
        ),
        default=0.0,
    )


def _clean(name: str) -> str:
    """The file name of a studio path, without its suffix and nothing a file system refuses."""
    stem = re.split(r"[\\/]", name)[-1].rsplit(".", 1)[0]
    return re.sub(r"[^\w.-]", "_", stem) or "animation"


def _influences(rdm: SRdmFile) -> int:
    """The most bones any vertex of the file's skinned meshes weighs (0 without a skin)."""
    rows = (mesh.column(6) or [] for mesh in rdm.meshes if mesh.skeleton is not None)
    return max((sum(1 for weight in row if weight) for weights in rows for row in weights), default=0)


def _cloth(mesh: SMesh) -> bool:
    """Whether a mesh's colours pin some of its vertices and leave others free: a cloth (all pinned is a stiff mesh)."""
    pins = [bool(colour[0] & PIN_BIT) for colour in mesh.column(5) or []]
    return any(pins) and not all(pins)


def _skin(target: STarget, influences: int) -> tuple[str, int]:
    """The layout a skin is written in and the vertex description's word."""
    if target.skin == "weights":
        return f"{STD}_I4b_W4b", 4 if influences >= 4 else 3
    return (f"{STD}_I4b", 1) if influences <= 1 else (f"{STD}_{BONES16}", 16)


def _records(
    data: bytes, extras: list[grannyanim.SAnimation], scale: float, options: SOptions, target: STarget
) -> SRdmFile:
    """The file as float RDM records; where the target needs the 16-bone layout and a vertex weighs two bones, with it."""
    records = SRecordOptions(colours=True, turn=options.axes == "turn")
    rdm = granny_to_rdm(data, extras, scale, records)
    if target.skin == "bones16" and _influences(rdm) > 1:
        rdm = granny_to_rdm(data, extras, scale, replace(records, skin=BONES16))
    return rdm


def _cloth_file(rdm: SRdmFile, ids: list[int], target: STarget) -> bytes:
    """The cloth meshes ``ids`` as the target's cloth file: position, UV and the pin colour (2070), or with the
    source's normal and a tangent frame made from the triangles and UVs (117, 1800; stored negated as theirs are)."""
    wide = target.cloth == "117"
    meshes = []
    for i in ids:
        mesh = rdm.meshes[i]
        positions, normals, uvs, colours = mesh.positions(), mesh.column(1), mesh.column(4), mesh.column(5)
        frame = tangents.frames(positions, uvs, mesh.indices, normals)
        rows = []
        for k, (position, uv, colour) in enumerate(zip(positions, uvs, colours, strict=True)):
            normal, tangent, bitangent = frame[0][k], tangents.stored(frame[1][k]), tangents.stored(frame[2][k])
            rows.append(
                struct.pack("<3f3f3f3f2f4B", *position, *normal, *tangent, *bitangent, *uv, *colour)
                if wide
                else struct.pack("<3f2f4B", *position, *uv, *colour)
            )
        layout = rdmwrite.FORMAT_CLOTH_117 if wide else parse(CLOTH_2070)
        submeshes = [
            rdmwrite.SRdmSubmesh(submesh.first_index, submesh.index_count, submesh.material)
            for submesh in mesh.submeshes
        ]
        meshes.append(rdmwrite.SRdmMeshData(mesh.name, b"".join(rows), len(rows[0]), layout, mesh.indices, [x.name for x in rdm.materials],
                                    submeshes, mesh.bbox, rdm.source, rdm.exporter))  # fmt: skip
    return rdmwrite.write_rdm(*meshes)


def _mesh_file(rdm: SRdmFile, parts: list[int], target: STarget, skinned: bool, out: SMade) -> bytes:
    """The mesh file of the meshes ``parts``; sets ``out.layout`` and adds what the relayout filled or dropped."""
    only = replace(rdm, meshes=[rdm.meshes[i] for i in parts])
    if skinned:
        out.layout, word = _skin(target, _influences(only))
        done = rdmnative.convert_skinned(only, out.layout, word)
    else:
        coloured = any(colour != WHITE for i in parts for colour in rdm.meshes[i].column(5) or [])
        out.layout = target.colour if coloured and target.colour else STD
        if coloured and not target.colour:
            out.notes.append(f"vertex colours left out: {target.name} ships no layout for them")
        done = rdmnative.convert_static(replace(only, skeletons=[], animations=[]), layout=out.layout)
    for what, lines in (
        ("filled", done.notes.m_filled),
        ("approximated", done.notes.m_approximated),
        ("dropped", done.notes.m_dropped),
    ):
        out.notes += list(dict.fromkeys(f"{what}: {x}" for x in lines))
    names = [mesh.name for mesh in only.meshes]
    if len(set(names)) != len(names):
        out.notes.append("two meshes share a name (the source's); an animation entry names its model by it")
    return _boxed(done.data)


def _boxed(data: bytes) -> bytes:
    """The file with each mesh's stored box taken from the positions as written (half floats), so the box holds
    every vertex the game draws."""
    rdm = read_rdm(data)
    for mesh in rdm.meshes:
        mesh.bbox = mesh.computed_bounds() or mesh.bbox
    return rdmwrite.write_file(rdm)


def _animation_files(rdm: SRdmFile, own: int, extra: Sequence[SAnimationFile], stem: str, out: SMade) -> None:
    """One animation file per animation: the file's own (``<stem>_anim``; ``<stem>`` without a mesh), then those of
    ``extra`` (named as their ``.gr2``); a file with more than one animation adds each one's name."""
    animations = iter(rdm.animations)
    groups: list[tuple[str, list[SAnimation]]] = [
        (f"{stem}_anim" if rdm.meshes else stem, [next(animations) for _ in range(own)])
    ]
    groups += [(animation_file.name, [next(animations) for _ in animation_file.animations]) for animation_file in extra]
    for name, found in groups:
        for animation in found:
            if not any(
                track.keys for track in animation.tracks
            ):  # a clip of nothing the model has (a cloth's) moves nothing
                out.notes.append(f"animation {animation.name!r} moves no bone of the model: not written")
                continue
            entry = replace(animation, name=_clean(animation.name))
            out.files[f"{name}.rdm" if len(found) == 1 else f"{name}_{entry.name}.rdm"] = rdmwrite.write_animation_rdm(
                [entry], rdm.source, rdm.exporter
            )
    if any(groups_found for _name, groups_found in groups):
        out.notes.append("animations keep their frame rate and times (seconds); the games' own mostly ship 15 a second")


def convert(data: bytes, stem: str, options: SOptions = SOptions(), extra: Sequence[SAnimationFile] = ()) -> SMade:  # noqa: B008
    """A ``.gr2`` as RDM files named from ``stem``: the mesh file (``<stem>.rdm``; its cloth in ``<stem>_cloth.rdm``)
    and an animation file for each animation of the file and of ``extra``. A file with animations or a model of
    bones is written as a skin, else static. Raises ``CGrannyError`` for a file that does not read, ``ValueError`` for
    a model the target cannot hold."""
    if options.target not in TARGETS:
        raise ValueError(f"target {options.target!r}: one of {', '.join(TARGETS)}")
    if options.axes not in ("same", "turn"):
        raise ValueError(f"axes {options.axes!r}: same or turn")
    target, game = TARGETS[options.target], options.source or source_game(data)
    scale = options.scale or SOURCE_TILE[game] / TARGET_TILE
    out = SMade()
    if scale != 1.0:
        out.notes.append(f"lengths divided by {scale:g} ({game}: {SOURCE_TILE[game]:g} units a tile, {target.name}: 1)")
    rdm = _records(
        data, [animation for animation_file in extra for animation in animation_file.animations], scale, options, target
    )
    own = len(rdm.animations) - sum(len(animation_file.animations) for animation_file in extra)
    granny = meshes_of(grannyread.CGrannyFile(data).root())
    pinned = [i for i, mesh in enumerate(rdm.meshes) if _cloth(mesh)]
    if pinned and not target.cloth:  # no cloth layout shipped: the pinned meshes are drawn as plain ones
        out.notes.append(f"{len(pinned)} cloth mesh(es) written as plain meshes: {target.name} ships no cloth layout")
        pinned = []
    plain = [i for i in range(len(rdm.meshes)) if i not in pinned]
    if plain:
        bound = any(granny[i].bone_indices is not None for i in plain)
        skinned = bool(rdm.skeletons) and (bool(rdm.animations) or bound)
        out.kind = "skinned" if skinned else "static"
        out.files[f"{stem}.rdm"] = _mesh_file(rdm, plain, target, skinned, out)
    if pinned:
        out.files[f"{stem}_cloth.rdm" if plain else f"{stem}.rdm"] = _cloth_file(rdm, pinned, target)
        out.kind = out.kind or "cloth"
        out.layout = out.layout or (CLOTH_2070 if target.cloth == "2070" else CLOTH_117)
    _animation_files(rdm, own, extra, stem, out)
    if not out.files:
        raise ValueError("no mesh and no animation to write")
    out.kind = out.kind or "animation"
    if rdm.materials:
        out.notes.append(
            f"materials by name: {', '.join(x.name for x in rdm.materials)} (the model config picks the textures)"
        )
    return out
