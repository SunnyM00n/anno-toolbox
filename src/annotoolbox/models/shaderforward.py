"""Anno 2205's and Anno 1800's model shaders compiled for the viewer: a material's effect file read from the install,
the engine's own text put in front of it (semantic macros, the sampler declarations it writes), compiled by the
compiler the game ships (FXC, shader model 5.0), one technique's pass at a time, kept in the build cache.

``CForwardSource(files, compiler, game)`` is the entry: ``model_pass(values, technique)`` compiles the pass of a model
material (``values``: the config's material fields as a dict). The graphics defines, the material options and what the
engine writes into a shader's text: docs/rendering/shader-compile.md section 11.
"""

from __future__ import annotations

import re

from ..formats import fx11effect, fxeffect, fxlink
from ..infra.fxc import GAME_FLAGS, CFxc
from ..render import rawbuffers
from ..sources.assetdb import number
from . import fxstage
from .fxstage import SBuilt, SStage, SState

NAMESPACE = "shaders-forward"
FX_DIR = "data/shaders/fx/"
INCLUDE_DIR = "data/shaders/include/"
# what the engine writes before every effect's text: the semantic macros of Direct3D 11, and the sampling macros of
# the depth maps (a "comparison texture" is a plain texture with a comparison sampler)
SEMANTICS = (
    ("S_POSITION(n)", "SV_Position"), ("S_TARGET(n)", "SV_Target##n"), ("S_TEXCOORD(n)", "TEXCOORD##n"),
    ("S_COLOR(n)", "COLOR##n"), ("S_NORMAL(n)", "NORMAL##n"), ("S_TANGENT(n)", "TANGENT##n"),
    ("S_BINORMAL(n)", "BINORMAL##n"), ("S_BLENDINDICES(n)", "BLENDINDICES##n"), ("S_BLENDWEIGHT(n)", "BLENDWEIGHT##n"),
    ("S_DEPTH(n)", "SV_Depth"), ("S_DEPTH_GREATER_EQUAL(n)", "SV_DepthGreaterEqual"),
    ("S_DEPTH_LESS_EQUAL(n)", "SV_DepthLessEqual"), ("S_COVERAGE(n)", "SV_Coverage"), ("S_FACE(n)", "SV_IsFrontFace"),
    ("S_INSTANCE", "SV_InstanceID"), ("S_VERTEX_ID", "SV_VertexID"),
    ("S_RENDER_TARGET_ARRAY_INDEX", "SV_RenderTargetArrayIndex"), ("shared", ""), ("Texture2DCmp", "Texture2D"), ("global_table", ""), ("shared_context", ""),
    ("root_descriptor12", ""), ("root_constant12", ""), ("often_changed", ""),
    ("SAMPLEDEPTH2D(t, uv)", "t.SampleLevel(t##Sampler, uv, 0)"),
    ("SAMPLETEXTURE2D_LOD_SMP(t, s, uv, l)", "t.SampleLevel(s, uv, l)"),
    ("SAMPLEDEPTH2D_SMP(t, s, uv)", "t.SampleLevel(s, uv, 0)"), ("FX_CAT_(a, b)", "a##b"), ("FX_CAT(a, b)", "FX_CAT_(a, b)"),
)  # fmt: skip
PROFILES = (
    ("ds_max", "ds_5_0"),
    ("gs_max", "gs_5_0"),
    ("hs_max", "hs_5_0"),
    ("ps_max", "ps_5_0"),
    ("vs_max", "vs_5_0"),
)
# a graphics set of the shipped cache (a key of cache.xml): one sample count, the smallest shadow map
GRAPHICS = {
    "anno2205": (
        ("AA_SAMPLES", "1"), ("DS_VERSION", "50"), ("DX_VERSION", "110"), ("FX_VERSION", "50"), ("GS_VERSION", "50"),
        ("HS_VERSION", "50"), ("PS_VERSION", "50"), ("RD_FX_VERSION", "0_6"),
        ("HARDWARESHADOW_ENABLED", ""), ("SF_POISSON", ""), ("SHADER_AA_SAMPLES", "1"), ("SHADOWMAP_HEIGHT", "2048"),
        ("SHADOWMAP_WIDTH", "2048"), ("SHADOW_ENABLED", ""), ("SOFTSHADOWS_ENABLED", ""), ("VS_VERSION", "50"),
        ("_DIRECTX11", ""), *PROFILES,
    ),
    "anno1800": (
        ("DX_VERSION", "110"), ("DS_VERSION", "50"), ("GS_VERSION", "50"), ("HS_VERSION", "50"), ("PS_VERSION", "50"),
        ("VS_VERSION", "50"), ("FX_VERSION", "50"), ("DIRECTX11", ""), ("AA_SAMPLES", "1"), ("SHADER_AA_SAMPLES", "1"),
        ("ENABLE_MODEL_SHADOW_GPU_CULLING", ""), *PROFILES,  # the engine's every model (shader-compile.md 11)
    ),
}  # fmt: skip
# the options a model effect takes, as the shipped cache's keys show them; a material field of that name turns one on
OPTIONS = {
    "model_pbr": ("NORMAL_ENABLED", "SCAO_ENABLED", "ENVIRONMENT_ENABLED", "LINEAR_SHADOW_INTERPOLATION_ENABLED",
                  "NIGHT_GLOW_ENABLED", "SEPARATE_AO_TEXTURE", "EXCLUDE_FROM_SSR_ENABLED"),
    # 1800's material switches are defines its effect tests with #ifdef (2205 reads DIFFUSE_ENABLED and GLOW_ENABLED
    # from its material buffer)
    "anno1800/model_pbr": ("NORMAL_ENABLED", "DIFFUSE_ENABLED", "DYE_MASK_ENABLED", "GLOW_ENABLED",
                           "NIGHT_GLOW_ENABLED"),
    # USE_SMALL_WAVE_TEXTURE: the engine's small-wave map (shader-compile.md 11 "1800's small ocean waves")
    "anno1800/model_water": ("NORMAL_ENABLED", "DIFFUSE_ENABLED", "FLOW_MAP_ENABLED", "DETAIL_MAP_ENABLED",
                             "VERTEX_COLOR_FOAM", "USE_SMALL_WAVE_TEXTURE"),
}  # fmt: skip
# a config's ShaderID -> the effect's stem, per game (read from the materials' fields; 117 numbers them alike)
SHADER_FILES = {"anno2205": {0: "model_phong_ex", 2: "model_glass", 6: "model_decal", 7: "model_water", 8: "model_pbr",
                             9: "model_frame_cycle", 10: "model_interior_mapping", 12: "model_hair",
                             13: "model_arctic", 14: "model_icon", 16: "model_planet"},
                "anno1800": {7: "model_water", 8: "model_pbr"}}  # fmt: skip
