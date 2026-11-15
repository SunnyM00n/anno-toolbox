"""Anno 117's effect files (``.fx``, ``.fxh``) as plain HLSL a DirectX compiler takes: includes inlined, the dialect
turned into HLSL, the techniques read out (docs/rendering/shader-compile.md, "The effect file").

``effect_source`` builds the whole text of one variant (its switches as defines, the dialect's macros, the engine's
samplers, the material structures (``fxmaterial``), the engine's intrinsics header, the file with its includes); after
the preprocessor ``split_effect`` takes the techniques out and leaves the HLSL, which ``fxlink`` cuts down to what one
pass shader uses. The reading is ours, not the game's own effect compiler: most shaders come out byte for byte as the
game's (shader-compile.md, "How close").
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .fxmaterial import MaterialFields, material_header

# the shader setters of a pass -> (profile stage, shader model); the models are the game's (shader-compile.md)
STAGES = {
    "SetVertexShader": ("vs", "6_2"),
    "SetPixelShader": ("ps", "6_2"),
    "SetComputeShader": ("cs", "6_2"),
    "SetGeometryShader": ("gs", "6_2"),
    "SetHullShader": ("hs", "6_2"),
    "SetDomainShader": ("ds", "6_2"),
    "SetMeshShader": ("ms", "6_5"),
    "SetAmplificationShader": ("as", "6_5"),
    "SetRayGenShader": ("lib", "6_5"),
    "SetMissShader": ("lib", "6_5"),
}
# semantics the dialect writes with their index in brackets, TEXCOORD(0) for TEXCOORD0
INDEXED_SEMANTICS = (
    "TEXCOORD", "COLOR", "NORMAL", "POSITION", "TANGENT", "BINORMAL", "BLENDINDICES", "BLENDWEIGHT",
    "SV_Target", "SV_Position", "SV_Depth", "SV_DepthLessEqual", "SV_DepthGreaterEqual",
)  # fmt: skip
# the dialect's storage words, which say which descriptor table a resource goes into: kept as markers for fxbind
TABLE_WORDS = ("shared", "shared_context", "global_table", "root_constant", "root_descriptor", "bindless")
TABLE_WORDS += ("often_changed",)
MARKER = "__fx_"  # a table word after the preprocessor (fxlink reads and drops it)
_PRELUDE_TAIL = """#ifdef MSAA_ENABLED
#define Texture2D_PotentialMS Texture2DMS
#else
#define Texture2D_PotentialMS Texture2D
#endif
#define RWTEX_FORMAT(format)
#define FX_CAT_(a, b) a##b
#define FX_CAT(a, b) FX_CAT_(a, b)
"""
# the engine's fixed samplers (every shader sees them; registers as the shipped cache's shaders bind them)
SAMPLERS = (
    ("cPointClampSampler", 0), ("cLinearClampSampler", 3), ("cLinearWrapSampler", 4), ("cLinearMirrorSampler", 5),
    ("cLinearBorderSampler", 6), ("cAnisoClampSampler", 7), ("cAnisoWrapSampler", 8), ("cAnisoMirrorSampler", 9),
    ("cAnisoBorderSampler", 10), ("cLSAOSampler", 12),
)  # fmt: skip
COMPARISON_SAMPLERS = (("cLinearClampCmpSampler", 11),)
# a macro that ends in a token paste: the game's preprocessor takes it, the standard one refuses it
_TRAILING_PASTE_RE = re.compile(r"(?m)^([ \t]*#[ \t]*define\b[^\n]*?)[ \t]*##[ \t]*$", re.M)
_INCLUDE_RE = re.compile(r'^([ \t]*#[ \t]*include[ \t]*)"([^"\n]+)"[^\n]*$', re.M)
# the function with its arguments, or named alone (``SetComputeShader(csFFTRadix2Vertical);``)
_SETTER_RE = re.compile(r"\b(" + "|".join(STAGES) + r")\s*\(\s*(\w+)\s*(?:\(([^()]*(?:\([^()]*\)[^()]*)*)\))?\s*\)")
_PASSTHROUGH_RE = re.compile(r"\bPassthroughDefines\s*=\s*([^;]*);")
_STATE_BLOCK_RE = re.compile(r"\bSetStateBlock\s*\(\s*(\w+)\s*\)")
_STATE_RE = re.compile(r"(\w+(?:\[\d+\])?)\s*=\s*([\w.]+)\s*;")
Read = Callable[[str], bytes | None]


class EffectError(ValueError):
    pass


def prelude() -> str:
    """The macros that turn the dialect into HLSL (storage words marked, bracketed semantic indices joined) and
    the engine's samplers."""
    lines = [f"#define {w} {MARKER}{w}" for w in TABLE_WORDS]
    lines += [f"#define {s}(index) {s}##index" for s in INDEXED_SEMANTICS]
    lines += [f"SamplerState {n} : register(s{r});" for n, r in SAMPLERS]
    lines += [f"SamplerComparisonState {n} : register(s{r});" for n, r in COMPARISON_SAMPLERS]
    return "\n".join(lines) + "\n" + _PRELUDE_TAIL


