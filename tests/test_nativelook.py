"""The rendering options per game (``render.nativelook``): the game's own shaders as the default where its compiler
and backend are there, our looks as the fallback, a look that cannot draw greyed out with its reason, the backend a
look needs, the frame's switches, the choice kept per game; the setting "Hardware acceleration" (a software
adapter draws the same picture, in a child process; skipped without a Direct3D 12 device)."""

from __future__ import annotations

import os

import pytest

from annotoolbox.render import nativelook as nl

DXC = nl.Found("DXC 1.8.2405 (the game's)")
NONE = nl.Found(why="no DXC found")
ALL = {"gl", "d3d12", "d3d11", "opengl"}


def test_the_games_own_shaders_are_the_default_where_they_can_draw():
    o = nl.options("anno117", DXC, ALL)
    assert o.look == nl.NATIVE and o.backend == "d3d12" and o.needed == "d3d12"
    assert o.look_option(nl.NATIVE).enabled and "DXC" in o.look_option(nl.NATIVE).label
    assert {x.key for x in o.looks} == {nl.NATIVE, nl.PLAIN}
    assert o.compiler.startswith("DXC")
    fxc = nl.options("anno2070", nl.Found("FXC 9.29"), ALL)
    assert fxc.look == nl.NATIVE and fxc.backend == "d3d11"  # FXC's DXBC runs on Direct3D 11
    forward = nl.options("anno2205", nl.Found("FXC 6.3"), ALL)
    assert forward.look == nl.NATIVE and forward.backend == "d3d11" and forward.look_option(nl.NATIVE).enabled


def test_each_game_names_the_pass_that_draws_it_and_the_exe_version_is_read_from_the_install(tmp_path, monkeypatch):
    assert nl.GAMES["anno117"].pass_class == "viewer.gamepass:GamePass"
    assert nl.GAMES["anno2070"].pass_class == "viewer.fxcpass:FxcPass"
    assert nl.GAMES["anno2205"].pass_class == "viewer.forwardpass:ForwardPass"
    assert nl.GAMES["anno1800"].pass_class == "viewer.forwardpass:ForwardPass1800"
    assert nl.GAMES["anno1404"].pass_class == "viewer.fxcpass1404:FxcPass1404"
    assert nl.GAMES["anno1701"].pass_class == "viewer.fxcpass1701:FxcPass1701"
    assert nl.exe_version(None) == "" and nl.exe_version(tmp_path) == ""
    if os.name == "nt":
        import sys

        from annotoolbox.infra import com

        # an empty stand-in, its version read off this Python: a copied program in a temp folder draws the virus
        # scanner (CONTRIBUTING "Files the tests write"); test_com reads a real program's version
        (tmp_path / "Bin" / "Win64").mkdir(parents=True)
        (tmp_path / "Bin" / "Win64" / "Anno117.exe").write_bytes(b"")
        real = com.file_version
        monkeypatch.setattr(
            com, "file_version", lambda path: real(sys.executable) if path.name == "Anno117.exe" else ""
        )
        assert nl.exe_version(tmp_path).startswith("Anno117.exe 3.")


def test_a_missing_compiler_or_backend_falls_back_to_our_look_with_the_reason(monkeypatch):
    from dataclasses import replace

    o = nl.options("anno117", NONE, ALL)
    native = o.look_option(nl.NATIVE)
    assert not native.enabled and native.why == "no DXC found"
    assert o.look == nl.PLAIN and o.backend == "gl"  # our plain look on our own default device
    o = nl.options("anno117", DXC, {"gl", "opengl"})
    assert not o.look_option(nl.NATIVE).enabled and "d3d12" in o.look_option(nl.NATIVE).why
    monkeypatch.setitem(nl.GAMES, "anno1701", replace(nl.GAMES["anno1701"], built=False))
    unbuilt = nl.options("anno1701", nl.Found("FXC"), ALL)
    assert unbuilt.look == "editor" and nl.NOT_YET in unbuilt.look_option(nl.NATIVE).why
    assert nl.options("anno2070", NONE, ALL).look == nl.PLAIN  # every game's fallback is the plain look
    assert [o.key for o in nl.options("anno2070", NONE, ALL).looks] == [nl.NATIVE, nl.PLAIN]  # no imitations offered
    assert nl.options("anno117", DXC, ALL, nl.Choice(look="game")).look == nl.NATIVE  # a look no longer offered


