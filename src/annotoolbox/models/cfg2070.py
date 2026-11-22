"""Parser for Anno 2070 model configs (``.cfg``): the parts the editor reads, the raw tree kept for the rest.

A 2070 cfg is an XML tree of ``<m_Config>`` nodes, each typed by its ``<m_ConfigType>`` child (MODEL, MATERIAL,
EFFECT, FILE, CLOTH, LIGHT, TERRAINDECAL, SEQUENCE, INSTANCING_MODEL, INSTANCING_BBCLUSTER and the
``*_TRANSFORM`` family), fields prefixed ``m_`` and vectors flattened as ``m_Position.x``.
"""

from __future__ import annotations

import functools
import operator
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]
Vec4 = tuple[float, float, float, float]

TEXTURE_FIELDS = {
    "diffuse": "m_DiffuseTexture",
    "normal": "m_NormalTexture",
    "environment": "m_EnvironmentTexture",
    "height": "m_HeightTexture",
    "mask": "m_MaskTexture",
    "ripples": "m_RipplesTexture",
}
COLOR_FIELDS = ("m_AmbientColor", "m_DiffuseColor", "m_SpecularColor", "m_EmissiveColor")
FLAG_FIELDS = (
    "m_DiffuseEnabled",
    "m_NormalEnabled",
    "m_EnvironmentEnabled",
    "m_ParallaxEnabled",
    "m_RipplesEnabled",
)

# config type -> reference kind for m_FileName / m_InstancingModel entries
FILE_KINDS = {
    "MODEL": "mesh",
    "INSTANCING_MODEL": "mesh:instancing",
    "CLOTH": "mesh:cloth",
    "ANIMATION": "animation",
    "EFFECT": "effect",
    "FILE": "cfg",
}


def _text(element: ET.Element, tag: str, default: str = "") -> str:
    value = element.findtext(tag)
    return value.strip() if value else default


def child_float(element: ET.Element, tag: str, default: float = 0.0) -> float:
    """The number in a child element's text, ``default`` when the child or a number is missing."""
    try:
        return float(_text(element, tag, str(default)))
    except ValueError:
        return default


def _int(element: ET.Element, tag: str, default: int = 0) -> int:
    try:
        return int(_text(element, tag, str(default)))
    except ValueError:
        return default


def _vec2(element: ET.Element, prefix: str) -> tuple[float, float]:
    return child_float(element, f"{prefix}.x"), child_float(element, f"{prefix}.y")


def _vec3(element: ET.Element, prefix: str) -> Vec3:
    return child_float(element, f"{prefix}.x"), child_float(element, f"{prefix}.y"), child_float(element, f"{prefix}.z")


def _vec4(element: ET.Element, prefix: str, components: str = "xyzw") -> Vec4:
    first, second, third, fourth = (child_float(element, f"{prefix}.{comp}") for comp in components)
    return first, second, third, fourth


def _rgb(element: ET.Element, default: float = 1.0) -> Vec3:
    return (
        child_float(element, "m_Color.r", default),
        child_float(element, "m_Color.g", default),
        child_float(element, "m_Color.b", default),
    )


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def config_type(element: ET.Element) -> str:
    return _text(element, "m_ConfigType")


def _configs(element: ET.Element | None, kind: str) -> list[ET.Element]:
    """The ``m_Config`` children of ``el`` whose type is ``kind`` (none for a missing container)."""
    return (
        [config for config in element.findall("m_Config") if config_type(config) == kind] if element is not None else []
    )


@dataclass
class STransform:
    position: Vec3 = (0.0, 0.0, 0.0)
    rotation: Vec4 = (0.0, 0.0, 0.0, 1.0)
    scale: float = 1.0
    conditions: int = 0
    # further ORIENTATION_TRANSFORMs of the same node, in order (23 nodes carry two: the lobster
    # groups' second y offset, the weather station's second scale); written out as they come
    chain: list[STransform] = field(default_factory=list)

    @classmethod
    def from_element(cls, element: ET.Element) -> STransform:
        return cls(
            position=_vec3(element, "m_Position"),
            rotation=_vec4(element, "m_Rotation"),
            scale=child_float(element, "m_Scale", 1.0),
            conditions=_int(element, "m_Conditions"),
        )


