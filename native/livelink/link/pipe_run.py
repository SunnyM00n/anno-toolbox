"""The live link's engine side against the suite's client over the real named pipe, the engine stubbed (build.cmd test).

    python pipe_run.py <livelink_pipe_test.exe> [anno1800 | anno2205]

livelink_pipe_test.c is mod_livelink.c with stand-ins for the library's services and the engine's calls; each scenario
starts it with a user folder of its own, switches the stand-ins with files there and asks through the client: the
confirmation on the engine's end line, the silent drop, busy and refusals, one request at a time, the stall warning.
It does not show that the engine does what the stand-ins say."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from annotoolbox.suite import debugcommands, linkclient, livelink  # noqa: E402
from annotoolbox.suite.livelink import Sent  # noqa: E402

failures: list[str] = []


def check(condition: bool, what: str) -> None:
    if not condition:
        failures.append(what)
        print(f"pipe_run: FAILED {what}")


class Game:
    """One run of the stand-in game: its stdout lines, its user folder, its link."""

    def __init__(self, exe: str, flags: tuple[str, ...] = (), game: str = "") -> None:
        self.dir = tempfile.TemporaryDirectory(prefix="livelink-pipe-")
        self.user = Path(self.dir.name)
        self.name = game
        for name in flags:
            (self.user / name).write_text("", encoding="ascii")
        self.lines: list[str] = []
        self.proc = subprocess.Popen(
            [exe, str(self.user)], stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
        )
        threading.Thread(target=self._read, daemon=True).start()
        self.link = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not any(line.startswith("READY") for line in self.lines):
            time.sleep(0.05)
        check(any(line.startswith("READY") for line in self.lines), "the stand-in game starts and opens its pipe")
        self.link = linkclient.connect_game(self.user, game=game)
        check(self.link is not None and self.link.connected, "the client finds the game through its session file")

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    def states(self) -> list[str]:
        return [line for line in self.lines if line.startswith("STATE")]

    def flag(self, name: str, on: bool = True) -> None:
        path = self.user / name
        if on:
            path.write_text("", encoding="ascii")
        elif path.exists():
            path.unlink()

    def stop(self) -> None:
        if self.link is not None:
            self.link.close()
        self.flag("quit.txt")
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()  # the process this run started itself
        self.dir.cleanup()


def run(exe: str, scenario, flags: tuple[str, ...] = (), game: str = "") -> None:
    game = Game(exe, flags, game)
    try:
        if game.link is not None:
            scenario(game, game.link)
    except Exception as error:  # the scenario failed in a way no check names
        check(False, f"{scenario.__name__}: {error!r}")
    finally:
        game.stop()


def confirmed_on_the_engines_end_line(game, link) -> None:
    reply = link.reload(livelink.ASSETS)
    check(
        reply.status == livelink.DONE,
        f"the pass is confirmed by the engine's end line ({reply.status}: {reply.message})",
    )
    check(
        reply.data.get("confirmed") is True and reply.data.get("staleObjects") is True,
        "the answer says confirmed and stale",
    )
    check("keep the data from before the reload" in reply.message, "the answer names what a reload cannot do")
    check(
        game.states() == ["STATE pending on", "STATE rebuild started", "STATE pending off"],
        f"pending from the request to the end line, held by the start line {game.states()}",
    )
    rebuilt = link.reload(livelink.ASSETS, mode="rebuild")  # the rebuild alone is confirmed the same way
    check(
        rebuilt.status == livelink.DONE and rebuilt.data.get("confirmed") is True,
        f"a rebuild alone is confirmed ({rebuilt})",
    )
    check(
        game.states()[-3:] == ["STATE pending on", "STATE rebuild started", "STATE pending off"],
        "pending around the rebuild too",
    )


def dropped_without_a_line(game, link) -> None:
    reply = link.reload(livelink.ASSETS)
    check(reply.status == livelink.SKIPPED and "did not start" in reply.message, f"a silent drop is skipped ({reply})")
    check(game.states()[-1] == "STATE pending off", "the lookups work again after a drop")


def busy_while_saving_and_loading(game, link) -> None:
    save = game.user / "accounts" / "p" / "q"
    save.mkdir(parents=True)
    (save / "auto.a8s").write_text("x", encoding="ascii")
    reply = link.reload(livelink.ASSETS)
    check(reply.status == livelink.BUSY and "saving" in reply.message, f"busy in a save's first seconds ({reply})")
    check(game.states() == [], "a refused request leaves the lookups alone")
    time.sleep(2.2)
    check(link.reload(livelink.ASSETS).status == livelink.DONE, "and the reload goes through once the save is old")
    game.flag("loading.txt")
    reply = link.reload(livelink.ASSETS)
    check(reply.status == livelink.BUSY and "loads" in reply.message, f"busy while a session loads ({reply})")
    game.flag("loading.txt", False)


def refused_in_multiplayer(game, link) -> None:
    reply = link.reload(livelink.ASSETS)
    check(reply.status == livelink.REFUSED, f"refused in a multiplayer game ({reply})")
    check(game.states() == [], "nothing was touched")


def one_request_at_a_time(game, link) -> None:
    first: list = []
    thread = threading.Thread(target=lambda: first.append(link.reload(livelink.ASSETS)))
    thread.start()
    time.sleep(0.3)
    second = link.reload(livelink.AMBIENT)
    check(second.status == livelink.BUSY, f"a second request waits outside ({second})")
    thread.join(30)
    check(bool(first) and first[0].status == livelink.DONE, "the first still ends confirmed")


def the_watchdog_tells_a_stall(game, link) -> None:
    seen: list = []
    link.on(livelink.STALLED, seen.append)
    result: list = []
    thread = threading.Thread(target=lambda: result.append(link.reload(livelink.ASSETS)))
    thread.start()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not seen:
        time.sleep(0.05)
    check(bool(seen) and seen[0].get("seconds", -1) >= 0, "the pipe thread reports the stall while the game blocks")
    check("hang" in link.status().text, f"the status carries the warning ({link.status().text})")
    thread.join(30)
    check(bool(result) and result[0].status == livelink.DONE, "the reload ends after the stall")
    check(len(seen) == 1, "the stall is reported once per request")


def lua_lines(game) -> list[str]:
    return [line for line in game.lines if line.startswith("LUA ")]


def speeds(game) -> list[str]:
    return [line for line in game.lines if line.startswith("GAME speed")]


def paused_for_the_pass(game, link) -> None:
    reply = link.reload(livelink.ASSETS)
    check(
        reply.status == livelink.DONE and reply.data.get("confirmed") is True,
        f"the paused reload is confirmed ({reply})",
    )
    check(
        reply.data.get("pausedByLink") is True and reply.data.get("resumeAsked") is True,
        f"the answer says so ({reply.data})",
    )
    check("runs again" in reply.message, f"and the message too ({reply.message})")
    ran = [line[4:] for line in lua_lines(game)]
    check(
        ran == ["Pause:Paused()", "Mods:LoadMods()", "Pause:FastForward()"],
        f"pause, the pass, then the speed the game had ({ran})",
    )
    check(speeds(game) == ["GAME speed 3", "GAME speed 5", "GAME speed 3"], f"the speeds {speeds(game)}")
    rebuilt = link.reload(livelink.ASSETS, mode="rebuild")
    check(
        rebuilt.status == livelink.DONE and rebuilt.data.get("pausedByLink") is True, f"a rebuild alone too ({rebuilt})"
    )


def left_alone_when_paused_or_outside_a_session(game, link) -> None:
    reply = link.reload(livelink.ASSETS)
    check(reply.status == livelink.DONE and "pausedByLink" not in reply.data, f"no pause of the link ({reply})")
    check(not any(line.startswith("LUA Pause:") for line in game.lines), "no speed button was pressed")


def the_pass_runs_when_the_pause_does_not_land(game, link) -> None:
    reply = link.reload(livelink.ASSETS)
    check(reply.status == livelink.DONE, f"the pass still runs ({reply})")
    check(reply.data.get("resumeAsked") is False, "the speed is not set when the game never paused")
    check(any("did not pause" in line for line in game.lines), "the log says the game did not pause")


def busy_while_a_scenario_action_waits(game, link) -> None:
    reply = link.reload(livelink.ASSETS)
    check(
        reply.status == livelink.BUSY and "scenario" in reply.message, f"busy over a pending scenario action ({reply})"
    )
    check(game.states() == [] and lua_lines(game) == [], "nothing ran")
    check(link.reload(livelink.AMBIENT).status != livelink.BUSY, "a request that keeps the database is not held")
    game.flag("scenario.txt", False)
    time.sleep(0.1)
    check(link.reload(livelink.ASSETS).status == livelink.DONE, "and the reload goes through once it is gone")


def debug_commands_run_from_their_table(game, link) -> None:
    check(link.supports(livelink.DEBUG), "the welcome names the debug capability when the second switch is on")
    listing = link.debug_list()
    commands = listing.data.get("commands", {})
    check(
        listing.ok and commands.get("ignore_building_costs") == {"available": True, "state": False},
        f"the list ({listing})",
    )
    check(
        commands.get("heal_selected", {}).get("available") is False, "a command the probe does not find is unavailable"
    )
    check(
        commands.get("unlock_all", {}).get("available") is False and len(commands) == len(debugcommands.commands()),
        "every id is listed",
    )
    check(listing.data.get("cheats") == ["Fast_Build", "Give-Money"], "the game's own cheats are listed")
    done = link.debug_run("ignore_building_costs")
    check(done.status == livelink.DONE and done.data == {"state": True}, f"a toggle comes back with its state ({done})")
    check(link.debug_run("fps_view").status == livelink.DONE, "a toggle without a state field is done")
    check(
        link.debug_run("destroy_selected").status == livelink.UNAVAILABLE,
        "a method the build does not bind is unavailable",
    )
    check(link.debug_run("heal_selected", {"percentage": 50}).status == livelink.DONE, "a command with a value")
    check(link.debug_cheat("Fast_Build").status == livelink.DONE, "a listed cheat runs")
    check(link.debug_cheat("Other").status == livelink.REFUSED, "a cheat the game does not list is refused")
    ran = " ".join(lua_lines(game))
    for wanted in (
        "pcall(function() Cheat.GlobalCheats:ToggleIgnoreBuildingCosts() end)",
        "pcall(function() Cheats:HealSelectedObjects(50) end)",
        'Cheat:TriggerCheatByName("Fast_Build")',
    ):
        check(wanted in ran, f"the Lua that ran holds {wanted}")
    check("Other" not in ran, "a refused cheat was not run")

    def raw(name: str, args: dict):
        """What the client's own checks would stop, sent anyway."""
        return link._transmit(Sent(name, args))

    before = len(lua_lines(game))
    for name, args in (
        ("debug.run", {"command": "os_execute"}),
        ("debug.run", {"command": "heal_selected", "values": {"percentage": 500}}),
        ("debug.run", {"command": "heal_selected", "values": {"amount": 5}}),
        ("debug.run", {"command": "invincible_selected", "values": {"value": 1}}),
        ("debug.run", {"command": "fps_view", "values": {"value": True}}),
        ("debug.run", {"command": "spawn_object", "values": {"guid": 1.5}}),
    ):
        reply = raw(name, args)
        check(reply.status == livelink.REFUSED, f"the game refuses {name} {args} ({reply})")
    check(len(lua_lines(game)) == before, "nothing refused reached the Lua runner")


