"""The live link's seam: the client interface, what each app tells it, and the game to editor routing, on a test double."""

from __future__ import annotations

import json
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from tests.fakes import FakeLink
from tests.test_uiedit import WIDGET, ui_tree  # noqa: F401 - the fixture and the file it holds

from annotoolbox.suite import linkannounce, linkroute, links, livelink, project


@pytest.fixture
def own_listeners():
    """Only this test's windows hear the link's events: the handlers that windows of earlier tests left registered
    are set aside (an edit request would reach their apps too, and open a game from their empty settings)."""
    kept = {name: handlers[:] for name, handlers in livelink._listeners.items()}
    for handlers in livelink._listeners.values():
        handlers.clear()
    providers = dict(links._providers)  # the apps of earlier windows answer links too, and a late callback asks them
    links._providers.clear()
    try:  # what earlier windows were told is delivered now, to nobody: not later, to this test's window
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()
    except ImportError:
        pass
    yield
    links._providers.clear()
    links._providers.update(providers)
    for name, handlers in kept.items():
        livelink._listeners[name][:] = handlers


@pytest.fixture
def link(own_listeners):
    """A connected double as the suite's link; the not-connected one is back afterwards."""
    fake = livelink.use(FakeLink())
    yield fake
    livelink.reset()


def test_a_link_that_is_not_connected_records_and_gives_the_manual_route():
    livelink.reset()
    mine = livelink.current()
    assert not mine.connected and mine.capabilities() == frozenset() and not mine.supports(livelink.MODELS)
    assert mine.status().text == "game link: not connected"
    reply = mine.reload(livelink.TEXTS, mod="m")
    assert reply.status == livelink.NOT_CONNECTED and "Shift+F3" in reply.message and not reply.sent
    assert mine.reload(livelink.SCENES).message == "restart the game"
    assert [(s.name, s.args) for s in mine.sent] == [("reload.texts", {}), ("reload.scenes", {})]
    assert mine.select(guid=5).status == livelink.NOT_CONNECTED and mine.sent[-1].args == {"guid": 5}


def test_requests_are_typed_checked_and_sent_only_when_the_game_takes_them(link):
    assert link.files_changed(["data/a/x.rdm"], "m").ok
    assert link.reload(livelink.MODELS, ["data/a/x.rdm"], "m").ok
    assert link.reload(livelink.ASSETS, mode="rebuild").ok
    assert link.jump(object=3).ok and link.read(guid=7, fields=["Building/BuildCost"]).ok
    assert link.subscribe([livelink.SELECTION]).ok and link.select(object=1).ok
    sent = [(s.name, s.args) for s in link.sent]
    assert sent[0] == ("files.changed", {"mod": "m", "paths": ["data/a/x.rdm"]})
    assert sent[1] == ("reload.models", {"mod": "m", "paths": ["data/a/x.rdm"]})
    assert sent[2] == ("reload.assets", {"mode": "rebuild"})
    assert sent[4] == ("read", {"guid": 7, "fields": ["Building/BuildCost"]})
    before = len(link.sent)
    for bad in ("../x.rdm", "/abs/x.rdm", "C:/x.rdm", "a b.rdm", "data/../x.rdm"):
        assert link.reload(livelink.MODELS, [bad]).status == livelink.REFUSED
    assert len(link.sent) == before
    with pytest.raises(ValueError):
        link.reload("everything")
    narrow = livelink.use(FakeLink(kinds=[livelink.ASSETS]))
    assert narrow.capabilities() == {livelink.ASSETS} and narrow.reload(livelink.MODELS, ["a.rdm"]).status == (
        livelink.UNAVAILABLE
    )
    assert narrow.names == []


def test_the_registry_gives_every_app_one_link_and_tells_the_window_when_it_changes(link):
    seen = []
    remove = livelink.on("status", seen.append)
    assert livelink.current() is link
    livelink.use(FakeLink(game="anno1800", build="9"))
    assert seen[-1].text == "game link: anno1800 9"
    livelink.reset()
    assert seen[-1].connected is False
    remove()
    livelink.register_connector(lambda: None)  # the transport's extension point: nobody answers, nothing changes
    assert not livelink.connect().connected


