"""A part of the island's objects compiled in the background for the look "The game's own shaders"
(``desktopgameobjects``): its material by its effect (or its shipped shaders on the native backend), its meshes per
level of detail, its maps read once for all parts; a cut-out volume's passes and the passes' states.

``compile_part(source, files, part, natives)`` is the entry (a loader job); ``part_key`` what its draws depend on.
docs/rendering/island-117-view.md "Phase 5", docs/rendering/native-backend.md section 11.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import numpy as np

from ...render import cull, gamedraw, renderstates, terraindraw, terrainnative
from ...render.objectsnative import SNativeStages, SPartShaders

DEPTH_ONLY = renderstates.SPassState(colour=False)  # a depth prepass: depth tested and written, no colour
SHADOW = renderstates.GBUFFER_STATE
# the cut-out volume's ShaderID: it draws no colour, it clears the terrain's depth inside it and marks the stencil the
# water tests (model_volume_cutout.fx; island-117-view.md "Round 12")
CUT_OUT_VOLUME = 4
CUT_OUT_FILE = "model_volume_cutout"
# the stencil bits it sets: the sea's and the rivers' cut-out bits with CUT_OUT_WATER, else the water's own (defines.fxh)
CUT_OUT_WATER_BITS, CUT_OUT_TERRAIN_BITS = 0x01 | 0x02, 0x08
SHADOW_CUT_OUT_BIT = 0x01  # the shadow map's stencil holds only the cut-outs' count [ours]
# CutOutShadow's pixel shaders flip and test the bit in the game's own array; with the bit in the stencil they draw
# nothing: this one stands in for both [ours]
SHADOW_CUT_OUT_PS = (
    "void psCutOutShadowStencil(float4 position : SV_Position) {}\n",
    "psCutOutShadowStencil",
    "ps_6_2",
)


@dataclass
class SPartDraws:
    """A part's compiled material, its meshes as stored per level of detail (level 0 first) and their bounding
    sphere in the part's space (centre, radius) (``compile_part``)."""

    stages: (
        object  # models.shaderview.SDrawStages; a part drawn natively: objectsnative.SNativeStages, nothing compiled
    )
    material: gamedraw.SMaterial
    levels: list
    sphere: tuple = (np.zeros(3), 1.0)
    native: object = None  # render.objectsnative.SPartShaders, or why not (an exception; None: not asked)


class CSharedMaps:
    """``files`` whose maps (``.dds``) are read once for every part that names them: a continental island's parts
    held 13 GB of maps, most of them the same files (island-117-view.md "Speed"); ``clear`` lets them go once the
    parts are compiled (each part's own copy goes when its arrays are made)."""

    def __init__(self, files) -> None:
        self.m_files, self.m_cache = files, {}
        self._m_lock = threading.Lock()

    def __getattr__(self, name: str):
        return getattr(self.m_files, name)

    def read(self, path: str) -> bytes:
        if not path.lower().endswith(".dds"):
            return self.m_files.read(path)
        with self._m_lock:
            got = self.m_cache.get(path)
        if got is None:
            got = self.m_files.read(path)
            with self._m_lock:
                got = self.m_cache.setdefault(path, got)
        return got

    def clear(self) -> None:
        with self._m_lock:
            self.m_cache.clear()


@dataclass
class SCutOutStages:
    """A cut-out volume's two passes of ``CutOut`` (vertex and pixel shader each): ``mark`` counts the volume's faces
    in front of the terrain into the stencil, ``clear`` (None without ``CUT_OUT_TERRAIN``) sets the depth to the far
    plane where the count is odd; ``water``: ``CUT_OUT_WATER``, whose bits the water tests."""

    mark: tuple
    clear: tuple | None
    water: bool
    refs: tuple
    is_transparent: bool = False
    shadow: tuple | None = None  # CutOutShadow's two passes: (mark, clear), each (vertex shader, ours) (``states``)
    prepass: tuple | None = None

    def states(self) -> tuple[renderstates.SPassState, renderstates.SPassState]:
        """The two passes' states as ``sbCutOutMarkStencil`` and ``sbCutOutClearDepth`` set them."""
        return volume_states(self.water)

    @staticmethod
    def shadow_states() -> tuple[renderstates.SPassState, renderstates.SPassState]:
        """``CutOutShadow``'s two passes: the game flips and clears a bit per shadow texel in an array of its own
        (``cShadowCutout``); here the shadow map's stencil holds that bit, counted and cleared alike [ours]."""
        return _count_and_clear(SHADOW_CUT_OUT_BIT, "INVERT")


def _count_and_clear(bits: int, after: str) -> tuple[renderstates.SPassState, renderstates.SPassState]:
    """A cut-out's count of its faces in front of the depth (``bits`` flipped, the depth kept), then the far depth
    written where the count is odd (the bits then ``after``)."""
    mark = renderstates.SStencil("ALWAYS", "INVERT", bits, bits, bits)
    clear = renderstates.SStencil("EQUAL", after, bits, bits, bits)
    return (renderstates.SPassState(depth_write=False, colour=False, stencil=mark),
            renderstates.SPassState(depth_always=True, colour=False, stencil=clear))  # fmt: skip