def debug_table_equals_the_suites(exe: str) -> None:
    out = subprocess.run([exe, "--table"], capture_output=True, text=True, check=True).stdout.splitlines()
    kinds = {debugcommands.PERCENT: "1", debugcommands.GUID: "2", debugcommands.FLAG: "3"}
    mine = {}
    for command in debugcommands.commands().values():
        arg = command.args[0] if command.args else None
        mine[command.id] = (
            f"{command.id}|{command.call}|{kinds[arg.kind] if arg else 0}|{arg.name if arg else ''}|{command.state}|{int(command.confirm)}"
        )
    check(
        sorted(out) == sorted(mine.values()),
        f"the game's table is the suite's catalogue: {sorted(set(out) ^ set(mine.values()))[:4]}",
    )


def debug_commands_are_off_without_the_second_switch(game, link) -> None:
    check(not link.supports(livelink.DEBUG), "no debug capability without live_link_commands")
    reply = link._transmit(Sent("debug.list", {}))
    check(
        reply.status == livelink.UNAVAILABLE and "live_link_commands=1" in reply.message,
        f"a debug request is unavailable ({reply})",
    )
    check(lua_lines(game) == [], "nothing ran")


def debug_commands_are_refused_in_multiplayer(game, link) -> None:
    reply = link._transmit(Sent("debug.run", {"command": "fps_view"}))
    check(reply.status == livelink.REFUSED, f"refused in multiplayer ({reply})")
    check(lua_lines(game) == [], "nothing ran")


