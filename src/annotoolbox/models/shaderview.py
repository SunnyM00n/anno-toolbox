"""117 model configs' materials compiled for the viewer: each material's own effect file's G-buffer pass (its vertex
and deferred pixel shader), from the install's sources, by the game's compiler, bound the way QRhi binds
(``render.gameshader``), with the material's values and maps as ``render.gamedraw`` draws them.

``CShaderSource(files, compiler)`` is the entry: ``stages(stem, values)`` compiles (once, kept in the build cache) and
``material(refs, values)`` reads the maps; ``load(source, cfg)`` is a config's first draw (the spike). The shader
text comes from ``formats.fxeffect`` / ``fxlink``, the switches from ``shaderwarm``. docs/tool-suite.md section 21,
"The game-shader path".
"""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..formats import fxeffect, fxlink, rdmglb, rdmread
from ..infra import buildcache, dxc
from ..render import gamedraw, gamelight, gamepost, gameshader, gamesky, shadowfit
from ..sources.assetdb import decimal, number
from . import configtree, ddsdecode, fileresolve, shadercache, shaderdefines

NAMESPACE = "game-shaders"
VERSION = 19  # bump when what a cached variant record holds changes (or the rewrite of gameshader.adapt)
SHADER_VERSION = 1  # bump when what a cached shader holds changes (its bytecode and reflection)
MEMO = 8192  # the variants and shaders a source keeps at hand (the rest is read from the build cache again) [ours]
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


class CNoGBufferPass(ValueError):
    """The effect draws nothing into the G-buffer (a cut-out, a forward-only or transparent shader)."""


@dataclass
class SDrawStages:
    """A material's compiled techniques (``CShaderSource.draw_stages``): the stages it draws with, the material
    references its effect reads, its shadow technique's stages (None: it casts none), whether it draws in the
    transparent pass."""

    vertex_stage: gamedraw.SStage
    pixel_stage: gamedraw.SStage
    refs: tuple
    shadow: tuple | None
    is_transparent: bool
    prepass: tuple | None = None  # the depth prepass's stages when the G-buffer pass draws only where it lies


@dataclass
class SGameDraw:
    vertex_stage: gamedraw.SStage
    pixel_stage: gamedraw.SStage
    mesh: gamedraw.CMesh
    material: gamedraw.SMaterial


