"""The render state blocks of a shader cache file, encoded from their text as the engine encodes them: each
assignment in the order written becomes (state, slot, value), a state at its default is left out
(docs/rendering/shader-compile.md section 8, "Written from scratch"; the tables read off every shipped block).

``blocks(preprocessed)`` is the entry: the ``RenderStateBlocks`` items of a preprocessed effect.
"""

from __future__ import annotations

import re

from . import filedb, fxeffect

A, Tag = filedb.SAttrib, filedb.STag

# the engine's state ids
STATES = {
    "ZEnable": 1, "ZFunc": 2, "ZWriteEnable": 3, "FillMode": 4, "CullMode": 5, "AlphaBlendEnable": 6,
    "IndependentBlendEnable": 7, "SrcBlend": 9, "DstBlend": 10, "BlendOp": 11, "SrcBlendAlpha": 12,
    "DstBlendAlpha": 13, "BlendOpAlpha": 14, "StencilEnable": 15, "StencilFail": 17, "StencilZFail": 18, "StencilPass": 19,
    "StencilFunc": 20, "StencilRef": 21, "StencilMask": 22, "StencilWriteMask": 23, "CCWStencilFail": 24,
    "CCWStencilZFail": 25, "CCWStencilPass": 26, "CCWStencilFunc": 27, "ScissorTestEnable": 30, "DepthBias": 32,
    "ColorWriteEnable": 33,
}  # fmt: skip
# the value names, one enum of the engine's: comparisons, blend operations and factors, culling, fill
VALUES = {
    "FALSE": 0, "TRUE": 1, "NEVER": 2, "LESS": 3, "EQUAL": 4, "LESS_EQUAL": 5, "GREATER": 6, "NOT_EQUAL": 7,
    "GREATER_EQUAL": 8, "ALWAYS": 9, "ADD": 10, "SUBTRACT": 11, "REV_SUBTRACT": 12, "MIN": 13, "MAX": 14, "ZERO": 15,
    "ONE": 16, "SRC_COLOR": 17, "INV_SRC_COLOR": 18, "SRC_ALPHA": 19, "INV_SRC_ALPHA": 20, "DST_ALPHA": 21,
    "INV_DST_ALPHA": 22, "DST_COLOR": 23, "INV_DST_COLOR": 24, "SRC_ALPHA_SAT": 25, "SRC1_COLOR": 26,
    "INV_SRC1_COLOR": 27, "SRC1_ALPHA": 28, "INV_SRC1_ALPHA": 29, "CW": 30, "CCW": 31, "NONE": 32, "SOLID": 38,
    "WIREFRAME": 39,
}  # fmt: skip
# the stencil operations are an enum of their own (KEEP shares its number with NONE)
STENCIL_OPS = {
    "KEEP": 32,
    "ZERO": 33,
    "REPLACE": 34,
    "INCR": 35,
    "INCR_SAT": 35,
    "DECR": 36,
    "DECR_SAT": 36,
    "INVERT": 37,
}
_STENCIL_OP_STATES = {
    "StencilFail",
    "StencilZFail",
    "StencilPass",
    "CCWStencilFail",
    "CCWStencilZFail",
    "CCWStencilPass",
}
# a state at this value is not written; the masks have none (always written)
DEFAULTS = {
    "ZEnable": 1, "ZFunc": 6, "ZWriteEnable": 1, "FillMode": 38, "CullMode": 31, "AlphaBlendEnable": 0,
    "IndependentBlendEnable": 0, "BlendOp": 10, "SrcBlend": 16, "DstBlend": 15, "BlendOpAlpha": 10,
    "SrcBlendAlpha": 16, "DstBlendAlpha": 15, "StencilEnable": 0, "StencilFail": 32, "StencilZFail": 32,
    "StencilPass": 32, "StencilFunc": 9, "StencilRef": 0, "CCWStencilFail": 32, "CCWStencilZFail": 32,
    "CCWStencilPass": 32, "CCWStencilFunc": 9, "ScissorTestEnable": 0, "DepthBias": 0, "ColorWriteEnable": 0xF,
}  # fmt: skip
_ASSIGN_RE = re.compile(r"(\w+)\s*(?:\[\s*(\d+)\s*\])?\s*=\s*([^;]+?)\s*;")
_NUMBER_RE = re.compile(r"[0-9a-fA-FxX|&()<+ -]+")


class CStateError(ValueError):
    """A state or value the tables do not know (the block cannot be encoded)."""


def value_of(state: str, text: str) -> int:
    if state in _STENCIL_OP_STATES and text in STENCIL_OPS:
        return STENCIL_OPS[text]
    if text in VALUES:
        return VALUES[text]
    if state == "DepthBias" and re.fullmatch(r"-?\d+", text):
        return abs(int(text))  # the one shipped bias (-1) is stored as 1
    if _NUMBER_RE.fullmatch(text):
        try:
            return _number(text) & 0xFFFFFFFF
        except ValueError:
            pass
    raise CStateError(f"{state} = {text}: no value known")


def _number(text: str) -> int:
    """A numeric expression of the blocks (``0x10 | 0x20``): numbers joined by ``|``."""
    out = 0
    for part in text.replace("(", " ").replace(")", " ").split("|"):
        out |= int(part.strip(), 0)
    return out


def encode(body: str) -> list[tuple[int, int, int]]:
    """A block's (state, slot, value) list from its text."""
    out = []
    for name, slot, value in _ASSIGN_RE.findall(body):
        if name not in STATES:
            raise CStateError(f"state {name}: not known")
        code = value_of(name, value)
        if DEFAULTS.get(name) == code:
            continue
        out.append((STATES[name], int(slot or 0), code))
    return out


def _state(state: int, slot: int, value: int) -> Tag:
    item = Tag("#1", [A("Type", state.to_bytes(4, "little"))])
    if slot:
        item.children.append(A("Slot", slot.to_bytes(4, "little")))
    item.children.append(A("Value", value.to_bytes(4, "little")))
    return item


def blocks(preprocessed: str) -> list[Tag]:
    """The ``RenderStateBlocks`` of a preprocessed effect, in the order declared."""
    found = fxeffect.declarations(preprocessed).blocks
    return [
        Tag("#1", [A("Name", name.encode("utf-8")), Tag("States", [_state(*state) for state in encode(body)])])
        for name, body in found.items()
    ]
