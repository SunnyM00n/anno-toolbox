"""The techniques of a shader cache file written from scratch: each technique's header (vertex layout, topology,
render target sets, passthrough switches), its passes (shaders, state blocks, the resources each table binds, the
vertex elements read) and its root signature (the descriptor tables with their ranges and entries, root constants,
bindless tables), as the engine builds them (docs/rendering/shader-compile.md section 8, "Written from scratch").

``techniques(variant, source)`` is the entry (``formats.fxowrite``); ``CTables`` lays out one technique's tables from
its reach, a pass lists what its compiled shaders bind; ``declared`` reads a technique's passes.
"""

from __future__ import annotations

import itertools
import re
import struct
from dataclasses import dataclass, field

from . import fxbind, fxeffect, fxlink
from .fxodecl import (
    ITEM,
    NO_STAGE,
    STAGE_TYPES,
    A,
    CSource,
    CWriteError,
    SShader,
    SVariant,
    Tag,
    dimension,
    global_offset,
    layout_size,
    members,
    text,
    u8,
    u16,
    u32,
)

UNUSED = 0xFF  # an entry of the global table no shader of the technique reaches
# a table's type in the root signature, and a bindless table's own type
TABLE_GLOBAL, TABLE_BINDLESS, TABLE_EFFECT, TABLE_OFTEN = 0, 1, 2, 3
BINDLESS_TYPES = {"bindless textures": 4, "bindless materials": 2}
KNOWN_TABLES = ("global", "bindless textures", "bindless materials", "effect", "often changed")
GLOBAL_RANGES = [(kind, count) for kind, count in fxbind.GLOBAL_TABLE.items() if count]
# the two fixed bindless tables: a range a resource (fxbind's lists), -1 an unbounded array
BINDLESS_TEXTURE_RANGES = [("SB_SRV", 1), ("SB_SRV", 1), ("BA_UAV", 1), ("TX_SRV", -1)]
BINDLESS_MATERIAL_RANGES = [("SB_SRV", 1)] * (len(fxbind.BINDLESS_MATERIALS) - 1) + [("TX_SRV", -1)]
STAGE_VISIBILITY = {"vs": 1, "hs": 2, "ds": 3, "gs": 4, "ps": 5}
SRV_STATE_NON_PIXEL, SRV_STATE_PIXEL, UAV_STATE = 0x40, 0x80, 0x08
ROOT_FLAGS_GRAPHICS, ROOT_FLAGS_COMPUTE = 0x11, 0x12
_PASS_RE = re.compile(r"\bpass\s+(\w+)\s*\{")
_ASSIGN_RE = re.compile(r"(\w+)\s*=\s*([^;{}]+?)\s*;")
_TARGET_RE = re.compile(r"\bRenderTarget\s*(?:\[\s*(\d+)\s*\])?\s*=\s*([^;{}]+?)\s*;")
_BLOCK_RE = re.compile(r"\bSetStateBlock\s*\(\s*(\w+)\s*(?:,\s*(\d+)\s*)?\)")
DEPTH_FORMATS = ("D32F", "D24S8", "D16", "D32FS8")


@dataclass
class SPass:
    """A pass: its shaders, and its state block for each render target set (``SetStateBlock(sb, i)``)."""

    name: str
    calls: list[fxeffect.SShaderCall]
    blocks: dict[int, str]


@dataclass
class SDeclared:
    """A technique's header (its assignments and render target sets) and passes as its declaration states them."""

    assigns: dict[str, str]
    targets: dict[int, str] = field(default_factory=dict)
    passes: list[SPass] = field(default_factory=list)


