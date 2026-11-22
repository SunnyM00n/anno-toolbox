"""What the model view draws of a model config or a mesh file, in the model's own space: its parts (a mesh with a
material per submesh group and a place), the effects it places, its sequences and each model's animations.

``build(files, path, options)`` is the entry, by the game's family: the newer games' configs (117, 1800, 2205) read
with ``models.configtree`` (the baked ``shared/`` copy the game reads in place of a ``data/`` config, levels of
detail, materials per level), 2070's with ``models.cfg2070`` and ``modelread``, 1404's and 1701's Granny models with
``modelread`` and ``othergames.grannymodels``; an ``.rdm`` alone draws as it is. A part that spins or hangs on a
joint carries its ``motion`` (``viewer.motion``). docs/tool-suite.md "The viewer".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

import numpy as np

from ..infra.i18n import tr
from ..models import cfg2070, configtree, fileresolve, modelread, shader2070
from ..models.configtree import SClip, SNode, SPlacement
from ..models.fileresolve import CResolver
from ..othergames import installs
from ..render import clothsim, decalmesh
from ..render.modellibrary import FOAM_SHADER
from ..sources.overlay import normalize
from .clothplay import SClothPart, cloth_part, cloth_part2070
from .motion import SAlign, SBob, SLinked, moves, rest, spin2070_of, spin_of

MODEL_SHADER, WATER_SHADER, BANNER_SHADER = 8, 7, 28  # the island editor's model shaders (render.shaders)
MAX_DEPTH = 4  # nested configs followed this deep
GRANNY = installs.keys(lambda g: g.granny)  # 1404 and 1701: Granny models
SLOTS_2070 = {"height": "parallax"}  # a 2070 texture slot -> the flag that switches it (the slot's own name else)


@dataclass(frozen=True)
class SOptions:
    lod: int = 0  # the level of detail drawn (clamped to what a mesh has)
    shared: bool = True  # the newer games: read a config's baked shared/ copy when there is one, as the game does
    follow: bool = True  # draw the nested configs (the files check only looks their names up)


@dataclass(frozen=True)
class SMaterial:
    """How one submesh group draws: its textures (game paths, "" for none), tint, shader and, for the property panel,
    the config node it comes from; its metal map (the forward games' own shaders read it) and a decal's opacity
    (rendering/material-fields.md)."""

    name: str = ""
    diffuse: str = ""
    normal: str = ""
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    shader: int = MODEL_SHADER
    node: str = ""
    hidden: bool = False  # a terrain cut-out: drawn by the game as a hole, not as a surface
    metal: str = ""  # R metallic, A ambient occlusion
    opacity: float = 1.0  # cOpacity of the decal-like shaders
    values: tuple = ()  # the MATERIAL node's fields as (name, value), for the game's own shaders
    foam: tuple = ()  # a foam sheet's two scroll speeds and opacity (``modelread.foam_values``)


@dataclass
class SPart:
    """One mesh in the model: its file, its place, and per submesh group key (the file's material number, or a
    Granny group's material name) the material; ``node`` is the config node it was read from (``Files/1/Models/0``
    for a nested config's), ``animations`` the clips of that model in the config's order; ``motion`` the steps to
    its place when it moves (``matrix`` is then its place at rest); ``when`` the visibility masks it is shown under
    (its own and its nested files', each needing a bit on: ``viewer.states``), ``times`` its and their
    ``DAYNIGHT_TRANSFORM``s (time slice, condition bits, fade time)."""

    node: str
    mesh: str
    matrix: np.ndarray
    materials: dict = field(default_factory=dict)
    default: SMaterial = field(default_factory=SMaterial)
    animations: list[SClip] = field(default_factory=list)
    lods: int = 1
    banner: dict | None = None  # a leaf cluster's cards instead of a mesh (2070's trees)
    motion: tuple = ()
    when: tuple[int, ...] = ()
    times: tuple[tuple[int, int, float], ...] = ()
    cloth: object = None  # a CLOTH part's simulation fields (``viewer.clothplay.SClothPart``)

    def material(self, key) -> SMaterial:
        return self.materials.get(key, self.default)


@dataclass
class SPlacedEffect:
    """A PARTICLE node: its file, place and motion, the tint, a newer game's switches (``SoftParticlesEnabled``,
    ``ReceiveShadows``, ``UseDepthBias``; shader-compile.md section 11 "Soft particles") and states (as ``SPart``)."""

    node: str
    file: str
    matrix: np.ndarray
    tint: tuple[float, float, float] = (1.0, 1.0, 1.0)
    alpha: float = 1.0
    motion: tuple = ()
    soft: bool = False
    shadows: bool = False
    depth_bias: bool = False
    when: tuple[int, ...] = ()
    times: tuple[tuple[int, int, float], ...] = ()
    started: int = -1  # the slot a sequence's effect element starts it by (-1: no sequence starts it, it runs on)


@dataclass
class SPlacedLight:
    """A LIGHT: its colour (``Diffuse`` times its strength), reach and falloff, its flicker (``ColorGraph``: (time 0..1,
    r, g, b, a) over ``duration`` seconds); a newer game's ``Type`` (0 point, 2 spot, 5/6/8 area), cone (``Theta``,
    ``Phi``; 2070's are half-angles), size (``Width``, ``Height``), 2070's ``Volume`` and flags, read by the game-shader
    look; its states (as ``SPart``)."""

    node: str
    matrix: np.ndarray
    color: tuple[float, float, float]
    range: float = 1.0
    falloff: float = 1.0
    graph: list = field(default_factory=list)
    duration: float = 0.0
    motion: tuple = ()
    kind: int = 0
    cone: tuple = (0.5, 0.8)
    size: tuple = (1.0, 1.0)
    double_sided: bool = False
    no_fog: bool = False
    clustered: bool = True
    when: tuple[int, ...] = ()
    times: tuple[tuple[int, int, float], ...] = ()
    volume: float = 0.0

    def color_at(self, seconds: float) -> tuple[float, float, float]:
        """The colour at a time: the graph's key before and after, blended (the strength in its alpha)."""
        if not self.graph or self.duration <= 0:
            return self.color
        t = (seconds / self.duration) % 1.0
        keys = sorted(self.graph)
        after = next((k for k in keys if k[0] >= t), keys[-1])
        before = next((k for k in reversed(keys) if k[0] <= t), keys[0])
        span = after[0] - before[0]
        f = (t - before[0]) / span if span > 1e-9 else 0.0
        r, g, b, a = (before[i] + (after[i] - before[i]) * f for i in range(1, 5))
        return tuple(c * k * a for c, k in zip(self.color, (r, g, b), strict=True))


@dataclass
class SPlacedDecal:
    """A DECAL: a rectangle of half sizes ``extents`` (x, z) laid on the ground under its place, drawn with its
    material over what is below, in the pass its ``Type`` and ``OnStreets`` pick (``render.decalmesh.pass_of``); its
    states (as ``SPart``)."""

    node: str
    matrix: np.ndarray
    extents: tuple[float, float]
    material: SMaterial
    motion: tuple = ()
    kind: str = "Terrain"
    on_streets: bool = True  # 2070's decals lie over its streets (rendering/mapping.md "Decals and ground pieces")
    when: tuple[int, ...] = ()
    times: tuple[tuple[int, int, float], ...] = ()


@dataclass
class CScene:
    """A model as the view draws it. ``config`` is the file read (a ``shared/`` copy, say), ``tree`` its parts as the
    object builder shows them; ``notes`` say what is left out and why; ``resolve`` keeps every file name looked up
    and what it found (``fileresolve.CResolver``; its ``missing`` is the Checks panel's list)."""

    m_path: str
    m_game: str
    m_config: str = ""
    m_tree: SNode | None = None
    m_parts: list[SPart] = field(default_factory=list)
    m_effects: list[SPlacedEffect] = field(default_factory=list)
    m_lights: list[SPlacedLight] = field(default_factory=list)
    m_decals: list[SPlacedDecal] = field(default_factory=list)
    m_sequences: list[configtree.SSequence] = field(default_factory=list)
    m_when_of: dict[str, tuple[int, ...]] = field(default_factory=dict)  # a nested file's prefix -> its masks
    m_times_of: dict[str, tuple] = field(default_factory=dict)  # a nested file's prefix -> its DAYNIGHT_TRANSFORMs
    m_notes: list[str] = field(default_factory=list)
    m_lods: int = 1
    m_resolve: CResolver | None = None

    def note(self, text: str) -> None:
        if text not in self.m_notes:
            self.m_notes.append(text)


def matrix(placement: SPlacement | modelread.SPlace) -> np.ndarray:
    """A 4x4 placement: translate, rotate, scale per axis."""
    from ..render.modellibrary import trs

    return trs(placement.position, placement.rotation, placement.scale).astype(np.float64)


def placed(steps: tuple) -> tuple[np.ndarray, tuple]:
    """(the matrix at rest, the steps when they move, else ())."""
    return rest(steps), (steps if moves(steps) else ())


def _steps(parent_steps: tuple, node: SNode, prefix: str, scene: CScene) -> tuple:
    """The steps to a newer game's part: the parent's, its orientations, then each ANIMATION_TRANSFORM's alignment,
    position motion and spin (the engine moves them to the end, engine-animation "Spinning parts"); on a joint the
    joint first."""
    local = matrix(configtree.placement(node))
    anims = configtree.animation_transforms(node)
    oriented = any(
        t.kind == "ORIENTATION_TRANSFORM" and not t.number("Conditions") for t in configtree.transforms(node)
    )
    if not oriented and any(any(a.offset) for a in anims):  # the offset is the motion's, not a place
        local = np.identity(4)
    own_steps: list = [local]
    for spec in anims:
        if 1 <= spec.alignment <= 7:  # the light, the wind, the camera, the clock (motion.SAlign)
            own_steps.append(SAlign(spec.alignment, local, spec.conditions))
        if any(spec.offset):
            own_steps.append(SBob(spec.offset, spec.position_speed, spec.position_rotate, local, spec.conditions))
        spin = spin_of(configtree.SSpinSpec(spec.rotation, spec.speed, spec.oscillation, spec.conditions))
        if spin is not None:
            own_steps.append(replace(spin, when=spec.conditions))
    link = configtree.bone_link(node)
    if link is not None:
        return (SLinked(f"{prefix}Models/{link.model}", link.hash, link.index), *own_steps)
    return (*parent_steps, *own_steps)


def in_scene(node: SNode) -> bool:
    """Whether a newer game's part is drawn: shown always, or in a state (a visibility mask: ``viewer.states``)."""
    return configtree.shown_always(node, on_joints=True) or bool(configtree.shown_when(node))


def started_slots(scene: CScene, depth: int) -> set[int]:
    """The effect slots the config's own sequences start (their type 1 elements); a nested file's effects are started
    by sequences the view does not play, so none of them (``SPlacedEffect.started``)."""
    if depth:
        return set()
    return {
        element.effect for seq in scene.m_sequences for track in seq.tracks for element in track if element.kind == 1
    }


def states_of(node: SNode, prefix: str, scene: CScene) -> dict:
    """A part's visibility masks and time slices, its nested files' first (``SPart.when``, ``SPart.times``)."""
    mask, time = configtree.shown_when(node), configtree.day_night(node)
    return {"when": (*scene.m_when_of.get(prefix, ()), *((mask,) if mask else ())),
            "times": (*scene.m_times_of.get(prefix, ()), *((time,) if time else ()))}  # fmt: skip


def build(files, path: str, game: str, options: SOptions | None = None) -> CScene:
    """The scene of a config (or an ``.rdm``) of ``files`` (a ``sources.gametree.CGameTree``)."""
    path, options = normalize(path), options or SOptions()
    scene = CScene(path, game, m_resolve=CResolver(files, game))
    if path.endswith(".rdm"):
        scene.m_parts.append(SPart("", path, np.identity(4)))
        scene.m_config = path
        return scene
    if game == "anno1701":
        _mcf1701(files, path, scene)
    elif game in GRANNY or game == "anno2070":  # 1404: 2070's config format over Granny meshes
        _cfg2070(files, path, (), "", scene, 0, options.follow, granny=game in GRANNY)
        scene.m_tree = configtree.read_tree(files.read(path).decode("utf-8", "replace"))
    else:
        _new_engine(files, path, (), "", scene, options, 0)
    scene.m_config = scene.m_config or path
    return scene


# The newer games


def read_config(files, path: str, options: SOptions) -> tuple[str, SNode]:
    """The config the game reads for ``path`` (its ``shared/`` copy when ``options.shared`` and one exists) and its tree."""
    shared = configtree.shared_config(path) if options.shared else ""
    read = shared if shared and files.exists(shared) else path
    return read, configtree.read_tree(files.read(read).decode("utf-8", "replace"))


def _new_engine(
    files, path: str, parent_steps: tuple, prefix: str, scene: CScene, options: SOptions, depth: int
) -> None:
    read, tree = read_config(files, path, options)
    if depth == 0:
        scene.m_config, scene.m_tree, scene.m_sequences = read, tree, configtree.sequences(tree)
    for node in tree.section_of("Models") + tree.section_of("Clothes"):
        if not in_scene(node):
            continue
        mesh = scene.m_resolve.mesh(node.get("FileName"), read, prefix + node.path)
        if not mesh:
            if node.get("FileName"):
                scene.note(
                    tr(
                        "viewer.where_file_not_files",
                        where=f"{prefix}{node.path}",
                        file=configtree.mesh_file(node),
                    )
                )
            continue
        lods = configtree.lod_files(mesh, files.exists)
        level = min(options.lod, len(lods) - 1)
        scene.m_lods = max(scene.m_lods, len(lods))
        rest_matrix, motion = placed(_steps(parent_steps, node, prefix, scene))
        part = SPart(prefix + node.path, lods[level], rest_matrix, lods=len(lods), motion=motion,
                    **states_of(node, prefix, scene))  # fmt: skip
        part.materials = _materials(node, level, prefix, scene.m_resolve, read)
        part.default = next(iter(part.materials.values()), SMaterial())
        part.animations = [_clip(c, scene.m_resolve, read, prefix + node.path) for c in configtree.animations(node)]
        if node.section == "Clothes":
            part.cloth = cloth_part(node, tree, lambda c: rest(_steps(parent_steps, c, prefix, scene)))
        scene.m_parts.append(part)
    _lights_and_decals(tree, parent_steps, prefix, scene, read)
    started = started_slots(scene, depth)
    for slot, node in enumerate(tree.section_of("Particles")):  # a ParticleID is the place in Particles
        name = normalize(node.get("FileName"))
        if not name.endswith(".rdp") or not in_scene(node):
            continue
        if scene.m_resolve.effect(name, read, prefix + node.path):
            rest_matrix, motion = placed(_steps(parent_steps, node, prefix, scene))
            on = {
                k: node.get(k, "0").strip() == "1" for k in ("SoftParticlesEnabled", "ReceiveShadows", "UseDepthBias")
            }
            scene.m_effects.append(
                SPlacedEffect(
                    prefix + node.path,
                    name,
                    rest_matrix,
                    motion=motion,
                    soft=on["SoftParticlesEnabled"],
                    shadows=on["ReceiveShadows"],
                    depth_bias=on["UseDepthBias"],
                    started=slot if slot in started else -1,
                    **states_of(node, prefix, scene),
                )  # fmt: skip
            )
    follow = (lambda p, m, pre: _new_engine(files, p, m, pre, scene, options, depth + 1)) if options.follow else None
    _nested(tree, parent_steps, prefix, scene, depth, follow, (read, options.shared))


def _nested(tree: SNode, parent_steps: tuple, prefix: str, scene: CScene, depth: int, follow, owner: tuple) -> None:
    """The configs a newer game's config nests, each looked up (``owner``: the config read and whether the game reads
    the baked copies) and, with ``follow``, drawn at its place."""
    for node in tree.section_of("Files"):
        name = normalize(node.get("FileName"))
        if not name.endswith(".cfg") or not in_scene(node):
            continue
        found = scene.m_resolve.config(name, owner[0], prefix + node.path, prefers_shared=owner[1])
        states, inner = states_of(node, prefix, scene), f"{prefix}{node.path}/"
        scene.m_when_of[inner], scene.m_times_of[inner] = states["when"], states["times"]  # its parts need them too
        if follow is None:
            continue
        if depth >= MAX_DEPTH or not found:
            _not_followed(scene, f"{prefix}{node.path}", name, found)
            continue
        try:
            follow(found, _steps(parent_steps, node, prefix, scene), f"{prefix}{node.path}/")
        except Exception as exc:  # a nested config that does not read: the rest of the model still draws
            scene.note(
                tr(
                    "viewer.where_file_not_read_kind",
                    where=f"{prefix}{node.path}",
                    file=name,
                    kind=type(exc).__name__,
                    error=exc,
                )
            )


def _not_followed(scene: CScene, where: str, name: str, found: str) -> None:
    """Say why a nested config is not drawn: not in the files, or nested too deep."""
    if found:
        scene.note(tr("viewer.nests_too_deep", where=where, file=name))
    else:
        scene.note(tr("viewer.where_file_not_files", where=where, file=name))


def _materials(model: SNode, level: int, prefix: str, resolve: CResolver, owner: str) -> dict[int, SMaterial]:
    """The file's material number -> how it draws, at a level of detail (``MaterialLODInfos`` maps the numbers)."""
    return {k: material_of(node, prefix, resolve, owner) for k, node in configtree.materials_at(model, level).items()}


def _clip(clip: SClip, resolve: CResolver, owner: str, node: str) -> SClip:
    """An animation with its file as the game loads it (the name as written when the game has none: it says so)."""
    return SClip(resolve.mesh(clip.file, owner, node, kind="animation") or clip.file, clip.scale, clip.loops)


def material_of(node: SNode, prefix: str = "", resolve: CResolver | None = None, owner: str = "") -> SMaterial:
    """A newer game's MATERIAL node as the view draws it: its maps as the files the game loads. ``resolve`` looks
    every texture slot up (the ones the view does not draw too) and keeps what it found; without it the names are
    only mapped (``fileresolve.texture_name``)."""
    fields, game = configtree.material_fields(node), resolve.m_game if resolve is not None else "anno117"
    texture = lambda name: fileresolve.texture_name(name, game)  # noqa: E731
    if resolve is not None:
        texture = fileresolve.view_textures(fileresolve.material_textures(node, resolve, owner, prefix + node.path))
    return SMaterial(
        fields.name,
        texture(fields.diffuse),
        texture(fields.normal),
        fields.color,
        fields.shader,
        prefix + node.path,
        metal=texture(fields.metal),
        opacity=fields.opacity,
        values=tuple(node.fields),
    )


def color_graph(node: SNode) -> list[tuple[float, float, float, float, float]]:
    """A LIGHT's ``ColorGraph`` keys as (time 0..1, r, g, b, a)."""
    block = node.blocks.get("ColorGraph")
    out = []
    for key in block.findall("i") if block is not None else []:
        values = {child.tag: float(child.text or 0) for child in key if (child.text or "").strip()}
        out.append((values.get("t", 0.0), *(values.get(f"c.{child}", 1.0) for child in "rgba")))
    return out


def _lights_and_decals(tree: SNode, parent_steps: tuple, prefix: str, scene: CScene, owner: str = "") -> None:
    """A newer game's LIGHTs (a point light: ``Diffuse`` x its alpha, ``Range``, ``Falloff``) and DECALs."""
    for node in tree.section_of("Lights"):
        if not in_scene(node):
            continue
        rest_matrix, motion = placed(_steps(parent_steps, node, prefix, scene))
        strength = node.number("Diffuse.a", 1.0)
        color = tuple(node.number(f"Diffuse.{channel}", 1.0) * strength for channel in "rgb")
        light = SPlacedLight(prefix + node.path, rest_matrix, color, node.number("Range", 1.0),
                            node.number("Falloff", 1.0), color_graph(node), node.number("GraphDuration"), motion,
                            int(node.number("Type")), (node.number("Theta", 0.5), node.number("Phi", 0.8)),
                            (node.number("Width", 1.0), node.number("Height", 1.0)), node.number("DoubleSided") != 0,
                            node.number("NoFog") != 0, node.number("ClusteredShading", 1.0) != 0,
                            **states_of(node, prefix, scene))  # fmt: skip
        scene.m_lights.append(light)
    for node in tree.section_of("Decals"):
        materials = node.section_of("Materials")
        if not materials or not in_scene(node):
            continue
        rest_matrix, motion = placed(_steps(parent_steps, node, prefix, scene))
        extents = (node.number("Extents.x"), node.number("Extents.z"))
        if min(extents) > 0:
            material = material_of(materials[0], prefix, scene.m_resolve, owner)
            kind, on_streets = decalmesh.read_decal(node)
            decal = SPlacedDecal(prefix + node.path, rest_matrix, extents, material, motion, kind, on_streets,
                                **states_of(node, prefix, scene))  # fmt: skip
            scene.m_decals.append(decal)


# 2070


def _texture_2070(scene: CScene, owner: str):
    """2070's, 1404's and 1701's texture names as the files the view loads (``CResolver.shown``), each lookup kept."""
    return lambda name: scene.m_resolve.shown(name, owner=owner)


def _other_slots2070(materials, resolve: CResolver, owner: str, node: str) -> None:
    """The texture slots of 2070's materials the view does not draw (environment, height, mask, ripples), looked
    up for the Checks panel and the files check; a slot its flag switches off is kept as off."""
    for i, material in enumerate(materials):
        for slot, name in material.textures.items():
            if slot not in ("diffuse", "normal"):
                is_off = not material.flags.get(SLOTS_2070.get(slot, slot), True)
                resolve.texture_reference(name, slot, owner, f"{node}/Materials/{i}", is_off=is_off)


def _steps2070(parent_steps: tuple, item, prefix: str) -> tuple:
    """The steps to a 2070 part: the parent's (or the joint it hangs on), its transform chain, its spins."""
    own_steps = [matrix(modelread.place_of(item.transform))]
    own_steps += [
        spin_step
        for spin in getattr(item, "spins", [])
        if not spin.conditions and (spin_step := spin2070_of(spin)) is not None
    ]
    if getattr(item, "bone", None) is not None:
        model, _skeleton, bone = item.bone
        return (SLinked(f"{prefix}Models/{model}", index=bone), *own_steps)
    return (*parent_steps, *own_steps)


def _when2070(item, prefix: str, scene: CScene) -> tuple[int, ...]:
    """A 2070 or 1404 item's visibility masks: its nested files' and its own ``VISIBILITY_TRANSFORM`` (``SPart.when``)."""
    own_mask = getattr(item, "visible_when", 0)
    return (*scene.m_when_of.get(prefix, ()), *((own_mask,) if own_mask else ()))


def _part2070(
    model, node: str, parent_steps: tuple, prefix: str, path: str, scene: CScene, texture, granny=False
) -> SPart | None:
    """A 2070 MODEL or CLOTH (``cfg2070.SCloth``: its rest pose, the simulation's vertices are not the viewer's) as a
    part; None, noted, when its mesh is not in the files. A 1404 one (``granny``) keys its materials by name, as its
    Granny mesh names its groups, tinted by an instancing model's colour."""
    resolve = scene.m_resolve
    mesh = resolve.mesh(model.filename, path, node)
    if not mesh:
        scene.note(tr("viewer.where_file_not_files", where=node, file=normalize(model.filename)))
        return None
    surfaces = modelread.surfaces(model.materials, texture)
    _other_slots2070(model.materials, resolve, path, node)
    foam = modelread.mesh_foam_values(resolve.m_files.read, mesh) if any(s.kind == "foam" for s in surfaces) else ()
    rest_matrix, motion = placed(_steps2070(parent_steps, model, prefix))
    part = SPart(node, mesh, rest_matrix, motion=motion, when=_when2070(model, prefix, scene))
    cloth = model if isinstance(model, cfg2070.SCloth) else None
    tint = tuple(getattr(model, "tint", None) or (1.0, 1.0, 1.0))
    part.materials = {
        s.name.lower() if granny else i: SMaterial(
            s.name, s.diffuse, s.normal, tint if granny else s.color,
            WATER_SHADER if s.kind == "water" and not granny else FOAM_SHADER if s.kind == "foam" and not granny
            else MODEL_SHADER, f"{node}/Materials/{i}", s.kind == "cutout" and not granny,
            values=shader2070.material_values(model.materials[i], texture, model.color_transform, cloth),
            foam=foam if s.kind == "foam" and not granny else ())
        for i, s in enumerate(surfaces)
    }  # fmt: skip
    part.default = next(iter(part.materials.values()), SMaterial()) if granny else part.materials.get(0, SMaterial())
    part.cloth = cloth_part2070(cloth) if cloth is not None else None
    part.animations = [
        SClip(resolve.mesh(a.filename, path, node, "animation") or normalize(a.filename), a.scale, a.loop_count)
        for a in getattr(model, "animation_specs", ())
    ]
    return part


def _cfg2070(
    files, path: str, parent_steps: tuple, prefix: str, scene: CScene, depth: int, follow=True, granny=False
) -> None:
    """A 2070 config (or 1404's, ``granny``: the same format over Granny meshes) and the ones it nests. A part, effect
    or nested file a ``VISIBILITY_TRANSFORM`` shows only in a state carries the mask (``SPart.when``)."""
    cfg = cfg2070.parse_cfg(files.read(path).decode("utf-8", "replace"), path)
    texture, resolve = _texture_2070(scene, path), scene.m_resolve
    if depth == 0:
        scene.m_sequences = configtree.sequences(configtree.read_tree(files.read(path).decode("utf-8", "replace")))
    nodes = [(f"{prefix}Models/{k}", model) for k, model in enumerate(cfg.models) if model.filename]
    nodes += [(f"{prefix}Clothes/{k}", cloth) for k, cloth in enumerate(cfg.cloths) if cloth.filename]
    for node, model in nodes:
        part = _part2070(model, node, parent_steps, prefix, path, scene, texture, granny)
        if part is not None:
            scene.m_parts.append(part)
    for k, cluster in enumerate(cfg.billboard_clusters):
        scene.m_parts.append(_leaves(cluster, k, rest(parent_steps), prefix, texture))
        scene.m_parts[-1].when = _when2070(None, prefix, scene)
    _lights_and_decals2070(cfg, parent_steps, prefix, scene, texture)
    started = started_slots(scene, depth)
    for k, e in enumerate(cfg.effects):
        if e.filename and not e.damage and resolve.effect(e.filename, path, f"{prefix}Effects/{k}"):
            rest_matrix, motion = placed(_steps2070(parent_steps, e, prefix))
            node, when = f"{prefix}Effects/{k}", _when2070(e, prefix, scene)
            slot = e.index if e.index in started else -1  # 2070's m_EffectID names an effect's m_Index
            scene.m_effects.append(SPlacedEffect(node, normalize(e.filename), rest_matrix, motion=motion, when=when,
                                                 started=slot))  # fmt: skip
    for k, f in enumerate(cfg.files):
        if not f.filename or f.damage:
            continue
        name = resolve.config(f.filename, path, f"{prefix}Files/{k}")
        scene.m_when_of[f"{prefix}Files/{k}/"] = _when2070(f, prefix, scene)  # its parts need its mask too
        if not follow:
            continue
        if not name or depth >= MAX_DEPTH:
            _not_followed(scene, f"{prefix}Files/{k}", normalize(f.filename), name)
            continue
        try:
            _cfg2070(
                files, name, _steps2070(parent_steps, f, prefix), f"{prefix}Files/{k}/", scene, depth + 1, True, granny
            )
        except Exception as exc:  # a nested config that does not read: the rest still draws
            scene.note(
                tr(
                    "viewer.where_file_not_read_kind",
                    where=f"{prefix}Files/{k}",
                    file=name,
                    kind=type(exc).__name__,
                    error=exc,
                )
            )


def _lights_and_decals2070(cfg, parent_steps: tuple, prefix: str, scene: CScene, texture) -> None:
    """2070's lights (always shining ones only: a switched one waits for a state) and its terrain decals."""
    for k, light in enumerate(cfg.lights):
        if light.visible_when or light.lit_when or light.damage:
            continue
        values = light.values
        rest_matrix, motion = placed(_steps2070(parent_steps, light, prefix))
        color = tuple(values.get(f"Diffuse.{channel}", 1.0) * values.get("Diffuse.a", 1.0) for channel in "rgb")
        light_at = SPlacedLight(f"{prefix}Lights/{k}", rest_matrix, color, values.get("Range", 1.0), values.get("Falloff", 1.0),
                               kind=int(values.get("Type", 0)), cone=(values.get("Theta", 0.5), values.get("Phi", 0.8)),
                               volume=values.get("Volume", 0.0), motion=motion)  # fmt: skip
        scene.m_lights.append(light_at)
    for k, decal in enumerate(cfg.decals):
        surface = next(iter(modelread.surfaces(decal.materials, texture)), None)
        if surface is None or min(decal.extent) <= 0:
            continue
        rest_matrix, motion = placed((*parent_steps, matrix(modelread.place_of(decal.transform))))
        material = SMaterial(
            surface.name, surface.diffuse, surface.normal, surface.color, 1, f"{prefix}TerrainDecals/{k}"
        )
        scene.m_decals.append(SPlacedDecal(f"{prefix}TerrainDecals/{k}", rest_matrix, decal.extent, material, motion))


def _leaves(cluster, k: int, place: np.ndarray, prefix: str, texture) -> SPart:
    """A tree's leaf cluster as banner cards: per corner the card's centre, the corner offset, the outward direction."""
    cards: dict[str, list] = {"positions": [], "corners": [], "radial": [], "uvs": [], "indices": []}
    for card_index, card in enumerate(modelread.cards(cluster)):
        for (sign_x, sign_y), (u, v) in zip(modelread.CORNERS, card.uvs, strict=True):
            cards["positions"] += card.position
            cards["corners"] += [sign_x * card.reach, sign_y * card.reach]
            cards["radial"] += card.radial
            cards["uvs"] += [u, v]
        cards["indices"] += [
            4 * card_index,
            4 * card_index + 1,
            4 * card_index + 2,
            4 * card_index,
            4 * card_index + 2,
            4 * card_index + 3,
        ]
    diffuse = next(iter(modelread.surfaces(cluster.materials, texture)), modelread.SSurface("", "", "")).diffuse
    node = f"{prefix}BillboardClusters/{k}"
    part = SPart(node, f"{node}|leaves", place @ matrix(modelread.place_of(cluster.transform)), banner=cards)
    part.default = SMaterial("leaves", diffuse, "", (1.0, 1.0, 1.0), BANNER_SHADER, node)
    return part


# 1701: mesh configs over Granny meshes (1404's configs are 2070's format: ``_cfg2070``)


def _clip1701(clip, resolve: CResolver, owner: str, node: str) -> SClip:
    """An ``m_Animation`` as a clip: its speed (1000 the clip's own) as the slowing, a clip that does not loop
    played once."""
    scale = 1000.0 / clip.speed if clip.speed > 0 else 0.0
    return SClip(resolve.mesh(clip.file, owner, node, kind="animation") or clip.file, scale, 0 if clip.loop else 1)


def _cloth1701(fields: dict) -> SClothPart:
    """A class 9 cloth: its gravity and wind factors on the simulation's defaults (granny.md "Cloth")."""
    base = clothsim.SClothParams()
    return SClothPart(replace(base, gravity=base.gravity * fields.get("GravityFactor", 1.0),
                             wind_strength=base.wind_strength * fields.get("WindFactor", 1.0)))  # fmt: skip


def _mcf1701(files, path: str, scene: CScene) -> None:
    """A 1701 ``.mcf`` and the files it nests: its pieces with their clips and cloth, its effects; a condition
    trigger (``IS_DAMAGED``) or a named effect start parameter is a state bit, in the order met, named in the notes."""
    from ..othergames.grannymodels import read_1701_model
    from ..othergames.isd1701 import TILE

    texture = _texture_2070(scene, path)
    model = read_1701_model(path, lambda p: files.read(p).decode("utf-8", "replace"), files.exists, 1 / TILE, texture)
    bits = {name: k for k, name in enumerate(model.states()[:32])}
    for name, k in bits.items():
        scene.note(tr("viewer.state_bit_name", bit=k, name=name))
    for file, error in model.unread:
        if error is None or isinstance(error, OSError | KeyError):
            _not_followed(scene, path, file, file if error is None else "")
        else:
            scene.note(tr("viewer.where_file_not_read_kind", where=path, file=file,
                          kind=type(error).__name__, error=error))  # fmt: skip

    def when(names) -> tuple[int, ...]:  # one mask per condition: all of them needed
        return tuple(1 << bits[n] for n in names if n in bits)

    for k, item in enumerate(model.items):
        node, piece = f"Models/{k}", item.piece
        mesh = scene.m_resolve.mesh(piece.mesh, path, node)
        if not mesh:
            scene.note(tr("viewer.where_file_not_files", where=node, file=piece.mesh))
            continue
        part = SPart(node, mesh, matrix(piece.place), when=when(item.conditions))
        part.materials = {
            name: SMaterial(name, d, n, piece.color, MODEL_SHADER, node, values=piece.values.get(name, ()))
            for name, (d, n) in piece.materials.items()
        }
        part.default = part.materials.get(modelread.ANY) or next(iter(part.materials.values()), SMaterial())
        part.animations = [_clip1701(c, scene.m_resolve, path, node) for c in item.clips]
        part.cloth = _cloth1701(item.cloth) if item.cloth is not None else None
        scene.m_parts.append(part)
    for k, e in enumerate(model.effects):
        node = f"Effects/{k}"
        if scene.m_resolve.effect(e.file, path, node):
            masks = when(e.conditions) + ((1 << bits[e.start],) if e.start in bits else ())
            scene.m_effects.append(SPlacedEffect(node, e.file, matrix(e.place), when=masks))


def is_model(path: str) -> bool:
    """Whether the view can draw a file: a model config or a mesh."""
    return bool(re.search(r"\.(cfg|rdm|mcf)$", path))
