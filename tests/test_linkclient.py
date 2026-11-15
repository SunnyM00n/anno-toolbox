"""The live link's wire protocol and the suite's client, against the stand-in for the game (``tests/fakegame.py``)."""

from __future__ import annotations

import sys
import threading
import time
import uuid

import pytest
from tests import fakegame
from tests.fakegame import FakeGame

from annotoolbox.sources import gametree
from annotoolbox.suite import linkclient, livelink
from annotoolbox.suite import linkprotocol as proto


def wait_for(condition, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


@pytest.fixture
def link():
    """A link connected to a stand-in game over a socket pair; the game is on ``link.game``."""
    channel, game = fakegame.pair()
    mine = linkclient.PipeLink(channel)
    assert mine.open(fakegame.session_for(game, r"\\.\pipe\anno117-live-link-1"))
    mine.game = game
    yield mine
    mine.close()
    game.drop()


# frames and the handshake


def test_a_frame_round_trips_and_a_reader_waits_for_the_rest_of_one():
    first, second = {"id": 1, "type": "ping", "args": {}}, {"event": "log", "data": {"text": "ü"}}
    data = proto.encode_frame(first) + proto.encode_frame(second)
    reader = proto.FrameReader()
    assert reader.feed(data[:3]) == [] and reader.feed(data[3:10]) == []
    assert reader.feed(data[10:]) == [first, second]
    assert data[:4] == len(proto.encode_frame(first)[4:]).to_bytes(4, "little")


def test_a_frame_over_the_limit_or_not_an_object_is_refused():
    with pytest.raises(proto.FrameError):
        proto.FrameReader().feed((proto.MAX_FRAME + 1).to_bytes(4, "little"))
    with pytest.raises(proto.FrameError):
        proto.encode_frame({"x": "a" * proto.MAX_FRAME})
    with pytest.raises(proto.FrameError):
        proto.FrameReader().feed(len(b"[1]").to_bytes(4, "little") + b"[1]")
    with pytest.raises(proto.FrameError):
        proto.FrameReader().feed(len(b"{x").to_bytes(4, "little") + b"{x")


def test_a_proof_is_bound_to_the_token_the_purpose_and_the_value():
    good = proto.proof("t" * 32, "client", "abc")
    assert proto.verify("t" * 32, "client", "abc", good) and proto.verify("t" * 32, "client", "abc", good.upper())
    for wrong in (("u" * 32, "client", "abc"), ("t" * 32, "game", "abc"), ("t" * 32, "client", "abd")):
        assert not proto.verify(*wrong, good)
    assert not proto.verify("t" * 32, "client", "abc", None) and len(good) == 64


def test_a_session_file_is_trusted_only_for_the_links_own_pipe(tmp_path):
    info = fakegame.session_for(FakeGame(), r"\\.\pipe\anno117-live-link-4711")
    path = fakegame.write_session(tmp_path, info)
    assert proto.read_session(tmp_path) == info and path.parent.name == "live-link"
    for bad in (
        '{"pipe": "\\\\\\\\.\\\\pipe\\\\other", "token": "' + "a" * 32 + '", "protocol": 1}',
        '{"pipe": "\\\\\\\\.\\\\pipe\\\\anno117-live-link-1", "token": "short", "protocol": 1}',
        '{"pipe": "\\\\\\\\.\\\\pipe\\\\anno117-live-link-1", "token": "' + "a" * 32 + '", "protocol": 2}',
        "[1]",
        "{",
    ):
        with pytest.raises(proto.SessionError):
            proto.parse_session(bad)
    path.write_text("{", encoding="utf-8")
    assert proto.read_session(tmp_path) is None
    assert proto.read_session(tmp_path / "nothing") is None


def test_a_request_is_checked_against_the_closed_message_set():
    ok = proto.check_request
    assert ok("reload.assets", {"mode": "full"}) == "" and ok("ping", {}) == ""
    assert ok("reload.models", {"mod": "m", "paths": ["data/a.rdm"]}) == ""
    assert ok("read", {"guid": 5, "fields": ["Building/BuildCost"]}) == ""
    assert "unknown request" in ok("shell", {})
    assert "missing" in ok("reload.models", {"mod": "m"})
    assert "unexpected" in ok("ping", {"x": 1})
    assert "paths" in ok("reload.models", {"paths": ["../outside.rdm"]})
    assert "paths" in ok("reload.models", {"paths": ["a b.rdm"]})
    assert "mode" in ok("reload.assets", {"mode": "rm -rf"})
    assert "mod" in ok("files.changed", {"mod": "a;b", "paths": ["a.xml"]})
    assert "topics" in ok("subscribe", {"topics": ["secrets"]})
    assert "guid" in ok("select", {"guid": -1}) and "guid" in ok("select", {"guid": True})
    assert "position" in ok("jump", {"position": [1, 2, "x"]})


def test_what_a_changed_file_needs_and_which_effects_include_a_header(tmp_path):
    kinds = {
        "data/a/house_lod0.rdm": livelink.MODELS, "data/a/house.cfg": livelink.MODELS,
        "data/shaders/fx/water.fx": livelink.EFFECTS, "data/config/export/main/asset/assets.xml": livelink.ASSETS,
        "data/config/gui/texts_english.xml": livelink.TEXTS, "data/ui/studio/generated/hudscene": livelink.SCENES,
        "data/graphics/t_diffuse.dds": proto.TEXTURE, "audio/bank.bnk": livelink.BANKS,
        "maps/island.a7m": livelink.SESSION, "readme.txt": proto.RESTART,
    }  # fmt: skip
    assert {p: proto.needs_of(p) for p in kinds} == kinds
    (tmp_path / "data/shaders/fx").mkdir(parents=True)
    (tmp_path / "data/shaders/fx/water.fx").write_text('#include "common/light.fxh"\n', encoding="utf-8")
    (tmp_path / "data/shaders/fx/sky.fx").write_text("// no include\n", encoding="utf-8")
    assert proto.effects_including(tmp_path, ["data/shaders/common/light.fxh"]) == ["data/shaders/fx/water.fx"]


# the client against the stand-in game


def test_a_handshake_gives_a_connected_link_that_knows_the_game(link):
    status = link.status()
    assert status.connected and status.game == "anno117" and status.build == "3.0"
    assert link.supports(livelink.MODELS) and link.mods == link.game.mods
    assert link.game.names() == ["subscribe"] and link.game.topics == {"selection", "reload", "session"}


def test_a_wrong_token_is_refused_and_a_game_that_cannot_prove_it_is_not_trusted():
    channel, game = fakegame.pair()
    mine = linkclient.PipeLink(channel)
    info = fakegame.session_for(game, r"\\.\pipe\anno117-live-link-1")
    wrong = proto.SessionInfo(info.pipe, "f" * 32, proto.PROTOCOL)
    assert not mine.open(wrong) and not mine.connected and not game.welcomed.is_set()
    channel, game = fakegame.pair(FakeGame(token="e" * 32))  # a game with another token greets us too
    assert not linkclient.PipeLink(channel).open(info)


def test_requests_get_their_answers_and_the_games_message_comes_back(link):
    assert link.reload(livelink.ASSETS, mode="full") == livelink.Reply(livelink.DONE, "", {"ms": 1})
    link.game.answers["reload.texts"] = (livelink.SKIPPED, "a session loads", {})
    reply = link.reload(livelink.TEXTS)
    assert reply.status == livelink.SKIPPED and reply.message == "a session loads" and not reply.ok
    link.game.answers["reload.models"] = (livelink.BUSY, "a loading screen", {})
    assert link.reload(livelink.MODELS, ["data/a.rdm"], "m").status == livelink.BUSY
    reply = link.files_changed(["data/a.rdm", "assets.xml", "x.txt"], "m")
    assert reply.data["needs"] == {"data/a.rdm": "models", "assets.xml": "assets", "x.txt": "restart"}
    assert link.read(guid=7, fields=["Building/BuildCost"]).ok and link.select(guid=7).ok
    assert link.jump(session="s1", position=(1.0, 2.0, 3.0)).ok


def test_a_request_the_client_would_send_wrong_is_refused_before_it_leaves(link):
    assert link.reload(livelink.MODELS, ["../x.rdm"]).status == livelink.REFUSED
    assert link.reload(livelink.ASSETS, mode="rm").status == livelink.REFUSED
    assert "reload.models" not in link.game.names()


def test_a_kind_the_game_cannot_serve_is_unavailable_and_not_sent():
    channel, game = fakegame.pair(FakeGame(kinds=(livelink.ASSETS,)))
    mine = linkclient.PipeLink(channel)
    assert mine.open(fakegame.session_for(game, r"\\.\pipe\anno117-live-link-1"))
    assert mine.reload(livelink.BANKS, mod="m", banks=["b"]).status == livelink.UNAVAILABLE
    assert "reload.banks" not in game.names()
    mine.close()


def test_a_header_named_for_an_effect_reload_becomes_the_effects_that_include_it(tmp_path):
    (tmp_path / "fx").mkdir()
    (tmp_path / "fx/water.fx").write_text('#include "light.fxh"\n', encoding="utf-8")
    channel, game = fakegame.pair(FakeGame(mods=[{"id": "m", "folder": str(tmp_path)}]))
    mine = linkclient.PipeLink(channel)
    assert mine.open(fakegame.session_for(game, r"\\.\pipe\anno117-live-link-1"))
    mine.reload(livelink.EFFECTS, ["fx/light.fxh", "fx/own.fx"], "m")
    assert game.requests[-1] == ("reload.effects", {"mod": "m", "paths": ["fx/own.fx", "fx/water.fx"]})
    mine.close()


def test_events_of_the_game_reach_the_handlers_of_the_current_link(link):
    seen, edits = [], []
    link.on(livelink.SELECTION, seen.append)
    link.on(livelink.EDIT_REQUEST, edits.append)
    link.game.push("selection", {"objects": [{"id": 4, "guid": 77}]})
    link.game.push("edit.request", {"guid": 77})
    link.game.push("log", {"text": "not subscribed"})
    assert wait_for(lambda: seen and edits)
    assert seen == [{"objects": [{"id": 4, "guid": 77}]}] and edits == [{"guid": 77}]
    link.on(livelink.LOG, seen.append)
    link.subscribe([livelink.LOG])
    link.game.push("log", {"text": "now"})
    assert wait_for(lambda: len(seen) == 2)


def test_a_handler_that_fails_does_not_stop_the_link(link):
    got = []
    link.on(livelink.SELECTION, lambda p: 1 / 0)
    link.on(livelink.SELECTION, got.append)
    link.game.push("selection", {"objects": []})
    assert wait_for(lambda: got) and link.reload(livelink.ASSETS).ok


def test_a_game_that_does_not_answer_times_out_and_a_late_answer_is_ignored(link, monkeypatch):
    monkeypatch.setattr(linkclient, "REQUEST_TIMEOUT", 0.2)
    link.game.delay["select"] = 0.5
    reply = link.select(guid=1)
    assert reply.status == livelink.UNAVAILABLE and "did not answer" in reply.message
    time.sleep(0.6)
    assert link.connected and link.read(guid=1, fields=["a/b"]).ok


def test_a_lost_connection_answers_the_waiting_and_marks_the_link_not_connected(link):
    states = []
    link.on_status(states.append)
    link.game.delay["reload.assets"] = 5
    result = []
    waiter = threading.Thread(target=lambda: result.append(link.reload(livelink.ASSETS)))
    waiter.start()
    assert wait_for(lambda: "reload.assets" in link.game.names())
    link.game.drop()
    waiter.join(3)
    assert result and result[0].status == livelink.NOT_CONNECTED
    assert wait_for(lambda: not link.connected) and states[-1].connected is False
    assert (
        link.reload(livelink.TEXTS).status == livelink.NOT_CONNECTED
        and "Shift+F3" in link.reload(livelink.TEXTS).message
    )


def test_a_game_that_stops_answering_pings_is_dropped(monkeypatch):
    monkeypatch.setattr(linkclient, "PING_AFTER", 0.1)
    monkeypatch.setattr(linkclient, "PING_WAIT", 0.2)
    channel, game = fakegame.pair()
    mine = linkclient.PipeLink(channel)
    assert mine.open(fakegame.session_for(game, r"\\.\pipe\anno117-live-link-1"))
    game.silent = True
    assert wait_for(lambda: not mine.connected, 3) and "ping" in game.names()


def test_requests_from_several_threads_each_get_their_own_answer(link):
    link.game.delay["read"] = 0.02
    replies = {}

    def ask(n):
        replies[n] = link.read(guid=n, fields=["a/b"])

    threads = [threading.Thread(target=ask, args=(n,)) for n in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(3)
    assert len(replies) == 8 and all(r.ok for r in replies.values())
    assert sorted(a["guid"] for n, a in link.game.requests if n == "read") == list(range(1, 9))


# finding the game


def test_connect_game_reads_the_session_file_and_the_registry_uses_it(tmp_path, monkeypatch):
    assert linkclient.connect_game(tmp_path) is None  # no game running
    channel, game = fakegame.pair()
    fakegame.write_session(tmp_path, fakegame.session_for(game, r"\\.\pipe\anno117-live-link-9"))
    opened = []
    found = linkclient.connect_game(tmp_path, opener=lambda name: opened.append(name) or channel)
    assert found.connected and opened == [r"\\.\pipe\anno117-live-link-9"]
    found.close()

    def gone(name):  # a session file left behind by a game that crashed
        raise linkclient.ChannelClosed(name)

    assert linkclient.connect_game(tmp_path, opener=gone) is None
    game.drop()
    # the registered connector finds the session file under the game's folder; its pipe is not there, so no link
    assert linkclient.connect_game in livelink._connectors
    monkeypatch.setattr(gametree, "user_dir", lambda key: tmp_path)
    livelink.reset()
    assert not livelink.connect().connected


def test_autoconnect_finds_a_game_that_starts_later_and_one_that_comes_back(monkeypatch):
    games = []

    def connector():
        if len(games) < 1 and not attempts:
            attempts.append(1)
            return None  # the first look: no game yet
        channel, game = fakegame.pair()
        link = linkclient.PipeLink(channel)
        link.open(fakegame.session_for(game, r"\\.\pipe\anno117-live-link-1"))
        games.append(game)
        return link

    attempts: list[int] = []
    monkeypatch.setattr(livelink, "_connectors", [connector])
    livelink.reset()
    auto = linkclient.AutoConnect(0.02).start()
    try:
        assert wait_for(lambda: livelink.current().connected)
        games[0].drop()
        assert wait_for(lambda: not livelink.current().connected)
        assert wait_for(lambda: livelink.current().connected and len(games) == 2)
    finally:
        auto.stop()
        getattr(livelink.current(), "close", lambda: None)()
        livelink.reset()


def test_autoconnect_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("ANNO_LIVE_LINK", "0")
    assert linkclient.autoconnect() is None


# the real pipe


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows named pipe")
def test_the_client_talks_to_a_named_pipe_server_with_events_and_a_dropped_end():
    name = rf"\\.\pipe\anno117-live-link-test{uuid.uuid4().hex[:12]}"
    game = FakeGame()
    accept = fakegame.pipe_server(game, name)
    channel = linkclient.PipeChannel.connect(name)
    accept.join(3)
    mine = linkclient.PipeLink(channel)
    assert mine.open(fakegame.session_for(game, name))
    assert mine.reload(livelink.ASSETS).ok and mine.files_changed(["a.rdm"], "mymod").ok
    seen = []
    mine.on(livelink.SELECTION, seen.append)
    game.push("selection", {"objects": []})
    assert wait_for(lambda: seen)
    game.drop()
    assert wait_for(lambda: not mine.connected)


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows named pipe")
def test_there_is_no_pipe_when_no_game_runs():
    with pytest.raises(linkclient.ChannelClosed):
        linkclient.PipeChannel.connect(r"\\.\pipe\anno117-live-link-nothing-here")


def test_a_long_list_of_files_goes_in_parts_and_the_answers_are_put_together(link):
    paths = [f"data/f{i}.rdm" for i in range(600)]
    reply = link.files_changed(paths, "m")
    assert reply.ok and len(reply.data["needs"]) == 600
    assert [len(a["paths"]) for n, a in link.game.requests if n == "files.changed"] == [256, 256, 88]
    link.game.answers["reload.models"] = (livelink.REFUSED, "a part was refused", {"files": [{"path": "x"}]})
    reply = link.reload(livelink.MODELS, paths, "m")
    assert reply.status == livelink.REFUSED and reply.message == "a part was refused" and len(reply.data["files"]) == 3


def test_a_path_the_game_would_cut_is_refused_before_it_is_sent(link):
    assert link.reload(livelink.MODELS, ["data/" + "a" * 200 + ".rdm"], "m").status == livelink.REFUSED
    assert "reload.models" not in link.game.names()


def test_a_scene_reload_names_the_states_of_the_changed_scene_files(link):
    from annotoolbox.suite import linkscenes

    assert linkscenes.scene_state("data/ui/studio/generated/maintitlescene") == "MainTitle"
    assert linkscenes.scene_state(r"data\ui\studio\generated\ConstructionMenuScene") == "ConstructionMenu"
    assert linkscenes.scene_state("data/ui/studio/generated/7e4af917-38da-5edf-a430-2fd1ffc2b4ad") == ""
    assert linkscenes.scene_state("data/ui/studio/generated/nosuchscene") == ""
    reply = link.reload(
        livelink.SCENES, ["data/ui/studio/generated/hudscene", "data/ui/studio/generated/hudscene"], "m"
    )
    assert reply.ok
    assert [a for n, a in link.game.requests if n == "reload.scenes"][-1]["states"] == ["HUD"]


def test_a_widget_file_reloads_the_states_a_registered_resolver_names_and_none_means_skipped(link):
    from annotoolbox.suite import linkscenes

    widget = "data/ui/studio/generated/7e4af917-38da-5edf-a430-2fd1ffc2b4ad"
    assert link.reload(livelink.SCENES, [widget], "m").status == livelink.SKIPPED  # nobody knows who shows it
    assert not [n for n, _a in link.game.requests if n == "reload.scenes"]
    forget = linkscenes.register(lambda paths: ["GameSetup", "MainTitle"] if widget in paths else [])
    try:
        assert link.reload(livelink.SCENES, [widget, "data/ui/studio/generated/maintitlescene"], "m").ok
    finally:
        forget()
    request = [a for n, a in link.game.requests if n == "reload.scenes"][-1]
    assert request["states"] == ["MainTitle", "GameSetup"]


def test_the_state_names_the_suite_knows_are_the_documented_ones():
    from pathlib import Path

    from annotoolbox.suite import linkscenes

    documented = Path(__file__).resolve().parents[1] / "docs" / "formats" / "ui-states.txt"
    if documented.is_file():
        names = {line.split("\t")[0] for line in documented.read_text(encoding="utf-8").splitlines() if line.strip()}
        assert names <= set(linkscenes.state_names().values())
    assert len(linkscenes.state_names()) == 160


def test_a_ui_dump_names_the_states_and_the_files_come_back(link):
    link.game.answers["ui.dump"] = (livelink.OK, "", {"files": ["a.json"]})
    assert link.ui_dump(["HUD"]).data == {"files": ["a.json"]}
    assert link.ui_dump().ok
    sent = [a for n, a in link.game.requests if n == "ui.dump"]
    assert sent == [{"states": ["HUD"]}, {}]
    assert link.ui_dump(["a b"]).status == livelink.REFUSED


def test_the_1800_hand_over_names_a_user_folder_with_other_letters_through_its_variable(tmp_path, monkeypatch):
    from annotoolbox.suite import linkhandover

    home = tmp_path / "Jörg" / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(home))
    bat = linkhandover.install(tmp_path / "Anno1800", home / "anno-toolbox")
    text = bat.read_bytes().decode("ascii")
    assert "%LOCALAPPDATA%\\anno-toolbox\\live-link\\edit-request-anno1800.json" in text and "Jörg" not in text
    monkeypatch.delenv("LOCALAPPDATA")
    for var in ("APPDATA", "USERPROFILE"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(linkhandover.UnsuitableFolder):  # an OSError: the link panel shows it
        linkhandover.install(tmp_path / "Other1800", home / "anno-toolbox")
    assert not (tmp_path / "Other1800").exists()


def test_the_1800_hand_over_leaves_a_guid_the_suite_turns_into_an_edit_request(tmp_path):
    from annotoolbox.suite import linkhandover

    game = tmp_path / "Anno1800"
    data = tmp_path / "data"
    with pytest.raises(linkhandover.UnsuitableFolder):  # the game starts the file by an unquoted command line
        linkhandover.install(tmp_path / "Anno 1800", data)
    assert not (tmp_path / "Anno 1800").exists()
    bat = linkhandover.install(game, data)
    text = bat.read_bytes().decode("ascii")
    assert bat == game / "Tools" / "ToolOne" / "launch.bat" and "%2" in text and "\r\n" in text
    assert text.rstrip().endswith("exit /b 0") and "@echo off" in text  # returns at once, prints nothing
    assert ">nul 2>&1" in text and text.count('>"') == 1  # the mkdir is silent; the only other output goes to the file
    assert linkhandover.install(game, data) == bat  # ours: written again
    other = tmp_path / "Other" / "Tools" / "ToolOne"
    other.mkdir(parents=True)
    (other / "launch.bat").write_text("@echo mine\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        linkhandover.install(tmp_path / "Other", data)
    assert not linkhandover.uninstall(tmp_path / "Other") and linkhandover.uninstall(game) and not bat.exists()
    request = linkhandover.request_file(data)
    request.parent.mkdir(parents=True)
    request.write_text('{"game":"anno1800","guid":1234}', encoding="utf-8")
    assert linkhandover.take_request(data) == {"game": "anno1800", "guid": 1234} and not request.exists()
    for bad in ('{"guid":"1; calc"}', '{"guid":-1}', "[1]", "not json"):
        request.write_text(bad, encoding="utf-8")
        assert linkhandover.take_request(data) is None
    seen = []
    livelink.use(livelink.NotConnected())
    livelink.on(livelink.EDIT_REQUEST, seen.append)
    request.write_text('{"guid":77}', encoding="utf-8")
    watcher = linkhandover.Watcher(data, interval=0.01).start()
    for _ in range(100):
        if seen:
            break
        time.sleep(0.02)
    watcher.stop()
    assert seen == [{"game": "anno1800", "guid": 77}]
    livelink.reset()


def test_a_changed_bank_file_is_asked_for_by_its_name_and_the_session_reload_takes_no_arguments(link):
    assert link.reload(livelink.BANKS, ["data/sound/MyBank.bnk", "data/sound/12345.wem"], "m").ok
    assert link.game.requests[-1] == ("reload.banks", {"banks": ["MyBank"], "mod": "m"})
    assert link.reload(livelink.BANKS, ["data/sound/12345.wem"], "m").status == livelink.REFUSED
    assert link.reload(livelink.SESSION).ok and link.game.requests[-1] == ("reload.session", {})
    assert link.select(guid=1010343).ok and link.jump(session="180023", position=(1.0, 2.5, -3.0)).ok
