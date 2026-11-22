"""RDM vertex layouts by name: ``P4h_N4b_G4b_B4b_T2h`` to its elements and back, and one element's values.

``parse`` turns a layout name into the elements a vertex description holds, ``decode`` / ``encode`` read and write
one element of one vertex (floats for half and float types, the four bytes of a packed vector). ``KNOWN`` lists the
layouts the games ship and what uses them; ``VANILLA_117`` the element lists of 117's own files (rdm.md "Vertices").
"""

from __future__ import annotations

import re
import struct

from .rdmread import LETTERS, TYPE_SIZES, SVertexElement

# Packed four-byte vectors (type 5): the subtype by usage, as the shipped files have them (rdm.md "Vertices").
DIRECTION, COLOUR, SECOND_COLOUR, CLOTH_COLOUR, INDICES, WEIGHTS = 6, 2, 6, 4, 0, 2
_KINDS = {"h": 6, "f": 7, "s": 2}
_STRUCT = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "e", 7: "f", 8: "Q", 9: "I"}

# fmt: off
VANILLA_117: dict[str, tuple[tuple[int, int, int, int], ...]] = {
    "P4h": ((0, 6, 0, 4),),
    "P4h_N4b_G4b_B4b_T2h": ((0, 6, 0, 4), (1, 5, 6, 1), (2, 5, 6, 1), (3, 5, 6, 1), (4, 6, 0, 2)),
    "P4h_N4b_G4b_B4b_T2h_C4b": ((0, 6, 0, 4), (1, 5, 6, 1), (2, 5, 6, 1), (3, 5, 6, 1), (4, 6, 0, 2), (5, 5, 2, 1)),
    "P4h_N4b_G4b_B4b_T2h_C4b_C4b": ((0, 6, 0, 4), (1, 5, 6, 1), (2, 5, 6, 1), (3, 5, 6, 1), (4, 6, 0, 2), (5, 5, 2, 1), (5, 5, 6, 1)),
    "P4h_N4b_G4b_B4b_T2h_I4b_W4b": ((0, 6, 0, 4), (1, 5, 6, 1), (2, 5, 6, 1), (3, 5, 6, 1), (4, 6, 0, 2), (7, 5, 0, 1), (6, 5, 2, 1)),
    "P3f_N3f_G3f_B3f_T2f_C4c": ((0, 7, 0, 3), (1, 7, 0, 3), (2, 7, 0, 3), (3, 7, 0, 3), (4, 7, 0, 2), (5, 5, 4, 1)),
    "P3f_N3f_G3f_B3f_T2f_T2f_C4c": ((0, 7, 0, 3), (1, 7, 0, 3), (2, 7, 0, 3), (3, 7, 0, 3), (4, 7, 0, 2), (4, 7, 0, 2), (5, 5, 4, 1)),
}

# Layout name -> what ships it (2070 counts: rdm.md "Vertices"; 117: its own files; decal detail: the 1800 decals).
KNOWN: dict[str, str] = {
    "P4h": "shadow and cut-out helpers (2070, 117)",
    "P4h_N4b_G4b_B4b_T2h": "static meshes: the standard layout (2070, 2205, 1800, 117)",
    "P4h_N4b_G4b_B4b_T2h_C4b": "static meshes with one vertex colour (117)",
    "P4h_N4b_G4b_B4b_T2h_C4b_C4b": "plants: two vertex colours (1800, 117)",
    "P4h_N4b_G4b_B4b_T2h_I4b": "skinned, one bone per vertex (2070, 1800, 117)",
    "P4h_N4b_G4b_B4b_T2h_I4b_W4b": "skinned with weights, up to four bones (2070, 1800, 117)",
    "P4h_N4b_T2h": "static without a tangent frame (2070)",
    "P4h_N4b_T2h_I4b": "skinned without a tangent frame (2070)",
    "P4h_N4b_T2h_I4b_W4b": "skinned with weights, no tangent frame (2070 portraits)",
    "P4h_N4b_G4b_B4b_T2h_C4c": "static with a colour (2070)",
    "P4h_N4b_T2h_C4c": "static with a colour, no tangent frame (2070)",
    "P4h_N4b_T2h_C4c_C4c": "waterfalls (2070)",
    "P4h_T2h_C4b": "decal detail: grey 0.5 is neutral (1800)",
    "P3f_T2f_C4c": "cloth (2070)",
    "P3f_T2f_T2f_C4c": "cloth with an emblem (2070)",
    "P3f_N3f_G3f_B3f_T2f_C4c": "cloth (1800, 117)",
    "P3f_N3f_G3f_B3f_T2f_T2f_C4c": "cloth with a logo (117)",
}
# fmt: on

