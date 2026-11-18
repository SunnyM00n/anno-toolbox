"""The live link's requests behind the game's debug switch, on the suite's side: the debug commands (docs/live-link.md
section 5 and 13, docs/debug-palette.md section 5: the typed ``values``, the three requests, the refusals before
anything is sent) and the game side's modules (section 5 and 14: the list, the switch, the panel's rows)."""

from __future__ import annotations

import pytest
from tests import fakegame
from tests.fakegame import FakeGame

from annotoolbox.suite import debugcommands, livelink
from annotoolbox.suite import linkprotocol as proto

MODULES = [
    {"name": "trees", "class": "experimental", "on": True, "switchable": False, "why": "it places layers"},
    {"name": "farms", "class": "dev", "on": False, "switchable": True, "why": ""},
]


@pytest.fixture
def link():
    """A link to a game with the debug switch on: three commands bound, one cheat, two modules."""
    mine = fakegame.connect(FakeGame(kinds=livelink.CAPABILITIES))
    mine.game.debug_table = {
        "ignore_building_costs": {"available": True, "state": False},
        "heal_selected": {"available": True, "state": None},
        "destroy_selected": {"available": False, "state": None},
    }
    mine.game.cheats = ["Fast_Build"]
    mine.game.modules = [dict(m) for m in MODULES]
    yield mine
    fakegame.disconnect(mine)


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


def test_a_game_without_the_second_opt_in_says_so_and_gets_no_debug_or_module_request():
    mine = fakegame.connect()  # the welcome names no "debug"
    try:
        assert not mine.supports(livelink.DEBUG)
        reply = mine.debug_run("fps_view")
        assert reply.status == livelink.UNAVAILABLE and "live_link_commands=1" in reply.message
        for refused in (mine.debug_list(), mine.debug_cheat("Fast_Build"), mine.modules_set("farms", True)):
            assert refused.status == livelink.UNAVAILABLE
        assert [n for n, _ in mine.game.requests if n.startswith(("debug.", "modules."))] == []
    finally:
        fakegame.disconnect(mine)


def test_without_a_game_a_command_is_recorded_not_sent():
    idle = livelink.NotConnected()
    reply = idle.debug_run("fps_view")
    assert reply.status == livelink.NOT_CONNECTED and [s.name for s in idle.sent] == ["debug.run"]
    assert (
        idle.debug_run("nonsense").status == livelink.REFUSED and len(idle.sent) == 1
    )  # refused before it is recorded
    assert "fps_view" in debugcommands.commands()


# the game side's modules


def test_the_two_module_requests_take_a_name_and_a_true_switch_only():
    assert proto.check_request("modules.list", {}) == ""
    assert proto.check_request("modules.set", {"module": "farm_growth", "on": False}) == ""
    for bad in ({"module": "farm_growth", "on": 1}, {"module": "a b", "on": True}, {"module": "x"}, {"on": True}):
        assert proto.check_request("modules.set", bad), bad
    assert proto.check_request("modules.list", {"module": "x"})


def test_the_module_list_and_the_switch_and_a_refusal_with_its_reason(link):
    listed = link.modules_list()
    assert listed.ok and [m["name"] for m in listed.data["modules"]] == ["trees", "farms"]
    done = link.modules_set("farms", True)
    assert done.status == livelink.DONE and done.data == {"module": "farms", "on": True}
    assert link.game.modules[1]["on"] is True
    refused = link.modules_set("trees", False)
    assert refused.status == livelink.REFUSED and "layers" in refused.message
    assert ("modules.set", {"module": "trees", "on": False}) in link.game.requests


def test_the_panel_lists_each_module_ticked_when_it_runs_and_greyed_when_it_cannot_switch():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite import linkpanel

    data = {"modules": [*MODULES, {"class": "game"}]}  # one without a name: left out
    items = linkpanel.module_items(data)
    assert items == [
        ("trees", "trees (experimental)", True, False, "it places layers"),
        ("farms", "farms (dev)", False, True, ""),
    ]
    assert linkpanel.module_items({"modules": "x"}) == [] and linkpanel.module_items(None) == []
    qt = QApplication.instance() or QApplication([])
    dialog = linkpanel.LinkDialog()
    dialog._fill_modules(livelink.Reply(livelink.OK, "", data))
    trees, dump = dialog.modules.item(0), dialog.modules.item(1)
    assert trees.checkState() == Qt.CheckState.Checked and not trees.flags() & Qt.ItemFlag.ItemIsEnabled
    assert dump.checkState() == Qt.CheckState.Unchecked and dump.flags() & Qt.ItemFlag.ItemIsEnabled
    dialog.close()
    qt.processEvents()
