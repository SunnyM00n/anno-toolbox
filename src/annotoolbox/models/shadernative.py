"""The game's compiled shaders as the native backend binds them: a variant's shipped cache file found by the name the
engine gives it, and for a variant the game does not ship (or ships compiled from another revision of its source) the
fallback, compiled from the unmodified source with the engine's defines and arguments by the game's compiler, kept in
the build cache per game build. No rewrite: what comes back binds like a shipped shader.

``CNativeShaders(files, compiler)`` is the entry: ``variant(stem, ...)`` names a variant, ``shaders(variant)`` gives its
compiled shaders by entry function (``shipped`` says which way), ``build_id(game)`` the key a game update changes.
The names and the rules: docs/rendering/shader-compile.md section 5, docs/rendering/native-backend.md section 2.
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import Future
from dataclasses import dataclass

from ..formats import fxeffect, fxlink
from ..infra import buildcache
from . import shadercache, shaderdefines

NAMESPACE = "game-shaders-native"  # the fallback compiles, by their would-be cache file name and the game build
VERSION = 1  # bump when what a fallback record holds changes


@dataclass(frozen=True)
class SVariant:
    """One variant of an effect: its file, the mesh's vertex format ("" for an effect without one) and the material
    switches, for the source's graphics set."""

    stem: str
    vertex_format: str = ""
    material: shadercache.Switches = ()

    @property
    def path(self) -> str:
        return f"{shadercache.FX_DIR}{self.stem}.fx"


@dataclass(frozen=True)
class STechniqueCalls:
    """One technique of an effect: its shaders by pass (stage, function, the literal arguments joined without
    spaces, as ``CNativeShaders.call`` takes them) and its first pass's render states (``fxeffect.SEffect.state_of``)."""

    name: str
    calls: tuple
    state: dict


def build_id(game: shadercache.CGameShaders) -> str:
    """What identifies the install's shaders: the shipped names' shared prefix and every file hash (a game update that
    touches a shader changes them)."""
    return buildcache.digest(VERSION, *(game.m_prefix or (0, 0)), *sorted(game.m_by_file))


def install_build(files) -> str:
    """``build_id`` of an install's tree (a game tree or the files ``CGameShaders`` reads)."""
    from . import shaderview

    tree = files if hasattr(files, "iter_paths") else shaderview._CFiles(files)
    return build_id(shadercache.CGameShaders(tree))


