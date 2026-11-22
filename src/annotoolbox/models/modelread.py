"""A model of Anno 2070 or 1404 as it is drawn: the config's meshes, where each sits, its materials and their
textures, the leaf clusters' cards, and a mesh file's surface. The island editor draws from it; the model build
reads 2070's models through the same functions.

``read_config`` is the entry: a model config in 2070's format (1404 shares it; ``cfg2070`` parses it) as a
``SModelConfig`` of ``SPiece``s (a mesh, its ``SPlace``, its ``SSurface``s in the material order the mesh's submeshes
count in), the configs it nests and its ``SLeaves``. ``material_kind`` tells a material's family, ``cards`` lays
out a leaf cluster, ``surface`` reads a mesh's triangles. Formats: docs/formats/rdm.md.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import rdmread
from ..formats.rdmio import all_mesh_blocks
from ..sources.overlay import normalize
from . import cfg2070, fileresolve
from .cfg2070 import SBillboardCluster, SMaterial
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

# 2070's foam sheet (model_foamfall.fx): the vertex colour's green and blue scroll its two samples at 0.5 and 1 texture
# lengths a second, its alpha x 0.25 is the opacity (materials-2070 section 2)
FOAM_SPEEDS = (0.5, 1.0)
FOAM_OPACITY = 0.25
COLOUR_USAGE = rdmread.LETTERS.index("C")  # a vertex element's usage: the colour


def foam_values(mesh: rdmread.SMesh | None) -> tuple[float, float, float]:
    """(speed of the first sample, of the second, opacity) of a foam sheet from its 2070 mesh's vertex colours, read
    as the shader reads them (``.bgra`` of the stored bytes): the means over the vertices, as 117's slot takes them
    per material (the fade to the vertices whose alpha is 0 is lost). A mesh without colours: full speed and opacity."""
    index = mesh.element_at(COLOUR_USAGE) if mesh else None
    if mesh is None or index is None or not mesh.vertex_count:
        return (FOAM_SPEEDS[0], FOAM_SPEEDS[1], FOAM_OPACITY)
    rows = mesh.element(index)  # stored blue, green, red, alpha

    def mean(channel: int) -> float:
        return sum(row[channel] for row in rows) / len(rows) / 255

    return (FOAM_SPEEDS[0] * mean(1), FOAM_SPEEDS[1] * mean(0), FOAM_OPACITY * mean(3))


def mesh_foam_values(read: Callable[[str], bytes], mesh: str) -> tuple[float, float, float]:
    """``foam_values`` of the first mesh of the file ``mesh``, read with ``read(path)`` (the full speeds and opacity
    for a file that does not read)."""
    try:
        meshes = [candidate for candidate in rdmread.read_rdm(read(mesh)).meshes if candidate.vertex_count]
    except (rdmread.CRdmReadError, OSError, KeyError):
        meshes = []
    return foam_values(meshes[0] if meshes else None)


@dataclass(frozen=True)
class SPlace:
    """A placement: translate, rotate (x, y, z, w), scale per axis."""

    position: Vec3 = (0.0, 0.0, 0.0)
    rotation: Quat = (0.0, 0.0, 0.0, 1.0)
    scale: Vec3 = (1.0, 1.0, 1.0)

    def then(self, child: SPlace) -> SPlace:
        """``child`` placed inside this one (shear from a turned non-uniform scale is left out)."""
        scaled_position = tuple(
            coordinate * axis_scale for coordinate, axis_scale in zip(child.position, self.scale, strict=True)
        )
        moved = tuple(
            own_value + child_value
            for own_value, child_value in zip(self.position, qrotate(self.rotation, scaled_position), strict=True)
        )
        scale = tuple(own_value * child_value for own_value, child_value in zip(self.scale, child.scale, strict=True))
        return SPlace(moved, qmul(self.rotation, child.rotation), scale)


@dataclass(frozen=True)
class SSurface:
    """One material of a mesh, in the config's order (a submesh names it by this index): its textures (the files
    the game loads, "" for none), its family (``material_kind``) and its colour."""

    name: str
    diffuse: str
    normal: str
    kind: str = "model"
    color: Vec3 = (1.0, 1.0, 1.0)


@dataclass(frozen=True)
class SPiece:
    """One mesh of a model: the mesh file, its place in the model, per material name (lower case; ``ANY``
    for all) the diffuse and normal texture, the colour it is tinted with (an instancing model's), its
    ``SSurface``s in material order and per material name the (name, value) pairs the game's own shaders read
    (``shader2070.material_values``, 1701's ``grannymodels.material_values``)."""

    mesh: str
    place: SPlace
    materials: dict[str, tuple[str, str]] = field(default_factory=dict)
    color: Vec3 = (1.0, 1.0, 1.0)
    surfaces: tuple[SSurface, ...] = ()
    values: dict[str, tuple] = field(default_factory=dict)


@dataclass(frozen=True)
class SLeaves:
    """A tree's leaf cards (an ``INSTANCING_BBCLUSTER`` of the config ``cfg``, the ``index``-th), which the
    game turns to the camera (``cards``)."""

    cfg: str
    index: int
    place: SPlace
    diffuse: str
    color: Vec3


@dataclass(frozen=True)
class SPlacedEffect:
    """A particle effect file (``.efc``) the config places; ``started``: a sequence of the config names it (an effect
    of one-shot emitters plays only then)."""

    file: str
    place: SPlace
    color: Vec3 | None
    started: bool


@dataclass
class SModelConfig:
    """What a model config draws: its pieces, the configs it nests (path, place), its leaf clusters and effects."""

    pieces: list[SPiece]
    files: list[tuple[str, SPlace]] = field(default_factory=list)
    leaves: list[SLeaves] = field(default_factory=list)
    effects: list[SPlacedEffect] = field(default_factory=list)


# Textures and materials


def texture_path(name: str, exists: Callable[[str], bool]) -> str:
    """The file a config's texture name loads: 1404 names a ``.png`` and ships ``<stem>_0.dds``; 1701 ships
    the ``.tga`` it names. "" when none is there (the old games' rules of ``fileresolve``)."""
    return fileresolve.texture_file(name, exists, "anno2070")


def material_kind(material: SMaterial, alpha: float = 1.0) -> str:
    """A 2070 material's family, by the shader it selects in its owner's table (materials-2070 2-3): cutout, water,
    plant (an ``INSTANCING_MODEL`` on a wind shader), glass (a model drawn blended: the object's ``COLOR_TRANSFORM``
    ``alpha`` below 1, never above 0), icon, video, or a family 117 has no shader for (foam, hologram, portrait);
    model otherwise."""
    if material.owner == "INSTANCING_MODEL":
        return "plant" if material.shader_index in PLANT_INSTANCING_SHADERS else "model"
    if material.owner != "MODEL":
        return "model"
    kind = MODEL_FAMILY.get(material.shader_index, "model")
    return "glass" if kind == "model" and 0.0 < alpha < 1.0 else kind


def _textures(material: SMaterial, texture: Callable[[str], str]) -> tuple[str, str]:
    """A material's diffuse and normal files, "" where it switches a map off or the file is not there."""
    diffuse = material.textures.get("diffuse", "") if material.flags.get("diffuse", True) else ""
    normal = material.textures.get("normal", "") if material.flags.get("normal", True) else ""
    return (texture(diffuse) if diffuse else "", texture(normal) if normal else "")


def surfaces(materials: list[SMaterial], texture: Callable[[str], str], alpha: float = 1.0) -> tuple[SSurface, ...]:
    """A mesh's materials as ``SSurface``s, in order; ``texture`` turns a config's texture name into a file;
    ``alpha``: the model's ``COLOR_TRANSFORM`` alpha."""
    out = []
    for material in materials:
        diffuse, normal = _textures(material, texture)
        red, green, blue, _a = material.colors.get("diffuse", (1.0, 1.0, 1.0, 1.0))
        out.append(SSurface(material.name, diffuse, normal, material_kind(material, alpha), (red, green, blue)))
    return tuple(out)


# Model configs


def place_of(transform: cfg2070.STransform | None) -> SPlace:
    """A config's transform chain as one placement (a missing turn is no turn)."""
    place = SPlace()
    for step in [transform, *transform.chain] if transform else []:
        turn = step.rotation if any(step.rotation) else SPlace.rotation
        place = place.then(SPlace(step.position, turn, (step.scale,) * 3))
    return place


def read_config(text: str, texture: Callable[[str], str], path: str = "") -> SModelConfig:
    """A model config (at ``path``) in 2070's format: its pieces, nested configs and leaf clusters. ``texture``
    turns a texture name into the file the game loads ("" when there is none). Models and effects a
    VISIBILITY_TRANSFORM shows only in some state (damaged, under construction) are left out."""
    cfg = cfg2070.parse_cfg(text, path)
    out = SModelConfig([])
    for model in [*cfg.models, *cfg.cloths]:
        if getattr(model, "visible_when", 0) or not model.filename:
            continue
        tint = getattr(model, "tint", None) or (1.0, 1.0, 1.0)
        mesh = normalize(model.filename)
        color_transform = getattr(model, "color_transform", None)
        found = surfaces(model.materials, texture, color_transform.alpha if color_transform is not None else 1.0)
        by_name = {found_surface.name.lower(): (found_surface.diffuse, found_surface.normal) for found_surface in found}
        values = {
            material.name.lower(): material_values(material, texture, color_transform) for material in model.materials
        }
        out.pieces.append(SPiece(mesh, place_of(model.transform), by_name, tuple(tint), found, values))
    out.files = [
        (normalize(nested.filename), place_of(nested.transform))
        for nested in cfg.files
        if nested.filename and not nested.visible_when and not nested.damage
    ]
    named = {index for seq in cfg.sequences for _start, _end, index in seq.effects}
    out.effects = [  # an effect in a state (damage, a mask) or on a bone is not in the model's rest pose
        SPlacedEffect(normalize(effect.filename), place_of(effect.transform), effect.color, effect.index in named)
        for effect in cfg.effects
        if effect.filename and not effect.visible_when and not effect.damage and effect.bone is None
    ]
    for k, cluster in enumerate(cfg.billboard_clusters):
        diffuse = next(iter(surfaces(cluster.materials, texture)), SSurface("", "", "")).diffuse
        colors = cluster.colors or [(1.0, 1.0, 1.0, 1.0)]
        color = tuple(sum(colour[i] for colour in colors) / len(colors) for i in range(3))
        out.leaves.append(SLeaves(normalize(path), k, place_of(cluster.transform), diffuse, color))
    return out


# Leaf cards

CORNERS = ((-1, -1), (1, -1), (1, 1), (-1, 1))  # a card's corners in drawing order: x right, y up


@dataclass(frozen=True)
class SCard:
    """One leaf card: its centre, half its size, the unit direction out of the cluster (from its middle, lifted
    half a unit: the light falls on a crown, not a ball) and the texture coordinate of each corner."""

    position: Vec3
    reach: float
    radial: Vec3
    uvs: tuple[tuple[float, float], ...]


def _unit(vector: Vec3, fallback: Vec3 = (0.0, 1.0, 0.0)) -> Vec3:
    length = math.sqrt(sum(component * component for component in vector))
    return tuple(component / length for component in vector) if length > 1e-6 else fallback


def cards(cluster: SBillboardCluster) -> list[SCard]:
    """A leaf cluster's cards as the game draws them, turned to the camera about their centres."""
    if not cluster.billboards:
        return []
    middle = tuple(sum(b.position[i] for b in cluster.billboards) / len(cluster.billboards) for i in range(3))
    out = []
    for card in cluster.billboards:
        position = card.position
        radial = _unit((position[0] - middle[0], position[1] - middle[1], position[2] - middle[2]))
        u0, v0, u1, v1 = card.texcoords
        out.append(SCard(tuple(position), card.size / 2, radial, ((u0, v1), (u1, v1), (u1, v0), (u0, v0))))
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
            indices = np.frombuffer(data, "<u2" if blocks.index_size == 2 else "<u4", blocks.n_idx, blocks.index_ptr)
            indices = indices[: (len(indices) // 3) * 3].reshape(-1, 3).astype(np.int64)
            if indices.max(initial=0) >= count:
                return None
            triangles.append(indices + first)
            first += count
        return np.concatenate(positions), np.concatenate(triangles)
    except (OSError, struct.error, ValueError):
        return None
