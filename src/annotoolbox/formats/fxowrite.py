"""117's shader cache file (``.fxo``) written from scratch: every part of the ``EffectFile`` from the preprocessed
effect (its declarations, techniques and state blocks), the variant's switches and the compiled shaders with their
reflection, in the engine's shape (docs/rendering/shader-compile.md section 8, "Written from scratch").

``write(Variant)`` is the entry and gives the file's bytes; ``effect_file`` the FileDB tree. This module writes the
switch lists, the shaders, the resources and the constant buffers; ``formats.fxostate`` the state blocks,
``formats.fxotech`` the techniques; ``formats.fxodecl`` holds what they share.
"""

from __future__ import annotations

import struct

from . import filedb, fxo, fxostate, fxotech
from .fxodecl import (
    CONSTANT_TYPES,
    ITEM,
    NO_STAGE,
    RANGE_INDEX,
    STAGE_TYPES,
    A,
    CSource,
    CWriteError,
    SShader,
    SVariant,
    Tag,
    binding,
    dimension,
    layout_size,
    members,
    qualifier,
    resource_type,
    text,
    u8,
    u32,
)

__all__ = ["CSource", "CWriteError", "SShader", "SVariant", "effect_file", "write"]


def effect_file(variant: SVariant) -> Tag:
    """The FileDB root of ``v``'s cache file; ``CWriteError`` for a part the writer does not know."""
    source = CSource(variant.text)
    local, glob = resources(source)
    cbs = constant_buffers(source)
    try:
        blocks = fxostate.blocks(variant.text)
    except fxostate.CStateError as exc:
        raise CWriteError(str(exc)) from None
    effect = Tag(
        fxo.ROOT,
        [
            Tag(
                "GlobalDefines", [_define(define_name, define_value) for define_name, define_value in variant.graphics]
            ),
            Tag("LocalDefines", [_define(define_name, define_value) for define_name, define_value in variant.material]),
            Tag("Shaders", [_shader(shader) for shader in variant.shaders]),
            Tag("RenderStateBlocks", blocks),
            Tag("LocalResourceConstants", local),
            Tag("GlobalResourceConstants", glob),
            Tag("LocalConstantBuffers", cbs[0]),
            Tag("GlobalConstantBuffers", cbs[1]),
            Tag("TemplateConstantBuffers", cbs[2]),
            Tag("Techniques", fxotech.techniques(variant, source)),
        ],
    )
    return Tag("", [effect])


def write(variant: SVariant) -> bytes:
    """``v``'s cache file, with the name tables the engine writes (``fxo.engine_name_ids``)."""
    root = effect_file(variant)
    return filedb.write(root, *fxo.engine_name_ids(root))


def _define(name: str, value: str) -> Tag:
    item = Tag(ITEM, [A("Name", text(name))])
    if value:
        item.children.append(A("Value", text(value)))
    return item


def _shader(shader: SShader) -> Tag:
    if shader.call.stage not in STAGE_TYPES:
        raise CWriteError(f"{shader.call.function}: a {shader.call.stage} shader")
    item = Tag(ITEM, [A("Name", text(shader.call.function))])
    if shader.args:
        item.children.append(A("Values", text(shader.args)))
    item.children.append(A("Type", u32(STAGE_TYPES[shader.call.stage])))
    if shader.next_type != NO_STAGE:
        item.children.append(A("NextType", u32(shader.next_type)))
    item.children += [
        A("SourceHash", struct.pack("<Q", shader.source_hash)),
        A("ByteCodeHash", struct.pack("<Q", fxo.bytecode_hash(shader.bytecode))),
        A("ByteCode", shader.bytecode),
    ]
    return item


def _resource(item) -> Tag:
    resource = item.resource
    if resource.kind not in RANGE_INDEX:
        raise CWriteError(f"{resource.name}: a {resource_type(item)}")
    description = Tag("Desc", [
        A("Name", text(resource.name)),
        A("Type", u8(0x0E if resource.kind.startswith("TX") else 0x0F)),
        A("Count", u32(1)),
        binding(resource, RANGE_INDEX[resource.kind]),
    ])  # fmt: skip
    dimension_code = dimension(item)
    if dimension_code is not None:
        description.children.append(A("Dimension", u8(dimension_code)))
    description.children.append(A("ResourceBindFlag", u8(4 if resource.kind.endswith("UAV") else 2)))
    out = Tag(ITEM, [description])
    qualifier_value = qualifier(resource.words)
    if qualifier_value is not None:
        out.children.append(A("Qualifier", u8(qualifier_value)))
    return out


def resources(source: CSource) -> tuple[list[Tag], list[Tag]]:
    """``LocalResourceConstants`` and ``GlobalResourceConstants``: the effect's resources and the shared ones in
    declaration order (the bindless tables and the constant buffers are listed elsewhere)."""
    local, glob = [], []
    for item in source.m_items:
        resource = item.resource
        if resource.kind == "CB" or "bindless" in resource.words:
            continue
        (glob if "shared" in resource.words else local).append(_resource(item))
    return local, glob


def _cbuffer(item) -> Tag:
    resource = item.resource
    found = members(item)
    out = Tag(ITEM, [A("Name", text(resource.name))])
    qualifier_value = qualifier(resource.words)
    if qualifier_value is not None:
        out.children.append(A("Qualifier", u8(qualifier_value)))
    out.children.append(binding(resource, 0))
    if "shared" in resource.words or "shared_context" in resource.words:
        out.children.append(A("Shared", u8(1)))
    out.children.append(binding(resource, 0))  # written twice, as the engine writes it
    size = layout_size(found)
    out.children.append(A("Size", u32(size if "root_constant" in resource.words else (size + 15) // 16 * 16)))
    constants = []
    for type_name, name, count in found:
        description = Tag(
            "Desc", [A("Name", text(name)), A("Type", u8(CONSTANT_TYPES[type_name])), A("Count", u32(count))]
        )
        description.children += [Tag("GlobalBinding"), A("Dimension", u8(0xFF))]
        constants.append(Tag(ITEM, [description]))
    out.children.append(Tag("Constants", constants))
    return out


def constant_buffers(source: CSource) -> tuple[list[Tag], list[Tag], list[Tag]]:
    """``LocalConstantBuffers``, ``GlobalConstantBuffers`` (shared) and ``TemplateConstantBuffers`` (per context), in
    declaration order."""
    out = ([], [], [])
    for item in source.m_items:
        resource = item.resource
        if resource.kind != "CB":
            continue
        list_index = 2 if "shared_context" in resource.words else 1 if "shared" in resource.words else 0
        out[list_index].append(_cbuffer(item))
    return out
