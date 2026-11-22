"""Granny ``.gr2`` files (Anno 1404, 1701) and glTF, both ways through the RDM records the toolchain already moves:
``granny_to_rdm`` makes a ``.gr2``'s meshes, skeletons and animations an ``SRdmFile`` for ``rdmgltf.export``, and
``rdm_to_granny`` writes the RDM files a glTF import gives (``gltfrdm.import_gltf``) as one ``.gr2``
(``formats.grannywrite``); ``gltf_to_granny`` does both steps, ``config_files`` the model config and info file that
place the result. Granny keeps the usual quaternion, RDM its conjugate; both sit in the game's own axes, so the glTF
axes option works as for an ``.rdm``. ``scale`` multiplies every length on the way into Granny (1701 counts 32 units
a tile). docs/rdm-gltf.md "Granny models".
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from ..formats import grannyanim, grannyread, grannywrite
from ..formats.grannyglb import meshes_of
from ..formats.isd2070 import FIXED
from ..formats.rdmlayout import encode, parse, weight_bytes
from ..formats.rdmread import SAnimation, SMaterial, SMesh, SRdmFile, SSkeleton, SSubmesh
from ..models.grannyrig import best_skeleton, granny_animation, granny_skeleton, granny_skin
from ..models.meshxform import qconj
from ..models.rdmmesh import mesh_data
from ..models.skinning import matrix_quat, rigid
from ..models.tangents import frames

STATIC, SKINNED = "P3f_N3f_T2f", "P3f_N3f_T2f_I4b_W4b"  # the RDM layouts a Granny mesh becomes


@dataclass(frozen=True)
class SRecordOptions:
    """What ``granny_to_rdm`` makes beyond the glTF export's needs: ``colours`` the vertex colours as a ``C4c``
    element of an unskinned mesh (bytes B, G, R, A, as 2070 stores them), ``skin`` the skin elements' names (a
    second and later ``I4b`` or ``W4b`` hold zeros: 2205's 16-bone layout), ``turn`` the model turned half a turn
    about y (x and z negated; a proper rotation, so the skins stay exact)."""

    colours: bool = False
    skin: str = "I4b_W4b"
    turn: bool = False


# Granny to RDM

_HALF_TURN = np.diag([-1.0, 1.0, -1.0, 1.0])  # x and z negated: a rotation, the winding stays


def _turned(mesh):
    """A Granny mesh with its positions and normals turned half a turn about y."""
    flip = lambda vector: (-vector[0], vector[1], -vector[2])  # noqa: E731
    return replace(
        mesh,
        positions=[flip(position) for position in mesh.positions],
        normals=[flip(normal) for normal in mesh.normals],
    )


def _row(elements, position, normal, uv, colour, joints, weights) -> bytes:
    """One vertex: the value each element takes by its usage (the first ``I4b`` and ``W4b`` the skin's, later ones zero)."""
    seen: Counter[int] = Counter()
    out = []
    for element in elements:
        slot, seen[element.usage] = seen[element.usage], seen[element.usage] + 1
        value = {0: position, 1: normal, 4: uv, 5: colour}.get(element.usage)
        if element.usage in (6, 7):
            value = (joints if element.usage == 7 else weights) if slot == 0 else (0, 0, 0, 0)
        out.append(encode(element, value))
    return b"".join(out)


def _rdm_mesh(
    granny_mesh,
    skin,
    scale: float = 1.0,
    options: SRecordOptions = SRecordOptions(),  # noqa: B008
) -> tuple[SMesh, list[str]]:
    """A Granny mesh (``grannyglb.SGrannyMesh``) as an RDM mesh record of float vertices, skinned by ``skin``, and
    its material names in the order its submeshes number them."""
    coloured = options.colours and skin is None and granny_mesh.colors is not None
    layout = SKINNED.replace("I4b_W4b", options.skin) if skin is not None else STATIC + ("_C4c" if coloured else "")
    elements = parse(layout)
    rows, positions = [], [tuple(component / scale for component in position) for position in granny_mesh.positions]
    for k, (position, normal, uv) in enumerate(zip(positions, granny_mesh.normals, granny_mesh.uvs, strict=True)):
        colour = (
            bytes(round(min(max(granny_mesh.colors[k][c], 0.0), 1.0) * 255) for c in (2, 1, 0, 3)) if coloured else None
        )
        joints, weights = (skin[1][k], weight_bytes(skin[2][k])) if skin is not None else (None, None)
        rows.append(_row(elements, position, normal, uv, colour and tuple(colour), joints, weights))
    indices, submeshes, names = [], [], []
    for name, triangles in granny_mesh.groups:
        if name not in names:
            names.append(name)
        submeshes.append(SSubmesh(len(indices), len(triangles), names.index(name)))
        indices += triangles
    low = tuple(min(position[k] for position in positions) for k in range(3))
    high = tuple(max(position[k] for position in positions) for k in range(3))
    stride = len(rows[0]) if rows else 0
    mesh = SMesh(granny_mesh.name, elements, 4 if skin is not None else 0, 1 if len(granny_mesh.positions) > 0xFFFF else 0,
                len(rows), stride, b"".join(rows), indices, submeshes, skin[0] if skin else None, (*low, *high), 0x5C)  # fmt: skip
    return mesh, names


def _turn_rig(out: SRdmFile) -> None:
    """The skeletons and keys of ``out`` for a model turned half a turn about y: a joint's inverse bind keeps its move
    and turns by the half turn after it (a vertex in bone space stays where it was); a root joint's keys, which
    are its place in the model, are turned by it, a child's stay as they are."""
    for skeleton in out.skeletons:
        for j in skeleton.joints:
            matrix = rigid(j.rotation, j.position) @ _HALF_TURN
            j.rotation = qconj(matrix_quat(matrix[:3, :3]))
    roots = {j.name for skeleton in out.skeletons for j in skeleton.joints if j.parent is None}
    for animation in out.animations:
        for track in (track for track in animation.tracks if track.joint in roots):
            for k in track.keys:
                matrix = _HALF_TURN @ rigid(k.rotation, k.position)
                k.rotation, k.position = qconj(matrix_quat(matrix[:3, :3])), tuple(matrix[:3, 3])


def granny_to_rdm(
    data: bytes,
    animations: Sequence[grannyanim.SAnimation] = (),
    scale: float = 1.0,
    options: SRecordOptions = SRecordOptions(),  # noqa: B008
) -> SRdmFile:
    """A ``.gr2`` as one RDM file: its meshes (float vertices; a skin's bones through its bindings, a rigid mesh as a
    one-bone skin), its skeletons as joints, its own animations and ``animations`` (read from other ``.gr2`` files)
    sampled every time step against the skeleton naming most of their bones; every length divided by ``scale``
    (``rdm_to_granny``'s). Raises ``CGrannyError``."""
    root = grannyread.CGrannyFile(data).root()
    skeletons = grannyanim.read_skeletons(root)
    owners = grannyanim.model_skeletons(root)
    out = SRdmFile(
        False, root.get("FromFileName") or "", "granny", skeletons=[granny_skeleton(skeleton) for skeleton in skeletons]
    )
    materials: list[str] = []
    for granny_mesh in meshes_of(root):
        granny_mesh = _turned(granny_mesh) if options.turn else granny_mesh
        mesh, names = _rdm_mesh(
            granny_mesh, granny_skin(granny_mesh, skeletons, owners.get(granny_mesh.name)), scale, options
        )
        for submesh in mesh.submeshes:  # the file's material numbers: one list over all its meshes
            name = names[submesh.material]
            if name not in materials:
                materials.append(name)
            submesh.material = materials.index(name)
        out.meshes.append(mesh)
    out.materials = [SMaterial(material_name, "") for material_name in materials]
    for animation in [*grannyanim.read_animations(root), *animations]:
        out.animations.append(granny_animation(animation, best_skeleton(animation, skeletons)))
    for joint in (
        j for skeleton in out.skeletons for j in skeleton.joints
    ):  # a uniform scale moves an inverse bind's move
        joint.position = tuple(component / scale for component in joint.position)
    for key in (k for animation in out.animations for track in animation.tracks for k in track.keys):
        key.position = tuple(component / scale for component in key.position)
    if options.turn:
        _turn_rig(out)
    return out


# RDM to Granny


@dataclass(frozen=True)
class SGrannyOptions:
    """``scale`` every length (1701: 32 units a tile), ``units_per_meter`` the art tool info's (1701: 39.37)."""

    scale: float = 1.0
    units_per_meter: float = 1.0


GAME_UNITS = {
    "anno1404": SGrannyOptions(),
    "anno1701": SGrannyOptions(32.0, 39.370079),
}  # granny.md "What the games ship"


def _bones(skeleton: SSkeleton, scale: float) -> list[dict]:
    """RDM joints (inverse binds) as Granny bones: the rest transform in the parent, the inverse world for row
    vectors (the move in the last row)."""
    inverse = [rigid(j.rotation, np.asarray(j.position) * scale) for j in skeleton.joints]
    bind = [np.linalg.inv(matrix) for matrix in inverse]
    out = []
    for k, j in enumerate(skeleton.joints):
        parent = j.parent if j.parent is not None and 0 <= j.parent < k else None
        local = bind[k] if parent is None else np.linalg.inv(bind[parent]) @ bind[k]
        transform = {**grannywrite.IDENTITY_TRANSFORM, "position": tuple(local[:3, 3]),
                     "orientation": matrix_quat(local[:3, :3])}  # fmt: skip
        out.append({"Name": j.name, "ParentIndex": -1 if parent is None else parent, "Transform": transform,
                    "InverseWorldTransform": tuple(inverse[k].T.reshape(16))})  # fmt: skip
    return out


def _spec(mesh: SMesh, rdm: SRdmFile, materials: dict, names: list[str], scale: float) -> grannywrite.SMeshSpec:
    """An RDM mesh as a Granny mesh: its groups by the file's material names, a skin's bones numbered as the
    skeleton's joints (``names``); a rigid mesh binds the one bone ``names`` holds."""
    data = mesh_data(mesh)
    vertices = data.vertices
    groups = []
    for k, triangles in data.groups.items():
        name = rdm.materials[k].name if 0 <= k < len(rdm.materials) else f"material_{k}"
        groups.append(
            (
                materials.setdefault(name, grannywrite.SRec(grannywrite.MATERIAL, {"Name": name})),
                [int(i) for i in triangles],
            )
        )
    spec = grannywrite.SMeshSpec(mesh.name, [tuple(position * scale) for position in vertices[:, :3]], [tuple(normal) for normal in vertices[:, 3:6]],
                       [tuple(uv_pair) for uv_pair in vertices[:, 6:8]], groups, list(names))  # fmt: skip
    indices = [i for _material, triangles in groups for i in triangles]
    _normals, spec.tangents, spec.binormals = frames(spec.positions, spec.uvs, indices, spec.normals)
    if data.skinned:
        spec.bone_indices = [tuple(int(j) for j in row) for row in data.joints]
        spec.bone_weights = [weight_bytes([float(weight) for weight in row]) for row in data.weights]
    return spec


def _track(track, scale: float) -> dict:
    """An RDM track (local keys) as linear Granny curves."""
    times = [k.time for k in track.keys]
    position = [tuple(component * scale for component in k.position) for k in track.keys]
    orientation = [qconj(k.rotation) for k in track.keys]
    return {"Name": track.joint, "PositionCurve": grannywrite.curve(times, position),
            "OrientationCurve": grannywrite.curve(times, orientation, dimension=4)}  # fmt: skip


def _animation(animation: SAnimation, group: str, scale: float) -> grannywrite.SRec:
    tracks = [_track(track, scale) for track in animation.tracks if track.keys]
    step = 1.0 / animation.frame_rate if animation.frame_rate > 0 else 1.0 / 30
    return grannywrite.animation_record(animation.name, animation.duration_ms / 1000.0, step, [(group, tracks)])


def _rigid_model(
    mesh: SMesh, rdm: SRdmFile, materials: dict, scale: float
) -> tuple[grannywrite.SRec, grannywrite.SRec, grannywrite.SRec]:
    """A mesh without a skeleton as a model of its own: one bone at the origin; its skeleton, mesh and model."""
    bone = {"Name": mesh.name, "ParentIndex": -1, "InverseWorldTransform": tuple(np.identity(4).reshape(16))}
    skeleton = grannywrite.skeleton_record(mesh.name, [bone])
    record = grannywrite.mesh_record(_spec(mesh, rdm, materials, [mesh.name], scale))
    return skeleton, record, grannywrite.model_record(mesh.name, skeleton, [record])


def rdm_to_granny(files: list[tuple[str, SRdmFile]], name: str, options: SGrannyOptions | None = None) -> bytes:
    """One ``.gr2`` of RDM files (mesh files first, then animation files): a model per skeleton (a rigid mesh
    without one gets a bone of its own), every animation as a track group of the first model."""
    options = options or SGrannyOptions()
    materials: dict[str, grannywrite.SRec] = {}
    meshes, skeletons, models, animations = [], [], [], []
    stem = name.rsplit(".", 1)[0]
    for _file, rdm in files:
        made = [
            grannywrite.skeleton_record(f"{stem}_{k}", _bones(skeleton, options.scale))
            for k, skeleton in enumerate(rdm.skeletons)
        ]
        joints = [[j.name for j in skeleton.joints] for skeleton in rdm.skeletons]
        bound: dict[int, list] = {}
        for mesh in rdm.meshes:
            if mesh.skeleton is not None and 0 <= mesh.skeleton < len(made):
                spec = _spec(mesh, rdm, materials, joints[mesh.skeleton], options.scale)
                bound.setdefault(mesh.skeleton, []).append(grannywrite.mesh_record(spec))
            else:
                own, record, model = _rigid_model(mesh, rdm, materials, options.scale)
                skeletons.append(own)
                meshes.append(record)
                models.append(model)
        for k, skeleton in enumerate(made):
            skeletons.append(skeleton)
            meshes += bound.get(k, [])
            models.append(grannywrite.model_record(stem if not k else f"{stem}_{k}", skeleton, bound.get(k, [])))
        animations += rdm.animations
    group = models[0].values["Name"] if models else stem
    records = [_animation(animation, group, options.scale) for animation in animations]
    root = grannywrite.root_record(name, options.units_per_meter, Materials=list(materials.values()), Skeletons=skeletons,
                          Meshes=meshes, Models=models, Animations=records)  # fmt: skip
    return grannywrite.write_file(root)


def gltf_to_granny(path, options: SGrannyOptions | None = None, import_options=None) -> tuple[bytes, list[str]]:
    """A glTF file as one ``.gr2`` (its name the glTF's, ``.gr2``) and the import's notes."""
    from ..formats.gltf import read_gltf
    from . import gltfrdm

    path = Path(path)
    imported = gltfrdm.import_gltf(read_gltf(path), path.with_suffix(".rdm").name, import_options)
    return rdm_to_granny(imported.files, path.with_suffix(".gr2").name, options), imported.notes


# The files that place a written .gr2

_MATERIAL_1701 = r"""          <m_Material>
            <m_MCClassType>51</m_MCClassType>
            <m_Name>{name}</m_Name>
            <m_AmbientColor>-1</m_AmbientColor>
            <m_DiffuseColor>-1</m_DiffuseColor>
            <m_SpecularColor>-16777216</m_SpecularColor>
            <m_EmissiveColor>-16777216</m_EmissiveColor>
            <m_Opacity>1.000000</m_Opacity>
            <m_Shader>data\system\fxshaders\mesh\default.fx</m_Shader>
            <m_DiffuseTexture>{texture}</m_DiffuseTexture>
          </m_Material>
"""
_MCF = """<m_MeshConfig>
  <m_MCClassType>0</m_MCClassType>
  <m_MCChilds>
    <m_MCConfig>
      <m_MeshConfig>
        <m_MCClassType>0</m_MCClassType>
        <m_MCChilds>
          <m_MCConfig>
            <m_MeshConfig>
              <m_MCClassType>1</m_MCClassType>
              <m_MeshFilename>{mesh}</m_MeshFilename>
            </m_MeshConfig>
          </m_MCConfig>
{materials}        </m_MCChilds>
      </m_MeshConfig>
    </m_MCConfig>
  </m_MCChilds>
</m_MeshConfig>
"""
_INFO = """<BoundingBox>
  <Min>
    <x>{0}</x>
    <y>{1}</y>
    <z>{2}</z>
  </Min>
  <Max>
    <x>{3}</x>
    <y>{4}</y>
    <z>{5}</z>
  </Max>
</BoundingBox>
<Dummies/>
<AnimationList/>
"""
_MATERIAL_1404 = """<m_Config><m_Name>{name}</m_Name>
<m_DiffuseTexture>{texture}</m_DiffuseTexture>
<m_VertexFormat>p3n3g3b3t2</m_VertexFormat>
<m_DiffuseEnabled>1</m_DiffuseEnabled>
<m_NormalEnabled>0</m_NormalEnabled>
<m_ConfigType>MATERIAL</m_ConfigType>
</m_Config>"""
_CFG = """<m_Config><m_Models><m_Config><m_FileName>{mesh}</m_FileName>
<m_ConfigType>MODEL</m_ConfigType>
<m_Materials>{materials}</m_Materials>
</m_Config></m_Models>
<m_ConfigType>ROOT</m_ConfigType></m_Config>
"""
_IFO = """<Info>
  <BoundingBox>
    <Name>BoundingBox</Name>
    <Position>
      <x>{0}</x>
      <y>{1}</y>
      <z>{2}</z>
    </Position>
    <Rotation>
      <x>0</x>
      <y>0</y>
      <z>0</z>
      <w>4096</w>
      <Direction>0</Direction>
    </Rotation>
    <Extents>
      <x>{3}</x>
      <y>{4}</y>
      <z>{5}</z>
    </Extents>
  </BoundingBox>
</Info>
"""


def config_files(data: bytes, mesh_path: str, game: str) -> dict[str, str]:
    """The model config and info file that place a written ``.gr2`` (``mesh_path``: where it lies in the game's
    files) as the game's own name them, beside it: 1701's ``.mcf`` and ``.info``, 1404's ``.cfg`` and ``.ifo``; each
    material's diffuse texture named ``<mesh>_<material>_diff`` (``.tga`` for 1701, ``.png`` for 1404, which loads
    its ``_0.dds``). docs/rdm-gltf.md "Granny models"."""
    meshes = meshes_of(grannyread.CGrannyFile(data).root())
    points = [point for mesh in meshes for point in mesh.positions] or [(0.0, 0.0, 0.0)]
    low = [min(point[k] for point in points) for k in range(3)]
    high = [max(point[k] for point in points) for k in range(3)]
    names = list(dict.fromkeys(name for mesh in meshes for name, _tris in mesh.groups))
    stem, windows = mesh_path.rsplit(".", 1)[0], mesh_path.replace("/", "\\")
    texture = lambda name, extension: f"{stem}_{name or 'default'}_diff.{extension}".replace("/", "\\")  # noqa: E731
    if game == "anno1701":
        materials = "".join(
            _MATERIAL_1701.format(name=material_name, texture=texture(material_name, "tga")) for material_name in names
        )
        box = [math.floor(value) for value in low] + [math.ceil(value) for value in high]
        return {f"{stem}.mcf": _MCF.format(mesh=windows, materials=materials), f"{stem}.info": _INFO.format(*box)}
    materials = "".join(
        _MATERIAL_1404.format(name=material_name, texture=texture(material_name, "png")) for material_name in names
    )
    fixed = [round((lower + upper) / 2 * FIXED) for lower, upper in zip(low, high, strict=True)]
    fixed += [round((upper - lower) / 2 * FIXED) for lower, upper in zip(low, high, strict=True)]
    return {f"{stem}.cfg": _CFG.format(mesh=windows, materials=materials), f"{stem}.ifo": _IFO.format(*fixed)}
