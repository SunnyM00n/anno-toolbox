"""The island's objects drawn by the game's shipped model shaders through the native backend, each part's variant
found by its effect, vertex format and material switches, bound as the engine binds it (``nativematerials``; the
frame's instances in the module's structured buffers, ``cbModel`` a root view at the draw's slot, the material index
the push constants), recorded on QRhi's list after QRhi's own draws of the G-buffer's and the shadow map's passes
(a transparent part's in its place of the transparent pass: ``waternative.SFrameWater``).
``part_shaders`` finds a part's shaders (in the background); ``CNativeObjects(device, natives)`` is the device side,
one per view: ``part`` makes a ``CNativePart``, ``upload(frame)`` sends the frame's instances, ``SFrameDraws`` records
a frame. docs/rendering/native-backend.md sections 11 and 12.
"""

from __future__ import annotations

import itertools
import os
import struct
from dataclasses import dataclass, field

import numpy as np

from . import gameshader, gameshadow, nativegpu, nativelayout
from .gameinstances import KINDS, SLOT_BUFFER
from .nativecompute import CTold, guarded
from .nativematerials import CBindlessMaps, CMaterialTables, CStandIns
from .terrainnative import COPY_SOURCE, GBUFFER_FORMATS, LIT_FORMATS, UNORDERED, CBridge, camera_buffer

ENV = "ANNO_NATIVE_OBJECTS"  # "0": the objects by the current path (the rewrite), to hold the two against each other
FALLBACK_ENV = "ANNO_NATIVE_OBJECTS_COMPILE"  # "0": a variant the game does not ship stays on the current path
CUTOUT_ENV = "ANNO_NATIVE_CUTOUTS"  # "0": the cut-out volumes by the current path (their stencil passes)
# a part's roles (``desktopgameobjects._CPart``'s passes) -> the technique kind the engine names (shaderview.TECHNIQUES)
ROLES = {"main": "gbuffer", "prepass": "prepass", "shadow": "shadow"}
MATERIAL_INDEX = "cbMaterialIndex"  # the root constants that carry the draw's cGlobalMaterialIndex
BINDLESS = "cBindlessTextures"  # the engine's table of maps (``nativematerials.CBindlessMaps``)
PARTITION = "cbShadowPartition"  # a cut-out's shadow passes: the cascade they draw into (``CNativeCutOut``)
# the techniques a part draws with (``shaderview.TECHNIQUES``' G-buffer, shadow and prepass, the cut-out's own): a
# variant the game does not ship is compiled with these alone (``NativeShaders.fallback``)
ONLY = r"Deferred$|(?:Obj2|Cloth|CutOut)Shadow$|DepthPrepass$|^CutOut$"
TRANSPARENT = ONLY + r"|Transparent$"  # a transparent part's variant: its main pass among them
OPEN_DESCRIPTOR_COUNT = 64  # the stand-in table of another unbounded array: its descriptors [ours]
MATERIAL_SPACE = 2
KEPT_SET_COUNT = 512  # binding sets kept for the resources they were made with [ours]
WATER_ENV = "ANNO_NATIVE_WATER"  # "0": the transparent parts by the current path too (``waternative.ENV``)
TARGETS = {"gbuffer": GBUFFER_FORMATS, "lit": LIT_FORMATS, "shadow": []}  # a technique's colour targets by pass
# QRhi's blend factors (``renderstates.blend_factor``) by the module's names
FACTORS = {"Zero": "zero", "One": "one", "SrcColor": "src_color", "OneMinusSrcColor": "inv_src_color",
           "SrcAlpha": "src_alpha", "OneMinusSrcAlpha": "inv_src_alpha", "DstAlpha": "dst_alpha",
           "OneMinusDstAlpha": "inv_dst_alpha", "DstColor": "dst_color", "OneMinusDstColor": "inv_dst_color",
           "Src1Color": "src1_color", "OneMinusSrc1Color": "inv_src1_color", "Src1Alpha": "src1_alpha",
           "OneMinusSrc1Alpha": "inv_src1_alpha"}  # fmt: skip
# the frame's instance buffers by the names the shaders read them (``gameinstances.KINDS``)
INSTANCE_NAMES = {name: kind for kind, (_stride, names) in KINDS.items() for name in names}


class CNativeError(RuntimeError):
    """A part the native objects do not draw (said in its reason; it draws by the current path)."""


def is_enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and nativegpu.on()


@dataclass
class SPartShaders:
    """A part's native shaders: its variant, per role the vertex and pixel shader (``shadercache.SCachedShader``)
    and their reflections; ``flags`` what the part's owner takes instead of the current path's stages."""

    variant: object
    stages: dict
    reflections: dict
    is_transparent: bool = False  # its main pass draws in the transparent pass (step 6: ``waternative``)
    flags: object = None  # SNativeStages: found with no compile of the current path

    def structured(self) -> list:
        """The material structures every role reads (space 2), once each by name."""
        out = {}
        for reflection in (pair_reflection for pair in self.reflections.values() for pair_reflection in pair):
            names = {
                binding.name
                for binding in reflection.bindings
                if binding.space == MATERIAL_SPACE and binding.kind == "structured"
            }
            out.update({cbuffer.name: cbuffer for cbuffer in reflection.cbuffers if cbuffer.name in names})
        return list(out.values())