# The transformers of a node


def _transforms(parent: ET.Element, kind: str) -> list[ET.Element]:
    return _configs(parent.find("m_Transformer"), kind)


def _transform_of(parent: ET.Element) -> STransform | None:
    found = [
        STransform.from_element(transform_element) for transform_element in _transforms(parent, "ORIENTATION_TRANSFORM")
    ]
    if not found:
        return None
    found[0].chain = found[1:]
    return found[0]


def _damage_only(parent: ET.Element) -> bool:
    """Whether a DAMAGE_TRANSFORM hides the part on an intact object: it starts above damage 0 (the burning flames)
    or at scale 0. One from damage 0 at a size (the town centre's hologram, steam) shrinks it, the intact object has it."""
    return any(
        _int(transform_element, "m_StartDamage") > 0 or child_float(transform_element, "m_StartScale") <= 0
        for transform_element in _transforms(parent, "DAMAGE_TRANSFORM")
    )


def _visibility_of(parent: ET.Element) -> int:
    """The condition bits of a node's VISIBILITY_TRANSFORM (it is shown while the object has one), else 0."""
    found = _transforms(parent, "VISIBILITY_TRANSFORM")
    return _int(found[0], "m_Conditions") if found else 0


def _bone_of(parent: ET.Element) -> tuple[int, int, int] | None:
    """(model, skeleton, bone) of a node's BONELINK_TRANSFORM, None without one."""
    found = _transforms(parent, "BONELINK_TRANSFORM")
    if not found:
        return None
    return _int(found[0], "m_ModelID"), _int(found[0], "m_SkeletonID"), _int(found[0], "m_BoneID")


@dataclass
class SSpin:
    """An ``ANIMATION_TRANSFORM``: a constant turn of ``rotation`` (Euler radians) at ``speed``, while the object
    has one of the ``conditions`` bits (0: always); 117 has the same fields (engine-animation "Spinning parts")."""

    rotation: Vec3
    speed: float
    conditions: int = 0


def _spins_of(parent: ET.Element) -> list[SSpin]:
    return [
        SSpin(
            _vec3(spin_element, "m_Rotation"),
            child_float(spin_element, "m_RotationSpeed", 1.0),
            _int(spin_element, "m_Conditions"),
        )
        for spin_element in _transforms(parent, "ANIMATION_TRANSFORM")
    ]


@dataclass
class SColorTransform:
    """A node's first COLOR_TRANSFORM, its first state: the colour it sets, and whether it multiplies
    the object's own colour (the owner's participant colour; 2070: player colour) or replaces it."""

    color: Vec3 = (1.0, 1.0, 1.0)
    multiply: bool = False
    override: bool = False
    apply_always: bool = False
    alpha: float = 1.0  # m_Color.a: below 1 the object is drawn blended (materials-2070 6)

    def applies(self, is_shown: bool = False) -> bool:
        """Whether 2070 runs it: always on an object without ShowPlayerColor, else only with m_ApplyAlways."""
        return self.apply_always or not is_shown

    def takes_participant_colour(self, is_shown: bool = False) -> bool:
        """Whether the node ends up in the owner's colour; ``is_shown``: the object's Mesh/ShowPlayerColor
        (engine-rendering "Player colour")."""
        if not self.applies(is_shown):
            return True
        return not self.override and (is_shown or self.multiply)


def object_color(
    color_transform: SColorTransform | None, participant_colour: Vec3, is_shown: bool = False
) -> tuple[float, ...]:
    """The object colour (rgba) the engine hands a model's shaders (``cMtxMeshWorld``'s first row): the transform's
    colour, times the owner's ``participant_colour`` where it multiplies; without a transform white, or the participant's with
    ``shown``. The scene's water colour (``m_ApplyWaterColor``) needs a world, so it is not applied here
    (rendering-2070.md 7.7)."""
    if color_transform is None or not color_transform.applies(is_shown):
        return (*(participant_colour if is_shown else (1.0, 1.0, 1.0)), 1.0)
    rgb = color_transform.color
    if color_transform.takes_participant_colour(is_shown):
        rgb = tuple(
            channel * participant_channel for channel, participant_channel in zip(rgb, participant_colour, strict=True)
        )
    return (*rgb, color_transform.alpha)


