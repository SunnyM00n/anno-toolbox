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


def _text(el: ET.Element, tag: str, default: str = "") -> str:
    value = el.findtext(tag)
    return value.strip() if value else default


def child_float(el: ET.Element, tag: str, default: float = 0.0) -> float:
    """The number in a child element's text, ``default`` when the child or a number is missing."""
    try:
        return float(_text(el, tag, str(default)))
    except ValueError:
        return default


def _int(el: ET.Element, tag: str, default: int = 0) -> int:
    try:
        return int(_text(el, tag, str(default)))
    except ValueError:
        return default


def _vec2(el: ET.Element, prefix: str) -> tuple[float, float]:
    return child_float(el, f"{prefix}.x"), child_float(el, f"{prefix}.y")


def _vec3(el: ET.Element, prefix: str) -> Vec3:
    return child_float(el, f"{prefix}.x"), child_float(el, f"{prefix}.y"), child_float(el, f"{prefix}.z")


def _vec4(el: ET.Element, prefix: str, comps: str = "xyzw") -> Vec4:
    a, b, c, d = (child_float(el, f"{prefix}.{comp}") for comp in comps)
    return a, b, c, d


def _rgb(el: ET.Element, default: float = 1.0) -> Vec3:
    return (
        child_float(el, "m_Color.r", default),
        child_float(el, "m_Color.g", default),
        child_float(el, "m_Color.b", default),
    )


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def config_type(el: ET.Element) -> str:
    return _text(el, "m_ConfigType")


def _configs(el: ET.Element | None, kind: str) -> list[ET.Element]:
    """The ``m_Config`` children of ``el`` whose type is ``kind`` (none for a missing container)."""
    return [c for c in el.findall("m_Config") if config_type(c) == kind] if el is not None else []


@dataclass
class Transform:
    position: Vec3 = (0.0, 0.0, 0.0)
    rotation: Vec4 = (0.0, 0.0, 0.0, 1.0)
    scale: float = 1.0
    conditions: int = 0
    # further ORIENTATION_TRANSFORMs of the same node, in order (23 nodes carry two: the lobster
    # groups' second y offset, the weather station's second scale); written out as they come
    chain: list[Transform] = field(default_factory=list)

    @classmethod
    def from_element(cls, el: ET.Element) -> Transform:
        return cls(
            position=_vec3(el, "m_Position"),
            rotation=_vec4(el, "m_Rotation"),
            scale=child_float(el, "m_Scale", 1.0),
            conditions=_int(el, "m_Conditions"),
        )


# The transformers of a node


def _transforms(parent: ET.Element, kind: str) -> list[ET.Element]:
    return _configs(parent.find("m_Transformer"), kind)


def _transform_of(parent: ET.Element) -> Transform | None:
    found = [Transform.from_element(c) for c in _transforms(parent, "ORIENTATION_TRANSFORM")]
    if not found:
        return None
    found[0].chain = found[1:]
    return found[0]


