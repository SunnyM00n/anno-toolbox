"""``suite.links``: one app asks another for a file, an asset or a model through the suite's window, without importing
it; the asset editor's "View" beside a file field; the viewer's names for the file the game loads."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from annotoolbox.models import fileresolve
from annotoolbox.sources import gametree
from annotoolbox.suite import links


def test_a_link_is_answered_by_its_last_provider_and_withdrawn_by_it():
    first, second = (lambda *a: ("first", a)), (lambda *a: ("second", a))
    links.provide("test_link", first)
    links.provide("test_link", second)
    assert links.available("test_link") and links.follow("test_link", 1) == ("second", (1,))
    links.withdraw("test_link", first)  # not the provider any more: nothing changes
    assert links.follow("test_link") == ("second", ())
    links.withdraw("test_link", second)
    assert not links.available("test_link") and links.follow("test_link") is None


def test_the_viewer_finds_the_file_the_game_loads_for_a_written_name(tmp_path):
    from annotoolbox.viewer.viewerwindow import game_file

    for rel in ("data/ui/icon_0.dds", "data/a/mesh.rdm", "data/a/b.cfg"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")
    tree = gametree.loose_tree(tmp_path)
    assert game_file(tree, "Data\\UI\\icon.png") == "data/ui/icon_0.dds"
    assert game_file(tree, "data/a/mesh.fbx") == "data/a/mesh.rdm" and game_file(tree, "data/a/b.cfg") == "data/a/b.cfg"
    assert game_file(tree, "data/none.png") == "data/none.png"


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_suite_answers_each_apps_links_and_withdraws_them_on_close(qt, tmp_path):
    from annotoolbox.suite import apps, shell
    from annotoolbox.suite.settings import Settings

    window = shell.SuiteWindow(list(apps.BUILTIN), Settings(workspace=str(tmp_path / "ws")), tmp_path / "s.json")
    assert all(links.available(n) for n in (links.VIEW_FILE, links.SHOW_ASSET, links.PLACE_MODEL, links.ASSET_FILES))
    assert "data/base/config/export/assets.xml" in links.follow(links.ASSET_FILES, "anno117")
    assert "assets" not in window.pages  # a query makes no page
    assert links.follow(links.PLACE_MODEL, "anno117", "data/a/b.cfg") == "open an island first"
    assert window.current == "islands"
    window.close()
    assert not links.available(links.VIEW_FILE)


def test_a_file_field_of_the_asset_editor_has_a_view_button_when_a_viewer_answers(qt, tmp_path):
    from PySide6.QtWidgets import QLineEdit, QToolButton

    from annotoolbox.assetedit.assetform import AssetForm

    asked = []
    form = AssetForm()
    form.doc = type("Doc", (), {"changes": type("C", (), {"game": "anno117"})(), "files": None})()
    edit = QLineEdit("data/ui/icon.png")
    assert isinstance(form._file_button(edit, None, ("Icon",)), QToolButton)  # no viewer: the picker alone
    links.provide(links.VIEW_FILE, lambda game, path: asked.append((game, path)) or True)
    try:
        box = form._file_button(edit, None, ("Icon",))
        view = next(b for b in box.findChildren(QToolButton) if b.text() == "View")
        view.click()
    finally:
        links.withdraw(links.VIEW_FILE)
    assert asked == [("anno117", "data/ui/icon.png")]


def test_a_model_picked_in_the_viewer_goes_to_the_island_editors_place_tool(qt):
    from annotoolbox.editor.editsession import game_of
    from annotoolbox.suite.apps import _islands_place

    picked, said = [], []
    view = type("View", (), {"set_pick": lambda self, pick: picked.append(pick)})()
    doc = type("Doc", (), {"info": {"kind": "built"}, "view": view, "say": lambda self, t: said.append(t)})()
    page = type("Page", (), {"doc": doc})()
    assert game_of({}, kind="file") == "anno117" and game_of({}, kind="anno2070") == "anno2070"
    assert (
        _islands_place(page, "anno2070", "data/graphics/a.cfg") == "the open island is anno117's, the model anno2070's"
    )
    assert _islands_place(page, "anno117", "data/base/graphics/x/house.cfg") == "" and said
    assert picked == [{"name": "house", "cfg": "data/base/graphics/x/house.cfg", "group": "from the viewer",
                       "title": "data/base/graphics/x/house.cfg"}]  # fmt: skip


def test_the_users_of_a_file_are_the_files_and_assets_that_name_it(tmp_path):
    from annotoolbox.refgraph import graph

    files = {
        "data/a/house.cfg": "<Config><FileName>data/a/rdm/house_lod0.fbx</FileName>"
        "<cModelDiffTex>data/a/wall_diff.psd</cModelDiffTex><Name>x.cfg</Name></Config>",
        "data/a/rdm/house_lod0.rdm": "", "data/a/wall_diff_0.dds": "",
        "data/config/assets.xml": "<Assets><Asset><Values><Standard><GUID>7</GUID><Name>House</Name></Standard>"
        "<Object><Variations><Item><Filename>data/a/house.cfg</Filename></Item></Variations></Object>"
        "<IconFilename>data/a/wall_diff.png</IconFilename></Values></Asset>"
        "<Asset><Values><Standard><GUID>8</GUID></Standard><Missing>data/none.cfg</Missing></Values></Asset></Assets>",
    }  # fmt: skip
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    tree = gametree.loose_tree(tmp_path)
    tree.game = "anno117"
    users = graph.scan_tree(tree)
    configs, assets = users.users("data/a/wall_diff_0.dds")
    assert configs == ["data/a/house.cfg"]
    assert [(u.guid, u.name, u.field) for u in assets] == [(7, "House", "IconFilename")]
    assert users.users("data/a/house.cfg")[1][0].field == "Filename" and users.users("data/a/rdm/house_lod0.rdm")[0]
    assert fileresolve.loaded_file(r"Data\A\wall_diff.tga", tree.exists, tree.game) == "data/a/wall_diff_0.dds"


def _selection_session():
    import base64

    import numpy as np

    from annotoolbox.editor.editsession import EditSession

    obj = {"key": "k", "guid": 10, "cfg": "a.cfg", "label": "Hut", "clone": True, "rotation": [0, 0, 0, 1], "scale": 1}
    objects = [
        {**obj, "key": "a", "position": [2.0, 0.5, 2.0]},
        {**obj, "key": "b", "position": [20.0, 0.5, 20.0]},
        {**obj, "key": "c", "guid": 11, "cfg": "b.cfg", "label": "Well", "position": [5.0, 0.5, 5.0]},
    ]
    island = {"width": 33, "samples_per_tile": 1, "objects": objects, "footprints": {},
              "heights": base64.b64encode(np.zeros((33, 33), "<f4").tobytes()).decode()}  # fmt: skip
    return EditSession(island)


def test_the_games_selection_finds_the_objects_of_the_open_island_by_asset_and_place():
    from annotoolbox.editor import gameselection

    s = _selection_session()
    keys = lambda found: [e.key for e in found]  # noqa: E731
    assert keys(gameselection.entries_for(s, {"objects": [{"guid": 10, "position": [19, 0, 21]}]})) == ["b"]
    assert keys(gameselection.entries_for(s, {"objects": [{"guid": "10"}, {"guid": 11}]})) == ["a", "c"]
    assert keys(gameselection.entries_for(s, {"guid": 11})) == ["c"]  # one object, not listed
    assert gameselection.entries_for(s, {"objects": [{"guid": 99}, {"name": "no guid"}, "x"]}) == []
    s.delete(s.entries[0])
    assert keys(gameselection.entries_for(s, {"guid": 10})) == ["b"]  # a deleted object is not selected


def test_the_island_editor_answers_open_island_and_follows_the_selection_without_taking_the_window():
    from annotoolbox.suite import apps

    said, opened, picked, focused = [], [], [], []
    s = _selection_session()
    view = type(
        "View",
        (),
        {"select": lambda self, e: picked.append(e.key), "focus_on": lambda self, es: focused.append(len(es))},
    )()
    doc = type("Doc", (), {"session": s, "view": view, "say": lambda self, t: said.append(t)})()
    page = type("Page", (), {"doc": doc})()
    assert apps._islands_selection(page, {"objects": [{"guid": 11}]}) and picked == ["c"] and focused == [1]
    assert said == ["the game selected Well"]
    assert not apps._islands_selection(page, {"guid": 99}) and not apps._islands_selection(None, {"guid": 10})
    spec = next(a for a in apps.BUILTIN if a.key == "islands")
    assert {links.OPEN_ISLAND, links.GAME_SELECTION} <= set(spec.links) and links.GAME_SELECTION in spec.quiet_links

    app = type(
        "App", (), {"find_island": lambda self, game, island: ("anno117", island) if game == "anno117" else None}
    )()
    page = type("Page", (), {"app": app, "open": lambda self, kind, name: opened.append((kind, name))})()
    assert apps._islands_open(page, "anno117", "data/x/i.a7m") and opened == [("anno117", "data/x/i.a7m")]
    assert not apps._islands_open(page, "anno1800", "x") and not apps._islands_open(object(), "anno117", "x")


def test_an_edit_request_names_an_island_by_path_or_file_name_and_the_app_resolves_it(tmp_path):
    from annotoolbox.editor import editorapp
    from annotoolbox.suite.settings import Settings
    from test_islandfiles import _Listed

    settings = Settings(anno117=str(tmp_path), anno2070=str(tmp_path), mods=str(tmp_path / "mods"))
    settings.save(tmp_path / "settings.json")
    app = editorapp.App(tmp_path / "settings.json")
    path = "data/base/provinces/roman/islands/pool/roman_island_small_01/roman_island_small_01.a7m"
    app.island_tree = lambda game: _Listed({path: b""})
    assert app.find_island("anno117", path) == ("anno117", path)
    assert app.find_island("anno117", "Roman_Island_Small_01.a7m") == ("anno117", path)  # by file name, any case
    assert app.find_island("anno117", "roman_island_small_01") == ("anno117", path)
    assert app.find_island("anno117", "nothing") is None and app.find_island("anno1800", "x") is None  # not installed
    assert app.find_island("anno9999", "x") is None
