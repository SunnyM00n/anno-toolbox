"""The native backend's shaders (models/shadernative) and the start-up stage (render/shaderstart): a variant found by
the engine's cache name, the fallback compile kept per game build, and the stage's job list (the variants the game
does not ship) and once-per-build mark. With the game (ANNO117_DIR): the fallback of a shipped variant is byte for byte the game's own DXIL."""

from __future__ import annotations

import contextlib
import os
import threading
import time
from types import SimpleNamespace

import pytest

from annotoolbox.models import shadercache, shadernative

GRAPHICS = (("SHADER_AA_SAMPLES", "1"),)
SWITCHES = (("NUM_BONES_PER_VERTEX", "0"), ("USE_X", ""))


class Game:
    """What ``NativeShaders`` reads of ``shadercache.GameShaders``: one effect with one shipped variant."""

    prefix = (0x11, 0x22)
    by_file = {0xABC: ["data/shaders/cache/windx12/shipped.fxo"]}
    materials: dict = {}

    def info(self, stem):
        return shadercache.ShaderInfo(stem, [], 0xABC) if stem in ("model_x", "stale_y") else None

    def shipped_of(self, stem, file_hash):
        return [(self.by_file[0xABC][0], GRAPHICS, SWITCHES)] if file_hash == 0xABC else []

    def switches(self, path):
        return GRAPHICS, SWITCHES


class Files:
    def __init__(self, names=()):
        self.names = set(names)

    def exists(self, path):
        return path in self.names

    def iter_paths(self, prefix):
        return []

    def read(self, path):
        raise FileNotFoundError(path)


class Compiler:
    def version(self):
        return "dxc-test"


def _shaders(files=()):
    return shadernative.NativeShaders(Files(files), Compiler(), Game(), GRAPHICS)


def test_a_variant_is_named_as_the_engine_names_its_cache_file():
    ns = _shaders()
    v = ns.variant("model_x")
    assert v.material == SWITCHES  # no material given: the first shipped variant's (the engine's constants)
    want = shadercache.variant_hash("model_x", "", GRAPHICS, SWITCHES)
    assert ns.cache_name(v) == shadercache.CACHE_DIR + shadercache.cache_name((0x11, 0x22, 0xABC, want))
    assert not ns.shipped(v)
    assert _shaders([ns.cache_name(v)]).shipped(v)
    with pytest.raises(FileNotFoundError):
        ns.cache_name(shadernative.Variant("nothing"))


def test_a_variant_the_game_does_not_ship_is_compiled_once_and_kept_per_build(monkeypatch):
    ns = _shaders()
    v = ns.variant("model_x", "P4h_T2h", SWITCHES[:1])
    made = []

    def fallback(variant):
        made.append(variant)
        return {"vsMain": shadercache.CachedShader("vsMain", (), b"DXBC")}

    monkeypatch.setattr(ns, "fallback", fallback)
    assert ns.shaders(v)["vsMain"].bytecode == b"DXBC"
    assert ns.shaders(v) and made == [v]  # made once a session
    other = shadernative.NativeShaders(Files(), Compiler(), type("G", (Game,), {"prefix": (0x99, 0x22)})(), GRAPHICS)
    assert other.fallback_key(v) != ns.fallback_key(v)  # another game build: compiled again


def test_a_fallback_record_keeps_its_shaders_in_order():
    shaders = [shadercache.CachedShader("csA", ("1",), b"one"), shadercache.CachedShader("csA", ("2",), b"two"),
               shadercache.CachedShader("psB", (), b"three")]  # fmt: skip
    back = shadernative._unpack(shadernative._pack(shaders))
    assert back == shaders
    assert list(shadernative._by_function(back)) == ["csA", "csA#1", "psB"]


class Natives:
    """What the stage reads of ``shadernative.NativeShaders``: one effect not shipped, the rest shipped."""

    build, graphics, compiler = "build-1", GRAPHICS, Compiler()

    def __init__(self, missing=("terrain_baking",)) -> None:
        self.missing, self.made = missing, []

    def variant(self, stem):
        return shadernative.Variant(stem)

    def shipped(self, v):
        return v.stem not in self.missing

    def fallback(self, v):
        self.made.append(v.stem)
        return {}


