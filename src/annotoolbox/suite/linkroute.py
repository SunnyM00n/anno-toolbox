"""The game to editor direction: what the player picks or asks for in the game opens in the app that edits it.

``attach(call)`` listens to the current link's ``edit.request`` and ``selection`` events (``livelink.on``) and routes
them through ``suite.links`` (``SHOW_ASSET``, ``VIEW_FILE``, ``OPEN_ISLAND``, ``GAME_SELECTION``), so no app is
imported; an app that provides none of them is simply not asked. ``call`` runs the routing on the window's thread (a
Qt signal's ``emit``). docs/live-link.md, "Toolbox side".
"""

from __future__ import annotations

from collections.abc import Callable

from . import links, livelink

DEFAULT_GAME = "anno117"


def open_in_editor(request: dict) -> str:
    """Show what an ``edit.request`` names (``guid``, ``file``, ``island``; ``game`` else the connected game's) in the
    app that provides it. Returns what was opened ("asset", "file", "island") or "" when nothing could be."""
    game = request.get("game") or livelink.current().status().game or DEFAULT_GAME
    guid = str(request.get("guid", "")).strip()
    if guid.isdecimal() and links.follow(links.SHOW_ASSET, game, int(guid)):  # a GUID that is no number names nothing
        return "asset"
    if request.get("file") and links.follow(links.VIEW_FILE, game, str(request["file"])):
        return "file"
    if request.get("island") and links.follow(links.OPEN_ISLAND, game, str(request["island"])):
        return "island"
    return ""


def show_selection(selection: dict) -> bool:
    """Hand the game's selection to the app that follows it (``GAME_SELECTION``); False when none does."""
    return bool(links.follow(links.GAME_SELECTION, selection))


def attach(call: Callable[[Callable, dict], None] | None = None) -> Callable[[], None]:
    """Route the link's events from now on; returns the function that stops it. ``call(handler, payload)`` runs a
    handler on the right thread (default: here)."""
    run = call or (lambda handler, payload: handler(payload))
    removers = [
        livelink.on(livelink.EDIT_REQUEST, lambda payload: run(open_in_editor, payload)),
        livelink.on(livelink.SELECTION, lambda payload: run(show_selection, payload)),
    ]

    def detach() -> None:
        for remove in removers:
            remove()

    return detach
