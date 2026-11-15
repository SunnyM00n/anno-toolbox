"""The live link's module requests on the suite's side (docs/live-link.md section 5): the closed arguments, the list and
the switch through the client, a refusal with the game's reason, and nothing sent without the debug commands' switch."""

from __future__ import annotations

from tests import fakegame
from tests.fakegame import FakeGame

from annotoolbox.suite import linkclient, livelink
from annotoolbox.suite import linkprotocol as proto


def connect(game: FakeGame):
    channel, game = fakegame.pair(game)
    link = linkclient.PipeLink(channel)
    assert link.open(fakegame.session_for(game, r"\\.\pipe\anno117-live-link-1"))
    return link, game


def test_the_two_requests_take_a_name_and_a_true_switch_only():
    assert proto.check_request("modules.list", {}) == ""
    assert proto.check_request("modules.set", {"module": "farm_growth", "on": False}) == ""
    for bad in ({"module": "farm_growth", "on": 1}, {"module": "a b", "on": True}, {"module": "x"}, {"on": True}):
        assert proto.check_request("modules.set", bad), bad
    assert proto.check_request("modules.list", {"module": "x"})


def test_the_list_and_the_switch_and_a_refusal_with_its_reason():
    link, game = connect(FakeGame(kinds=livelink.CAPABILITIES))
    game.modules = [
        {"name": "trees", "class": "experimental", "on": True, "switchable": False, "why": "it places layers"},
        {"name": "ui_dump", "class": "dev", "on": False, "switchable": True, "why": ""},
    ]
    try:
        listed = link.modules_list()
        assert listed.ok and [m["name"] for m in listed.data["modules"]] == ["trees", "ui_dump"]
        done = link.modules_set("ui_dump", True)
        assert done.status == livelink.DONE and done.data == {"module": "ui_dump", "on": True}
        assert game.modules[1]["on"] is True
        refused = link.modules_set("trees", False)
        assert refused.status == livelink.REFUSED and "layers" in refused.message
        assert ("modules.set", {"module": "trees", "on": False}) in game.requests
    finally:
        link.close()
        game.drop()


def test_a_game_without_the_debug_switch_gets_no_module_request():
    link, game = connect(FakeGame(kinds=livelink.KINDS))
    try:
        assert link.modules_set("ui_dump", True).status == livelink.UNAVAILABLE
        assert not [r for r in game.requests if r[0].startswith("modules.")]
    finally:
        link.close()
        game.drop()


def test_the_panel_lists_each_module_ticked_when_it_runs_and_greyed_when_it_cannot_switch():
    import pytest

    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite import linkpanel

    data = {
        "modules": [
            {"name": "trees", "class": "experimental", "on": True, "switchable": False, "why": "it places layers"},
            {"name": "ui_dump", "class": "dev", "on": False, "switchable": True, "why": ""},
            {"class": "game"},
        ]
    }
    items = linkpanel.module_items(data)
    assert items == [
        ("trees", "trees (experimental)", True, False, "it places layers"),
        ("ui_dump", "ui_dump (dev)", False, True, ""),
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