# effects the engine draws with their blended state (the third SetStateBlock of their colour technique), after the
# opaque ones; a model_pbr material with an opacity below 1 too
BLENDED_FILES = ("model_glass", "model_decal", "model_frame_cycle", "model_water")
G_BUFFER = "Default"
PREPASS, PREZ_NORMALS = "ModelNormalsDepth", ("PREZ_NORMAL_RT_ENABLED", "1")  # 1800's depth pre-pass, its normals
_SWITCH_RE = re.compile(r"(?m)^\s*#\s*(?:ifn?def\s+(\w+)|(?:el)?if\b(.*)$)")
_SET_BLOCK_RE = re.compile(r"SetStateBlock\s*\(\s*(\w+)")
_PASS_RE = re.compile(r"\bpass\s+\w+\s*\{(.*?)\}", re.S)
# a game's colour pass of a model, by game or "game/effect" where an effect has its own (colour_technique)
TECHNIQUES = {"anno2205": G_BUFFER, "anno1800": "ModelDefault", "anno1800/model_water": G_BUFFER}
_BLOCK_RE = re.compile(r"\bsamplerstateblock\s+(\w+)\s*(?::\s*register\s*\([^)]*\))?\s*\{([^}]*)\}\s*;")
_PARAM_RE = re.compile(r"\bsamplerstateblock\b")
_ADDRESS = {"WRAP": "WRAP", "CLAMP": "CLAMP", "MIRROR": "MIRROR", "BORDER": "BORDER"}


_SEMANTIC_CALL_RE = re.compile(rf"(:\s*)({'|'.join(fxeffect.INDEXED_SEMANTICS)})\s*\(")
SEMANTIC_PREFIX = "FX_SEMANTIC_"


