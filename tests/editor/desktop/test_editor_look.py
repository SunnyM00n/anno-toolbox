"""The editor window's remembered layout, and its look (theme, scale) - offscreen, on a synthetic settings file."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from annotoolbox.editor import editorapp
from annotoolbox.editor.desktop import workbench
from annotoolbox.suite.settings import CSettings


def _window(path):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    return workbench.CMainWindow(editorapp.CApp(path))


def _settings(tmp_path):
    path = tmp_path / "settings.json"
    CSettings(anno117=str(tmp_path), anno2070=str(tmp_path), mods=str(tmp_path / "mods")).save(path)
    return path


def test_the_layout_is_remembered_between_runs_and_reset_forgets_it(tmp_path):
    path = _settings(tmp_path)
    window = _window(path)
    window.m_docks["Properties"].hide()
    window.resize(700, 520)
    size = (window.width(), window.height())  # the docks' minimum may widen it past 700
    window.close()
    assert set(CSettings.load(path).layout) == {"geometry", "state"}

    again = _window(path)
    assert again.m_docks["Properties"].isHidden() and not again.m_docks["Islands"].isHidden()
    assert (again.width(), again.height()) == size
    again.reset_layout()
    assert not again.m_docks["Properties"].isHidden()
    assert CSettings.load(path).layout == {}
    again.m_persist_layout = False
    again.m_docks["Properties"].hide()
    again.close()
    assert CSettings.load(path).layout == {}  # a window that does not persist writes nothing


def test_a_damaged_remembered_layout_leaves_the_default(tmp_path):
    path = _settings(tmp_path)
    settings = CSettings.load(path)
    settings.m_layout = {"geometry": "!!", "state": "bm90IGEgc3RhdGU="}
    settings.save(path)
    window = _window(path)
    assert not window.m_docks["Islands"].isHidden()
    window.m_persist_layout = False
    window.close()


def test_the_views_own_colours_follow_the_scheme_but_the_games_do_not():
    from annotoolbox.editor.desktop import desktoprender
    from annotoolbox.render import light as looks
    from annotoolbox.render import viewcolors

    assert viewcolors.PALETTES["light"].keys() == viewcolors.PALETTES["dark"].keys()
    light, dark = (looks.light(desktoprender.SViewOptions(scheme=s), 200) for s in ("light", "dark"))
    assert light["fogColor"] != dark["fogColor"] and light["sky"] != dark["sky"]
    assert sum(dark["sky"][1]) < sum(light["sky"][1])  # the dark sky is darker at the zenith
    old = [looks.light(desktoprender.SViewOptions(scheme=s, look2070=True), 200) for s in ("light", "dark")]
    assert old[0]["sky"] == old[1]["sky"] and old[0]["fogColor"] == old[1]["fogColor"]  # 2070's look is 2070's


def test_an_island_list_greys_what_cannot_be_picked_and_says_why_one_did_not_open():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from annotoolbox.editor.desktop import desktopislands

    QApplication.instance() or QApplication([])
    rows = [("a.isd", "x/a", "x/a"), ("b.isd  ·  cannot be converted", "x/b", "a level's own island")]
    lst = desktopislands.CIslandList(rows, [], group_of=lambda v: "G", disabled=["x/b"])
    items = [lst.m_list.item(i) for i in range(lst.m_list.count())]
    assert items[2].flags() == Qt.ItemFlag.NoItemFlags and items[1].flags() & Qt.ItemFlag.ItemIsSelectable
    lst.m_list.setCurrentRow(1)
    assert lst.current_value() == "x/a"
    lst.mark("x/a", "ValueError: no heights")  # an open that failed: still listed, with the reason
    row = next(
        lst.m_list.item(i)
        for i in range(lst.m_list.count())
        if lst.m_list.item(i).data(Qt.ItemDataRole.UserRole) == "x/a"
    )
    assert "cannot be opened: ValueError: no heights" in row.text() and row.toolTip() == "ValueError: no heights"


def test_the_rendering_quality_is_kept_per_game_and_set_on_the_view(tmp_path):
    """Rendering > Rendering quality writes the preset of the open island's game into the settings; an island of
    117 or 1800 opens at the kept one (Medium when none is kept), an older game's at none (its best)."""
    from types import SimpleNamespace

    from annotoolbox.editor.desktop.desktopeditor import CIslandDoc
    from annotoolbox.editor.desktop.desktoprender import SViewOptions

    path = _settings(tmp_path)
    window = _window(path)
    assert set(window.m_quality_actions) == {"player", "Low", "Medium", "High", "VeryHigh", "UltraHigh", "Custom"}
    view = SimpleNamespace(m_opts=SViewOptions(), update=lambda: None)
    doc = SimpleNamespace(m_session=SimpleNamespace(m_island={"game": "anno117"}), m_info={"kind": "anno117"},
                          m_shell=window, m_view=view)  # fmt: skip
    doc.game_key = lambda: CIslandDoc.game_key(doc)
    doc.apply_quality = lambda: CIslandDoc.apply_quality(doc)
    doc.apply_quality()
    assert view.m_opts.quality.preset == "Medium"
    window.m_doc, window.sync = doc, lambda: None  # the rest of the window's state is not this test's
    window.set_quality("Low")
    assert CSettings.load(path).quality == {"anno117": "Low"} and view.m_opts.quality.preset == "Low"
    window.set_quality("player")  # the game's own settings again: nothing kept
    assert CSettings.load(path).quality == {} and not view.m_opts.quality.is_player_setting  # no engine.ini here
    doc.m_session.m_island["game"] = "anno2070"
    doc.apply_quality()
    assert view.m_opts.quality is None
    window.m_doc = None
    window.m_persist_layout = False
    window.close()


