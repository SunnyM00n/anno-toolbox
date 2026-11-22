"""Anno 1701's model configs as the pieces the editor draws: each piece a Granny ``.gr2`` mesh
(``formats/grannyread``), where it sits in the model, and its materials' textures by name.

1701 names an object's models in an ``.ocf`` (``object_configs``: GUID -> its variations' ``.mcf``) and builds
each from a tree of mesh configs; ``read_1701`` follows that tree and its nested ``.mcf`` files. 1404's configs
are 2070's format (``models.modelread.read_config``). island-editor "Other games".
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field

from ..models.cfg2070 import child_float
from ..models.configtree import BARE_AMP
from ..models.modelread import ANY, SModelConfig, SPiece, SPlace, texture_path
from ..sources.overlay import normalize

MAX_DEPTH = 4  # nested configs followed this deep (a config that nests itself would never end)
# 1701 mesh-config classes and the transforms that place or switch them (island-editor "Other games")
MESH, NESTED, EFFECT, VEGETATION, CLOTH = 1, 2, 3, 7, 9
OFFSET, ROTATION, LOD, SCALE, CONDITION = 101, 102, 105, 107, 109


# Anno 1701
def _xml(text: str) -> ET.Element:
    """An ``.mcf`` / ``.ocf``: XML, except for a bare ``&`` in a few texture names."""
    return ET.fromstring(BARE_AMP.sub("&amp;", text))


def object_configs(texts: dict[str, str]) -> dict[int, list[str]]:
    """1701's ``.ocf`` files (path -> text): object GUID -> its variations' ``.mcf`` paths."""
    out: dict[int, list[str]] = {}
    for text in texts.values():
        try:
            root = _xml(text)
            guid = int(root.findtext("GUID") or "")
        except (ET.ParseError, ValueError):
            continue
        out.setdefault(
            guid, [normalize(mesh_config.text) for mesh_config in root.findall("MeshConfig") if mesh_config.text]
        )
    return out


def _transform_1701(transforms: list[ET.Element]) -> SPlace:
    by = {transform.findtext("m_MCClassType"): transform for transform in transforms}
    position = (0.0, 0.0, 0.0)
    rot = (0.0, 0.0, 0.0, 1.0)
    scale = (1.0, 1.0, 1.0)
    if (transform := by.get(str(OFFSET))) is not None:
        position = tuple(child_float(transform, f"m_OffsetPosition.{axis}") for axis in "xyz")
    if (transform := by.get(str(ROTATION))) is not None:
        quaternion = [child_float(transform, f"m_q.{axis}") for axis in "xyz"] + [child_float(transform, "m_q.w", 1.0)]
        norm = math.sqrt(sum(component * component for component in quaternion)) or 1.0
        rot = tuple(component / norm for component in quaternion)
    if (transform := by.get(str(SCALE))) is not None:
        scale = tuple(child_float(transform, f"m_ScaleFactor.{axis}", 1.0) for axis in "xyz")
    return SPlace(position, rot, scale)


# a material's texture slots the game's shaders read -> the tag naming the file (``shader1701.MAPS``)
TEXTURE_TAGS = {"diffuse": "m_DiffuseTexture", "bump": "m_BumpTexture", "specular": "m_SpecularTexture",
                "opacity": "m_OpacityTexture", "reflection": "m_ReflectionTexture"}  # fmt: skip
COLOR_TAGS = {"ambient": "m_AmbientColor", "diffuse": "m_DiffuseColor", "specular": "m_SpecularColor",
              "emissive": "m_EmissiveColor"}  # fmt: skip


def argb(value: int) -> tuple[float, float, float, float]:
    """A material colour as the ``.mcf`` stores it (a signed 32-bit ARGB integer) as (r, g, b, a) from 0 to 1."""
    masked = value & 0xFFFFFFFF
    return tuple(((masked >> shift) & 0xFF) / 255.0 for shift in (16, 8, 0, 24))


def material_values(material: ET.Element, texture: Callable[[str], str]) -> tuple:
    """A ``.mcf`` material as the (name, value) pairs the viewer's 1701 shaders read: its effect file
    (``m_Shader``), opacity, colours and the files of its texture slots."""
    values = [
        ("Shader", normalize(material.findtext("m_Shader") or "")),
        ("Opacity", child_float(material, "m_Opacity", 1.0)),
    ]
    values += [(f"Color.{k}", argb(int(child_float(material, tag, -1)))) for k, tag in COLOR_TAGS.items()]
    values += [(f"Texture.{k}", texture(material.findtext(tag) or "")) for k, tag in TEXTURE_TAGS.items()]
    return tuple(values)


@dataclass(frozen=True)
class SClip1701:
    """An ``m_Animation`` of a list: its ``.gr2``, speed (1000 the clip's own), start offset (ms) and looping."""

    file: str
    speed: float = 1000.0
    offset: float = 0.0
    loop: bool = True


@dataclass(frozen=True)
class SItem1701:
    """A drawn piece with what switches and moves it: the condition triggers it is shown under (``IS_DAMAGED``;
    all of them needed), the clips of its list, a cloth's fields (class 9: ``m_GravityFactor``, ``m_WindFactor``)."""

    piece: SPiece
    conditions: tuple[str, ...] = ()
    clips: tuple[SClip1701, ...] = ()
    cloth: dict[str, float] | None = None


@dataclass(frozen=True)
class SEffect1701:
    """An effect (class 3, an ``.efc``): where it sits, its start parameter ("" plays always, a name is a state
    the game starts it in), its loop length and time offset (ms), the conditions it is shown under."""

    file: str
    place: SPlace
    start: str = ""
    loop_ms: float = 0.0
    offset_ms: float = 0.0
    conditions: tuple[str, ...] = ()


@dataclass
class SModel1701:
    """A 1701 ``.mcf`` tree as the view draws it: its pieces and effects (``read_1701_model``); ``unread`` the nested
    files left out, each with why: the error reading it (missing: ``OSError`` or ``KeyError``), None nested too deep."""

    items: list[SItem1701] = field(default_factory=list)
    effects: list[SEffect1701] = field(default_factory=list)
    unread: list[tuple[str, Exception | None]] = field(default_factory=list)

    def states(self) -> list[str]:
        """Every condition trigger and named start parameter, in the order met: the view's state bits."""
        names = [condition for i in (*self.items, *self.effects) for condition in i.conditions]
        names += [effect.start for effect in self.effects if effect.start]
        return list(dict.fromkeys(names))


@dataclass
class _CWalk1701:
    """One ``.mcf`` tree being read: where the nested files come from, what is found."""

    read_text: Callable[[str], str]
    texture: Callable[[str], str]  # a texture name -> the file the game loads ("" for none)
    model: SModel1701 = field(default_factory=SModel1701)

    def file(self, path: str, place: SPlace, depth: int, within: tuple = ((), ())) -> None:
        root = _xml(self.read_text(path))
        self.children(root, place, {}, depth, within)

    def children(self, node: ET.Element, place: SPlace, materials: dict, depth: int, within: tuple) -> None:
        """A node's list: its materials and clips apply to the configs in it (and below); ``within`` is the
        (conditions, clips) the list is reached under."""
        childs = node.find("m_MCChilds")
        if childs is None:
            return
        materials = dict(materials)  # name -> its material_values, from here down
        for material in childs.findall("m_Material"):
            materials[(material.findtext("m_Name") or "").lower()] = material_values(material, self.texture)
        clips = (
            *within[1],
            *(_clip(animation) for animation in childs.findall("m_Animation") if animation.findtext("m_Filename")),
        )
        configs = childs.findall("m_MCConfig")
        lods = [
            child_float(transform, "m_LODMax")
            for config_node in configs
            for transform in config_node.findall("m_MeshTransform")
            if _lod(transform)
        ]
        for config in configs:
            transforms = config.findall("m_MeshTransform")
            lod = next((transform for transform in transforms if _lod(transform)), None)
            if lod is not None and child_float(lod, "m_LODMax") < max(lods):
                continue  # a farther level of detail than the nearest one listed
            shown = [
                transform.findtext("m_ConditionTrigger") or ""
                for transform in transforms
                if _kind(transform) == CONDITION
            ]
            mesh = config.find("m_MeshConfig")
            if mesh is not None:
                state = (
                    tuple(dict.fromkeys((*within[0], *(config_node for config_node in shown if config_node)))),
                    clips,
                )
                self.node(mesh, place.then(_transform_1701(transforms)), materials, depth, state)

    def node(self, mesh: ET.Element, place: SPlace, materials: dict, depth: int, within: tuple) -> None:
        kind = _kind(mesh)
        name, texture = _NODE_FILES.get(kind, ("", ""))
        path = normalize(mesh.findtext(name) or "") if name else ""
        if kind == EFFECT and (effect := normalize(mesh.findtext("m_EffectFilename") or "")):
            start = (mesh.findtext("m_EffectStartParameter") or "").strip()
            loop, effect_time_offset = (
                child_float(mesh, "m_EffectLoopDuration"),
                child_float(mesh, "m_EffectTimeOffset"),
            )
            self.model.effects.append(SEffect1701(effect, place, start, loop, effect_time_offset, within[0]))
        elif path.endswith(".mcf"):  # a nested config (vegetation names one too)
            if depth >= MAX_DEPTH:
                self.model.unread.append((path, None))
            else:
                try:
                    self.file(path, place, depth + 1, within)
                except (OSError, KeyError, ET.ParseError) as exc:  # the rest of the model still draws
                    self.model.unread.append((path, exc))
        elif path:
            if texture:
                piece = SPiece(path, place, {ANY: (self.texture(mesh.findtext(texture) or ""), "")})
            else:
                maps = {
                    k: (dict(texture_pairs)["Texture.diffuse"], dict(texture_pairs)["Texture.bump"])
                    for k, texture_pairs in materials.items()
                }
                piece = SPiece(path, place, maps, values=materials)
            cloth = (
                {k: child_float(mesh, f"m_{k}", default_value) for k, default_value in CLOTH_FIELDS.items()}
                if kind == CLOTH
                else None
            )
            self.model.items.append(SItem1701(piece, within[0], within[1], cloth))
        self.children(mesh, place, materials, depth, within)


CLOTH_FIELDS = {"GravityFactor": 1.0, "WindFactor": 1.0}  # a class 9 node's simulation fields and their defaults


def _kind(node: ET.Element) -> int:
    return int(child_float(node, "m_MCClassType", -1))


def _clip(animation: ET.Element) -> SClip1701:
    return SClip1701(normalize(animation.findtext("m_Filename") or ""), child_float(animation, "m_Speed", 1000.0),
                    child_float(animation, "m_StartOffset"), child_float(animation, "m_Loop", 1.0) != 0)  # fmt: skip


_NODE_FILES = {  # a drawn node's class -> the tag naming its file, the tag naming its own texture ("": by material)
    MESH: ("m_MeshFilename", ""),
    NESTED: ("m_MeshConfigFilename", ""),
    VEGETATION: ("m_GrannyObjectFilename", "m_TextureFilename"),
    CLOTH: ("m_MeshFilename", "m_DiffuseTexture"),
}


def _lod(transform: ET.Element) -> bool:
    return transform.findtext("m_MCClassType") == str(LOD)


def read_1701_model(
    path: str,
    read_text: Callable[[str], str],
    exists: Callable[[str], bool],
    unit: float = 1.0,
    texture: Callable[[str], str] | None = None,
) -> SModel1701:
    """A 1701 ``.mcf`` and the ones it nests, scaled by ``unit`` (1701 models are in world units, 32 a tile): every
    piece at the nearest level of detail with its conditions, clips and cloth, and the effects. ``texture`` turns
    a texture name into the file the game loads (``texture_path`` over ``exists`` by default)."""
    walk = _CWalk1701(read_text, texture or (lambda name: texture_path(name, exists)))
    walk.file(normalize(path), SPlace(scale=(unit,) * 3), 0)
    return walk.model


def read_1701(
    path: str,
    read_text: Callable[[str], str],
    exists: Callable[[str], bool],
    unit: float = 1.0,
    texture: Callable[[str], str] | None = None,
) -> SModelConfig:
    """``read_1701_model`` as the pieces the island editor draws: what a condition switches on (damaged) is left
    out, as are the effects."""
    model = read_1701_model(path, read_text, exists, unit, texture)
    return SModelConfig([i.piece for i in model.items if not i.conditions])