def semantic_calls(text: str) -> str:
    """1800's bracketed semantics (``: NORMAL(0)``) under a macro name of their own: FXC's preprocessor does not expand
    a function-like macro named like a semantic, and once one is defined it expands no function-like macro after it
    (117's DXC does both)."""
    return _SEMANTIC_CALL_RE.sub(lambda m: f"{m.group(1)}{SEMANTIC_PREFIX}{m.group(2)}(", text)


def prelude_1800() -> str:
    """117's prelude for 1800 under FXC: its semantic macros renamed (``semantic_calls``), its samplers left out (1800
    declares the same ones itself, ``DEFINESAMPLER`` in ``constants.fxh``)."""
    lines = fxeffect.prelude().splitlines()
    kept = [line for line in lines if not re.match(r"#define \w+\(index\)|Sampler(Comparison)?State ", line)]
    renamed = [
        f"#define {SEMANTIC_PREFIX}{semantic}(index) {semantic}##index" for semantic in fxeffect.INDEXED_SEMANTICS
    ]
    return "".join(f"{line}{chr(10)}" for line in renamed + kept)


# a structured buffer's elements as a constant buffer holds them: at most 64 KB (1800's model matrices, its light
# cells, its shadow cascades and its casters' words: 1024 instances of 4 cascades)
STRUCTURED_LENGTHS = {"float4x3": 1024, "uint2": 4096, "uint4": 4096, "ShadowInformation": 4}  # each at most 64 KB
_STRUCTURED_RE = re.compile(
    r"(?m)^(\s*(?:__fx_\w+\s+)*)StructuredBuffer\s*<\s*(\w+)\s*>\s*(\w+)\s*(?::\s*register\s*\([^)]*\))?\s*;"
)


# a compute effect's read-only structured buffers that become raw buffers with its read-write ones (``sm5_raw``)
RAW_BUFFERS = {"eye_adaptation": ("cPerTileHistogramsIn", "cMergedHistogramIn")}
_TEMPLATED_RE = re.compile(r"\b(\w+)\.(Load|Store)<(\w+)>\(")


def sm5_raw(hlsl: str) -> str:
    """``rawbuffers.rewrite``'s templated loads and stores (shader model 6.2) as shader model 5 has them, for scalar
    elements: ``b.Load<float>(a)`` -> ``asfloat(b.Load(a))``, ``b.Store<float>(a, v)`` -> ``b.Store(a, asuint(v))``."""
    out, cursor = [], 0
    for match in _TEMPLATED_RE.finditer(hlsl):
        if match.start() < cursor:
            continue
        name, operation, kind = match.groups()
        close = fxeffect.matching(hlsl, match.end() - 1, "()")
        arguments = (
            fxeffect.split_arguments(hlsl[match.end() : close])
            if operation == "Store"
            else [sm5_raw(hlsl[match.end() : close])]
        )
        if operation == "Load":
            call = f"{name}.Load({arguments[0]})"
            text = call if kind == "uint" else f"as{kind}({call})"
        else:
            text = f"{name}.Store({sm5_raw(arguments[0])}, asuint({sm5_raw(arguments[1])}))"
        out += [hlsl[cursor : match.start()], text]
        cursor = close + 1
    return "".join(out) + hlsl[cursor:]


# compute effects whose storage textures the engine binds one slice each (shader-compile.md 11 "1800's ambient
# occlusion"): ``slice_targets`` writes them into one view of the whole array
SLICED = ("ambient_ssao",)
_SLICE_DECL_RE = re.compile(r"RWTexture2DArray\s*<\s*float\s*>\s*cOutQuarterSizeViewDepthTex(\d)\s*;")
_SLICE_WRITE_RE = re.compile(
    r"\b(cOutQuarterSizeViewDepthTex(\d)|cOutQuarterSizeAOTex)\s*\[\s*uint3\s*\(\s*([^,()]+?)\s*,\s*0\s*\)\s*\]"
)
_PAIR_DECL_RE = re.compile(r"RWTexture2D\s*<\s*float2\s*>\s*cOutAOZTex\b")
_PAIR_WRITE_RE = re.compile(r"\bcOutAOZTex\s*\[\s*([^\]]+?)\s*\]\s*=\s*([^;]+);")