def expand_includes(read: Read, path: str, _stack: tuple[str, ...] = ()) -> str:
    """The file at ``path`` with every ``#include "..."`` inlined (relative to the including file), each piece
    under a ``#line`` naming its file, so the compiler's messages name the file and line they come from."""
    data = read(path)
    if data is None:
        raise EffectError(f"{_stack[-1] if _stack else path}: include {path} not found")
    text = _pastes(_TRAILING_PASTE_RE.sub(r"\1", data.decode("utf-8", "replace").replace("\r\n", "\n")))
    out, last, line = [f'#line 1 "{path}"\n'], 0, 1
    for m in _INCLUDE_RE.finditer(text):
        out.append(text[last : m.start()])
        line += text.count("\n", last, m.start())
        target = _resolve(read, path, m.group(2))
        if read(target) is None:  # an error only where the preprocessor takes the branch (ffx_core's GLSL)
            out.append(f"#error include {m.group(2)} not found")
        elif target not in _stack and target != path:
            out.append(expand_includes(read, target, (*_stack, path)))
        out.append(f'\n#line {line + 1} "{path}"')
        last = m.end()
    out.append(text[last:])
    return "".join(out)


_PASTE_RE = re.compile(r"(FX_CAT\((?:[^()]|\([^()]*\))*\)|\b\w+)[ \t]*##[ \t]*(\w+)")


def _pastes(text: str) -> str:
    """Token pastes in macros as the game's preprocessor makes them: a macro name beside ``##`` is expanded first
    (``c##TECH_ART_MATERIAL_NAME``), which the standard preprocessor only does through a second macro."""
    out, in_define = [], False
    for line in text.split("\n"):
        in_define = in_define or bool(re.match(r"[ \t]*#[ \t]*define\b", line))
        if in_define and "##" in line and "__VA_ARGS__" not in line:
            head = re.match(r"([ \t]*#[ \t]*define[ \t]+\w+(?:\([^)]*\))?)", line)
            cut = head.end() if head else 0
            body = line[cut:]
            while "##" in body:
                body, n = _PASTE_RE.subn(r"FX_CAT(\1, \2)", body, count=1)
                if not n:
                    break
            line = line[:cut] + body
        out.append(line)
        in_define = in_define and line.rstrip().endswith("\\")
    return "\n".join(out)


INCLUDE_DIRS = ("data/shaders/include", "data/shaders")
INTRINSICS = "data/shaders/include/intrinsics_dx.fxh"


def _resolve(read: Read, including: str, name: str) -> str:
    """An include's path: beside the including file, else in the include folder (as the game finds it)."""
    name = name.replace("\\", "/")
    first = posixpath.normpath(posixpath.join(posixpath.dirname(including), name))
    if read(first) is not None:
        return first
    for folder in INCLUDE_DIRS:
        other = posixpath.normpath(posixpath.join(folder, name))
        if read(other) is not None:
            return other
    return first


def includes_of(read: Read, path: str) -> set[str]:
    """Every file ``path`` includes, directly or through another."""
    found: set[str] = set()
    todo = [path]
    while todo:
        current = todo.pop()
        data = read(current)
        text = (data or b"").decode("utf-8", "replace")
        for m in _INCLUDE_RE.finditer(text):
            target = _resolve(read, current, m.group(2))
            if target not in found:
                found.add(target)
                todo.append(target)
    return found


def define_lines(defines: list[tuple[str, str]]) -> str:
    """``#define`` lines for (name, value) pairs; an empty value defines the name alone."""
    return "".join(f"#define {n} {v}\n" if v else f"#define {n}\n" for n, v in defines)


def effect_source(read: Read, path: str, defines: list[tuple[str, str]], materials: list[MaterialFields]) -> str:
    """The whole text of one variant of an effect file, ready for the preprocessor; the engine puts its intrinsics
    header (GLSL's built-ins written in HLSL: ``equal``, ``MatrixFromRowVectors``, ...) before every shader."""
    intrinsics = expand_includes(read, INTRINSICS) if read(INTRINSICS) is not None else ""
    return (
        '#line 1 "<defines>"\n'
        + define_lines(defines)
        + prelude()
        + material_header(materials)
        + intrinsics
        + expand_includes(read, path)
    )


