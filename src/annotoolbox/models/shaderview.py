"""117 model configs' materials compiled for the viewer: each material's own effect file's G-buffer pass (its vertex
and deferred pixel shader), from the install's sources, by the game's compiler, bound the way QRhi binds
(``render.gameshader``), with the material's values and maps as ``render.gamedraw`` draws them.

``ShaderSource(files, compiler)`` is the entry: ``stages(stem, values)`` compiles (once, kept in the build cache) and
``material(refs, values)`` reads the maps; ``load(source, cfg)`` is a config's first draw (the spike). The shader
text comes from ``formats.fxeffect`` / ``fxlink``, the switches from ``shaderwarm``. docs/tool-suite.md section 21,
"The game-shader path".
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from ..formats import fxeffect, fxlink, rdmglb, rdmread
from ..infra import buildcache, dxc
from ..render import gamedraw, gamelight, gamepost, gameshader, gamesky, shadowfit
from ..sources.assetdb import number
from . import configtree, ddsdecode, fileresolve, shadercache, shaderdefines

NAMESPACE = "game-shaders"
VERSION = 18  # bump when what a cached compile holds changes (or the rewrite of gameshader.adapt)
# the texture streaming's feedback buffer stays out: QRhi refused the pipeline with it (tool-suite 21)
PASSTHROUGH = ("DISABLE_TEXTURE_FEEDBACK",)
# the techniques a model's draws take part in, by the engine's names: the G-buffer (ModelDeferred, Deferred,
# ClothDeferred, ...), the sun's shadow (ModelObj2Shadow, ClothShadow, CutOutShadow), the transparent pass and the
# depth prepass (ModelDepthPrepass: the cut-outs of a G-buffer pass that tests its depth for equality)
TECHNIQUES = {"gbuffer": re.compile(r"Deferred$"), "shadow": re.compile(r"(?:Obj2|Cloth|CutOut)Shadow$"),
              "transparent": re.compile(r"Transparent$"), "prepass": re.compile(r"DepthPrepass$")}  # fmt: skip
DEFERRED = TECHNIQUES["gbuffer"]
# the deferred shading: the uber technique (every feature, no tile classification), and the engine's tables
SHADING = ("data/shaders/fx/deferred_shading.fx", "DeferredShading_Uber", "csDeferredShading")
ENGINE = "data/base/graphics/engine/"
TABLES = {"brdf": f"{ENGINE}brdf/prefiltered_environment_brdf.dds", "ltc0": f"{ENGINE}area_lights/ltc1.dds",
          "ltc1": f"{ENGINE}area_lights/ltc2.dds", "noise": f"{ENGINE}noise/blue_noise_32_0.dds"}  # fmt: skip
_FIELD = re.compile(r"\n\s*(\w+)\s+(c\w+)\s*\{(.*?)\}", re.S)
_DEFAULT = re.compile(r"default\s*=\s*([^\n]*)")


class NoGBufferPass(ValueError):
    """The effect draws nothing into the G-buffer (a cut-out, a forward-only or transparent shader)."""


@dataclass
class DrawStages:
    """A material's compiled techniques (``ShaderSource.draw_stages``): the stages it draws with, the material
    references its effect reads, its shadow technique's stages (None: it casts none), whether it draws in the
    transparent pass."""

    vs: gamedraw.Stage
    ps: gamedraw.Stage
    refs: tuple
    shadow: tuple | None
    transparent: bool
    prepass: tuple | None = None  # the depth prepass's stages when the G-buffer pass draws only where it lies


@dataclass
class GameDraw:
    vs: gamedraw.Stage
    ps: gamedraw.Stage
    mesh: gamedraw.Mesh
    material: gamedraw.Material


def parse_default(kind: str, text: str):
    """An ``.fxm`` field's default as the structure holds it: a number, a list, 1/0 for a bool; None for a texture
    (its index comes from the maps) or what cannot be read."""
    text = re.sub(r"^[A-Za-z_]\w*\s*\(", "(", text.strip())  # color(1.0), float2(0.0, 0.0)
    if kind.startswith("Texture"):
        return None
    if text.startswith(("true", "false")):
        return 1 if text.startswith("true") else 0
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", text)]
    if not numbers:
        return None
    if kind == "color":
        return numbers * 4 if len(numbers) == 1 else (numbers + [1.0] * 4)[:4]
    return numbers[0] if len(numbers) == 1 else numbers


def _texture_default(body: str) -> str:
    """A texture field's default file in a material description (``default = "data/base/graphics/engine/..."``)."""
    m = _DEFAULT.search(body)
    return m.group(1).split(",")[0].strip().strip('"') if m else ""