@dataclass
class SNativeStages:
    """What a part drawn natively takes from its techniques instead of the current path's compile
    (``shaderview.SDrawStages``' flags): the material references, whether it has a prepass; ``water``: a cut-out
    volume's ``CUT_OUT_WATER`` (None: not a cut-out); ``transparent``: its main pass draws in the transparent pass,
    by ``state``, its technique's render states for its own variant."""

    refs: tuple
    prepass: bool
    shadow: bool
    water: bool | None = None
    is_transparent: bool = False
    state: dict | None = None


def part_variant(natives, stem: str, values: dict):
    """A part's variant by its effect, vertex format and material switches (``CNativeError`` without one, or for a
    variant not shipped while ``FALLBACK_ENV`` is 0)."""
    from ..models import shadercache

    effect_info = natives.m_game.read_info(stem)
    if effect_info is None:
        raise CNativeError(f"no effect {stem}")
    vertex_format = values.get("VertexFormat", "")
    if not vertex_format:
        raise CNativeError("the material names no vertex format")
    variant = natives.variant(
        stem, vertex_format, shadercache.material_switches(effect_info.references, natives.m_game.m_materials, values)
    )
    if os.environ.get(FALLBACK_ENV, "") == "0" and not natives.shipped(variant):
        raise CNativeError(f"{stem}: the variant is not shipped")
    return variant, effect_info.references


def role_calls(natives, variant, is_transparent: bool = False) -> tuple[dict[str, tuple], dict]:
    """(per role the vertex and pixel shader's (function, literal arguments) of the first technique the role's
    pattern names (``shaderview.TECHNIQUES``) whose first pass's shaders the variant holds, as the current path
    picks it from the variant's own text; the main technique's render states). The prepass only for a main pass that
    tests its depth for equality. ``is_transparent``: the main pass is the transparent technique, read from the
    variant's own techniques (its switches change its states). ``CNativeError`` without a main technique."""
    from ..models import shaderview

    held = {
        (shader.name, "".join(shader.arguments).replace(" ", ""))
        for shader in natives.shaders(variant, TRANSPARENT if is_transparent else ONLY).values()
    }
    techniques = list(natives.techniques(variant.stem, variant if is_transparent else None).values())

    def first(kind: str):
        for technique in techniques:
            pair = [next((call for call in technique.calls if call[0] == stage), None) for stage in ("vs", "ps")]
            if shaderview.TECHNIQUES[kind].search(technique.name) and all(
                call and (call[1], call[2]) in held for call in pair
            ):
                return technique, tuple((call[1], call[2]) for call in pair)
        return None, None

    main, calls = first("transparent" if is_transparent else ROLES["main"])
    if main is None:
        raise CNativeError(
            "no transparent technique" if is_transparent else "no G-buffer technique: a transparent part"
        )
    equal = main.state.get("zfunc", "").upper() == "EQUAL"
    found = {
        "main": calls,
        "shadow": first(ROLES["shadow"])[1],
        "prepass": first(ROLES["prepass"])[1] if equal else None,
    }
    return {role: calls for role, calls in found.items() if calls is not None}, main.state


def cutout_shaders(natives, stem: str, values: dict, own_ps, water: bool) -> SPartShaders:
    """In the background: a cut-out volume's shipped shaders by role, its technique ``CutOut``'s two passes (``main``
    counts into the stencil, ``clear`` sets the far depth; the second only where the variant has it) and
    ``CutOutShadow``'s vertex shaders with ``own_ps`` (ours, a ``shadercache.SCachedShader``: the engine's flip a
    texel's bit in an array of its own) as ``shadow_mark`` and ``shadow_clear``, both only with ``clear``;
    ``water``: whether it cuts the water (its stencil bits)."""
    variant, refs = part_variant(natives, stem, values)
    held = {
        (shader.name, "".join(shader.arguments).replace(" ", "")) for shader in natives.shaders(variant, ONLY).values()
    }
    techniques = natives.techniques(stem)

    def passes(name: str) -> list:
        calls = [call for call in techniques[name].calls if (call[1], call[2]) in held] if name in techniques else []
        return [
            calls[k : k + 2]
            for k in range(0, len(calls) - 1, 2)
            if [call[0] for call in calls[k : k + 2]] == ["vs", "ps"]
        ]

    def shaders(pair) -> dict:
        return {stage: natives.call(variant, function, args, ONLY) for stage, function, args in pair}

    cut, cast = passes("CutOut"), passes("CutOutShadow")
    if not cut:
        raise CNativeError(f"{stem}: no CutOut pass")
    found = {"main": shaders(cut[0])}
    if len(cut) > 1:
        found["clear"] = shaders(cut[1])
        if len(cast) > 1:
            found.update({role: {"vs": shaders(pair)["vs"], "ps": own_ps}
                          for role, pair in zip(("shadow_mark", "shadow_clear"), cast, strict=False)})  # fmt: skip
    reflected = {
        role: (natives.reflection(shader["vs"]), natives.reflection(shader["ps"])) for role, shader in found.items()
    }
    return SPartShaders(
        variant, found, reflected, flags=SNativeStages(tuple(refs), False, "shadow_mark" in found, water)
    )


