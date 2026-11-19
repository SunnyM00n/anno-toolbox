"""The start-up stage of the game look's shaders, once per game build: the variants the game does not ship, compiled
for the native backend (``models.shadernative``), and the QRhi path's compiles of every phase it still draws, so that
opening an island compiles nothing. ``SPLASH`` runs while the start-up screen shows, ``MODELS`` in the background.

``on_start`` is the entry (``suite.shell``): it runs the first part and gives the second as a ``Later``, whose
``start(progress)`` runs it behind any island being opened. ``plan(files, compiler, part)`` lists a part's jobs,
``needed`` says whether this build ran it, ``run`` runs them and marks it. docs/tool-suite.md section 21, "The start-up
stage"; island-117-view.md "Speed", part 15.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from ..infra import buildcache, shadertrack
from ..models import shadercache, shadernative, shaderview
from . import loader
from .loader import VIEW_WORKERS

ENV = "ANNO_SHADER_START"  # "0": no start-up stage (the view compiles on demand, as before)
NAMESPACE, VERSION = "shader-start", 4  # the mark of a build whose part ran (bump when a job list grows)
SPLASH, MODELS = "splash", "models"  # the parts: on the start-up screen, in the background after it
HOLD_POLL = 0.25  # seconds the background part waits before it looks again whether an island still loads [ours]
LATER_WORKERS = max(1, VIEW_WORKERS // 2)  # the background part's: the cores the window and an island leave [ours]
# the effects of the view's fixed passes (the render modules' STAGES; ``fixed_effects`` lists them from there, and a
# test holds the two alike): each one's first shipped variant, compiled for the native backend when the install lacks it
EFFECTS = ("copy", "deferred_shading", "environment", "eye_adaptation", "ocean_simulation", "posteffect_colorgrade",
           "posteffect_tonemap", "shadow", "sky", "terrain", "terrain_baking", "terrain_compress")  # fmt: skip
# the effects whose variants a model's material picks (ShaderID), the island's nature and its transparent parts
MODEL_EFFECTS = sorted({*shadercache.SHADER_FILES.values(), *shaderview.NATURE_FILES.values(),
                        *shaderview.TRANSPARENT_FILES.values(), "decal_pbr"})  # fmt: skip


@dataclass
class Job:
    label: str  # what the info line names
    run: Callable[[], object]
    kind: str = "phase"  # phase | native | model


@dataclass
class Plan:
    build: str
    jobs: list[Job] = field(default_factory=list)


@dataclass
class Result:
    done: int = 0
    failed: list[str] = field(default_factory=list)
    seconds: float = 0.0
    stopped: bool = False  # ``stop`` came before every job ran (the program closed): the build stays unmarked


@dataclass(frozen=True)
class Phase:
    """A fixed pass of the QRhi path: its name, its ``STAGES`` and the native phase that draws it instead (its
    pre-warm drops out once that phase is native and no longer leans on these compiles: ``native_phases``)."""

    name: str
    stages: dict
    native: str = ""


def phases() -> list[Phase]:
    """The QRhi path's fixed passes (the render modules' ``STAGES``)."""
    from . import (
        eyeadapt,
        gameatmosphere,
        gameclouds,
        gameocean,
        gamepost,
        gamesky,
        gamewater,
        shadowfit,
        terrainbake,  # fmt: skip
        terraindraw,
        terrainpages,
    )

    terrain = "terrain"  # native-backend.md section 9 (``terrainnative``)
    return [Phase("terrain", terraindraw.STAGES, terrain), Phase("terrain detail", terraindraw.DETAILED, terrain),
            Phase("terrain shadow", terraindraw.SHADOW, terrain),
            Phase("terrain shadow detail", terraindraw.SHADOW_DETAILED, terrain),
            Phase("terrain pages", terrainpages.STAGES), Phase("ground bake", terrainbake.STAGES),
            Phase("shadow fit", shadowfit.STAGES), Phase("sky", gamesky.STAGES), Phase("water depth", gamewater.STAGES),
            Phase("ocean", gameocean.STAGES), Phase("atmosphere", gameatmosphere.STAGES),
            Phase("clouds", gameclouds.STAGES), Phase("eye adaptation", eyeadapt.STAGES), Phase("post", gamepost.STAGES),
            Phase("shading", {"cs": shaderview.SHADING})]  # fmt: skip


def native_phases() -> frozenset[str]:
    """The native phases whose QRhi compiles the view no longer needs (their pre-warm drops out). The native terrain
    still does: the switch back (``ANNO_NATIVE_TERRAIN=0``, a failure) draws by them, and its stand-ins take their
    kinds from them (native-backend.md 9.1); a phase joins here when it no longer leans on its QRhi shaders."""
    return frozenset()


def fixed_effects() -> list[str]:
    """The effects the view's fixed passes draw with, read from their modules' ``STAGES`` (what ``EFFECTS`` holds)."""
    return sorted({spec[0].rsplit("/", 1)[-1][:-3] for p in phases() for spec in p.stages.values()})


def phase_jobs(source: shaderview.ShaderSource, native: frozenset[str] = frozenset()) -> list[Job]:
    """The QRhi path's fixed passes but those of a ``native`` phase, and the passes whose switches the view picks
    itself (the sea, the rivers, the plain ground, the bake's own flip)."""
    from . import gameground, gameinland, gamesea, terrainbake

    jobs = [
        Job(p.name, lambda s=p.stages: source.stage_set(s)) for p in phases() if p.native not in native or not p.native
    ]
    flip = (terrainbake.FLIP_HLSL, terrainbake.FLIP_ENTRY)
    jobs += [Job("ground bake flip", lambda: source.own_stage(*flip)),
             Job("sea", lambda: gamesea.compile_stages(source)),
             Job("rivers", lambda: gameinland.compile_stages(source)),
             Job("rivers (Egypt)", lambda: gameinland.compile_stages(source, True)),
             Job("ground", lambda: gameground.compile_ground(source))]  # fmt: skip
    return jobs


def native_jobs(natives: shadernative.NativeShaders) -> list[Job]:
    """The fixed passes' effects (``EFFECTS``) whose variant the game does not ship: compiled for the native backend."""
    out = []
    for stem in EFFECTS:
        v = natives.variant(stem)
        if not natives.shipped(v):
            out.append(Job(f"{stem} (not shipped)", lambda v=v: natives.fallback(v), "native"))
    return out


def model_variants(game: shadercache.GameShaders, graphics) -> list[tuple[str, shadercache.Switches]]:
    """Every (effect, material switches) the shipped cache holds for the view's graphics set, once (the vertex
    format does not change the QRhi path's compile)."""
    found: dict[tuple[str, shadercache.Switches], None] = {}
    for stem in MODEL_EFFECTS:
        info = game.info(stem)
        if info is None:
            continue
        for _path, g, m in game.shipped_of(stem, info.vanilla_hash):
            if g == graphics:
                found.setdefault((stem, m), None)
    return list(found)


def warm_model(source: shaderview.ShaderSource, stem: str, switches: shadercache.Switches) -> None:
    """The passes ``ShaderSource.draw_stages`` compiles for a material, by the variant's switches: its G-buffer
    technique (else its transparent one), its shadow and, for a G-buffer pass that tests for equality, its prepass."""
    path = f"{shadercache.FX_DIR}{stem}.fx"
    try:
        _vs, ps = source.pass_stages(path, switches, "gbuffer")
        equal = ps.state.get("zfunc", "").upper() == "EQUAL"
    except shaderview.NoGBufferPass:
        try:
            source.pass_stages(path, switches, "transparent")
        except shaderview.NoGBufferPass:
            return  # neither (a cut-out volume): the view draws it by its own look, nothing to compile
        equal = False
    for role in ("shadow", "prepass") if equal else ("shadow",):
        try:
            source.pass_stages(path, switches, role)
        except shaderview.NoGBufferPass:
            pass  # the effect has no such technique: the view asks for none either


def plan(files, compiler, part: str = SPLASH) -> tuple[Plan, shaderview.ShaderSource]:
    """A part's jobs for an install (``files``: its tree) and the game's compiler, on the view's shared source: an
    island opened while a part runs waits for a compile in flight instead of making it again (``run`` keeps what a
    part makes in the build cache, not in the session's memory: ``ShaderSource.passing``)."""
    source = shaderview.shared(files, compiler, frame_instances=True)
    if part == SPLASH:
        natives = shadernative.NativeShaders(source.files, compiler, source.game, source.graphics)
        return Plan(natives.build, native_jobs(natives) + phase_jobs(source, native_phases())), source
    jobs = [Job(stem, lambda s=stem, m=m: warm_model(source, s, m), "model")
            for stem, m in model_variants(source.game, source.graphics)]  # fmt: skip
    return Plan(shadernative.build_id(source.game), jobs), source


def mark_key(p: Plan, source) -> str:
    """The build, the compiler, the graphics set and the jobs (each part has its own)."""
    jobs = sorted(f"{j.kind}:{j.label}" for j in p.jobs)
    return buildcache.digest(VERSION, p.build, source.compiler.version(), *(f"{k}={v}" for k, v in source.graphics),
                             len(jobs), *jobs)  # fmt: skip


def enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and buildcache.enabled()


def needed(p: Plan, source) -> bool:
    """Whether this build's part has not run yet (a later start skips it)."""
    return enabled() and buildcache.get(NAMESPACE, mark_key(p, source)) is None


class Stopped(Exception):
    """A job not run: the part was stopped first."""


@dataclass
class Gate:
    """When a job may start: never after ``stop``, and not while ``hold()`` says an island loads or the person paused
    the preparation (``shadertrack.pause``: a view's own compile is not held, it makes what it needs); ``low``: the
    workers run below the normal thread priority."""

    hold: Callable[[], bool] | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    low: bool = False

    def wait(self) -> None:
        while not self.stop.is_set() and (shadertrack.paused() or (self.hold is not None and self.hold())):
            self.stop.wait(HOLD_POLL)
        if self.stop.is_set():
            raise Stopped


def _lower_priority() -> None:
    """This worker below the normal priority (Windows; elsewhere as it is): the window and an island go first."""
    if sys.platform == "win32":
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL


def run(p: Plan, source, progress: Callable[[int, int, str], None] | None = None, workers: int = VIEW_WORKERS,
        gate: Gate | None = None) -> Result:  # fmt: skip
    """The jobs side by side (a compiler each: ``Dxc.for_thread``), each started when ``gate`` lets it;
    ``progress(done, total, label)`` is called on this thread after each. A job that fails is named in the result and
    compiles on demand later; the part is marked when none failed and none was stopped."""
    gate = gate or Gate()
    result, start, total = Result(), time.perf_counter(), len(p.jobs)
    shadertrack.batch(0, total)

    def go(job: Job):
        gate.wait()
        with source.passing():
            return job.run()

    try:
        with ThreadPoolExecutor(workers, "shader-start", _lower_priority if gate.low else None) as pool:
            futures = {pool.submit(go, j): j for j in p.jobs}
            for f in as_completed(futures):
                job, exc = futures[f], f.exception()
                if isinstance(exc, Stopped):
                    result.stopped = True
                    continue
                if exc is not None:
                    result.failed.append(f"{job.label}: {type(exc).__name__}: {exc}"[:300])
                result.done += 1
                shadertrack.batch(result.done, total)
                if progress is not None:
                    progress(result.done, total, job.label)
    finally:
        shadertrack.batch(0, 0)
    result.seconds = time.perf_counter() - start
    if not result.failed and not result.stopped:
        buildcache.put(NAMESPACE, mark_key(p, source), str(int(time.time())).encode())
    return result


class Later:
    """The second part (``MODELS``) once the window is up: its plan made and its jobs run on worker threads of low
    priority, none started while a view's loader has work (an island being opened: ``loader.loading``).
    ``opener()`` gives (tree, compiler) on the part's own thread when the start did not open them (reading the
    install's archive index takes seconds: the window must not wait for it)."""

    def __init__(self, tree, compiler, quick: str, hold: Callable[[], bool] | None = loader.loading,
                 opener: Callable[[], tuple] | None = None) -> None:  # fmt: skip
        self.tree, self.compiler, self.quick, self.opener = tree, compiler, quick, opener
        self.gate = Gate(hold, low=True)
        self.thread: threading.Thread | None = None
        self.result: Result | None = None

    def start(self, progress: Callable[[int, int], None] | None = None,
              finished: Callable[[Result | None], None] | None = None) -> None:  # fmt: skip
        """Run the part on a thread of its own: ``progress(done, total)`` after each job (on that thread; 0 done
        first, once the plan is made), ``finished(result)`` at the end (None: it failed before its jobs)."""
        self.thread = threading.Thread(target=self._work, args=(progress, finished), name="shader-start-models",
                                       daemon=True)  # fmt: skip
        self.thread.start()

    def _work(self, progress, finished) -> None:
        try:
            if self.opener is not None:
                self.tree, self.compiler = self.opener()
            p, source = plan(self.tree, self.compiler, MODELS)
            if self.gate.stop.is_set():
                self.result = Result(stopped=True)
            elif needed(p, source):
                if progress is not None:
                    progress(0, len(p.jobs))
                each = (lambda d, t, _w: progress(d, t)) if progress is not None else None
                self.result = run(p, source, each, LATER_WORKERS, self.gate)
            else:
                self.result = Result()
            if not self.result.failed and not self.result.stopped:
                buildcache.put(NAMESPACE, self.quick, b"1")
        except Exception:  # the view compiles on demand, as before
            self.result = None
        if finished is not None:
            finished(self.result)

    def stop(self, wait: float = 0.0) -> None:
        """No job starts from now on (the program closes); ``wait``: seconds to let the running ones end."""
        self.gate.stop.set()
        if wait and self.thread is not None:
            self.thread.join(wait)


def on_start(install: str, renderer: str, hardware: bool, status: Callable[[str], None],
             detail: Callable[[str], None]) -> Later | None:  # fmt: skip
    """The stage as the program's start runs it (``suite.shell``): only with a 117 install, hardware acceleration and
    Direct3D 12 as the views' device (where the game look draws), each part only once a build; ``status`` and
    ``detail`` are the start-up screen's two lines. Runs the first part and gives the second (None: nothing left, or
    the stage cannot run); an error is the stage's, never the program's."""
    from pathlib import Path

    from ..infra import dxc
    from ..infra.i18n import tr
    from ..sources.gametree import open_game
    from . import rhiview

    if not install or not hardware or not enabled() or rhiview.chosen(renderer) != "d3d12":
        return None
    first, second = quick_key(Path(install), SPLASH), quick_key(Path(install), MODELS)
    if buildcache.get(NAMESPACE, first) is not None and buildcache.get(NAMESPACE, second) is not None:
        return None  # a later start of the same build: nothing opened, nothing read

    def opened() -> tuple:
        return open_game("anno117", Path(install)), dxc.Dxc.find(anno117=install)

    try:
        if buildcache.get(NAMESPACE, first) is not None:  # only the second part is left: opened in the background
            return Later(None, None, second, opener=opened)
        tree, compiler = opened()
        if buildcache.get(NAMESPACE, first) is None:
            p, source = plan(tree, compiler, SPLASH)
            result = None
            if needed(p, source):
                status(tr("common.preparing_game_shaders"))
                result = run(p, source, lambda done, total, what: detail(tr("common.preparing_game_shaders_detail",
                                                                            done=done, total=total, what=what)))  # fmt: skip
            if result is None or not result.failed:
                buildcache.put(NAMESPACE, first, b"1")
        return Later(tree, compiler, second) if buildcache.get(NAMESPACE, second) is None else None
    except Exception:  # no compiler, an unreadable install: the view compiles on demand, as before
        return None


def quick_key(install, part: str = SPLASH) -> str:
    """A part's mark of the build by its archives' names, sizes and times, the stage's code (``VERSION``) and the
    native phases (their QRhi pre-warm drops out): checked without opening the install, so a later start costs a few
    file stats."""
    archives = sorted((install / "maindata").glob("*.rda")) if (install / "maindata").is_dir() else []
    return buildcache.digest(VERSION, "quick", part, *sorted(native_phases()), buildcache.fingerprint(archives),
                             str(install))  # fmt: skip