def debug_commands_wait_for_a_quiet_game(game, link) -> None:
    save = game.user / "accounts" / "p" / "q"
    save.mkdir(parents=True)
    (save / "auto.a8s").write_text("x", encoding="ascii")
    reply = link.debug_run("fps_view")
    check(reply.status == livelink.BUSY and "saving" in reply.message, f"busy in a save's first seconds ({reply})")
    game.flag("loading.txt")
    time.sleep(2.2)
    check(link.debug_run("fps_view").status == livelink.BUSY, "busy while a session loads")
    game.flag("loading.txt", False)
    check(link.debug_run("fps_view").status == livelink.DONE, "and it runs when the game is quiet")
    check(link.debug_list().ok, "the list is a read: no wait")


def modules_are_listed_and_switched(game, link) -> None:
    listing = link.modules_list()
    names = [m.get("name") for m in listing.data.get("modules", [])]
    check(listing.ok and names == ["trees", "farm_growth", "probe"], f"the module list ({listing})")
    first = listing.data["modules"][0] if names else {}
    check(first.get("class") == "experimental" and first.get("switchable") is False, f"class and switch ({first})")
    done = link.modules_set("probe", False)
    check(done.status == livelink.DONE and done.data == {"module": "probe", "on": False}, f"switched off ({done})")
    refused = link.modules_set("trees", False)
    check(refused.status == livelink.REFUSED and "cannot be switched" in refused.message, f"refused ({refused})")
    check(link.modules_set("nothing", True).status == livelink.REFUSED, "an unknown module is refused")