def volume_states(water: bool) -> tuple[renderstates.SPassState, renderstates.SPassState]:
    """A cut-out's count and clear: the water's stencil bits with ``CUT_OUT_WATER``, else the terrain's."""
    return _count_and_clear(CUT_OUT_WATER_BITS if water else CUT_OUT_TERRAIN_BITS, "KEEP" if water else "INVERT")


def cutout_states(flags) -> dict:
    """A native cut-out's states by role (``objectsnative.CNativeCutOut``), as ``SCutOutStages`` sets them."""
    mark, clear = volume_states(flags.water)
    shadow_mark, shadow_clear = SCutOutStages.shadow_states()
    return {"main": mark, "clear": clear, "shadow_mark": shadow_mark, "shadow_clear": shadow_clear}


def cutout_stages(source, values: dict) -> SCutOutStages:
    """In the background: the cut-out volume's passes compiled with the material's switches (``CUT_OUT_TERRAIN``,
    ``CUT_OUT_WATER``: on by default, ``volume_cutout.fxm``)."""
    from ...models import shadercache

    file_info = source.m_game.read_info(CUT_OUT_FILE)
    if file_info is None:
        raise FileNotFoundError(f"{shadercache.FX_DIR}{CUT_OUT_FILE}.fx")
    switches = shadercache.material_switches(file_info.references, source.m_game.m_materials, values)
    path = f"{shadercache.FX_DIR}{CUT_OUT_FILE}.fx"

    def stage(function: str, technique: str = "CutOut"):
        return source.pass_stages(path, switches, f"{technique}/{function}")[0]

    on = {k: values.get(k, "1") not in ("0", "false", "False") for k in ("CUT_OUT_TERRAIN", "CUT_OUT_WATER")}
    pixel_stage = stage("psCutOut")
    clear = (stage("vsCutOutClearDepth"), pixel_stage) if on["CUT_OUT_TERRAIN"] else None
    shadow = None
    if on["CUT_OUT_TERRAIN"]:  # the terrain's shadow cut where its depth is [I: the technique has no switch]
        own = source.own_stage(*SHADOW_CUT_OUT_PS)
        shadow = ((stage("vsCutOutMarkStencilShadow", "CutOutShadow"), own),
                  (stage("vsCutOutClearDepthShadow", "CutOutShadow"), own))  # fmt: skip
    return SCutOutStages(
        (stage("vsCutOutMarkStencil"), pixel_stage), clear, on["CUT_OUT_WATER"], file_info.references, shadow=shadow
    )


def part_key(part: dict) -> tuple:
    """What a part's draws depend on: its mesh file, its material number and the material's fields."""
    return part["mesh"], part.get("material"), tuple(sorted(part.get("values", {}).items()))


def level_meshes(files, path: str, numbers: list[int] | None, strict: bool = False) -> list:
    """The meshes of one level's file cut to the material ``numbers`` (None: whole); [] when it cannot be read
    (``strict``: it raises)."""
    from ...formats import rdmglb, rdmread

    try:
        meshes = rdmread.read_rdm(files.read(path)).meshes
    except Exception:  # a coarser level that does not read: the finer one stands in
        if strict:
            raise
        return []
    out = []
    for mesh in meshes:
        if not (mesh.vertex_count and mesh.indices):
            continue
        cut = (
            [rdmglb.material_indices(mesh, k) for k in numbers]
            if numbers is not None
            else [rdmglb.material_indices(mesh, None)]
        )
        indices = [i for part in cut for i in part]
        if indices:
            out.append(gamedraw.CMesh.of_rdm(mesh, indices))
    return out


def compile_part(source, files, part: dict, natives=None) -> SPartDraws:
    """In the background: a part's material compiled by its effect (``ShaderSource.draw_stages``), its maps read from
    ``files`` (the mod's over the game's), its mesh file's levels of detail (``_lodN.rdm``) cut to the part's material
    (``MaterialLODInfos``); ``CNoGBufferPass`` for a part the game's model shaders do not draw (no shader id known, no
    geometry). ``natives``: its shipped shaders found first (``objectsnative.part_shaders``); a part they draw compiles
    nothing of the current path."""
    from ...models import configtree, shaderview
    from ...sources.assetdb import number

    values = part.get("values") or {}
    shader_id = number(values.get("ShaderID"), -1)
    native = None
    if shader_id == CUT_OUT_VOLUME:  # its hole in the terrain, drawn after the terrain (``Drawn.cutouts``)
        native = _native_cutout(natives, source, values)
        stages = native.flags if isinstance(native, SPartShaders) else cutout_stages(source, values)
    else:
        stem = (shaderview.effect_of(shader_id) or shaderview.NATURE_FILES.get(shader_id)) if values else None
        if stem is None:
            raise shaderview.CNoGBufferPass("not a model of the game's shaders")
        native = _native_shaders(natives, stem, values)
        # a part drawn natively compiles nothing of the current path (native-backend.md 11.4)
        stages = native.flags if isinstance(native, SPartShaders) else source.draw_stages(stem, values)
        if natives is not None and stages.is_transparent and not isinstance(native, SPartShaders):  # from that compile
            native = _native_shaders(natives, stem, values, stages)
    levels: list = []
    for path in configtree.lod_files(part["mesh"], files.exists):
        numbers = cull.level_numbers(part.get("lod_materials") or [], part.get("material"), len(levels))
        found = level_meshes(files, path, numbers, strict=not levels)
        levels.append(found or (levels[-1] if levels else []))  # a first level without geometry: said below
    if not levels or not levels[0]:
        raise shaderview.CNoGBufferPass("no geometry for the material")
    boxes = [mesh.box() for mesh in levels[0]]
    box_min, box_max = np.min([box[0] for box in boxes], axis=0), np.max([box[1] for box in boxes], axis=0)
    sphere = ((box_min + box_max) / 2, float(np.linalg.norm(box_max - box_min) / 2))
    return SPartDraws(stages, source.material(stages.refs, values, files), levels, sphere, native)


