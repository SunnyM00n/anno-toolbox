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
class Node:
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
    children: list[Node] = field(default_factory=list)
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

    def section_of(self, section: str) -> list[Node]:
        """The children in one list (``Models``, ``Materials`` ...), in order."""
        return [c for c in self.children if c.section == section]

    def walk(self):
        """This node and every node below it, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, path: str) -> Node | None:
        return next((n for n in self.walk() if n.path == path), None)


def _is_config_el(el: ET.Element) -> bool:
    return is_config_tag(el.tag)


def parse(text: str) -> ET.Element:
    """The config's XML element: group headings kept, tag suffixes cut, ``</>`` closed."""
    text = BARE_AMP.sub("&amp;", CONTROL.sub("", text))
    text = _GROUP.sub(lambda m: f"<{GROUP}>{m.group(1)} [{m.group(2)}]</{GROUP}>", text)
    root = ET.fromstring(close_tags(_TAG.sub(r"<\1\2\3>", text.lstrip(BOM))))
    if not _is_config_el(root):  # a wrapper around the main config
        root = next((el for el in root.iter() if _is_config_el(el)), root)
    return root


def read_tree(text: str) -> Node:
    """The whole config as a tree of ``Node``s; raises ``ET.ParseError`` for a file that is not one."""
    return _node(parse(text), "", 0, "")


def _node(el: ET.Element, section: str, index: int, path: str) -> Node:
    node = Node("", section, index, path, element=el)
    for child in el:
        tag = strip(child.tag)
        if tag == "ConfigType":
            node.kind = (child.text or "").strip()
        elif len(child) and all(_is_config_el(c) for c in child):
            for k, sub in enumerate(child):
                node.children.append(_node(sub, tag, k, f"{path}/{tag}/{k}".lstrip("/")))
        elif len(child):
            node.blocks.setdefault(child.tag, child)
        else:
            node.fields.append((child.tag, (child.text or "").strip()))
    return node


# Placement and sequences


@dataclass(frozen=True)
class Placement:
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


def transforms(node: Node) -> list[Node]:
    """A part's transformers (ORIENTATION_TRANSFORM, ANIMATION_TRANSFORM, BONELINK_TRANSFORM ...), in order."""
    return node.section_of("Transformer")


def placement(node: Node) -> Placement:
    """Where a part sits: its ORIENTATION_TRANSFORMs nested in order, the first outermost (scale per axis: Scale x
    NonUniformScale), those with conditions (a state's pose) left out. The other transformers animate or switch a
    part; without an orientation an ANIMATION_TRANSFORM's offset places it (rendering/material-fields.md)."""
    found = transforms(node)
    orients = [t for t in found if t.kind == "ORIENTATION_TRANSFORM" and not t.number("Conditions")]
    if not orients:
        anim = next((t for t in found if t.kind == "ANIMATION_TRANSFORM"), None)
        if anim is not None:
            return Placement(tuple(anim.number(f"PositionOffset.{a}") for a in "xyz"))
        return Placement()
    pos, rot, scale = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)
    for t in orients:
        p = tuple(t.number(f"Position.{a}") for a in "xyz")
        q = (*(t.number(f"Rotation.{a}") for a in "xyz"), t.number("Rotation.w", 1.0))
        if not any(q):
            q = (0.0, 0.0, 0.0, 1.0)
        k = t.number("Scale", 1.0)
        s = tuple(k * t.number(f"NonUniformScale.{a}", 1.0) for a in "xyz")
        moved = meshxform.qrotate(rot, tuple(c * v for c, v in zip(scale, p, strict=True)))
        pos = tuple(a + b for a, b in zip(pos, moved, strict=True))
        rot, scale = meshxform.qmul(rot, q), tuple(a * b for a, b in zip(scale, s, strict=True))
    return Placement(pos, rot, scale)


def shown_always(node: Node, on_joints: bool = False) -> bool:
    """False for a part shown only in some state of the object (a VISIBILITY_TRANSFORM with conditions) or, unless
    ``on_joints``, hung on a joint (a BONELINK_TRANSFORM): the rest pose leaves those out."""
    for t in transforms(node):
        if (t.kind == "BONELINK_TRANSFORM" and not on_joints) or (
            t.kind == "VISIBILITY_TRANSFORM" and t.number("Conditions")
        ):
            return False
    return True


def shown_when(node: Node) -> int:
    """The condition bits a part's VISIBILITY_TRANSFORM shows it under (shown while one of them is on); 0: always
    (engine-animation "Bone links, conditions and the other sequences")."""
    return next((int(t.number("Conditions")) for t in transforms(node)
                 if t.kind == "VISIBILITY_TRANSFORM" and t.number("Conditions")), 0)  # fmt: skip


