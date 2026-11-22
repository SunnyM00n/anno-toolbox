"""The Direct3D 10 / 11 effect dialect (Anno 2070's and 1404's ``.fx``): the techniques, their passes' shaders and
state blocks read out of an effect after the preprocessor, and the HLSL left for the compiler (docs/rendering/
shader-compile.md section 10).

``split(preprocessed)`` is the entry: ``technique10`` / ``technique11`` blocks with ``SetVertexShader(CompileShader(
vs_5_0, vs_Model(0)))`` or a named ``vertexshader`` object, ``SetRasterizerState`` and friends. The shaders come out
as ``fxeffect.SShaderCall``s (the same record 117's reader gives), which ``fxlink.CProgram`` cuts the entry text for.
The reading is ours, not the game's effect compiler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import fxeffect
from .fxeffect import STAGES, SShaderCall, split_arguments

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
class SPass:
    name: str
    calls: list[SShaderCall] = field(default_factory=list)
    rasterizer: str = ""
    depth: str = ""
    blend: str = ""
    state_block: str = ""  # Direct3D 9's: the engine's state block the pass names (``fx9effect``)
    textures: dict[int, str] = field(default_factory=dict)  # Direct3D 9's fixed-function stages: stage -> texture

    def call(self, stage: str) -> SShaderCall | None:
        return next((shader_call for shader_call in self.calls if shader_call.stage == stage), None)


@dataclass
class STechnique:
    name: str
    format: str = ""  # the annotation ``string format``: the vertex layout the technique is made for
    passes: list[SPass] = field(default_factory=list)


@dataclass
class SEffect:
    """An effect after the preprocessor: the HLSL (techniques and shader objects blanked, line numbers kept) and
    its techniques."""

    hlsl: str
    techniques: list[STechnique]

    def technique(self, name: str) -> STechnique | None:
        return next((candidate for candidate in self.techniques if candidate.name == name), None)


class CFx11Error(ValueError):
    pass


def _call_of(stage: str, text: str, objects: dict[str, SShaderCall]) -> SShaderCall | None:
    """The shader a setter's argument names: ``NULL`` (none), a ``vertexshader`` object, or an inline compile."""
    text = text.strip()
    if text == "NULL" or not text:
        return None
    if text in objects:
        return objects[text]
    match = _COMPILE_RE.match(text)
    if match is None:
        raise CFx11Error(f"a {stage} shader this reader does not know: {text[:60]}")
    profile = match.group(1).split("_")
    model = "_".join(profile[1:]) if len(profile) == 3 else "5_0"
    return SShaderCall(stage, model, match.group(2), tuple(split_arguments(match.group(3))))


def _object(kind: str, text: str) -> tuple[str, SShaderCall] | None:
    """``vertexshader name = CompileShader(vs_5_0, fn(args));`` -> (name, call)."""
    match = re.match(r"\s*(\w+)\s*=\s*(.*?)\s*;\s*$", text, re.S)
    if match is None:
        return None
    call = _call_of(_OBJECT_TYPES[kind], match.group(2), {})
    return (match.group(1), call) if call else None


def _pass(name: str, body: str, objects: dict[str, SShaderCall]) -> SPass:
    render_pass = SPass(name)
    for setter, arg in _SETTER_RE.findall(body):
        stage = STAGES[setter][0]
        call = _call_of(stage, arg, objects)
        if call is not None:
            render_pass.calls.append(call)
    for kind, state in _STATE_RE.findall(body):
        setattr(render_pass, {"Rasterizer": "rasterizer", "DepthStencil": "depth", "Blend": "blend"}[kind], state)
    return render_pass


def _technique(name: str, body: str, objects: dict[str, SShaderCall]) -> STechnique:
    technique = STechnique(name)
    format_match = re.search(r'<\s*string\s+format\s*=\s*"([^"]*)"', body)
    technique.format = format_match.group(1) if format_match else ""
    body_start = body.index("{")
    for match in _PASS_RE.finditer(body, body_start):
        technique.passes.append(
            _pass(match.group(1), body[match.end() : fxeffect.block_end(body, match.end() - 1)], objects)
        )
    return technique


def split(preprocessed: str, technique=_technique) -> SEffect:
    """The techniques of an effect and the HLSL left (see the module docstring); ``technique(name, body, objects)``
    reads one technique's block (``fx9effect`` passes Direct3D 9's)."""
    out, objects, pending, last, scan_position = [], {}, [], 0, 0
    depth_at = depth = 0
    while True:
        match = _TOP_RE.search(preprocessed, scan_position)
        if match is None:
            break
        depth += preprocessed.count("{", depth_at, match.start()) - preprocessed.count("}", depth_at, match.start())
        depth_at = scan_position = match.end()
        if depth != 0:  # a word inside a function or struct
            continue
        kind = match.group(1)
        out.append(preprocessed[last : match.start()])
        if kind.startswith("technique"):
            name_match = _NAME_RE.match(preprocessed, match.end())
            if name_match is None:
                raise CFx11Error("a technique without a name")
            end = fxeffect.block_end(preprocessed, name_match.end())
            pending.append((name_match.group(1), preprocessed[name_match.end() : end]))
        else:
            end = preprocessed.index(";", match.end()) + 1
            found = _object(kind, preprocessed[match.end() : end])
            if found is not None:
                objects[found[0]] = found[1]
        out.append("\n" * preprocessed.count("\n", match.start(), end))
        last = scan_position = depth_at = end
    out.append(preprocessed[last:])
    hlsl = _SHARED_RE.sub("", "".join(out))  # the effect pool's storage word: a plain compile has no pool
    return SEffect(
        lines_outside_parentheses(hlsl),
        [technique(technique_name, technique_body, objects) for technique_name, technique_body in pending],
    )


_LINE_RE = re.compile(r"[ \t]*#line[^\n]*\n?")


def lines_outside_parentheses(hlsl: str) -> str:
    """The text without the ``#line`` directives that stand inside parentheses (the preprocessor leaves one where a
    conditional took lines out of a parameter list; ``fxlink`` rejoins the parameters on one line)."""
    out, depth, last = [], 0, 0
    for match in _LINE_RE.finditer(hlsl):
        depth += hlsl.count("(", last, match.start()) - hlsl.count(")", last, match.start())
        out.append(hlsl[last : match.start()])
        if depth <= 0:
            out.append(match.group(0))
        last = match.end()
    out.append(hlsl[last:])
    return "".join(out)
