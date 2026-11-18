"""The game mirror (``uiedit/follow``, ``uiedit/mirror``) and the link's ``ui.scene`` event: the scenes a fake game
opens and closes stacked in its order, their live values asked one at a time and slower while nothing changes, a
click opening a scene in the editor, pause and the switch off asking nothing, and the edit loop: an edit pushed into
the installed mod, the game's reload, the values taken again."""

from __future__ import annotations

import json
import os
import time

import pytest
from tests import fakegame
from tests.test_uiedit_compose import (  # noqa: F401  # imported so that pytest finds the fixture
    INNER,
    LIST,
    OUTER,
    composed,
)
from tests.test_uiedit_speed import _open, _settle, _title, scene_files, window  # noqa: F401

from annotoolbox.suite import linkprotocol, livelink
from annotoolbox.uiedit import follow

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
KINDS = (*livelink.KINDS, livelink.VALUES, livelink.FOLLOW)


def opened(state: str, order: int, scene: str = "", depth: int = 5, parent: str = "HUD", **more) -> dict:
    return {"change": "opened", "state": state, "scene": scene or f"{state.lower()}scene", "parent": parent,
            "depth": depth, "order": order, **more}  # fmt: skip


def closed(state: str, order: int, scene: str = "") -> dict:
    return {"change": "closed", "state": state, "scene": scene or f"{state.lower()}scene", "order": order}


# the open scenes, with no window


def test_the_scenes_stack_in_the_order_the_game_opened_them():
    scenes = follow.GameScenes()
    for event in (opened("Blur", 1, depth=2, parent="Game"), opened("ConstructionMenu", 2), opened("GenericPopup", 3)):
        assert scenes.apply(event) == event["state"]
    assert [s.state for s in scenes.stack()] == ["Blur", "ConstructionMenu", "GenericPopup"]  # the popup on top
    assert scenes.layer("Blur") == 1 and scenes.layer("GenericPopup") == 3 and scenes.layer("Nothing") == 0
    assert scenes.apply(closed("ConstructionMenu", 2)) == "ConstructionMenu"
    assert scenes.apply(opened("ConstructionMenu", 4)) == "ConstructionMenu"  # opened again: over the popup now
    assert [s.state for s in scenes.stack()] == ["Blur", "GenericPopup", "ConstructionMenu"]
    scenes.hide("Blur", True)
    assert [s.state for s in scenes.shown()] == ["GenericPopup", "ConstructionMenu"]
    assert scenes.scenes["ConstructionMenu"].scene == "constructionmenuscene" and scenes.last == "ConstructionMenu"


def test_an_event_of_another_shape_changes_nothing():
    scenes = follow.GameScenes()
    scenes.apply(opened("HUD", 1, window=[2560, 1440]))
    for bad in ({"change": "opened", "state": "a b", "scene": "x"}, {"change": "moved", "state": "HUD", "scene": "x"},
                opened("X", 2, scene="../x"), closed("Never", 9), {}):  # fmt: skip
        assert scenes.apply(bad) == ""
    assert list(scenes.scenes) == ["HUD"] and scenes.window == (2560, 1440)
    scenes.apply(opened("Notes", 2, window=[0, -1]))  # a size that is none keeps the one known
    assert scenes.window == (2560, 1440)


def test_the_poller_asks_one_at_a_time_and_slower_while_nothing_changes():
    poller = follow.Poller()
    a, b = follow.Part("HUD"), follow.Part("HUD", INNER)
    poller.follow([a, b])
    first = poller.next(0.0)
    assert first is not None and poller.next(1.0) is None  # one waits
    poller.answered(first, 0.05, changed=True)
    assert poller.next(0.1) is None  # too soon after the last request (four a second at most)
    second = poller.next(0.3)
    assert second is not None and second != first
    poller.answered(second, 0.35, changed=True)
    assert poller.next(0.5) is None and poller.next(0.6) == first  # due again FAST after its answer
    gaps = []
    for _ in range(4):  # the values stay the same: each ask comes later, up to SLOW
        poller.answered(first, 10.0, changed=False)
        gaps.append(poller.due[first].every)
    assert gaps == sorted(gaps) and gaps[-1] == follow.Poller.SLOW
    poller.refresh("HUD")
    assert poller.due[first].at == 0.0  # the game reloaded the scene: asked again at once
    poller.follow([b])
    assert list(poller.due) == [b]