def modules_are_refused_in_multiplayer(game, link) -> None:
    reply = link._transmit(Sent("modules.set", {"module": "probe", "on": False}))
    check(reply.status == livelink.REFUSED and "multiplayer" in reply.message, f"refused in multiplayer ({reply})")


PANEL_FILE = "23222120-2524-2726-2829-2a2b2c2d2e2f"  # the widget file the stand-in's panel places
ANSWERED = {"guid", "crc", "name", "visible", "text", "image", "values"}  # all a widget of ui.values may hold


def live_values_of_the_open_file(game, link) -> None:
    check(link.supports(livelink.VALUES), "the welcome names the live values")
    scene = link.ui_values(["HUD"])
    widgets = (scene.data.get("scenes") or [{}])[0].get("widgets", [])
    check(scene.ok and [w.get("name") for w in widgets] == ["Title", "Panel"], f"the scene's own widgets ({scene})")
    check(widgets[0].get("text") == "Hello" and widgets[1].get("values") == {"7": 2.5}, f"what they show {widgets}")
    placed = link.ui_values(["HUD", "Pause"], file=PANEL_FILE)
    widgets = (placed.data.get("scenes") or [{}])[0].get("widgets", [])
    check(placed.ok and placed.data.get("file") == PANEL_FILE, f"the widget file's widgets ({placed})")
    check([w.get("name") for w in widgets] == ["Amount", "Icon"], f"only the file's own widgets {widgets}")
    check(widgets[1].get("visible") is False and widgets[1].get("image") == "icon_wood", f"a hidden image {widgets}")
    check(all(set(w) <= ANSWERED for w in widgets), "nothing but the shown values")
    check(link.ui_values(["Pause"]).status == livelink.SKIPPED, "a state whose scene is not open is skipped")
    check(link.ui_values(["Nothing"]).status == livelink.SKIPPED, "a name that is no state is skipped")
    other = link.ui_values(["HUD"], file="00000000-0000-0000-0000-000000000000")
    check(other.status == livelink.SKIPPED and "not placed" in other.message, f"a file the scene lacks ({other})")
    for args in ({"states": ["HUD"], "file": "not-a-guid"}, {"file": PANEL_FILE}):
        reply = link._transmit(Sent("ui.values", args))
        check(not reply.ok, f"the game refuses {args} ({reply})")
    asked = [line for line in game.lines if line.startswith("VALUES ")]
    check(asked == ["VALUES HUD"] * 2 + ["VALUES Pause"] * 2 + ["VALUES Nothing", "VALUES HUD"], f"walked {asked}")