def test_events_of_the_game_reach_the_handlers_of_whichever_link_is_current(link):
    got = []
    removers = [livelink.on(name, lambda p, n=name: got.append((n, p))) for name in livelink.EVENTS]
    link.player(livelink.SELECTION, {"object": 4})
    other = livelink.use(FakeLink())
    other.player(livelink.EDIT_REQUEST, {"guid": 9})
    link.player(livelink.LOG, {"line": "old link"})  # the replaced link no longer speaks for the suite
    assert got == [("selection", {"object": 4}), ("edit.request", {"guid": 9})]
    own = []
    stop = other.on(livelink.SESSION_EVENT, own.append)
    other.player(livelink.SESSION_EVENT, {"entered": "x"})
    stop()
    other.player(livelink.SESSION_EVENT, {"left": "x"})
    assert own == [{"entered": "x"}]
    for remove in removers:
        remove()


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("data/base/config/export/assets.xml", livelink.ASSETS),
        ("data/config/gui/texts_english.xml", livelink.TEXTS),
        ("data/ui/generated/x.phx", livelink.SCENES),
        ("data/graphics/ark/ark_lod0.rdm", livelink.MODELS),
        ("data/graphics/ark/ark.cfg", livelink.MODELS),
        ("data/graphics/shaders/x.fx", livelink.EFFECTS),
        ("data/graphics/ark/ark_diff.dds", linkannounce.TEXTURES),
        ("data/sound/mine.bnk", livelink.BANKS),
        ("data/dlc/islands/a/a.a7m", livelink.SESSION),
        ("data/ambient/light.xml", livelink.AMBIENT),
        ("modinfo.json", ""),
    ],
)
def test_a_changed_file_is_sorted_into_the_reload_it_needs(path, kind):
    assert linkannounce.classify(path) == kind


def test_an_app_announces_a_change_and_gets_a_sentence_for_its_status_line(link):
    notice = linkannounce.announce((livelink.ASSETS, livelink.TEXTS), mod="m")
    assert (
        notice.sent
        and notice.text == "game reloaded: assets, texts"
        and link.names == ["reload.assets", "reload.texts"]
    )
    link.sent.clear()
    notice = linkannounce.announce_files("m", ["a/b.rdm", "a/b.cfg", "a/t.dds", "readme.txt"])
    assert link.names == ["files.changed", "reload.models", "reload.assets"] and notice.sent
    assert link.sent[1].args == {"mod": "m", "paths": ["a/b.rdm", "a/b.cfg"]} and "texture" in notice.text
    link.sent.clear()
    pending = linkannounce.announce((livelink.SCENES,), "m", ["data/ui/x.phx"], deployed=False)
    assert not pending.sent and link.names == [] and pending.text.startswith("saved in the project: build and install")


def test_without_a_game_the_notice_is_the_manual_route():
    livelink.reset()
    notice = linkannounce.announce((livelink.ASSETS, livelink.TEXTS), mod="m")
    assert not notice.sent and notice.text == livelink.MANUAL[livelink.ASSETS]
    notice = linkannounce.announce_files("m", ["a.rdm", "b.fx"])
    assert "Ctrl+Shift+R" in notice.text and not notice.sent
    assert linkannounce.announce_files("m", ["readme.txt"]).text == "no file the game reloads changed"


def test_installing_a_project_tells_the_game_which_files_changed(link, tmp_path):
    proj = project.Project(tmp_path / "p", mod_id="mine")
    built = proj.build_dir
    (built / "data/ui").mkdir(parents=True)
    (built / "modinfo.json").write_text('{"Anno": 8, "ModID": "mine", "Version": "1.0.0"}', encoding="utf-8")
    (built / "data/ui/a.phx").write_bytes(b"1")
    (built / "data/m.rdm").write_bytes(b"1")
    lines = []
    project.install(proj, tmp_path / "mods", lines.append)
    assert link.names == [
        "files.changed",
        "reload.assets",
        "reload.models",
        "reload.scenes",
    ]  # a new scene: the pass first
    link.sent.clear()
    (built / "data/m.rdm").write_bytes(b"2")
    project.install(
        proj, tmp_path / "mods", lines.append, trash=lambda _p: None
    )  # only what differs from the installed copy
    assert [(s.name, s.args["paths"]) for s in link.sent if s.name != "files.changed"] == [
        ("reload.models", ["data/m.rdm"])
    ]
    assert lines[-1] == "game reloaded: models"


def test_the_island_editors_install_announces_the_session_files(link, tmp_path):
    from annotoolbox.editor import editorapp

    source = tmp_path / "built"
    (source / "data/islands").mkdir(parents=True)
    (source / "modinfo.json").write_text("{}", encoding="utf-8")
    (source / "data/islands/i.a7m").write_bytes(b"x")
    app = editorapp.App(tmp_path / "settings.json")
    app.settings.mods = str(tmp_path / "mods")
    job = editorapp.Job("x", lambda j: None)
    job._thread.join(5)
    app._copy_mod(job, source, editorapp.MOD_ID)
    assert link.names == ["files.changed", "reload.session"]