def test_an_edit_is_pushed_once_the_edits_stop():
    pushes = follow.Pushes()
    pushes.edit("composed", 0.0)
    pushes.edit("composed", 1.0)
    assert pushes.due(2.0) == [] and pushes.due(2.6) == ["composed"] and pushes.due(9.0) == []
    pushes.edit("x", 5.0)
    assert pushes.due(5.1, at_once=True) == ["x"]


def test_a_push_writes_only_inside_the_installed_mod(tmp_path):
    mod = tmp_path / "mods" / "mymod"
    mod.mkdir(parents=True)
    assert follow.installed_mod(tmp_path / "mods", "mymod", lambda f: "mymod") == mod
    assert follow.installed_mod(tmp_path / "mods", "mymod", lambda f: "another") is None  # another mod's folder
    assert (
        follow.installed_mod(tmp_path / "mods", "other", lambda f: "other") is None
        and not (tmp_path / "mods" / "other").exists()
    )
    dest = follow.write_into(mod, "data/ui/studio/generated/x", b"PHX")
    assert dest.read_bytes() == b"PHX" and dest.parent == mod / "data" / "ui" / "studio" / "generated"
    for bad in ("../x", "/abs", "", "C:/outside/x", "C:x", "data\\..\\..\\x", "data/ui/x:stream"):
        with pytest.raises(ValueError):
            follow.write_into(mod, bad, b"")
    assert not (tmp_path / "x").exists() and [p.name for p in mod.iterdir()] == ["data"]


def test_the_topic_is_part_of_the_protocol():
    assert linkprotocol.check_request("subscribe", {"topics": [livelink.SCENE]}) == ""
    assert livelink.SCENE in livelink.EVENTS and livelink.FOLLOW in livelink.CAPABILITIES


# the window


def _mirror(window, game: fakegame.FakeGame):  # noqa: F811
    from annotoolbox.uiedit import mirror

    link = fakegame.connect(game)
    livelink.use(link)
    m = mirror.open_mirror(window)
    m.sync = True
    m.timer.stop()  # the test ticks itself
    return m, link


def _until(window, condition) -> bool:  # noqa: F811
    deadline = time.time() + 5
    while not condition() and time.time() < deadline:
        window.qt.processEvents()
        time.sleep(0.01)
    return condition()


def _answer(game, *widgets: dict, state: str = "Composed", file: str | None = None) -> None:
    game.answers["ui.values"] = (livelink.OK, "", {"file": file, "scenes": [{"state": state, "widgets": list(widgets),
                                                                             "cut": False}], "cut": False})  # fmt: skip