def _color_transform_of(parent: ET.Element) -> SColorTransform | None:
    found = _transforms(parent, "COLOR_TRANSFORM")
    if not found:
        return None
    transform_element = found[0]
    state = transform_element.find("m_State")
    override, always = (
        _text(transform_element, "m_OverrideColor") == "1",
        _text(transform_element, "m_ApplyAlways") == "1",
    )
    if state is None:
        return SColorTransform(override=override, apply_always=always)
    return SColorTransform(
        color=_rgb(state),
        multiply=_text(state, "m_Multiply") == "1",
        override=override,
        apply_always=always,
        alpha=child_float(state, "m_Color.a", 1.0),
    )


def _lit_when(parent: ET.Element) -> int:
    """The condition bits a light shines for when its COLOR_TRANSFORM makes it black otherwise (2070 lights a
    building's lamps by state: while it works, while it is selected); 0 for any other colour transform."""
    found = _transforms(parent, "COLOR_TRANSFORM")
    if not found:
        return 0
    states = [(_int(state, "m_Condition"), _rgb(state, 0.0)) for state in found[0].findall("m_State")]
    off_conditions = [condition for condition, rgb in states if condition == 0 and not any(rgb)]
    on_conditions = [condition for condition, rgb in states if condition and any(rgb)]
    stray = [condition for condition, rgb in states if condition and not any(rgb)]
    return functools.reduce(operator.or_, on_conditions, 0) if off_conditions and on_conditions and not stray else 0


# The parts


@dataclass
class SMaterial:
    name: str
    shader_index: int
    vertex_format: str
    textures: dict[str, str]
    flags: dict[str, bool]
    colors: dict[str, Vec4]
    owner: str = (
        "MODEL"  # the config type whose shader table m_ShaderIndex indexes (docs/rendering/materials-2070.md 1)
    )
    bones: int = 0  # m_NumBonesPerVertex

    @classmethod
    def from_element(cls, element: ET.Element, owner: str = "MODEL") -> SMaterial:
        textures = {slot: _text(element, tag) for slot, tag in TEXTURE_FIELDS.items()}
        return cls(
            name=_text(element, "m_Name"),
            shader_index=_int(element, "m_ShaderIndex"),
            vertex_format=_text(element, "m_VertexFormat"),
            textures={slot: path for slot, path in textures.items() if path},
            flags={tag[2:-7].lower(): _text(element, tag) == "1" for tag in FLAG_FIELDS},
            colors={color_field[2:-5].lower(): _vec4(element, color_field, "rgba") for color_field in COLOR_FIELDS},
            owner=owner,
            bones=_int(element, "m_NumBonesPerVertex"),
        )


def _materials_of(parent: ET.Element, owner: str = "MODEL") -> list[SMaterial]:
    return [
        SMaterial.from_element(material_element, owner)
        for material_element in _configs(parent.find("m_Materials"), "MATERIAL")
    ]


@dataclass
class SAnimation:
    filename: str
    loop_count: int = 0
    scale: float = 1.0


@dataclass
class SModel:
    filename: str
    materials: list[SMaterial]
    transform: STransform | None
    animation_specs: list[SAnimation] = field(default_factory=list)
    tint: Vec3 | None = None  # an instancing model's colour (m_ColorR0 ...)
    color_transform: SColorTransform | None = None  # whether its Dye parts show the owner's colour
    visible_when: int = 0  # its VISIBILITY_TRANSFORM: the condition bits that show it; 0: always shown
    bone: tuple[int, int, int] | None = None  # (model, skeleton, bone) of its BONELINK_TRANSFORM: it hangs on a joint
    spins: list[SSpin] = field(default_factory=list)  # its ANIMATION_TRANSFORMs