def declared(body: str, technique: fxeffect.STechnique) -> SDeclared:
    """The technique's assignments before its first pass, its render target sets (``RenderTarget[i] = ...``) and
    each pass with its shaders and state blocks (``body``: its declaration, ``fxeffect.declarations``)."""
    starts = list(_PASS_RE.finditer(body))
    head = body[: starts[0].start()] if starts else body
    out = SDeclared(dict(_ASSIGN_RE.findall(head)), {int(i or 0): target for i, target in _TARGET_RE.findall(head)})
    calls = iter(technique.calls)
    for match in starts:
        close = fxeffect.matching(body, match.end() - 1, "{}")
        inner = body[match.end() : close]
        shader_count = len(fxeffect._SETTER_RE.findall(inner))
        blocks = {int(i or 0): block for block, i in _BLOCK_RE.findall(inner)}
        out.passes.append(SPass(match.group(1), _take(calls, shader_count, f"technique {technique.name}"), blocks))
    return out


def _take(items, count: int, where: str) -> list:
    """The next ``n`` of ``items``; ``CWriteError`` when fewer are left (the declaration and the effect disagree)."""
    taken = list(itertools.islice(items, count))
    if len(taken) != count:
        raise CWriteError(f"{where}: {count} shaders declared in a pass, {len(taken)} found")
    return taken


def next_stages(shader_pass: SPass) -> list[int]:
    """Each shader's next stage in its pass (``NO_STAGE`` for the last and for compute)."""
    graphics = [call for call in shader_pass.calls if call.stage != "cs"]
    out = []
    for call in shader_pass.calls:
        graphics_index = graphics.index(call) if call in graphics else -1
        out.append(
            STAGE_TYPES[graphics[graphics_index + 1].stage] if 0 <= graphics_index < len(graphics) - 1 else NO_STAGE
        )
    return out


def _target(name: str | None) -> list:
    """A render target set: a predefined one by name, or the formats of a dynamic one; unset, the back buffer."""
    if name is None:
        return [A("Type", text("RTT_BACKBUFFER"))]
    if name.startswith("RTT_"):
        return [A("Type", text("RTT_DEFAULT")), A("Predefined", text(name))]
    formats = [format_name.strip() for format_name in name.split(",") if format_name.strip()]
    colors = [format_name for format_name in formats if format_name not in DEPTH_FORMATS]
    depth = next((format_name for format_name in formats if format_name in DEPTH_FORMATS), "UNKNOWN")
    out = [A("Type", text("RTT_DEFAULT_DEPTH"))]
    out += [A(f"DynamicColor{i}", text(colors[i] if i < len(colors) else "UNKNOWN")) for i in range(8)]
    return out + [A("DynamicDepth", text(depth))]


@dataclass
class SSlot:
    """A resource's place in a technique's tables: table, offset in it, its kind's range and the offset there."""

    table: int
    offset: int
    range: int
    range_offset: int


