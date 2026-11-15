"""A model of Anno 2070 or 1404 as it is drawn: the config's meshes, where each sits, its materials and their
textures, the leaf clusters' cards, and a mesh file's surface. The island editor draws from it; the model build
reads 2070's models through the same functions.

``read_config`` is the entry: a model config in 2070's format (1404 shares it; ``cfg2070`` parses it) as a
``ModelConfig`` of ``Piece``s (a mesh, its ``Place``, its ``Surface``s in the material order the mesh's submeshes
count in), the configs it nests and its ``Leaves``. ``material_kind`` tells a material's family, ``cards`` lays
out a leaf cluster, ``surface`` reads a mesh's triangles. Formats: docs/formats/rdm.md.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..formats.rdmio import all_mesh_blocks
from ..sources.overlay import normalize
from . import cfg2070, fileresolve
from .cfg2070 import BillboardCluster, Material
from .meshxform import qmul, qrotate
from .shader2070 import material_values

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
ANY = ""  # the materials key of a texture that applies whatever the mesh's material is called

# The family a material's shader is: (owner config type, m_ShaderIndex), docs/rendering/materials-2070.md 2-3. A
# family without a 117 shader of its own keeps its name here and is dealt with in models/materials.
MODEL_FAMILY = {
    0: "model",  # Model, Dye and Glow compile one file: the normal map's blue is a glow mask in all three
    6: "model",
    10: "model",
    1: "water",
    2: "water",
    3: "foam",
    4: "cutout",
    7: "icon",
    9: "icon",
    12: "hologram",
    13: "video",
    5: "portrait",
    11: "portrait",
    14: "portrait",
}
SHADER_DYE = 6  # "Dye": the player colour where the mask's green says (engine-rendering "Player colour")
GLOW_MODEL_SHADERS = (0, 6, 10)  # the normal map's blue is glow
PLANT_INSTANCING_SHADERS = (0, 1, 2)  # Model Wind, Grass, Vegetation: the instancing shaders that sway


@dataclass(frozen=True)
class Place:
    """A placement: translate, rotate (x, y, z, w), scale per axis."""

    position: Vec3 = (0.0, 0.0, 0.0)
    rotation: Quat = (0.0, 0.0, 0.0, 1.0)
    scale: Vec3 = (1.0, 1.0, 1.0)

    def then(self, child: Place) -> Place:
        """``child`` placed inside this one (shear from a turned non-uniform scale is left out)."""
        p = tuple(c * s for c, s in zip(child.position, self.scale, strict=True))
        moved = tuple(a + b for a, b in zip(self.position, qrotate(self.rotation, p), strict=True))
        scale = tuple(a * b for a, b in zip(self.scale, child.scale, strict=True))
        return Place(moved, qmul(self.rotation, child.rotation), scale)


@dataclass(frozen=True)
class Surface:
    """One material of a mesh, in the config's order (a submesh names it by this index): its textures (the files
    the game loads, "" for none), its family (``material_kind``) and its colour."""

    name: str
    diffuse: str
    normal: str
    kind: str = "model"
    color: Vec3 = (1.0, 1.0, 1.0)


@dataclass(frozen=True)
class Piece:
    """One mesh of a model: the mesh file, its place in the model, per material name (lower case; ``ANY``
    for all) the diffuse and normal texture, the colour it is tinted with (an instancing model's), its
    ``Surface``s in material order and per material name the (name, value) pairs the game's own shaders read
    (``shader2070.material_values``, 1701's ``grannymodels.material_values``)."""

    mesh: str
    place: Place
    materials: dict[str, tuple[str, str]] = field(default_factory=dict)
    color: Vec3 = (1.0, 1.0, 1.0)
    surfaces: tuple[Surface, ...] = ()
    values: dict[str, tuple] = field(default_factory=dict)


@dataclass(frozen=True)
class Leaves:
    """A tree's leaf cards (an ``INSTANCING_BBCLUSTER`` of the config ``cfg``, the ``index``-th), which the
    game turns to the camera (``cards``)."""

    cfg: str
    index: int
    place: Place
    diffuse: str
    color: Vec3


@dataclass(frozen=True)
class PlacedEffect:
    """A particle effect file (``.efc``) the config places; ``started``: a sequence of the config names it (an effect
    of one-shot emitters plays only then)."""

    file: str
    place: Place
    color: Vec3 | None
    started: bool


@dataclass
class ModelConfig:
    """What a model config draws: its pieces, the configs it nests (path, place), its leaf clusters and effects."""

    pieces: list[Piece]
    files: list[tuple[str, Place]] = field(default_factory=list)
    leaves: list[Leaves] = field(default_factory=list)
    effects: list[PlacedEffect] = field(default_factory=list)


# Textures and materials


def texture_path(name: str, exists: Callable[[str], bool]) -> str:
    """The file a config's texture name loads: 1404 names a ``.png`` and ships ``<stem>_0.dds``; 1701 ships
    the ``.tga`` it names. "" when none is there (the old games' rules of ``fileresolve``)."""
    return fileresolve.texture_file(name, exists, "anno2070")


def material_kind(mat: Material, alpha: float = 1.0) -> str:
    """A 2070 material's family, by the shader it selects in its owner's table (materials-2070 2-3): cutout, water,
    plant (an ``INSTANCING_MODEL`` on a wind shader), glass (a model drawn blended: the object's ``COLOR_TRANSFORM``
    ``alpha`` below 1, never above 0), icon, video, or a family 117 has no shader for (foam, hologram, portrait);
    model otherwise."""
    if mat.owner == "INSTANCING_MODEL":
        return "plant" if mat.shader_index in PLANT_INSTANCING_SHADERS else "model"
    if mat.owner != "MODEL":
        return "model"
    kind = MODEL_FAMILY.get(mat.shader_index, "model")
    return "glass" if kind == "model" and 0.0 < alpha < 1.0 else kind


def _textures(mat: Material, texture: Callable[[str], str]) -> tuple[str, str]:
    """A material's diffuse and normal files, "" where it switches a map off or the file is not there."""
    diffuse = mat.textures.get("diffuse", "") if mat.flags.get("diffuse", True) else ""
    normal = mat.textures.get("normal", "") if mat.flags.get("normal", True) else ""
    return (texture(diffuse) if diffuse else "", texture(normal) if normal else "")


def surfaces(materials: list[Material], texture: Callable[[str], str], alpha: float = 1.0) -> tuple[Surface, ...]:
    """A mesh's materials as ``Surface``s, in order; ``texture`` turns a config's texture name into a file;
    ``alpha``: the model's ``COLOR_TRANSFORM`` alpha."""
    out = []
    for mat in materials:
        diffuse, normal = _textures(mat, texture)
        r, g, b, _a = mat.colors.get("diffuse", (1.0, 1.0, 1.0, 1.0))
        out.append(Surface(mat.name, diffuse, normal, material_kind(mat, alpha), (r, g, b)))
    return tuple(out)


# Model configs


def place_of(t: cfg2070.Transform | None) -> Place:
    """A config's transform chain as one placement (a missing turn is no turn)."""
    place = Place()
    for step in [t, *t.chain] if t else []:
        turn = step.rotation if any(step.rotation) else Place.rotation
        place = place.then(Place(step.position, turn, (step.scale,) * 3))
    return place


def read_config(text: str, texture: Callable[[str], str], path: str = "") -> ModelConfig:
    """A model config (at ``path``) in 2070's format: its pieces, nested configs and leaf clusters. ``texture``
    turns a texture name into the file the game loads ("" when there is none). Models and effects a
    VISIBILITY_TRANSFORM shows only in some state (damaged, under construction) are left out."""
    cfg = cfg2070.parse_cfg(text, path)
    out = ModelConfig([])
    for m in [*cfg.models, *cfg.cloths]:
        if getattr(m, "visible_when", 0) or not m.filename:
            continue
        tint = getattr(m, "tint", None) or (1.0, 1.0, 1.0)
        mesh = normalize(m.filename)
        ct = getattr(m, "color_transform", None)
        found = surfaces(m.materials, texture, ct.alpha if ct is not None else 1.0)
        by_name = {s.name.lower(): (s.diffuse, s.normal) for s in found}
        values = {mat.name.lower(): material_values(mat, texture, ct) for mat in m.materials}
        out.pieces.append(Piece(mesh, place_of(m.transform), by_name, tuple(tint), found, values))
    out.files = [
        (normalize(f.filename), place_of(f.transform))
        for f in cfg.files
        if f.filename and not f.visible_when and not f.damage
    ]
    named = {index for seq in cfg.sequences for _start, _end, index in seq.effects}
    out.effects = [  # an effect in a state (damage, a mask) or on a bone is not in the model's rest pose
        PlacedEffect(normalize(e.filename), place_of(e.transform), e.color, e.index in named)
        for e in cfg.effects
        if e.filename and not e.visible_when and not e.damage and e.bone is None
    ]
    for k, cluster in enumerate(cfg.billboard_clusters):
        diffuse = next(iter(surfaces(cluster.materials, texture)), Surface("", "", "")).diffuse
        colors = cluster.colors or [(1.0, 1.0, 1.0, 1.0)]
        color = tuple(sum(c[i] for c in colors) / len(colors) for i in range(3))
        out.leaves.append(Leaves(normalize(path), k, place_of(cluster.transform), diffuse, color))
    return out


# Leaf cards

CORNERS = ((-1, -1), (1, -1), (1, 1), (-1, 1))  # a card's corners in drawing order: x right, y up


@dataclass(frozen=True)
class Card:
    """One leaf card: its centre, half its size, the unit direction out of the cluster (from its middle, lifted
    half a unit: the light falls on a crown, not a ball) and the texture coordinate of each corner."""

    position: Vec3
    reach: float
    radial: Vec3
    uvs: tuple[tuple[float, float], ...]


def _unit(v: Vec3, fallback: Vec3 = (0.0, 1.0, 0.0)) -> Vec3:
    length = math.sqrt(sum(c * c for c in v))
    return tuple(c / length for c in v) if length > 1e-6 else fallback


def cards(cluster: BillboardCluster) -> list[Card]:
    """A leaf cluster's cards as the game draws them, turned to the camera about their centres."""
    if not cluster.billboards:
        return []
    middle = tuple(sum(b.position[i] for b in cluster.billboards) / len(cluster.billboards) for i in range(3))
    out = []
    for card in cluster.billboards:
        p = card.position
        radial = _unit((p[0] - middle[0], p[1] - middle[1], p[2] - middle[2]))
        u0, v0, u1, v1 = card.texcoords
        out.append(Card(tuple(p), card.size / 2, radial, ((u0, v1), (u1, v1), (u1, v0), (u0, v0))))
    return out


# Meshes


def surface(data: bytes | Path):
    """A mesh file (its bytes or its path)'s triangles: ``(positions Nx3, indices Mx3)`` as numpy arrays (all its meshes, one after the
    other), or None. Only the position is read; the vertex layout always begins with it, four half floats of
    which the first three are x, y, z."""
    import struct

    import numpy as np

    try:
        data = data.read_bytes() if isinstance(data, Path) else data
        meshes = all_mesh_blocks(data)
        if not meshes:
            return None
        positions, triangles, first = [], [], 0
        for blocks in meshes:
            count, stride = blocks.count, blocks.stride
            raw = np.frombuffer(data, np.uint8, count * stride, blocks.vertex_ptr).reshape(count, stride)[:, :8]
            positions.append(np.frombuffer(raw.tobytes(), np.float16).reshape(count, 4)[:, :3].astype(np.float32))
            idx = np.frombuffer(data, "<u2" if blocks.index_size == 2 else "<u4", blocks.n_idx, blocks.index_ptr)
            idx = idx[: (len(idx) // 3) * 3].reshape(-1, 3).astype(np.int64)
            if idx.max(initial=0) >= count:
                return None
            triangles.append(idx + first)
            first += count
        return np.concatenate(positions), np.concatenate(triangles)
    except (OSError, struct.error, ValueError):
        return None
