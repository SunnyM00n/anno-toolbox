"""Anno 1701's model shaders compiled for the viewer: a material's Direct3D 9 effect (``m_Shader``) read from the
install, preprocessed with the engine's defines and compiled by ``d3dcompiler_43`` in backwards-compatibility mode as
shader model 4, one technique's pass at a time, kept in the build cache.

``CSource1701(files, compiler)`` is the entry: ``model_pass(values, technique)`` (``values``: the material's pairs,
``othergames.grannymodels.material_values``). Each pass's pixel shader is our entry ``PS1701`` (``pixel_wrapper``):
the game's pixel shader, or a stand-in for Direct3D 9's fixed-function stage, and what that stage did after it by the
pass's state block. docs/rendering/shader-compile.md section 13.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..formats import fx9effect, fxlink
from ..formats.fxeffect import SShaderCall, split_arguments
from ..infra import fxc
from . import fxstage, shader2070
from .fxstage import SBuilt, SSampler, SState
from .shader2070 import CNoPass

NAMESPACE = "shaders1701"
FX_DIR = "data/system/fxshaders/"
STATE_BLOCKS = "data/system/stateblocks/default.stb"
ENTRY = "PS1701"
# the engine's defines (shader-compile.md 13): every shader version up to a 3.0 device's, its constants, normal
# mapping, DDS textures, the shadow of the R32F map (soft, self-shadowing); not the hardware shadow map's (its
# comparison through tex2Dproj needs Direct3D 9's depth textures, shader model 4 reads the depth as a value)
GLOBALS_1701 = (
    *((f"{kind}SHADER{v}", "1") for kind in ("VERTEX", "PIXEL") for k in range(4) for v in (k, f"{k}0")),
    ("MAXVERTEXSHADERCONST", "256"), ("NORMALMAPPINGENABLED", "1"), ("SHADOWINGENABLED", "1"),
    ("SELFSHADOWINGENABLED", "1"), ("SOFTSHADOWSENABLED", "1"), ("USEDDSTEXTURES", "1"),
)  # fmt: skip
# the effects of mesh/ that are not drawn as models: the falls, the main menu's sea, the water holes, the GUI's
# portraits (a model's water sheet, ``water.fx``, is: shader-compile.md 13 "Water")
NOT_MODELS = ("waterfall.fx", "watermainmenu.fx", "water_cutout.fx", "portrait.fx")
WATER = "water.fx"
# the effects that write into a float target, which keeps what the pixel shader writes unclamped (the sea's
# displacement pass: world positions; shader-compile.md 13 "The sea")
FLOAT_TARGETS = ("various/waterdisp.fx",)
# the shader's samplers -> the material's texture slot (``Texture.<slot>`` of its values)
MAPS = {"samplerTexDiffuseColor": "diffuse", "samplerTexBump": "bump", "samplerTexSpecularColor": "specular",
        "samplerTexOpacity": "opacity", "samplerCubeTexReflection": "reflection"}  # fmt: skip
FOG_COLOR = "cFixedFogColor"  # ours: the fog colour of Direct3D 9's render state, which no effect declares

_CULL = {"CCW": "back", "CW": "front", "NONE": "none"}
_DEPTH = {"LESSEQUAL": "less_equal", "LESS": "less", "EQUAL": "equal", "GREATER": "greater",
          "GREATEREQUAL": "greater_equal", "ALWAYS": "always"}  # fmt: skip
_BLEND = {"ZERO": "zero", "ONE": "one", "SRCALPHA": "src_alpha", "INVSRCALPHA": "inv_src_alpha", "SRCCOLOR": "src_color",
          "INVSRCCOLOR": "inv_src_color", "DESTALPHA": "dst_alpha", "INVDESTALPHA": "inv_dst_alpha",
          "DESTCOLOR": "dst_color", "INVDESTCOLOR": "inv_dst_color"}  # fmt: skip
_ADDRESS = {"WRAP": "WRAP", "CLAMP": "CLAMP", "MIRROR": "MIRROR", "BORDER": "BORDER"}


def model_effect(values: dict) -> str:
    """The effect file a material names (``m_Shader``); ``CNoPass`` for one that is not a model's."""
    fx = str(values.get("Shader", "")).replace(chr(92), "/").lower()
    if not fx.startswith(FX_DIR + "mesh/") or fx.rsplit("/", 1)[-1] in NOT_MODELS:
        raise CNoPass(f"{fx or 'no shader'} is not one of the model effects this reader draws")
    return fx


