"""Anno 2070's model shaders compiled for the viewer: a material's effect file read from the install, preprocessed and
compiled by the compiler the game used (``d3dcompiler_43``, FXC for shader model 5), one technique's pass at a time,
the shaders kept in the build cache.

``CSource2070(files, compiler)`` is the entry: ``model_pass(values, technique)`` compiles the pass of a model material
(``values``: ``material_values``), ``effect_pass(fx, defines, technique)`` any other effect (the deferred lighting).
The defines the engine sets, which material switch reaches which shader and the result of checking our compile against
the game's own cache: docs/rendering/shader-compile.md section 10; the materials: docs/rendering/materials-2070.md.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from ..formats import fx11effect, fxeffect, fxlink
from ..infra.fxc import CFxc
from . import fxstage
from .cfg2070 import object_color
from .fxstage import SBuilt, SStage, SState

NAMESPACE = "shaders2070"
FX_DIR = "data/system/shaders/fx/"
BACKSLASH = chr(92)
# the defines the engine gives every effect (shader-compile.md section 10): the profile words, the API and the
# graphics options of the owner's configuration (QualityLevel 4); every compile of the game's own cache matched,
# the shadow techniques with the hardware comparison and the soft taps
GLOBALS = (
    ("vs_max", "vs_5_0"), ("ps_max", "ps_5_0"), ("hs_max", "hs_5_0"), ("ds_max", "ds_5_0"), ("gs_max", "gs_5_0"),
    ("DX_VERSION", "11"), ("MAX_ANISOTROPY", "16"), ("MAX_SHADER_CONSTANTS", "256"),
    ("ENABLE_MULTISAMPLEANTIALIAS", "FALSE"), ("GLOBAL_NORMAL_ENABLED", "1"), ("POLLUTION_ENABLED", "1"),
    ("ATMOSPHERIC_SCATTERING", "1"), ("SHADOW_ENABLED", "1"), ("LOW_QUALITY_HDR_ENABLED", "1"),
    ("INVERSE_CLOUD_SHADOWS", "1"), ("HARDWARESHADOW_ENABLED", "1"), ("SOFTSHADOWS_ENABLED", "1"),
)  # fmt: skip
# Anno 1404's (docs/rendering/shader-compile.md section 12): Direct3D 10, shader model 4, forward lit; its sources
# declare the ambient trick only in a comment, a normal-mapped model does not compile without it; no cache to check
# the shadow's against, so 2070's (the same shadow code)
GLOBALS_1404 = (
    ("vs_max", "vs_4_0"), ("ps_max", "ps_4_0"), ("gs_max", "gs_4_0"), ("DX_VERSION", "10"), ("MAX_ANISOTROPY", "16"),
    ("MAX_SHADER_CONSTANTS", "256"), ("ENABLE_MULTISAMPLEANTIALIAS", "FALSE"), ("GLOBAL_NORMAL_ENABLED", "1"),
    ("ATMOSPHERIC_SCATTERING", "1"), ("SHADOW_ENABLED", "1"), ("ENABLE_AMBIENT_TRICK", "1"),
    ("EXTRA_AMBIENT_FACTOR", "0.4"), ("HARDWARESHADOW_ENABLED", "1"), ("SOFTSHADOWS_ENABLED", "1"),
)  # fmt: skip
# the config owner's shader tables (CMaterialConfigData; materials-2070 sections 1 to 3): index -> (effect, switch mask)
MODEL_SHADERS = {
    0: ("model_glow.fx", 0x1F), 1: ("model_water.fx", 0x14), 2: ("model_waterfall.fx", 0), 3: ("model_foamfall.fx", 0),
    4: ("model_cutout.fx", 0), 5: ("model_portrait.fx", 0x1F), 6: ("model_dye.fx", 0x1F), 7: ("model_icon.fx", 0x1F),
    8: ("model_lava.fx", 0x1F), 9: ("model_animated_icon.fx", 0x1F), 10: ("model_glow.fx", 0x1F),
}  # fmt: skip
SWITCHES = (("DIFFUSE", "diffuse"), ("NORMAL", "normal"), ("ENVIRONMENT", "environment"), ("PARALLAX", "parallax"),
            ("RIPPLES", "ripples"))  # fmt: skip
# the texture slots of a material -> the shader's cTexture<n>
SLOTS = ("diffuse", "normal", "environment", "height", "mask", "ripples")
G_BUFFER = "Deferred"


class CNoPass(ValueError):
    """The material's effect has no such technique (a forward-only, water or cut-out shader)."""