def part_shaders(natives, stem: str, values: dict) -> SPartShaders:
    """In the background: the shipped shaders of a part's passes (``role_calls``), found by the variant's name, with
    no compile of the current path; a variant the game does not ship is compiled from the unmodified source
    (``NativeShaders.fallback``) unless ``FALLBACK_ENV`` is 0. ``SPartShaders.stages`` holds them by role."""
    variant, refs = part_variant(natives, stem, values)
    try:
        roles, state = role_calls(natives, variant)
        is_transparent = False
    except CNativeError:  # no G-buffer technique: its main pass in the transparent pass (step 6), by its own states
        if os.environ.get(WATER_ENV, "") == "0":
            raise CNativeError(
                f"a transparent part: the transparent pass is drawn the earlier way ({WATER_ENV}=0)"
            ) from None
        roles, state = role_calls(natives, variant, is_transparent=True)
        is_transparent = True
    only = TRANSPARENT if is_transparent else ONLY
    found, reflected = {}, {}
    for role, calls in roles.items():
        shaders = tuple(natives.call(variant, function, args, only) for function, args in calls)
        found[role] = dict(zip(("vs", "ps"), shaders, strict=True))
        reflected[role] = tuple(natives.reflection(shader) for shader in shaders)
    flags = SNativeStages(tuple(refs), "prepass" in found, "shadow" in found, is_transparent=is_transparent,
                         state=state if is_transparent else None)  # fmt: skip
    return SPartShaders(variant, found, reflected, is_transparent=is_transparent, flags=flags)


def transparent_shaders(natives, stem: str, values: dict, stages) -> SPartShaders:
    """In the background: a transparent part's shipped shaders (its main pass in the transparent pass, step 6),
    following the current path's compile (``stages``: ``shaderview.SDrawStages``; its technique's render states are
    the variant's own, which the effect's techniques alone do not give), found by their entries."""
    from ..models import shaderview

    if os.environ.get(WATER_ENV, "") == "0":
        raise CNativeError(f"a transparent part: the transparent pass is drawn the earlier way ({WATER_ENV}=0)")
    variant, _refs = part_variant(natives, stem, values)
    roles = {"main": (stages.vertex_stage, stages.pixel_stage), "prepass": stages.prepass, "shadow": stages.shadow}
    found, reflected = {}, {}
    for role, pair in roles.items():
        if pair is None:
            continue
        kind = "transparent" if role == "main" else ROLES[role]
        shaders = tuple(natives.entry(variant, stage.entry, shaderview.TECHNIQUES[kind]) for stage in pair)
        found[role] = dict(zip(("vs", "ps"), shaders, strict=True))
        reflected[role] = tuple(natives.reflection(stage) for stage in shaders)
    return SPartShaders(variant, found, reflected, is_transparent=True)


def blend_of(found, mask: int) -> tuple:
    """The module's blend of a target from ``SPassState.blends``' entry (QRhi's colour and alpha factors; None: off)
    and its write mask (D3D's bits)."""
    if found is None:
        return (False, "one", "zero", "add", "one", "zero", "add", mask)
    names = []
    for factor in found:
        name = FACTORS.get(getattr(factor, "name", str(factor)))
        if name is None:
            raise CNativeError(f"the blend factor {factor}: not native")
        names.append(name)
    source, destination, source_alpha, destination_alpha = names
    # an alpha factor of a colour (the effect's alpha defaults to its colour words) is its alpha one, as QRhi turns it
    source_alpha, destination_alpha = (
        factor_name.replace("_color", "_alpha") for factor_name in (source_alpha, destination_alpha)
    )
    return (True, source, destination, "add", source_alpha, destination_alpha, "add", mask)


def pass_state(state, colours: list[str], depth: str) -> dict:
    """The module's pipeline state of a ``renderstates.SPassState`` (the current path's: the same depth, culling,
    blends, writes and stencil test) into targets of ``colours`` and ``depth``."""
    depth_function = "always" if state.depth_always else "equal" if state.depth_equal else "greater_equal"
    blends = []
    for k in range(len(colours)):
        mask = state.writes[k] if k < len(state.writes) and state.writes[k] is not None else 15
        found = state.blends[k] if k < len(state.blends) else None
        blends.append(blend_of(found, mask if state.colour else 0))
    out = {"cull": state.cull, "front_ccw": True, "depth_test": state.depth_test, "depth_write": state.depth_write,
           "depth_func": depth_function, "colors": colours, "depth": depth, "blends": blends}  # fmt: skip
    stencil = state.stencil
    if stencil is not None:  # QRhi's reference is set per draw (``Mesh.draw``), the module's with the pipeline
        if "S" not in depth:
            raise CNativeError("a stencil test into a depth target without stencil")
        out.update(stencil=True, stencil_func=stencil.func.lower(), stencil_pass=stencil.pass_op.lower(),
                   stencil_fail=stencil.fail.lower(), stencil_depth_fail=stencil.depth_fail.lower(), stencil_read_mask=stencil.mask,
                   stencil_write_mask=stencil.write_mask, stencil_ref=stencil.ref)  # fmt: skip
    return out


@dataclass
class STechnique:
    """One role of a part on the device: its plan and layouts, its shaders, the reflections' bindings by name, its
    pipelines by vertex layout (shared by every part of the same shaders and state)."""

    plan: nativelayout.SBindingPlan
    layouts: list
    shaders: tuple
    bytecode: bytes
    bindings: dict
    cbuffers: dict
    state: dict
    pipelines: dict = field(default_factory=dict)
    named: dict = field(default_factory=dict)  # its own resources by name (a cascade's ``cbShadowPartition``)
    element_keys: dict = field(default_factory=dict)  # its constant buffers' layout keys by name (``_frame``)


