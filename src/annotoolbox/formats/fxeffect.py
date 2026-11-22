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

from .fxmaterial import SMaterialFields, material_header

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


class CEffectError(ValueError):
    pass


def prelude() -> str:
    """The macros that turn the dialect into HLSL (storage words marked, bracketed semantic indices joined) and
    the engine's samplers."""
    lines = [f"#define {word} {MARKER}{word}" for word in TABLE_WORDS]
    lines += [f"#define {semantic}(index) {semantic}##index" for semantic in INDEXED_SEMANTICS]
    lines += [
        f"SamplerState {sampler_name} : register(s{register_index});" for sampler_name, register_index in SAMPLERS
    ]
    lines += [
        f"SamplerComparisonState {sampler_name} : register(s{register_index});"
        for sampler_name, register_index in COMPARISON_SAMPLERS
    ]
    return "\n".join(lines) + "\n" + _PRELUDE_TAIL


def expand_includes(read: Read, path: str, _stack: tuple[str, ...] = ()) -> str:
    """The file at ``path`` with every ``#include "..."`` inlined (relative to the including file), each piece
    under a ``#line`` naming its file, so the compiler's messages name the file and line they come from."""
    data = read(path)
    if data is None:
        raise CEffectError(f"{_stack[-1] if _stack else path}: include {path} not found")
    text = _pastes(_TRAILING_PASTE_RE.sub(r"\1", data.decode("utf-8", "replace").replace("\r\n", "\n")))
    out, last, line = [f'#line 1 "{path}"\n'], 0, 1
    for match in _INCLUDE_RE.finditer(text):
        out.append(text[last : match.start()])
        line += text.count("\n", last, match.start())
        target = _resolve(read, path, match.group(2))
        if read(target) is None:  # an error only where the preprocessor takes the branch (ffx_core's GLSL)
            out.append(f"#error include {match.group(2)} not found")
        elif target not in _stack and target != path:
            out.append(expand_includes(read, target, (*_stack, path)))
        out.append(f'\n#line {line + 1} "{path}"')
        last = match.end()
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
                body, replaced = _PASTE_RE.subn(r"FX_CAT(\1, \2)", body, count=1)
                if not replaced:
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
        for match in _INCLUDE_RE.finditer(text):
            target = _resolve(read, current, match.group(2))
            if target not in found:
                found.add(target)
                todo.append(target)
    return found


def define_lines(defines: list[tuple[str, str]]) -> str:
    """``#define`` lines for (name, value) pairs; an empty value defines the name alone."""
    return "".join(f"#define {name} {value}\n" if value else f"#define {name}\n" for name, value in defines)


def effect_source(read: Read, path: str, defines: list[tuple[str, str]], materials: list[SMaterialFields]) -> str:
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
class SShaderCall:
    """One shader of a pass: its setter's stage, the function and the literal arguments of its uniform parameters."""

    stage: str
    model: str
    function: str
    args: tuple[str, ...] = ()

    @property
    def profile(self) -> str:
        return f"{self.stage}_{self.model}"


@dataclass
class STechnique:
    name: str
    passthrough: tuple[str, ...] = ()  # PassthroughDefines: switches the technique's shaders are compiled with
    calls: list[SShaderCall] = field(default_factory=list)
    state: str = ""  # the first pass's SetStateBlock: a renderstateblock of the effect

    @property
    def functions(self) -> set[str]:
        """The shader functions of all its passes: what they reach makes the technique's tables (fxbind)."""
        return {call.function for call in self.calls}


@dataclass
class SEffect:
    """An effect file after the preprocessor: its HLSL and its techniques."""

    hlsl: str
    techniques: list[STechnique]
    blocks: dict[str, dict[str, str]] = field(default_factory=dict)  # renderstateblock name -> its states (lower case)

    def state_of(self, technique: STechnique) -> dict[str, str]:
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
    raise CEffectError("an unclosed bracket")


def block_end(text: str, start: int) -> int:
    """The index after the ``{...}`` block that opens at or after ``at`` (and a ``;`` right after it)."""
    opening = text.find("{", start)
    if opening < 0:
        raise CEffectError("a block without its opening brace")
    close = matching(text, opening, "{}")
    k = close + 1
    while k < len(text) and text[k].isspace():
        k += 1
    return k + 1 if k < len(text) and text[k] == ";" else close + 1