def material_values(material, texture, color_transform=None, cloth=None) -> tuple:
    """A cfg material (``cfg2070.SMaterial``) as the (name, value) pairs ``SMaterial.values`` holds: owner, shader
    index, vertex format, bones, the five switches, the game paths of its six textures (``texture`` turns a
    config's name into the file the game loads), its diffuse colour (``cMatDiffuse``) and the object colour its
    model's ``COLOR_TRANSFORM`` (``ct``) gives (``ObjectColor`` without the owner's colour, ``ObjectPlayer`` 1 where
    the owner's colour multiplies it in); a ``cloth``'s (``cfg2070.SCloth``) ripple and sides."""
    values = [("Owner", material.owner), ("ShaderIndex", material.shader_index), ("VertexFormat", material.vertex_format),
              ("Bones", material.bones)]  # fmt: skip
    values += [(f"Switch.{k}", int(material.flags.get(k, False))) for _tag, k in SWITCHES]
    values += [(f"Texture.{k}", texture(material.textures[k]) if material.textures.get(k) else "") for k in SLOTS]
    values.append(("DiffuseColor", tuple(material.colors.get("diffuse", (1.0, 1.0, 1.0, 1.0)))))
    takes_participant_colour = int(color_transform.takes_participant_colour()) if color_transform is not None else 0
    values += [
        ("ObjectColor", object_color(color_transform, (1.0, 1.0, 1.0))),
        ("ObjectPlayer", takes_participant_colour),
    ]
    if cloth is not None:
        values += [("ClothRipple", tuple(cloth.ripple)), ("DoubleSided", int(cloth.double_sided))]
    return tuple(values)


def material_defines(values: dict) -> list[tuple[str, str]]:
    """The defines a material gives its effect (``GetShaderMacros``): its switches that its shader accepts, the bones,
    the vertex format and the define named by it."""
    index = int(values.get("ShaderIndex", 0))
    _fx, mask = MODEL_SHADERS.get(index, ("", 0))
    out = [
        (f"{name}_ENABLED", "1")
        for bit, (name, key) in enumerate(SWITCHES)
        if mask >> bit & 1 and values.get(f"Switch.{key}")
    ]
    out.append(("NUM_BONES_PER_VERTEX", str(min(int(values.get("Bones", 0)), 4))))
    vertex_format = str(values.get("VertexFormat", ""))
    if vertex_format:
        out += [("VERTEX_FORMAT", f'"{vertex_format}"'), (vertex_format.upper(), "1")]
    return out


def model_effect(values: dict) -> str:
    """The effect file of a model material (a ``MODEL`` owner's table)."""
    owner, index = values.get("Owner", "MODEL"), int(values.get("ShaderIndex", 0))
    if owner != "MODEL" or index not in MODEL_SHADERS:
        raise CNoPass(f"the {owner} shader {index} is not one of the model shaders this reader draws")
    return FX_DIR + MODEL_SHADERS[index][0]


@dataclass
class SPassStages:
    """One pass of an effect: its vertex and pixel shader and its render states."""

    vertex_stage: SStage
    pixel_stage: SStage
    state: SState
    technique: str


def _fill_defaults(program: fxlink.CProgram, call: fxeffect.SShaderCall) -> fxeffect.SShaderCall:
    """The call with the defaults of the ``uniform`` parameters it gives no argument for (the effect compiler
    folds them in; a plain compile would make them constants of a buffer)."""
    heads = [program.m_items[i].head for i in program.m_by_name.get(call.function, ()) if program.m_items[i].head]
    for head in heads:
        parameters = fxeffect.split_arguments(head[head.index("(") + 1 : head.rindex(")")], is_parameter_list=True)
        uniform = [parameter for parameter in parameters if parameter.lstrip().startswith("uniform")]
        if len(uniform) < len(call.args):
            continue
        defaults = [parameter.split("=", 1)[1].strip() if "=" in parameter else None for parameter in uniform]
        rest = defaults[len(call.args) :]
        if None in rest:
            continue
        return fxeffect.SShaderCall(call.stage, call.model, call.function, (*call.args, *rest))
    return call