def _native_shaders(natives, stem: str, values: dict, transparent=None):
    """A part's shipped shaders (``objectsnative.part_shaders``; a transparent part's with its compiled stages,
    ``transparent_shaders``), why not (an exception: the current path draws it) or None when not asked."""
    if natives is None:
        return None
    from ...render import objectsnative

    try:
        if transparent is not None:
            return objectsnative.transparent_shaders(natives, stem, values, transparent)
        return objectsnative.part_shaders(natives, stem, values)
    except Exception as exc:  # the current path draws it
        return exc


def _native_cutout(natives, source, values: dict):
    """A cut-out volume's shipped shaders (``objectsnative.cutout_shaders``, with our shadow pixel shader), why not
    or None, as ``_native_shaders``."""
    from ...models import shadercache
    from ...render import objectsnative

    if natives is None or os.environ.get(objectsnative.CUTOUT_ENV, "") == "0":
        return None
    try:
        own = source.own_stage(*SHADOW_CUT_OUT_PS)
        ps = shadercache.SCachedShader(own.entry, (), own.blob)
        water = values.get("CUT_OUT_WATER", "1") not in ("0", "false", "False")  # as ``cutout_stages`` reads it
        return objectsnative.cutout_shaders(natives, CUT_OUT_FILE, values, ps, water)
    except Exception as exc:  # the current path draws it
        return exc


def main_state(state) -> renderstates.SPassState:
    """A part's main pass state: its own technique's in the transparent pass, else the G-buffer's, testing for the
    depth its prepass laid where it has one (the cut-outs)."""
    if state.is_transparent:  # a native part's from its variant's technique (``objectsnative.SNativeStages.state``)
        return renderstates.pass_state(state.state if isinstance(state, SNativeStages) else state.pixel_stage.state)
    if state.prepass:  # its stages, or for a native part whether it has one
        return renderstates.SPassState(depth_write=False, depth_equal=True)
    return renderstates.GBUFFER_STATE


def shadow_stages(source) -> dict:
    """The terrain's shadow techniques and the cascades' analysis; the tessellated technique and the analysis None
    when they do not compile (the nodes then cast untessellated, the CPU fits the cascades)."""
    out = {"plain": source.stage_set(terraindraw.SHADOW)}
    for key, make in (("detailed", lambda: source.stage_set(terraindraw.SHADOW_DETAILED)), ("fit", source.shadow_fit)):
        try:
            out[key] = make()
        except Exception:  # said by its absence: no tessellated shadow, the CPU's fit
            out[key] = None
    return out


def native_shaders(game, compiler, mod=None):
    """The shipped shaders of the native backend's terrain parts (``render.terrainnative``, ``pagesnative``,
    ``bakenative``) read and reflected, in the ``CNativeShaders`` that keep them; None where none draws natively
    (switched off, no module). A part whose shaders do not read raises again when it is made, which says why.
    ``mod``: the island's files with its mod folder, whose precompiled variants count as shipped."""
    from ...render import (
        bakenative,
        copiesnative,
        inlandnative,
        objectsnative,
        oceannative,
        pagesnative,
        postnative,
        shadingnative,  # fmt: skip
        waternative,
    )

    parts = [terrainnative, pagesnative, bakenative, waternative, oceannative, copiesnative, inlandnative, shadingnative,
             postnative]  # fmt: skip
    parts = [candidate for candidate in parts if candidate.is_enabled()]
    if not parts and not objectsnative.is_enabled():  # the objects' shaders are found part by part (``compile_part``)
        return None
    from ...models import shadernative

    natives = shadernative.CNativeShaders(game, compiler, caches=(mod,) if mod is not None and mod is not game else ())
    for part in parts:
        try:
            part.shaders(natives)
        except Exception:  # that part alone draws the earlier way (``CGameIsland._make``)
            continue
    return natives
