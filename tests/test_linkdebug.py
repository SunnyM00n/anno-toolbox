"""The live link's debug commands on the suite's side (docs/live-link.md section 5 and 13, docs/debug-palette.md section 5):
the typed ``values`` argument, the three requests, the client's methods and the refusals before anything is sent."""

from __future__ import annotations

import pytest
from tests import fakegame
from tests.fakegame import FakeGame

from annotoolbox.suite import debugcommands, linkclient, livelink
from annotoolbox.suite import linkprotocol as proto


def connect(game: FakeGame):
    channel, game = fakegame.pair(game)
    link = linkclient.PipeLink(channel)
    assert link.open(fakegame.session_for(game, r"\\.\pipe\anno117-live-link-1"))
    return link, game


@pytest.fixture
def link():
    mine, game = connect(FakeGame(kinds=livelink.CAPABILITIES))
    game.debug_table = {
        "ignore_building_costs": {"available": True, "state": False},
        "heal_selected": {"available": True, "state": None},
        "destroy_selected": {"available": False, "state": None},
    }
    game.cheats = ["Fast_Build"]
    mine.game = game
    yield mine
    mine.close()
    game.drop()


def test_the_three_requests_and_the_values_kind_are_a_closed_set():
    assert proto.check_request("debug.list", {}) == ""
    assert proto.check_request("debug.run", {"command": "heal_selected", "values": {"percentage": 50}}) == ""
    assert proto.check_request("debug.run", {"command": "fps_view"}) == ""
    assert proto.check_request("debug.run", {"command": "invincible_selected", "values": {"value": True}}) == ""
    assert proto.check_request("debug.cheat", {"name": "Fast_Build"}) == ""
    for bad in (
        {"command": "x", "values": {}},  # none: leave the key out
        {"command": "x", "values": {"a": "text"}},  # no free text
        {"command": "x", "values": {"a": [1]}},
        {"command": "x", "values": {"a b": 1}},
        {"command": "x", "values": {f"v{i}": i for i in range(proto.MAX_VALUES + 1)}},
        {"command": "x", "values": {"a": float("nan")}},
        {"command": "x y"},
        {"values": {"a": 1}},
        {"command": "x", "free": "os.exit()"},
    ):
        assert proto.check_request("debug.run", bad), bad
    assert proto.check_request("debug.cheat", {"name": "a; os.exit()"}) and proto.check_request("debug.cheat", {})


def test_the_list_gives_what_the_game_binds_and_the_cheats_it_registers(link):
    reply = link.debug_list()
    assert reply.ok and reply.data["cheats"] == ["Fast_Build"]
    assert reply.data["commands"]["ignore_building_costs"] == {"available": True, "state": False}
    assert reply.data["commands"]["destroy_selected"]["available"] is False


def test_a_command_goes_out_with_typed_values_and_comes_back_with_its_state(link):
    reply = link.debug_run("ignore_building_costs")
    assert reply.status == livelink.DONE and reply.data == {"state": False}
    assert link.debug_run("heal_selected", {"percentage": 50}).status == livelink.DONE
    assert link.game.requests[-1] == ("debug.run", {"command": "heal_selected", "values": {"percentage": 50}})
    assert link.debug_run("destroy_selected").status == livelink.UNAVAILABLE  # a method the game does not bind


def test_what_the_catalogue_does_not_allow_is_refused_before_anything_is_sent(link):
    before = len(link.game.requests)
    for command, values in (
        ("os.execute", {}),  # not an id of the catalogue
        ("heal_selected", {}),  # a value missing
        ("heal_selected", {"percentage": 101}),
        ("heal_selected", {"percentage": "50"}),
        ("heal_selected", {"percentage": 5, "extra": 1}),
        ("invincible_selected", {"value": 1}),  # a flag is a bool
        ("fps_view", {"value": True}),  # a command without arguments takes none
    ):
        assert link.debug_run(command, values).status == livelink.REFUSED, (command, values)
    assert len(link.game.requests) == before


def test_a_cheat_is_sent_by_name_and_the_game_checks_it_against_its_own_list(link):
    assert link.debug_cheat("Fast_Build").status == livelink.DONE
    assert link.debug_cheat("Other").status == livelink.REFUSED
    assert link.game.requests[-1] == ("debug.cheat", {"name": "Other"})


def test_a_game_without_the_second_opt_in_says_so_and_gets_nothing():
    mine, game = connect(FakeGame())  # the welcome names no "debug"
    try:
        assert not mine.supports(livelink.DEBUG)
        reply = mine.debug_run("fps_view")
        assert reply.status == livelink.UNAVAILABLE and "live_link_commands=1" in reply.message
        assert (
            mine.debug_list().status == livelink.UNAVAILABLE
            and mine.debug_cheat("Fast_Build").status == livelink.UNAVAILABLE
        )
        assert [n for n, _ in game.requests if n.startswith("debug.")] == []
    finally:
        mine.close()
        game.drop()


def test_without_a_game_a_command_is_recorded_not_sent():
    idle = livelink.NotConnected()
    reply = idle.debug_run("fps_view")
    assert reply.status == livelink.NOT_CONNECTED and [s.name for s in idle.sent] == ["debug.run"]
    assert (
        idle.debug_run("nonsense").status == livelink.REFUSED and len(idle.sent) == 1
    )  # refused before it is recorded
    assert "fps_view" in debugcommands.commands()