def test_the_edit_request_of_the_game_opens_the_asset_the_file_or_the_island(link):
    opened = []
    handlers = {
        links.SHOW_ASSET: lambda game, guid: opened.append(("asset", game, guid)) or True,
        links.VIEW_FILE: lambda game, path: opened.append(("file", game, path)) or True,
        links.OPEN_ISLAND: lambda game, island: opened.append(("island", game, island)) or True,
        links.GAME_SELECTION: lambda selection: opened.append(("selection", selection)) or True,
    }
    for name, handler in handlers.items():
        links.provide(name, handler)
    detach = linkroute.attach()
    try:
        link.player(livelink.EDIT_REQUEST, {"guid": 80629})
        link.player(livelink.EDIT_REQUEST, {"file": "data/graphics/a.cfg", "game": "anno1800"})
        link.player(livelink.EDIT_REQUEST, {"island": "data/dlc/x/x.a7m"})
        link.player(livelink.EDIT_REQUEST, {"nothing": 1})
        link.player(livelink.SELECTION, {"objects": [1]})
    finally:
        detach()
        for name, handler in handlers.items():
            links.withdraw(name, handler)
    assert opened == [
        ("asset", "anno117", 80629),
        ("file", "anno1800", "data/graphics/a.cfg"),
        ("island", "anno117", "data/dlc/x/x.a7m"),
        ("selection", {"objects": [1]}),
    ]
    link.player(livelink.EDIT_REQUEST, {"guid": 1})  # detached: nothing more is opened
    assert len(opened) == 4
    links.provide(links.SHOW_ASSET, lambda game, guid: False)  # an app that cannot show it: nothing opened
    try:
        assert linkroute.open_in_editor({"guid": 1}) == ""
    finally:
        links.withdraw(links.SHOW_ASSET)


def test_the_suite_window_shows_the_link_and_routes_an_edit_request_to_the_asset_editor(
    own_listeners, monkeypatch, tmp_path
):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from tests.fakes import asset_game

    from annotoolbox.assetedit import assetwindow
    from annotoolbox.suite import apps, shell
    from annotoolbox.suite.settings import Settings

    qt = QApplication.instance() or QApplication([])
    path = tmp_path / "settings.json"
    s = Settings(anno117=str(asset_game(tmp_path / "117")), workspace=str(tmp_path / "ws"), mods=str(tmp_path / "mods"))
    s.save(path)
    livelink.reset()
    window = shell.SuiteWindow(list(apps.BUILTIN), s, path)
    assert window.link_label.text() == "game link: not connected"
    fake = livelink.use(FakeLink(game="anno117", build="3.0"))
    assert window.link_label.text() == "game link: anno117 3.0"
    release = threading.Event()
    real_open = assetwindow.assetgame.open_game

    def slow_open(*args, **kwargs):
        release.wait(30)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(assetwindow.assetgame, "open_game", slow_open)
    fake.player(livelink.EDIT_REQUEST, {"guid": 2001})
    fake.player(livelink.EDIT_REQUEST, {"guid": 2002})  # while the game opens: replaces the pending one
    qt.processEvents()
    page = window.pages["assets"]
    assert window.current == "assets" and page.guid is None and page.pending == ("anno117", 2002)  # not blocked
    release.set()
    page.background.join()
    qt.processEvents()
    assert page.guid == 2002 and page.pending is None
    window.close()
    livelink.reset()


