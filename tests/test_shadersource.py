"""The game shaders' compiles kept (``models.shaderview.ShaderSource``): the build cache's keys hold nothing of the
machine or the run, two variants whose shader comes out as the same text compile it once, a variant asked for by
many threads at once compiles once, a later start reads what the first compiled (the bytecode and its reflection,
equal to a fresh compile's); with an installed Anno 117 (``ANNO117_DIR``) the same for the game's own compiler."""

from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from annotoolbox.infra import buildcache, dxc

pytest.importorskip("PySide6")
from annotoolbox.models import shaderview

FX = "data/shaders/fx/demo.fx"
EFFECT = b"""
float4 Shared() { return float4(1, 2, 3, 4); }
void vsMain(out float4 p : SV_Position) { p = Shared(); }
float4 psMain() : SV_Target0 { return Shared() * TINT; }
technique ModelDeferred { pass p0 { SetVertexShader(vsMain()); SetPixelShader(psMain()); } }
"""


def _reflection(blob: bytes) -> dxc.Reflection:
    member = dxc.Variable("Inner", 16, 0, "float4", 1, 4)
    cbuffer = dxc.ConstantBuffer("cbDemo", 64, [dxc.Variable("cTint", 0, 32, "Demo", members=[member])])
    return dxc.Reflection(0x10062, [dxc.Binding("cbDemo", "cbuffer", 0, 1, 0)], [cbuffer],
                          [("SV_Position", 0, 0)], [(blob[4:8].hex(), 0, 0)])  # fmt: skip


class Compiler:
    """DXC's calls on a text: the preprocessor's object-like defines put in and its lines dropped, a compile as the
    text's hash, a reflection made of it; it counts the compiles."""

    def __init__(self, version: str = "1.8.2405.17") -> None:
        self._version, self.compiles, self.lock = version, [], threading.Lock()

    def version(self) -> str:
        return self._version

    def for_thread(self) -> Compiler:
        return self

    def preprocess(self, source: str, args, name: str = "") -> tuple[str, str]:
        defines = dict(re.findall(r"(?m)^#define (\w+) (\S+)$", source))
        text = "\n".join("" if line.startswith("#") else line for line in source.splitlines())
        return re.sub(r"\b(" + "|".join(map(re.escape, defines)) + r")\b", lambda m: defines[m.group(1)], text), ""

    def compile(self, hlsl: str, args, name: str = "") -> dxc.Compiled:
        with self.lock:
            self.compiles.append(args[args.index("-E") + 1])
        return dxc.Compiled(True, b"DXBC" + hashlib.sha256((hlsl + repr(args)).encode()).digest(), "")

    def reflect(self, blob: bytes) -> dxc.Reflection:
        return _reflection(blob)


class Files:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def read(self, path: str) -> bytes:
        return self.files[path]

    def exists(self, path: str) -> bool:
        return path in self.files

    def iter_paths(self, prefix: str):
        return [p for p in self.files if p.startswith(prefix)]


def _source(compiler: Compiler, effect: bytes = EFFECT) -> shaderview.ShaderSource:
    """A source over one effect file, without the install's graphics sets and descriptions."""
    source = object.__new__(shaderview.ShaderSource)
    files = Files({FX: effect})
    source.files, source.compiler, source.frame_instances = shaderview._Files(files), compiler, False
    source.graphics, source.descriptions, source._install = (("SHADER_AA_SAMPLES", "1"),), [], files.files.get
    source._texts, source._bodies, source._digests, source._memo = {}, {}, {}, {}
    source._lock, source._serial = threading.Lock(), threading.Lock()
    return source