_TOKEN = re.compile(r"([PNGBTCWI])(\d)([hfsbc])")


class CLayoutError(ValueError):
    """A layout name that does not describe RDM vertex elements."""


def parse(name: str) -> tuple[SVertexElement, ...]:
    """The elements of a layout name, with the subtypes the games use (a second ``C4b`` is subtype 6, as 117's
    plants; ``C4c`` subtype 4). Raises ``CLayoutError`` for a name that is not one."""
    out: list[SVertexElement] = []
    colours = 0
    for token in name.split("_"):
        match = _TOKEN.fullmatch(token)
        if not match:
            raise CLayoutError(f"{token!r} in {name!r} is not an element (letter, count, kind: P4h, N4b, T2f ...)")
        letter, count, kind = match.group(1), int(match.group(2)), match.group(3)
        usage = LETTERS.index(letter)
        if kind in "bc":
            if count != 4:
                raise CLayoutError(f"{token!r}: a packed byte vector always counts 4")
            out.append(SVertexElement(usage, 5, _packed_subtype(usage, kind, colours), 1))
            colours += usage == 5
            continue
        if letter in "CWI":
            raise CLayoutError(f"{token!r}: colours, weights and bone indices are packed bytes (C4b, W4b, I4b)")
        out.append(SVertexElement(usage, _KINDS[kind], 0, count))
    if not out or out[0].usage != 0:
        raise CLayoutError(f"{name!r}: a layout starts with the position")
    return tuple(out)


def _packed_subtype(usage: int, kind: str, colours_before: int) -> int:
    if usage == 5:
        if kind == "c":
            return CLOTH_COLOUR
        return COLOUR if colours_before == 0 else SECOND_COLOUR
    if kind == "c":
        raise CLayoutError(f"{LETTERS[usage]}4c: only colours are written 4c")
    return {1: DIRECTION, 2: DIRECTION, 3: DIRECTION, 4: DIRECTION, 6: WEIGHTS, 7: INDICES}.get(usage, 0)


def name_of(elements: tuple[SVertexElement, ...] | list[SVertexElement]) -> str:
    return "_".join(element.code for element in elements)


def stride_of(elements: tuple[SVertexElement, ...] | list[SVertexElement]) -> int:
    return sum(TYPE_SIZES[element.type] * element.count for element in elements)


def _format(element: SVertexElement) -> str:
    if element.type == 5:
        return "<4B"
    return f"<{element.count}{_STRUCT[element.type]}"


def decode(element: SVertexElement, raw: bytes) -> tuple:
    """One element of one vertex: floats for half and float types, integers otherwise, four bytes when packed."""
    return struct.unpack(_format(element), raw)


def encode(element: SVertexElement, values) -> bytes:
    """The bytes of one element of one vertex (the inverse of ``decode``; floats are rounded to the type)."""
    if element.type in (6, 7):
        return struct.pack(_format(element), *(float(value) for value in values))
    return struct.pack(_format(element), *(int(value) for value in values))


def weight_bytes(weights) -> tuple[int, ...]:
    """Skin weights as bytes summing to 255 (to 4 for four zeros), in their proportions: each rounded down, the units
    left over going to the largest remainders."""
    total = sum(weights) or 1.0
    exact = [weight * 255 / total for weight in weights]
    out = [int(channel_value) for channel_value in exact]
    for k in sorted(range(len(out)), key=lambda k: out[k] - exact[k])[: 255 - sum(out)]:
        out[k] += 1
    return tuple(out)


def direction(raw: bytes | tuple) -> tuple[float, float, float]:
    """The vector a packed direction holds: each byte ``b / 127.5 - 1`` (the 4th byte is not part of it)."""
    return (raw[0] / 127.5 - 1.0, raw[1] / 127.5 - 1.0, raw[2] / 127.5 - 1.0)