def test_the_mirror_follows_the_scenes_the_game_opens_and_closes(window):  # noqa: F811
    game = fakegame.FakeGame(kinds=KINDS)
    m, link = _mirror(window, game)
    try:
        assert _until(window, lambda: livelink.SCENE in game.topics)  # subscribed while it follows
        game.push(livelink.SCENE, opened("Composed", 1, scene="composed", window=[1280, 720]))
        assert _until(window, lambda: "Composed" in m.images and m.list.topLevelItemCount() == 1)
        assert m.scenes.window == (1280, 720) and m.images["Composed"].width() == 1280
        assert "following the game: composed" in m.statusBar().currentMessage()
        title = None
        _open(window, "composed")
        _settle(window)
        title = _title(window)
        _answer(game, {"guid": title.guid, "text": "LIVE 7"})
        m.tick(100.0)
        assert game.requests[-1] == ("ui.values", {"states": ["Composed"]})  # the scene's own widgets first
        assert m.held[follow.Part("Composed")].texts()[title.guid] == "LIVE 7"
        assert [p.file for p in m.parts["Composed"]][1:] == [OUTER, LIST]  # the files it places itself
        game.push(livelink.SCENE, opened("Popup", 2, scene="composed"))
        assert _until(window, lambda: m.list.topLevelItemCount() == 2)
        assert [s.state for s in m.scenes.stack()] == ["Composed", "Popup"]
        assert m.list.topLevelItem(0).text(1) == "2"  # the list reads from the top
        assert [state for state, _ in m.view.layers] == ["Composed", "Popup"]
        game.push(livelink.SCENE, closed("Popup", 2, scene="composed"))
        assert _until(window, lambda: m.list.topLevelItemCount() == 1 and "Popup" not in m.images)
        window.doc = None
        m.edit_scene("Composed")  # a click on the scene, or a double-click in the list
        assert _until(window, lambda: window.doc is not None and window.doc.key == "composed")
    finally:
        m.close()
        fakegame.disconnect(link)


def test_values_that_did_not_change_are_not_recorded_again(window, monkeypatch):  # noqa: F811
    from annotoolbox.uiedit import liverecord

    saved = []
    save = liverecord.Recordings.save
    monkeypatch.setattr(
        liverecord.Recordings, "save", lambda self, held: saved.append(held.widgets) or save(self, held)
    )
    game = fakegame.FakeGame(kinds=KINDS)
    m, link = _mirror(window, game)
    try:
        assert _until(window, lambda: livelink.SCENE in game.topics)
        game.push(livelink.SCENE, opened("Composed", 1, scene="composed"))
        assert _until(window, lambda: "Composed" in m.images)
        m.poller.follow([follow.Part("Composed")])  # the scene's own widgets only
        for text, records in (("A", 1), ("A", 1), ("B", 2)):
            _answer(game, {"guid": "g", "text": text})
            asked = len(game.requests)
            m.poller.refresh("Composed")
            m.poller.last = -1e9
            m.tick(time.monotonic())
            assert len(game.requests) == asked + 1 and len(saved) == records  # asked each time, written on a change
    finally:
        m.close()
        fakegame.disconnect(link)


def test_a_click_names_the_topmost_scene_that_draws_there(window):  # noqa: F811
    from PySide6.QtGui import QColor, QImage

    from annotoolbox.uiedit import mirror

    view = mirror.MirrorView()
    view.fit = False
    under, over = QImage(100, 100, QImage.Format.Format_ARGB32), QImage(100, 100, QImage.Format.Format_ARGB32)
    under.fill(QColor(255, 0, 0, 255))
    over.fill(QColor(0, 0, 0, 0))
    for x in range(50):
        over.setPixelColor(x, 10, QColor(0, 255, 0, 255))
    view.show_layers([("Under", under), ("Over", over)], (100, 100))
    assert view.state_at(10, 10) == "Over" and view.state_at(80, 10) == "Under" and view.state_at(150, 10) == ""


def test_paused_or_switched_off_the_game_is_asked_nothing(window):  # noqa: F811
    from annotoolbox.uiedit import mirror

    plain = fakegame.FakeGame(kinds=(*livelink.KINDS, livelink.VALUES))  # a game that sends no scene events
    link = fakegame.connect(plain)
    livelink.use(link)
    try:
        window.qt.processEvents()
        assert window.mirror is None and plain.names() == ["subscribe"]  # no mirror: nothing but the link's own
        m = mirror.open_mirror(window)
        m.sync = True
        m.timer.stop()
        m.tick(50.0)
        assert not m.following and "no scene events" in m.statusBar().currentMessage()
        assert plain.names() == ["subscribe"] and livelink.SCENE not in plain.topics
        m.close()
    finally:
        fakegame.disconnect(link)
    game = fakegame.FakeGame(kinds=KINDS)
    m, link = _mirror(window, game)
    try:
        assert _until(window, lambda: livelink.SCENE in game.topics)
        game.push(livelink.SCENE, opened("Composed", 1, scene="composed"))
        assert _until(window, lambda: "Composed" in m.images)
        m.toggle_pause()
        assert _until(window, lambda: livelink.SCENE not in game.topics)
        asked = len(game.requests)
        for now in (100.0, 101.0, 102.0):
            m.tick(now)
        assert len(game.requests) == asked and "paused" in m.statusBar().currentMessage()
        assert "Composed" in m.images  # the last picture stays
        m.toggle_pause()  # resumed: every open scene again
        assert _until(window, lambda: livelink.SCENE in game.topics) and not m.scenes.scenes
    finally:
        m.close()
        fakegame.disconnect(link)