class CNativeRun:
    """One draw of a native part this frame: a role, a mesh, its slot (``cbModel``'s byte offset) and how many
    instances (a shadow draw: its culled words); in the transparent pass ordered by its box as the current path's
    runs and recorded by the backend in its place (``native``: ``nativepass.draw_models``); ``viewport``: a share
    of the target (x, y from the top left, width, height), None the whole."""

    __slots__ = ("m_part", "m_role", "m_mesh", "m_slot", "m_worlds", "m_words", "m_viewport")
    m_native = True
    m_world = np.identity(4)

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        places = np.asarray(self.m_worlds, float)[:, :3, 3]
        return places.min(axis=0), places.max(axis=0)

    def __init__(self, part, role: str, mesh, slot: int, worlds, words=None, viewport=None) -> None:
        self.m_part, self.m_role, self.m_mesh, self.m_slot, self.m_worlds, self.m_words = (
            part,
            role,
            mesh,
            slot,
            worlds,
            words,
        )
        self.m_viewport = viewport

    @property
    def m_instances(self) -> int:
        return len(self.m_words) if self.m_words is not None else len(self.m_worlds)


@dataclass
class SPartLevel:
    """A level of detail of a compiled part: its meshes and their bones (both ways' parts)."""

    meshes: list
    joints: list

    @staticmethod
    def of(got) -> list[SPartLevel]:
        """A compiled part's levels (``desktopgameobjects.SPartDraws.levels``)."""
        return [SPartLevel(level_meshes, [mesh.bone_count(4) for mesh in level_meshes]) for level_meshes in got.levels]


class CNativePart:
    """A compiled part (``desktopgameobjects.PartDraws``) drawn natively: its meshes on the device, its material in
    the tables, per role a ``STechnique``; ``models`` gives a frame's ``CNativeRun``s as ``_CPart.models`` gives its
    draws. Its owner's flags: never a cut-out, nothing made ahead; transparent as its material."""

    m_native = True
    m_transparent = False
    m_cutout = False

    def __init__(self, objects: CNativeObjects, got, shaders: SPartShaders, frame, states: dict) -> None:
        self.m_objects, self.m_frame, self.m_got = objects, frame, got
        self.m_passes: dict = {}  # none of the current path's (``_unmade``)
        self.m_made: set[str] = set()
        self.m_is_transparent = shaders.is_transparent  # its main role in the transparent pass (``SFrameWater``)
        self.m_techniques = self._techniques(objects, shaders, states)
        structured = shaders.structured()
        read = {member.name for cbuffer in structured for variable in cbuffer.variables for member in variable.members}
        slots, self.m_maps = objects.m_maps.slots(got.material, read)  # the files it gives back on release
        self.m_filled = False
        self.m_group, self.m_index = objects.m_tables.add(structured, got.material.fields, slots)
        self.m_push = struct.pack("<4I", self.m_index, 0, 0, 0)
        self.m_levels = SPartLevel.of(got)
        self._m_meshes: dict[int, tuple] = {}  # id of a mesh -> (vertices, indices, mesh)
        self._m_items: dict[tuple, tuple] = {}  # (role, id of a mesh) -> (sets' key, SDrawItem, what it holds)
        self.begin()

    def _techniques(self, objects: CNativeObjects, shaders: SPartShaders, states: dict) -> dict:
        targets = {"shadow": "shadow", "main": "lit" if self.m_is_transparent else "gbuffer"}
        return {role: objects.technique(shaders.stages[role], shaders.reflections[role], states[role],
                                        targets.get(role, "gbuffer")) for role in shaders.stages}  # fmt: skip

    def begin(self) -> None:
        self.m_runs: list[CNativeRun] = []

    def models(self, level: int, worlds: np.ndarray, shadows: bool, mask=None, deform: bool = False) -> tuple:
        """This frame's runs of ``worlds`` at ``level`` (the prepass first where there is one) and their casters
        into the cascades ``mask`` marks (None: all)."""
        out, cast = [], []
        if not self.m_filled:  # its maps go up with its first draw (``CBindlessMaps.fill``)
            self.m_objects.m_maps.fill(self.m_maps)
            self.m_filled = True
        part_level = self.m_levels[min(level, len(self.m_levels) - 1)]
        reach = mask if mask is not None else np.ones((len(worlds), gameshadow.COUNT), bool)
        for mesh, joints in zip(part_level.meshes, part_level.joints, strict=True):
            slot = self.m_frame.place(worlds, joints, deform)
            if "prepass" in self.m_techniques:
                out.append(CNativeRun(self, "prepass", mesh, slot, worlds))
            out.append(CNativeRun(self, "main", mesh, slot, worlds))
            if shadows and "shadow" in self.m_techniques:
                caster_slot, words = self.m_frame.place_casters(worlds, joints, reach, deform)
                if len(words):
                    cast.append(CNativeRun(self, "shadow", mesh, caster_slot, worlds, words))
        self.m_runs += out + cast
        return out, cast

    def ahead(self, shadows: bool) -> tuple[list, list, list]:
        return [], [], []  # its pipelines come from DXIL in milliseconds, its maps go up with its first draw

    def all(self) -> list:
        return []

    def mesh(self, mesh) -> tuple:
        """A mesh's vertex and index buffers on the device, made once (its bytes let go after)."""
        got = self._m_meshes.get(id(mesh))
        if got is None:
            objects = self.m_objects
            vertices = objects.static(mesh.m_data, "vertex", "vertices")
            indices = objects.static(np.ascontiguousarray(mesh.m_indices, "<u4").tobytes(), "index", "indices")
            got = self._m_meshes[id(mesh)] = (vertices, indices, mesh)
            mesh.m_data = b""
        return got

    def item(self, role: str, mesh, sets: list):
        """The draw of ``mesh`` in ``role`` with ``sets`` (the frame's and its own, in its layouts' order), made
        again when they changed."""
        key = (role, id(mesh))
        ids = tuple(map(id, sets))
        found = self._m_items.get(key)
        if found is not None and found[0] == ids:
            return found[1]
        technique = self.m_techniques[role]
        vertices, indices, kept = self.mesh(mesh)
        pipeline, fill = self.m_objects.pipeline(technique, kept)
        item = self.m_objects.m_module.draw_item(pipeline, sets, len(kept.m_indices), [vertices, fill], indices, True,
                                             self.m_push)  # fmt: skip
        self._m_items[key] = (ids, item, (pipeline, fill, sets, vertices, indices))
        return item

    def release(self) -> None:
        for vertices, indices, _mesh in self._m_meshes.values():
            vertices.release()
            indices.release()
        self._m_meshes, self._m_items = {}, {}
        self.m_objects.m_maps.give_back(self.m_maps)
        if self.m_index is not None:  # its record free for the next part (a part let go and made again took a new one)
            self.m_objects.m_tables.remove(self.m_group, self.m_index)
        self.m_maps, self.m_index = [], None
        self.begin()