def _on(states: dict, key: str) -> bool:
    return states.get(key, "FALSE") == "TRUE"


def pass_state(states: dict[str, str]) -> SState:
    """The render states of a pass's state block (``fx9effect.block_states``) as a ``SState``: Direct3D 9's culling
    of counter-clockwise faces is Direct3D 11's of back faces."""
    return SState(
        cull=_CULL.get(states.get("CULLMODE", "CCW"), "back"),
        depth_test=_on(states, "ZENABLE"),
        depth_write=_on(states, "ZWRITEENABLE"),
        depth_func=_DEPTH.get(states.get("ZFUNC", "LESSEQUAL"), "less_equal"),
        blend=_on(states, "ALPHABLENDENABLE"),
        source=_BLEND.get(states.get("SRCBLEND", "ONE"), "one"),
        dest=_BLEND.get(states.get("DESTBLEND", "ZERO"), "zero"),
    )


def sampler_state(sampler: fx9effect.SSampler9, states: dict[str, str]) -> SSampler:
    """A legacy sampler's state: the block's for its stage (its register, else every stage's), then its own. An
    engine word the file leaves (``TSM_FILTER``, the shadow map's) reads as point, as its comment says."""
    stage = sampler.register if sampler.register is not None else -1

    def get(key: str, default: str) -> str:
        return sampler.states.get(key) or fx9effect.stage_state(states, key, stage, default)

    minify = get("MINFILTER", "LINEAR")
    filter_ = {"ANISOTROPIC": "ANISOTROPIC", "LINEAR": "MIN_MAG_MIP_LINEAR"}.get(minify, "MIN_MAG_MIP_POINT")
    address_u, address_v, address_w = (_ADDRESS.get(get(f"ADDRESS{axis}", "WRAP"), "WRAP") for axis in "UVW")
    return SSampler(filter_, address_u, address_v, address_w)


@dataclass(frozen=True)
class SParam:
    """One parameter of a shader function: ``in`` / ``out`` / ``inout`` / ``uniform``, type, name and the semantic
    with its index (``COLOR0``, ``TEXCOORD2``; "" for none)."""

    direction: str
    type: str
    name: str
    semantic: str


_PARAM_RE = re.compile(r"^\s*(in|out|inout|uniform)?\s*(\w+)\s+(\w+)\s*(?::\s*(\w+))?\s*$")


def _semantic(word: str) -> str:
    match = re.fullmatch(r"([A-Za-z_]+?)(\d*)", word or "")
    return f"{match.group(1).upper()}{match.group(2) or 0}" if match and word else ""


def params(program: fxlink.CProgram, function: str) -> list[SParam]:
    """The parameters of a shader function as the effect defines it (its first definition)."""
    heads = [program.m_items[i].head for i in program.m_by_name.get(function, ()) if program.m_items[i].head]
    if not heads:
        raise CNoPass(f"no shader function {function}")
    head = heads[0]
    out = []
    for parameter in split_arguments(head[head.index("(") + 1 : head.rindex(")")]):
        match = _PARAM_RE.match(parameter)
        if match:
            out.append(SParam(match.group(1) or "in", match.group(2), match.group(3), _semantic(match.group(4) or "")))
    return out


@dataclass(frozen=True)
class SPixel(SShaderCall):
    """The pass's pixel stage as ours compiles it: the wrapper text, the game's functions it calls (``roots``) and
    the samplers with the pass's states."""

    wrapper: str = ""
    roots: tuple[str, ...] = ()
    samplers: tuple = ()