def _model_of(model_element: ET.Element) -> SModel:
    animations = [
        SAnimation(
            _text(animation_element, "m_FileName"),
            _int(animation_element, "m_LoopCount"),
            child_float(animation_element, "m_Scale") or 1.0,
        )
        for animation_element in _configs(model_element.find("m_Animations"), "ANIMATION")
        if _text(animation_element, "m_FileName")
    ]
    return SModel(
        _text(model_element, "m_FileName"),
        _materials_of(model_element),
        _transform_of(model_element),
        animations,
        color_transform=_color_transform_of(model_element),
        visible_when=_visibility_of(model_element),
        bone=_bone_of(model_element),
        spins=_spins_of(model_element),
    )


def _instancing_model_of(model_element: ET.Element) -> SModel:
    """A farm field's crop or a tree's trunk: one mesh holding every plant, drawn at the origin. 117 has no
    instancing container, so it becomes a plain model with the first colour set as its tint."""
    model = SModel(
        _text(model_element, "m_InstancingModel"),
        _materials_of(model_element, "INSTANCING_MODEL"),
        _transform_of(model_element),
        visible_when=_visibility_of(model_element),
    )
    if model_element.find("m_ColorR0") is not None:
        model.tint = (
            child_float(model_element, "m_ColorR0", 1.0),
            child_float(model_element, "m_ColorG0", 1.0),
            child_float(model_element, "m_ColorB0", 1.0),
        )
    return model


@dataclass
class SCloth:
    """A simulated cloth (flag, sail): CLOTH config in ``m_Clothes`` (older files: inside ``m_Models``)."""

    filename: str
    materials: list[SMaterial]
    transform: STransform | None
    unique_simulation: bool = False
    gravity: float = 0.1
    local_wind_direction: float = 0.0
    line_size: float = 1.0
    color_transform: SColorTransform | None = None
    double_sided: bool = False  # m_IsDoubleSided: cloth.fx's two-pass technique
    ripple: Vec3 = (1.0, 0.0, 0.0)  # m_RippleSize, m_RippleNormalIntensity, m_RippleParallaxIntensity (cClothRipple)


def _cloth_of(cloth_element: ET.Element) -> SCloth:
    return SCloth(
        _text(cloth_element, "m_FileName"),
        _materials_of(cloth_element, "CLOTH"),
        _transform_of(cloth_element),
        unique_simulation=_text(cloth_element, "m_UniqueSimulation") == "1",
        gravity=child_float(cloth_element, "m_Gravity") or 0.1,
        local_wind_direction=child_float(cloth_element, "m_LocalWindDirection"),
        line_size=child_float(cloth_element, "m_LineSize") or 1.0,
        color_transform=_color_transform_of(cloth_element),
        double_sided=_text(cloth_element, "m_IsDoubleSided") == "1",
        ripple=tuple(
            child_float(cloth_element, f"m_Ripple{suffix}")
            for suffix in ("Size", "NormalIntensity", "ParallaxIntensity")
        ),
    )


@dataclass
class SEffect:
    """An ``EFFECT`` of a cfg's ``m_Effects``: the ``.efc`` file, where it sits and what switches it. ``index``
    is the slot a sequence's effect element names; ``bone`` (model, skeleton, bone) is set when it hangs on a
    skeleton's bone; ``visible_when`` is its VISIBILITY_TRANSFORM mask (0: none); ``damage`` is set when a
    DAMAGE_TRANSFORM hides it on an intact object (``_damage_only``); ``color`` is the first colour state without a condition."""

    filename: str
    transform: STransform | None
    index: int = 0
    bone: tuple[int, int, int] | None = None
    visible_when: int = 0
    damage: bool = False
    color: Vec3 | None = None
    spins: list[SSpin] = field(default_factory=list)


