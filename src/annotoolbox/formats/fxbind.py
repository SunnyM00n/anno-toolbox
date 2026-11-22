"""Where Anno 117's engine binds each resource of a pass: its descriptor tables and their registers, so that a shader
compiled from our reading binds what the game's binds (docs/rendering/shader-compile.md, "Bindings").

``bind(resources)`` takes the resources one pass reaches (``fxlink.CProgram.resources``) and returns each one's
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
class SResource:
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
    stripped = type_name.strip()
    is_read_write = stripped.startswith(("RW", "Append", "Consume", "RasterizerOrdered"))
    base = re.sub(r"^(RW|Append|Consume|RasterizerOrdered)", "", stripped)
    if stripped in ("cbuffer", "tbuffer") or stripped.startswith("ConstantBuffer"):
        return "CB"
    if base.startswith("Texture"):
        return "TX_UAV" if is_read_write else "TX_SRV"
    if base.startswith("StructuredBuffer"):
        return "SB_UAV" if is_read_write else "SB_SRV"
    if base.startswith("ByteAddressBuffer"):
        return "BA_UAV" if is_read_write else "BA_SRV"
    if base.startswith("Buffer"):
        return "BF_UAV" if is_read_write else "BF_SRV"
    if stripped.startswith("RaytracingAccelerationStructure"):
        return "AS_SRV"
    return None


def _global_register(resource: SResource) -> int:
    start = 0
    for kind in KINDS:
        if kind == resource.kind:
            return start + (resource.slot or 0)
        start += GLOBAL_TABLE[kind]
    raise ValueError(resource.kind)


def _table_of(resource: SResource) -> str:
    if resource.slot is not None and "global_table" in resource.words:
        return "global"
    if "bindless" in resource.words:
        if resource.name in BINDLESS_TEXTURES:
            return "bindless textures"
        return "bindless " + (BINDLESS_TABLES[resource.name][0] if resource.name in BINDLESS_TABLES else resource.file)
    if "root_constant" in resource.words:
        return "root"
    return "often changed" if "often_changed" in resource.words else "effect"


def _order(table: str) -> tuple[int, str]:
    """A table's place in a pass's root signature (its space when every table before it is there)."""
    fixed = ("global", "bindless textures", "bindless materials", "bindless raytrace")
    if table in fixed:
        return fixed.index(table), ""
    return (5, "") if table == "effect" else (6, "") if table == "often changed" else (4, table)


def bind(resources: list[SResource], declared: list[SResource] | None = None) -> dict[str, str]:
    """Each resource's register clause (``register(t3, space1)``) for one pass's resources (``declared``: every
    resource of the effect, whose order places a bindless table the engine's lists do not name)."""
    tables: dict[str, list[SResource]] = {}
    for resource in resources:  # a register the source names is the engine's to replace, its space too
        tables.setdefault(_table_of(resource), []).append(resource)
    out, space = {}, 0
    for name in sorted((table_name for table_name in tables if table_name != "root"), key=_order):
        members = tables[name]
        if name == "global":
            out |= {resource.name: _clause(resource.kind, _global_register(resource), space) for resource in members}
        elif name == "bindless textures":
            out |= {
                resource.name: _clause(resource.kind, BINDLESS_TEXTURES.index(resource.name), space)
                for resource in members
            }
        elif name.startswith("bindless"):
            out |= _bindless(members, declared or members, space)
        else:
            out |= _effect_table(members, space)
        space += 1
    roots = sorted(tables.get("root", ()), key=lambda resource: resource.name)
    out |= {resource.name: _clause(resource.kind, i, ROOT_SPACE) for i, resource in enumerate(roots)}
    return out


def _bindless(members: list[SResource], declared: list[SResource], space: int) -> dict[str, str]:
    """A bindless table: the engine's places where its lists name them, else the order its file declares its
    bindless resources in (an array takes its length)."""
    places, offset_by_file = {}, {}
    for resource in declared:
        if "bindless" in resource.words and resource.name not in BINDLESS_TABLES and resource.name not in places:
            places[resource.name] = offset_by_file.get(resource.file, 0)
            offset_by_file[resource.file] = places[resource.name] + max(resource.count, 1)
    return {
        resource.name: _clause(
            resource.kind,
            BINDLESS_TABLES[resource.name][1] if resource.name in BINDLESS_TABLES else places.get(resource.name, 0),
            space,
        )
        for resource in members
    }


def _effect_table(members: list[SResource], space: int) -> dict[str, str]:
    """An effect's own table: its resources by kind, then by name, each array its length."""
    out, offset = {}, 0
    for resource in sorted(members, key=lambda resource: (KINDS.index(resource.kind), resource.name)):
        out[resource.name] = _clause(resource.kind, offset, space)
        offset += max(resource.count, 1)
    return out


def _clause(kind: str, register: int, space: int) -> str:
    return f"register({LETTERS.get(kind, 't')}{register}, space{space})"