def live_values_need_the_build_and_a_quiet_game(game, link) -> None:
    check(not link.supports(livelink.VALUES), "no live values when the widget classes do not match")
    reply = link._transmit(Sent("ui.values", {"states": ["HUD"]}))
    check(reply.status == livelink.UNAVAILABLE, f"unavailable on that build ({reply})")
    game.flag("novalues.txt", False)
    game.flag("loading.txt")
    reply = link._transmit(Sent("ui.values", {"states": ["HUD"]}))
    check(reply.status == livelink.BUSY, f"busy while a session loads ({reply})")
    check(not any(line.startswith("VALUES ") for line in game.lines), "no scene was walked")


def live_values_are_refused_in_multiplayer(game, link) -> None:
    reply = link.ui_values(["HUD"])
    check(reply.status == livelink.REFUSED and "multiplayer" in reply.message, f"refused in multiplayer ({reply})")


SCENE_KEYS = {"change", "state", "scene", "parent", "depth", "order", "window"}  # all a ui.scene event may hold


def open_scenes(game, *rows: str) -> None:
    """The stand-in's open scenes ("id depth name parent"), written whole so that the game never reads half."""
    part = game.user / "scenes.part"
    part.write_text("".join(f"{row}\n" for row in rows), encoding="ascii")
    part.replace(game.user / "scenes.txt")


def wait_for(seen: list, n: int, seconds: float = 3.0) -> list:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and len(seen) < n:
        time.sleep(0.05)
    return list(seen)


def following_the_open_scenes(game, link) -> None:
    check(link.supports(livelink.FOLLOW), "the welcome names following")
    seen: list = []
    link.on(livelink.SCENE, seen.append)
    open_scenes(game, "10 5 ConstructionMenu HUD", "6 2 Blur Game")
    time.sleep(0.4)
    check(seen == [], "no event before the subscription")
    check(link.subscribe([livelink.SCENE]).ok, "the topic is taken")
    first = wait_for(seen, 2)
    check([(e.get("change"), e.get("state"), e.get("order")) for e in first] == [("opened", "Blur", 1), ("opened", "ConstructionMenu", 2)],
          f"every open scene, parents first ({first})")  # fmt: skip
    check(bool(first) and first[-1].get("scene") == "constructionmenuscene" and first[-1].get("parent") == "HUD",
          f"its scene file and parent ({first})")  # fmt: skip
    check(all(set(e) <= SCENE_KEYS for e in first), "nothing but the scene's names")
    open_scenes(game, "10 5 ConstructionMenu HUD", "6 2 Blur Game", "51 5 HUDGovernorsBar HUD")
    later = wait_for(seen, 3)[2:]
    check([(e.get("change"), e.get("order")) for e in later] == [("opened", 3)], f"opened later, on top ({later})")
    open_scenes(game, "6 2 Blur Game", "51 5 HUDGovernorsBar HUD")
    gone = wait_for(seen, 4)[3:]
    check([(e.get("change"), e.get("state"), e.get("order")) for e in gone] == [("closed", "ConstructionMenu", 2)],
          f"closed, with its opening's number ({gone})")  # fmt: skip
    check(link.unsubscribe([livelink.SCENE]).ok, "unsubscribed")
    open_scenes(game)
    time.sleep(0.6)
    check(len(seen) == 4, f"no event after the unsubscribe ({seen[4:]})")
    open_scenes(game, "6 2 Blur Game")
    link.subscribe([livelink.SCENE])
    again = wait_for(seen, 5)[4:]
    check(
        [(e.get("change"), e.get("state")) for e in again] == [("opened", "Blur")], f"resubscribed: all again ({again})"
    )
    check(any("follow: 1 scene(s) open" in line for line in game.lines), "the log says what was open")