def slice_targets(hlsl: str) -> str:
    """1800's SSAO targets as QRhi binds them: the eight one-slice views ``cOutQuarterSizeViewDepthTex0..7`` one view
    of the array written at ``cSliceIndex`` plus the view's number, the AO slice at ``cSliceIndex``; ``cOutAOZTex``
    (two channels) four channels wide, as RGBA16F holds it."""
    hlsl = _SLICE_DECL_RE.sub(
        lambda m: "RWTexture2DArray<float> cOutQuarterSizeViewDepthTex;" if m.group(1) == "0" else "", hlsl
    )

    def write(match: re.Match) -> str:
        name, k = ("cOutQuarterSizeViewDepthTex", match.group(2)) if match.group(2) else ("cOutQuarterSizeAOTex", "0")
        return f"{name}[uint3({match.group(3)}, (uint)cSliceIndex + {k})]"

    hlsl = _SLICE_WRITE_RE.sub(write, hlsl)
    hlsl = _PAIR_DECL_RE.sub("RWTexture2D<float4> cOutAOZTex", hlsl)
    return _PAIR_WRITE_RE.sub(lambda m: f"cOutAOZTex[{m.group(1)}] = float4({m.group(2)}, 0, 0);", hlsl)


# effects whose stencil reads stand in for a stencil QRhi cannot sample (shader-compile.md 11 "1800's screen-space
# reflections"): the trace's mark where its gloss target is covered, the scene's own marks none
STENCIL_READS = ("posteffect_ssr",)
_LOAD_G = r"\s*\.\s*Load\s*\(\s*int3\s*\(\s*([^;]+?)\s*,\s*0\s*\)\s*\)\s*\.\s*g\b"
_SSR_STENCIL_RE = re.compile(r"\bcScreenReflectStencilMap" + _LOAD_G)
_SCENE_STENCIL_RE = re.compile(r"\bcDefaultMRTStencil" + _LOAD_G)
SSR_MARK = 1  # defines.fxh SCREEN_SPACE_REFLECTION_STENCIL_REF


def stencil_reads(hlsl: str) -> str:
    """``posteffect_ssr``'s stencil loads as QRhi can bind them: the reflection's mark where the gloss target
    (``cScreenReflectNormalMap``) is covered, the scene's (the info layer's ``NO_SSR_STENCIL_REF``) never."""
    covered = rf"(cScreenReflectNormalMap.Load(int3(\1, 0)).g > 0.0 ? {SSR_MARK}u : 0u)"
    return _SCENE_STENCIL_RE.sub("0u", _SSR_STENCIL_RE.sub(covered, hlsl))


def structured_as_cbuffers(hlsl: str) -> str:
    """Each read-only ``StructuredBuffer<T> name;`` (its pool words kept, its register hint dropped) as ``cbuffer
    cb_name { T name[n]; }``: QRhi binds no structured view, and FXC has no templated raw-buffer load to read one as
    a raw buffer (117's way); the reads ``name[i]`` stay as they are."""

    def one(match: re.Match) -> str:
        words, element, name = match.groups()
        return f"{words}cbuffer cb_{name} {{ {element} {name}[{STRUCTURED_LENGTHS.get(element, 16)}]; }};"

    return _STRUCTURED_RE.sub(one, hlsl)


def without_declaration(hlsl: str, entry: str) -> str:
    """``hlsl`` without the forward declaration of ``entry``: the linked source declares every function first,
    without its attributes, and FXC then misses a hull shader's domain, partitioning and patch function."""
    return re.sub(rf"^[^\n{{}}]*\b{re.escape(entry)}\s*\([^{{}};]*\)\s*;[ \t]*$", "", hlsl, flags=re.M)


class CNoPass(ValueError):
    """The material's effect is not one this reader draws, or has no such technique."""


def sampler_blocks(hlsl: str) -> tuple[str, dict[str, fxstage.SSampler]]:
    """The effect's ``samplerstateblock name { Filter = ...; Address = ...; }`` declarations as plain ``SamplerState``
    ones (``SamplerComparisonState`` with a comparison function), and their states by name; a parameter of that type
    becomes ``SamplerState``."""
    found: dict[str, fxstage.SSampler] = {}

    def one(match: re.Match) -> str:
        values = {k.lower(): x.upper() for k, x in re.findall(r"(\w+)\s*=\s*([\w.]+)\s*;", match.group(2))}
        both = _ADDRESS.get(values.get("address", ""), "WRAP")
        aniso = values.get("maxanisotropy", "1")
        compares = values.get("comparisonfunc", "NEVER") != "NEVER"  # 1800 gives every sampler NEVER
        found[match.group(1)] = fxstage.SSampler(
            "MIN_MAG_MIP_" + values.get("filter", "LINEAR"), _ADDRESS.get(values.get("addressu", ""), both),
            _ADDRESS.get(values.get("addressv", ""), both), both, int(aniso) if aniso.isdigit() else 16, compares,
        )  # fmt: skip
        return f"Sampler{'Comparison' if compares else ''}State {match.group(1)};"

    text = _BLOCK_RE.sub(one, hlsl)
    return _PARAM_RE.sub("SamplerState", text), found


