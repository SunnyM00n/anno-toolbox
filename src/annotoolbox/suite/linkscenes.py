"""Which UI states a changed UI file reloads: what the live link's ``reload.scenes`` names (docs/live-link.md, "The scene
reload").

A scene file is named after its UI state (``data/ui/studio/generated/maintitlescene`` is the scene of ``MainTitle``), so
a scene is found by name. A widget file is named by its GUID and can sit in several scenes: the app that knows who
places it (the UI editor) registers a resolver with ``register``; without one a widget file names no state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "ui-states.txt"
SCENE_SUFFIX = "scene"

Resolver = Callable[[list[str]], Iterable[str]]  # changed UI file paths -> the names of the states that show them
_g_resolvers: list[Resolver] = []


@cache
def state_names() -> dict[str, str]:
    """The game's UI state names by their lower-case spelling (the one a scene file's name has)."""
    lines = DATA.read_text(encoding="utf-8").splitlines()
    return {line.lower(): line for line in map(str.strip, lines) if line}


def scene_state(path: str) -> str:
    """The state whose scene file is at ``path`` ("" when it is not one)."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not name.endswith(SCENE_SUFFIX) or "." in name:
        return ""
    return state_names().get(name[: -len(SCENE_SUFFIX)], "")


def register(resolver: Resolver) -> Callable[[], None]:
    """Let an app name the states of widget files it knows the users of; returns the remover."""
    _g_resolvers.append(resolver)
    return lambda: _g_resolvers.remove(resolver) if resolver in _g_resolvers else None


def states_for(paths: Iterable[str]) -> list[str]:
    """The states to reload for the changed UI files ``paths``: scenes by their name, the rest by the resolvers; each
    once, in order."""
    paths = list(paths)
    found = [scene_state(path) for path in paths]
    rest = [path for path, state in zip(paths, found, strict=True) if not state]
    for resolver in list(_g_resolvers):
        found += list(resolver(rest))
    return list(dict.fromkeys(entry for entry in found if entry))
