"""Where Anno 117's engine binds each resource of a pass: its descriptor tables and their registers, so that a shader
compiled from our reading binds what the game's binds (docs/rendering/shader-compile.md, "Bindings").

``bind(resources)`` takes the resources one pass reaches (``fxlink.Program.resources``) and returns each one's
register: the global table's fixed slots, the bindless tables' fixed places, the effect's own tables in kind and
name order, root constants in space 1024; spaces are numbered in the order of the tables the pass has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# a table's descriptor kinds, in the order a table lists them (the engine's descriptor range types)
KINDS = ("CB", "TX_SRV", "BF_SRV", "SB_SRV", "BA_SRV", "AS_SRV", "TX_UAV", "BF_UAV", "SB_UAV", "BA_UAV")
LETTERS = {"CB": "b", "TX_UAV": "u", "BF_UAV": "u", "SB_UAV": "u", "BA_UAV": "u"}  # the rest are t
# the global table's ranges: how many slots of each kind (defines.fxh "GLOBAL TABLE REGISTERS"; the shipped
# passes' root signatures give the same: 6 at 0, 49 at 6, 18 at 55, 2 at 73, 1 at 75, 1 at 76)
GLOBAL_TABLE = {"CB": 6, "TX_SRV": 49, "BF_SRV": 0, "SB_SRV": 18, "BA_SRV": 0, "AS_SRV": 0}
GLOBAL_TABLE |= {"TX_UAV": 2, "BF_UAV": 0, "SB_UAV": 1, "BA_UAV": 1}
# the bindless tables: a resource's place is the engine's, not the sources' order (read off the shipped shaders;
# the places nothing ships with are the gaps between them)
BINDLESS_TEXTURES = ("cTextureAtlas", "cCurrentBindlessTextureLODs", "cTextureFeedback", "cBindlessTextures")
BINDLESS_MATERIALS = (
    "cMaterialComponentIndices", "cCloth", "cCutOut", "cFlipbook", "cFrameCycle", "cLowPoly", "cModelCommon",
    "cModelDecal", "cModelGlass", "cModelVAT2", "cModelVAT3", "cModelWater", "cModelSkin", "cModelHair", "cModelPlant",
    "cTechArtist01", "cTechArtist02", "cTechArtist03", "cTechArtist04", "cTechArtistExample", "cTechArtistInternal",
    "cPhysicallyBasedSubstance", "cTerrainDecal", "cVolumeCutout", "cModelVoxelTextures",
)  # fmt: skip
BINDLESS_RAYTRACE = (
    "cTLAS", "cRTModelInstances", "cRTGeomInstances", "cRTClothInstances", "cRTTerrainInstances", "cRTInlandWaters",
    "cRTTerrainBakedDiffuse", "cRTTerrainBakedNormal", "cRTTerrainBakedSpec", "cRTInfo", "cRTHitFeedback",
    "cRTHitboxes", "cRTBuffers",
)  # fmt: skip
# the UI's table: (name, slots); its texture arrays take 2, 8 and 8
BINDLESS_UI = (
    ("cPhoenixSceneColorTex", 1), ("cPhoenixGlobalData", 1), ("cPhoenixSceneData", 1), ("cPhoenixDrawCallData", 1),
    ("cSlugMatrixData", 1), ("cPhoenixSoftMaskData", 1), ("cPhoenixSoftMaskIndices", 1),
    ("cPhoenixTextureOverlayData", 1), ("cPhoenixDistanceFieldData", 1), ("cPhoenixColorizationData", 1),
    ("cPhoenixGlowData", 1), ("cPhoenixIngameWorldDarkenData", 1), ("cPhoenixIslandShadingData", 1),
    ("cPhoenixMatrixTransformData", 1), ("cPhoenixEffectData", 1), ("cPhoenixEffectIndexData", 1),
    ("cPhoenixEffectTableIndices", 1), ("cHatchingTextures", 2), ("cSlugCurveTextures", 8), ("cSlugBandTextures", 8),
    ("cPhoenixLocalTextures", 1),
)  # fmt: skip
BINDLESS_TABLES = {n: ("materials", i) for i, n in enumerate(BINDLESS_MATERIALS)}
BINDLESS_TABLES |= {n: ("raytrace", i) for i, n in enumerate(BINDLESS_RAYTRACE)}
BINDLESS_TABLES |= {n: ("ui", sum(c for _n, c in BINDLESS_UI[:i])) for i, (n, _c) in enumerate(BINDLESS_UI)}
ROOT_SPACE = 1024


@dataclass(frozen=True)
class Resource:
    """A resource declaration of the effect's HLSL."""

    name: str
    kind: str  # KINDS
    words: frozenset[str]  # the dialect's storage words on it (global_table, bindless, root_constant, often_changed)
    slot: int | None  # the register the source names (the global table's slot), None for none
    space: int | None  # a space the source names itself (kept as written)
    count: int  # an array's length, 0 for an unbounded one, 1 for one
    file: str  # the file it is declared in


