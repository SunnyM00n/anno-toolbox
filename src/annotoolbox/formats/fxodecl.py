"""What a shader cache file written from scratch is made of (``formats.fxowrite``): the compiled shaders and the
variant (``SShader``, ``SVariant``), the preprocessed effect read once (``CSource``), and the facts the file states about
a declaration: a resource's type, dimension, range and place, a constant buffer's members and packed size (the
engine's codes read off the shipped files; docs/rendering/shader-compile.md section 8, "Written from scratch").
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from . import filedb, fxbind, fxeffect, fxlink

A, Tag = filedb.SAttrib, filedb.STag
ITEM = "#1"  # the list items' tag (formats.fxo)
STAGE_TYPES = {"vs": 0, "hs": 1, "ds": 2, "gs": 3, "ps": 6, "cs": 7, "lib": 8, "as": 10, "ms": 11}
NO_STAGE = 0xFFFFFFFF


class CWriteError(ValueError):
    """Something of the effect the writer does not know how the engine writes (the variant is left to the game)."""


def u8(value: int) -> bytes:
    return bytes([value & 0xFF])


def u16(value: int) -> bytes:
    return struct.pack("<H", value)


def u32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def text(string: str) -> bytes:
    return string.encode("utf-8")


@dataclass
class SShader:
    """One compiled shader: the call it was compiled for, its source hash, its container and reflection
    (``infra.dxc`` ``Reflection`` as a dict) and the stage after it in its pass (a vertex shader's pixel shader)."""

    call: fxeffect.SShaderCall
    source_hash: int
    bytecode: bytes
    reflection: dict = field(default_factory=dict)
    next_type: int = NO_STAGE

    @property
    def args(self) -> str:
        return "".join(self.call.args).replace(" ", "")


@dataclass
class SVariant:
    """What a file is written from: the effect's stem, the switch lists, the preprocessed text, the shaders once per
    source hash in the order the techniques first use them, and per technique its passes' source hashes."""

    stem: str
    graphics: tuple
    material: tuple
    text: str
    shaders: list[SShader]
    techniques: dict[str, list[int]]


class CSource:
    """The preprocessed effect, read once: techniques, the HLSL's items and its declared resources."""

    def __init__(self, preprocessed: str) -> None:
        self.m_text = preprocessed
        self.m_effect = fxeffect.split_effect(preprocessed)
        self.m_program = fxlink.CProgram(self.m_effect.hlsl)
        self.m_items = [item for item in self.m_program.m_items if item.resource is not None]
        self.m_by_name = {item.resource.name: item for item in self.m_items}


_TYPE_RE = re.compile(
    r"\b((?:RW|Append|Consume|RasterizerOrdered)?(?:Texture\w*|StructuredBuffer|ByteAddressBuffer|Buffer|"
    r"RaytracingAccelerationStructure))\b"
)
# a resource's dimension code by its declared HLSL type
DIMENSIONS = {
    "Texture1D": 2, "Texture2D": 3, "Texture2DArray": 4, "Texture2DMS": 5, "Texture2DMSArray": 6, "Texture3D": 7,
    "TextureCube": 8, "TextureCubeArray": 9, "RWByteAddressBuffer": 10, "RWStructuredBuffer": 10, "RWBuffer": 10,
    "RWTexture2D": 13, "RWTexture2DArray": 14, "RWTexture3D": 15, "AppendStructuredBuffer": 10,
    "ConsumeStructuredBuffer": 10,
}  # fmt: skip
# a descriptor kind's range in the global table's list
RANGE_INDEX = {"BA_SRV": 0, "TX_SRV": 1, "SB_SRV": 2, "TX_UAV": 3, "SB_UAV": 4, "BA_UAV": 5, "BF_SRV": 0, "BF_UAV": 5}
QUALIFIERS = (("global_table", 1), ("often_changed", 3), ("root_constant", 6))


def resource_type(item: fxlink.SItem) -> str:
    return _TYPE_RE.search(item.text).group(1)


def dimension(item: fxlink.SItem) -> int | None:
    """A resource's dimension code (3 for a constant buffer), None for a buffer that has none."""
    if item.resource.kind == "CB":
        return 3
    return DIMENSIONS.get(resource_type(item))


def qualifier(words) -> int | None:
    return next((qualifier_value for word, qualifier_value in QUALIFIERS if word in words), None)


def global_offset(resource: fxbind.SResource) -> int:
    """A global-table resource's place in the table (its kind's start plus its slot)."""
    return int(re.search(r"\d+", fxbind.bind([resource])[resource.name]).group(0))


def binding(resource: fxbind.SResource, range_index: int) -> Tag:
    """Where a declaration says it is bound: the global table's place, a slot in a space the source names; a slot
    elsewhere is the engine's to give (offsets 0)."""
    global_binding = Tag("GlobalBinding", [A("TableIndex", u8(resource.space or 0))])
    placed = "global_table" in resource.words or resource.space is not None
    if resource.slot is not None:
        offset = (
            global_offset(resource) if "global_table" in resource.words and resource.kind != "CB" else resource.slot
        )
        global_binding.children.append(A("TableOffset", u8(offset if placed else 0)))
    global_binding.children.append(A("RangeIndex", u8(range_index)))
    if resource.slot is not None:
        global_binding.children.append(A("RangeOffset", u8(resource.slot if placed else 0)))
    return global_binding


# a constant's type code
CONSTANT_TYPES = {
    "float": 1, "float2": 2, "float3": 3, "float4": 4, "int": 5, "int2": 6, "int3": 7, "int4": 8, "uint": 9,
    "uint2": 10, "uint3": 11, "uint4": 12, "float4x4": 13,
}  # fmt: skip
_MEMBER_RE = re.compile(r"^\s*(?:row_major\s+|column_major\s+|precise\s+)*(\w+)\s+(\w+)\s*(?:\[\s*(\w+)\s*\])?")


def members(item: fxlink.SItem) -> list[tuple[str, str, int]]:
    """A constant buffer's members: (type, name, count)."""
    body = item.text[item.text.index("{") + 1 : item.text.rindex("}")]
    out = []
    for part in body.split(";"):
        match = _MEMBER_RE.match(part)
        if match:
            if match.group(1) not in CONSTANT_TYPES:
                raise CWriteError(f"{item.resource.name}.{match.group(2)}: a constant of type {match.group(1)}")
            try:
                count = int(match.group(3), 0) if match.group(3) else 1
            except ValueError:  # an array length that is a macro or constant name
                raise CWriteError(f"{item.resource.name}.{match.group(2)}: an array length {match.group(3)}") from None
            out.append((match.group(1), match.group(2), count))
    return out


def _type_size(type_name: str) -> tuple[int, int]:
    """(registers a value takes: a matrix's columns, else 1; the bytes of one register's part)."""
    match = re.match(r"([a-z]+?)(\d)?(?:x(\d))?$", type_name)
    rows, columns = int(match.group(2) or 1), int(match.group(3) or 0)
    if columns:  # column-major: one register per column, each the rows' size
        return columns, rows * 4
    return 1, rows * 4


def layout_size(found: list[tuple[str, str, int]]) -> int:
    """The packed size of a constant buffer's members (HLSL's rules: nothing crosses a 16-byte row, an array's
    elements and a matrix's columns each start a row)."""
    offset = 0
    for type_name, _name, count in found:
        registers, size = _type_size(type_name)
        if count > 1 or registers > 1:
            offset = (offset + 15) // 16 * 16
            offset += 16 * (count * registers - 1) + size
        else:
            if offset // 16 != (offset + size - 1) // 16:
                offset = (offset + 15) // 16 * 16
            offset += size
    return offset