class CTables:
    """One technique's descriptor tables as the engine lays them out from its reach: the global table and the fixed
    bindless ones whole, the effect's own by kind and name."""

    def __init__(self, source: CSource, functions: set[str], stages: dict[str, str]) -> None:
        self.m_source = source
        reach = source.m_program.resources(functions)
        self.m_samplers = any(
            fxlink._SAMPLER_RE.match(source.m_program.m_items[i].text) for i in source.m_program.reached(functions)
        )
        groups: dict[str, list[fxbind.SResource]] = {}
        for resource in reach:
            groups.setdefault(fxbind._table_of(resource), []).append(resource)
        self.m_roots = sorted(groups.pop("root", []), key=lambda resource: resource.name)
        unknown = sorted(set(groups) - set(KNOWN_TABLES))
        if unknown:
            raise CWriteError(f"a {unknown[0]} table")
        self.m_order = sorted(groups, key=fxbind._order)
        self.m_groups = groups
        self.m_slots: dict[str, SSlot] = {}
        self.m_layouts: list[tuple[str, list[tuple[str, int]], list]] = []
        for index, name in enumerate(self.m_order):
            self._lay_out(index, name, groups[name])
        self.m_stages_of: dict[str, set[str]] = {}  # which stages reach a resource: its state, a table's visibility
        for function, stage in stages.items():
            for resource in source.m_program.resources({function}):
                self.m_stages_of.setdefault(resource.name, set()).add(stage)
        self.m_compute = bool(stages) and all(stage_name == "cs" for stage_name in stages.values())

    def visibility(self, found) -> int | None:
        """The one graphics stage whose shaders reach ``found`` (a table, a root constant), None for all."""
        reached = set().union(*(self.m_stages_of.get(resource.name, set()) for resource in found)) if found else set()
        if self.m_compute or len(reached) != 1:
            return None
        return STAGE_VISIBILITY.get(next(iter(reached)))

    def table_visibility(self, name: str) -> int | None:
        if name == "global" or name.startswith("bindless"):  # bound for every stage
            return None
        return self.visibility(self.m_groups[name])

    def _lay_out(self, index: int, name: str, found: list[fxbind.SResource]) -> None:
        if name == "global":
            ranges = GLOBAL_RANGES
            entries: list = [None] * sum(count for _kind, count in ranges)
            starts = _starts(ranges)
            kinds = [kind for kind, _count in ranges]
            for resource in found:
                offset = global_offset(resource)
                entries[offset] = resource
                range_index = kinds.index(resource.kind)
                self.m_slots[resource.name] = SSlot(index, offset, range_index, offset - starts[range_index])
        elif name.startswith("bindless"):  # the engine's fixed tables: a range a resource, every declared one listed
            names = fxbind.BINDLESS_TEXTURES if name == "bindless textures" else fxbind.BINDLESS_MATERIALS
            by_name = self.m_source.m_by_name
            entries = [by_name[entry_name].resource if entry_name in by_name else None for entry_name in names]
            ranges = BINDLESS_TEXTURE_RANGES if name == "bindless textures" else BINDLESS_MATERIAL_RANGES
            for resource in found:
                offset = names.index(resource.name)
                self.m_slots[resource.name] = SSlot(index, offset, offset, 0)
        else:
            kinds = sorted({resource.kind for resource in found}, key=fxbind.KINDS.index)
            ordered = sorted(found, key=lambda resource: (fxbind.KINDS.index(resource.kind), resource.name))
            ranges = [
                (kind, sum(max(resource.count, 1) for resource in ordered if resource.kind == kind)) for kind in kinds
            ]
            entries, offset = [], 0
            starts = _starts(ranges)
            for resource in ordered:
                range_index = kinds.index(resource.kind)
                self.m_slots[resource.name] = SSlot(index, offset, range_index, offset - starts[range_index])
                entries += [resource] * max(resource.count, 1)
                offset += max(resource.count, 1)
        self.m_layouts.append((name, ranges, entries))

    def dimension(self, resource: fxbind.SResource) -> int | None:
        return dimension(self.m_source.m_by_name[resource.name])

    def state(self, resource: fxbind.SResource, compute: bool) -> int:
        """The state a pass wants a resource in: written; read by a compute pass; else by its table's visibility
        (the pixel stage, another one, every one)."""
        if resource.kind.endswith("UAV"):
            return UAV_STATE
        if compute:
            return SRV_STATE_NON_PIXEL
        seen = self.table_visibility(fxbind._table_of(resource))
        if seen is None:
            return SRV_STATE_NON_PIXEL | SRV_STATE_PIXEL
        return SRV_STATE_PIXEL if seen == STAGE_VISIBILITY["ps"] else SRV_STATE_NON_PIXEL

    def root_size(self, resource: fxbind.SResource) -> int:
        return layout_size(members(self.m_source.m_by_name[resource.name]))


def _entry_dimension(resource: fxbind.SResource, dimension_code: int | None, table: str) -> int:
    """A table entry's dimension: a texture's; a raw storage buffer of the global or a bindless table 10; every
    other buffer 3, as a 2D texture's."""
    if resource.kind.startswith("TX"):
        return dimension_code if dimension_code is not None else 3
    return 0x0A if resource.kind in ("BA_UAV", "BF_UAV") and table not in ("effect", "often changed") else 3


