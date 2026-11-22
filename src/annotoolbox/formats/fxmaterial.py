"""The structures Anno 117's engine generates from its material descriptions (``.fxm``): one per material, which a
shader reads from the bindless material table, and the table of each component's index (docs/rendering/
shader-compile.md, "The material structures").

``read_material_fields`` reads a description; ``material_header`` writes every structure as the engine lays it out:
the parameters in file order (a texture is its bindless index, a colour a ``float3``, a ``bool`` a ``float``, an
``int`` switch ``Define_<NAME>``), each vector kept inside a 16-byte row, the gaps filled with the words that hold
the ``bool`` switches (``DefineFlags<n>``) and then ``_padding<n>``, the size rounded up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class CMaterialError(ValueError):
    pass


# a parameter's type in the structure (a texture is its index into the bindless texture table)
FIELD_TYPES = {"color": "float3", "bool": "float", "Texture2D": "uint", "Texture2DArray": "uint"}
FIELD_TYPES |= {"TextureCube": "uint", "Texture3D": "uint"}
SIZES = {f"{base}{suffix}": 4 * int(suffix or 1) for base in ("float", "int", "uint") for suffix in ("", "2", "3", "4")}
ROW = 16  # a vector does not cross a row
WORD = 4
_ITEM_RE = re.compile(
    r"\b(define|Texture2D|Texture2DArray|TextureCube|Texture3D|color|float[234]?|int[234]?|uint[234]?|bool)\s+(\w+)"
    r"\s*(?:\[[^\]]*\]\s*)?\{"
)


@dataclass
class SMaterialFields:
    """The parameters of one material description (``.fxm``), as the structure the shaders read."""

    name: str
    fields: list[tuple[str, str]]  # (HLSL type, name), in file order
    flag_count: int = 0  # its bool switches: one bit each in the DefineFlags words
    layout: list[tuple[str, str]] = field(default_factory=list)  # the fields with the gap and end words


def read_material_fields(text: str) -> SMaterialFields:
    """A material description's name, parameters and switches, in file order."""
    text = re.sub(r"/\*.*?\*/", "", re.sub(r"//[^\n]*", "", text), flags=re.S)
    head = re.match(r"\s*material\s+(\w+)", text)
    if not head:
        raise CMaterialError("no material header")
    seen, fields, flags = set(), [], 0
    for item in _ITEM_RE.finditer(text):
        kind, name = item.groups()
        if name in seen:
            continue
        seen.add(name)
        if kind == "define":
            block = text[item.end() : text.find("}", item.end())]
            typed = re.search(r"\btype\s*=\s*(\w+)", block)
            value = typed.group(1) if typed else "bool"
            if value == "bool":
                flags += 1
            else:
                fields.append((value, f"Define_{name}"))
            continue
        fields.append((FIELD_TYPES.get(kind, kind), name))
    material = SMaterialFields(head.group(1), fields, flags)
    material.layout = lay_out(fields, (flags + 31) // 32)
    return material


def lay_out(fields: list[tuple[str, str]], flag_words: int, pad: str = "float") -> list[tuple[str, str]]:
    """The fields as the engine places them: a vector that would cross a row starts the next, the gap before it
    taken by the switch words still to place, then by padding; the rest of the switch words after the last field;
    the size rounded to a power of two up to 128 bytes, beyond that to a multiple of 128."""
    out, offset_bytes, flags, pads = [], 0, iter(range(flag_words)), iter(range(1 << 20))

    def gap_word() -> tuple[str, str]:
        flag_index = next(flags, None)
        return ("uint", f"DefineFlags{flag_index}") if flag_index is not None else (pad, f"_padding{next(pads)}")

    for kind, name in fields:
        size = SIZES.get(kind, WORD)
        if size > WORD and offset_bytes % ROW + size > ROW:
            while offset_bytes % ROW:
                out.append(gap_word())
                offset_bytes += WORD
        out.append((kind, name))
        offset_bytes += size
    for flag_index in flags:
        out.append(("uint", f"DefineFlags{flag_index}"))
        offset_bytes += WORD
    target = 1 << max(offset_bytes - 1, 1).bit_length() if offset_bytes <= 128 else -(-offset_bytes // 128) * 128
    while offset_bytes < max(target, ROW):
        out.append((pad, f"_padding{next(pads)}"))
        offset_bytes += WORD
    return out


def material_header(materials: list[SMaterialFields]) -> str:
    """The structures the engine makes from the material descriptions, and the table of each component's index
    (``MaterialComponentIndexTable``, by material name) the bindless macros read."""
    out = []
    for material in materials:
        body = "".join(
            f"\t{field_type} {field_name};\n"
            for field_type, field_name in material.layout or lay_out(material.fields, (material.flag_count + 31) // 32)
        )
        out.append(f"struct {material.name}Struct\n{{\n{body}}};\n")
    names = sorted(material.name for material in materials)
    table = lay_out([("uint", f"{name}Index") for name in names], 0, pad="uint")
    table_body = "".join(f"\t{field_type} {field_name};\n" for field_type, field_name in table)
    out.append("struct MaterialComponentIndexTable\n{\n" + table_body + "};\n")
    return "".join(out)
