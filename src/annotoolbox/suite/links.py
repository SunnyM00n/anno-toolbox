"""Links between the suite's apps: one app asks another to show a file, an asset or a model without importing it.

An app that can do something provides it under a link's name (``provide``; the suite's window does it for every app
from ``AppSpec.links``), another one asks for it (``follow``) and shows the button only when someone provides it
(``available``). Alone in a window of its own an app finds no provider and hides what needs one.
docs/tool-suite.md section 19, "Links between the apps".
"""

from __future__ import annotations

from collections.abc import Callable

VIEW_FILE = "view_file"  # (game, path): show a game file in the viewer
EDIT_OBJECT = "edit_object"  # (game, path): open a model config in the object builder
SHOW_ASSET = "show_asset"  # (game, guid): show an asset in the asset editor
PLACE_MODEL = "place_model"  # (game, cfg): pick a model config for placing on the open island
PLACE_ASSET = "place_asset"  # (game, guid): pick an asset (a building, any object with a model) for placing
OPEN_ISLAND = "open_island"  # (game, island): open an island in the island editor (the game's edit request)
GAME_SELECTION = "game_selection"  # (selection): the objects selected in the running game (the live link)
EXPORT_FILES = "export_files"  # (game, [paths]): export game files (as stored, converted) from the viewer
ASSET_FILES = "asset_files"  # (game) -> the asset files' name patterns, in load order (the asset editor's layout)
DIFF_MODS = "diff_mods"  # (mode, game, a, b, profile): ask the Mod diff a question about mods (``moddiff.jobs.Job``)
SHOW_MODS = "show_mods"  # (game, [mod ids]): show a game's installed mods in the Mods app, the first one selected
EDIT_EFFECT = "edit_effect"  # (game, path, model config or ""): open an effect file in the particle editor
MODE_MOD, MODE_PROFILE = (
    "mod",
    "profile",
)  # the modes of that question another app asks (``moddiff.jobs.MOD``, ``PROFILE``)

_providers: dict[str, Callable] = {}


def provide(link: str, handler: Callable) -> None:
    """``handler`` answers ``link`` from now on (the last one provided wins)."""
    _providers[link] = handler


def withdraw(link: str, handler: Callable | None = None) -> None:
    """Stop answering ``link``; with ``handler``, only when that one is still the provider."""
    if handler is None or _providers.get(link) is handler:
        _providers.pop(link, None)


def available(link: str) -> bool:
    return link in _providers


def follow(link: str, *args):
    """What the provider of ``link`` returns for ``args``; None when nobody provides it."""
    handler = _providers.get(link)
    return handler(*args) if handler is not None else None


def export_files(game: str, paths: list[str]) -> bool:
    """Export game files from the viewer (its dialog asks for the forms and the folder); False when no viewer is there."""
    return bool(follow(EXPORT_FILES, game, paths))


def view_file(game: str, path: str) -> bool:
    """Show a game file in the viewer; False when no viewer is there."""
    return bool(follow(VIEW_FILE, game, path))


def diff_mods(mode: str, game: str, a: str = "", b: str = "", profile: str = "") -> bool:
    """Open a question about mods in the Mod diff (``mode``: ``moddiff.jobs.MODES``) and run it; False when no Mod diff is
    there."""
    return bool(follow(DIFF_MODS, mode, game, a, b, profile))


def show_mods(game: str, mod_ids: list[str]) -> bool:
    """Show a game's mods in the Mods app with the first of ``mod_ids`` selected; False when no Mods app is there."""
    return bool(follow(SHOW_MODS, game, list(mod_ids)))