def material_defaults(fxm_text: str) -> dict[str, object]:
    """Every field of a material description with its default (``parse_default``)."""
    out = {}
    for kind, name, body in _FIELD.findall(fxm_text):
        m = _DEFAULT.search(body)
        value = parse_default(kind, m.group(1)) if m else None
        if value is not None:
            out[name] = value
    return out


def config_value(name: str, default, values: dict[str, str]):
    """A field's value in the config (``cDiffuseColor.r`` .. for a colour), else its default."""
    if isinstance(default, list) and len(default) == 4 and f"{name}.r" in values:
        return [float(values.get(f"{name}.{k}", d)) for k, d in zip("rgba", default, strict=True)]
    if isinstance(default, list) and f"{name}.x" in values:
        return [float(values.get(f"{name}.{k}", d)) for k, d in zip("xyzw", default, strict=False)]
    if name in values:
        try:
            return float(values[name])
        except ValueError:
            return default
    return default


def _fxm_file(ref: str) -> str:
    """The ``.fxm`` that declares ``material <ref>`` (``ModelCommon`` is ``model_common.fxm``)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", ref).lower() + ".fxm"


# what a graphics set needs that the viewer's frame does not make: ray tracing (QRhi has no ray-tracing API, so no
# acceleration structure), screen-space reflections, local-light shadows, screen-space ambient occlusion
NEEDS = ("ENABLE_RTGI", "ENABLE_RAYTRACED_REFLECTIONS", "ENABLE_SSR", "ENABLE_LOCAL_SHADOWS")


def graphics_set(game: shadercache.GameShaders) -> shadercache.Switches:
    """The shipped graphics set the viewer can feed: one sample a pixel, no multisampling, and the fewest inputs
    it cannot make (``NEEDS``; SSAO sampling off): the game's own fallback without ray tracing."""
    sets = [g for g in game.global_sets() if ("SHADER_AA_SAMPLES", "1") in g and all(n != "MSAA_ENABLED" for n, _ in g)]

    def cost(g) -> tuple:
        names = {n for n, _ in g}
        return (sum(n in names for n in NEEDS), "DISABLE_SSAO_SAMPLING" not in names)

    return min(sets, key=cost)


def technique_calls(effect: fxeffect.Effect, role: str = "gbuffer"):
    """The technique of an effect that takes part in ``role`` (a key of ``TECHNIQUES``) and its first pass's vertex
    and pixel shader; ``NoGBufferPass`` when it has none (for the G-buffer: the effect draws only forward)."""
    for tech in effect.techniques:
        if not TECHNIQUES[role].search(tech.name):
            continue
        vs = next((c for c in tech.calls if c.stage == "vs"), None)
        ps = next((c for c in tech.calls if c.stage == "ps"), None)
        if vs is not None and ps is not None:
            return tech, vs, ps
    raise NoGBufferPass(f"no {role} technique")


def gbuffer_calls(effect: fxeffect.Effect) -> tuple[fxeffect.Technique, fxeffect.ShaderCall, fxeffect.ShaderCall]:
    """The G-buffer technique of an effect (its name ends in ``Deferred``) and its first pass's shaders."""
    return technique_calls(effect, "gbuffer")


class _Files:
    """A tree as ``shadercache.GameShaders`` reads it: ``read``, ``exists`` and ``iter_paths`` (a viewer's tree has
    ``under``)."""

    def __init__(self, files) -> None:
        self.files = files
        self.read, self.exists = files.read, files.exists

    def iter_paths(self, prefix: str):
        own = getattr(self.files, "iter_paths", None)
        return own(prefix) if own is not None else self.files.under(prefix)