def preprocessor_switches(source: str) -> tuple[str, ...]:
    """The names an effect's text tests with the preprocessor (``#ifdef X``, ``#if defined(X)``) that a material can
    switch: ``..._ENABLED`` and ``SEPARATE_AO_TEXTURE`` (for an effect without an ``OPTIONS`` entry)."""
    names = set()
    for match in _SWITCH_RE.finditer(source):
        names.update([match.group(1)] if match.group(1) else re.findall(r"defined\s*\(?\s*(\w+)", match.group(2) or ""))
    return tuple(sorted(name for name in names if name.endswith("_ENABLED") or name == "SEPARATE_AO_TEXTURE"))


def pass_of(technique: str) -> tuple[str, int]:
    """A technique's name and the index of its pass: ``Default`` is its first pass, ``Default#1`` its second."""
    name, _, pass_index = technique.partition("#")
    return name, int(pass_index or 0)


def block_state(block: dict[str, str]) -> SState:
    """A ``renderstateblock``'s states as the pipeline's: what it does not name keeps the engine's default (depth
    tested with LESS_EQUAL and written, back faces culled, no blend)."""
    state = {k.lower(): value.upper() for k, value in block.items()}
    cull = {"CW": "front", "CCW": "back", "NONE": "none"}.get(state.get("cullmode", "CCW"), "back")
    return SState(
        cull=cull, depth_test=state.get("zenable", "TRUE") == "TRUE", depth_write=state.get("zwriteenable", "TRUE") == "TRUE",
        depth_func=state.get("zfunc", "LESS_EQUAL").lower(), blend=state.get("alphablendenable", "FALSE") == "TRUE",
        source=state.get("srcblend", "ONE").lower(), dest=state.get("dstblend", "ZERO").lower(),
        source_alpha=state.get("srcblendalpha", "").lower(), dest_alpha=state.get("dstblendalpha", "").lower(),
    )  # fmt: skip


def blended(values: dict, stem: str) -> bool:
    """Whether the engine draws a material in its blended pass (``BLENDED_FILES``, or an opacity below 1)."""
    try:
        opacity = float(values.get("cOpacity", 1) or 1)
    except ValueError:
        opacity = 1.0
    return stem in BLENDED_FILES or opacity < 1.0


def switched_on(value) -> bool:
    """Whether a material field switches its option on: ``1``, ``true`` or a number other than 0 (``0.000000``
    is off, as the configs write it)."""
    raw = str(value if value is not None else "0").strip().lower()
    return raw in ("1", "true") or (raw.replace(".", "", 1).isdigit() and float(raw) != 0)


def material_defines(values: dict, effect: str, game: str = "anno2205", names=None) -> list[tuple[str, str]]:
    """The defines a material gives its effect: the bone count and each option of the effect its fields switch on
    (``names``: the effect's options when ``OPTIONS`` has none)."""
    out = [("NUM_BONES_PER_VERTEX", str(number(values.get("NumBonesPerVertex"), 0)))]
    names = OPTIONS.get(f"{game}/{effect}", OPTIONS.get(effect, names or ()))
    return out + [(name, "") for name in names if switched_on(values.get(name))]


def _technique(built: SBuilt, name: str) -> fxeffect.STechnique | None:
    """The effect's technique of that name, None without one."""
    return next((candidate for candidate in built.effect.techniques if candidate.name == name), None)


GAME = fxstage.SFxcGame(
    NAMESPACE,
    GAME_FLAGS,
    lambda call: f"{call.stage}_5_0",
    lambda hlsl, name: bool(re.search(rf"\b{name}\b", hlsl)),
)