class CNativeShaders:
    """The install's compiled shaders by variant: shipped, else compiled once from the source and kept."""

    def __init__(
        self, files, compiler, game: shadercache.CGameShaders | None = None, graphics=None, caches: tuple = ()
    ) -> None:
        """``caches``: trees (``read``, ``exists``) whose cache files count as shipped after the install's: a mod's
        precompiled variants (``shaderprebuild``), found by the same name."""
        from . import shaderview  # the view's graphics set: what it feeds

        # a game tree lists its files by ``under``: read as CGameShaders reads one
        self.m_files = files if hasattr(files, "iter_paths") else shaderview._CFiles(files)
        files = self.m_files
        self.m_caches = (self.m_files, *caches)
        self.m_compiler = compiler
        self.m_game = game or shadercache.CGameShaders(files)
        self.m_graphics = graphics if graphics is not None else shadercache.view_graphics_set(self.m_game)
        self.m_build = build_id(self.m_game)
        self.m_materials = shadercache.material_fields(None, files)
        self._m_read = shadercache.overlay_reader(None, files)
        self._m_lock = threading.Lock()
        self._m_done: dict[str, dict[str, shadercache.SCachedShader]] = {}
        self._m_reflected: dict[str, object] = {}  # a shader's digest -> its reflection (``reflection``)
        self._m_techniques: dict = {}  # an effect's stem (and a variant's switches) -> its techniques (``techniques``)
        self._m_bodies: dict[str, str] = {}  # an effect's path -> its text with its includes (``source``)

    def variant(self, stem: str, vertex_format: str = "", material: shadercache.Switches | None = None) -> SVariant:
        """A variant; without ``material`` the switches of the effect's first shipped variant (an effect no material
        picks: they carry the constants the engine passes, ``ShaderSource.shipped_switches``)."""
        if material is None:  # the first file's alone: reading every file's switches took a minute on a cold start
            effect_info = self.m_game.read_info(stem)
            by_file = self.m_game.m_by_file
            paths = by_file.get(effect_info.vanilla_hash if effect_info else 0) or by_file.get(
                self.m_game.stale().get(stem, 0), []
            )
            material = self.m_game.switches(sorted(paths)[0])[1] if paths else ()
        return SVariant(stem, vertex_format, tuple(material))

    def cache_name(self, variant: SVariant) -> str:
        """The cache file the engine names the variant by (the file hash is the installed source's, so a stale
        file's variant gets a name the shipped cache does not hold)."""
        effect_info = self.m_game.read_info(variant.stem)
        if effect_info is None or self.m_game.m_prefix is None:
            raise FileNotFoundError(variant.path)
        variant_digest = shadercache.variant_hash(
            variant.stem, variant.vertex_format, self.m_graphics, variant.material
        )
        return shadercache.CACHE_DIR + shadercache.cache_name(
            (*self.m_game.m_prefix, effect_info.vanilla_hash, variant_digest)
        )

    def holder(self, name: str):
        """The tree that has the cache file ``name`` (the install's first, then ``caches``), None when none has."""
        return next((tree for tree in self.m_caches if tree.exists(name)), None)

    def shipped(self, variant: SVariant) -> bool:
        return self.holder(self.cache_name(variant)) is not None

    def shaders(self, variant: SVariant, only: str = "") -> dict[str, shadercache.SCachedShader]:
        """The variant's compiled shaders by entry function (a function a second technique calls again with other
        arguments: ``<function>#1``), shipped or compiled; made once a session. ``only``: a compiled variant holds
        just the techniques this pattern finds (``fallback``; a shipped one holds all)."""
        name = self.cache_name(variant)
        holder = self.holder(name)
        shipped = holder is not None

        def make() -> dict[str, shadercache.SCachedShader]:
            if shipped:
                return _by_function(shadercache.read_cached_shaders(holder.read(name)))
            return self.fallback(variant, only)

        return self._once(self._m_done, name if shipped or not only else f"{name}|{only}", make)

    def _once(self, table: dict, key: str, make):
        """``table[key]``, made by ``make()`` once for every thread that asks at the same time (the others wait: an
        island's parts of one effect or variant come together); a failure is not kept, asked again it runs again."""
        with self._m_lock:
            found = table.get(key)
            mine = found is None
            if mine:
                found = table[key] = Future()
        if not mine:
            return found.result() if isinstance(found, Future) else found
        try:
            made = make()
        except BaseException as exc:
            with self._m_lock:
                del table[key]
            found.set_exception(exc)
            raise
        with self._m_lock:
            table[key] = made
        found.set_result(made)
        return made

    def call(self, variant: SVariant, function: str, arguments: str = "", only: str = "") -> shadercache.SCachedShader:
        """A variant's shader by its entry function and the literal arguments its technique passes, as the cache file
        stores them (joined, without spaces: ``vsTerrain`` with ``"2true"``); ``only`` as ``shaders`` takes it."""
        for shader in self.shaders(variant, only).values():
            if shader.name == function and "".join(shader.arguments).replace(" ", "") == arguments:
                return shader
        raise KeyError(f"{variant.stem}: no {function}({arguments})")

    def techniques(self, stem: str, variant: SVariant | None = None) -> dict[str, STechniqueCalls]:
        """An effect's techniques by name: each pass shader's (stage, function, literal arguments as ``call`` takes
        them) and the first pass's render states, read from the preprocessed text of its first shipped variant (or of
        ``variant``: a variant's own switches change its render states, a transparent part's blend among them); once per
        game build (kept in the build cache: a preprocess takes a second or more), and once for every thread that
        asks at the same time (``_once``)."""
        key = stem if variant is None else (stem, tuple(variant.material))
        return self._once(self._m_techniques, key, lambda: self._read_techniques(stem, variant))

    def _read_techniques(self, stem: str, variant: SVariant | None = None) -> dict[str, STechniqueCalls]:
        own = (
            ()
            if variant is None
            else ("variant", *(f"{switch}={switch_value}" for switch, switch_value in variant.material))
        )
        key = buildcache.digest(VERSION, self.m_build, self.m_compiler.version(), "techniques", stem, *own)
        kept = buildcache.get(NAMESPACE, key)
        if kept is None:
            variant = variant if variant is not None else self.variant(stem)
            compiler = getattr(self.m_compiler, "for_thread", lambda: self.m_compiler)()
            text, messages = compiler.preprocess(self.source(variant.path, [*self.m_graphics, *variant.material]), ["-HV", "2021"],
                                                 name=variant.path)  # fmt: skip
            if not text:
                raise RuntimeError(f"{stem}: {messages[:2000]}")
            effect = fxeffect.split_effect(text)
            listed = {technique.name: {"calls": [[call.stage, call.function, "".join(call.args).replace(" ", "")] for call in technique.calls],
                               "state": effect.state_of(technique)} for technique in effect.techniques}  # fmt: skip
            kept = json.dumps(listed).encode("utf-8")
            buildcache.put(NAMESPACE, key, kept)
        return {name: STechniqueCalls(name, tuple(tuple(call) for call in technique["calls"]), technique["state"])
                for name, technique in json.loads(kept).items()}  # fmt: skip

    def entry(self, variant: SVariant, function: str, pattern=None) -> shadercache.SCachedShader:
        """``variant``'s shader of the entry ``function``: the one of that name, or of several alike the first; where they
        differ, the one whose literal arguments the first technique matching ``pattern`` (a compiled regex,
        ``shaderview.TECHNIQUES``) passes it (``techniques``: a preprocess once per effect). ``KeyError`` when none
        is found."""
        found = [shader for shader in self.shaders(variant).values() if shader.name == function]
        if not found:
            raise KeyError(f"{variant.stem}: no {function}")
        if len({shader.bytecode for shader in found}) == 1:
            return found[0]
        for technique in self.techniques(variant.stem).values() if pattern is not None else ():
            arguments = next((call[2] for call in technique.calls if call[1] == function), None)
            if pattern.search(technique.name) and arguments is not None:
                return self.call(variant, function, arguments)
        raise KeyError(f"{variant.stem}: {function} is called with other arguments by several techniques")

    def reflection(self, shader: shadercache.SCachedShader):
        """A shader's reflection by the game's compiler, made once a session."""
        key = buildcache.digest(shader.bytecode)
        found = self._m_reflected.get(key)
        if found is None:
            compiler = getattr(self.m_compiler, "for_thread", lambda: self.m_compiler)()
            found = self._m_reflected.setdefault(key, compiler.reflect(shader.bytecode))
        return found

    def fallback_key(self, variant: SVariant, only: str = "") -> str:
        return buildcache.digest(
            VERSION, self.m_build, self.m_compiler.version(), self.cache_name(variant), *((only,) * bool(only))
        )

    def fallback_cached(self, variant: SVariant) -> bool:
        return self.shipped(variant) or buildcache.get(NAMESPACE, self.fallback_key(variant)) is not None

    def source(self, path: str, defines: list) -> str:
        """An effect's text with its includes as ``fxeffect.effect_source`` makes it, read once per file: a variant's
        defines go in front of it (``shaderview.CShaderSource.source``, which the current path compiles from)."""
        body = self._once(
            self._m_bodies, path, lambda: fxeffect.effect_source(self._m_read, path, [], self.m_materials)
        )
        head = '#line 1 "<defines>"' + chr(10)
        return body.replace(head, head + fxeffect.define_lines(defines), 1)

    def fallback(self, variant: SVariant, only: str = "") -> dict[str, shadercache.SCachedShader]:
        """The variant compiled from the unmodified source as the engine compiles it (shader-compile.md 4: the
        engine's defines, the pass's arguments, DXC's options), kept in the build cache under its would-be cache
        file name and the game build; ``only``: just the techniques whose names this pattern finds (an island's
        objects need three of a model effect's many). A pass shader several techniques share is compiled once."""
        key = self.fallback_key(variant, only)
        kept = buildcache.get(NAMESPACE, key)
        if kept is not None:
            try:
                return _by_function(_unpack(kept))
            except (ValueError, KeyError, TypeError):  # a cut or damaged record is compiled again
                pass
        compiler = getattr(self.m_compiler, "for_thread", lambda: self.m_compiler)()
        text, messages = compiler.preprocess(self.source(variant.path, [*self.m_graphics, *variant.material]), ["-HV", "2021"],
                                             name=variant.path)  # fmt: skip
        if not text:
            raise RuntimeError(f"{variant.stem}: {messages[:2000]}")
        effect = fxeffect.split_effect(text)
        program = fxlink.CProgram(effect.hlsl)
        out, made = [], {}
        for technique, call in fxeffect.shader_calls(effect):
            if only and not re.search(only, technique.name):
                continue
            hlsl, entry = program.entry_source(call, technique.passthrough, program.bindings(technique.functions))
            arguments = shaderdefines.compile_args(entry, call.profile)
            same = buildcache.digest(hlsl, *arguments)
            if same not in made:
                done = compiler.compile(hlsl, arguments, name=variant.path)
                if not done.ok:
                    raise RuntimeError(f"{variant.stem} {technique.name}/{call.function}: {done.messages[:2000]}")
                made[same] = done.object
            out.append(shadercache.SCachedShader(call.function, tuple(call.args), made[same]))
        buildcache.put(NAMESPACE, key, _pack(out))
        return _by_function(out)


