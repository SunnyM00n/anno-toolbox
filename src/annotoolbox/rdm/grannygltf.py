"""Granny ``.gr2`` files (Anno 1404, 1701) and glTF, both ways through the RDM records the toolchain already moves:
``granny_to_rdm`` makes a ``.gr2``'s meshes, skeletons and animations an ``RdmFile`` for ``rdmgltf.export``, and
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

import numpy as np

from ..formats import grannyanim, grannyread
from ..formats import grannywrite as gw
from ..formats.grannyglb import meshes_of
from ..formats.isd2070 import FIXED
from ..formats.rdmlayout import encode, parse, weight_bytes
from ..formats.rdmread import Animation, Material, Mesh, RdmFile, Skeleton, Submesh
from ..models.grannyrig import best_skeleton, granny_animation, granny_skeleton, granny_skin
from ..models.meshxform import qconj
from ..models.rdmmesh import mesh_data
from ..models.skinning import matrix_quat, rigid
from ..models.tangents import frames

STATIC, SKINNED = "P3f_N3f_T2f", "P3f_N3f_T2f_I4b_W4b"  # the RDM layouts a Granny mesh becomes


@dataclass(frozen=True)
class RecordOptions:
    """What ``granny_to_rdm`` makes beyond the glTF export's needs: ``colours`` the vertex colours as a ``C4c``
    element of an unskinned mesh (bytes B, G, R, A, as 2070 stores them), ``skin`` the skin elements' names (a
    second and later ``I4b`` or ``W4b`` hold zeros: 2205's 16-bone layout), ``turn`` the model turned half a turn
    about y (x and z negated; a proper rotation, so the skins stay exact)."""

    colours: bool = False
    skin: str = "I4b_W4b"
    turn: bool = False


# Granny to RDM

_HALF_TURN = np.diag([-1.0, 1.0, -1.0, 1.0])  # x and z negated: a rotation, the winding stays


def _turned(m):
    """A Granny mesh with its positions and normals turned half a turn about y."""
    flip = lambda v: (-v[0], v[1], -v[2])  # noqa: E731
    return replace(m, positions=[flip(p) for p in m.positions], normals=[flip(n) for n in m.normals])


def _row(elements, p, n, uv, colour, joints, weights) -> bytes:
    """One vertex: the value each element takes by its usage (the first ``I4b`` and ``W4b`` the skin's, later ones zero)."""
    seen: Counter[int] = Counter()
    out = []
    for e in elements:
        slot, seen[e.usage] = seen[e.usage], seen[e.usage] + 1
        value = {0: p, 1: n, 4: uv, 5: colour}.get(e.usage)
        if e.usage in (6, 7):
            value = (joints if e.usage == 7 else weights) if slot == 0 else (0, 0, 0, 0)
        out.append(encode(e, value))
    return b"".join(out)


def _rdm_mesh(m, skin, scale: float = 1.0, opts: RecordOptions = RecordOptions()) -> tuple[Mesh, list[str]]:  # noqa: B008
    """A Granny mesh (``grannyglb.GrannyMesh``) as an RDM mesh record of float vertices, skinned by ``skin``, and
    its material names in the order its submeshes number them."""
    coloured = opts.colours and skin is None and m.colors is not None
    layout = SKINNED.replace("I4b_W4b", opts.skin) if skin is not None else STATIC + ("_C4c" if coloured else "")
    elements = parse(layout)
    rows, positions = [], [tuple(c / scale for c in p) for p in m.positions]
    for k, (p, n, uv) in enumerate(zip(positions, m.normals, m.uvs, strict=True)):
        colour = bytes(round(min(max(m.colors[k][c], 0.0), 1.0) * 255) for c in (2, 1, 0, 3)) if coloured else None
        joints, weights = (skin[1][k], weight_bytes(skin[2][k])) if skin is not None else (None, None)
        rows.append(_row(elements, p, n, uv, colour and tuple(colour), joints, weights))
    indices, submeshes, names = [], [], []
    for name, tris in m.groups:
        if name not in names:
            names.append(name)
        submeshes.append(Submesh(len(indices), len(tris), names.index(name)))
        indices += tris
    lo = tuple(min(p[k] for p in positions) for k in range(3))
    hi = tuple(max(p[k] for p in positions) for k in range(3))
    stride = len(rows[0]) if rows else 0
    mesh = Mesh(m.name, elements, 4 if skin is not None else 0, 1 if len(m.positions) > 0xFFFF else 0,
                len(rows), stride, b"".join(rows), indices, submeshes, skin[0] if skin else None, (*lo, *hi), 0x5C)  # fmt: skip
    return mesh, names


def _turn_rig(out: RdmFile) -> None:
    """The skeletons and keys of ``out`` for a model turned half a turn about y: a joint's inverse bind keeps its move
    and turns by the half turn after it (a vertex in bone space stays where it was); a root joint's keys, which
    are its place in the model, are turned by it, a child's stay as they are."""
    for sk in out.skeletons:
        for j in sk.joints:
            m = rigid(j.rotation, j.position) @ _HALF_TURN
            j.rotation = qconj(matrix_quat(m[:3, :3]))
    roots = {j.name for sk in out.skeletons for j in sk.joints if j.parent is None}
    for a in out.animations:
        for t in (t for t in a.tracks if t.joint in roots):
            for k in t.keys:
                m = _HALF_TURN @ rigid(k.rotation, k.position)
                k.rotation, k.position = qconj(matrix_quat(m[:3, :3])), tuple(m[:3, 3])


def granny_to_rdm(
    data: bytes,
    animations: Sequence[grannyanim.Animation] = (),
    scale: float = 1.0,
    opts: RecordOptions = RecordOptions(),  # noqa: B008
) -> RdmFile:
    """A ``.gr2`` as one RDM file: its meshes (float vertices; a skin's bones through its bindings, a rigid mesh as a
    one-bone skin), its skeletons as joints, its own animations and ``animations`` (read from other ``.gr2`` files)
    sampled every time step against the skeleton naming most of their bones; every length divided by ``scale``
    (``rdm_to_granny``'s). Raises ``GrannyError``."""
    root = grannyread.GrannyFile(data).root()
    skeletons = grannyanim.read_skeletons(root)
    owners = grannyanim.model_skeletons(root)
    out = RdmFile(False, root.get("FromFileName") or "", "granny", skeletons=[granny_skeleton(s) for s in skeletons])
    materials: list[str] = []
    for m in meshes_of(root):
        m = _turned(m) if opts.turn else m
        mesh, names = _rdm_mesh(m, granny_skin(m, skeletons, owners.get(m.name)), scale, opts)
        for s in mesh.submeshes:  # the file's material numbers: one list over all its meshes
            name = names[s.material]
            if name not in materials:
                materials.append(name)
            s.material = materials.index(name)
        out.meshes.append(mesh)
    out.materials = [Material(n, "") for n in materials]
    for a in [*grannyanim.read_animations(root), *animations]:
        out.animations.append(granny_animation(a, best_skeleton(a, skeletons)))
    for joint in (j for sk in out.skeletons for j in sk.joints):  # a uniform scale moves an inverse bind's move
        joint.position = tuple(c / scale for c in joint.position)
    for key in (k for a in out.animations for t in a.tracks for k in t.keys):
        key.position = tuple(c / scale for c in key.position)
    if opts.turn:
        _turn_rig(out)
    return out


# RDM to Granny


@dataclass(frozen=True)
class GrannyOptions:
    """``scale`` every length (1701: 32 units a tile), ``units_per_meter`` the art tool info's (1701: 39.37)."""

    scale: float = 1.0
    units_per_meter: float = 1.0


GAME_UNITS = {
    "anno1404": GrannyOptions(),
    "anno1701": GrannyOptions(32.0, 39.370079),
}  # granny.md "What the games ship"


def _bones(skeleton: Skeleton, scale: float) -> list[dict]:
    """RDM joints (inverse binds) as Granny bones: the rest transform in the parent, the inverse world for row
    vectors (the move in the last row)."""
    inverse = [rigid(j.rotation, np.asarray(j.position) * scale) for j in skeleton.joints]
    bind = [np.linalg.inv(m) for m in inverse]
    out = []
    for k, j in enumerate(skeleton.joints):
        parent = j.parent if j.parent is not None and 0 <= j.parent < k else None
        local = bind[k] if parent is None else np.linalg.inv(bind[parent]) @ bind[k]
        transform = {**gw.IDENTITY_TRANSFORM, "position": tuple(local[:3, 3]),
                     "orientation": matrix_quat(local[:3, :3])}  # fmt: skip
        out.append({"Name": j.name, "ParentIndex": -1 if parent is None else parent, "Transform": transform,
                    "InverseWorldTransform": tuple(inverse[k].T.reshape(16))})  # fmt: skip
    return out


def _spec(mesh: Mesh, rdm: RdmFile, materials: dict, names: list[str], scale: float) -> gw.MeshSpec:
    """An RDM mesh as a Granny mesh: its groups by the file's material names, a skin's bones numbered as the
    skeleton's joints (``names``); a rigid mesh binds the one bone ``names`` holds."""
    data = mesh_data(mesh)
    v = data.vertices
    groups = []
    for k, tris in data.groups.items():
        name = rdm.materials[k].name if 0 <= k < len(rdm.materials) else f"material_{k}"
        groups.append((materials.setdefault(name, gw.Rec(gw.MATERIAL, {"Name": name})), [int(i) for i in tris]))
    spec = gw.MeshSpec(mesh.name, [tuple(p * scale) for p in v[:, :3]], [tuple(n) for n in v[:, 3:6]],
                       [tuple(t) for t in v[:, 6:8]], groups, list(names))  # fmt: skip
    indices = [i for _material, tris in groups for i in tris]
    _normals, spec.tangents, spec.binormals = frames(spec.positions, spec.uvs, indices, spec.normals)
    if data.skinned:
        spec.bone_indices = [tuple(int(j) for j in row) for row in data.joints]
        spec.bone_weights = [weight_bytes([float(w) for w in row]) for row in data.weights]
    return spec


def _track(track, scale: float) -> dict:
    """An RDM track (local keys) as linear Granny curves."""
    times = [k.time for k in track.keys]
    position = [tuple(c * scale for c in k.position) for k in track.keys]
    orientation = [qconj(k.rotation) for k in track.keys]
    return {"Name": track.joint, "PositionCurve": gw.curve(times, position),
            "OrientationCurve": gw.curve(times, orientation, dimension=4)}  # fmt: skip


def _animation(a: Animation, group: str, scale: float) -> gw.Rec:
    tracks = [_track(t, scale) for t in a.tracks if t.keys]
    step = 1.0 / a.frame_rate if a.frame_rate > 0 else 1.0 / 30
    return gw.animation_record(a.name, a.duration_ms / 1000.0, step, [(group, tracks)])


def rdm_to_granny(files: list[tuple[str, RdmFile]], name: str, opts: GrannyOptions | None = None) -> bytes:
    """One ``.gr2`` of RDM files (mesh files first, then animation files): a model per skeleton (a rigid mesh
    without one gets a bone of its own), every animation as a track group of the first model."""
    opts = opts or GrannyOptions()
    materials: dict[str, gw.Rec] = {}
    meshes, skeletons, models, animations = [], [], [], []
    stem = name.rsplit(".", 1)[0]
    for _file, rdm in files:
        made = [gw.skeleton_record(f"{stem}_{k}", _bones(s, opts.scale)) for k, s in enumerate(rdm.skeletons)]
        joints = [[j.name for j in s.joints] for s in rdm.skeletons]
        bound: dict[int, list] = {}
        for mesh in rdm.meshes:
            if mesh.skeleton is not None and 0 <= mesh.skeleton < len(made):
                spec = _spec(mesh, rdm, materials, joints[mesh.skeleton], opts.scale)
                bound.setdefault(mesh.skeleton, []).append(gw.mesh_record(spec))
            else:  # its own bone at the origin
                own = gw.skeleton_record(mesh.name, [{"Name": mesh.name, "ParentIndex": -1,
                                                      "InverseWorldTransform": tuple(np.identity(4).reshape(16))}])  # fmt: skip
                record = gw.mesh_record(_spec(mesh, rdm, materials, [mesh.name], opts.scale))
                skeletons.append(own)
                meshes.append(record)
                models.append(gw.model_record(mesh.name, own, [record]))
        for k, s in enumerate(made):
            skeletons.append(s)
            meshes += bound.get(k, [])
            models.append(gw.model_record(stem if not k else f"{stem}_{k}", s, bound.get(k, [])))
        animations += rdm.animations
    group = models[0].values["Name"] if models else stem
    records = [_animation(a, group, opts.scale) for a in animations]
    root = gw.root_record(name, opts.units_per_meter, Materials=list(materials.values()), Skeletons=skeletons,
                          Meshes=meshes, Models=models, Animations=records)  # fmt: skip
    return gw.write_file(root)


def gltf_to_granny(path, opts: GrannyOptions | None = None, import_options=None) -> tuple[bytes, list[str]]:
    """A glTF file as one ``.gr2`` (its name the glTF's, ``.gr2``) and the import's notes."""
    from ..formats.gltf import read_gltf
    from . import gltfrdm

    imported = gltfrdm.import_gltf(read_gltf(path), path.with_suffix(".rdm").name, import_options)
    return rdm_to_granny(imported.files, path.with_suffix(".gr2").name, opts), imported.notes


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
    meshes = meshes_of(grannyread.GrannyFile(data).root())
    points = [p for m in meshes for p in m.positions] or [(0.0, 0.0, 0.0)]
    lo = [min(p[k] for p in points) for k in range(3)]
    hi = [max(p[k] for p in points) for k in range(3)]
    names = list(dict.fromkeys(name for m in meshes for name, _tris in m.groups))
    stem, windows = mesh_path.rsplit(".", 1)[0], mesh_path.replace("/", "\\")
    texture = lambda name, ext: f"{stem}_{name or 'default'}_diff.{ext}".replace("/", "\\")  # noqa: E731
    if game == "anno1701":
        materials = "".join(_MATERIAL_1701.format(name=n, texture=texture(n, "tga")) for n in names)
        box = [math.floor(v) for v in lo] + [math.ceil(v) for v in hi]
        return {f"{stem}.mcf": _MCF.format(mesh=windows, materials=materials), f"{stem}.info": _INFO.format(*box)}
    materials = "".join(_MATERIAL_1404.format(name=n, texture=texture(n, "png")) for n in names)
    fixed = [round((a + b) / 2 * FIXED) for a, b in zip(lo, hi, strict=True)]
    fixed += [round((b - a) / 2 * FIXED) for a, b in zip(lo, hi, strict=True)]
    return {f"{stem}.cfg": _CFG.format(mesh=windows, materials=materials), f"{stem}.ifo": _IFO.format(*fixed)}
