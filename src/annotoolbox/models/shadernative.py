"""The game's compiled shaders as the native backend binds them: a variant's shipped cache file found by the name the
engine gives it, and for a variant the game does not ship (or ships compiled from another revision of its source) the
fallback, compiled from the unmodified source with the engine's defines and arguments by the game's compiler, kept in
the build cache per game build. No rewrite: what comes back binds like a shipped shader.

``NativeShaders(files, compiler)`` is the entry: ``variant(stem, ...)`` names a variant, ``shaders(variant)`` gives its
compiled shaders by entry function (``shipped`` says which way), ``build_id(game)`` the key a game update changes.
The names and the rules: docs/rendering/shader-compile.md section 5, docs/rendering/native-backend.md section 2.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from ..formats import fxeffect, fxlink
from ..infra import buildcache
from . import shadercache, shaderdefines

NAMESPACE = "game-shaders-native"  # the fallback compiles, by their would-be cache file name and the game build
VERSION = 1  # bump when what a fallback record holds changes


@dataclass(frozen=True)
class Variant:
    """One variant of an effect: its file, the mesh's vertex format ("" for an effect without one) and the material
    switches, for the source's graphics set."""

    stem: str
    vertex_format: str = ""
    material: shadercache.Switches = ()

    @property
    def path(self) -> str:
        return f"{shadercache.FX_DIR}{self.stem}.fx"


def build_id(game: shadercache.GameShaders) -> str:
    """What identifies the install's shaders: the shipped names' shared prefix and every file hash (a game update that
    touches a shader changes them)."""
    return buildcache.digest(VERSION, *(game.prefix or (0, 0)), *sorted(game.by_file))


def install_build(files) -> str:
    """``build_id`` of an install's tree (a game tree or the files ``GameShaders`` reads)."""
    from . import shaderview

    tree = files if hasattr(files, "iter_paths") else shaderview._Files(files)
    return build_id(shadercache.GameShaders(tree))