def _effect_of(element: ET.Element, index: int) -> SEffect:
    color = None
    for transform_element in _transforms(element, "COLOR_TRANSFORM"):
        for state in transform_element.findall("m_State"):
            if _int(state, "m_Condition") == 0:
                color = _rgb(state)
                break
    return SEffect(
        _text(element, "m_FileName"),
        _transform_of(element),
        index=index,
        bone=_bone_of(element),
        visible_when=_visibility_of(element),
        damage=_damage_only(element),
        color=color,
        spins=_spins_of(element),
    )


@dataclass
class SSubFile:
    """A nested config (props, trees, ...) placed with a transform; ``bone``, ``visible_when`` and ``spins`` as a
    ``SModel``'s. ``damage``: a DAMAGE_TRANSFORM hides it on an intact object (the burning flames, ``_damage_only``)."""

    filename: str
    transform: STransform | None
    visible_when: int = 0
    bone: tuple[int, int, int] | None = None
    spins: list[SSpin] = field(default_factory=list)
    damage: bool = False


def _subfile_of(file_element: ET.Element) -> SSubFile:
    return SSubFile(
        _text(file_element, "m_FileName"),
        _transform_of(file_element),
        visible_when=_visibility_of(file_element),
        bone=_bone_of(file_element),
        spins=_spins_of(file_element),
        damage=_damage_only(file_element),
    )


@dataclass
class SBillboard:
    """One camera-facing card of an ``INSTANCING_BBCLUSTER``: where it hangs, how wide it is and
    which part of the leaf sheet it shows."""

    position: Vec3
    size: float
    texcoords: Vec4  # u0, v0, u1, v1


@dataclass
class SBillboardCluster:
    """2070's foliage: a tree is a trunk mesh plus a cluster of leaf cards the engine turns towards the camera.
    There is no mesh for them - the cards are in the cfg (``billboards.banner_mesh`` builds one)."""

    billboards: list[SBillboard]
    materials: list[SMaterial]
    transform: STransform | None
    colors: list[Vec4] = field(default_factory=list)


def _billboard_cluster_of(element: ET.Element) -> SBillboardCluster:
    """The cards of one INSTANCING_BBCLUSTER. Their fields are numbered in hex - ``m_BBPosX0`` to
    ``m_BBPosXf`` and on through ``m_BBPosX10``, which is the seventeenth and not the eleventh."""
    cards = []
    for i in range(_int(element, "m_BBClusterSize")):
        hex_index = f"{i:x}"
        size = child_float(element, f"m_BBSize{hex_index}")
        if size <= 0:
            continue
        cards.append(
            SBillboard(
                position=(
                    child_float(element, f"m_BBPosX{hex_index}"),
                    child_float(element, f"m_BBPosY{hex_index}"),
                    child_float(element, f"m_BBPosZ{hex_index}"),
                ),
                size=size,
                # X/Y is one corner of the sheet's rectangle, Z/W the other
                texcoords=_bb_texcoords(element, hex_index),
            )
        )
    colors = [
        (
            child_float(element, f"m_ColorR{i}"),
            child_float(element, f"m_ColorG{i}"),
            child_float(element, f"m_ColorB{i}"),
            child_float(element, f"m_ColorA{i}"),
        )
        for i in range(_int(element, "m_ColorCount"))
    ]
    return SBillboardCluster(cards, _materials_of(element, "INSTANCING_BBCLUSTER"), _transform_of(element), colors)


def _bb_texcoords(element: ET.Element, hex_index: str) -> Vec4:
    return (
        child_float(element, f"m_BBTexX{hex_index}"),
        child_float(element, f"m_BBTexY{hex_index}"),
        child_float(element, f"m_BBTexZ{hex_index}"),
        child_float(element, f"m_BBTexW{hex_index}"),
    )


@dataclass
class SDecal:
    extent: tuple[float, float]
    texcoords: Vec4
    materials: list[SMaterial]
    transform: STransform | None = None


def _decal_of(decal_element: ET.Element) -> SDecal:
    return SDecal(
        extent=_vec2(decal_element, "m_Extend"),
        texcoords=_vec4(decal_element, "m_TexCoords"),
        materials=_materials_of(decal_element, "TERRAINDECAL"),
        transform=_transform_of(decal_element),
    )


