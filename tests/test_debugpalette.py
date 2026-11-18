"""The debug-command palette: the catalogue, its checks and the window, against the stand-in game (tests/fakegame.py)
over the link's own debug requests (docs/debug-palette.md section 5)."""

from __future__ import annotations

import pytest
from tests import fakegame

from annotoolbox.suite import debugcommands as dc
from annotoolbox.suite import livelink


@pytest.fixture
def game_link():
    """A link to a game that takes the debug commands (its welcome names ``debug``)."""
    link = fakegame.connect(fakegame.FakeGame(kinds=livelink.CAPABILITIES))
    yield link
    fakegame.disconnect(link)


def test_values_are_typed_and_nothing_else_passes():
    heal = dc.commands()["heal_selected"]
    assert dc.check_values(heal, {"percentage": 50}) == ({"percentage": 50}, "")
    for bad in (
        {},
        {"percentage": 101},
        {"percentage": -1},
        {"percentage": "50"},
        {"percentage": True},
        {"percentage": 5, "x": 1},
    ):
        assert dc.check_values(heal, bad)[1]
    flag = dc.commands()["invincible_selected"]
    assert dc.check_values(flag, {"value": False})[1] == "" and dc.check_values(flag, {"value": 1})[1]
    assert dc.check_values(dc.commands()["upgrade_check"], {"free": "Lua"})[1]


def test_the_catalogue_is_consistent():
    for command in dc.commands().values():
        assert command.category in dc.CATEGORIES and command.id.replace("_", "").isalnum()
        assert dc.preview(command, {}).count("{") == len(command.args)  # unfilled values show their names
        for arg in command.args:
            assert arg.kind in (dc.PERCENT, dc.GUID, dc.FLAG) and f"{{{arg.name}}}" in command.call
    assert dc.preview(dc.commands()["spawn_object"], {"guid": 42}) == "Cheats:SpawnGameObject(42)"
    assert dc.preview(dc.commands()["show_collectables"], {"value": True}).endswith("(true)")
    assert dc.commands("anno2070") == {}


def test_search_categories_favourites_and_history():
    assert {c.id for c in dc.find(dc.GAME, "unlock")} == {"unlock_all"}
    assert all(c.category == dc.VIEWS for c in dc.find(dc.GAME, "", dc.VIEWS))
    assert dc.find(dc.GAME, "", dc.FAVOURITES, ("build_mode",))[0].id == "build_mode"
    state: dict = {}
    assert dc.toggle_favourite(state, "build_mode") is True and dc.toggle_favourite(state, "build_mode") is False
    for n in range(dc.HISTORY_LIMIT + 5):
        dc.push_history(state, {"command": f"c{n}"})
    assert len(state["history"]) == dc.HISTORY_LIMIT and state["history"][0]["command"] == f"c{dc.HISTORY_LIMIT + 4}"


def test_a_command_goes_over_the_link_as_its_id_with_typed_values(game_link):
    from annotoolbox.suite import linkpalette

    game_link.game.answers["debug.run"] = (livelink.DONE, "toggled", {"state": True})
    reply = linkpalette.send(game_link, dc.commands()["heal_selected"], {"percentage": 25})
    assert reply.status == livelink.DONE and reply.data == {"state": True}
    assert game_link.game.requests[-1] == ("debug.run", {"command": "heal_selected", "values": {"percentage": 25}})


def test_nothing_unchecked_is_sent(game_link):
    from annotoolbox.suite import linkpalette

    before = len(game_link.game.requests)
    assert linkpalette.send(game_link, dc.commands()["heal_selected"], {"percentage": 500}).status == livelink.REFUSED
    assert linkpalette.send_cheat(game_link, "rm -rf", ["Money"]).status == livelink.REFUSED
    assert linkpalette.send_cheat(game_link, "Money", ["Money"]).sent  # a listed name goes
    assert len(game_link.game.requests) == before + 1


def test_without_a_game_the_link_says_so():
    from annotoolbox.suite import linkpalette

    reply = linkpalette.send(livelink.NotConnected(), dc.commands()["fps_view"], {})
    assert (
        reply.status == livelink.NOT_CONNECTED  # the link has the debug requests since docs/live-link.md section 13
        and linkpalette.fetch(livelink.NotConnected()).status == livelink.NOT_CONNECTED
    )


def wait_for(qt, condition, seconds=3.0):
    """``fakegame.wait_for`` with the window's events run meanwhile (the answers come back through a signal)."""
    return fakegame.wait_for(lambda: qt.processEvents() or condition(), seconds)


def test_the_window_offline_shows_the_catalogue_and_sends_nothing():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite import linkpalette

    qt = QApplication.instance() or QApplication([])
    livelink.reset()
    following = len(livelink._listeners["status"])
    window = linkpalette.PaletteWindow(None, {})
    assert window.list.count() == len(dc.commands()) and "not connected" in window.connection.text()
    assert not window.run.isEnabled() and len(livelink._listeners["status"]) == following + 1
    window.search.setText("heal")
    assert window.list.count() == 1 and window.current.id == "heal_selected"
    window.show()
    window.close()
    assert len(livelink._listeners["status"]) == following  # closed: it no longer asks the game at every change
    qt.processEvents()


def test_the_window_sends_lists_states_and_keeps_history(game_link):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite import linkpalette

    qt = QApplication.instance() or QApplication([])
    game_link.game.answers["debug.list"] = (
        livelink.OK, "", {"commands": {"ignore_building_costs": {"available": True, "state": False}}, "cheats": ["Money"]},
    )  # fmt: skip
    livelink.use(game_link)
    saved = []
    state: dict = {}
    window = linkpalette.PaletteWindow(None, state, lambda: saved.append(1))
    try:
        assert wait_for(qt, lambda: "ignore_building_costs" in window.available)
        assert "[off]" in window.list.item(0).text() and window.run.isEnabled()
        game_link.game.answers["debug.run"] = (livelink.DONE, "on now", {"state": True})
        window.run_current()
        assert wait_for(qt, lambda: state.get("history"))
        sent = [args for name, args in game_link.game.requests if name == "debug.run"]
        assert sent == [{"command": "ignore_building_costs"}]  # a command without arguments carries no values
        assert state["history"][0]["status"] == livelink.DONE and saved
        assert wait_for(qt, lambda: "[on]" in window.list.item(0).text())
        window.star_current()
        assert state["favourites"] == ["ignore_building_costs"]
        window.categories.setCurrentRow(window.categories.count() - 1)  # the game's own cheats
        assert window.list.count() == 1 and window.list.item(0).text() == "Money"
    finally:
        window.close()
        livelink.reset()
        qt.processEvents()