def _fake_parts(monkeypatch, shaderstart, model_job, phase_job=lambda: 1):
    """The stage over a stand-in source: the not-shipped variant, two fixed phases (one the native terrain's), two
    model variants."""
    source = SimpleNamespace(compiler=Compiler(), graphics=GRAPHICS, game=Game(), files=Files(),
                             passing=contextlib.nullcontext, stage_set=lambda stages: phase_job(),
                             own_stage=lambda *a: 1)  # fmt: skip
    monkeypatch.setattr(shaderstart.shaderview, "shared", lambda files, compiler, frame_instances: source)
    natives = Natives()
    monkeypatch.setattr(shaderstart.shadernative, "NativeShaders", lambda *a: natives)
    monkeypatch.setattr(shaderstart, "phases", lambda: [shaderstart.Phase("terrain", {}, "terrain"),
                                                        shaderstart.Phase("sky", {})])  # fmt: skip
    monkeypatch.setattr(shaderstart, "phase_jobs", _phase_jobs_without_extras(shaderstart))
    monkeypatch.setattr(shaderstart, "model_variants", lambda g, gr: [("model_pbr", SWITCHES), ("model_x", ())])
    monkeypatch.setattr(shaderstart, "warm_model", model_job)
    return source, natives


def _phase_jobs_without_extras(shaderstart):
    """``phase_jobs`` but the passes whose switches the view picks itself (they need the real source)."""
    real = shaderstart.phase_jobs

    def jobs(source, native=frozenset()):
        return [j for j in real(source, native) if j.label in {p.name for p in shaderstart.phases()}]

    return jobs


def test_the_start_up_screen_runs_what_an_island_needs_first_and_marks_its_build(monkeypatch):
    pytest.importorskip("PySide6")
    from annotoolbox.render import shaderstart

    _source, natives = _fake_parts(monkeypatch, shaderstart, lambda *a: None)
    first, source = shaderstart.plan(Files(), Compiler(), shaderstart.SPLASH)
    assert sorted((j.kind, j.label) for j in first.jobs) == [("native", "terrain_baking (not shipped)"),
                                                            ("phase", "sky"), ("phase", "terrain")]  # fmt: skip
    later, _ = shaderstart.plan(Files(), Compiler(), shaderstart.MODELS)
    assert [j.kind for j in later.jobs] == ["model", "model"] and later.build == shadernative.build_id(Game())
    assert shaderstart.needed(first, source)
    seen = []
    result = shaderstart.run(first, source, lambda done, total, what: seen.append((done, total)), workers=2)
    assert result.done == 3 and not result.failed and seen[-1] == (3, 3) and natives.made == ["terrain_baking"]
    assert not shaderstart.needed(first, source)
    monkeypatch.setenv(shaderstart.ENV, "0")
    assert not shaderstart.enabled()


def test_a_failed_job_leaves_the_build_unmarked(monkeypatch):
    pytest.importorskip("PySide6")
    from annotoolbox.render import shaderstart

    def boom():
        raise RuntimeError("no")

    _fake_parts(monkeypatch, shaderstart, lambda *a: None, phase_job=boom)
    p, source = shaderstart.plan(Files(), Compiler(), shaderstart.SPLASH)
    result = shaderstart.run(p, source, workers=2)
    assert result.failed and result.failed[0].endswith("RuntimeError: no") and shaderstart.needed(p, source)


def test_a_native_phase_drops_out_of_the_qrhi_pre_warm(monkeypatch):
    pytest.importorskip("PySide6")
    from annotoolbox.render import shaderstart

    source, _ = _fake_parts(monkeypatch, shaderstart, lambda *a: None)
    assert [j.label for j in shaderstart.phase_jobs(source, frozenset({"terrain"}))] == ["sky"]
    monkeypatch.setattr(shaderstart, "native_phases", lambda: frozenset({"terrain"}))
    p, _ = shaderstart.plan(Files(), Compiler(), shaderstart.SPLASH)
    assert "terrain" not in [j.label for j in p.jobs]


def test_the_stages_effect_list_is_the_view_fixed_passes():
    pytest.importorskip("PySide6")
    from annotoolbox.render import shaderstart

    assert list(shaderstart.EFFECTS) == shaderstart.fixed_effects()
    assert not shaderstart.native_phases()  # the native terrain still leans on its QRhi compiles (its fallback)


def test_the_model_part_waits_for_an_island_being_opened_and_marks_the_build_at_the_end(monkeypatch):
    pytest.importorskip("PySide6")
    from annotoolbox.infra import buildcache
    from annotoolbox.render import shaderstart

    warmed, loading = [], threading.Event()
    loading.set()  # an island loads: no model job starts
    _fake_parts(monkeypatch, shaderstart, lambda source, stem, m: warmed.append(stem))
    monkeypatch.setattr(shaderstart, "HOLD_POLL", 0.01)
    later = shaderstart.Later(Files(), Compiler(), "quick-models", hold=loading.is_set)
    steps, done = [], threading.Event()
    later.start(lambda d, t: steps.append((d, t)), lambda result: done.set())
    time.sleep(0.2)
    assert warmed == [] and steps == [(0, 2)]
    loading.clear()  # the island is in
    assert done.wait(5)
    assert sorted(warmed) == ["model_pbr", "model_x"] and steps[-1] == (2, 2) and later.result.done == 2
    assert buildcache.get(shaderstart.NAMESPACE, "quick-models") is not None