def _stand_in(states: dict, mirror: dict[str, str], sampler: str) -> list[str]:
    """Direct3D 9's texture stage 0 where a pass sets no pixel shader: its colour and alpha operations on the
    stage's texture and the vertex colour, then the specular colour added when the state says so."""
    texcoord = mirror.get(f"TEXCOORD{fx9effect.stage_state(states, 'TEXCOORDINDEX', 0, '0')}")
    diffuse = f"saturate({mirror['COLOR0']})" if "COLOR0" in mirror else "float4(1, 1, 1, 1)"
    texture = f"tex2D({sampler}, ({texcoord}).xy)" if sampler and texcoord else "float4(1, 1, 1, 1)"
    arguments = {"TEXTURE": texture, "DIFFUSE": diffuse, "CURRENT": diffuse}

    def stage(kind: str) -> str:
        op = fx9effect.stage_state(states, f"{kind}OP", 0, "DISABLE")
        first, second = (
            arguments.get(fx9effect.stage_state(states, f"{kind}ARG{k}", 0, ""), "float4(1, 1, 1, 1)") for k in (1, 2)
        )
        return {"MODULATE": f"{first} * {second}", "MODULATE2X": f"2 * {first} * {second}", "MODULATE4X": f"4 * {first} * {second}",
                "SELECTARG1": first, "SELECTARG2": second, "ADD": f"{first} + {second}"}.get(op, diffuse)  # fmt: skip

    lines = [f"c.rgb = ({stage('COLOR')}).rgb;", f"c.a = ({stage('ALPHA')}).a;"]
    if _on(states, "SPECULARENABLE") and "COLOR1" in mirror:
        lines.append(f"c.rgb += saturate({mirror['COLOR1']}).rgb;")
    return lines


def _inner_call(pixel_call: SShaderCall, inner: list[SParam], mirror: dict[str, str]) -> str:
    """The game's pixel shader called with the vertex outputs of its semantics (colours clamped, as Direct3D 9's
    colour registers are), the colour and depth it writes into ``c`` and ``d``, the pass's literal arguments."""
    literal, arguments = iter(pixel_call.args), []
    for parameter in inner:
        if parameter.direction == "uniform":
            arguments.append(next(literal, "0"))
        elif parameter.direction == "out":
            arguments.append("d" if parameter.semantic.startswith("DEPTH") else "c")
        elif parameter.semantic in mirror:
            value = mirror[parameter.semantic]
            arguments.append(f"saturate({value})" if parameter.semantic.startswith("COLOR") else value)
        else:
            arguments.append(f"({parameter.type})0")
    return f"{pixel_call.function}({', '.join(arguments)});"


def pixel_wrapper(outputs: list[SParam], pixel_call: SShaderCall | None, inner: list[SParam], states: dict, sampler: str,
                  clamp: bool = True) -> str:  # fmt: skip
    """``PS1701``: the vertex shader's ``outputs`` as its inputs (a signature that links), the game's pixel shader
    ``ps`` (its parameters ``inner``) or the fixed-function stand-in (``sampler``: stage 0's), then Direct3D 9's
    alpha test and fog of the pass's ``states``, the colour clamped as an 8-bit target holds it (``clamp`` False:
    a float target's, as written)."""
    decls, mirror = [], {}
    for k, parameter in enumerate(output for output in outputs if output.semantic):
        semantic = "SV_Position" if parameter.semantic == "POSITION0" else parameter.semantic
        decls.append(f"in {parameter.type} v{k} : {semantic}")
        mirror[parameter.semantic] = f"v{k}"
    depth = any(parameter.direction == "out" and parameter.semantic.startswith("DEPTH") for parameter in inner)
    decls.append("out float4 oTarget : SV_Target")
    decls += ["out float oDepth1701 : SV_Depth"] if depth else []
    body = ["float4 c = float4(1, 1, 1, 1);", "float d = 0;"]
    body += [_inner_call(pixel_call, inner, mirror)] if pixel_call is not None else _stand_in(states, mirror, sampler)
    if _on(states, "ALPHATESTENABLE") and states.get("ALPHAFUNC", "GREATER") in ("GREATER", "GREATEREQUAL"):
        alpha_ref = int(states.get("ALPHAREF", "0"), 0) / 255.0
        body.append(
            f"if (c.a {'<=' if states.get('ALPHAFUNC', 'GREATER') == 'GREATER' else '<'} {alpha_ref:.6f}) discard;"
        )
    if _on(states, "FOGENABLE") and "FOG0" in mirror:
        body.append(f"c.rgb = lerp({FOG_COLOR}.rgb, c.rgb, saturate({mirror['FOG0']}));")
    body.append("oTarget = saturate(c);" if clamp else "oTarget = c;")
    body += ["oDepth1701 = d;"] if depth else []
    head = f"float4 {FOG_COLOR};\n" + (f"sampler {sampler};\n" if pixel_call is None and sampler else "")
    lines = "\n\t".join(body)
    return f"{head}void {ENTRY}({', '.join(decls)})\n{{\n\t{lines}\n}}\n"