class CNativeCutOut(CNativePart):
    """A cut-out volume drawn natively (``cutout_shaders``): its count and clear right after the terrain in the
    G-buffer's pass, and in each cascade it reaches the shadow's two, each into the cascade's part of the shadow map
    by the cascade's own ``cbShadowPartition``, as the engine draws them (``desktopgameobjects._CPart``'s cut-out)."""

    m_cutout = True

    def _techniques(self, objects: CNativeObjects, shaders: SPartShaders, states: dict) -> dict:
        out = {role: objects.technique(shaders.stages[role], shaders.reflections[role], states[role])
               for role in ("main", "clear") if role in shaders.stages}  # fmt: skip
        for k in range(gameshadow.COUNT) if "shadow_mark" in shaders.stages else ():
            for role in ("shadow_mark", "shadow_clear"):
                out[f"{role}{k}"] = objects.technique(shaders.stages[role], shaders.reflections[role], states[role],
                                                      "shadow", k)  # fmt: skip
        return out

    def models(self, level: int, worlds: np.ndarray, shadows: bool, mask=None, deform: bool = False) -> tuple:
        """This frame's count and clear of ``worlds`` and their casters per cascade ``mask`` marks (None: all)."""
        out, cast = [], []
        if not self.m_filled:
            self.m_objects.m_maps.fill(self.m_maps)
            self.m_filled = True
        part_level = self.m_levels[min(level, len(self.m_levels) - 1)]
        reach = mask if mask is not None else np.ones((len(worlds), gameshadow.COUNT), bool)
        for mesh, joints in zip(part_level.meshes, part_level.joints, strict=True):
            slot = self.m_frame.place(worlds, joints, deform)
            out += [
                CNativeRun(self, role, mesh, slot, worlds) for role in ("main", "clear") if role in self.m_techniques
            ]
            if not (shadows and "shadow_mark0" in self.m_techniques):
                continue
            for k, caster_slot, words in self.m_frame.place_each_cascade(worlds, joints, reach, deform):
                cast += [CNativeRun(self, f"{role}{k}", mesh, caster_slot, worlds, words, gameshadow.DIVISIONS[k])
                         for role in ("shadow_mark", "shadow_clear")]  # fmt: skip
        self.m_runs += out + cast
        return out, cast


class CInstanceBuffers:
    """The frame's instance buffers (``gameinstances.CFrameInstances.gathered``) as the module's structured buffers,
    and the draws' slots as one constant buffer whose slot a draw's ``cbModel`` root view points at; written when the
    frame's instances are new, grown to the next power of two (made again: the sets follow by their ids)."""

    def __init__(self, device) -> None:
        self.m_device = device
        self.m_buffers: dict[str, object] = {}
        self.m_serial = None

    def _fit(self, kind: str, size_bytes: int, usage: str, stride: int):
        buffer = self.m_buffers.get(kind)
        need = max(1 << 16, 1 << max(0, size_bytes - 1).bit_length())
        if buffer is None or buffer.size < size_bytes:
            if buffer is not None:
                buffer.release()
            buffer = self.m_buffers[kind] = self.m_device.buffer(need, usage, stride=stride, name=f"frame {kind}")
        return buffer

    def send(self, command_list, frame) -> None:
        if frame.m_serial == self.m_serial and self.m_buffers:
            return
        self.m_serial = frame.m_serial
        got = frame.gathered()
        for kind, (stride, _names) in KINDS.items():
            buffer = self._fit(kind, len(got[kind]), "structured", stride)
            if got[kind]:
                command_list.write_buffer(buffer, got[kind])
        buffer = self._fit("slots", len(got["slots"]), "constant", 0)
        if got["slots"]:
            command_list.write_buffer(buffer, got["slots"])

    def named(self) -> dict:
        """The buffers by the shaders' names, and ``cbModel``'s slots under its own."""
        if not self.m_buffers:
            raise RuntimeError("the frame's instances were not sent")
        return {
            **{name: self.m_buffers[kind] for name, kind in INSTANCE_NAMES.items()},
            SLOT_BUFFER: self.m_buffers["slots"],
        }

    def release(self) -> None:
        for buffer in self.m_buffers.values():
            buffer.release()
        self.m_buffers, self.m_serial = {}, None