def test_the_choice_is_kept_per_game_and_applied_where_it_still_can():
    rendering: dict = {}
    nl.Choice(look="editor", backend="opengl", features={"shadows": False}).store(rendering, "anno117")
    choice = nl.Choice.of(rendering, "anno117")
    o = nl.options("anno117", DXC, ALL, choice)
    assert o.look == "editor" and o.backend == "opengl"
    assert nl.Choice.of(rendering, "anno2070") == nl.Choice()  # another game keeps its own
    # a remembered native look whose compiler went away falls back; a backend override cannot move DXIL off D3D12
    assert nl.options("anno117", NONE, ALL, nl.Choice(look=nl.NATIVE)).look == nl.PLAIN
    o = nl.options("anno117", DXC, ALL, nl.Choice(look=nl.NATIVE, backend="d3d11"))
    assert o.backend == "d3d12"


def _post_not_yet(monkeypatch) -> None:
    """117's path with one part not drawn (every part draws since 2026-11-09): the post chain greyed out."""
    from dataclasses import replace

    g = nl.GAMES["anno117"]
    monkeypatch.setitem(nl.GAMES, "anno117", replace(g, features={**g.features, "post": nl.NOT_YET}))


def test_the_frames_parts_are_switches_where_the_path_draws_them(monkeypatch):
    _post_not_yet(monkeypatch)
    o = nl.options("anno117", DXC, ALL, nl.Choice(features={"lights": False}))
    shadows = next(f for f in o.features if f.key == "shadows")
    assert shadows.enabled and shadows.on and o.on("shadows")
    assert not o.on("lights")  # switched off by the user
    post = next(f for f in o.features if f.key == "post")
    assert not post.enabled and post.status == nl.NOT_YET  # greyed out: not reproduced yet
    ours = nl.options("anno117", DXC, ALL, nl.Choice(look="editor"))
    assert not any(f.enabled for f in ours.features)  # only the game's own shaders have them


def test_every_part_a_game_lists_as_drawn_is_a_switch_its_pass_reads():
    """The table is the menu: a part offered as a switch must be one the game's pass (or its bases) turns off, and
    the forward games' shadows, sky cubes and blended pass, drawn since 2026-11-10/12, are offered (R7)."""
    import importlib
    import inspect

    pytest.importorskip("PySide6")
    for key, game in nl.GAMES.items():
        module, name = game.pass_class.split(":")
        cls = getattr(importlib.import_module(f"annotoolbox.{module}"), name)
        modules = {c.__module__ for c in cls.__mro__ if c.__module__.startswith("annotoolbox")}
        text = "".join(inspect.getsource(importlib.import_module(m)) for m in modules)
        for part, state in game.features.items():
            if state == nl.YES:
                assert f'"{part}"' in text, f"{key}: {part} is offered, its pass does not read the switch"
    for key in ("anno1800", "anno2205"):
        assert all(nl.GAMES[key].features[p] == nl.YES for p in ("shadows", "sky", "lights", "transparent"))


def test_hardware_acceleration_off_asks_qt_for_a_software_adapter():
    assert nl.hardware_env(True) == {}
    assert nl.hardware_env(False) == {nl.SOFTWARE_ENV: "1"}
    before = os.environ.get(nl.SOFTWARE_ENV)
    try:
        nl.apply_hardware(False)
        assert os.environ[nl.SOFTWARE_ENV] == "1"
        nl.apply_hardware(True)
        assert nl.SOFTWARE_ENV not in os.environ
    finally:
        os.environ.pop(nl.SOFTWARE_ENV, None)
        if before is not None:
            os.environ[nl.SOFTWARE_ENV] = before