@dataclass
class SLight:
    """A ``LIGHT`` of a cfg's ``m_Lights``: its values (``m_Type``, ``m_Diffuse.r`` ... as named in the cfg),
    where it sits, whether a colour transform switches it (2070 lights its buildings by state). ``lit_when``:
    the condition bits a colour transform makes it shine for (dark otherwise; ``_lit_when``), ``visible_when``:
    its VISIBILITY_TRANSFORM, ``bone`` and ``spins`` as a ``SModel``'s."""

    values: dict[str, float]
    transform: STransform | None
    switched: bool
    bone: tuple[int, int, int] | None = None
    visible_when: int = 0
    lit_when: int = 0
    spins: list[SSpin] = field(default_factory=list)
    damage: bool = False  # a DAMAGE_TRANSFORM hides it on an intact object (_damage_only)


def _light_of(light_element: ET.Element) -> SLight:
    values = {
        field_element.tag[2:]: float(field_element.text)
        for field_element in light_element
        if field_element.text and field_element.tag != "m_ConfigType" and _is_number(field_element.text)
    }
    return SLight(
        values,
        _transform_of(light_element),
        bool(_transforms(light_element, "COLOR_TRANSFORM")),
        bone=_bone_of(light_element),
        visible_when=_visibility_of(light_element),
        lit_when=_lit_when(light_element),
        spins=_spins_of(light_element),
        damage=_damage_only(light_element),
    )


@dataclass
class STrackElement:
    """One animation slot of a sequence track (type 0); effect slots are ignored."""

    model_id: int
    animation_id: int
    start_ms: int
    end_ms: int


@dataclass
class SSequence:
    sequence_id: int
    looped: bool
    speed: float
    tracks: list[list[STrackElement]] = field(default_factory=list)
    conditions: list[tuple[int, int, int]] = field(default_factory=list)  # condition tracks: (start ms, end ms, bits)
    effects: list[tuple[int, int, int]] = field(default_factory=list)  # effect slots: (start ms, end ms, effect index)

    @property
    def duration_ms(self) -> int:
        return max((track_element.end_ms for track in self.tracks for track_element in track), default=0)


def _sequence_of(element: ET.Element) -> SSequence:
    sequence = SSequence(
        _int(element, "m_SequenceID"), _text(element, "m_Looped") == "1", child_float(element, "m_Speed") or 1.0
    )
    for track in element.findall("Track"):
        elements = []
        for slot_element in track.findall("TrackElement"):
            kind = _text(slot_element, "Type")
            start, end = _int(slot_element, "m_StartTime"), _int(slot_element, "m_EndTime")
            if kind == "2":  # the condition bits the object has while the sequence plays
                sequence.conditions.append((start, end, _int(slot_element, "m_Condition")))
            elif kind == "1":
                sequence.effects.append((start, end, _int(slot_element, "m_EffectID")))
            elif kind == "0":
                elements.append(
                    STrackElement(_int(slot_element, "m_ModelID"), _int(slot_element, "m_AnimationID"), start, end)
                )
        if elements:
            sequence.tracks.append(elements)
    return sequence


# The cfg