class CNativeObjects(CTold):
    """The objects' draws on the native backend (module docstring)."""

    def __init__(self, device, natives, has_stencil: bool = True, max_texture: int = 0) -> None:
        self.m_device, self.m_natives = device, natives
        self.m_module = nativegpu.load()
        self.m_list = device.command_list()
        self.m_list.open()
        self.m_bridge = CBridge(device)
        self.m_maps = CBindlessMaps(device, self.m_list, max_texture)
        self.m_tables = CMaterialTables(device)
        self.m_stand_ins = CStandIns(device, self.m_list)
        self.m_instances = CInstanceBuffers(device)
        self.m_depth = "D32S8" if has_stencil else "D32"
        self._m_layouts: dict[tuple, object] = {}
        self._m_shaders: dict[tuple, object] = {}
        self._m_pipelines: dict[tuple, tuple] = {}
        self._m_sets = nativegpu.CSetCache(KEPT_SET_COUNT)
        self._m_tables: dict[tuple, object] = {}  # the stand-in tables of unbounded arrays (``_open``)
        self._m_techniques: dict[tuple, STechnique] = {}
        self._m_values: dict[tuple, bytes] = {}  # a pass's constants by layout, this pass (``_frame``)
        self._m_layout_keys: dict[tuple, int] = {}  # an element layout -> its number, the key in ``_m_values``
        self.m_buffers = nativegpu.CKeptBuffers(device)  # the frame's constant and camera buffers by (pass, layout)
        self.m_failed: list[tuple[CNativePart, str]] = []  # parts whose draws raised: the current path's from now on
        self.m_error = ""
        self.m_drawn = 0  # the native draws of the last frame
        self.m_messages, self.m_foreign = [], []

    # what parts share: layouts, shaders, pipelines, static buffers

    def layout(self, spec: nativelayout.SLayoutSpec):
        key = (spec.space, spec.bindless, tuple(spec.tuples()))
        if key not in self._m_layouts:
            self._m_layouts[key] = nativegpu.layout(self.m_device, spec)
        return self._m_layouts[key]

    def shader(self, stage: str, cached):
        key = (stage, cached.name, cached.bytecode)
        if key not in self._m_shaders:
            self._m_shaders[key] = self.m_device.shader(stage, cached.bytecode, cached.name)
        return self._m_shaders[key]

    def technique(self, stages: dict, reflections: tuple, state, target: str = "gbuffer", cascade=None) -> STechnique:
        """A role's technique: its binding plan from the two shaders' reflections, its layouts and shaders, into
        the pass's targets (``TARGETS``: the G-buffer, the lit colour of the transparent pass, the shadow map's depth
        alone; ``cascade``: its ``cbShadowPartition`` names that cascade); one for every part of the same shaders and
        state (a pass's sets and constants are worked out once a technique)."""
        key = (stages["vs"].bytecode, stages["ps"].bytecode, state, target, cascade)
        found = self._m_techniques.get(key)
        if found is None:
            found = self._m_techniques[key] = self._technique(stages, reflections, state, target)
            cbuffer = found.cbuffers.get(PARTITION)
            if cascade is not None and cbuffer is not None:
                constants = gameshader.CConstants(cbuffer)
                constants.set("cCurrentCascade", cascade)
                found.named[PARTITION] = self._constant(("cascade", cascade, cbuffer.size), bytes(constants.m_data))
        return found

    def _technique(self, stages: dict, reflections: tuple, state, target: str) -> STechnique:
        plan = nativelayout.SBindingPlan.of(list(reflections), bindless=(BINDLESS,), rooted=(SLOT_BUFFER,))
        if plan.push is not None and plan.push.name != MATERIAL_INDEX:
            raise CNativeError(f"the push constants are {plan.push.name}, not {MATERIAL_INDEX}")
        roots = [item for spec in plan.layouts if spec.space == nativelayout.ROOT_SPACE for item in spec.items]
        if any(item.kind == "volatile" and item.name != SLOT_BUFFER for item in roots):
            raise CNativeError("a root constant buffer besides cbModel")
        shaders = (self.shader("vs", stages["vs"]), self.shader("ps", stages["ps"]))
        bindings = {binding.name: binding for reflection in reflections for binding in reflection.bindings}
        cbuffers = {cbuffer.name: cbuffer for reflection in reflections for cbuffer in reflection.cbuffers}
        return STechnique(plan, [self.layout(layout_spec) for layout_spec in plan.layouts], shaders, stages["vs"].bytecode, bindings,
                         cbuffers, pass_state(state, TARGETS[target], self.m_depth))  # fmt: skip

    def pipeline(self, technique: STechnique, mesh) -> tuple:
        """(pipeline, the constant vertex buffer of the inputs the mesh lacks) for a mesh's stored layout."""
        key = (
            id(technique.shaders[0]),
            id(technique.shaders[1]),
            str(sorted(technique.state.items())),
            mesh.m_elements,
            mesh.m_stride,
        )
        if key not in self._m_pipelines:
            self._m_pipelines[key] = nativegpu.mesh_pipeline(self.m_device, self.m_list, technique.shaders, technique.bytecode,
                                                           technique.layouts, technique.state, mesh)  # fmt: skip
        return self._m_pipelines[key]

    def static(self, data: bytes, usage: str, name: str):
        return nativegpu.static_buffer(self.m_device, self.m_list, data, usage, name)

    def part(self, got, shaders: SPartShaders, frame, states: dict) -> CNativePart:
        """A compiled part on the device (``states``: per role the current path's ``SPassState``; a cut-out volume's
        shaders make a ``CNativeCutOut``)."""
        cutout = getattr(shaders.flags, "water", None) is not None
        return (CNativeCutOut if cutout else CNativePart)(self, got, shaders, frame, states)

    def upload(self, frame) -> None:
        """The frame's instances and the materials added since, on the module's list (sent with the passes)."""
        self.m_instances.send(self.m_list, frame)
        self.m_tables.send(self.m_list)

    # one pass

    def _constant(self, key: tuple, data: bytes, usage: str = "constant", stride: int = 0):
        return self.m_buffers.get(self.m_list, key, data, usage, stride, str(key[1]))

    def _frame(self, name: str, technique: STechnique, camera, fills: list, named: dict) -> dict:
        """The pass's constant buffers and camera records a technique reads, by name (``name``: the pass)."""
        from .nativematerials import element_layout

        out = {}
        for cb_name, cbuffer in technique.cbuffers.items():
            binding = technique.bindings.get(cb_name)
            # the frame's buffers are space 0's; the transparent pass's own ones (cbWater) sit in the effect's table
            frame_space = binding is not None and (
                binding.space == 0 or (name == "transparent" and binding.space != MATERIAL_SPACE)
            )
            if not frame_space or binding.space == nativelayout.ROOT_SPACE or cb_name in named:
                continue
            if binding.kind == "cbuffer":
                if cb_name not in technique.element_keys:  # the reflection never changes: walked once, not each frame
                    layout = element_layout(cbuffer)
                    technique.element_keys[cb_name] = self._m_layout_keys.setdefault(layout, len(self._m_layout_keys))
                key = (name, technique.element_keys[cb_name])
                if key not in self._m_values:
                    constants = gameshader.CConstants(cbuffer)
                    gameshader.fill_frame(constants, camera)
                    for fill in fills:
                        fill(constants)
                    self._m_values[key] = bytes(constants.m_data)
                out[cb_name] = self._constant(key, self._m_values[key])
            elif cb_name == "cCameraBuffer":
                key = ("camera", cbuffer.size)
                if key not in self._m_values:
                    raw = gameshader.CConstants(camera_buffer(cbuffer))
                    gameshader.fill_frame(raw, camera)
                    self._m_values[key] = bytes(raw.m_data)
                out[cb_name] = self._constant(key, self._m_values[key], "structured", cbuffer.size)
        return out

    def _resource(self, item: nativelayout.SItem, technique: STechnique, named: dict):
        """What a set item binds: the frame's by name (QRhi's textures wrapped), else its stand-in."""
        found = named.get(item.name)
        if item.kind == "texture":
            if found is not None and not isinstance(found, self.m_module.Texture):  # QRhi's
                return self.m_bridge.texture(found)[0]
            return found if found is not None else self.m_stand_ins.texture(technique.bindings[item.name])
        cbuffer = technique.cbuffers.get(item.name)
        stride = cbuffer.size if cbuffer is not None and item.kind == "structured" else 0
        if found is not None and not isinstance(found, self.m_module.Buffer):  # QRhi's: its passes bind it for storage
            usage = "raw" if item.kind in ("raw", "rwraw") else ""
            return self.m_bridge.buffer(found, stride, UNORDERED, usage)[0]
        if found is not None:
            return found
        if item.kind == "structured":
            return self.m_stand_ins.structured(stride or 16)
        if item.kind in ("raw", "rwraw"):
            return self.m_stand_ins.raw(item.kind == "rwraw")
        raise CNativeError(f"nothing to bind for {item.name} ({item.kind})")

    def _set(self, technique: STechnique, index: int, named: dict):
        """A layout's set for the frame's ``named`` resources, made when what it binds changed (``KEPT_SET_COUNT`` kept)."""
        spec = technique.plan.layouts[index]
        if spec.bindless:
            item = spec.items[0]
            return self.m_maps.m_table if item.name == BINDLESS else self._open(technique, index)
        held = tuple(
            self._resource(layout_item, technique, named) if layout_item.kind not in ("sampler", "push") else None
            for layout_item in spec.items
        )
        resources = {
            layout_item.name: resource
            for layout_item, resource in zip(spec.items, held, strict=True)
            if resource is not None
        }
        return self._m_sets.get(technique.layouts[index], held, lambda: nativegpu.binding_set(
            self.m_device, technique.layouts[index], spec, resources, nativegpu.samplers(self.m_device, technique.plan)))  # fmt: skip

    def _open(self, technique: STechnique, index: int):
        """An unbounded array no pass fills (``cModelVoxelTextures[]``): a table of stand-ins of its declared kind,
        as the current path sizes it to one element and stands that in."""
        item = technique.plan.layouts[index].items[0]
        binding = technique.bindings[item.name]
        key = ("open", item.kind, binding.dimension, binding.returns, binding.flags)
        table = self._m_tables.get(key)
        if table is None:
            table = self._m_tables[key] = self.m_device.descriptor_table(
                technique.layouts[index], OPEN_DESCRIPTOR_COUNT
            )
            stand_in = self.m_stand_ins.texture(binding) if item.kind == "texture" else self.m_stand_ins.raw(False)
            for slot in range(OPEN_DESCRIPTOR_COUNT):
                self.m_device.write_table(table, (item.kind, slot, stand_in))
        return table

    def record(
        self, command_buffer, name: str, runs: list, camera, fills: list, named: dict, size_px: tuple[int, int]
    ) -> None:
        """``runs`` on QRhi's list (inside ``beginExternal``) into the targets it has bound: the module's list (the
        uploads, the frame's constants) goes to the queue first; a part whose draw raised goes to ``failed``."""
        self._told_before()
        frame_named = {**named, **self.m_instances.named()}
        made: dict[tuple, list] = {}  # (technique, material group) -> its sets: worked out once a pass
        frames: dict[int, dict] = {}  # technique -> the pass's constants it reads
        self._m_values = {}
        items, counts, offsets = [], [], []
        for run in runs:
            part = run.m_part
            try:
                tech = part.m_techniques[run.m_role]
                sets = made.get((id(tech), part.m_group))
                if sets is None:
                    if id(tech) not in frames:
                        frames[id(tech)] = self._frame(name, tech, camera, fills, frame_named)
                    own = {**frame_named, **frames[id(tech)], **self.m_tables.group(part.m_group).buffers, **tech.named}
                    sets = made[(id(tech), part.m_group)] = [self._set(tech, k, own) for k in range(len(tech.layouts))]
                items.append(part.item(run.m_role, run.m_mesh, sets))
            except Exception as exc:
                self.m_failed.append((part, f"{type(exc).__name__}: {exc}"))
                items.append(None)
            counts.append(run.m_instances)
            offsets.append(run.m_slot)
        failed = {id(failed_part) for failed_part, _why in self.m_failed}
        keep = [k for k, draw_item in enumerate(items) if draw_item is not None and id(runs[k].m_part) not in failed]

        def record(list_ptr) -> None:
            for share, group in itertools.groupby(keep, key=lambda k: runs[k].m_viewport):  # a cascade's part: a call
                run_indices = list(group)
                x, y, share_width, share_height = share or (0.0, 0.0, 1.0, 1.0)
                area = (x * size_px[0], y * size_px[1], share_width * size_px[0], share_height * size_px[1])
                self.m_module.record_draws(self.m_device, list_ptr, [items[k] for k in run_indices], [counts[k] for k in run_indices],
                                         [offsets[k] for k in run_indices], area)  # fmt: skip

        self.m_bridge.send(self.m_list, command_buffer, record)
        self.m_drawn += len(keep)
        self._told_after()

    def end_frame(self) -> None:
        self.m_bridge.end_frame()
        self.m_maps.end_frame()
        self.m_drawn = 0

    def release(self) -> None:
        for pipeline, fill in self._m_pipelines.values():
            pipeline.release()
            fill.release()
        for resource in (*self._m_tables.values(), *self._m_layouts.values(), *self._m_shaders.values()):
            resource.release()
        for part in (
            self._m_sets,
            self.m_buffers,
            self.m_maps,
            self.m_tables,
            self.m_stand_ins,
            self.m_instances,
            self.m_bridge,
        ):
            part.release()
        self.m_list.close()
        self._m_pipelines, self._m_tables, self._m_layouts, self._m_shaders, self._m_techniques = {}, {}, {}, {}, {}


