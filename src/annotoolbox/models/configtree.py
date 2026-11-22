"""A model config of any game read as a tree, the way the studio's object builder shows it: typed parts (models,
materials, nested files, cloths, particles, decals, lights, collisions, sequences ...) with their fields.

``read_tree(text)`` is the entry: 117's, 1800's and 2205's ``Config`` / ``ConfigType`` files and 2070's and 1404's
``m_Config`` / ``m_ConfigType`` ones alike. On the tree: ``placement``, ``spins``, ``bone_link`` (where a part sits and
what moves it), ``sequences``, ``animations``, and for the newer games ``mesh_file``, ``lod_files``, ``material_lods``,
``shared_config`` (docs/engine-modloader.md "the ``shared/`` copies"; docs/rendering/material-fields.md).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..formats.fnv import fnv
from ..mod.modxml import close_tags
from ..sources.assetdb import number
from ..sources.overlay import normalize
from . import meshxform
from .cfgdoc import is_config_tag, strip

# a heading inside a material (<Common [ModelCommon]>Common [ModelCommon]</...>), kept as a field of its own
_GROUP = re.compile(r"<([A-Za-z_][\w.]*) \[([^\]<>]*)\]>[^<]*</[^<>]*>")
# a tag cut down to its leading name: <DistanceScaleEndScale;>
_TAG = re.compile(r"<(/?)([A-Za-z_][\w.]*)[^<>/]*(/?)>")
GROUP = "_group"
BOM = chr(0xFEFF)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # not allowed in XML: a unit's name field holds some
BARE_AMP = re.compile(r"&(?!#?\w+;)")  # the same stray bytes hold an "&" now and then (three of 117's configs)
LOD_COUNT = 5  # the newer games' baked levels: _lod0.rdm to _lod4.rdm


@dataclass
class SNode:
    """One part of a config: its ``kind`` (MAIN, MODEL, MATERIAL, FILE, ...), the list it sits in (``section``: Models,
    Materials, ...) and its place there (``index``: what a sequence's ``ModelID`` counts), its fields in file order,
    the structured values that are not parts (``blocks``, the first of each name: ``MaterialLODInfos``, ``AABBMin``),
    its parts (``children``), ``path``, a stable id (``Models/0/Materials/2``), and the ``element`` it was read from."""

    kind: str
    section: str = ""
    index: int = 0
    path: str = ""
    fields: list[tuple[str, str]] = field(default_factory=list)
    blocks: dict[str, ET.Element] = field(default_factory=dict)
    children: list[SNode] = field(default_factory=list)
    element: ET.Element | None = field(default=None, repr=False)

    def get(self, name: str, default: str = "") -> str:
        """The first field of that name (2070's ``m_`` prefix optional)."""
        for key, value in self.fields:
            if key == name or key == "m_" + name:
                return value
        return default

    def number(self, name: str, default: float = 0.0) -> float:
        try:
            return float(self.get(name, ""))
        except ValueError:
            return default

    @property
    def name(self) -> str:
        return self.get("Name")

    def section_of(self, section: str) -> list[SNode]:
        """The children in one list (``Models``, ``Materials`` ...), in order."""
        return [child for child in self.children if child.section == section]

    def walk(self):
        """This node and every node below it, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, path: str) -> SNode | None:
        return next((node for node in self.walk() if node.path == path), None)


def _is_config_element(element: ET.Element) -> bool:
    return is_config_tag(element.tag)


def parse(text: str) -> ET.Element:
    """The config's XML element: group headings kept, tag suffixes cut, ``</>`` closed."""
    text = BARE_AMP.sub("&amp;", CONTROL.sub("", text))
    text = _GROUP.sub(lambda match: f"<{GROUP}>{match.group(1)} [{match.group(2)}]</{GROUP}>", text)
    root = ET.fromstring(close_tags(_TAG.sub(r"<\1\2\3>", text.lstrip(BOM))))
    if not _is_config_element(root):  # a wrapper around the main config
        root = next((element for element in root.iter() if _is_config_element(element)), root)
    return root


def read_tree(text: str) -> SNode:
    """The whole config as a tree of ``SNode``s; raises ``ET.ParseError`` for a file that is not one."""
    return _node(parse(text), "", 0, "")


def _node(element: ET.Element, section: str, index: int, path: str) -> SNode:
    node = SNode("", section, index, path, element=element)
    for child in element:
        tag = strip(child.tag)
        if tag == "ConfigType":
            node.kind = (child.text or "").strip()
        elif len(child) and all(_is_config_element(part) for part in child):
            for k, sub in enumerate(child):
                node.children.append(_node(sub, tag, k, f"{path}/{tag}/{k}".lstrip("/")))
        elif len(child):
            node.blocks.setdefault(child.tag, child)
        else:
            node.fields.append((child.tag, (child.text or "").strip()))
    return node


# Placement and sequences


@dataclass(frozen=True)
class SPlacement:
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


def transforms(node: SNode) -> list[SNode]:
    """A part's transformers (ORIENTATION_TRANSFORM, ANIMATION_TRANSFORM, BONELINK_TRANSFORM ...), in order."""
    return node.section_of("Transformer")


def placement(node: SNode) -> SPlacement:
    """Where a part sits: its ORIENTATION_TRANSFORMs nested in order, the first outermost (scale per axis: Scale x
    NonUniformScale), those with conditions (a state's pose) left out. The other transformers animate or switch a
    part; without an orientation an ANIMATION_TRANSFORM's offset places it (rendering/material-fields.md)."""
    found = transforms(node)
    orientations = [
        transformer
        for transformer in found
        if transformer.kind == "ORIENTATION_TRANSFORM" and not transformer.number("Conditions")
    ]
    if not orientations:
        animation = next((transformer for transformer in found if transformer.kind == "ANIMATION_TRANSFORM"), None)
        if animation is not None:
            return SPlacement(tuple(animation.number(f"PositionOffset.{axis}") for axis in "xyz"))
        return SPlacement()
    position, rotation, scale = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)
    for transformer in orientations:
        local_position = tuple(transformer.number(f"Position.{axis}") for axis in "xyz")
        local_rotation = (
            *(transformer.number(f"Rotation.{axis}") for axis in "xyz"),
            transformer.number("Rotation.w", 1.0),
        )
        if not any(local_rotation):
            local_rotation = (0.0, 0.0, 0.0, 1.0)
        uniform_scale = transformer.number("Scale", 1.0)
        local_scale = tuple(uniform_scale * transformer.number(f"NonUniformScale.{axis}", 1.0) for axis in "xyz")
        moved = meshxform.qrotate(
            rotation, tuple(factor * value for factor, value in zip(scale, local_position, strict=True))
        )
        position = tuple(old + step for old, step in zip(position, moved, strict=True))
        rotation, scale = (
            meshxform.qmul(rotation, local_rotation),
            tuple(old * factor for old, factor in zip(scale, local_scale, strict=True)),
        )
    return SPlacement(position, rotation, scale)


def shown_always(node: SNode, on_joints: bool = False) -> bool:
    """False for a part shown only in some state of the object (a VISIBILITY_TRANSFORM with conditions) or, unless
    ``on_joints``, hung on a joint (a BONELINK_TRANSFORM): the rest pose leaves those out."""
    for transformer in transforms(node):
        if (transformer.kind == "BONELINK_TRANSFORM" and not on_joints) or (
            transformer.kind == "VISIBILITY_TRANSFORM" and transformer.number("Conditions")
        ):
            return False
    return True


def shown_when(node: SNode) -> int:
    """The condition bits a part's VISIBILITY_TRANSFORM shows it under (shown while one of them is on); 0: always
    (engine-animation "Bone links, conditions and the other sequences")."""
    return next((int(transformer.number("Conditions")) for transformer in transforms(node)
                 if transformer.kind == "VISIBILITY_TRANSFORM" and transformer.number("Conditions")), 0)  # fmt: skip


def day_night(node: SNode) -> tuple[int, int, float] | None:
    """A part's DAYNIGHT_TRANSFORM as (its ``TimeSlice``, its ``Conditions`` bits, its ``FadeTime`` in seconds), None
    without one (engine-animation "Time of day and the states of effects, lights and decals")."""
    return next(((int(transformer.number("TimeSlice")), int(transformer.number("Conditions")), transformer.number("FadeTime"))
                 for transformer in transforms(node) if transformer.kind == "DAYNIGHT_TRANSFORM"), None)  # fmt: skip


@dataclass(frozen=True)
class SAnimSpec:
    """An ANIMATION_TRANSFORM's fields (engine-animation "Spinning parts"): the spin (``rotation`` Euler radians,
    ``speed``, ``oscillation`` degrees), the position motion (``offset`` x, y, z, ``position_speed``,
    ``position_rotate``), the ``alignment`` (1 the light, 2 the wind, 3 and 4 the camera, 5 to 7 the clock) and the
    ``conditions`` bits it runs under (0: always)."""

    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    speed: float = 1.0
    oscillation: float = 0.0
    conditions: int = 0
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    position_speed: float = 1.0
    position_rotate: bool = False
    alignment: int = 0


def animation_transforms(node: SNode) -> list[SAnimSpec]:
    """A part's ANIMATION_TRANSFORMs, in order."""
    return [
        SAnimSpec(tuple(transformer.number(f"Rotation.{axis}") for axis in "xyz"), transformer.number("RotationSpeed", 1.0),
                 transformer.number("Oscillation"), int(transformer.number("Conditions")),
                 tuple(transformer.number(f"PositionOffset.{axis}") for axis in "xyz"), transformer.number("PositionSpeed", 1.0),
                 transformer.number("PositionRotate") != 0, int(transformer.number("Alignment")))
        for transformer in transforms(node) if transformer.kind == "ANIMATION_TRANSFORM"
    ]  # fmt: skip


def fnv1a32(text: str) -> int:
    """117 names a joint by this hash of its name (a BONELINK_TRANSFORM's ``BoneHash``)."""
    return fnv(text.encode("latin-1", "replace"))


@dataclass(frozen=True)
class SBoneLink:
    """A part hung on a joint of ``Models[model]``'s skeleton: 117 names the joint by its name's hash, 2070 by its
    number (engine-animation "Bone links")."""

    model: int
    hash: int | None = None
    index: int | None = None


def bone_link(node: SNode) -> SBoneLink | None:
    """The joint a part hangs on (its BONELINK_TRANSFORM), None for a part that hangs on none."""
    found = next((transformer for transformer in transforms(node) if transformer.kind == "BONELINK_TRANSFORM"), None)
    if found is None:
        return None
    if found.get("BoneHash"):
        return SBoneLink(int(found.number("ModelID")), int(found.number("BoneHash")) & 0xFFFFFFFF)
    return SBoneLink(int(found.number("ModelID")), index=int(found.number("BoneID")))


@dataclass(frozen=True)
class SSpinSpec:
    """An ANIMATION_TRANSFORM: ``rotation`` Euler radians, ``speed``, ``oscillation`` degrees, the ``conditions`` bits
    it runs under (0: always) (engine-animation "Spinning parts")."""

    rotation: tuple[float, float, float]
    speed: float = 1.0
    oscillation: float = 0.0
    conditions: int = 0


def spins(node: SNode) -> list[SSpinSpec]:
    """A part's ANIMATION_TRANSFORMs that turn it, in order."""
    out = []
    for transformer in transforms(node):
        rotation = tuple(transformer.number(f"Rotation.{axis}") for axis in "xyz")
        if transformer.kind == "ANIMATION_TRANSFORM" and any(rotation):
            out.append(
                SSpinSpec(
                    rotation,
                    transformer.number("RotationSpeed", 1.0),
                    transformer.number("Oscillation"),
                    int(transformer.number("Conditions")),
                )
            )
    return out


@dataclass(frozen=True)
class SClip:
    """One of a model's animations: the file, the time ``scale`` (5 plays it five times slower; 0 plays nothing) and
    ``loops`` (0 for ever) (engine-animation "Skinned meshes")."""

    file: str
    scale: float = 1.0
    loops: int = 0


def animations(model: SNode) -> list[SClip]:
    """A MODEL's ANIMATION blocks as clips (the files as the game loads them), in the config's order."""
    return [
        SClip(mesh_file(clip_node), clip_node.number("Scale", 1.0), int(clip_node.number("LoopCount")))
        for clip_node in model.section_of("Animations")
    ]


@dataclass(frozen=True)
class STrackElement:
    """One element of a sequence's track: ``kind`` 0 plays ``Models[model]``'s ``Animations[animation]``, 1 starts
    the effect in slot ``effect`` (the newer games' ``ParticleID``: its place in ``Particles``; 2070's ``m_EffectID``:
    the ``m_Index`` of one of its ``m_Effects``), 2 sets condition bits (docs/engine-animation.md "Sequences")."""

    kind: int
    start_ms: int
    end_ms: int
    model: int = 0
    animation: int = 0
    effect: int = 0
    condition: int = 0


@dataclass
class SSequence:
    node: str
    sequence_id: int
    looped: bool
    speed: float
    tracks: list[list[STrackElement]] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return max((element.end_ms for track in self.tracks for element in track), default=0)

    @property
    def label(self) -> str:
        """The id, a newer game's packed one spelled out: action, info, variant (100691457 is 6 / 110 / 1)."""
        packed_id = self.sequence_id
        return (
            f"{packed_id} ({packed_id >> 24} / {(packed_id >> 8) & 0xFFFF} / {packed_id & 0xFF})"
            if packed_id >= 1 << 24
            else str(packed_id)
        )


def _int(element: ET.Element, name: str) -> int:
    child = next((child for child in element if strip(child.tag) == name), None)
    return number(child.text if child is not None else None)


def sequences(tree: SNode) -> list[SSequence]:
    """The config's sequences with their tracks, in file order."""
    out = []
    for node in tree.section_of("Sequences"):
        seq = SSequence(node.path, int(node.number("SequenceID")), node.get("Looped") == "1", node.number("Speed", 1.0))
        for track in (
            [child for child in node.element if strip(child.tag) == "Track"] if node.element is not None else []
        ):
            seq.tracks.append([
                STrackElement(_int(track_element, "Type"), _int(track_element, "StartTime"), _int(track_element, "EndTime"), _int(track_element, "ModelID"),
                             _int(track_element, "AnimationID"), _int(track_element, "ParticleID") or _int(track_element, "EffectID"),
                             _int(track_element, "Condition"))
                for track_element in track if strip(track_element.tag) == "TrackElement"
            ])  # fmt: skip
        out.append(seq)
    return out


# The newer games' files (117, 1800, 2205)


def config_suffixes(game: str) -> tuple[str, ...]:
    """The file suffixes of a game's model configs (1701's are ``.mcf``, every other game's ``.cfg``)."""
    return (".mcf",) if game == "anno1701" else (".cfg",)


def model_configs(paths: Iterable[str], game: str) -> list[str]:
    """The model configs among ``paths`` of a game, in their order."""
    suffixes = config_suffixes(game)
    return [path for path in paths if path.endswith(suffixes)]


def mesh_file(node: SNode) -> str:
    """The mesh a MODEL or CLOTH loads: the configs name the source ``.fbx``, the game loads the ``.rdm``."""
    return re.sub(r"\.fbx$", ".rdm", normalize(node.get("FileName")))


def lod_files(
    mesh: str, exists: Callable[[str], bool], *, any_level: bool = False, count: int = LOD_COUNT
) -> list[str]:
    """The levels of detail of a mesh, finest first: ``_lod0.rdm`` and its siblings that exist (the mesh alone else).
    ``any_level``: a mesh named at any ``_lodN`` stands for the whole chain, listed from the levels that exist."""
    pattern = r"_lod\d+\.rdm$" if any_level else r"_lod0\.rdm$"
    if not re.search(pattern, mesh):
        return [mesh]
    levels = [re.sub(pattern, f"_lod{level}.rdm", mesh) for level in range(count)]
    if any_level:
        return [path for path in levels if exists(path)] or [mesh]
    return [mesh] + [path for path in levels[1:] if exists(path)]


def material_lods(model: SNode) -> list[list[int]]:
    """Per level of detail, the config's material index of each of the mesh's materials (``MaterialLODInfos``); empty
    when the config gives none (the mesh names the config's materials directly)."""
    block = model.blocks.get("MaterialLODInfos")
    if block is None:
        return []
    return [
        [number(item.text) for item in level.iter("i") if item is not level and not len(item)]
        for level in block.findall("i")
    ]


@dataclass(frozen=True)
class SMaterialFields:
    """A MATERIAL as its config writes it (texture names as written, slashes forward; "" for a map switched off):
    the albedo or diffuse map and colour, the normal, metal and dye maps, the shader, the gloss factor, the glow
    (``cEmissiveColor`` when ``GLOW_ENABLED``), the opacity, the water's flow and a flipbook's frames
    (rendering/material-fields.md)."""

    name: str = ""
    diffuse: str = ""
    normal: str = ""
    metal: str = ""
    dye: str = ""
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    shader: int = 8
    gloss: float = 1.0
    glow: float = 0.0
    opacity: float = 1.0
    flow: float = 0.0
    flipbook: dict | None = None


def _map(node: SNode, name: str, switch: str, default: str = "1") -> str:
    found = node.get(name).replace("\\", "/") if node.get(switch, default) != "0" else ""
    return "" if "default_" in found else found


def material_fields(node: SNode) -> SMaterialFields:
    """A MATERIAL node's fields (the banner slot names its map and colour as albedo, the others as diffuse)."""
    albedo = any(key == "cAlbedoTexture" for key, _value in node.fields)
    texture_field, color_field = ("cAlbedoTexture", "cAlbedoColor") if albedo else ("cModelDiffTex", "cDiffuseColor")
    flipbook = None
    if node.get("FLIPBOOK_ENABLED") == "1":  # an atlas of frames the shader steps through (water effects: 8 x 8)
        flipbook = {"x": int(node.number("cNumSubImagesX", 1)), "y": int(node.number("cNumSubImagesY", 1)),
                    "frames": int(node.number("cNumFrames", 1)), "duration": node.number("cFrameDuration", 0.1),
                    "tiling": [node.number("cFlipbookTiling.x", 1), node.number("cFlipbookTiling.y", 1)]}  # fmt: skip
    return SMaterialFields(
        node.name, _map(node, texture_field, "DIFFUSE_ENABLED"), _map(node, "cModelNormalTex", "NORMAL_ENABLED"),
        _map(node, "cModelMetallicTex", "METALLIC_TEX_ENABLED", "0"), _map(node, "cDyeMask", "DYE_MASK_ENABLED", "0"),
        tuple(node.number(f"{color_field}.{channel}", 1.0) for channel in "rgb"), int(node.number("ShaderID", 8)),
        node.number("cGlossinessFactor", 1.0), node.number("cEmissiveColor", 2.0) if node.get("GLOW_ENABLED") == "1" else 0.0,
        _opacity(node), node.number("cFlowSpeed"), flipbook,
    )  # fmt: skip


def _opacity(node: SNode) -> float:
    """How much of its colour a material lays down: ``cOpacity``, a terrain decal's ``cDiffuseOpacity`` (0 lays
    only its normal map on the ground; island-editor-view "Decals on the terrain")."""
    return node.number("cOpacity", 1.0) * node.number("cDiffuseOpacity", 1.0)


def first_values(fields) -> dict[str, str]:
    """A node's fields by name, the first of a name winning (as ``shadercache.cfg_values`` reads a MATERIAL)."""
    out: dict[str, str] = {}
    for key, value in fields:
        out.setdefault(key, value)
    return out


def materials_at(model: SNode, level: int = 0) -> dict[int, SNode]:
    """The mesh's material number -> the config's MATERIAL node at a level of detail (``MaterialLODInfos``; the
    numbers in order without it)."""
    nodes = model.section_of("Materials")
    lods = material_lods(model)
    order = lods[level] if level < len(lods) and lods[level] else list(range(len(nodes)))
    return {k: nodes[material_index] for k, material_index in enumerate(order) if 0 <= material_index < len(nodes)}


def shared_config(cfg: str) -> str:
    """The baked copy the game reads in place of a ``data/`` config when it exists: the first ``data/`` as ``shared/``."""
    path = normalize(cfg)
    return "shared/" + path[len("data/") :] if path.startswith("data/") else ""