SOFTWARE_FRAME = """
import json, os, sys, tempfile
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
import tests.test_viewer as tv
from annotoolbox.render import nativelook, rhiview
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import ModelFiles
from annotoolbox.viewer.modelrender import ViewState
from annotoolbox.viewer.modelview import rhi_picture
nativelook.apply_hardware(sys.argv[1] == "hw")
app = QApplication([])
class Probe(rhiview.RhiView):
    def draw(self, ctx, screen, size):
        self.adapter = nativelook.adapter_of(ctx.rhi)
p = Probe("d3d12")
p.picture(8, 8)
with tempfile.TemporaryDirectory() as tmp:
    game = tv.game.__wrapped__(Path(tmp))
    scene = modelscene.build(game, tv.HOUSE, "anno117")
    img = rhi_picture(ModelFiles(game), scene, ViewState(look="editor", time=0.5, particles=False), (96, 64), "d3d12")
px = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(96)] for y in range(64)], float)
np.save(sys.argv[2], px)
print(json.dumps({"software": p.adapter.software, "name": p.adapter.name}))
"""


def test_a_software_adapter_draws_the_same_picture(tmp_path):
    import numpy as np
    from tests.test_rhi import ROOT, _backends

    if "d3d12" not in _backends():
        pytest.skip("no Direct3D 12 device")
    import json
    import subprocess
    import sys

    paths = [str(ROOT / "src"), str(ROOT), str(ROOT / "tests")]
    env = {**os.environ, "QT_QPA_PLATFORM": "windows", "PYTHONPATH": os.pathsep.join(paths)}
    env.pop(nl.SOFTWARE_ENV, None)
    got = {}
    for mode in ("hw", "sw"):
        out = tmp_path / f"{mode}.npy"
        done = subprocess.run([sys.executable, "-c", SOFTWARE_FRAME, mode, str(out)], capture_output=True, text=True,
                              env=env, cwd=ROOT, timeout=300)  # fmt: skip
        lines = [ln for ln in done.stdout.splitlines() if ln.startswith("{")]
        if done.returncode != 0 or not lines:
            if mode == "sw":
                pytest.skip(f"no software adapter here: {done.stderr[-300:]}")
            raise AssertionError(done.stderr[-3000:])
        got[mode] = (json.loads(lines[-1]), np.load(out))
    assert not got["hw"][0]["software"] or "Basic Render" in got["hw"][0]["name"]
    assert got["sw"][0]["software"], got["sw"][0]
    diff = float(np.abs(got["hw"][1] - got["sw"][1]).mean())
    assert got["hw"][1].std() > 1.0 and diff < 3.0, diff  # the same picture within rasterisation differences


def test_the_rendering_menu_lists_the_games_looks_and_keeps_the_choice(monkeypatch):
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QMenu

    from annotoolbox.suite.settings import Settings
    from annotoolbox.viewer import renderingmenu

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(nl, "find_compiler", lambda game, folder: DXC)
    monkeypatch.setattr(nl, "exe_version", lambda folder: "Anno117.exe 1.0")
    _post_not_yet(monkeypatch)
    changes, devices = [], []
    window = SimpleNamespace(
        tree=SimpleNamespace(game="anno117", folder=None), settings=Settings(), hosts_gl=False,
        model=SimpleNamespace(backend="d3d12"), set_state_quietly=lambda **c: changes.append(c),
        set_renderer=lambda name, remember=True: devices.append(name), statusBar=lambda: None,
    )  # fmt: skip
    menu = renderingmenu.RenderingMenu(window, QMenu())
    menu._backends = set(ALL)
    menu.apply()
    assert changes[-1]["look"] == nl.NATIVE and changes[-1]["switches"]["shadows"] and not devices
    menu.rebuild()
    texts = [a.text() for a in menu.menu.actions()]
    assert texts[0].startswith("Anno 117's own shaders") and "Compiler: DXC 1.8.2405 (the game's)" in texts
    post = next(a for a in menu.menu.actions() if a.text().startswith("Post chain"))
    assert not post.isEnabled() and nl.NOT_YET in post.text()  # greyed out with why
    editor = next(a for a in menu.menu.actions() if a.text() == "Our plain look")
    editor.trigger()
    assert window.settings.rendering["anno117"]["look"] == "editor" and changes[-1]["look"] == "editor"
    assert devices[-1] == "gl"  # our look on our own default device