@dataclass
class SCfg:
    path: str
    models: list[SModel] = field(default_factory=list)
    effects: list[SEffect] = field(default_factory=list)
    files: list[SSubFile] = field(default_factory=list)
    decals: list[SDecal] = field(default_factory=list)
    lights: list[SLight] = field(default_factory=list)
    sequences: list[SSequence] = field(default_factory=list)
    cloths: list[SCloth] = field(default_factory=list)
    billboard_clusters: list[SBillboardCluster] = field(default_factory=list)
    center: Vec3 = (0.0, 0.0, 0.0)
    extent: Vec3 = (0.0, 0.0, 0.0)
    radius: float = 0.0
    root: ET.Element | None = field(default=None, repr=False)

    def referenced_files(self) -> Iterator[tuple[str, str]]:
        """Yield (kind, path) for every external file this config points at.

        Walks every ``m_Config`` node in the tree, so nothing is missed even in
        containers this parser does not model explicitly. Kinds are ``mesh``,
        ``mesh:instancing``, ``mesh:cloth``, ``animation``, ``effect``, ``cfg``,
        ``texture:<slot>``, ``decal:<slot>`` and ``other:<CONFIGTYPE>``.
        """
        if self.root is None:
            return
        seen: set[tuple[str, str]] = set()

        def emit(kind: str, path: str) -> Iterator[tuple[str, str]]:
            key = (kind, path.lower())
            if path and key not in seen:
                seen.add(key)
                yield kind, path

        def walk(element: ET.Element, container: str) -> Iterator[tuple[str, str]]:
            for child in element:
                if child.tag == "m_Config":
                    config_kind = config_type(child)
                    file_name = _text(child, "m_FileName")
                    if file_name:
                        yield from emit(FILE_KINDS.get(config_kind, f"other:{config_kind or container}"), file_name)
                    instancing_model = _text(child, "m_InstancingModel")
                    if instancing_model:
                        yield from emit("mesh:instancing", instancing_model)
                    if config_kind == "MATERIAL":
                        prefix = "decal" if container == "m_TerrainDecals" else "texture"
                        for slot, tag in TEXTURE_FIELDS.items():
                            texture = _text(child, tag)
                            if texture:
                                yield from emit(f"{prefix}:{slot}", texture)
                yield from walk(child, container)

        # Top-level containers (m_Models, m_TerrainDecals, ...) set the context.
        for container in self.root:
            yield from walk(container, container.tag)

    def vertex_formats(self) -> set[str]:
        return {
            material.vertex_format for model in self.models for material in model.materials if material.vertex_format
        }

    def shader_indices(self) -> set[int]:
        return {material.shader_index for model in self.models for material in model.materials}


def parse_cfg(text: str, path: str = "") -> SCfg:
    root = ET.fromstring(text)
    if root.tag != "m_Config":
        raise ValueError(f"{path}: unexpected root element <{root.tag}>")
    models = root.find("m_Models")
    cfg = SCfg(
        path=path,
        center=_vec3(root, "m_Center"),
        extent=_vec3(root, "m_Extent"),
        radius=child_float(root, "m_Radius"),
        root=root,
    )
    cfg.models = [_model_of(model_element) for model_element in _configs(models, "MODEL")]
    cfg.models += [
        _instancing_model_of(model_element)
        for model_element in _configs(root.find("m_InstancingModels"), "INSTANCING_MODEL")
        if _text(model_element, "m_InstancingModel")
    ]
    clusters = [
        _billboard_cluster_of(cluster_element)
        for cluster_element in _configs(root.find("m_InstancingBBClusters"), "INSTANCING_BBCLUSTER")
    ]
    cfg.billboard_clusters = [cluster for cluster in clusters if cluster.billboards]
    cfg.cloths = [
        _cloth_of(cloth_element)
        for holder in (root.find("m_Clothes"), models)
        for cloth_element in _configs(holder, "CLOTH")
    ]
    cfg.effects = [
        _effect_of(effect_element, i)
        for i, effect_element in enumerate(_all_configs(root.find("m_Effects")))
        if config_type(effect_element) == "EFFECT"
    ]
    cfg.files = [_subfile_of(file_element) for file_element in _configs(root.find("m_Files"), "FILE")]
    cfg.lights = [_light_of(light_element) for light_element in _configs(root.find("m_Lights"), "LIGHT")]
    cfg.decals = [_decal_of(decal_element) for decal_element in _configs(root.find("m_TerrainDecals"), "TERRAINDECAL")]
    cfg.sequences = [
        _sequence_of(sequence_element) for sequence_element in _configs(root.find("m_Sequences"), "SEQUENCE")
    ]
    return cfg


def _all_configs(element: ET.Element | None) -> list[ET.Element]:
    """Every ``m_Config`` child (an effect's slot number counts the others too)."""
    return element.findall("m_Config") if element is not None else []


def parse_cfg_bytes(data: bytes, path: str = "") -> SCfg:
    return parse_cfg(data.decode("utf-8", "replace"), path)