def _starts(ranges: list[tuple[str, int]]) -> list[int]:
    out, offset = [], 0
    for _kind, count in ranges:
        out.append(offset)
        offset += max(count, 0)
    return out


def _binding(slot: SSlot) -> Tag:
    tag = Tag("ShaderBinding")
    for name, value in (("TableIndex", slot.table), ("TableOffset", slot.offset), ("RangeIndex", slot.range),
                        ("RangeOffset", slot.range_offset)):  # fmt: skip
        if value:
            tag.children.append(A(name, u8(value)))
    return tag


def _pass_shaders(shader_pass: SPass, sources: list[int]) -> Tag:
    out = []
    for call, source, next_stage in zip(shader_pass.calls, sources, next_stages(shader_pass), strict=True):
        item = Tag(ITEM, [A("Name", text(call.function)), A("GroupID", u32(NO_STAGE))])
        if STAGE_TYPES[call.stage]:
            item.children.append(A("Type", u32(STAGE_TYPES[call.stage])))
        item.children.append(A("NextType", u32(next_stage)))
        args = "".join(call.args).replace(" ", "")
        if args:
            item.children.append(A("UniformParameterValues", text(args)))
        item.children.append(A("SourceHash", struct.pack("<Q", source)))
        out.append(item)
    return Tag("Shaders", out)


def _bound(sources: list[int], shaders: dict[int, SShader]) -> set[str]:
    """The names the shaders ``sources`` bind (their reflection)."""
    out: set[str] = set()
    for source in sources:
        if source in shaders:
            out |= {binding["name"] for binding in shaders[source].reflection.get("bindings", [])}
    return out


def _lists(tables: CTables, bound: set[str], compute: bool) -> tuple[dict[str, list[Tag]], int]:
    """A pass's binding lists by table (the global constant buffers only counted), in each table's order."""
    lists: dict[str, list[Tag]] = {
        list_name: [] for list_name in ("ConstantBuffers", "ConstantsSRV", "ConstantsUAV", "GlobalsSRV")
    }
    lists["GlobalsUAV"] = []
    global_constant_buffer_count = 0
    for name in tables.m_order:
        if name.startswith("bindless"):
            continue
        for resource in sorted(
            (resource for resource in tables.m_groups[name] if resource.name in bound),
            key=lambda resource: tables.m_slots[resource.name].offset,
        ):
            slot = tables.m_slots[resource.name]
            if resource.kind == "CB":
                if name == "global":
                    global_constant_buffer_count += 1
                else:
                    lists["ConstantBuffers"].append(Tag(ITEM, [A("Name", text(resource.name)), _binding(slot)]))
                continue
            key = ("Globals" if name == "global" else "Constants") + ("UAV" if resource.kind.endswith("UAV") else "SRV")
            item = Tag(ITEM, [A("Name", text(resource.name))])
            dimension_code = tables.dimension(resource)
            if resource.kind.endswith("UAV") or dimension_code is None:
                item.children.append(A("RangeType", u8(fxbind.KINDS.index(resource.kind))))
            if dimension_code is not None:
                item.children.append(A("Dimension", u8(dimension_code)))
            item.children += [_binding(slot), A("ResourceState", u32(tables.state(resource, compute)))]
            lists[key].append(item)
    return lists, global_constant_buffer_count