class NativeShaders:
    """The install's compiled shaders by variant: shipped, else compiled once from the source and kept."""

    def __init__(self, files, compiler, game: shadercache.GameShaders | None = None, graphics=None) -> None:
        from . import shaderview  # the view's graphics set: what it feeds

        # a game tree lists its files by ``under``: read as GameShaders reads one
        self.files = files if hasattr(files, "iter_paths") else shaderview._Files(files)
        files = self.files
        self.compiler = compiler
        self.game = game or shadercache.GameShaders(files)
        self.graphics = graphics if graphics is not None else shaderview.graphics_set(self.game)
        self.build = build_id(self.game)
        self.materials = shadercache.material_fields(None, files)
        self._read = shadercache.overlay_reader(None, files)
        self._lock = threading.Lock()
        self._done: dict[str, dict[str, shadercache.CachedShader]] = {}
        self._reflected: dict[str, object] = {}  # a shader's digest -> its reflection (``reflection``)

    def variant(self, stem: str, vertex_format: str = "", material: shadercache.Switches | None = None) -> Variant:
        """A variant; without ``material`` the switches of the effect's first shipped variant (an effect no material
        picks: they carry the constants the engine passes, ``ShaderSource.shipped_switches``)."""
        if material is None:
            info = self.game.info(stem)
            shipped = self.game.shipped_of(stem, info.vanilla_hash if info else 0)
            material = self.game.switches(sorted(p for p, _g, _m in shipped)[0])[1] if shipped else ()
        return Variant(stem, vertex_format, tuple(material))

    def cache_name(self, v: Variant) -> str:
        """The cache file the engine names the variant by (the file hash is the installed source's, so a stale
        file's variant gets a name the shipped cache does not hold)."""
        info = self.game.info(v.stem)
        if info is None or self.game.prefix is None:
            raise FileNotFoundError(v.path)
        h = shadercache.variant_hash(v.stem, v.vertex_format, self.graphics, v.material)
        return shadercache.CACHE_DIR + shadercache.cache_name((*self.game.prefix, info.vanilla_hash, h))

    def shipped(self, v: Variant) -> bool:
        return self.files.exists(self.cache_name(v))

    def shaders(self, v: Variant) -> dict[str, shadercache.CachedShader]:
        """The variant's compiled shaders by entry function (a function a second technique calls again with other
        arguments: ``<function>#1``), shipped or compiled; made once a session."""
        name = self.cache_name(v)
        found = self._done.get(name)
        if found is None:
            if self.files.exists(name):
                found = _by_function(shadercache.read_cached_shaders(self.files.read(name)))
            else:
                found = self.fallback(v)
            with self._lock:
                found = self._done.setdefault(name, found)
        return found

    def call(self, v: Variant, function: str, args: str = "") -> shadercache.CachedShader:
        """A variant's shader by its entry function and the literal arguments its technique passes, as the cache file
        stores them (joined, without spaces: ``vsTerrain`` with ``"2true"``)."""
        for s in self.shaders(v).values():
            if s.name == function and "".join(s.args).replace(" ", "") == args:
                return s
        raise KeyError(f"{v.stem}: no {function}({args})")

    def reflection(self, shader: shadercache.CachedShader):
        """A shader's reflection by the game's compiler, made once a session."""
        key = buildcache.digest(shader.bytecode)
        found = self._reflected.get(key)
        if found is None:
            compiler = getattr(self.compiler, "for_thread", lambda: self.compiler)()
            found = self._reflected.setdefault(key, compiler.reflect(shader.bytecode))
        return found

    def fallback_key(self, v: Variant) -> str:
        return buildcache.digest(VERSION, self.build, self.compiler.version(), self.cache_name(v))

    def fallback_cached(self, v: Variant) -> bool:
        return self.files.exists(self.cache_name(v)) or buildcache.get(NAMESPACE, self.fallback_key(v)) is not None

    def fallback(self, v: Variant) -> dict[str, shadercache.CachedShader]:
        """The variant compiled from the unmodified source as the engine compiles it (shader-compile.md 4: the
        engine's defines, the pass's arguments, DXC's options), kept in the build cache under its would-be cache
        file name and the game build."""
        key = self.fallback_key(v)
        kept = buildcache.get(NAMESPACE, key)
        if kept is not None:
            return _by_function(_unpack(kept))
        compiler = getattr(self.compiler, "for_thread", lambda: self.compiler)()
        source = fxeffect.effect_source(self._read, v.path, [*self.graphics, *v.material], self.materials)
        text, messages = compiler.preprocess(source, ["-HV", "2021"], name=v.path)
        if not text:
            raise RuntimeError(f"{v.stem}: {messages[:2000]}")
        effect = fxeffect.split_effect(text)
        program = fxlink.Program(effect.hlsl)
        out = []
        for tech, call in fxeffect.shader_calls(effect):
            hlsl, entry = program.entry_source(call, tech.passthrough, program.bindings(tech.functions))
            done = compiler.compile(hlsl, shaderdefines.compile_args(entry, call.profile), name=v.path)
            if not done.ok:
                raise RuntimeError(f"{v.stem} {tech.name}/{call.function}: {done.messages[:2000]}")
            out.append(shadercache.CachedShader(call.function, tuple(call.args), done.object))
        buildcache.put(NAMESPACE, key, _pack(out))
        return _by_function(out)


def _by_function(shaders: list[shadercache.CachedShader]) -> dict[str, shadercache.CachedShader]:
    out: dict[str, shadercache.CachedShader] = {}
    for s in shaders:
        name, n = s.name, 0
        while name in out:
            n += 1
            name = f"{s.name}#{n}"
        out[name] = s
    return out


def _pack(shaders: list[shadercache.CachedShader]) -> bytes:
    index = [{"name": s.name, "args": list(s.args), "size": len(s.bytecode)} for s in shaders]
    head = json.dumps(index).encode("utf-8")
    return len(head).to_bytes(4, "little") + head + b"".join(s.bytecode for s in shaders)


def _unpack(blob: bytes) -> list[shadercache.CachedShader]:
    size = int.from_bytes(blob[:4], "little")
    at, out = 4 + size, []
    for item in json.loads(blob[4 : 4 + size]):
        out.append(shadercache.CachedShader(item["name"], tuple(item["args"]), blob[at : at + item["size"]]))
        at += item["size"]
    return out