class CForwardSource(fxstage.CFxcSource):
    """A forward-lit game's effects, compiled one pass at a time; thread-safe."""

    def __init__(self, files, compiler: CFxc, game: str = "anno2205") -> None:
        super().__init__(files, compiler, GAME)
        self.m_game = game
        self._m_switches: dict[str, tuple] = {}

    def _read(self, path: str) -> bytes | None:
        return self.m_files.read(path) if self.m_files.exists(path) else None

    def _preprocess(self, fx: str, defines: tuple) -> SBuilt:
        intrinsics = INCLUDE_DIR + "intrinsics_dx.fxh"
        if self.m_game == "anno1800":  # 1800 writes its semantics as 117 does: the 117 reader's prelude
            head = fxeffect.define_lines([*GRAPHICS[self.m_game], *defines]) + prelude_1800()
        else:
            head = fxeffect.define_lines([*SEMANTICS, *GRAPHICS[self.m_game], *defines])
        head += fxeffect.expand_includes(self._read, intrinsics) if self._read(intrinsics) else ""
        body = fxeffect.expand_includes(self._read, fx)
        if self.m_game == "anno1800":
            body = semantic_calls(body)
        text, messages = self.m_compiler.preprocess(head + body, name=fx)
        if not text:
            raise RuntimeError(f"{fx}: {messages[:1500]}")
        text, samplers = sampler_blocks(text)
        stem = fx.rsplit("/", 1)[-1].removesuffix(".fx")
        if stem in STENCIL_READS:
            text = stencil_reads(text)
        found = fxeffect.split_effect(fx11effect.lines_outside_parentheses(text))
        hlsl, raw = found.hlsl, RAW_BUFFERS.get(stem)
        if raw is not None:  # a compute effect: its storage buffers as raw buffers, QRhi's one kind
            hlsl = sm5_raw(rawbuffers.rewrite(hlsl, raw))
        if stem in SLICED:
            hlsl = slice_targets(hlsl)
        return SBuilt(found, text, fxlink.CProgram(structured_as_cbuffers(hlsl)), samplers)

    def _pick(self, built: SBuilt, technique: str, fx: str) -> fxstage.Picked:
        name, pass_index = pass_of(technique)
        tech = _technique(built, name)
        vertex_calls = [call for call in tech.calls if call.stage == "vs"] if tech else []
        pixel_calls = [call for call in tech.calls if call.stage == "ps"] if tech else []
        if pass_index >= min(len(vertex_calls), len(pixel_calls)):
            raise CNoPass(f"{fx} has no pass with a vertex and a pixel shader in {technique}")
        return vertex_calls[pass_index], pixel_calls[pass_index], None

    def pass_count(self, values: dict, technique: str = G_BUFFER) -> int:
        """How many passes with a vertex and a pixel shader the material's ``technique`` has (the planet: its
        atmosphere and its surface); pass ``n`` > 0 is asked for as ``f"{technique}#{n}"``."""
        tech = _technique(self.effect(*self._material(values)), technique)
        return min(sum(call.stage == stage for call in tech.calls) for stage in ("vs", "ps")) if tech else 0

    def _geometry(self, built: SBuilt, technique: str) -> fxeffect.SShaderCall | None:
        """The technique's geometry shader, when its function is in the text: model_arctic names ``gsDefault`` but
        leaves ``USE_GEOMETRY_SHADER`` off, so the function is not there and the engine draws without one."""
        name, pass_index = pass_of(technique)
        tech = _technique(built, name)
        geometry_call = (
            next((call for call in tech.calls if call.stage == "gs"), None) if tech and pass_index == 0 else None
        )
        if geometry_call is None or not re.search(rf"\bvoid\s+{geometry_call.function}\s*\(", built.text):
            return None
        return geometry_call

    def effect_pass(self, fx: str, defines: tuple, technique: str) -> tuple[SStage, ...]:
        """The vertex and pixel shader of ``technique`` of ``fx`` with ``defines`` (and its geometry shader, third,
        where it has one), compiled once and kept in the build cache."""
        stages, _state = super().effect_pass(fx, defines, technique)
        return tuple(stages)

    def _material(self, values: dict) -> tuple[str, tuple]:
        """The effect file and the defines of a model material (``values``: its fields as a dict)."""
        stem = SHADER_FILES.get(self.m_game, {}).get(number(values.get("ShaderID"), -1))
        if stem is None:
            raise CNoPass(f"the ShaderID {values.get('ShaderID')} is not a shader this reader draws")
        fx = f"{FX_DIR}{stem}.fx"
        with self._m_lock:
            if fx not in self._m_switches:
                self._m_switches[fx] = preprocessor_switches(fxeffect.expand_includes(self._read, fx))
        defines = material_defines(values, stem, self.m_game, self._m_switches[fx])
        # the planet's parallax makes it the orbit scene's backdrop, carried along under the camera: a viewer shows
        # it as an object (ours; shader-compile.md section 11 "Planet")
        return fx, tuple(
            define for define in defines if not (stem == "model_planet" and define[0] == "PARALLAX_ENABLED")
        )

    def call_stage(self, fx: str, defines: tuple, technique: str, stage: str = "cs") -> SStage:
        """One shader of ``technique`` of ``fx`` by its stage (``cs``, ``hs``, ``ds``: ``<stage>_5_0``), compiled
        once and kept in the build cache; the passes of a vertex and a pixel shader are ``effect_pass``'s."""
        built = self.effect(fx, defines)
        tech = _technique(built, technique)
        call = next((candidate for candidate in tech.calls if candidate.stage == stage), None) if tech else None
        if call is None:
            raise CNoPass(f"{fx} has no {stage} shader in {technique}")
        hlsl, entry = built.program.entry_source(call)
        text = (without_declaration(hlsl, entry), entry)
        return self.compiled(self.prepare(built, call, profile=f"{stage}_5_0", text=text), fx)

    def colour_technique(self, values: dict) -> str:
        """The technique a model material's colour pass takes (``TECHNIQUES``: 1800's water its own)."""
        stem = SHADER_FILES.get(self.m_game, {}).get(number(values.get("ShaderID"), -1), "")
        return TECHNIQUES.get(f"{self.m_game}/{stem}", TECHNIQUES[self.m_game])

    def model_pass(self, values: dict, technique: str = G_BUFFER) -> tuple[SStage, ...]:
        """The pass of a model material's effect (``values``: the material's fields as a dict)."""
        return self.effect_pass(*self._material(values), technique)

    def prepass(self, values: dict) -> tuple[tuple[SStage, ...], SState] | None:
        """1800's depth pre-pass of a model material with its face normals (``PREPASS``): its stages and states, None
        when the effect has none (shader-compile.md 11 "1800's ambient occlusion")."""
        fx, defines = self._material(values)
        defines = (*defines, PREZ_NORMALS)
        tech = _technique(self.effect(fx, defines), PREPASS)
        if tech is None or not {call.stage for call in tech.calls} >= {"vs", "ps"}:
            return None
        return self.effect_pass(fx, defines, PREPASS), self.technique_state(fx, defines, PREPASS)

    def has_technique(self, values: dict, technique: str) -> bool:
        """Whether the material's effect has ``technique`` with a vertex and a pixel shader (a shadow caster)."""
        tech = _technique(self.effect(*self._material(values)), technique)
        return tech is not None and {call.stage for call in tech.calls} >= {"vs", "ps"}

    def model_state(self, values: dict, technique: str = G_BUFFER) -> SState:
        """The render states the engine draws a material with: the first ``SetStateBlock`` of the technique, its
        blended one (the second but the planar reflection's: 2205's ``sbModelAlpha``, 1800's water's
        ``sbModelWaterAlpha``) for a material of the blended pass (``blended``)."""
        fx, defines = self._material(values)
        return self.technique_state(fx, defines, technique, 1 if blended(values, fx.rsplit("/", 1)[-1][:-3]) else 0)

    def technique_state(self, fx: str, defines: tuple, technique: str, slot: int = 0) -> SState:
        """The render states of the ``slot``-th ``SetStateBlock`` of ``technique``'s pass (``name#n``: its n-th; the
        first block when it has fewer), the planar reflection's blocks (``...Reflection``, front faces) not counted:
        the viewer draws no reflection pass."""
        built = self.effect(fx, defines)
        name, pass_index = pass_of(technique)
        match = re.search(rf"\btechnique\s+{name}\s*\{{(.*?)\n\}}", built.text, re.S)
        passes = _PASS_RE.findall(match.group(1)) if match else []
        found = _SET_BLOCK_RE.findall(
            passes[pass_index] if pass_index < len(passes) else match.group(1) if match else ""
        )
        names = [candidate for candidate in found if not candidate.endswith("Reflection")] or found
        return block_state(built.effect.blocks.get(names[slot if slot < len(names) else 0], {}) if names else {})