@dataclass(frozen=True)
class ShaderCall:
    """One shader of a pass: its setter's stage, the function and the literal arguments of its uniform parameters."""

    stage: str
    model: str
    function: str
    args: tuple[str, ...] = ()

    @property
    def profile(self) -> str:
        return f"{self.stage}_{self.model}"


@dataclass
class Technique:
    name: str
    passthrough: tuple[str, ...] = ()  # PassthroughDefines: switches the technique's shaders are compiled with
    calls: list[ShaderCall] = field(default_factory=list)
    state: str = ""  # the first pass's SetStateBlock: a renderstateblock of the effect

    @property
    def functions(self) -> set[str]:
        """The shader functions of all its passes: what they reach makes the technique's tables (fxbind)."""
        return {c.function for c in self.calls}


@dataclass
class Effect:
    """An effect file after the preprocessor: its HLSL and its techniques."""

    hlsl: str
    techniques: list[Technique]
    blocks: dict[str, dict[str, str]] = field(default_factory=dict)  # renderstateblock name -> its states (lower case)

    def state_of(self, technique: Technique) -> dict[str, str]:
        """The render states of a technique's first pass (property as written -> value; empty: the defaults)."""
        return self.blocks.get(technique.state, {})


def assignments(text: str) -> dict[str, str]:
    """The ``property = value;`` assignments of a render-state block: lower-cased property (``blendenable[0]`` keeps
    its index) -> value. The first assignment counts: a later one is an ``#ifdef``'s other arm."""
    out: dict[str, str] = {}
    for key, value in _STATE_RE.findall(text):
        out.setdefault(key.lower(), value)
    return out