_TOP_RE = re.compile(r"(?m)^[ \t]*(technique|renderstateblock|MaterialRef)\b[ \t]*(\w*)")


def split_effect(preprocessed: str) -> SEffect:
    """The techniques (with their passes' shaders) and the HLSL left when the effect-only parts are taken out."""
    out, techniques, last = [], [], 0
    blocks: dict[str, dict[str, str]] = {}
    position = depth_at = depth = 0
    while True:
        match = _TOP_RE.search(preprocessed, position)
        if match is None:
            break
        depth += _depth(preprocessed, depth_at, match.start())
        depth_at = position = match.end()
        if depth != 0:  # a word inside a function or struct: not ours
            continue
        out.append(preprocessed[last : match.start()])
        if match.group(1) == "MaterialRef":
            end = preprocessed.find(";", match.end()) + 1
            if not end:
                raise CEffectError("a MaterialRef without its semicolon")
        else:
            end = block_end(preprocessed, match.end())
            if match.group(1) == "technique":
                techniques.append(_technique(match.group(2), preprocessed[match.end() : end]))
            elif match.group(1) == "renderstateblock":
                blocks[match.group(2)] = assignments(preprocessed[match.end() : end])
        out.append("\n" * preprocessed.count("\n", match.start(), end))  # keeps the line numbers
        last = position = depth_at = end
    out.append(preprocessed[last:])
    hlsl = _STRING_RE.sub(_debug_string, _DEFERRED_RE.sub(_deferred, "".join(out)))
    hlsl = _GS_RE.sub(_geometry_shader, _INTERPOLATION_RE.sub(r"\1", hlsl))
    return SEffect(hlsl, techniques, blocks)


# the dialect's geometry shader: its primitives as attributes, one input and one output (the stream) by name
_GS_RE = re.compile(
    r"\[\s*InputPrimitive\s*\(\s*(\w+)\s*\)\s*\]\s*\\?\s*\[\s*OutputPrimitive\s*\(\s*(\w+)\s*\)\s*\]\s*"
    r"\[\s*MaxOutputVertices\s*\(([^)]*)\)\s*\]\s*(\w+)\s+(\w+)\s*\(\s*(?:in\s+)?(\w+)\s+(\w+)\s*,\s*out\s+(\w+)\s+(\w+)\s*((?:,[^()]*)?)\)"
)
GS_INPUTS = {"point": 1, "line": 2, "triangle": 3, "line_adj": 4, "lineadj": 4, "triangle_adj": 6, "triangleadj": 6}
GS_STREAMS = {"point": "PointStream", "line": "LineStream", "triangle": "TriangleStream"}


def _geometry_shader(match: re.Match) -> str:
    prim_in, prim_out, count, returns, name, in_type, in_name, out_type, out_name, rest = match.groups()
    word = {"line_adj": "lineadj", "triangle_adj": "triangleadj"}.get(prim_in, prim_in)
    return (
        "\n"
        * match.group(0).count("\n")  # the attributes' lines stay, so the lines after keep their numbers
        + f"[maxvertexcount({count.strip()})] {returns} {name}({word} {in_type} {in_name}[{GS_INPUTS.get(prim_in, 1)}], "
        f"inout {GS_STREAMS.get(prim_out, 'TriangleStream')}<{out_type}> {out_name}{rest})"
    )


# [nointerpolation] float x: the dialect brackets the interpolation modifiers
_INTERPOLATION_RE = re.compile(r"\[\s*(noperspective|nointerpolation|linear|centroid|sample)\s*\]")


# a string in code: the effect compiler packs its first 16 bytes into a uint4 (defines.fxh, DebugString)
_STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_ATTRIBUTE_OPEN_RE = re.compile(r"\[\s*\w+\s*\([^\]]*$")


def _debug_string(match: re.Match) -> str:
    line_start = match.string.rfind("\n", 0, match.start()) + 1
    before = match.string[line_start : match.start()]
    if before.lstrip().startswith("#") or _ATTRIBUTE_OPEN_RE.search(before):
        return match.group(0)  # a directive's or an attribute's string stays a string
    raw = match.group(1).encode("utf-8", "replace")[:16].ljust(16, b"\0")
    words = [int.from_bytes(raw[i : i + 4], "little") for i in range(0, 16, 4)]
    return "PackDebugString(uint4(" + ", ".join(f"0x{word:08x}u" for word in words) + "))"