def _by_function(shaders: list[shadercache.SCachedShader]) -> dict[str, shadercache.SCachedShader]:
    out: dict[str, shadercache.SCachedShader] = {}
    for shader in shaders:
        name, copy_number = shader.name, 0
        while name in out:
            copy_number += 1
            name = f"{shader.name}#{copy_number}"
        out[name] = shader
    return out


def _pack(shaders: list[shadercache.SCachedShader]) -> bytes:
    index = [{"name": shader.name, "args": list(shader.arguments), "size": len(shader.bytecode)} for shader in shaders]
    head = json.dumps(index).encode("utf-8")
    return len(head).to_bytes(4, "little") + head + b"".join(shader.bytecode for shader in shaders)


def _unpack(blob: bytes) -> list[shadercache.SCachedShader]:
    """The shaders ``_pack`` wrote; ``ValueError`` for a record that is cut or does not hold what it lists."""
    index_bytes = int.from_bytes(blob[:4], "little")
    offset, out = 4 + index_bytes, []
    for item in json.loads(blob[4 : 4 + index_bytes]):
        end = offset + item["size"]
        if end > len(blob):
            raise ValueError("a fallback record is cut")
        out.append(shadercache.SCachedShader(item["name"], tuple(item["args"]), blob[offset:end]))
        offset = end
    return out