def matching(text: str, open_at: int, pair: str = "[]") -> int:
    """The index of the bracket of ``pair`` that closes the one at ``open_at``."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == pair[0]:
            depth += 1
        elif text[i] == pair[1]:
            depth -= 1
            if depth == 0:
                return i
    raise EffectError("an unclosed bracket")


def block_end(text: str, at: int) -> int:
    """The index after the ``{...}`` block that opens at or after ``at`` (and a ``;`` right after it)."""
    close = matching(text, text.index("{", at), "{}")
    k = close + 1
    while k < len(text) and text[k].isspace():
        k += 1
    return k + 1 if k < len(text) and text[k] == ";" else close + 1


_TOP_RE = re.compile(r"(?m)^[ \t]*(technique|renderstateblock|MaterialRef)\b[ \t]*(\w*)")


def split_effect(preprocessed: str) -> Effect:
    """The techniques (with their passes' shaders) and the HLSL left when the effect-only parts are taken out."""
    out, techniques, last = [], [], 0
    blocks: dict[str, dict[str, str]] = {}
    pos = depth_at = depth = 0
    while True:
        m = _TOP_RE.search(preprocessed, pos)
        if m is None:
            break
        depth += _depth(preprocessed, depth_at, m.start())
        depth_at = pos = m.end()
        if depth != 0:  # a word inside a function or struct: not ours
            continue
        out.append(preprocessed[last : m.start()])
        if m.group(1) == "MaterialRef":
            end = preprocessed.index(";", m.end()) + 1
        else:
            end = block_end(preprocessed, m.end())
            if m.group(1) == "technique":
                techniques.append(_technique(m.group(2), preprocessed[m.end() : end]))
            elif m.group(1) == "renderstateblock":
                blocks[m.group(2)] = assignments(preprocessed[m.end() : end])
        out.append("\n" * preprocessed.count("\n", m.start(), end))  # keeps the line numbers
        last = pos = depth_at = end
    out.append(preprocessed[last:])
    hlsl = _STRING_RE.sub(_debug_string, _DEFERRED_RE.sub(_deferred, "".join(out)))
    hlsl = _GS_RE.sub(_geometry_shader, _INTERPOLATION_RE.sub(r"\1", hlsl))
    return Effect(hlsl, techniques, blocks)


# the dialect's geometry shader: its primitives as attributes, one input and one output (the stream) by name
_GS_RE = re.compile(
    r"\[\s*InputPrimitive\s*\(\s*(\w+)\s*\)\s*\]\s*\\?\s*\[\s*OutputPrimitive\s*\(\s*(\w+)\s*\)\s*\]\s*"
    r"\[\s*MaxOutputVertices\s*\(([^)]*)\)\s*\]\s*(\w+)\s+(\w+)\s*\(\s*(?:in\s+)?(\w+)\s+(\w+)\s*,\s*out\s+(\w+)\s+(\w+)\s*((?:,[^()]*)?)\)"
)
GS_INPUTS = {"point": 1, "line": 2, "triangle": 3, "line_adj": 4, "lineadj": 4, "triangle_adj": 6, "triangleadj": 6}
GS_STREAMS = {"point": "PointStream", "line": "LineStream", "triangle": "TriangleStream"}


def _geometry_shader(m: re.Match) -> str:
    prim_in, prim_out, count, returns, name, in_type, in_name, out_type, out_name, rest = m.groups()
    word = {"line_adj": "lineadj", "triangle_adj": "triangleadj"}.get(prim_in, prim_in)
    return (
        "\n"
        * m.group(0).count("\n")  # the attributes' lines stay, so the lines after keep their numbers
        + f"[maxvertexcount({count.strip()})] {returns} {name}({word} {in_type} {in_name}[{GS_INPUTS.get(prim_in, 1)}], "
        f"inout {GS_STREAMS.get(prim_out, 'TriangleStream')}<{out_type}> {out_name}{rest})"
    )


# [nointerpolation] float x: the dialect brackets the interpolation modifiers
_INTERPOLATION_RE = re.compile(r"\[\s*(noperspective|nointerpolation|linear|centroid|sample)\s*\]")


# a string in code: the effect compiler packs its first 16 bytes into a uint4 (defines.fxh, DebugString)
_STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_ATTRIBUTE_OPEN_RE = re.compile(r"\[\s*\w+\s*\([^\]]*$")


def _debug_string(m: re.Match) -> str:
    line_start = m.string.rfind("\n", 0, m.start()) + 1
    before = m.string[line_start : m.start()]
    if before.lstrip().startswith("#") or _ATTRIBUTE_OPEN_RE.search(before):
        return m.group(0)  # a directive's or an attribute's string stays a string
    raw = m.group(1).encode("utf-8", "replace")[:16].ljust(16, b"\0")
    words = [int.from_bytes(raw[i : i + 4], "little") for i in range(0, 16, 4)]
    return "PackDebugString(uint4(" + ", ".join(f"0x{w:08x}u" for w in words) + "))"


# @("#ifndef X"): a directive the effect keeps for the compiler's own preprocessor, which sees a technique's
# PassthroughDefines
_DEFERRED_RE = re.compile(r'@\(\s*"((?:[^"\\]|\\.)*)"\s*\)')


def _deferred(m: re.Match) -> str:
    """The directive in place, on its own line (it stands alone in the sources: no line number moves)."""
    start = m.string.rfind("\n", 0, m.start()) + 1
    alone = not m.string[start : m.start()].strip()
    return ("" if alone else "\n") + m.group(1).replace('\\"', '"') + ("" if alone else "\n")


def _depth(text: str, start: int, end: int) -> int:
    """How much deeper the braces are at ``end`` than at ``start``."""
    return text.count("{", start, end) - text.count("}", start, end)


def _technique(name: str, body: str) -> Technique:
    t = Technique(name)
    m = _PASSTHROUGH_RE.search(body)
    if m:
        t.passthrough = tuple(x.strip() for x in m.group(1).split(",") if x.strip())
    for setter, function, args in _SETTER_RE.findall(body):
        stage, model = STAGES[setter]
        t.calls.append(ShaderCall(stage, model, function, tuple(split_args(args))))
    block = _STATE_BLOCK_RE.search(body)
    t.state = block.group(1) if block else ""
    return t


def split_args(text: str, angles: bool = False) -> list[str]:
    """``text`` split at its top-level commas (``angles``: a parameter list, where ``<...>`` is a template's)."""
    out, depth, cur = [], 0, ""
    nest = {"(": 1, ")": -1, "<": 1, ">": -1} if angles else {"(": 1, ")": -1}
    for c in text:
        if c == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        depth += nest.get(c, 0)
        cur += c
    return [*out, cur.strip()] if cur.strip() or out else []


def shader_calls(effect: Effect) -> list[tuple[Technique, ShaderCall]]:
    """Each technique's shaders, each (passthrough set, shader) once, in file order."""
    seen, out = set(), []
    for t in effect.techniques:
        for c in t.calls:
            key = (t.passthrough, c)
            if key not in seen:
                seen.add(key)
                out.append((t, c))
    return out