class ShaderSource:
    """The install's shaders for drawing: the graphics set, the material descriptions and the compiles made, shared
    by every draw of a view (thread-safe: the compiler runs one compile at a time)."""

    def __init__(self, files, compiler: dxc.Dxc, frame_instances: bool = False) -> None:
        self.files, self.compiler = _Files(files), compiler
        # the model shaders read their instances from the frame's buffers (the island view's draws,
        # ``gameinstances.FrameInstances``), not one buffer per draw (the viewer's)
        self.frame_instances = frame_instances
        self.game = shadercache.GameShaders(self.files)
        self.graphics = graphics_set(self.game)
        self._install = shadercache.overlay_reader(None, self.files)
        self._texts: dict[str, bytes | None] = {}  # the sources read so far: every variant includes the same files
        self._bodies: dict[str, str] = {}
        self.descriptions = shadercache.material_fields(None, self.files)
        self._lock = threading.Lock()

    def _read(self, path: str) -> bytes | None:
        if path not in self._texts:
            self._texts[path] = self._install(path)
        return self._texts[path]

    def source(self, path: str, defines: list[tuple[str, str]]) -> str:
        """``fxeffect.effect_source`` of a variant: the file's text with its includes is made once, the variant's
        defines go in front of it."""
        if path not in self._bodies:
            self._bodies[path] = fxeffect.effect_source(self._read, path, [], self.descriptions)
        head = '#line 1 "<defines>"' + chr(10)
        return self._bodies[path].replace(head, head + fxeffect.define_lines(defines), 1)

    def _compile_all(self, path: str, source: str, what: str) -> dict[str, bytes]:
        """Preprocess, pick the pass shaders ``what`` names (``gbuffer``, or ``<technique>/<function>``), adapt
        and compile each: the files the build cache keeps."""
        with self._lock:
            text, _messages = self.compiler.preprocess(source, ["-HV", "2021"], name=path)
        found = fxeffect.split_effect(text)
        if what in TECHNIQUES:
            try:
                tech, *calls = technique_calls(found, what)
            except NoGBufferPass as e:
                return {"stages.json": json.dumps({"error": str(e)}).encode()}
        else:
            technique, function = what.split("/")
            function, _, nth = function.partition("#")  # "#1": the technique's second call of it (another pass)
            tech, call = [(t, c) for t, c in fxeffect.shader_calls(found)
                          if t.name == technique and c.function == function][int(nth or 0)]  # fmt: skip
            calls = [call]
        files, info = {}, []
        for k, call in enumerate(calls):
            hlsl, entry = fxlink.Program(found.hlsl).entry_source(call, (*tech.passthrough, *PASSTHROUGH))
            adapted = gameshader.adapt(hlsl, entry, vertex=call.stage == "vs", frame=self._frame(source))
            with self._lock:
                done = self.compiler.compile(adapted.hlsl, shaderdefines.compile_args(entry, call.profile), name=path)
            if not done.ok:
                raise RuntimeError(f"{call.function}: {done.messages[:2000]}")
            files[f"{k}.dxil"] = done.object
            inputs = [[i.name, i.type, i.semantic, i.location] for i in adapted.inputs]
            info.append({"entry": entry, "inputs": inputs, "types": gameshader.declared_types(adapted.hlsl)})
        files["stages.json"] = json.dumps({"stages": info, "state": found.state_of(tech)}).encode()
        return files

    def _frame(self, source: str) -> bool:
        """Whether an effect's text is compiled with the frame's instance buffers (it reads instances at all)."""
        return self.frame_instances and gameshader.FRAME_INSTANCES[0] in source

    def _key(self, path: str, switches: tuple, what: str) -> tuple[str, str]:
        """An effect variant's text and the build cache's key of its compiled pass shaders ``what``."""
        source = self.source(path, [*self.graphics, *switches])
        return source, buildcache.digest(VERSION, self.compiler.version(), what, source,
                                         *(("frame",) * self._frame(source)))  # fmt: skip

    def cached(self, stages: dict[str, tuple[str, str, str]]) -> bool:
        """Whether every shader of a ``STAGES`` set is compiled in the build cache already (a later start: reading
        them takes seconds; a first start compiles them for minutes)."""
        for path, technique, function in stages.values():
            _source, key = self._key(path, self.shipped_switches(path), f"{technique}/{function}")
            if buildcache.get_files(NAMESPACE, key) is None:
                return False
        return True

    def pass_stages(self, path: str, switches: tuple, what: str) -> list[gamedraw.Stage]:
        """The pass shaders ``what`` names of an effect variant, compiled once and kept in the build cache under the
        effect's whole text (``NoGBufferPass`` for an effect without a G-buffer pass)."""
        source, key = self._key(path, switches, what)
        kept = buildcache.get_files(NAMESPACE, key)
        if kept is None:
            kept = self._compile_all(path, source, what)
            buildcache.put_files(NAMESPACE, key, kept)
        info = json.loads(kept["stages.json"])
        if "error" in info:
            raise NoGBufferPass(info["error"])
        out = []
        for k, stage in enumerate(info["stages"]):
            blob = kept[f"{k}.dxil"]
            with self._lock:
                reflection = self.compiler.reflect(blob)
            inputs = [gameshader.Input(*i) for i in stage["inputs"]]
            out.append(gamedraw.Stage(blob, stage["entry"], reflection, inputs, stage["types"], info.get("state", {})))
        return out

    def stages(self, stem: str, values: dict[str, str], role: str = "gbuffer"):
        """The vertex and pixel shader of the effect ``stem``'s technique for ``role`` (``TECHNIQUES``) for a
        material's ``values``, and the material references the effect reads; ``NoGBufferPass`` without one."""
        info = self.game.info(stem)
        if info is None:
            raise FileNotFoundError(f"{shadercache.FX_DIR}{stem}.fx")
        switches = shadercache.material_switches(info.refs, self.game.materials, values)
        vs, ps = self.pass_stages(f"{shadercache.FX_DIR}{stem}.fx", switches, role)
        return vs, ps, info.refs

    def draw_stages(self, stem: str, values: dict[str, str]) -> DrawStages:
        """A material's stages: its effect's G-buffer technique, else its transparent one, its shadow technique
        (None for an effect without one) and, for a G-buffer pass that draws only where its depth lies already (a
        tree's leaves), its depth prepass; ``NoGBufferPass`` when it has neither of the first two."""
        try:
            vs, ps, refs = self.stages(stem, values)
            transparent = False
        except NoGBufferPass:  # no G-buffer pass: the transparent pass draws it (or our look)
            vs, ps, refs = self.stages(stem, values, "transparent")
            transparent = True
        try:
            shadow = self.stages(stem, values, "shadow")[:2]
        except NoGBufferPass:
            shadow = None  # an effect without a shadow technique casts none
        prepass = None
        if not transparent and ps.state.get("zfunc", "").upper() == "EQUAL":
            try:
                prepass = self.stages(stem, values, "prepass")[:2]
            except NoGBufferPass:
                prepass = None  # drawn as a plain G-buffer pass: its cut-outs fill
        return DrawStages(vs, ps, refs, shadow, transparent, prepass)

    def material(self, refs, values: dict[str, str], files=None) -> gamedraw.Material:
        """The structures of the descriptions the shader reads (defaults with the config over them) and the maps,
        read from ``files`` (a mod's over the game's) when given, else from the install."""
        fields, maps, paths = {}, {}, {}
        maps_from = files if files is not None else self.files
        for ref, _opts in refs:
            path = f"{shadercache.MATERIAL_DIR}{_fxm_file(ref)}"
            if not self.files.exists(path):
                continue
            text = self.files.read(path).decode("utf-8", "replace")
            fields[f"{ref}Struct"] = {k: config_value(k, d, values) for k, d in material_defaults(text).items()}
            for kind, name, body in _FIELD.findall(text):
                if not kind.startswith("Texture"):
                    continue
                written = values.get(name) or _texture_default(body)  # the description's default when none is set
                texture = fileresolve.texture_file(written, maps_from.exists) if written else ""
                if texture:
                    try:
                        maps[name] = ddsdecode.DdsFile(maps_from.read(texture))
                    except ddsdecode.UnsupportedDds:
                        continue
                    paths[name] = texture
        return gamedraw.Material(fields, maps, paths)

    def pass_stage(self, path: str, technique: str, function: str) -> gamedraw.Stage:
        """One pass shader of an effect no material picks, with the switches of a shipped variant."""
        return self.pass_stages(path, self.shipped_switches(path), f"{technique}/{function}")[0]

    def shading(self) -> tuple[gamedraw.Stage, gamelight.Tables]:
        """The deferred shading's compute shader and the engine's tables it reads (float RGBA, half precision)."""

        def level(path: str, mip: int = 0):
            return ddsdecode.DdsFile(self.files.read(path)).level_float(mip).astype("f2")

        brdf = ddsdecode.DdsFile(self.files.read(TABLES["brdf"]))
        tables = gamelight.Tables([brdf.level_float(m).astype("f2") for m in range(brdf.info.mips)],
                                  [level(TABLES["ltc0"]), level(TABLES["ltc1"])], level(TABLES["noise"]))  # fmt: skip
        return self.pass_stage(*SHADING), tables

    def stage_set(self, stages: dict[str, tuple[str, str, str]]) -> dict[str, gamedraw.Stage]:
        """The compiled shaders of a render module's ``STAGES`` (key -> effect, technique, function), by key."""
        return {key: self.pass_stage(*spec) for key, spec in stages.items()}

    def own_stage(self, hlsl: str, entry: str, profile: str = "cs_6_2") -> gamedraw.Stage:
        """A shader of ours (a step the view needs that no game shader does), compiled once by the game's compiler
        and kept in the build cache."""
        key = buildcache.digest(VERSION, self.compiler.version(), "own", entry, profile, hlsl)
        kept = buildcache.get_files(NAMESPACE, key)
        if kept is None:
            with self._lock:
                done = self.compiler.compile(hlsl, shaderdefines.compile_args(entry, profile), name=entry)
            if not done.ok:
                raise RuntimeError(f"{entry}: {done.messages[:2000]}")
            kept = {"0.dxil": done.object}
            buildcache.put_files(NAMESPACE, key, kept)
        with self._lock:
            reflection = self.compiler.reflect(kept["0.dxil"])
        return gamedraw.Stage(kept["0.dxil"], entry, reflection, [], gameshader.declared_types(hlsl))

    def shadow_fit(self) -> dict[str, gamedraw.Stage]:
        """The compute shaders of the sun's cascade analysis (``shadowfit.STAGES``)."""
        return self.stage_set(shadowfit.STAGES)

    def ambient_text(self, ambient: str = "amb_roman_01") -> str:
        """The text of an ambient file (``gamesky.AMBIENT``): the sky's, the clouds' and the water's values."""
        return self.files.read(gamesky.AMBIENT.format(ambient)).decode("utf-8", "replace")

    def sky_passes(self, ambient: str = "amb_roman_01") -> tuple[dict[str, gamedraw.Stage], dict]:
        """The compute shaders of the sky's light (``gamesky.STAGES``), and the ambient's atmosphere
        (``gamesky.read_atmosphere``)."""
        return self.stage_set(gamesky.STAGES), gamesky.read_atmosphere(self.ambient_text(ambient))

    def float_maps(self, paths: dict[str, str]) -> dict:
        """Level 0 of each named DDS file (name -> path), as float RGBA arrays (the engine's global maps)."""
        return {name: ddsdecode.DdsFile(self.files.read(path)).level_float(0) for name, path in paths.items()}

    def post_stages(self) -> dict[str, gamedraw.Stage]:
        """The vertex and pixel shader of each post effect (``gamepost.STAGES``)."""
        return self.stage_set(gamepost.STAGES)

    def shipped_switches(self, effect: str) -> shadercache.Switches:
        """The material switches of the first shipped variant of an effect: for a shader no material picks, they
        carry the constants the engine passes (``DEFERRED_SHADING_TILE_SIZE_X`` = 16 ...)."""
        info = self.game.info(Path(effect).stem)
        paths = self.game.by_file.get(info.vanilla_hash, []) if info else []
        return shadercache.read_switches(self.files.read(sorted(paths)[0]))[1] if paths else ()