# @("#ifndef X"): a directive the effect keeps for the compiler's own preprocessor, which sees a technique's
# PassthroughDefines
_DEFERRED_RE = re.compile(r'@\(\s*"((?:[^"\\]|\\.)*)"\s*\)')


def _deferred(match: re.Match) -> str:
    """The directive in place, on its own line (it stands alone in the sources: no line number moves)."""
    start = match.string.rfind("\n", 0, match.start()) + 1
    alone = not match.string[start : match.start()].strip()
    return ("" if alone else "\n") + match.group(1).replace('\\"', '"') + ("" if alone else "\n")


def _depth(text: str, start: int, end: int) -> int:
    """How much deeper the braces are at ``end`` than at ``start``."""
    return text.count("{", start, end) - text.count("}", start, end)


def _technique(name: str, body: str) -> STechnique:
    technique = STechnique(name)
    match = _PASSTHROUGH_RE.search(body)
    if match:
        technique.passthrough = tuple(x.strip() for x in match.group(1).split(",") if x.strip())
    for setter, function, arguments in _SETTER_RE.findall(body):
        stage, model = STAGES[setter]
        technique.calls.append(SShaderCall(stage, model, function, tuple(split_arguments(arguments))))
    block = _STATE_BLOCK_RE.search(body)
    technique.state = block.group(1) if block else ""
    return technique


def split_arguments(text: str, is_parameter_list: bool = False) -> list[str]:
    """``text`` split at its top-level commas (``is_parameter_list``: a parameter list, where ``<...>`` is a template's)."""
    out, depth, current = [], 0, ""
    nest = {"(": 1, ")": -1, "<": 1, ">": -1} if is_parameter_list else {"(": 1, ")": -1}
    for character in text:
        if character == "," and depth == 0:
            out.append(current.strip())
            current = ""
            continue
        depth += nest.get(character, 0)
        current += character
    return [*out, current.strip()] if current.strip() or out else []


def shader_calls(effect: SEffect) -> list[tuple[STechnique, SShaderCall]]:
    """Each technique's shaders, each (passthrough set, shader) once, in file order."""
    seen, out = set(), []
    for technique in effect.techniques:
        for call in technique.calls:
            key = (technique.passthrough, call)
            if key not in seen:
                seen.add(key)
                out.append((technique, call))
    return out


_LINE_MARK_RE = re.compile(r"(?m)^[ \t]*#[ \t]*line\b[^\n]*$")
_BLOCK_HEADS = ("cbuffer", "tbuffer", "struct", "technique", "renderstateblock", "pass")


@dataclass
class SDeclarations:
    """A preprocessed effect without its function bodies and line marks, whitespace folded: ``base`` the resources,
    constant buffers, structures, function heads and the names of its techniques and state blocks, whose bodies are
    in ``techniques`` and ``blocks`` by name."""

    base: str
    techniques: dict[str, str] = field(default_factory=dict)
    blocks: dict[str, str] = field(default_factory=dict)


def _fold(text: str) -> str:
    return " ".join(text.split())


def declarations(preprocessed: str) -> SDeclarations:
    """What a variant declares (``SDeclarations``). Two variants whose parts are the same differ there only in what
    their shaders' code does (models.shaderprebuild holds the compiled shaders against each other)."""
    text = _LINE_MARK_RE.sub("", preprocessed)
    out, found, last, head, depth = [], SDeclarations(""), 0, 0, 0
    i = 0
    while i < len(text):
        character = text[i]
        if character == "{" and depth == 0:
            words = text[head:i].split()
            named = words[:1] in (["technique"], ["renderstateblock"]) and len(words) > 1
            if named or ("(" in text[head:i] and words[:1] and words[0] not in _BLOCK_HEADS):
                close = matching(text, i, "{}")
                if named:  # a technique's or state block's body: kept by its name
                    table = found.techniques if words[0] == "technique" else found.blocks
                    table[words[1]] = _fold(text[i + 1 : close])
                out.append(text[last:i] + "{}")  # a function's body goes
                last = head = i = close + 1
                continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
        if character in ";}" and depth == 0:
            head = i + 1
        i += 1
    out.append(text[last:])
    found.base = _fold("".join(out))
    return found