def day_night(node: Node) -> tuple[int, int, float] | None:
    """A part's DAYNIGHT_TRANSFORM as (its ``TimeSlice``, its ``Conditions`` bits, its ``FadeTime`` in seconds), None
    without one (engine-animation "Time of day and the states of effects, lights and decals")."""
    return next(((int(t.number("TimeSlice")), int(t.number("Conditions")), t.number("FadeTime"))
                 for t in transforms(node) if t.kind == "DAYNIGHT_TRANSFORM"), None)  # fmt: skip


@dataclass(frozen=True)
class AnimSpec:
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


def animation_transforms(node: Node) -> list[AnimSpec]:
    """A part's ANIMATION_TRANSFORMs, in order."""
    return [
        AnimSpec(tuple(t.number(f"Rotation.{a}") for a in "xyz"), t.number("RotationSpeed", 1.0),
                 t.number("Oscillation"), int(t.number("Conditions")),
                 tuple(t.number(f"PositionOffset.{a}") for a in "xyz"), t.number("PositionSpeed", 1.0),
                 t.number("PositionRotate") != 0, int(t.number("Alignment")))
        for t in transforms(node) if t.kind == "ANIMATION_TRANSFORM"
    ]  # fmt: skip


def fnv1a32(text: str) -> int:
    """117 names a joint by this hash of its name (a BONELINK_TRANSFORM's ``BoneHash``)."""
    return fnv(text.encode("latin-1", "replace"))


@dataclass(frozen=True)
class BoneLink:
    """A part hung on a joint of ``Models[model]``'s skeleton: 117 names the joint by its name's hash, 2070 by its
    number (engine-animation "Bone links")."""

    model: int
    hash: int | None = None
    index: int | None = None


def bone_link(node: Node) -> BoneLink | None:
    """The joint a part hangs on (its BONELINK_TRANSFORM), None for a part that hangs on none."""
    found = next((t for t in transforms(node) if t.kind == "BONELINK_TRANSFORM"), None)
    if found is None:
        return None
    if found.get("BoneHash"):
        return BoneLink(int(found.number("ModelID")), int(found.number("BoneHash")) & 0xFFFFFFFF)
    return BoneLink(int(found.number("ModelID")), index=int(found.number("BoneID")))


@dataclass(frozen=True)
class SpinSpec:
    """An ANIMATION_TRANSFORM: ``rotation`` Euler radians, ``speed``, ``oscillation`` degrees, the ``conditions`` bits
    it runs under (0: always) (engine-animation "Spinning parts")."""

    rotation: tuple[float, float, float]
    speed: float = 1.0
    oscillation: float = 0.0
    conditions: int = 0


def spins(node: Node) -> list[SpinSpec]:
    """A part's ANIMATION_TRANSFORMs that turn it, in order."""
    out = []
    for t in transforms(node):
        rot = tuple(t.number(f"Rotation.{a}") for a in "xyz")
        if t.kind == "ANIMATION_TRANSFORM" and any(rot):
            out.append(
                SpinSpec(rot, t.number("RotationSpeed", 1.0), t.number("Oscillation"), int(t.number("Conditions")))
            )
    return out


@dataclass(frozen=True)
class Clip:
    """One of a model's animations: the file, the time ``scale`` (5 plays it five times slower; 0 plays nothing) and
    ``loops`` (0 for ever) (engine-animation "Skinned meshes")."""

    file: str
    scale: float = 1.0
    loops: int = 0


def animations(model: Node) -> list[Clip]:
    """A MODEL's ANIMATION blocks as clips (the files as the game loads them), in the config's order."""
    return [
        Clip(mesh_file(a), a.number("Scale", 1.0), int(a.number("LoopCount"))) for a in model.section_of("Animations")
    ]


@dataclass(frozen=True)
class TrackElement:
    """One element of a sequence's track: ``kind`` 0 plays ``Models[model]``'s ``Animations[animation]``, 1 starts
    ``Particles[effect]``, 2 sets condition bits (docs/engine-animation.md "Sequences")."""

    kind: int
    start_ms: int
    end_ms: int
    model: int = 0
    animation: int = 0
    effect: int = 0
    condition: int = 0


@dataclass
class Sequence:
    node: str
    sequence_id: int
    looped: bool
    speed: float
    tracks: list[list[TrackElement]] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return max((e.end_ms for track in self.tracks for e in track), default=0)

    @property
    def label(self) -> str:
        """The id, a newer game's packed one spelled out: action, info, variant (100691457 is 6 / 110 / 1)."""
        i = self.sequence_id
        return f"{i} ({i >> 24} / {(i >> 8) & 0xFFFF} / {i & 0xFF})" if i >= 1 << 24 else str(i)


def _int(el: ET.Element, name: str) -> int:
    child = next((c for c in el if strip(c.tag) == name), None)
    return number(child.text if child is not None else None)