def _pass(shader_pass: SPass, sources: list[int], tables: CTables, shaders: dict[int, SShader]) -> Tag:
    bound = _bound(sources, shaders)
    compute = all(call.stage == "cs" for call in shader_pass.calls)
    lists, global_constant_buffer_count = _lists(tables, bound, compute)
    roots = []
    for index, resource in enumerate(tables.m_roots):
        if resource.name in bound:
            item = Tag(ITEM, [A("Name", text(resource.name))])
            if index:
                item.children.append(A("Index", u32(index)))
            roots.append(item)
            item.children.append(A("NumElements", u32(tables.root_size(resource) // 4)))
    out = Tag(ITEM, [A("Name", text(shader_pass.name)), _pass_shaders(shader_pass, sources)])
    out.children.append(
        Tag(
            "StateBlocks",
            [A(f"StateBlockName{i}", text(block_name)) for i, block_name in sorted(shader_pass.blocks.items())],
        )
    )
    out.children += [Tag(list_name, tags) for list_name, tags in lists.items()]
    out.children.append(Tag("RootConstants", roots))
    if "global" in tables.m_order and any(resource.name in bound for resource in tables.m_groups["global"]):
        out.children.append(A("BindGlobalDescriptorTable", u8(1)))
    bindless = [BINDLESS_TYPES[name] for name in tables.m_order if name.startswith("bindless")]
    out.children.append(
        Tag("BindlessDescriptorTableTypes", [Tag(ITEM, [A("Type", u8(bindless_type))]) for bindless_type in bindless])
    )
    out.children.append(A("m_Type", u8(1 if compute else 0)))
    for name, count in (
        ("CB", global_constant_buffer_count),
        ("SR", len(lists["GlobalsSRV"])),
        ("UA", len(lists["GlobalsUAV"])),
    ):
        if count:
            out.children.append(A(f"NumGlobalConstants{name}", u16(count)))
    vertex_shader = next(
        (
            shaders[source]
            for call, source in zip(shader_pass.calls, sources, strict=True)
            if call.stage == "vs" and source in shaders
        ),
        None,
    )
    elements = []
    for name, index in _inputs(vertex_shader):
        item = Tag(ITEM, [A("Type", text(name))])
        if index:
            item.children.append(A("Index", u32(index)))
        elements.append(item)
    out.children.append(Tag("UsedVertexLayoutElements", [Tag("Elements", elements)]))
    return out


def _inputs(vertex_shader: SShader | None) -> list[tuple[str, int]]:
    """The vertex shader's inputs as the vertex layout's elements (the system values but the position left out)."""
    if vertex_shader is None:
        return []
    out = []
    for name, index, *_rest in vertex_shader.reflection.get("inputs", []):
        upper = name.upper()
        if upper.startswith("SV_") and upper != "SV_POSITION":
            continue
        out.append(("SV_POSITION" if upper == "POSITION" else upper, index))
    return out


def _table(tables: CTables, name: str, ranges: list[tuple[str, int]], entries: list) -> Tag:
    """One descriptor table of a root signature: its ranges, its entries, its visibility and size."""
    table_type = {"global": TABLE_GLOBAL, "often changed": TABLE_OFTEN}.get(name, TABLE_EFFECT)
    table_type = TABLE_BINDLESS if name.startswith("bindless") else table_type
    range_tags = []
    for (range_kind, count), offset in zip(ranges, _starts(ranges), strict=True):
        inner = [A("NumDescriptors", u32(count if count >= 0 else 0xFFFFFFFF))]
        if offset:
            inner.append(A("EntryOffset", u32(offset)))
        range_tags.append(Tag(ITEM, [A("#32768", u32(fxbind.KINDS.index(range_kind))), Tag(ITEM, inner)]))
    entry_tags = []
    for resource in entries:
        entry_tag = Tag(ITEM)
        if resource is not None:
            if resource.count == 0:
                entry_tag.children.append(A("ArraySize", u32(0xFFFFFFFF)))
            elif resource.count > 1:
                entry_tag.children.append(A("ArraySize", u32(resource.count)))
            if not (name.startswith("bindless") and resource.kind == "SB_SRV"):  # a bindless buffer has none
                entry_tag.children.append(
                    A("ResourceDimension", u8(_entry_dimension(resource, tables.dimension(resource), name)))
                )
        elif name == "global":
            entry_tag.children.append(A("ResourceDimension", u8(UNUSED)))
        entry_tags.append(entry_tag)
    body = [Tag("Ranges", range_tags), Tag("Entries", entry_tags)]
    seen = tables.table_visibility(name)
    if seen is not None:
        body.append(A("Visibility", u32(seen)))
    unbounded = any(count < 0 for _kind, count in ranges)
    body.append(A("NumDescriptors", u32(0xFFFFFFFF if unbounded else sum(count for _kind, count in ranges))))
    if name.startswith("bindless"):
        body.append(A("Flags", u32(3)))
    return Tag(ITEM, [A("#32768", u32(table_type)), Tag(ITEM, body)])


def _root_signature(tables: CTables, has_passes: bool) -> Tag:
    out = Tag("RootSignature")
    if not has_passes:
        out.children += [Tag("DescriptorTables"), Tag("RootConstants"), Tag("BindlessTables")]
        return out
    out.children.append(A("RootFlags", u32(ROOT_FLAGS_COMPUTE if tables.m_compute else ROOT_FLAGS_GRAPHICS)))
    out.children.append(Tag("DescriptorTables", [_table(tables, *layout) for layout in tables.m_layouts]))
    roots = []
    for index, resource in enumerate(tables.m_roots):
        item = Tag(ITEM, [A("NumElements", u32(tables.root_size(resource) // 4))])
        if index:
            item.children.append(A("Offset", u32(index)))
        seen = tables.visibility([resource])
        if seen is not None:
            item.children.append(A("Visibility", u32(seen)))
        roots.append(item)
    out.children.append(Tag("RootConstants", roots))
    if "global" in tables.m_order:
        out.children.append(A("GlobalTableIndex", u32(tables.m_order.index("global"))))
    bindless = []
    for index, name in enumerate(tables.m_order):
        if name.startswith("bindless"):
            item = Tag(ITEM, [A("Index", u32(index))] if index else [])
            item.children.append(A("Type", u8(BINDLESS_TYPES[name])))
            bindless.append(item)
    out.children.append(Tag("BindlessTables", bindless))
    if tables.m_samplers:
        out.children.append(A("UseStaticSamplers", u8(1)))
    if any(resource.name == "cbMaterialIndex" for resource in tables.m_roots):
        out.children.append(A("m_UseMaterialIndexRootConstant", u8(1)))
    return out


def techniques(variant: SVariant, source: CSource) -> list[Tag]:
    """The ``Techniques`` items of ``variant``, in the effect's order."""
    shaders = {shader.source_hash: shader for shader in variant.shaders}
    bodies = fxeffect.declarations(variant.text).techniques
    out = []
    for technique in source.m_effect.techniques:
        declaration = declared(bodies.get(technique.name, ""), technique)
        if any(call.stage not in STAGE_TYPES or call.stage == "lib" for call in technique.calls):
            raise CWriteError(f"technique {technique.name}: a ray tracing or unknown stage")
        sources = iter(variant.techniques.get(technique.name, []))
        tables = CTables(source, technique.functions, {call.function: call.stage for call in technique.calls})
        item = Tag(ITEM, [A("Name", text(technique.name))])
        layout = declaration.assigns.get("VertexLayout")
        item.children.append(Tag("VertexLayout", [A("#32768", text(layout))] if layout else []))
        item.children.append(A("Topology", text(declaration.assigns.get("Topology", "TriList"))))
        item.children += [Tag(f"RenderTargets{i}", _target(declaration.targets.get(i))) for i in range(8)]
        item.children.append(
            Tag("ShaderDefines", [Tag(ITEM, [A("Name", text(define_name))]) for define_name in technique.passthrough])
        )
        item.children.append(
            Tag(
                "Passes",
                [
                    _pass(
                        shader_pass,
                        _take(sources, len(shader_pass.calls), f"technique {technique.name}"),
                        tables,
                        shaders,
                    )
                    for shader_pass in declaration.passes
                ],
            )  # fmt: skip
        )
        many = f" | {len(declaration.passes)} passes" if len(declaration.passes) > 1 else ""
        item.children.append(A("RootSignatureName", text(f"{variant.stem}.fx | {technique.name}{many}")))
        item.children.append(_root_signature(tables, bool(declaration.passes)))
        out.append(item)
    return out