def following_needs_the_build(game, link) -> None:
    check(not link.supports(livelink.FOLLOW), "no following when the widget classes do not match")
    seen: list = []
    link.on(livelink.SCENE, seen.append)
    open_scenes(game, "6 2 Blur Game")
    check(link.subscribe([livelink.SCENE]).ok, "the topic is still taken")
    time.sleep(0.6)
    check(seen == [], "but no event comes")


def a_game_without_an_engine_side(game, link) -> None:
    """Anno 1800 and 2205 today (game.h, G_HAS_ENGINE 0): the link answers the handshake and ping with the game's own
    id and no reload kind, and everything else "unavailable"."""
    check(link.status().game == game.name, f"the welcome names the game ({link.status().game})")
    check(link.capabilities() == frozenset(), f"no kind of reload ({sorted(link.capabilities())})")
    check(link.welcome.get("kinds") == [] and link.welcome.get("mods") == [], f"an empty welcome ({link.welcome})")
    check(link._transmit(Sent("ping", {})).ok, "ping is answered")
    check(link.subscribe([livelink.SELECTION]).ok, "subscribing is answered")
    for name, args in (
        ("files.changed", {"paths": ["data/a.xml"]}),
        ("reload.assets", {}),
        ("reload.texts", {}),
        ("reload.session", {}),
        ("select", {"guid": 1}),
        ("jump", {"object": 1}),
        ("read", {"guid": 1, "fields": ["Building/BuildCost"]}),
        ("ui.values", {"states": ["HUD"]}),
        ("modules.list", {}),
    ):
        reply = link._transmit(Sent(name, args))
        check(
            reply.status == livelink.UNAVAILABLE and "not served" in reply.message,
            f"{name} is unavailable ({reply.status}: {reply.message})",
        )
    check(not any(line.startswith("STATE") for line in game.lines), "no engine call was made")


def main() -> int:
    if sys.platform != "win32":
        print("pipe_run: skipped (the pipe is a Windows one)")
        return 0
    exe = sys.argv[1]
    if len(sys.argv) > 2:  # the game's edition: only what a game with no engine side does
        run(exe, a_game_without_an_engine_side, game=sys.argv[2])
        print("pipe_run: ok" if not failures else f"pipe_run: {len(failures)} check(s) failed")
        return 1 if failures else 0
    run(exe, confirmed_on_the_engines_end_line)
    run(exe, dropped_without_a_line, ("drop.txt",))
    run(exe, busy_while_saving_and_loading)
    run(exe, refused_in_multiplayer, ("humans.txt",))
    run(exe, one_request_at_a_time)
    run(exe, the_watchdog_tells_a_stall, ("hang.txt",))
    run(exe, paused_for_the_pass, ("session.txt", "fast.txt"))
    run(exe, left_alone_when_paused_or_outside_a_session, ("session.txt", "paused.txt"))
    run(exe, left_alone_when_paused_or_outside_a_session)
    run(exe, the_pass_runs_when_the_pause_does_not_land, ("session.txt", "nopause.txt"))
    run(exe, busy_while_a_scenario_action_waits, ("scenario.txt",))
    debug_table_equals_the_suites(exe)
    run(exe, debug_commands_run_from_their_table, ("commands.txt",))
    run(exe, debug_commands_are_off_without_the_second_switch)
    run(exe, debug_commands_are_refused_in_multiplayer, ("commands.txt", "humans.txt"))
    run(exe, debug_commands_wait_for_a_quiet_game, ("commands.txt",))
    run(exe, modules_are_listed_and_switched, ("commands.txt",))
    run(exe, modules_are_refused_in_multiplayer, ("commands.txt", "humans.txt"))
    run(exe, live_values_of_the_open_file)
    run(exe, live_values_need_the_build_and_a_quiet_game, ("novalues.txt",))
    run(exe, live_values_are_refused_in_multiplayer, ("humans.txt",))
    run(exe, following_the_open_scenes)
    run(exe, following_needs_the_build, ("novalues.txt",))
    print("pipe_run: ok" if not failures else f"pipe_run: {len(failures)} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
