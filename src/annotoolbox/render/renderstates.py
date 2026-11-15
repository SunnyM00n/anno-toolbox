"""A technique's render states as a draw on QRhi needs them: the effect's ``renderstateblock`` (depth test and write,
blends per target, the colour write mask, the stencil test) read into a ``PassState`` (``pass_state``,
``stencil_of``), and QRhi's blend factors and stencil operations of the effect's words. ``gamedraw.GameModel`` takes
a ``PassState``; the words: docs/tool-suite.md section 21, "The game-shader path".
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QRhiGraphicsPipeline


@dataclass(frozen=True)
class Stencil:
    """A technique's stencil test, the same for both faces, by the effect's words: the compare, the operation where
    the test and the depth test pass, where the test fails and where the depth test fails, the reference value and
    the read and write masks (a pass that sets one needs a target with stencil: ``gameframe.GameFrame(stencil=True)``)."""

    func: str = "ALWAYS"
    pass_op: str = "KEEP"
    ref: int = 0
    mask: int = 0xFF
    write_mask: int = 0xFF
    fail: str = "KEEP"
    depth_fail: str = "KEEP"


@dataclass(frozen=True)
class PassState:
    """A technique's states as a draw needs them: the depth test (the engine's depth: 1 near, 0 far) and its write,
    per colour target its blend (None: off, else QRhi's colour source and destination and alpha source and
    destination factors), the culling."""

    depth_test: bool = True
    depth_write: bool = True
    blends: tuple = ()
    cull: str = "none"
    depth_equal: bool = False  # draw only where the depth lies already (after a depth prepass)
    colour: bool = True  # False: depth only (a depth prepass)
    writes: tuple = ()  # per colour target the channels written (D3D's bits: 1 red .. 8 alpha; None or missing: all)
    depth_always: bool = False  # the depth test passes everywhere (a cut-out's depth clear)
    stencil: Stencil | None = None


GBUFFER_STATE = PassState()
_COMPARES = {"NEVER": "Never", "LESS": "Less", "EQUAL": "Equal", "LESS_EQUAL": "LessOrEqual", "GREATER": "Greater",
             "NOT_EQUAL": "NotEqual", "GREATER_EQUAL": "GreaterOrEqual", "ALWAYS": "Always"}  # fmt: skip
_STENCIL_OPS = {"KEEP": "Keep", "ZERO": "StencilZero", "REPLACE": "Replace", "INCR_SAT": "IncrementAndClamp",
                "DECR_SAT": "DecrementAndClamp", "INVERT": "Invert", "INCR": "IncrementAndWrap",
                "DECR": "DecrementAndWrap"}  # fmt: skip


def stencil_state(s: Stencil):
    """QRhi's stencil operations of a face for ``s``."""
    op, compare = QRhiGraphicsPipeline.StencilOp, QRhiGraphicsPipeline.CompareOp

    def of(word: str):
        return getattr(op, _STENCIL_OPS[word])

    state = QRhiGraphicsPipeline.StencilOpState()
    state.failOp, state.depthFailOp, state.passOp = of(s.fail), of(s.depth_fail), of(s.pass_op)
    state.compareOp = getattr(compare, _COMPARES[s.func])
    return state


def stencil_of(states: dict[str, str]) -> Stencil | None:
    """A ``renderstateblock``'s stencil test (``StencilEnable`` and its words; ``0x`` numbers or the defines' values
    as the preprocessor left them), None without one."""
    s = {k.lower(): v.upper() for k, v in states.items()}
    if s.get("stencilenable", "FALSE") != "TRUE":
        return None

    def number(key: str) -> int:
        bits = 0
        for word in s.get(key, "0xFF").strip("() ").split("|"):  # "0x01 | 0x08": the defines as preprocessed
            try:
                bits |= int(word.strip("() "), 0)
            except ValueError:  # a word no preprocessor turned into a number: every bit
                return 0xFF
        return bits

    return Stencil(s.get("stencilfunc", "ALWAYS"), s.get("stencilpass", "KEEP"), number("stencilref"),
                   number("stencilmask"), number("stencilwritemask"), s.get("stencilfail", "KEEP"),
                   s.get("stencilzfail", "KEEP"))  # fmt: skip


_FACTORS = {"ZERO": "Zero", "ONE": "One", "SRC_COLOR": "SrcColor", "INV_SRC_COLOR": "OneMinusSrcColor",
            "SRC_ALPHA": "SrcAlpha", "INV_SRC_ALPHA": "OneMinusSrcAlpha", "DST_ALPHA": "DstAlpha",
            "INV_DST_ALPHA": "OneMinusDstAlpha", "DST_COLOR": "DstColor", "INV_DST_COLOR": "OneMinusDstColor",
            "SRC1_ALPHA": "Src1Alpha", "INV_SRC1_ALPHA": "OneMinusSrc1Alpha"}  # fmt: skip


def blend_factor(name: str, default: str = "ONE"):
    """QRhi's blend factor of an effect's word (``SRC_ALPHA``, ``dest_alpha``, ``inv_dst_color``: any case, ``DEST``
    read as ``DST``); ``default`` (a word) for one no table knows."""
    word = name.upper().replace("DEST", "DST")
    return getattr(QRhiGraphicsPipeline.BlendFactor, _FACTORS.get(word, _FACTORS[default]))


def pass_state(states: dict[str, str], targets: int = 2) -> PassState:
    """A ``renderstateblock``'s states (``fxeffect.Effect.state_of``) as a ``PassState``: what a block does not name
    keeps the engine's default (depth tested and written, no blend). The culling stays off: the view's mirror turns
    the winding (docs/rendering/frame-117-viewer.md 3)."""
    s = {k.lower(): v.upper() for k, v in states.items()}

    def factor(key: str, default: str):
        return blend_factor(s.get(key, default))

    blends = []
    for i in range(targets):
        on = s.get(f"alphablendenable[{i}]", s.get("alphablendenable", "FALSE")) == "TRUE"
        src, dst = factor(f"srcblend[{i}]", "ONE"), factor(f"dstblend[{i}]", "ZERO")
        blends.append((src, dst, factor(f"srcblendalpha[{i}]", s.get(f"srcblend[{i}]", "ONE")),
                       factor(f"dstblendalpha[{i}]", s.get(f"dstblend[{i}]", "ZERO"))) if on else None)  # fmt: skip
    writes = tuple(
        int(s[f"colorwriteenable[{i}]"], 0) if f"colorwriteenable[{i}]" in s else None for i in range(targets)
    )
    return PassState(s.get("zenable", "TRUE") == "TRUE", s.get("zwriteenable", "TRUE") == "TRUE", tuple(blends),
                     writes=writes if any(w is not None for w in writes) else ())  # fmt: skip