def parse_default(kind: str, text: str):
    """An ``.fxm`` field's default as the structure holds it: a number, a list, 1/0 for a bool; None for a texture
    (its index comes from the maps) or what cannot be read."""
    text = re.sub(r"^[A-Za-z_]\w*\s*\(", "(", text.strip())  # color(1.0), float2(0.0, 0.0)
    if kind.startswith("Texture"):
        return None
    if text.startswith(("true", "false")):
        return 1 if text.startswith("true") else 0
    numbers = [float(token) for token in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", text)]
    if not numbers:
        return None
    if kind == "color":
        return numbers * 4 if len(numbers) == 1 else (numbers + [1.0] * 4)[:4]
    return numbers[0] if len(numbers) == 1 else numbers


def _texture_default(body: str) -> str:
    """A texture field's default file in a material description (``default = "data/base/graphics/engine/..."``)."""
    match = _DEFAULT.search(body)
    return match.group(1).split(",")[0].strip().strip('"') if match else ""


def material_defaults(fxm_text: str) -> dict[str, object]:
    """Every field of a material description with its default (``parse_default``)."""
    defaults = {}
    for kind, name, body in _FIELD.findall(fxm_text):
        match = _DEFAULT.search(body)
        value = parse_default(kind, match.group(1)) if match else None
        if value is not None:
            defaults[name] = value
    return defaults


def config_value(name: str, default, values: dict[str, str]):
    """A field's value in the config (``cDiffuseColor.r`` .. for a colour), else its default."""
    if isinstance(default, list) and len(default) == 4 and f"{name}.r" in values:
        return [
            decimal(values.get(f"{name}.{channel}"), fallback)
            for channel, fallback in zip("rgba", default, strict=True)
        ]
    if isinstance(default, list) and f"{name}.x" in values:
        return [
            decimal(values.get(f"{name}.{channel}"), fallback)
            for channel, fallback in zip("xyzw", default, strict=False)
        ]
    return decimal(values[name], default) if name in values else default


def _fxm_file(ref: str) -> str:
    """The ``.fxm`` that declares ``material <ref>`` (``ModelCommon`` is ``model_common.fxm``)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", ref).lower() + ".fxm"


def technique_calls(effect: fxeffect.SEffect, role: str = "gbuffer"):
    """The technique of an effect that takes part in ``role`` (a key of ``TECHNIQUES``) and its first pass's vertex
    and pixel shader; ``CNoGBufferPass`` when it has none (for the G-buffer: the effect draws only forward)."""
    for technique in effect.techniques:
        if not TECHNIQUES[role].search(technique.name):
            continue
        vs = next((call for call in technique.calls if call.stage == "vs"), None)
        ps = next((call for call in technique.calls if call.stage == "ps"), None)
        if vs is not None and ps is not None:
            return technique, vs, ps
    raise CNoGBufferPass(f"no {role} technique")


def gbuffer_calls(effect: fxeffect.SEffect) -> tuple[fxeffect.STechnique, fxeffect.SShaderCall, fxeffect.SShaderCall]:
    """The G-buffer technique of an effect (its name ends in ``Deferred``) and its first pass's shaders."""
    return technique_calls(effect, "gbuffer")


class _CFiles:
    """A tree as ``shadercache.CGameShaders`` reads it: ``read``, ``exists`` and ``iter_paths`` (a viewer's tree has
    ``under``)."""

    def __init__(self, files) -> None:
        self.m_files = files
        self.read, self.exists = files.read, files.exists

    def iter_paths(self, prefix: str):
        own = getattr(self.m_files, "iter_paths", None)
        return own(prefix) if own is not None else self.m_files.under(prefix)


class _CMissing(LookupError):
    """A compiled shader the build cache no longer holds (its variant is compiled again)."""


class CShaderSource:
    """The install's shaders for drawing: the graphics set, the material descriptions and the compiles made, shared
    by every draw of a view and every view of a session (``shared``). Thread-safe: each thread compiles with its own
    instance of the compiler, and a variant or shader asked for by several threads at once is made once."""

    def __init__(self, files, compiler: dxc.CDxc, frame_instances: bool = False) -> None:
        self.m_files, self.m_compiler = _CFiles(files), compiler
        # the model shaders read their instances from the frame's buffers (the island view's draws,
        # ``gameinstances.CFrameInstances``), not one buffer per draw (the viewer's)
        self.m_frame_instances = frame_instances
        self.m_game = shadercache.CGameShaders(self.m_files)
        self.m_graphics = shadercache.view_graphics_set(self.m_game)
        self._m_install = shadercache.overlay_reader(None, self.m_files)
        self._m_texts: dict[str, bytes | None] = {}  # the sources read so far: every variant includes the same files
        self._m_bodies: dict[str, str] = {}
        self._m_digests: dict[str, tuple[str, bool]] = {}  # path -> its text's digest, whether it reads the frame's
        self.m_descriptions = shadercache.material_fields(None, self.m_files)
        self._m_lock = threading.Lock()  # the tables below
        self._m_serial = threading.Lock()  # a compiler without instances per thread (a test's): one call at a time
        self._m_memo: dict = {}  # a variant's or shader's key -> the Future of what it compiles to (``_once``)
        self._m_local = threading.local()  # ``passing`` on this thread
        self._m_build: str | None = None  # the game build the manifests are kept under (``_record``)

    @contextmanager
    def passing(self):
        """What this thread compiles is not kept at hand, unless another thread asked for it meanwhile: the start-up
        stage's pre-warm fills the build cache, not the session's memory (an island opened meanwhile still waits for
        the same compile instead of making it again)."""
        self._m_local.passing = True
        try:
            yield
        finally:
            self._m_local.passing = False

    def _read(self, path: str) -> bytes | None:
        if path not in self._m_texts:
            self._m_texts[path] = self._m_install(path)
        return self._m_texts[path]

    def _call(self, method: str, *arguments, **keywords):
        """A call of this thread's compiler (``CDxc.for_thread``)."""
        for_thread = getattr(self.m_compiler, "for_thread", None)
        if for_thread is not None:
            return getattr(for_thread(), method)(*arguments, **keywords)
        with self._m_serial:
            return getattr(self.m_compiler, method)(*arguments, **keywords)

    def _once(self, key, make):
        """``make()`` run once for every thread that asks for ``key`` (the others wait for it) and kept (``MEMO``);
        a failure is not kept, asked again it runs again, except an effect without the pass (its answer)."""
        with self._m_lock:
            pending = self._m_memo.get(key)
            is_owner = pending is None
            if is_owner:
                while len(self._m_memo) >= MEMO:  # the oldest go; asked again, they are read from the build cache
                    del self._m_memo[next(iter(self._m_memo))]
                pending = self._m_memo[key] = Future()
            else:
                pending.wanted = True  # another thread waits for it: kept even when made ``passing``
        if is_owner:
            passing = getattr(getattr(self, "_m_local", None), "passing", False)
            try:
                pending.set_result(make())
            except BaseException as error:
                if not isinstance(error, CNoGBufferPass):
                    with self._m_lock:
                        self._m_memo.pop(key, None)
                pending.set_exception(error)
            if passing and not pending.exception():
                with self._m_lock:
                    if not getattr(pending, "wanted", False) and self._m_memo.get(key) is pending:
                        del self._m_memo[key]
        return pending.result()

    def source(self, path: str, defines: list[tuple[str, str]]) -> str:
        """``fxeffect.effect_source`` of a variant: the file's text with its includes is made once, the variant's
        defines go in front of it."""
        if path not in self._m_bodies:
            self._m_bodies.setdefault(path, fxeffect.effect_source(self._read, path, [], self.m_descriptions))
        head = '#line 1 "<defines>"' + chr(10)
        return self._m_bodies[path].replace(head, head + fxeffect.define_lines(defines), 1)

    def _digest(self, path: str) -> tuple[str, bool]:
        """The digest of an effect's text with its includes, and whether it reads the frame's instance buffers
        (``_frame``), made once per file."""
        found = self._m_digests.get(path)
        if found is None:  # once, whichever threads ask first: each would read and join the same files

            def make() -> tuple[str, bool]:
                text = self.source(path, [])
                return buildcache.digest(text), self._frame(text)

            found = self._m_digests.setdefault(path, self._once(("digest", path), make))
        return found

    def _compile_all(self, path: str, source: str, what: str) -> dict[str, bytes]:
        """Preprocess, pick the pass shaders ``what`` names (``gbuffer``, or ``<technique>/<function>``), adapt
        and compile each (``_compiled``): the stages' record the build cache keeps for the variant."""
        text, _messages = self._call("preprocess", source, ["-HV", "2021"], name=path)
        found = fxeffect.split_effect(text)
        if what in TECHNIQUES:
            try:
                pass_technique, *calls = technique_calls(found, what)
            except CNoGBufferPass as error:
                return {"stages.json": json.dumps({"error": str(error)}).encode()}
        else:
            technique, function = what.split("/")
            function, _, call_index_text = function.partition(
                "#"
            )  # "#1": the technique's second call of it (another pass)
            pass_technique, call = [(candidate_technique, candidate_call) for candidate_technique, candidate_call in fxeffect.shader_calls(found)
                          if candidate_technique.name == technique and candidate_call.function == function][int(call_index_text or 0)]  # fmt: skip
            calls = [call]
        program, frame = fxlink.CProgram(found.hlsl), self._digest(path)[1]
        stage_records = []
        for call in calls:
            hlsl, entry = program.entry_source(call, (*pass_technique.passthrough, *PASSTHROUGH))
            adapted = gameshader.adapt(hlsl, entry, is_vertex=call.stage == "vs", is_frame=frame)
            key = self._compiled(adapted.hlsl, shaderdefines.compile_args(entry, call.profile), path, call.function)
            inputs = [
                [shader_input.name, shader_input.type, shader_input.semantic, shader_input.location]
                for shader_input in adapted.inputs
            ]
            stage_records.append({"entry": entry, "inputs": inputs, "types": gameshader.declared_types(adapted.hlsl),
                         "shader": key})  # fmt: skip
        return {"stages.json": json.dumps({"stages": stage_records, "state": found.state_of(pass_technique)}).encode()}

    def shader_key(self, hlsl: str, args: list[str], name: str) -> str:
        """The build cache's key of one compile: exactly what the compiler is handed (the text after every rewrite,
        with the variant's defines in it, the arguments, the file name it reports errors against) and the compiler's
        version; nothing of the machine or the run, so a later start and another island find it."""
        return buildcache.digest(SHADER_VERSION, self.m_compiler.version(), name, *args, hlsl)

    def _compiled(self, hlsl: str, args: list[str], name: str, function: str = "") -> str:
        """``hlsl`` compiled with ``args`` once (two variants whose shader comes out as the same text share the
        compile) and kept in the build cache with its reflection; its key (``shader_key``)."""
        key = self.shader_key(hlsl, args, name)

        def make() -> tuple[bytes, object]:
            try:
                return self._kept_shader(key)
            except _CMissing:
                pass
            done = self._call("compile", hlsl, args, name=name)
            if not done.ok:
                raise RuntimeError(f"{function or name}: {done.messages[:2000]}")
            reflection = self._call("reflect", done.object)
            buildcache.put_files(NAMESPACE, key, {"dxil": done.object, "reflection": dxc.reflection_json(reflection)})
            return done.object, reflection

        self._once(("shader", key), make)
        return key

    def _kept_shader(self, key: str) -> tuple[bytes, object]:
        """A compiled shader and its reflection from the build cache; ``_CMissing`` when it holds none."""
        kept = buildcache.get_files(NAMESPACE, key)
        if kept is None:
            raise _CMissing(key)
        return kept["dxil"], dxc.reflection_of_json(kept["reflection"])

    def _frame(self, source: str) -> bool:
        """Whether an effect's text is compiled with the frame's instance buffers (it reads instances at all)."""
        return self.m_frame_instances and gameshader.FRAME_INSTANCES[0] in source

    def _key(self, path: str, switches: tuple, what: str) -> str:
        """The build cache's key of an effect variant's compiled pass shaders ``what``: the effect's text with its
        includes (by its digest), the variant's defines, the compiler and the rewrite (``VERSION``)."""
        digest, frame = self._digest(path)
        defines = fxeffect.define_lines([*self.m_graphics, *switches])
        return buildcache.digest(VERSION, self.m_compiler.version(), what, digest, defines, *(("frame",) * frame))

    def cached(self, stages: dict[str, tuple[str, str, str]]) -> bool:
        """Whether every shader of a ``STAGES`` set is compiled in the build cache already (a later start: reading
        them takes seconds; a first start compiles them for minutes)."""
        for path, technique, function in stages.values():
            key = self._key(path, self.shipped_switches(path), f"{technique}/{function}")
            if buildcache.get_files(NAMESPACE, key) is None:
                return False
        return True

    def pass_stages(self, path: str, switches: tuple, what: str) -> list[gamedraw.SStage]:
        """The pass shaders ``what`` names of an effect variant, compiled once and kept in the build cache under the
        effect's text and the variant's defines (``CNoGBufferPass`` for an effect without a G-buffer pass)."""
        key = self._key(path, switches, what)
        return list(self._once(("variant", key), lambda: self._variant(path, switches, what, key)))

    def _variant(self, path: str, switches: tuple, what: str, key: str) -> tuple[gamedraw.SStage, ...]:
        """A variant's stages: its record from the build cache (or compiled), each shader read or compiled once."""
        kept = buildcache.get_files(NAMESPACE, key)
        for attempt in range(2):
            if kept is None:
                kept = self._compile_all(path, self.source(path, [*self.m_graphics, *switches]), what)
                buildcache.put_files(NAMESPACE, key, kept)
            record = json.loads(kept["stages.json"])
            self._record(path, what, [key, *(stage_record["shader"] for stage_record in record.get("stages", []))])
            if "error" in record:
                raise CNoGBufferPass(record["error"])
            try:
                shaders = [self._once(("shader", stage_record["shader"]), lambda stage_record=stage_record: self._kept_shader(stage_record["shader"]))
                           for stage_record in record["stages"]]  # fmt: skip
                break
            except _CMissing:  # the cache let a shader of the record go: the variant compiles again
                if attempt:
                    raise
                kept = None
        built = []
        for stage, (blob, reflection) in zip(record["stages"], shaders, strict=True):
            inputs = [gameshader.SInput(*input_fields) for input_fields in stage["inputs"]]
            built.append(
                gamedraw.SStage(blob, stage["entry"], reflection, inputs, stage["types"], record.get("state", {}))
            )
        return tuple(built)

    def _record(self, path: str, what: str, keys: list[str]) -> None:
        """The entries a variant wrote or read, in the manifest of its phase and game build (``render.shaderclean``:
        what a phase drawn natively no longer needs can go)."""
        from ..render import shaderclean
        from . import shadernative

        if getattr(self, "_m_build", None) is None:
            try:
                self._m_build = shadernative.build_id(self.m_game) if getattr(self.m_game, "m_prefix", None) else ""
            except Exception:  # pattern: ok (a damaged shipped cache of any kind: no manifest, nothing offered later)
                self._m_build = ""
        shaderclean.record(self._m_build, shaderclean.phase_of(path, what), keys)

    def stages(self, stem: str, values: dict[str, str], role: str = "gbuffer"):
        """The vertex and pixel shader of the effect ``stem``'s technique for ``role`` (``TECHNIQUES``) for a
        material's ``values``, and the material references the effect reads; ``CNoGBufferPass`` without one."""
        effect = self.m_game.read_info(stem)
        if effect is None:
            raise FileNotFoundError(f"{shadercache.FX_DIR}{stem}.fx")
        switches = shadercache.material_switches(effect.references, self.m_game.m_materials, values)
        vs, ps = self.pass_stages(f"{shadercache.FX_DIR}{stem}.fx", switches, role)
        return vs, ps, effect.references

    def draw_stages(self, stem: str, values: dict[str, str]) -> SDrawStages:
        """A material's stages: its effect's G-buffer technique, else its transparent one, its shadow technique
        (None for an effect without one) and, for a G-buffer pass that draws only where its depth lies already (a
        tree's leaves), its depth prepass; ``CNoGBufferPass`` when it has neither of the first two."""
        try:
            vs, ps, refs = self.stages(stem, values)
            transparent = False
        except CNoGBufferPass:  # no G-buffer pass: the transparent pass draws it (or our look)
            vs, ps, refs = self.stages(stem, values, "transparent")
            transparent = True
        try:
            shadow = self.stages(stem, values, "shadow")[:2]
        except CNoGBufferPass:
            shadow = None  # an effect without a shadow technique casts none
        prepass = None
        if not transparent and ps.state.get("zfunc", "").upper() == "EQUAL":
            try:
                prepass = self.stages(stem, values, "prepass")[:2]
            except CNoGBufferPass:
                prepass = None  # drawn as a plain G-buffer pass: its cut-outs fill
        return SDrawStages(vs, ps, refs, shadow, transparent, prepass)

    def material(self, refs, values: dict[str, str], files=None) -> gamedraw.SMaterial:
        """The structures of the descriptions the shader reads (defaults with the config over them) and the maps,
        read from ``files`` (a mod's over the game's) when given, else from the install."""
        fields, maps, paths = {}, {}, {}
        maps_from = files if files is not None else self.m_files
        for ref, _opts in refs:
            path = f"{shadercache.MATERIAL_DIR}{_fxm_file(ref)}"
            if not self.m_files.exists(path):
                continue
            text = self.m_files.read(path).decode("utf-8", "replace")
            fields[f"{ref}Struct"] = {
                field_name: config_value(field_name, default, values)
                for field_name, default in material_defaults(text).items()
            }
            for kind, name, body in _FIELD.findall(text):
                if not kind.startswith("Texture"):
                    continue
                written = values.get(name) or _texture_default(body)  # the description's default when none is set
                texture = fileresolve.texture_file(written, maps_from.exists) if written else ""
                if texture:
                    try:
                        maps[name] = ddsdecode.CDdsFile(maps_from.read(texture))
                    except ddsdecode.CUnsupportedDds:
                        continue
                    paths[name] = texture
        return gamedraw.SMaterial(fields, maps, paths)

    def pass_stage(self, path: str, technique: str, function: str) -> gamedraw.SStage:
        """One pass shader of an effect no material picks, with the switches of a shipped variant."""
        return self.pass_stages(path, self.shipped_switches(path), f"{technique}/{function}")[0]

    def shading(self) -> tuple[gamedraw.SStage, gamelight.STables]:
        """The deferred shading's compute shader and the engine's tables it reads (float RGBA, half precision)."""

        def level(path: str, mip: int = 0):
            return ddsdecode.CDdsFile(self.m_files.read(path)).level_float(mip).astype("f2")

        brdf = ddsdecode.CDdsFile(self.m_files.read(TABLES["brdf"]))
        tables = gamelight.STables([brdf.level_float(mip_level).astype("f2") for mip_level in range(brdf.m_info.mips)],
                                  [level(TABLES["ltc0"]), level(TABLES["ltc1"])], level(TABLES["noise"]))  # fmt: skip
        return self.pass_stage(*SHADING), tables

    def stage_set(self, stages: dict[str, tuple[str, str, str]]) -> dict[str, gamedraw.SStage]:
        """The compiled shaders of a render module's ``STAGES`` (key -> effect, technique, function), by key."""
        return {key: self.pass_stage(*spec) for key, spec in stages.items()}

    def own_stage(self, hlsl: str, entry: str, profile: str = "cs_6_2") -> gamedraw.SStage:
        """A shader of ours (a step the view needs that no game shader does), compiled once by the game's compiler
        and kept in the build cache."""
        key = self._compiled(hlsl, shaderdefines.compile_args(entry, profile), entry)
        blob, reflection = self._once(("shader", key), lambda: self._kept_shader(key))
        return gamedraw.SStage(blob, entry, reflection, [], gameshader.declared_types(hlsl))

    def shadow_fit(self) -> dict[str, gamedraw.SStage]:
        """The compute shaders of the sun's cascade analysis (``shadowfit.STAGES``)."""
        return self.stage_set(shadowfit.STAGES)

    def ambient_text(self, ambient: str = "amb_roman_01") -> str:
        """The text of an ambient file (``gamesky.AMBIENT``): the sky's, the clouds' and the water's values."""
        return self.m_files.read(gamesky.AMBIENT.format(ambient)).decode("utf-8", "replace")

    def sky_passes(self, ambient: str = "amb_roman_01") -> tuple[dict[str, gamedraw.SStage], dict]:
        """The compute shaders of the sky's light (``gamesky.STAGES``), and the ambient's atmosphere
        (``gamesky.read_atmosphere``)."""
        return self.stage_set(gamesky.STAGES), gamesky.read_atmosphere(self.ambient_text(ambient))

    def float_maps(self, paths: dict[str, str]) -> dict:
        """Level 0 of each named DDS file (name -> path), as float RGBA arrays (the engine's global maps)."""
        return {name: ddsdecode.CDdsFile(self.m_files.read(path)).level_float(0) for name, path in paths.items()}

    def post_stages(self) -> dict[str, gamedraw.SStage]:
        """The vertex and pixel shader of each post effect (``gamepost.STAGES``)."""
        return self.stage_set(gamepost.STAGES)

    def shipped_switches(self, effect: str) -> shadercache.Switches:
        """The material switches of the first shipped variant of an effect: for a shader no material picks, they
        carry the constants the engine passes (``DEFERRED_SHADING_TILE_SIZE_X`` = 16 ...)."""
        info = self.m_game.read_info(Path(effect).stem)
        paths = self.m_game.m_by_file.get(info.vanilla_hash, []) if info else []
        return self.m_game.switches(sorted(paths)[0])[1] if paths else ()


_g_shared: dict[tuple, CShaderSource] = {}
_shared_lock = threading.Lock()
SHARED = 2  # sources a session keeps (an install's, and a second one's while it switches) [ours]


def shared(files, compiler: dxc.CDxc, frame_instances: bool = False) -> CShaderSource:
    """The session's source for an install's files, compiler and kind of draw: another island or another rendering
    quality finds what the first compiled or read already (the read texts, the variants, the shaders). An install's
    tree is known by its folder: the start-up stage's tree and the view's share one source."""
    folder = getattr(files, "m_folder", None) or getattr(files, "folder", None)  # a CGameTree or a wrapper
    # a source without a folder is known by its files' id, and the entry's ``files`` is compared as well
    where = os.path.normcase(os.path.abspath(folder)) if folder is not None else id(files)
    key = (where, str(getattr(compiler, "m_folder", "")), frame_instances)
    with _shared_lock:
        found = _g_shared.get(key)
        if found is None or (folder is None and found.m_files.m_files is not files):
            while len(_g_shared) >= SHARED:
                del _g_shared[next(iter(_g_shared))]
            found = _g_shared[key] = CShaderSource(files, compiler, frame_instances)
        return found


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


def load(source: CShaderSource, cfg: str) -> SGameDraw:
    """The first material of the config ``cfg`` whose effect has a G-buffer pass, with its mesh's group."""
    text = source.m_files.read(cfg).decode("utf-8", "replace")
    model = next(config_node for config_node in configtree.read_tree(text).walk() if config_node.kind == "MODEL")
    rdm = rdmread.read_rdm(source.m_files.read(configtree.mesh_file(model)))
    mesh = next(candidate for candidate in rdm.meshes if candidate.vertex_count and candidate.indices)
    for slot, node in configtree.materials_at(model).items():
        values = first_values(node.fields)
        stem = effect_of(number(values.get("ShaderID"), -1))
        if stem is None:
            continue
        try:
            vs, ps, refs = source.stages(stem, values)
        except CNoGBufferPass:
            continue
        indices = rdmglb.material_indices(mesh, slot) or mesh.indices
        return SGameDraw(vs, ps, gamedraw.CMesh.of_rdm(mesh, indices), source.material(refs, values))
    raise ValueError(f"{cfg}: no material with a G-buffer pass")