def kind_of(type_name: str) -> str | None:
    """The descriptor kind of an HLSL resource type, None for anything else (a sampler is the engine's own)."""
    t = type_name.strip()
    rw = t.startswith(("RW", "Append", "Consume", "RasterizerOrdered"))
    base = re.sub(r"^(RW|Append|Consume|RasterizerOrdered)", "", t)
    if t in ("cbuffer", "tbuffer") or t.startswith("ConstantBuffer"):
        return "CB"
    if base.startswith("Texture"):
        return "TX_UAV" if rw else "TX_SRV"
    if base.startswith("StructuredBuffer"):
        return "SB_UAV" if rw else "SB_SRV"
    if base.startswith("ByteAddressBuffer"):
        return "BA_UAV" if rw else "BA_SRV"
    if base.startswith("Buffer"):
        return "BF_UAV" if rw else "BF_SRV"
    if t.startswith("RaytracingAccelerationStructure"):
        return "AS_SRV"
    return None


def _global_register(r: Resource) -> int:
    start = 0
    for kind in KINDS:
        if kind == r.kind:
            return start + (r.slot or 0)
        start += GLOBAL_TABLE[kind]
    raise ValueError(r.kind)


def _table_of(r: Resource) -> str:
    if r.slot is not None and "global_table" in r.words:
        return "global"
    if "bindless" in r.words:
        if r.name in BINDLESS_TEXTURES:
            return "bindless textures"
        return "bindless " + (BINDLESS_TABLES[r.name][0] if r.name in BINDLESS_TABLES else r.file)
    if "root_constant" in r.words:
        return "root"
    return "often changed" if "often_changed" in r.words else "effect"


def _order(table: str) -> tuple[int, str]:
    """A table's place in a pass's root signature (its space when every table before it is there)."""
    fixed = ("global", "bindless textures", "bindless materials", "bindless raytrace")
    if table in fixed:
        return fixed.index(table), ""
    return (5, "") if table == "effect" else (6, "") if table == "often changed" else (4, table)


def bind(resources: list[Resource], declared: list[Resource] | None = None) -> dict[str, str]:
    """Each resource's register clause (``register(t3, space1)``) for one pass's resources (``declared``: every
    resource of the effect, whose order places a bindless table the engine's lists do not name)."""
    tables: dict[str, list[Resource]] = {}
    for r in resources:  # a register the source names is the engine's to replace, its space too
        tables.setdefault(_table_of(r), []).append(r)
    out, space = {}, 0
    for name in sorted((t for t in tables if t != "root"), key=_order):
        members = tables[name]
        if name == "global":
            out |= {r.name: _clause(r.kind, _global_register(r), space) for r in members}
        elif name == "bindless textures":
            out |= {r.name: _clause(r.kind, BINDLESS_TEXTURES.index(r.name), space) for r in members}
        elif name.startswith("bindless"):
            out |= _bindless(members, declared or members, space)
        else:
            out |= _effect_table(members, space)
        space += 1
    roots = sorted(tables.get("root", ()), key=lambda r: r.name)
    out |= {r.name: _clause(r.kind, i, ROOT_SPACE) for i, r in enumerate(roots)}
    return out


def _bindless(members: list[Resource], declared: list[Resource], space: int) -> dict[str, str]:
    """A bindless table: the engine's places where its lists name them, else the order its file declares its
    bindless resources in (an array takes its length)."""
    places, at = {}, {}
    for r in declared:
        if "bindless" in r.words and r.name not in BINDLESS_TABLES and r.name not in places:
            places[r.name] = at.get(r.file, 0)
            at[r.file] = places[r.name] + max(r.count, 1)
    return {
        r.name: _clause(
            r.kind, BINDLESS_TABLES[r.name][1] if r.name in BINDLESS_TABLES else places.get(r.name, 0), space
        )
        for r in members
    }


def _effect_table(members: list[Resource], space: int) -> dict[str, str]:
    """An effect's own table: its resources by kind, then by name, each array its length."""
    out, at = {}, 0
    for r in sorted(members, key=lambda r: (KINDS.index(r.kind), r.name)):
        out[r.name] = _clause(r.kind, at, space)
        at += max(r.count, 1)
    return out


def _clause(kind: str, register: int, space: int) -> str:
    return f"register({LETTERS.get(kind, 't')}{register}, space{space})"
