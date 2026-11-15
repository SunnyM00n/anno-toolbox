"""Whether the game is running, and what a profile change means then.

The loader reads the profile when the game starts and again at a hot reload; a hot reload (Shift+F3 with the hot
reload mod) takes the **active** profile and the game rewrites that file, so a change made to another profile does
nothing there and a change to the active one can be written over. ``state`` finds the game by its process (and the
live link), ``change_warning`` says it, ``reload_in_game`` asks the running game to reload through the suite's live
link (``suite.livelink``). ``docs/mod-manager.md`` "The game is running"; ``docs/engine-modloader.md`` "Reloading while
the game runs". Stdlib only besides the link.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..infra import tools
from ..infra.i18n import tr
from ..sources.gametree import EXES, GAMES
from ..suite import livelink


@dataclass(frozen=True)
class GameState:
    game: str
    running: bool  # a process of the game is there
    linked: bool  # the live link has a game connected

    @property
    def name(self) -> str:
        return GAMES.get(self.game, self.game)

    @property
    def busy(self) -> bool:
        return self.running or self.linked


def system_lister() -> set[str]:
    """The running processes by the system's own list (a ``tasklist`` call of about half a second). Tests replace this
    seam (``tests/conftest.py``) so none of them runs it; the window calls it off its thread."""
    return tools.process_names()


def state(game: str, lister: Callable[[], set[str]] | None = None) -> GameState:
    lister = lister or system_lister
    exe = EXES.get(game, "").rsplit("/", 1)[-1].lower()
    link = livelink.current().status()
    names_it = not link.game or GAMES.get(game, game).lower() in link.game.lower()  # a link to another game: no
    return GameState(game, bool(exe) and exe in lister(), link.connected and names_it)


def change_warning(now: GameState, editing: str, active: str) -> str:
    """The sentence to show before a profile change while the game runs ("" when it does not)."""
    if not now.busy:
        return ""
    text = tr("{game} is running: a profile change takes effect at its next start.", game=now.name)
    text += " " + tr(
        "A hot reload (Shift+F3) loads the active profile ({active}) and rewrites its file.",
        active=active or tr("none"),
    )
    if editing and editing != active:
        text += " " + tr("You are editing {name}, which is not the active one: a reload will not use it.", name=editing)
    return text


def reload_in_game(mod: str = "") -> livelink.Reply:
    """Ask the running game to reload the assets and texts of its mods (the loader's hot reload). Without a link the
    reply says what to do by hand."""
    return livelink.current().reload(livelink.ASSETS, mod=mod, mode="full")


def reload_text(reply: livelink.Reply, active: str) -> str:
    """What to tell the person about a reload's reply, the active-profile trap included."""
    trap = " " + tr("It reloads the active profile ({active}), not the one open here.", active=active or tr("none"))
    if reply.ok:
        return tr("Reload asked: {message}.", message=reply.message or reply.status) + trap
    return f"{reply.message or reply.status}.{trap}"