first_values = configtree.first_values  # a MATERIAL node's fields by name, the first winning


# ids the viewer's transparent pass draws besides the checked table (material-passes.md "The passes by ShaderID"):
# the effect files of those names; not checked against the shipped cache's variants (SHADER_FILES is)
TRANSPARENT_FILES = {2: "model_glass", 11: "model_skin", 12: "model_hair", 26: "model_ocean_decal"}
# the island's nature (material-fields.md "which shader reads which parameters"): grass, trees and the terrain's rock
# meshes, by the effect files of those names; drawn by the island view, which lays their cut-outs by the depth prepass
# (the viewer does not: its leaves would fill), not checked against the shipped cache's variants either
NATURE_FILES = {21: "model_grass", 23: "model_tree", 24: "model_terrain", 27: "model_tree_experimental"}


def effect_of(shader_id: int) -> str | None:
    """The effect file's stem a config's ``ShaderID`` draws with (None for an id no file is known for)."""
    return shadercache.SHADER_FILES.get(shader_id) or TRANSPARENT_FILES.get(shader_id)


def load(source: ShaderSource, cfg: str) -> GameDraw:
    """The first material of the config ``cfg`` whose effect has a G-buffer pass, with its mesh's group."""
    text = source.files.read(cfg).decode("utf-8", "replace")
    model = next(n for n in configtree.read_tree(text).walk() if n.kind == "MODEL")
    rdm = rdmread.read_rdm(source.files.read(configtree.mesh_file(model)))
    mesh = next(m for m in rdm.meshes if m.vertex_count and m.indices)
    for slot, node in configtree.materials_at(model).items():
        values = first_values(node.fields)
        stem = effect_of(number(values.get("ShaderID"), -1))
        if stem is None:
            continue
        try:
            vs, ps, refs = source.stages(stem, values)
        except NoGBufferPass:
            continue
        indices = rdmglb.material_indices(mesh, slot) or mesh.indices
        return GameDraw(vs, ps, gamedraw.Mesh.of_rdm(mesh, indices), source.material(refs, values))
    raise ValueError(f"{cfg}: no material with a G-buffer pass")