GAME = fxstage.SFxcGame(
    NAMESPACE,
    0,
    lambda call: call.profile,
    lambda hlsl, name: f"SamplerState {name};" in hlsl or f"SamplerComparisonState {name};" in hlsl,
    True,
)


class CSource2070(fxstage.CFxcSource):
    """The install's effects, compiled one pass at a time; thread-safe."""

    def __init__(self, files, compiler: CFxc, defines: tuple = GLOBALS, game: fxstage.SFxcGame = GAME,
                 missing_empty: bool = False) -> None:  # fmt: skip
        super().__init__(files, compiler, game)
        self.m_globals = defines  # 1404's: ``GLOBALS_1404``; 1701's source passes its own and its game
        # 1404's sea and disaster clouds include a noise.fxh no install ships and read nothing of it: its engine
        # serves the include folder from a table it loads first, a missing one as empty **[I]** (shader-compile.md 12)
        self.m_missing_empty = missing_empty

    def _include(self, resolved: dict[str, str], base: str):
        def read(name: str, parent: str) -> bytes | None:
            folder = posixpath.dirname(resolved.get(parent, base))
            # beside the including file, else in the include folder beside the effects' (the water effects name
            # their includes bare: the engine searches there)
            for where in (folder, posixpath.join(posixpath.dirname(base), "../include")):
                path = posixpath.normpath(posixpath.join(where, name.replace(BACKSLASH, "/"))).lower()
                if self.m_files.exists(path):
                    break
            else:
                return b"" if self.m_missing_empty else None
            resolved[name] = path
            return self.m_files.read(path)

        return read

    def preprocessed(self, fx: str, defines: tuple) -> str:
        """The effect's text after the compiler's preprocessor, with the engine's and the material's defines."""
        text, messages = self.m_compiler.preprocess(
            self.m_files.read(fx).decode("latin1"), [*self.m_globals, *defines], include=self._include({}, fx), name=fx
        )
        if not text:
            raise RuntimeError(f"{fx}: {messages[:1500]}")
        return text

    def _preprocess(self, fx: str, defines: tuple) -> SBuilt:
        text = self.preprocessed(fx, defines)
        found = fx11effect.split(text)
        samplers = fxstage.samplers_of(found.hlsl)
        found.hlsl = fxstage.plain_samplers(found.hlsl)
        return SBuilt(found, text, fxlink.CProgram(found.hlsl), samplers)

    def _entry(self, built: SBuilt, call: fxeffect.SShaderCall, fx: str) -> fxstage.SEntry:
        return self.prepare(built, _fill_defaults(built.program, call))

    def _pick(self, built: SBuilt, technique: str, fx: str) -> fxstage.Picked:
        tech = built.effect.technique(technique)
        if tech is None or not tech.passes:
            raise CNoPass(f"{fx} has no technique {technique}")
        effect_pass = tech.passes[0]
        vertex_call, pixel_call = effect_pass.call("vs"), effect_pass.call("ps")
        if vertex_call is None or pixel_call is None:
            raise CNoPass(f"{fx} {technique} has no vertex and pixel shader")
        blocks = fxstage.read_states(built.text)
        return (
            vertex_call,
            pixel_call,
            fxstage.pass_state(blocks, effect_pass.rasterizer, effect_pass.depth, effect_pass.blend),
        )

    def effect_pass(self, fx: str, defines: tuple, technique: str) -> SPassStages:
        """The vertex and pixel shader and states of ``technique`` of effect ``fx`` with ``defines``, compiled once
        and kept in the build cache (``CNoPass`` for a technique the effect lacks)."""
        (vertex_stage, pixel_stage), state = super().effect_pass(fx, defines, technique)
        return SPassStages(vertex_stage, pixel_stage, state, technique)

    def model_pass(self, values: dict, technique: str = G_BUFFER) -> SPassStages:
        """The pass of a model material's effect (``values``: ``material_values`` as a dict)."""
        return self.effect_pass(model_effect(values), tuple(material_defines(values)), technique)