def _installed(tmp_path, settings) -> object:
    mods = tmp_path / "mods"
    folder = mods / "proj"
    folder.mkdir(parents=True)
    (folder / "modinfo.json").write_text(json.dumps({"ModID": "proj", "Anno": 8}), encoding="utf-8")
    settings.mods = str(mods)
    return folder


def test_an_edit_goes_into_the_game_and_the_mirror_shows_the_result(window, tmp_path):  # noqa: F811
    folder = _installed(tmp_path, window.settings)
    game = fakegame.FakeGame(kinds=KINDS, mods=[{"id": "proj", "folder": str(folder)}])
    m, link = _mirror(window, game)
    try:
        assert _until(window, lambda: livelink.SCENE in game.topics)
        game.push(livelink.SCENE, opened("Composed", 1, scene="composed"))
        assert _until(window, lambda: "Composed" in m.images)
        _open(window, "composed")
        _settle(window)
        m.push_box.setChecked(True)
        title = _title(window)
        window._edit(lambda d: d.move(title.node, (5.0, 650.0, 0.0)))
        _settle(window)
        assert "composed" in m.pushes.edited  # waits for the edits to stop
        m.tick(time.monotonic() + 0.1)
        assert not any(name == "reload.scenes" for name in game.names())  # not yet: typing goes on
        m.tick(time.monotonic() + follow.Pushes.DELAY + 1)
        assert _until(window, lambda: any(name == "reload.scenes" for name in game.names()))
        name, args = next((n, a) for n, a in game.requests if n == "reload.scenes")
        path = window.game.files.entries["composed"].path
        assert args["mod"] == "proj" and args["paths"] == [path] and "Composed" in args["states"]
        assert (folder / path).read_bytes() == window.game.files.extra["composed"]  # into the installed mod only
        assert "pushed" in m.statusBar().currentMessage()
        _answer(game, {"guid": title.guid, "text": "AFTER"})
        m.tick(time.monotonic() + 5)  # the reloaded scene is asked again at once
        assert m.held[follow.Part("Composed")].texts()[title.guid] == "AFTER"
        game.answers["reload.scenes"] = (livelink.BUSY, "a session loads", {})
        m.push_now()
        assert "a session loads" in m.statusBar().currentMessage()  # said, and the mirror goes on
        assert m.following and "Composed" in m.images
    finally:
        m.close()
        fakegame.disconnect(link)


def test_no_push_without_an_installed_mod_or_an_open_scene(window, tmp_path):  # noqa: F811
    game = fakegame.FakeGame(kinds=KINDS)
    m, link = _mirror(window, game)
    try:
        assert _until(window, lambda: livelink.SCENE in game.topics)
        _open(window, "composed")
        _settle(window)
        m.push_now()
        assert "no scene the game has open" in m.statusBar().currentMessage()
        game.push(livelink.SCENE, opened("Composed", 1, scene="composed"))
        assert _until(window, lambda: "Composed" in m.images)
        m.push_now()
        assert "not installed" in m.statusBar().currentMessage()
        assert not any(name == "reload.scenes" for name in game.names())
    finally:
        m.close()
        fakegame.disconnect(link)


@pytest.fixture(autouse=True)
def _no_link_left():
    yield
    livelink.reset()