def sequences(tree: Node) -> list[Sequence]:
    """The config's sequences with their tracks, in file order."""
    out = []
    for node in tree.section_of("Sequences"):
        seq = Sequence(node.path, int(node.number("SequenceID")), node.get("Looped") == "1", node.number("Speed", 1.0))
        for track in [c for c in node.element if strip(c.tag) == "Track"] if node.element is not None else []:
            seq.tracks.append([
                TrackElement(_int(te, "Type"), _int(te, "StartTime"), _int(te, "EndTime"), _int(te, "ModelID"),
                             _int(te, "AnimationID"), _int(te, "EffectID"), _int(te, "Condition"))
                for te in track if strip(te.tag) == "TrackElement"
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
    return [p for p in paths if p.endswith(suffixes)]


def mesh_file(node: Node) -> str:
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
    levels = [re.sub(pattern, f"_lod{n}.rdm", mesh) for n in range(count)]
    if any_level:
        return [p for p in levels if exists(p)] or [mesh]
    return [mesh] + [p for p in levels[1:] if exists(p)]


def material_lods(model: Node) -> list[list[int]]:
    """Per level of detail, the config's material index of each of the mesh's materials (``MaterialLODInfos``); empty
    when the config gives none (the mesh names the config's materials directly)."""
    block = model.blocks.get("MaterialLODInfos")
    if block is None:
        return []
    return [[number(i.text) for i in level.iter("i") if i is not level and not len(i)] for level in block.findall("i")]


@dataclass(frozen=True)
class MaterialFields:
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


def _map(node: Node, name: str, switch: str, default: str = "1") -> str:
    found = node.get(name).replace("\\", "/") if node.get(switch, default) != "0" else ""
    return "" if "default_" in found else found


def material_fields(node: Node) -> MaterialFields:
    """A MATERIAL node's fields (the banner slot names its map and colour as albedo, the others as diffuse)."""
    albedo = any(k == "cAlbedoTexture" for k, _v in node.fields)
    tex, col = ("cAlbedoTexture", "cAlbedoColor") if albedo else ("cModelDiffTex", "cDiffuseColor")
    flipbook = None
    if node.get("FLIPBOOK_ENABLED") == "1":  # an atlas of frames the shader steps through (water effects: 8 x 8)
        flipbook = {"x": int(node.number("cNumSubImagesX", 1)), "y": int(node.number("cNumSubImagesY", 1)),
                    "frames": int(node.number("cNumFrames", 1)), "duration": node.number("cFrameDuration", 0.1),
                    "tiling": [node.number("cFlipbookTiling.x", 1), node.number("cFlipbookTiling.y", 1)]}  # fmt: skip
    return MaterialFields(
        node.name, _map(node, tex, "DIFFUSE_ENABLED"), _map(node, "cModelNormalTex", "NORMAL_ENABLED"),
        _map(node, "cModelMetallicTex", "METALLIC_TEX_ENABLED", "0"), _map(node, "cDyeMask", "DYE_MASK_ENABLED", "0"),
        tuple(node.number(f"{col}.{c}", 1.0) for c in "rgb"), int(node.number("ShaderID", 8)),
        node.number("cGlossinessFactor", 1.0), node.number("cEmissiveColor", 2.0) if node.get("GLOW_ENABLED") == "1" else 0.0,
        _opacity(node), node.number("cFlowSpeed"), flipbook,
    )  # fmt: skip


def _opacity(node: Node) -> float:
    """How much of its colour a material lays down: ``cOpacity``, a terrain decal's ``cDiffuseOpacity`` (0 lays
    only its normal map on the ground; island-editor-view "Decals on the terrain")."""
    return node.number("cOpacity", 1.0) * node.number("cDiffuseOpacity", 1.0)


def first_values(fields) -> dict[str, str]:
    """A node's fields by name, the first of a name winning (as ``shadercache.cfg_values`` reads a MATERIAL)."""
    out: dict[str, str] = {}
    for key, value in fields:
        out.setdefault(key, value)
    return out


def materials_at(model: Node, level: int = 0) -> dict[int, Node]:
    """The mesh's material number -> the config's MATERIAL node at a level of detail (``MaterialLODInfos``; the
    numbers in order without it)."""
    nodes = model.section_of("Materials")
    lods = material_lods(model)
    order = lods[level] if level < len(lods) and lods[level] else list(range(len(nodes)))
    return {k: nodes[i] for k, i in enumerate(order) if 0 <= i < len(nodes)}


def shared_config(cfg: str) -> str:
    """The baked copy the game reads in place of a ``data/`` config when it exists: the first ``data/`` as ``shared/``."""
    path = normalize(cfg)
    return "shared/" + path[len("data/") :] if path.startswith("data/") else ""