def test_the_keys_hold_what_decides_the_compile_and_nothing_of_the_machine_or_the_run(tmp_path, monkeypatch):
    a, b = _source(Compiler()), _source(Compiler())
    key = a._key(FX, (("TINT", "2"),), "gbuffer")
    monkeypatch.setenv("ANNO2070_CACHE", str(tmp_path / "elsewhere"))  # another cache folder, another source
    assert b._key(FX, (("TINT", "2"),), "gbuffer") == key
    assert a.shader_key("float4 x;", ["-E", "x"], FX) == b.shader_key("float4 x;", ["-E", "x"], FX)
    assert a._key(FX, (("TINT", "3"),), "gbuffer") != key  # the defines
    assert a._key(FX, (("TINT", "2"),), "shadow") != key  # the pass shaders
    assert _source(Compiler("1.8.2502.8"))._key(FX, (("TINT", "2"),), "gbuffer") != key  # the compiler
    assert _source(Compiler(), EFFECT + b"\n// changed\n")._key(FX, (("TINT", "2"),), "gbuffer") != key  # the text
    assert re.fullmatch(r"[0-9a-f]{64}", key) and str(tmp_path) not in key


def test_a_shader_two_variants_share_compiles_once_and_a_later_start_compiles_nothing():
    compiler = Compiler()
    source = _source(compiler)
    first = source.pass_stages(FX, (("TINT", "2"),), "gbuffer")
    second = source.pass_stages(FX, (("TINT", "3"),), "gbuffer")
    assert sorted(compiler.compiles) == ["psMain", "psMain", "vsMain"]  # the vertex shader's text is the same
    assert first[0].blob == second[0].blob and first[1].blob != second[1].blob
    assert source.pass_stages(FX, (("TINT", "2"),), "gbuffer") == first and len(compiler.compiles) == 3
    later = Compiler()
    again = _source(later).pass_stages(FX, (("TINT", "2"),), "gbuffer")  # a new run: read from the build cache
    assert later.compiles == []
    assert [s.blob for s in again] == [s.blob for s in first]
    assert [s.reflection for s in again] == [_reflection(s.blob) for s in first]  # the kept reflection is the fresh


def test_a_variant_many_threads_ask_for_at_once_compiles_once():
    compiler = Compiler()
    source = _source(compiler)
    with ThreadPoolExecutor(8) as pool:
        got = list(pool.map(lambda _: source.pass_stages(FX, (("TINT", "2"),), "gbuffer"), range(16)))
    assert sorted(compiler.compiles) == ["psMain", "vsMain"]
    assert all(g == got[0] for g in got)


def test_a_variant_whose_shaders_the_cache_let_go_compiles_again():
    compiler = Compiler()
    first = _source(compiler).pass_stages(FX, (("TINT", "2"),), "gbuffer")
    for entry in (buildcache.root() / shaderview.NAMESPACE).rglob("*"):
        if entry.is_file() and "dxil" in buildcache.unpack(entry.read_bytes()):
            entry.unlink()  # the shaders go, the variant's record stays
    again = _source(compiler).pass_stages(FX, (("TINT", "2"),), "gbuffer")
    assert [s.blob for s in again] == [s.blob for s in first] and len(compiler.compiles) == 4


def test_a_reflection_kept_as_json_reads_back_equal():
    refl = _reflection(b"DXBC1234")
    assert dxc.reflection_of_json(dxc.reflection_json(refl)) == refl


def test_the_games_compiler_gives_the_kept_shader_byte_for_byte(monkeypatch):
    """With the install: the terrain's shaders compiled into the build cache, read back by a later start, and
    compiled again without the cache - the same bytecode and the same reflection."""
    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from annotoolbox.render import terraindraw
    from annotoolbox.sources.anno117 import Anno117Files

    files, compiler = Anno117Files(root), dxc.Dxc.find(anno117=root)
    first = shaderview.ShaderSource(files, compiler).stage_set(terraindraw.STAGES)
    kept = shaderview.ShaderSource(files, compiler).stage_set(terraindraw.STAGES)
    monkeypatch.setenv(buildcache.ENV_DISABLE, "1")
    fresh = shaderview.ShaderSource(files, compiler).stage_set(terraindraw.STAGES)
    for key, stage in first.items():
        assert kept[key].blob == fresh[key].blob == stage.blob
        assert kept[key].reflection == fresh[key].reflection == compiler.reflect(stage.blob)