def test_the_shipped_shaders_switch_is_kept_turns_every_native_phase_and_says_why_not(tmp_path, monkeypatch):
    """Rendering > The game's shipped shaders: off writes the setting, turns every native phase off (each phase's
    own switch then has nothing to say) and has the open view make its game look again; where the shaders cannot be
    bound (no module, not Direct3D 12, a device that refused them) it is greyed out with the reason as its tip."""
    from types import SimpleNamespace

    from annotoolbox.editor.desktop import desktopeditor
    from annotoolbox.render import nativegpu, nativelook, objectsnative, shaderstart, terrainnative

    monkeypatch.setattr(nativegpu, "_g_state", {"module": object(), "reason": ""})  # as if the module loaded
    monkeypatch.setattr(nativelook, "direct3d_here", lambda: True)
    path = _settings(tmp_path)
    window = _window(path)
    remade = []
    window.m_view.m_renderer = SimpleNamespace(remake_game=lambda: remade.append(1))
    monkeypatch.setattr(window.m_view, "m_backend", "d3d12", raising=False)
    window.sync()
    assert window.m_shipped_action.isEnabled() and window.m_shipped_action.isChecked()
    assert terrainnative.is_enabled() and objectsnative.is_enabled() and "objects" in shaderstart.native_phases()
    window.m_shipped_action.trigger()  # off
    assert not CSettings.load(path).native_shaders and remade == [1] and not window.m_shipped_action.isChecked()
    assert not terrainnative.is_enabled() and not objectsnative.is_enabled() and not shaderstart.native_phases()
    assert editorapp.CApp(path) and not nativegpu.wanted()  # the next start keeps it
    window.m_shipped_action.trigger()  # on again
    nativegpu.failed("RuntimeError: the device offers shader model 6.0")
    enabled, on, why = desktopeditor.shipped_state("d3d12")
    assert not enabled and not on and "shader model 6.0" in why and not terrainnative.is_enabled()
    assert "Direct3D 12" in desktopeditor.shipped_state("opengl")[2]
    nativegpu.want(True)  # a change forgets the failure
    assert nativegpu.on()
    window.m_view.m_renderer = None
    window.m_persist_layout = False
    window.close()