def _uses(hlsl: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", hlsl) is not None


GAME = fxstage.SFxcGame(NAMESPACE, fxc.BACKWARDS_COMPATIBILITY, lambda call: f"{call.stage}_4_0", _uses, True)


class CSource1701(shader2070.CSource2070):
    """The install's model effects and state blocks, compiled one pass at a time; thread-safe."""

    def __init__(self, files, compiler) -> None:
        super().__init__(files, compiler, GLOBALS_1701, GAME)
        text = files.read(STATE_BLOCKS).decode("latin-1") if files.exists(STATE_BLOCKS) else ""
        self.m_blocks = fx9effect.state_blocks(text)

    def _preprocess(self, fx: str, defines: tuple) -> SBuilt:
        found = fx9effect.split(text := self.preprocessed(fx, defines))
        return SBuilt(found, text, fxlink.CProgram(found.hlsl), fx9effect.samplers(found.hlsl))

    def _pick(self, built: SBuilt, technique: str, fx: str) -> fxstage.Picked:
        tech = built.effect.technique(technique)
        if tech is None or not tech.passes or tech.passes[0].call("vs") is None:
            raise CNoPass(f"{fx} has no technique {technique} with a vertex shader")
        first_pass = tech.passes[0]
        vertex_call, pixel_call = first_pass.call("vs"), first_pass.call("ps")
        states = fx9effect.block_states(self.m_blocks, first_pass.state_block)
        outputs = [
            parameter for parameter in params(built.program, vertex_call.function) if parameter.direction == "out"
        ]
        inner = params(built.program, pixel_call.function) if pixel_call is not None else []
        texture = first_pass.textures.get(0, "")
        stage0 = next(
            (name for name, sampler in built.samplers.items() if sampler.texture == texture and "Cube" not in name), ""
        )
        samplers = tuple((name, sampler_state(sampler, states)) for name, sampler in built.samplers.items())
        clamp = not fx.endswith(FLOAT_TARGETS)
        wrapper = pixel_wrapper(outputs, pixel_call, inner, states, "" if pixel_call is not None else stage0, clamp)
        pixel = SPixel(
            "ps", "4_0", ENTRY, (), wrapper, (pixel_call.function,) if pixel_call is not None else (), samplers
        )
        return vertex_call, pixel, pass_state(states)

    def _entry(self, built: SBuilt, call: SShaderCall, fx: str) -> fxstage.SEntry:
        if not isinstance(call, SPixel):
            return self.prepare(built, call)
        text = (built.program.source(set(call.roots)) if call.roots else "") + '\n#line 1 "<entry>"\n' + call.wrapper
        entry = self.prepare(built, call, text=(text, ENTRY), samplers=dict(call.samplers))
        # a legacy sampler declares no texture type: a cube one's stand-in must be a cube
        cubes = {name: "TextureCube" for name in entry.samplers if "Cube" in name}
        return replace(entry, types={**entry.types, **cubes})

    def model_pass(self, values: dict, technique: str = "Object") -> shader2070.SPassStages:
        """The pass of a model material's effect (``values``: the material's pairs as a dict)."""
        return self.effect_pass(model_effect(values), (), technique)
