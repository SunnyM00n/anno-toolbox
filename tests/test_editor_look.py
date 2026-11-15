"""The editor window's remembered layout, and its look (theme, scale) - offscreen, on a synthetic settings file."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from annotoolbox.editor import editorapp
from annotoolbox.editor.desktop import workbench
from annotoolbox.suite.settings import Settings


def _window(path):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    return workbench.MainWindow(editorapp.App(path))


def _settings(tmp_path):
    path = tmp_path / "settings.json"
    Settings(anno117=str(tmp_path), anno2070=str(tmp_path), mods=str(tmp_path / "mods")).save(path)
    return path


def test_the_layout_is_remembered_between_runs_and_reset_forgets_it(tmp_path):
    path = _settings(tmp_path)
    window = _window(path)
    window.docks["Properties"].hide()
    window.resize(700, 520)
    size = (window.width(), window.height())  # the docks' minimum may widen it past 700
    window.close()
    assert set(Settings.load(path).layout) == {"geometry", "state"}

    again = _window(path)
    assert again.docks["Properties"].isHidden() and not again.docks["Islands"].isHidden()
    assert (again.width(), again.height()) == size
    again.reset_layout()
    assert not again.docks["Properties"].isHidden()
    assert Settings.load(path).layout == {}
    again.persist_layout = False
    again.docks["Properties"].hide()
    again.close()
    assert Settings.load(path).layout == {}  # a window that does not persist writes nothing


def test_a_damaged_remembered_layout_leaves_the_default(tmp_path):
    path = _settings(tmp_path)
    settings = Settings.load(path)
    settings.layout = {"geometry": "!!", "state": "bm90IGEgc3RhdGU="}
    settings.save(path)
    window = _window(path)
    assert not window.docks["Islands"].isHidden()
    window.persist_layout = False
    window.close()


def test_the_views_own_colours_follow_the_scheme_but_the_games_do_not():
    from annotoolbox.editor.desktop import desktoprender
    from annotoolbox.render import light as looks
    from annotoolbox.render import viewcolors

    assert viewcolors.PALETTES["light"].keys() == viewcolors.PALETTES["dark"].keys()
    light, dark = (looks.light(desktoprender.ViewOptions(scheme=s), 200) for s in ("light", "dark"))
    assert light["fogColor"] != dark["fogColor"] and light["sky"] != dark["sky"]
    assert sum(dark["sky"][1]) < sum(light["sky"][1])  # the dark sky is darker at the zenith
    old = [looks.light(desktoprender.ViewOptions(scheme=s, look2070=True), 200) for s in ("light", "dark")]
    assert old[0]["sky"] == old[1]["sky"] and old[0]["fogColor"] == old[1]["fogColor"]  # 2070's look is 2070's


def test_an_island_list_greys_what_cannot_be_picked_and_says_why_one_did_not_open():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from annotoolbox.editor.desktop import desktopislands

    QApplication.instance() or QApplication([])
    rows = [("a.isd", "x/a", "x/a"), ("b.isd  ·  cannot be converted", "x/b", "a level's own island")]
    lst = desktopislands.IslandList(rows, [], group_of=lambda v: "G", disabled=["x/b"])
    items = [lst.list.item(i) for i in range(lst.list.count())]
    assert items[2].flags() == Qt.ItemFlag.NoItemFlags and items[1].flags() & Qt.ItemFlag.ItemIsSelectable
    lst.list.setCurrentRow(1)
    assert lst.current_value() == "x/a"
    lst.mark("x/a", "ValueError: no heights")  # an open that failed: still listed, with the reason
    row = next(
        lst.list.item(i) for i in range(lst.list.count()) if lst.list.item(i).data(Qt.ItemDataRole.UserRole) == "x/a"
    )
    assert "cannot be opened: ValueError: no heights" in row.text() and row.toolTip() == "ValueError: no heights"


def test_the_rendering_quality_is_kept_per_game_and_set_on_the_view(tmp_path):
    """Rendering > Rendering quality writes the preset of the open island's game into the settings; an island of
    117 or 1800 opens at the kept one (Medium when none is kept), an older game's at none (its best)."""
    from types import SimpleNamespace

    from annotoolbox.editor.desktop.desktopeditor import IslandDoc
    from annotoolbox.editor.desktop.desktoprender import ViewOptions

    path = _settings(tmp_path)
    window = _window(path)
    assert set(window.quality_actions) == {"low", "medium", "high", "ultra", "custom"}
    view = SimpleNamespace(opts=ViewOptions(), update=lambda: None)
    doc = SimpleNamespace(session=SimpleNamespace(island={"game": "anno117"}), info={"kind": "anno117"},
                          shell=window, view=view)  # fmt: skip
    doc.game_key = lambda: IslandDoc.game_key(doc)
    doc.apply_quality = lambda: IslandDoc.apply_quality(doc)
    doc.apply_quality()
    assert view.opts.quality.preset == "medium"
    window.doc, window.sync = doc, lambda: None  # the rest of the window's state is not this test's
    window.set_quality("low")
    assert Settings.load(path).quality == {"anno117": "low"} and view.opts.quality.preset == "low"
    doc.session.island["game"] = "anno2070"
    doc.apply_quality()
    assert view.opts.quality is None
    window.doc = None
    window.persist_layout = False
    window.close()