def _damage_only(parent: ET.Element) -> bool:
    """Whether a DAMAGE_TRANSFORM hides the part on an intact object: it starts above damage 0 (the burning flames)
    or at scale 0. One from damage 0 at a size (the town centre's hologram, steam) shrinks it, the intact object has it."""
    return any(
        _int(d, "m_StartDamage") > 0 or child_float(d, "m_StartScale") <= 0
        for d in _transforms(parent, "DAMAGE_TRANSFORM")
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
class Spin:
    """An ``ANIMATION_TRANSFORM``: a constant turn of ``rotation`` (Euler radians) at ``speed``, while the object
    has one of the ``conditions`` bits (0: always); 117 has the same fields (engine-animation "Spinning parts")."""

    rotation: Vec3
    speed: float
    conditions: int = 0


def _spins_of(parent: ET.Element) -> list[Spin]:
    return [
        Spin(_vec3(c, "m_Rotation"), child_float(c, "m_RotationSpeed", 1.0), _int(c, "m_Conditions"))
        for c in _transforms(parent, "ANIMATION_TRANSFORM")
    ]


@dataclass
class ColorTransform:
    """A node's first COLOR_TRANSFORM, its first state: the colour it sets, and whether it multiplies
    the object's own colour (the owner's player colour) or replaces it."""

    color: Vec3 = (1.0, 1.0, 1.0)
    multiply: bool = False
    override: bool = False
    apply_always: bool = False
    alpha: float = 1.0  # m_Color.a: below 1 the object is drawn blended (materials-2070 6)

    def applies(self, shown: bool = False) -> bool:
        """Whether 2070 runs it: always on an object without ShowPlayerColor, else only with m_ApplyAlways."""
        return self.apply_always or not shown

    def takes_player_colour(self, shown: bool = False) -> bool:
        """Whether the node ends up in the owner's colour; ``shown``: the object's Mesh/ShowPlayerColor
        (engine-rendering "Player colour")."""
        if not self.applies(shown):
            return True
        return not self.override and (shown or self.multiply)


def object_color(ct: ColorTransform | None, player: Vec3, shown: bool = False) -> tuple[float, ...]:
    """The object colour (rgba) the engine hands a model's shaders (``cMtxMeshWorld``'s first row): the transform's
    colour, times the owner's ``player`` colour where it multiplies; without a transform white, or the player's with
    ``shown``. The scene's water colour (``m_ApplyWaterColor``) needs a world, so it is not applied here
    (rendering-2070.md 7.7)."""
    if ct is None or not ct.applies(shown):
        return (*(player if shown else (1.0, 1.0, 1.0)), 1.0)
    rgb = ct.color
    if ct.takes_player_colour(shown):
        rgb = tuple(c * p for c, p in zip(rgb, player, strict=True))
    return (*rgb, ct.alpha)


def _color_transform_of(parent: ET.Element) -> ColorTransform | None:
    found = _transforms(parent, "COLOR_TRANSFORM")
    if not found:
        return None
    c = found[0]
    state = c.find("m_State")
    override, always = _text(c, "m_OverrideColor") == "1", _text(c, "m_ApplyAlways") == "1"
    if state is None:
        return ColorTransform(override=override, apply_always=always)
    return ColorTransform(
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
    states = [(_int(s, "m_Condition"), _rgb(s, 0.0)) for s in found[0].findall("m_State")]
    off = [cond for cond, rgb in states if cond == 0 and not any(rgb)]
    on = [cond for cond, rgb in states if cond and any(rgb)]
    stray = [cond for cond, rgb in states if cond and not any(rgb)]
    return functools.reduce(operator.or_, on, 0) if off and on and not stray else 0


# The parts


@dataclass
class Material:
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
    def from_element(cls, el: ET.Element, owner: str = "MODEL") -> Material:
        textures = {k: _text(el, tag) for k, tag in TEXTURE_FIELDS.items()}
        return cls(
            name=_text(el, "m_Name"),
            shader_index=_int(el, "m_ShaderIndex"),
            vertex_format=_text(el, "m_VertexFormat"),
            textures={k: v for k, v in textures.items() if v},
            flags={tag[2:-7].lower(): _text(el, tag) == "1" for tag in FLAG_FIELDS},
            colors={c[2:-5].lower(): _vec4(el, c, "rgba") for c in COLOR_FIELDS},
            owner=owner,
            bones=_int(el, "m_NumBonesPerVertex"),
        )


def _materials_of(parent: ET.Element, owner: str = "MODEL") -> list[Material]:
    return [Material.from_element(c, owner) for c in _configs(parent.find("m_Materials"), "MATERIAL")]


@dataclass
class Animation:
    filename: str
    loop_count: int = 0
    scale: float = 1.0


@dataclass
class Model:
    filename: str
    materials: list[Material]
    transform: Transform | None
    animation_specs: list[Animation] = field(default_factory=list)
    tint: Vec3 | None = None  # an instancing model's colour (m_ColorR0 ...)
    color_transform: ColorTransform | None = None  # whether its Dye parts show the owner's colour
    visible_when: int = 0  # its VISIBILITY_TRANSFORM: the condition bits that show it; 0: always shown
    bone: tuple[int, int, int] | None = None  # (model, skeleton, bone) of its BONELINK_TRANSFORM: it hangs on a joint
    spins: list[Spin] = field(default_factory=list)  # its ANIMATION_TRANSFORMs


def _model_of(m: ET.Element) -> Model:
    anims = [
        Animation(_text(a, "m_FileName"), _int(a, "m_LoopCount"), child_float(a, "m_Scale") or 1.0)
        for a in _configs(m.find("m_Animations"), "ANIMATION")
        if _text(a, "m_FileName")
    ]
    return Model(
        _text(m, "m_FileName"),
        _materials_of(m),
        _transform_of(m),
        anims,
        color_transform=_color_transform_of(m),
        visible_when=_visibility_of(m),
        bone=_bone_of(m),
        spins=_spins_of(m),
    )


def _instancing_model_of(m: ET.Element) -> Model:
    """A farm field's crop or a tree's trunk: one mesh holding every plant, drawn at the origin. 117 has no
    instancing container, so it becomes a plain model with the first colour set as its tint."""
    model = Model(
        _text(m, "m_InstancingModel"),
        _materials_of(m, "INSTANCING_MODEL"),
        _transform_of(m),
        visible_when=_visibility_of(m),
    )
    if m.find("m_ColorR0") is not None:
        model.tint = (
            child_float(m, "m_ColorR0", 1.0),
            child_float(m, "m_ColorG0", 1.0),
            child_float(m, "m_ColorB0", 1.0),
        )
    return model


@dataclass
class Cloth:
    """A simulated cloth (flag, sail): CLOTH config in ``m_Clothes`` (older files: inside ``m_Models``)."""

    filename: str
    materials: list[Material]
    transform: Transform | None
    unique_simulation: bool = False
    gravity: float = 0.1
    local_wind_direction: float = 0.0
    line_size: float = 1.0
    color_transform: ColorTransform | None = None
    double_sided: bool = False  # m_IsDoubleSided: cloth.fx's two-pass technique
    ripple: Vec3 = (1.0, 0.0, 0.0)  # m_RippleSize, m_RippleNormalIntensity, m_RippleParallaxIntensity (cClothRipple)


def _cloth_of(m: ET.Element) -> Cloth:
    return Cloth(
        _text(m, "m_FileName"),
        _materials_of(m, "CLOTH"),
        _transform_of(m),
        unique_simulation=_text(m, "m_UniqueSimulation") == "1",
        gravity=child_float(m, "m_Gravity") or 0.1,
        local_wind_direction=child_float(m, "m_LocalWindDirection"),
        line_size=child_float(m, "m_LineSize") or 1.0,
        color_transform=_color_transform_of(m),
        double_sided=_text(m, "m_IsDoubleSided") == "1",
        ripple=tuple(child_float(m, f"m_Ripple{k}") for k in ("Size", "NormalIntensity", "ParallaxIntensity")),
    )


@dataclass
class Effect:
    """An ``EFFECT`` of a cfg's ``m_Effects``: the ``.efc`` file, where it sits and what switches it. ``index``
    is the slot a sequence's effect element names; ``bone`` (model, skeleton, bone) is set when it hangs on a
    skeleton's bone; ``visible_when`` is its VISIBILITY_TRANSFORM mask (0: none); ``damage`` is set when a
    DAMAGE_TRANSFORM hides it on an intact object (``_damage_only``); ``color`` is the first colour state without a condition."""

    filename: str
    transform: Transform | None
    index: int = 0
    bone: tuple[int, int, int] | None = None
    visible_when: int = 0
    damage: bool = False
    color: Vec3 | None = None
    spins: list[Spin] = field(default_factory=list)


def _effect_of(el: ET.Element, index: int) -> Effect:
    color = None
    for c in _transforms(el, "COLOR_TRANSFORM"):
        for state in c.findall("m_State"):
            if _int(state, "m_Condition") == 0:
                color = _rgb(state)
                break
    return Effect(
        _text(el, "m_FileName"),
        _transform_of(el),
        index=index,
        bone=_bone_of(el),
        visible_when=_visibility_of(el),
        damage=_damage_only(el),
        color=color,
        spins=_spins_of(el),
    )


@dataclass
class SubFile:
    """A nested config (props, trees, ...) placed with a transform; ``bone``, ``visible_when`` and ``spins`` as a
    ``Model``'s. ``damage``: a DAMAGE_TRANSFORM hides it on an intact object (the burning flames, ``_damage_only``)."""

    filename: str
    transform: Transform | None
    visible_when: int = 0
    bone: tuple[int, int, int] | None = None
    spins: list[Spin] = field(default_factory=list)
    damage: bool = False


def _subfile_of(f: ET.Element) -> SubFile:
    return SubFile(
        _text(f, "m_FileName"),
        _transform_of(f),
        visible_when=_visibility_of(f),
        bone=_bone_of(f),
        spins=_spins_of(f),
        damage=_damage_only(f),
    )


@dataclass
class Billboard:
    """One camera-facing card of an ``INSTANCING_BBCLUSTER``: where it hangs, how wide it is and
    which part of the leaf sheet it shows."""

    position: Vec3
    size: float
    texcoords: Vec4  # u0, v0, u1, v1


@dataclass
class BillboardCluster:
    """2070's foliage: a tree is a trunk mesh plus a cluster of leaf cards the engine turns towards the camera.
    There is no mesh for them - the cards are in the cfg (``billboards.banner_mesh`` builds one)."""

    billboards: list[Billboard]
    materials: list[Material]
    transform: Transform | None
    colors: list[Vec4] = field(default_factory=list)


def _billboard_cluster_of(el: ET.Element) -> BillboardCluster:
    """The cards of one INSTANCING_BBCLUSTER. Their fields are numbered in hex - ``m_BBPosX0`` to
    ``m_BBPosXf`` and on through ``m_BBPosX10``, which is the seventeenth and not the eleventh."""
    cards = []
    for i in range(_int(el, "m_BBClusterSize")):
        s = f"{i:x}"
        size = child_float(el, f"m_BBSize{s}")
        if size <= 0:
            continue
        cards.append(
            Billboard(
                position=(
                    child_float(el, f"m_BBPosX{s}"),
                    child_float(el, f"m_BBPosY{s}"),
                    child_float(el, f"m_BBPosZ{s}"),
                ),
                size=size,
                # X/Y is one corner of the sheet's rectangle, Z/W the other
                texcoords=_bb_texcoords(el, s),
            )
        )
    colors = [
        (
            child_float(el, f"m_ColorR{i}"),
            child_float(el, f"m_ColorG{i}"),
            child_float(el, f"m_ColorB{i}"),
            child_float(el, f"m_ColorA{i}"),
        )
        for i in range(_int(el, "m_ColorCount"))
    ]
    return BillboardCluster(cards, _materials_of(el, "INSTANCING_BBCLUSTER"), _transform_of(el), colors)


def _bb_texcoords(el: ET.Element, s: str) -> Vec4:
    return (
        child_float(el, f"m_BBTexX{s}"),
        child_float(el, f"m_BBTexY{s}"),
        child_float(el, f"m_BBTexZ{s}"),
        child_float(el, f"m_BBTexW{s}"),
    )


@dataclass
class Decal:
    extent: tuple[float, float]
    texcoords: Vec4
    materials: list[Material]
    transform: Transform | None = None


def _decal_of(d: ET.Element) -> Decal:
    return Decal(
        extent=_vec2(d, "m_Extend"),
        texcoords=_vec4(d, "m_TexCoords"),
        materials=_materials_of(d, "TERRAINDECAL"),
        transform=_transform_of(d),
    )


@dataclass
class Light:
    """A ``LIGHT`` of a cfg's ``m_Lights``: its values (``m_Type``, ``m_Diffuse.r`` ... as named in the cfg),
    where it sits, whether a colour transform switches it (2070 lights its buildings by state). ``lit_when``:
    the condition bits a colour transform makes it shine for (dark otherwise; ``_lit_when``), ``visible_when``:
    its VISIBILITY_TRANSFORM, ``bone`` and ``spins`` as a ``Model``'s."""

    values: dict[str, float]
    transform: Transform | None
    switched: bool
    bone: tuple[int, int, int] | None = None
    visible_when: int = 0
    lit_when: int = 0
    spins: list[Spin] = field(default_factory=list)
    damage: bool = False  # a DAMAGE_TRANSFORM hides it on an intact object (_damage_only)


def _light_of(li: ET.Element) -> Light:
    values = {c.tag[2:]: float(c.text) for c in li if c.text and c.tag != "m_ConfigType" and _is_number(c.text)}
    return Light(
        values,
        _transform_of(li),
        bool(_transforms(li, "COLOR_TRANSFORM")),
        bone=_bone_of(li),
        visible_when=_visibility_of(li),
        lit_when=_lit_when(li),
        spins=_spins_of(li),
        damage=_damage_only(li),
    )


@dataclass
class TrackElement:
    """One animation slot of a sequence track (type 0); effect slots are ignored."""

    model_id: int
    animation_id: int
    start_ms: int
    end_ms: int


@dataclass
class Sequence:
    sequence_id: int
    looped: bool
    speed: float
    tracks: list[list[TrackElement]] = field(default_factory=list)
    conditions: list[tuple[int, int, int]] = field(default_factory=list)  # condition tracks: (start ms, end ms, bits)
    effects: list[tuple[int, int, int]] = field(default_factory=list)  # effect slots: (start ms, end ms, effect index)

    @property
    def duration_ms(self) -> int:
        return max((e.end_ms for track in self.tracks for e in track), default=0)


def _sequence_of(el: ET.Element) -> Sequence:
    seq = Sequence(_int(el, "m_SequenceID"), _text(el, "m_Looped") == "1", child_float(el, "m_Speed") or 1.0)
    for track in el.findall("Track"):
        elements = []
        for te in track.findall("TrackElement"):
            kind = _text(te, "Type")
            start, end = _int(te, "m_StartTime"), _int(te, "m_EndTime")
            if kind == "2":  # the condition bits the object has while the sequence plays
                seq.conditions.append((start, end, _int(te, "m_Condition")))
            elif kind == "1":
                seq.effects.append((start, end, _int(te, "m_EffectID")))
            elif kind == "0":
                elements.append(TrackElement(_int(te, "m_ModelID"), _int(te, "m_AnimationID"), start, end))
        if elements:
            seq.tracks.append(elements)
    return seq


# The cfg


@dataclass
class Cfg:
    path: str
    models: list[Model] = field(default_factory=list)
    effects: list[Effect] = field(default_factory=list)
    files: list[SubFile] = field(default_factory=list)
    decals: list[Decal] = field(default_factory=list)
    lights: list[Light] = field(default_factory=list)
    sequences: list[Sequence] = field(default_factory=list)
    cloths: list[Cloth] = field(default_factory=list)
    billboard_clusters: list[BillboardCluster] = field(default_factory=list)
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

        def walk(el: ET.Element, container: str) -> Iterator[tuple[str, str]]:
            for child in el:
                if child.tag == "m_Config":
                    ctype = config_type(child)
                    fn = _text(child, "m_FileName")
                    if fn:
                        yield from emit(FILE_KINDS.get(ctype, f"other:{ctype or container}"), fn)
                    inst = _text(child, "m_InstancingModel")
                    if inst:
                        yield from emit("mesh:instancing", inst)
                    if ctype == "MATERIAL":
                        prefix = "decal" if container == "m_TerrainDecals" else "texture"
                        for slot, tag in TEXTURE_FIELDS.items():
                            tex = _text(child, tag)
                            if tex:
                                yield from emit(f"{prefix}:{slot}", tex)
                yield from walk(child, container)

        # Top-level containers (m_Models, m_TerrainDecals, ...) set the context.
        for container in self.root:
            yield from walk(container, container.tag)

    def vertex_formats(self) -> set[str]:
        return {mat.vertex_format for m in self.models for mat in m.materials if mat.vertex_format}

    def shader_indices(self) -> set[int]:
        return {mat.shader_index for m in self.models for mat in m.materials}


def parse_cfg(text: str, path: str = "") -> Cfg:
    root = ET.fromstring(text)
    if root.tag != "m_Config":
        raise ValueError(f"{path}: unexpected root element <{root.tag}>")
    models = root.find("m_Models")
    cfg = Cfg(
        path=path,
        center=_vec3(root, "m_Center"),
        extent=_vec3(root, "m_Extent"),
        radius=child_float(root, "m_Radius"),
        root=root,
    )
    cfg.models = [_model_of(m) for m in _configs(models, "MODEL")]
    cfg.models += [
        _instancing_model_of(m)
        for m in _configs(root.find("m_InstancingModels"), "INSTANCING_MODEL")
        if _text(m, "m_InstancingModel")
    ]
    clusters = [_billboard_cluster_of(c) for c in _configs(root.find("m_InstancingBBClusters"), "INSTANCING_BBCLUSTER")]
    cfg.billboard_clusters = [c for c in clusters if c.billboards]
    cfg.cloths = [_cloth_of(m) for holder in (root.find("m_Clothes"), models) for m in _configs(holder, "CLOTH")]
    cfg.effects = [
        _effect_of(e, i) for i, e in enumerate(_all_configs(root.find("m_Effects"))) if config_type(e) == "EFFECT"
    ]
    cfg.files = [_subfile_of(f) for f in _configs(root.find("m_Files"), "FILE")]
    cfg.lights = [_light_of(li) for li in _configs(root.find("m_Lights"), "LIGHT")]
    cfg.decals = [_decal_of(d) for d in _configs(root.find("m_TerrainDecals"), "TERRAINDECAL")]
    cfg.sequences = [_sequence_of(s) for s in _configs(root.find("m_Sequences"), "SEQUENCE")]
    return cfg


def _all_configs(el: ET.Element | None) -> list[ET.Element]:
    """Every ``m_Config`` child (an effect's slot number counts the others too)."""
    return el.findall("m_Config") if el is not None else []


def parse_cfg_bytes(data: bytes, path: str = "") -> Cfg:
    return parse_cfg(data.decode("utf-8", "replace"), path)