def test_the_asset_editor_and_the_ui_editor_announce_what_they_changed(link, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from tests.fakes import asset_game

    from annotoolbox.assetedit.assetwindow import AssetWindow
    from annotoolbox.suite.settings import Settings

    qt = QApplication.instance() or QApplication([])
    settings = Settings(anno117=str(asset_game(tmp_path / "117")), workspace=str(tmp_path / "ws"))
    window = AssetWindow(settings, tmp_path / "s.json")
    window.open("anno117", wait=True)
    folder = window.build_mod(tmp_path / "mods", wait=True)
    window.background.join()
    qt.processEvents()
    assert folder is not None and link.names == ["reload.assets", "reload.texts"]
    window.close()
    qt.processEvents()


def test_the_ui_editor_saves_into_the_project_and_says_what_comes_next(link, ui_tree, tmp_path):  # noqa: F811
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite.settings import Settings
    from annotoolbox.uiedit.uiwindow import UiWindow, _Game

    qt = QApplication.instance() or QApplication([])
    window = UiWindow(Settings(workspace=str(tmp_path / "ws"), project=str(tmp_path / "proj")))
    window.sync_layout = True  # the file is decoded and laid out on this thread: it is open at once
    window._opened("anno117", "", _Game("anno117", ui_tree, None))
    window.open_file(WIDGET)
    window.save()
    assert link.names == []  # the file is only in the project: nothing reaches the game before the install
    assert "build and install" in window.statusBar().currentMessage()
    window.close()
    qt.processEvents()


def test_one_odd_file_name_is_left_out_alone_and_a_refusal_is_not_called_a_reload():
    livelink.use(FakeLink(kinds=[livelink.ASSETS, livelink.MODELS]))
    notice = linkannounce.announce_files("m", ["a b.rdm", "x.xml"])
    assert "game reloaded: assets" in notice.text and "models" not in notice.text.split("not sent")[0]
    assert "a b.rdm" in notice.text and notice.sent
    only = linkannounce.announce_files("m", ["a b.rdm"])
    assert "a b.rdm" in only.text and "game reloaded" not in only.text
    livelink.reset()


def test_a_bad_edit_request_is_ignored_and_a_failing_handler_does_not_stop_the_others():
    assert linkroute.open_in_editor({"guid": "abc"}) == "" and linkroute.open_in_editor({"guid": None}) == ""
    fake = livelink.use(FakeLink())
    seen = []

    def broken(_payload):
        raise ValueError("bad")

    removers = [livelink.on(livelink.SELECTION, broken), livelink.on(livelink.SELECTION, seen.append)]
    fake.emit(livelink.SELECTION, {"id": 1})
    assert seen == [{"id": 1}]
    for remove in removers:
        remove()
    livelink.reset()


def test_announcing_an_install_with_nothing_before_does_not_hash_the_mod(link, tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "m.rdm").write_bytes(b"1")
    calls = []
    monkeypatch.setattr(linkannounce, "_digest", lambda p: calls.append(p) or "x")
    notice = linkannounce.announce_install("m", tmp_path, {})
    assert calls == [] and "models" in notice.text


def test_a_reload_asked_from_a_window_does_not_block_it_and_is_said_when_answered(link):
    import threading

    said = []
    gate = threading.Event()

    class Background:
        def __init__(self):
            self.jobs = []

        def run(self, work, callback):
            self.jobs.append((work, callback))

    background = Background()
    link.hold = gate  # unused by the double: the call itself is what the background runs
    notice = linkannounce.announce_in_background(
        background, said.append, lambda: linkannounce.announce((livelink.ASSETS,), mod="m")
    )
    assert notice is None and said == ["game link: reloading ..."] and link.names == []
    work, callback = background.jobs[0]
    callback(work())
    assert said[-1] == "game reloaded: assets" and link.names == ["reload.assets"]
    callback(RuntimeError("pipe gone"))
    assert said[-1] == "the game link failed: pipe gone"


def test_without_a_game_the_announcement_is_said_at_once():
    livelink.reset()
    said = []
    notice = linkannounce.announce_in_background(None, said.append, lambda: linkannounce.announce((livelink.ASSETS,)))
    assert notice is not None and said == [notice.text] and "Shift+F3" in notice.text


def test_the_game_link_panel_says_what_the_status_bar_cannot(link, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite import linkpanel

    qt = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    text = dict(linkpanel.rows(link))
    assert text["Connection"].startswith("game link:") and "not there" in text["The game's session file"]
    assert text["The game takes"] != "none"
    dialog = linkpanel.LinkDialog()
    link.player(livelink.LOG, {"lines": ["Patching completed in 2s"]})
    for _ in range(50):
        qt.processEvents()
        if "Patching completed" in dialog.log.toPlainText():
            break
        import time

        time.sleep(0.02)
    assert "Patching completed" in dialog.log.toPlainText()
    dialog.close()
    qt.processEvents()


def test_the_ui_editor_holds_the_dump_the_game_wrote_on_request(link, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    import json

    from PySide6.QtWidgets import QApplication

    from annotoolbox.sources import gametree
    from annotoolbox.suite.settings import Settings
    from annotoolbox.uiedit import dumpview, uiwindow
    from annotoolbox.uiedit.uiwindow import UiWindow

    qt = QApplication.instance() or QApplication([])
    folder = tmp_path / "log" / "ui-dump"
    folder.mkdir(parents=True)
    (folder / "20261112-1-maintitlescene.json").write_text(
        json.dumps({"probe": "ui_dump", "scene": "maintitlescene", "state": "MainTitle", "widgets": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gametree, "user_dir", lambda _game: tmp_path)
    window = UiWindow(Settings(workspace=str(tmp_path / "ws"), project=str(tmp_path / "proj")))
    dumpview.live_dumped(window, livelink.Reply(livelink.OK, "", {"files": ["20261112-1-maintitlescene.json"]}))
    window.background.join()  # the dump is read off the window's thread
    qt.processEvents()
    assert window.dump_panel.dump is not None and window.dump_panel.dump.state == "MainTitle"
    dumpview.live_dumped(window, livelink.Reply(livelink.SKIPPED, "no open scene"))
    assert "no open scene" in window.statusBar().currentMessage()
    window.close()
    qt.processEvents()
    assert uiwindow  # the module the monkeypatch reaches through gametree


def test_an_edit_request_for_a_game_without_a_folder_asks_for_it_and_scans_nothing(
    own_listeners, monkeypatch, tmp_path
):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from annotoolbox.assetedit.assetwindow import AssetWindow
    from annotoolbox.sources import gametree
    from annotoolbox.suite.settings import Settings

    qt = QApplication.instance() or QApplication([])
    asked, scanned = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: asked.append(args[1:]))
    monkeypatch.setattr(gametree, "game_stamp", lambda *args: scanned.append(args))  # nothing may be read from "."
    window = AssetWindow(Settings(workspace=str(tmp_path / "ws")), tmp_path / "s.json")
    window.open("anno117", wait=True)
    qt.processEvents()
    assert len(asked) == 1 and not scanned and window.game is None
    window.close()


def test_the_companion_mod_binds_a_key_to_the_librarys_edit_call(tmp_path):
    from annotoolbox.mod import modxml
    from annotoolbox.suite import linkcompanion

    assert not linkcompanion.is_installed(tmp_path)
    root = linkcompanion.write_companion(tmp_path)
    assert linkcompanion.is_installed(tmp_path) and root.name == linkcompanion.MOD_ID
    ops = (root / "data/base/config/export/assets.xml").read_text(encoding="utf-8")
    assert f'GUID="{modxml.SHORTCUT_CONFIG}"' in ops and "HelperLink:EditSelected()" in ops
    assert "<Active>Session</Active>" in ops and "Control;Alt;E" in ops
    info = json.loads((root / "modinfo.json").read_text(encoding="utf-8"))
    assert info["ModID"] == linkcompanion.MOD_ID and info["Category"]["English"] == "Tools"
    other = linkcompanion.write_companion(tmp_path, key="Shift;F9", mod_id="second")
    assert "Shift;F9" in (other / "data/base/config/export/assets.xml").read_text(encoding="utf-8")


def test_a_folder_that_is_no_install_is_stamped_by_its_first_files_only(tmp_path):
    from annotoolbox.sources import gametree

    for i in range(30):
        (tmp_path / f"d{i}").mkdir()
        (tmp_path / f"d{i}" / "f.txt").write_text("x", encoding="utf-8")
    (tmp_path / "top.txt").write_text("y", encoding="utf-8")
    found = gametree._some_files(tmp_path, cap=5)
    assert len(found) == 5 and found[0].name == "top.txt"  # the top of the tree first
    assert len(gametree._some_files(tmp_path)) == 31
    assert gametree.game_stamp("anno117", tmp_path) == gametree.game_stamp("anno117", tmp_path)


def test_a_huge_folder_that_is_no_install_is_not_read_as_loose_files(tmp_path, monkeypatch):
    from annotoolbox.sources import gametree

    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(gametree, "LOOSE_OPEN_FILES", 1)
    with pytest.raises(ValueError, match="no anno117 install"):
        gametree.open_game("anno117", tmp_path, tmp_path / "cache")


def test_a_pending_link_request_is_served_once_by_the_opens_callback_and_never_reopens(
    own_listeners, monkeypatch, tmp_path
):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from tests.fakes import asset_game

    from annotoolbox.assetedit import assetwindow
    from annotoolbox.suite.settings import Settings

    qt = QApplication.instance() or QApplication([])
    folder = str(asset_game(tmp_path / "game"))
    page = assetwindow.AssetWindow(
        Settings(anno117=folder, anno1800=folder, workspace=str(tmp_path / "ws")), tmp_path / "s.json"
    )
    real_open, opened = assetwindow.assetgame.open_game, []

    def open_game(key, *args, **kwargs):
        opened.append(key)
        assert len(opened) < 4, "the open keeps routing to itself"  # fail fast, not forever
        return real_open("anno117", *args, **kwargs)  # a game whose layout is not the one asked for

    monkeypatch.setattr(assetwindow.assetgame, "open_game", open_game)
    assert page.show_asset_of("anno1800", 2002) == "pending"
    page.background.join()
    qt.processEvents()
    assert opened == ["anno1800"] and page.pending is None and page.guid == 2002
    page.close()