def test_the_rendering_quality_menu_keeps_the_preset_per_game_and_hands_it_to_the_view(monkeypatch):
    """quality.md "What 1800's path needs": View > Rendering > Rendering quality for 117 and 1800 (default Medium,
    none for the older games); the preset's effect switches take parts out, its scale and shadow size reach 1800's
    forward frame."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QMenu

    from annotoolbox.render import quality
    from annotoolbox.suite.settings import Settings
    from annotoolbox.viewer import forwardpass, renderingmenu

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(nl, "find_compiler", lambda game, folder: nl.Found("FXC 10.0"))
    monkeypatch.setattr(nl, "exe_version", lambda folder: "")
    changes = []
    window = SimpleNamespace(
        tree=SimpleNamespace(game="anno1800", folder=None), settings=Settings(), hosts_gl=False,
        model=SimpleNamespace(backend="d3d11"), set_state_quietly=lambda **c: changes.append(c),
        set_renderer=lambda name, remember=True: None, statusBar=lambda: None,
    )  # fmt: skip
    menu = renderingmenu.RenderingMenu(window, QMenu())
    menu._backends = {"gl", "d3d11"}
    menu.apply()
    assert changes[-1]["quality"].preset == quality.DEFAULT == "medium"
    menu.rebuild()
    sub = next(a.menu() for a in menu.menu.actions() if a.menu() is not None and a.text() == "Rendering quality")
    low = next(a for a in sub.actions() if a.text() == "Low")
    low.trigger()
    q = changes[-1]["quality"]
    assert window.settings.quality == {"anno1800": "low"} and q.preset == "low"
    off = nl.with_quality({"shadows": True}, q)
    assert off["clouds"] is False and off["atmosphere"] is False and off["eye"] is False and off["shadows"]
    assert nl.with_quality({"post": False}, None) == {"post": False}  # no quality: the view's switches alone
    assert forwardpass.drawn_size((1000, 500), q) == (600, 300) and forwardpass.shadow_quality(q) == (2048, 2)
    assert forwardpass.drawn_size((1000, 500), None) == (1000, 500) and forwardpass.shadow_quality(None) == (None, 4)
    window.tree = SimpleNamespace(game="anno2070", folder=None)
    menu.apply()
    assert changes[-1]["quality"] is None  # the older games draw at their best


def test_the_viewers_maps_take_the_rendering_qualitys_largest_side():
    """quality.md: ``max_texture`` (Low 512) cuts a map to its first stored level at or below it, under the viewer's own
    1024; no quality, or Ultra's 0, leaves the viewer's cap."""
    import struct
    from types import SimpleNamespace

    from annotoolbox.viewer.modelfiles import ModelFiles

    side, levels = 2048, 12
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000, side, side, 8, 0, levels)
    head += bytes(44) + struct.pack("<II4s5I", 32, 0x4, b"DXT1", 0, 0, 0, 0, 0)
    head += struct.pack("<5I", 0x1000 | 0x400008, 0, 0, 0, 0)
    body = b"".join(bytes(8) * max(1, (side >> k) // 4) ** 2 for k in range(levels))
    files = ModelFiles(SimpleNamespace(read=lambda path: head + body))
    assert files.texture("a.dds")[:2] == (1024, 1024)
    assert files.texture("a.dds", 512)[:2] == (512, 512) and files.texture("a.dds", 0)[:2] == (1024, 1024)
    assert files.texture("a.dds", 2048)[:2] == (1024, 1024)  # High's 2048: the viewer's cap is tighter
