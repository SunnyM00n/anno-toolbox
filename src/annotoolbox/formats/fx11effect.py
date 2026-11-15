"""The Direct3D 10 / 11 effect dialect (Anno 2070's and 1404's ``.fx``): the techniques, their passes' shaders and
state blocks read out of an effect after the preprocessor, and the HLSL left for the compiler (docs/rendering/
shader-compile.md section 10).

``split(preprocessed)`` is the entry: ``technique10`` / ``technique11`` blocks with ``SetVertexShader(CompileShader(
vs_5_0, vs_Model(0)))`` or a named ``vertexshader`` object, ``SetRasterizerState`` and friends. The shaders come out
as ``fxeffect.ShaderCall``s (the same record 117's reader gives), which ``fxlink.Program`` cuts the entry text for.
The reading is ours, not the game's effect compiler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import fxeffect
from .fxeffect import STAGES, ShaderCall, split_args

# the setter of a stage -> the profile prefix the effect names (SetHullShader(CompileShader(hs_5_0, ...)))
_OBJECT_TYPES = {"vertexshader": "vs", "pixelshader": "ps", "geometryshader": "gs", "hullshader": "hs",
                 "domainshader": "ds", "computeshader": "cs"}  # fmt: skip
_TOP_RE = re.compile(
    r"(?m)^[ \t]*(technique(?:10|11)?|vertexshader|pixelshader|geometryshader|hullshader|domainshader)\b"
)
_NAME_RE = re.compile(r"\s*(\w+)")
_SHARED_RE = re.compile(r"\bshared\s+")
_COMPILE_RE = re.compile(r"CompileShader\s*\(\s*(\w+)\s*,\s*(\w+)\s*\((.*)\)\s*\)\s*$", re.S)
_SETTER_RE = re.compile(
    r"\b(Set(?:Vertex|Pixel|Geometry|Hull|Domain)Shader)\s*\(((?:[^()]|\((?:[^()]|\([^()]*\))*\))*)\)\s*;"
)
_STATE_RE = re.compile(r"\bSet(Rasterizer|DepthStencil|Blend)State\s*\(\s*(\w+)")
_PASS_RE = re.compile(r"\bpass\s+(\w+)\s*\{")


@dataclass
class Pass:
    name: str
    calls: list[ShaderCall] = field(default_factory=list)
    rasterizer: str = ""
    depth: str = ""
    blend: str = ""
    state_block: str = ""  # Direct3D 9's: the engine's state block the pass names (``fx9effect``)
    textures: dict[int, str] = field(default_factory=dict)  # Direct3D 9's fixed-function stages: stage -> texture

    def call(self, stage: str) -> ShaderCall | None:
        return next((c for c in self.calls if c.stage == stage), None)


@dataclass
class Technique:
    name: str
    format: str = ""  # the annotation ``string format``: the vertex layout the technique is made for
    passes: list[Pass] = field(default_factory=list)


@dataclass
class Effect:
    """An effect after the preprocessor: the HLSL (techniques and shader objects blanked, line numbers kept) and
    its techniques."""

    hlsl: str
    techniques: list[Technique]

    def technique(self, name: str) -> Technique | None:
        return next((t for t in self.techniques if t.name == name), None)


class Fx11Error(ValueError):
    pass


def _call_of(stage: str, text: str, objects: dict[str, ShaderCall]) -> ShaderCall | None:
    """The shader a setter's argument names: ``NULL`` (none), a ``vertexshader`` object, or an inline compile."""
    text = text.strip()
    if text == "NULL" or not text:
        return None
    if text in objects:
        return objects[text]
    m = _COMPILE_RE.match(text)
    if m is None:
        raise Fx11Error(f"a {stage} shader this reader does not know: {text[:60]}")
    profile = m.group(1).split("_")
    model = "_".join(profile[1:]) if len(profile) == 3 else "5_0"
    return ShaderCall(stage, model, m.group(2), tuple(split_args(m.group(3))))


def _object(kind: str, text: str) -> tuple[str, ShaderCall] | None:
    """``vertexshader name = CompileShader(vs_5_0, fn(args));`` -> (name, call)."""
    m = re.match(r"\s*(\w+)\s*=\s*(.*?)\s*;\s*$", text, re.S)
    if m is None:
        return None
    call = _call_of(_OBJECT_TYPES[kind], m.group(2), {})
    return (m.group(1), call) if call else None


def _pass(name: str, body: str, objects: dict[str, ShaderCall]) -> Pass:
    p = Pass(name)
    for setter, arg in _SETTER_RE.findall(body):
        stage = STAGES[setter][0]
        call = _call_of(stage, arg, objects)
        if call is not None:
            p.calls.append(call)
    for kind, state in _STATE_RE.findall(body):
        setattr(p, {"Rasterizer": "rasterizer", "DepthStencil": "depth", "Blend": "blend"}[kind], state)
    return p


def _technique(name: str, body: str, objects: dict[str, ShaderCall]) -> Technique:
    t = Technique(name)
    fmt = re.search(r'<\s*string\s+format\s*=\s*"([^"]*)"', body)
    t.format = fmt.group(1) if fmt else ""
    pos = body.index("{")
    for m in _PASS_RE.finditer(body, pos):
        t.passes.append(_pass(m.group(1), body[m.end() : fxeffect.block_end(body, m.end() - 1)], objects))
    return t


def split(preprocessed: str, technique=_technique) -> Effect:
    """The techniques of an effect and the HLSL left (see the module docstring); ``technique(name, body, objects)``
    reads one technique's block (``fx9effect`` passes Direct3D 9's)."""
    out, objects, pending, last, pos = [], {}, [], 0, 0
    depth_at = depth = 0
    while True:
        m = _TOP_RE.search(preprocessed, pos)
        if m is None:
            break
        depth += preprocessed.count("{", depth_at, m.start()) - preprocessed.count("}", depth_at, m.start())
        depth_at = pos = m.end()
        if depth != 0:  # a word inside a function or struct
            continue
        kind = m.group(1)
        out.append(preprocessed[last : m.start()])
        if kind.startswith("technique"):
            n = _NAME_RE.match(preprocessed, m.end())
            end = fxeffect.block_end(preprocessed, n.end())
            pending.append((n.group(1), preprocessed[n.end() : end]))
        else:
            end = preprocessed.index(";", m.end()) + 1
            found = _object(kind, preprocessed[m.end() : end])
            if found is not None:
                objects[found[0]] = found[1]
        out.append("\n" * preprocessed.count("\n", m.start(), end))
        last = pos = depth_at = end
    out.append(preprocessed[last:])
    hlsl = _SHARED_RE.sub("", "".join(out))  # the effect pool's storage word: a plain compile has no pool
    return Effect(lines_outside_parentheses(hlsl), [technique(n, b, objects) for n, b in pending])


_LINE_RE = re.compile(r"[ \t]*#line[^\n]*\n?")


def lines_outside_parentheses(hlsl: str) -> str:
    """The text without the ``#line`` directives that stand inside parentheses (the preprocessor leaves one where a
    conditional took lines out of a parameter list; ``fxlink`` rejoins the parameters on one line)."""
    out, depth, last = [], 0, 0
    for m in _LINE_RE.finditer(hlsl):
        depth += hlsl.count("(", last, m.start()) - hlsl.count(")", last, m.start())
        out.append(hlsl[last : m.start()])
        if depth <= 0:
            out.append(m.group(0))
        last = m.end()
    out.append(hlsl[last:])
    return "".join(out)
