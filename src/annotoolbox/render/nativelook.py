"""Which look a 3D view draws a game's files in, by the open game: the game's own shaders compiled by the compiler
that game uses (117: DXC to DXIL on Direct3D 12; 2205, 2070, 1404, 1701, 1800: FXC to DXBC on Direct3D 11) where the
compiler, the backend and our reading of that game's frame are there, else our plain look (viewer.md "Looks"); with the
backend that look needs, the frame's parts as switches and the choice remembered per game.

``options(game, found, backends, choice)`` is the entry (no Qt, no GPU; texts in the interface language); ``GAMES``
says how far a game's path is built, ``part_on`` whether a pass draws a part, ``find_compiler`` finds the compiler,
``Choice`` is what ``Settings.rendering`` keeps, ``hardware_env`` Qt's hardware setting. viewer.md; tool-suite.md 21.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.i18n import N_, tr

DXC, FXC = "dxc", "fxc"
# the frame's parts a native path can draw, as switches; a game lists each with "yes" or why it does not draw it
FEATURES = {
    "shadows": N_("common.sun_shadows"),
    "sky": N_("common.skys_light"),
    "atmosphere": N_("common.visible_sky_haze"),
    "clouds": N_("common.clouds"),
    "lights": N_("common.local_lights"),
    "transparent": N_("common.transparent_pass"),
    "water": N_("common.water"),
    "waves": N_("common.ocean_waves"),
    "post": N_("common.post_chain_tone_map_grade"),
    "eye": N_("common.eye_adaptation"),
    "ssao": N_("common.ambient_occlusion"),
    "ssr": N_("common.screen_space_reflections"),
}
YES = "yes"
NOT_YET = "not yet"
# 2205 ships an ambient occlusion its default settings never run and whose pixels no shader marks (shader-compile.md
# 11 "2205's ambient occlusion")
NONE_2205 = "the game draws none"
# 2205 traces its screen-space reflections on rivers and lakes only: none under a model (shader-compile.md 11)
RIVERS_2205 = "the game traces them on rivers and lakes only"
ONLY_NATIVE = "only with the game's own shaders"  # a part's state under our plain look
# a part's state other than YES (a key the code compares) -> the string id of the reason a view shows
STATUS_TEXTS = {
    NOT_YET: N_("common.not_yet"),
    NONE_2205: N_("common.game_draws_none"),
    RIVERS_2205: N_("common.game_traces_rivers_lakes_only"),
    ONLY_NATIVE: N_("common.only_with_games_own_shaders"),
}
PLAIN = "editor"  # our plain look: the editor's shading, the fallback of every game
PLAIN_LABEL = N_("common.our_plain_look")
APIS = {"d3d12": "Direct3D 12", "d3d11": "Direct3D 11"}  # a backend as a label names it
NATIVE = "shaders"
SOFTWARE_ENV = "QSG_RHI_PREFER_SOFTWARE_RENDERER"  # Qt's widgets read it when they make their device


@dataclass(frozen=True)
class NativeGame:
    """A game's own-shader path: its compiler and backend, whether the viewer draws with it yet, per frame part its
    state."""

    game: str
    title: str
    compiler: str
    backend: str
    built: bool
    features: dict = field(default_factory=dict)
    note: str = ""  # the string id of what the path draws, "" none
    library: str = ""  # the compiler build the game used, when it is not the newest (2070: d3dcompiler_43)
    pass_class: str = ""  # "module:Class" of the viewer's pass that draws it ("viewer.fxcpass:FxcPass"), "" none
    off: tuple = ()  # the parts the game itself leaves off: offered, switched off until the user turns them on


def _features(**done: str) -> dict:
    return {k: done.get(k, NOT_YET) for k in FEATURES}


GAMES = {
    "anno117": NativeGame("anno117", "Anno 117", DXC, "d3d12", True,
                          _features(shadows=YES, lights=YES, transparent=YES, sky=YES, post=YES, eye=YES, water=YES,
                                    atmosphere=YES, clouds=YES, waves=YES),
                          pass_class="viewer.gamepass:GamePass"),
    "anno1800": NativeGame("anno1800", "Anno 1800", FXC, "d3d11", True,
                           _features(shadows=YES, sky=YES, atmosphere=YES, clouds=YES, lights=YES, transparent=YES,
                                     post=YES, water=YES, eye=YES, ssao=YES, waves=YES, ssr=YES),
                           note=N_("common.own_shaders_draw_anno1800"),
                           pass_class="viewer.forwardpass:ForwardPass1800", off=("eye", "waves")),
    "anno2205": NativeGame("anno2205", "Anno 2205", FXC, "d3d11", True,
                           _features(shadows=YES, sky=YES, atmosphere=YES, clouds=YES, lights=YES, transparent=YES,
                                     water=YES, post=YES, ssao=NONE_2205, ssr=RIVERS_2205, waves=YES),
                           note=N_("common.own_shaders_draw_anno2205"),
                           pass_class="viewer.forwardpass:ForwardPass", off=("waves",)),
    "anno2070": NativeGame("anno2070", "Anno 2070", FXC, "d3d11", True,
                           _features(shadows=YES, atmosphere=YES, clouds=YES, lights=YES, transparent=YES, post=YES,
                                     water=YES, waves=YES),
                           note=N_("common.own_shaders_draw_anno2070"),
                           library="d3dcompiler_43", pass_class="viewer.fxcpass:FxcPass", off=("waves",)),
    "anno1404": NativeGame("anno1404", "Anno 1404", FXC, "d3d11", True,
                           _features(shadows=YES, atmosphere=YES, clouds=YES, post=YES, water=YES, waves=YES),
                           note=N_("common.own_shaders_draw_anno1404"),
                           library="d3dcompiler_43", pass_class="viewer.fxcpass1404:FxcPass1404", off=("waves",)),
    "anno1701": NativeGame("anno1701", "Anno 1701", FXC, "d3d11", True,
                           _features(shadows=YES, atmosphere=YES, clouds=YES, water=YES, waves=YES),
                           note=N_("common.own_shaders_draw_anno1701"),
                           library="d3dcompiler_43", pass_class="viewer.fxcpass1701:FxcPass1701", off=("waves",)),
}  # fmt: skip


def part_on(game: str, switches: dict, part: str) -> bool:
    """Whether View > Rendering leaves the frame part ``part`` of ``game``'s own shaders on (``switches``: the view's,
    absent: on unless the game leaves it off, ``off``); a part the record does not list as drawn is never switched, so
    a stale setting cannot take it out."""
    native = GAMES.get(game)
    drawn = native is not None and native.features.get(part) == YES
    return not drawn or bool(switches.get(part, part not in native.off))


# a rendering quality's effect switch (render.quality.Quality) -> the frame part it takes out when off
QUALITY_PARTS = {
    "shadows": "shadows",
    "clouds": "clouds",
    "haze": "atmosphere",
    "post": "post",
    "eye_adaptation": "eye",
    "ssao": "ssao",
    "ssr": "ssr",
}


def with_quality(switches: dict, q) -> dict:
    """View > Rendering's switches with what the rendering quality ``q`` (``render.quality.Quality``, None: the
    best) switches off besides: a part is drawn only when both leave it on."""
    out = dict(switches)
    for name, part in QUALITY_PARTS.items():
        if q is not None and not getattr(q, name):
            out[part] = False
    return out


@dataclass(frozen=True)
class Found:
    """The compiler found for a game: what it is (``text``: its name, version and where from), or why there is none."""

    text: str = ""
    why: str = ""


def find_compiler(game: str, folder: str | Path | None) -> Found:
    """The compiler a game's own shaders need, looked for as the loaders do (the game's own first)."""
    native = GAMES.get(game)
    if native is None or not native.compiler:
        return Found(why=tr("common.no_own_shaders_drawn_game"))
    try:
        if native.compiler == DXC:
            from ..infra import dxc

            c = dxc.Dxc.find(anno117=folder)
            where = tr("common.the_games") if folder and Path(folder) in c.folder.parents else str(c.folder)
            return Found(f"DXC {c.version()} ({where})")
        from ..infra import fxc

        c = fxc.Fxc.find(folder, native.library)
        return Found(f"FXC {c.version()} ({tr('common.the_games') if c.is_games() else str(c.path.parent)})")
    except Exception as e:  # the loaders' Missing errors, or an OS without the DLLs
        return Found(why=str(e))


@dataclass
class Choice:
    """What the settings keep per game: the look, the backend override (empty: the look's own) and the frame's
    switches (absent: on)."""

    look: str = ""
    backend: str = ""
    features: dict = field(default_factory=dict)

    @classmethod
    def of(cls, rendering: dict, game: str) -> Choice:
        raw = rendering.get(game) or {}
        return cls(str(raw.get("look", "")), str(raw.get("backend", "")), dict(raw.get("features", {})))

    def store(self, rendering: dict, game: str) -> None:
        rendering[game] = {"look": self.look, "backend": self.backend, "features": dict(self.features)}


@dataclass(frozen=True)
class LookOption:
    key: str
    label: str
    enabled: bool
    why: str = ""


@dataclass(frozen=True)
class FeatureOption:
    key: str
    label: str
    status: str  # YES, or the key of why it is not drawn (NOT_YET, ...)
    on: bool

    @property
    def enabled(self) -> bool:
        return self.status == YES

    @property
    def status_text(self) -> str:
        """``status`` as a view shows it, in the interface language."""
        return status_text(self.status)


@dataclass(frozen=True)
class Options:
    """What a view offers for one game: the looks (greyed out with why), the look and backend in use, the backend
    the look needs, the frame's parts, the compiler and the game build in use."""

    game: str
    looks: tuple
    look: str
    backend: str
    needed: str
    features: tuple
    compiler: str
    build: str
    note: str = ""

    def look_option(self, key: str) -> LookOption | None:
        return next((o for o in self.looks if o.key == key), None)

    def on(self, feature: str) -> bool:
        f = next((o for o in self.features if o.key == feature), None)
        return f is not None and f.enabled and f.on


def direct3d_here() -> bool:
    """Whether this system has Direct3D at all (Windows only): the game's own shaders and the native module need it."""
    return sys.platform == "win32"


def native_label(game: str) -> str:
    """The label of the look "the game's own shaders" for ``game``: its title, compiler and API."""
    g = GAMES.get(game)
    if g is None:
        return tr("common.games_own_shaders")
    return tr("common.title_own_shaders_compiler_api", title=g.title, compiler=g.compiler.upper(), api=APIS[g.backend])


def status_text(status: str) -> str:
    """A frame part's state as a view shows it: the reason in the interface language (``YES`` and a state without a
    text as they are)."""
    return tr(STATUS_TEXTS[status]) if status in STATUS_TEXTS else status


def _native_why(native: NativeGame | None, found: Found, backends: set[str]) -> str:
    """Why the game's own shaders cannot draw ("" when they can), in the interface language."""
    if native is None:
        return tr("common.game_no_shaders_viewer_reads")
    if not native.built:
        if not native.note:
            return status_text(NOT_YET)
        return tr("common.title_own_shaders_not_yet", title=native.title, note=tr(native.note))
    if native.backend not in backends and not direct3d_here():
        return tr("common.game_look_windows_only")  # the compiler's absence is the lesser news
    if not found.text:
        return found.why or tr("common.shader_compiler_not_found")
    if native.backend not in backends:
        return tr("common.needs_backend_cannot_draw", backend=native.backend)
    return ""


def options(game: str, found: Found, backends: set[str], choice: Choice | None = None, default_backend: str = "gl",
            build: str = "") -> Options:  # fmt: skip
    """The looks for ``game`` (``found``: its compiler, ``backends``: those the view can draw with,
    ``default_backend``: the backend our plain look uses), the remembered ``choice`` applied where it is still
    possible: an unavailable look (or one no longer offered) falls back to our plain look."""
    choice = choice or Choice()
    native = GAMES.get(game)
    why = _native_why(native, found, backends)
    looks = [LookOption(NATIVE, native_label(game), not why, why), LookOption(PLAIN, tr(PLAIN_LABEL), True)]
    look = NATIVE if not why else PLAIN
    if choice.look and any(o.key == choice.look and o.enabled for o in looks):
        look = choice.look
    needed = native.backend if look == NATIVE and native else default_backend
    backend = choice.backend if choice.backend in backends else needed
    if look == NATIVE and native and backend != native.backend:
        backend = native.backend  # the game's compiled shaders run on one API only
    features = []
    for key, label in FEATURES.items():
        status = native.features.get(key, NOT_YET) if native and look == NATIVE else ONLY_NATIVE
        default = not (native and key in native.off)
        features.append(FeatureOption(key, label, status, bool(choice.features.get(key, default))))
    return Options(game, tuple(looks), look, backend, needed, tuple(features),
                   found.text or found.why, build, tr(native.note) if native and native.note else "")  # fmt: skip


def exe_version(folder: str | Path | None) -> str:
    """The installed game's executable and its file version (``Bin/Win64`` or the folder itself)."""
    if not folder:
        return ""
    from ..infra.com import file_version

    for where in (Path(folder) / "Bin" / "Win64", Path(folder)):
        for exe in sorted(where.glob("*.exe")) if where.is_dir() else []:
            if exe.stem.lower().startswith("anno") and (version := file_version(exe)):
                return f"{exe.name} {version}"
    return ""


def hardware_env(hardware: bool) -> dict[str, str]:
    """The environment for the setting "Hardware acceleration" (off: Qt's software adapter, WARP on Direct3D)."""
    return {} if hardware else {SOFTWARE_ENV: "1"}


def apply_hardware(hardware: bool) -> None:
    """The setting into this process's environment: Qt reads it when a window makes its QRhi device."""
    import os

    os.environ.pop(SOFTWARE_ENV, None)
    os.environ.update(hardware_env(hardware))


_SUPPORT: list = []  # the adapter as probed, asked once


def gpu_support() -> str:
    """The Direct3D 12 adapter's feature level and ray tracing (asked once), and whether the views use it."""
    if not _SUPPORT:
        from ..infra import gpuinfo

        _SUPPORT.append(gpuinfo.probe())
    found = _SUPPORT[0]
    return tr("common.support_ray_tracing_not_used", support=found.text()) if found.ray_tracing else found.text()


@dataclass(frozen=True)
class Adapter:
    """The adapter a view draws with, as the device reports it."""

    name: str
    software: bool
    backend: str
    feature_level: str = ""
    ray_tracing: str = ""

    def text(self) -> str:
        kind = tr("common.adapter_software") if self.software else tr("common.adapter_hardware")
        extra = "".join(f", {x}" for x in (self.feature_level, self.ray_tracing) if x)
        return f"{self.name} ({kind}, {self.backend}{extra})"


def adapter_of(rhi) -> Adapter:
    """The adapter of a ``QRhi`` (its driver info: Qt reports a software adapter as a CPU device)."""
    d = rhi.driverInfo()
    name = bytes(d.deviceName).decode("utf-8", "replace")
    software = d.deviceType.name == "CpuDevice" or "Basic Render" in name
    return Adapter(name, software, rhi.backendName())
