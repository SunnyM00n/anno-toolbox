"""The start-up stage of the game look's shaders, once per game build: the variants the game does not ship, compiled
for the native backend (``models.shadernative``), and the QRhi path's compiles of every phase it still draws, so that
opening an island compiles nothing. ``SPLASH`` runs while the start-up screen shows, ``MODELS`` in the background.

``on_start`` is the entry (``suite.shell``): it runs the first part and gives the second as a ``CLater``, whose
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
# the effects an object part draws with natively where they have a G-buffer technique (``objectsnative``)
OBJECT_EFFECTS = sorted({*shadercache.SHADER_FILES.values(), *shaderview.NATURE_FILES.values()})


@dataclass
class SJob:
    label: str  # what the info line names
    run: Callable[[], object]
    kind: str = "phase"  # phase | native | model


@dataclass
class SPlan:
    build: str
    jobs: list[SJob] = field(default_factory=list)


@dataclass
class SResult:
    done: int = 0
    failed: list[str] = field(default_factory=list)
    seconds: float = 0.0
    stopped: bool = False  # ``stop`` came before every job ran (the program closed): the build stays unmarked


@dataclass(frozen=True)
class SPhase:
    """A fixed pass of the QRhi path: its name, its ``STAGES`` and the native phase that draws it instead (its
    pre-warm drops out once that phase is native and no longer leans on these compiles: ``native_phases``)."""

    name: str
    stages: dict
    native: str = ""


def phases() -> list[SPhase]:
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
    return [SPhase("terrain", terraindraw.STAGES, terrain), SPhase("terrain detail", terraindraw.DETAILED, terrain),
            SPhase("terrain shadow", terraindraw.SHADOW, terrain),
            SPhase("terrain shadow detail", terraindraw.SHADOW_DETAILED, terrain),
            SPhase("terrain pages", terrainpages.STAGES), SPhase("ground bake", terrainbake.STAGES),
            SPhase("shadow fit", shadowfit.STAGES), SPhase("sky", gamesky.STAGES), SPhase("water depth", gamewater.STAGES),
            SPhase("ocean", gameocean.STAGES, "ocean"), SPhase("atmosphere", gameatmosphere.STAGES),
            SPhase("clouds", gameclouds.STAGES), SPhase("eye adaptation", eyeadapt.STAGES, "post"),
            SPhase("post", gamepost.STAGES, "post"),
            SPhase("shading", {"cs": shaderview.SHADING})]  # fmt: skip


def native_phases() -> frozenset[str]:
    """The native phases whose QRhi compiles the view no longer needs (their pre-warm drops out). The native terrain
    still does: the switch back (``ANNO_NATIVE_TERRAIN=0``, a failure) draws by them, and its stand-ins take their
    kinds from them (native-backend.md 9.1); a phase joins here when it no longer leans on its QRhi shaders. The
    objects (11.6), the ocean's simulation and the post chain (16.3) do not: what fails compiles its QRhi shaders
    then, on demand."""
    from . import objectsnative, oceannative, postnative

    native_modules = {"objects": objectsnative, "ocean": oceannative, "post": postnative}
    return frozenset(name for name, part in native_modules.items() if part.is_enabled())


def fixed_effects() -> list[str]:
    """The effects the view's fixed passes draw with, read from their modules' ``STAGES`` (what ``EFFECTS`` holds)."""
    return sorted({spec[0].rsplit("/", 1)[-1][:-3] for phase in phases() for spec in phase.stages.values()})


def phase_jobs(source: shaderview.CShaderSource, native: frozenset[str] = frozenset()) -> list[SJob]:
    """The QRhi path's fixed passes but those of a ``native`` phase, and the passes whose switches the view picks
    itself (the sea, the rivers, the plain ground, the bake's own flip)."""
    from . import gameground, gameinland, gamesea, terrainbake

    jobs = [
        SJob(phase.name, lambda stages=phase.stages: source.stage_set(stages))
        for phase in phases()
        if phase.native not in native or not phase.native
    ]
    flip = (terrainbake.FLIP_HLSL, terrainbake.FLIP_ENTRY)
    jobs += [SJob("ground bake flip", lambda: source.own_stage(*flip)),
             SJob("sea", lambda: gamesea.compile_stages(source)),
             SJob("rivers", lambda: gameinland.compile_stages(source)),
             SJob("rivers (Egypt)", lambda: gameinland.compile_stages(source, True)),
             SJob("ground", lambda: gameground.compile_ground(source))]  # fmt: skip
    return jobs


def native_jobs(natives: shadernative.CNativeShaders, objects: bool = False) -> list[SJob]:
    """The fixed passes' effects (``EFFECTS``) whose variant the game does not ship: compiled for the native backend;
    with the native ``objects`` the techniques of the effects a part draws with (``OBJECT_EFFECTS``: a part's
    shaders are found by them, ``objectsnative.role_calls``)."""
    out = []
    for stem in EFFECTS:
        variant = natives.variant(stem)
        if not natives.shipped(variant):
            out.append(SJob(f"{stem} (not shipped)", lambda variant=variant: natives.fallback(variant), "native"))
    for stem in object_effects(natives) if objects else ():
        out.append(SJob(f"{stem} (techniques)", lambda s=stem: natives.techniques(s), "native"))
    return out


def object_effects(natives: shadernative.CNativeShaders) -> list[str]:
    """``OBJECT_EFFECTS`` the game ships files of (the techniques are read from the first)."""
    game = natives.m_game
    return [
        stem
        for stem in OBJECT_EFFECTS
        if (shader_info := game.read_info(stem)) is not None and game.m_by_file.get(shader_info.vanilla_hash)
    ]


def native_effects(natives: shadernative.CNativeShaders) -> set[str]:
    """The effects of ``OBJECT_EFFECTS`` the native objects draw (one with a G-buffer technique): their QRhi
    pre-warm drops out."""
    out = set()
    for stem in object_effects(natives):
        try:
            techniques = natives.techniques(stem)
        except Exception:  # its QRhi compiles stay in the pre-warm
            continue
        if any(shaderview.DEFERRED.search(name) for name in techniques):
            out.add(stem)
    return out


def model_variants(
    game: shadercache.CGameShaders, graphics, skip=frozenset()
) -> list[tuple[str, shadercache.Switches]]:
    """Every (effect, material switches) the shipped cache holds for the view's graphics set, once (the vertex
    format does not change the QRhi path's compile), but those of the effects ``skip`` names."""
    found: dict[tuple[str, shadercache.Switches], None] = {}
    for stem in (effect for effect in MODEL_EFFECTS if effect not in skip):
        info = game.read_info(stem)
        if info is None:
            continue
        for _path, shipped_graphics, material_switches in game.shipped_of(stem, info.vanilla_hash):
            if shipped_graphics == graphics:
                found.setdefault((stem, material_switches), None)
    return list(found)


def warm_model(source: shaderview.CShaderSource, stem: str, switches: shadercache.Switches) -> None:
    """The passes ``CShaderSource.draw_stages`` compiles for a material, by the variant's switches: its G-buffer
    technique (else its transparent one), its shadow and, for a G-buffer pass that tests for equality, its prepass."""
    path = f"{shadercache.FX_DIR}{stem}.fx"
    try:
        _vertex_stage, pixel_stage = source.pass_stages(path, switches, "gbuffer")
        equal = pixel_stage.state.get("zfunc", "").upper() == "EQUAL"
    except shaderview.CNoGBufferPass:
        try:
            source.pass_stages(path, switches, "transparent")
        except shaderview.CNoGBufferPass:
            return  # neither (a cut-out volume): the view draws it by its own look, nothing to compile
        equal = False
    for role in ("shadow", "prepass") if equal else ("shadow",):
        try:
            source.pass_stages(path, switches, role)
        except shaderview.CNoGBufferPass:
            pass  # the effect has no such technique: the view asks for none either


def plan(files, compiler, part: str = SPLASH) -> tuple[SPlan, shaderview.CShaderSource]:
    """A part's jobs for an install (``files``: its tree) and the game's compiler, on the view's shared source: an
    island opened while a part runs waits for a compile in flight instead of making it again (``run`` keeps what a
    part makes in the build cache, not in the session's memory: ``CShaderSource.passing``)."""
    source = shaderview.shared(files, compiler, frame_instances=True)
    natives = shadernative.CNativeShaders(source.m_files, compiler, source.m_game, source.m_graphics)
    native = native_phases()
    if part == SPLASH:
        return SPlan(natives.m_build, native_jobs(natives, "objects" in native) + phase_jobs(source, native)), source
    skip = native_effects(natives) if "objects" in native else frozenset()  # what the native objects draw
    jobs = [SJob(stem, lambda s=stem, material_switches=material_switches: warm_model(source, s, material_switches), "model")
            for stem, material_switches in model_variants(source.m_game, source.m_graphics, skip)]  # fmt: skip
    return SPlan(natives.m_build, jobs), source


def mark_key(build_plan: SPlan, source) -> str:
    """The build, the compiler, the graphics set and the jobs (each part has its own)."""
    jobs = sorted(f"{j.kind}:{j.label}" for j in build_plan.jobs)
    return buildcache.digest(VERSION, build_plan.build, source.m_compiler.version(), *(f"{k}={value}" for k, value in source.m_graphics),
                             len(jobs), *jobs)  # fmt: skip


def is_enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and buildcache.enabled()


def needed(build_plan: SPlan, source) -> bool:
    """Whether this build's part has not run yet (a later start skips it)."""
    return is_enabled() and buildcache.get(NAMESPACE, mark_key(build_plan, source)) is None


class CStopped(Exception):
    """A job not run: the part was stopped first."""


@dataclass
class SGate:
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
            raise CStopped


def _lower_priority() -> None:
    """This worker below the normal priority (Windows; elsewhere as it is): the window and an island go first."""
    if sys.platform == "win32":
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL


def run(build_plan: SPlan, source, progress: Callable[[int, int, str], None] | None = None, workers: int = VIEW_WORKERS,
        gate: SGate | None = None) -> SResult:  # fmt: skip
    """The jobs side by side (a compiler each: ``CDxc.for_thread``), each started when ``gate`` lets it;
    ``progress(done, total, label)`` is called on this thread after each. A job that fails is named in the result and
    compiles on demand later; the part is marked when none failed and none was stopped."""
    gate = gate or SGate()
    result, start, total = SResult(), time.perf_counter(), len(build_plan.jobs)
    shadertrack.batch(0, total)

    def run_job(job: SJob):
        gate.wait()
        with source.passing():
            return job.run()

    try:
        with ThreadPoolExecutor(workers, "shader-start", _lower_priority if gate.low else None) as pool:
            futures = {pool.submit(run_job, scheduled): scheduled for scheduled in build_plan.jobs}
            for future in as_completed(futures):
                job, error = futures[future], future.exception()
                if isinstance(error, CStopped):
                    result.stopped = True
                    continue
                if error is not None:
                    result.failed.append(f"{job.label}: {type(error).__name__}: {error}"[:300])
                result.done += 1
                shadertrack.batch(result.done, total)
                if progress is not None:
                    progress(result.done, total, job.label)
    finally:
        shadertrack.batch(0, 0)
    result.seconds = time.perf_counter() - start
    if not result.failed and not result.stopped:
        buildcache.put(NAMESPACE, mark_key(build_plan, source), str(int(time.time())).encode())
    return result


class CLater:
    """The second part (``MODELS``) once the window is up: its plan made and its jobs run on worker threads of low
    priority, none started while a view's loader has work (an island being opened: ``loader.loading``).
    ``opener()`` gives (tree, compiler) on the part's own thread when the start did not open them (reading the
    install's archive index takes seconds: the window must not wait for it)."""

    def __init__(self, tree, compiler, quick: str, hold: Callable[[], bool] | None = loader.loading,
                 opener: Callable[[], tuple] | None = None) -> None:  # fmt: skip
        self.m_tree, self.m_compiler, self.m_quick, self.m_opener = tree, compiler, quick, opener
        self.m_gate = SGate(hold, low=True)
        self.m_thread: threading.Thread | None = None
        self.m_result: SResult | None = None

    def start(self, progress: Callable[[int, int], None] | None = None,
              finished: Callable[[SResult | None], None] | None = None) -> None:  # fmt: skip
        """Run the part on a thread of its own: ``progress(done, total)`` after each job (on that thread; 0 done
        first, once the plan is made), ``finished(result)`` at the end (None: it failed before its jobs)."""
        self.m_thread = threading.Thread(target=self._work, args=(progress, finished), name="shader-start-models",
                                       daemon=True)  # fmt: skip
        self.m_thread.start()

    def _work(self, progress, finished) -> None:
        try:
            if self.m_opener is not None:
                self.m_tree, self.m_compiler = self.m_opener()
            build_plan, source = plan(self.m_tree, self.m_compiler, MODELS)
            if self.m_gate.stop.is_set():
                self.m_result = SResult(stopped=True)
            elif needed(build_plan, source):
                if progress is not None:
                    progress(0, len(build_plan.jobs))
                each = (lambda d, t, _w: progress(d, t)) if progress is not None else None
                self.m_result = run(build_plan, source, each, LATER_WORKERS, self.m_gate)
            else:
                self.m_result = SResult()
            if not self.m_result.failed and not self.m_result.stopped:
                buildcache.put(NAMESPACE, self.m_quick, b"1")
        except Exception:  # the view compiles on demand, as before
            self.m_result = None
        if finished is not None:
            finished(self.m_result)

    def stop(self, wait: float = 0.0) -> None:
        """No job starts from now on (the program closes); ``wait``: seconds to let the running ones end."""
        self.m_gate.stop.set()
        if wait and self.m_thread is not None:
            self.m_thread.join(wait)


def on_start(install: str, renderer: str, hardware: bool, status: Callable[[str], None],
             detail: Callable[[str], None]) -> CLater | None:  # fmt: skip
    """The stage as the program's start runs it (``suite.shell``): only with a 117 install, hardware acceleration and
    Direct3D 12 as the views' device (where the game look draws), each part only once a build; ``status`` and
    ``detail`` are the start-up screen's two lines. Runs the first part and gives the second (None: nothing left, or
    the stage cannot run); an error is the stage's, never the program's."""
    from pathlib import Path

    from ..infra import dxc
    from ..infra.i18n import tr
    from ..sources.gametree import open_game
    from . import rhiview

    if not install or not hardware or not is_enabled() or rhiview.chosen(renderer) != "d3d12":
        return None
    first, second = quick_key(Path(install), SPLASH), quick_key(Path(install), MODELS)
    if buildcache.get(NAMESPACE, first) is not None and buildcache.get(NAMESPACE, second) is not None:
        return None  # a later start of the same build: nothing opened, nothing read

    def opened() -> tuple:
        return open_game("anno117", Path(install)), dxc.CDxc.find(anno117=install)

    try:
        if buildcache.get(NAMESPACE, first) is not None:  # only the second part is left: opened in the background
            return CLater(None, None, second, opener=opened)
        tree, compiler = opened()
        if buildcache.get(NAMESPACE, first) is None:
            build_plan, source = plan(tree, compiler, SPLASH)
            result = None
            if needed(build_plan, source):
                status(tr("common.preparing_game_shaders"))
                result = run(build_plan, source, lambda done, total, what: detail(tr("common.preparing_game_shaders_detail",
                                                                            done=done, total=total, what=what)))  # fmt: skip
            if result is None or not result.failed:
                buildcache.put(NAMESPACE, first, b"1")
        return CLater(tree, compiler, second) if buildcache.get(NAMESPACE, second) is None else None
    except Exception:  # no compiler, an unreadable install: the view compiles on demand, as before
        return None


def quick_key(install, part: str = SPLASH) -> str:
    """A part's mark of the build by its archives' names, sizes and times, the stage's code (``VERSION``) and the
    native phases (their QRhi pre-warm drops out): checked without opening the install, so a later start costs a few
    file stats."""
    archives = sorted((install / "maindata").glob("*.rda")) if (install / "maindata").is_dir() else []
    return buildcache.digest(VERSION, "quick", part, *sorted(native_phases()), buildcache.fingerprint(archives),
                             str(install))  # fmt: skip
