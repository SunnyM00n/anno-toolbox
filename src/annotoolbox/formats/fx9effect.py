"""The Direct3D 9 effect dialect (Anno 1701's ``.fx``, ``fx_2_0``) and the engine's state-block file it names: the
techniques and passes read out of an effect after the preprocessor, the legacy samplers' states, the blocks of
``data/system/stateblocks/default.stb`` (docs/rendering/shader-compile.md section 13).

``split(preprocessed)`` is the entry (``fx11effect.SEffect`` records: a pass's ``VertexShader = compile vs_1_1
VS_Object(0);``, ``PixelShader = NULL;``, ``Texture[0] = <pcTexDiffuseColor>;`` and its ``StateBlock``
annotation); ``samplers`` reads ``sampler s = sampler_state { ... };``, ``state_blocks`` / ``block_states`` the
state-block file. The reading is ours, not the game's effect compiler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import fx11effect, fxeffect
from .fxeffect import SShaderCall, split_arguments

_PASS_RE = re.compile(r"\bpass\s+(\w+)\s*(<[^>]*>)?\s*\{")
_SHADER_RE = re.compile(r"\b(Vertex|Pixel)Shader\s*=\s*(?:compile\s+(\w+)\s+(\w+)\s*\(([^;]*)\)|NULL)\s*;", re.I)
_TEXTURE_RE = re.compile(r"\bTexture\s*\[\s*(\d+)\s*\]\s*=\s*<\s*(\w+)\s*>", re.I)
_ANNOTATION_RE = r'<[^>]*\bstring\s+{0}\s*=\s*"([^"]*)"'


def _pass(name: str, annotation: str, body: str) -> fx11effect.SPass:
    shader_pass = fx11effect.SPass(name)
    block = re.search(_ANNOTATION_RE.format("StateBlock"), annotation or "")
    shader_pass.state_block = block.group(1) if block else ""
    for stage, profile, function, arguments in _SHADER_RE.findall(body):
        if function:
            model = profile.split("_", 1)[1] if "_" in profile else profile
            shader_pass.calls.append(
                SShaderCall(stage[0].lower() + "s", model, function, tuple(split_arguments(arguments)))
            )
    shader_pass.textures = {int(k): value for k, value in _TEXTURE_RE.findall(body)}
    return shader_pass


def _technique(name: str, body: str, _objects: dict) -> fx11effect.STechnique:
    """One ``technique Name <string VertexDecl = "...";> { pass p0 <string StateBlock = "...";> { ... } }``."""
    technique = fx11effect.STechnique(name)
    head = body[: body.index("{")]
    decl = re.search(_ANNOTATION_RE.format("VertexDecl"), head)
    technique.format = decl.group(1) if decl else ""
    for match in _PASS_RE.finditer(body, len(head)):
        technique.passes.append(
            _pass(match.group(1), match.group(2), body[match.end() : fxeffect.block_end(body, match.end() - 1)])
        )
    return technique


def split(preprocessed: str) -> fx11effect.SEffect:
    """The techniques of a Direct3D 9 effect and the HLSL left (techniques blanked, line numbers kept)."""
    return fx11effect.split(preprocessed, _technique)


@dataclass(frozen=True)
class SSampler9:
    """A legacy sampler's declaration: the texture its state names, the register the source gives it (None: the
    compiler's), its own states (upper-case key -> upper-case value)."""

    texture: str
    register: int | None = None
    states: dict = field(default_factory=dict)


_SAMPLER_RE = re.compile(
    r"\bsampler(?:2D|3D|CUBE)?\s+(\w+)\s*(?::\s*register\s*\(\s*s(\d+)\s*\))?\s*=\s*sampler_state\s*\{([^}]*)\}\s*;",
    re.I,
)
_STATE_RE = re.compile(r"(\w+)\s*=\s*<?\s*(\w+)\s*>?\s*;")


def samplers(hlsl: str) -> dict[str, SSampler9]:
    """Each ``sampler name [: register(sN)] = sampler_state { texture = <t>; minfilter = linear; };`` by name."""
    out = {}
    for match in _SAMPLER_RE.finditer(hlsl):
        states = {k.upper(): value.upper() for k, value in _STATE_RE.findall(match.group(3))}
        texture = next((value for k, value in _STATE_RE.findall(match.group(3)) if k.lower() == "texture"), "")
        states.pop("TEXTURE", None)
        out[match.group(1)] = SSampler9(texture, int(match.group(2)) if match.group(2) else None, states)
    return out


# The state-block file

DEFAULT_BLOCK = "Default"  # the block the engine's states start from: a named block changes only what it lists
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_BLOCK_RE = re.compile(r"(?m)^[ \t]*(\w+)[ \t]*\r?\n\s*\{([^{}]*)\}")
_LINE_RE = re.compile(r"^\s*(?:#include\s+\"(\w+)\"|([\w\[\]]+)\s*=\s*(\S+))\s*$")


def state_blocks(text: str) -> dict[str, list[tuple[str, str]]]:
    """A state-block file's blocks by name, each its lines in order: (key, value) upper-cased, an ``#include
    "Other"`` as ("#include", "Other")."""
    out: dict[str, list[tuple[str, str]]] = {}
    for match in _BLOCK_RE.finditer(_COMMENT_RE.sub("", text)):
        lines = []
        for line in match.group(2).splitlines():
            found = _LINE_RE.match(line)
            if found and found.group(1):
                lines.append(("#include", found.group(1)))
            elif found:
                lines.append((found.group(2).upper(), found.group(3).upper()))
        out.setdefault(match.group(1), lines)
    return out


def block_states(blocks: dict[str, list[tuple[str, str]]], name: str) -> dict[str, str]:
    """The states a pass naming block ``name`` draws with: the default block's, then the named block's (its
    includes where they stand). An unknown name gives the default block's."""

    def apply(into: dict, block: str, depth: int) -> None:
        for key, value in blocks.get(block, ()) if depth < 8 else ():
            if key == "#include":
                apply(into, value, depth + 1)
            else:
                into[key] = value

    out: dict[str, str] = {}
    apply(out, DEFAULT_BLOCK, 0)
    if name != DEFAULT_BLOCK:
        apply(out, name, 0)
    return out


def stage_state(states: dict[str, str], key: str, stage: int, default: str = "") -> str:
    """A per-stage state (``COLOROP[0]``, ``MINFILTER[3]``): the stage's own value, else the block's ``[N]`` one
    (every stage), else ``default``."""
    return states.get(f"{key}[{stage}]", states.get(f"{key}[N]", default))
