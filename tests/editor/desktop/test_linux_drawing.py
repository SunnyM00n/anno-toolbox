"""Drawing on Linux (docs/linux.md "Phase 3 done"): the backend choice without Direct3D, the Vulkan probe, the game
look switched off with its reason, and the note that Linux support is experimental. No GPU: every system is faked."""

import pytest

pytest.importorskip("PySide6")

from annotoolbox.render import nativelook, rhiview, vulkanprobe
from annotoolbox.suite import linuxnotice, updates


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(nativelook, "direct3d_here", lambda: False)
    monkeypatch.setattr(rhiview, "direct3d_here", lambda: False)
    monkeypatch.delenv(rhiview.ENV, raising=False)
    monkeypatch.setattr(rhiview, "_has_d3d12", lambda: False)
    monkeypatch.setattr(rhiview, "_moderngl_attaches", lambda: True)
    monkeypatch.setattr(vulkanprobe, "probe", lambda: vulkanprobe.SProbe(False, why="no device"))


def test_a_direct3d_backend_from_a_windows_settings_file_is_ignored_on_linux(linux):
    assert rhiview.chosen("d3d12") == rhiview.chosen("") == "gl"
    assert rhiview.blocked("d3d12") and rhiview.blocked("d3d11")
    assert rhiview.blocked("opengl") == ""


def test_vulkan_is_chosen_only_where_the_probe_finds_a_device(linux, monkeypatch):
    assert rhiview.chosen("vulkan") == "gl" and rhiview.blocked("vulkan") == "no device"
    monkeypatch.setattr(vulkanprobe, "probe", lambda: vulkanprobe.SProbe(True, 1))
    assert rhiview.chosen("vulkan") == "vulkan" and rhiview.blocked("vulkan") == ""
    assert rhiview.chosen("") == "gl"  # never the default


def test_wayland_gets_qts_opengl_by_default_because_moderngl_cannot_attach(linux, monkeypatch):
    monkeypatch.setattr(rhiview, "_moderngl_attaches", lambda: False)
    monkeypatch.setattr(rhiview, "_opengl_fits", lambda: True)
    assert rhiview.chosen("") == "opengl" and rhiview.chosen("gl") == "opengl"
    assert rhiview.blocked("gl")
    monkeypatch.setattr(rhiview, "_opengl_fits", lambda: False)
    assert rhiview.chosen("") == "gl"  # nothing better to offer


def test_the_probe_reports_a_missing_loader_and_a_machine_without_devices(monkeypatch):
    vulkanprobe.probe.cache_clear()
    monkeypatch.setattr(vulkanprobe, "_load", lambda: None)
    assert not vulkanprobe.probe().ok and vulkanprobe.probe().why
    vulkanprobe.probe.cache_clear()
    monkeypatch.setattr(vulkanprobe, "_load", lambda: object())
    monkeypatch.setattr(vulkanprobe, "_count_devices", lambda lib: (0, "could not start"))
    assert vulkanprobe.probe() == vulkanprobe.SProbe(False, 0, "could not start")
    vulkanprobe.probe.cache_clear()
    monkeypatch.setattr(vulkanprobe, "_count_devices", lambda lib: (2, ""))
    assert vulkanprobe.probe() == vulkanprobe.SProbe(True, 2)
    vulkanprobe.probe.cache_clear()


def test_the_games_own_shaders_are_greyed_out_with_the_reason_on_linux(linux):
    found = nativelook.SFound("DXC 1.8 (somewhere)")
    for game in ("anno117", "anno1800", "anno2070"):
        opts = nativelook.options(game, found, {"gl", "opengl"})
        own = opts.look_option(nativelook.NATIVE)
        assert not own.enabled and own.why  # a reason, never an empty entry
        assert opts.look == nativelook.PLAIN and opts.backend == "gl"


def test_the_island_editors_game_look_entry_says_why(linux):
    from annotoolbox.editor.desktop.desktopeditor import look_blocker

    assert look_blocker("native") and not look_blocker("game_terrain")


def test_the_experimental_note_is_for_linux_only_and_links_the_tracker(monkeypatch):
    monkeypatch.setattr(linuxnotice.sys, "platform", "linux")
    assert linuxnotice.applies()
    monkeypatch.setattr(updates, "RELEASES", "owner/name")
    assert "https://github.com/owner/name/issues" in linuxnotice.text()
    assert '<a href="https://github.com/owner/name/issues">' in linuxnotice.text(html=True)
    monkeypatch.setattr(updates, "RELEASES", "")
    assert "xperimental" in linuxnotice.text() and "http" not in linuxnotice.text()
    monkeypatch.setattr(linuxnotice.sys, "platform", "win32")
    assert not linuxnotice.applies()