def test_a_stopped_model_part_leaves_its_build_unmarked(monkeypatch):
    pytest.importorskip("PySide6")
    from annotoolbox.infra import buildcache
    from annotoolbox.render import shaderstart

    _fake_parts(monkeypatch, shaderstart, lambda *a: None)
    monkeypatch.setattr(shaderstart, "HOLD_POLL", 0.01)
    later = shaderstart.Later(Files(), Compiler(), "quick-stopped", hold=lambda: True)
    done = threading.Event()
    later.start(None, lambda result: done.set())
    later.stop()
    assert done.wait(5)
    assert later.result.stopped and later.result.done == 0
    assert buildcache.get(shaderstart.NAMESPACE, "quick-stopped") is None  # the next start runs it again


def test_on_start_runs_the_first_part_and_hands_on_the_second_once_a_build(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from annotoolbox.infra import dxc
    from annotoolbox.render import rhiview, shaderstart
    from annotoolbox.sources import gametree

    _fake_parts(monkeypatch, shaderstart, lambda *a: None)
    opened = []
    monkeypatch.setattr(rhiview, "chosen", lambda renderer: "d3d12")
    monkeypatch.setattr(gametree, "open_game", lambda key, folder: opened.append(folder) or Files())
    monkeypatch.setattr(dxc.Dxc, "find", staticmethod(lambda anno117: Compiler()))
    shown = []
    later = shaderstart.on_start(str(tmp_path), "auto", True, shown.append, shown.append)
    assert isinstance(later, shaderstart.Later) and shown  # the first part ran on the screen
    done = threading.Event()
    later.start(None, lambda result: done.set())
    assert done.wait(5)
    assert shaderstart.on_start(str(tmp_path), "auto", True, shown.append, shown.append) is None  # both marked
    assert len(opened) == 1  # the quick marks: the install was not opened again
    assert shaderstart.on_start(str(tmp_path), "auto", False, shown.append, shown.append) is None  # no hardware


def test_an_island_waits_for_the_pre_warms_compile_instead_of_making_it_again():
    from annotoolbox.models import shaderview

    source = object.__new__(shaderview.ShaderSource)
    source._lock, source._memo, source._local = threading.Lock(), {}, threading.local()
    made, started, release = [], threading.Event(), threading.Event()

    def make():
        made.append(1)
        started.set()
        release.wait(5)
        return "dxil"

    def warm():
        with source.passing():
            source._once("shader-a", make)

    t = threading.Thread(target=warm)
    t.start()
    assert started.wait(5)
    got = []
    island = threading.Thread(target=lambda: got.append(source._once("shader-a", make)))
    island.start()
    time.sleep(0.05)
    release.set()
    t.join(5)
    island.join(5)
    assert got == ["dxil"] and made == [1]  # one compile for both
    assert "shader-a" in source._memo  # an island asked for it: kept at hand
    with source.passing():
        source._once("shader-b", lambda: "only warmed")
    assert "shader-b" not in source._memo  # nobody else asked: the build cache keeps it, not the session


def test_the_view_and_the_stage_share_one_source_per_install(tmp_path):
    from annotoolbox.models import shaderview

    made = []

    class Source:
        def __init__(self, files, compiler, frame_instances=False) -> None:
            made.append(files)
            self.files = SimpleNamespace(files=files)

    trees = [SimpleNamespace(folder=tmp_path), SimpleNamespace(folder=str(tmp_path) + "/")]
    real = shaderview.ShaderSource
    shaderview.ShaderSource = Source
    try:
        shaderview._shared.clear()
        a, b = (shaderview.shared(t, Compiler(), True) for t in trees)
    finally:
        shaderview.ShaderSource = real
        shaderview._shared.clear()
    assert a is b and len(made) == 1  # the start-up stage's tree and the view's: one install


def test_the_fallback_of_a_shipped_variant_is_the_games_own_dxil():
    root = os.environ.get("ANNO117_DIR")
    if not root:
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR)")
    from annotoolbox.infra import dxc
    from annotoolbox.sources.anno117 import Anno117Files

    try:
        compiler = dxc.Dxc.find(anno117=root)
    except Exception as exc:
        pytest.skip(f"the game's compiler: {exc}")
    files = Anno117Files(root)
    ns = shadernative.NativeShaders(files, compiler)
    v = ns.variant("posteffect_tonemap")
    assert ns.shipped(v)
    shipped, ours = ns.shaders(v), ns.fallback(v)
    assert {k: s.bytecode for k, s in ours.items()} == {k: s.bytecode for k, s in shipped.items()}
    stale = ns.variant("terrain_baking")  # its shipped cache was built from other sources: compiled here
    assert not ns.shipped(stale) and "csCompressBC4" in ns.shaders(stale)