@dataclass
class SFrameDraws:
    """One frame's native objects as ``gameframe.SPasses.native_objects`` takes them: the G-buffer's runs, the
    casters', the textures they read by name (the parts' and the ground's); the cut-out volumes' (``CNativeCutOut``),
    recorded right after the native terrain in both passes (``cutouts``, ``cutout_shadow``). A pass that raises turns
    the native objects off (``CNativeObjects.error``): the next frame draws them by the current path."""

    objects: CNativeObjects
    runs: list
    caster_runs: list
    textures: dict
    ground: object
    cutout_runs: list = field(default_factory=list)
    cutout_casters: list = field(default_factory=list)

    @property
    def casters(self) -> bool:
        return bool(self.caster_runs or self.cutout_casters)

    def gbuffer(self, command_buffer, camera, size_px, runs=None) -> None:
        runs = self.runs if runs is None else runs
        if runs:
            fills = [fill for fill in (self.ground.fill() if self.ground is not None else None,) if fill is not None]
            guarded(
                self.objects,
                self.objects.record,
                command_buffer,
                "gbuffer",
                runs,
                camera,
                fills,
                self.textures,
                size_px,
            )

    def cutouts(self, command_buffer, camera, size_px) -> None:
        """The cut-out volumes' count and clear, after the terrain and before any decal or model."""
        self.gbuffer(command_buffer, camera, size_px, self.cutout_runs)

    def cutout_shadow(self, command_buffer, camera, cascades, analysis, side: int) -> None:
        """Their shadow passes: the terrain's shadow cleared inside them before any model casts."""
        self.shadow(command_buffer, camera, cascades, analysis, side, self.cutout_casters)

    def shadow(self, command_buffer, camera, cascades, analysis, side: int, runs=None) -> None:
        runs = self.caster_runs if runs is None else runs
        if not runs:
            return
        light = [*cascades.light, 0.0]
        fills = [
            lambda constants: gameshadow.fill(constants, cascades),
            lambda constants: constants.set("cLgtDirection", light),
        ]
        if self.ground is not None:
            fills.append(self.ground.fill())
        objects = self.objects
        buffer, read = analysis
        if buffer is not None and cascades.gpu:  # the analysis's last pass left it, or the read-back's copy
            information = objects.m_bridge.buffer(buffer, gameshadow.INFO_BYTES, COPY_SOURCE if read else UNORDERED)[0]
        else:
            information = objects._constant(("shadow", "cShadowInformation"), gameshadow.info_bytes(cascades), "structured",
                                      gameshadow.INFO_BYTES)  # fmt: skip
        named = {**self.textures, "cShadowInformation": information}
        guarded(objects, objects.record, command_buffer, "shadow", runs, camera, fills, named, (side, side))
