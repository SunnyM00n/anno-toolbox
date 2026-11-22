"""Sources from an installed game and the rule for targets: a game's files listed and copied out as they are stored
(their game paths kept, so the kind of a language text or an island stays known), and no target inside a game.

``open_tree`` opens a game (slow: off the window's thread), ``paths`` lists its files of some suffixes, ``extract``
writes the chosen ones below a fresh folder of the workspace; ``refused_target`` says why a target folder may not be
written. No Qt. docs/converter-app.md, "Sources and targets".
"""

from __future__ import annotations

import time
from pathlib import Path, PurePosixPath

from ..othergames import installs
from ..sources import gametree

FOLDER = "converter"  # the app's folder in the workspace and in a project


def open_tree(settings, game: str) -> gametree.CGameTree:
    """The installed game ``game`` of the settings, with the suite's index of its archives."""
    folder = getattr(settings, game, "")
    if not folder or not Path(folder).is_dir():
        raise FileNotFoundError(folder or game)
    return gametree.open_game(game, Path(folder), settings.index_dir(game))


def paths(tree, suffixes: tuple[str, ...], query: str = "", limit: int = 400) -> list[str]:
    """The game's paths with one of ``suffixes`` (any when empty) that contain every word of ``query``."""
    words = query.lower().split()
    found = []
    for path in tree.m_paths:
        if suffixes and not path.endswith(suffixes):
            continue
        if all(word in path for word in words):
            found.append(path)
            if len(found) >= limit:
                break
    return found


def fresh_folder(settings, game: str) -> Path:
    """A new folder below the workspace for one pick of a game's files."""
    root = Path(settings.workspace) / FOLDER / "game-files" / game
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for attempt in range(1, 1000):
        folder = root / (stamp if attempt == 1 else f"{stamp}-{attempt}")
        if not folder.exists():
            return folder
    raise FileExistsError(root)


def extract(tree, chosen: list[str], folder: Path) -> list[Path]:
    """Write each of ``chosen`` below ``folder`` at its game path, as stored; a path that would leave it is refused."""
    written = []
    for path in chosen:
        relative_path = PurePosixPath(path)
        if relative_path.is_absolute() or ".." in relative_path.parts or ":" in path:
            raise ValueError(f"{path}: not a path inside the game")
        target = folder.joinpath(*relative_path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(tree.read(path))
        written.append(target)
    return written


def game_folders(settings) -> list[tuple[str, Path]]:
    """Every folder of a game the settings know: its install, and where it keeps its mods, saves and logs."""
    out = []
    for key in installs.GAMES:
        if getattr(settings, key, ""):
            out.append((key, Path(getattr(settings, key))))
        try:
            out.append((key, gametree.user_dir(key)))
        except (ValueError, OSError):
            pass
    if getattr(settings, "mods", ""):
        out.append(("anno117", Path(settings.mods)))
    return out


def refused_target(out: Path, settings) -> str:
    """The game whose folder ``out`` lies in ("" when it lies in none): the app writes into a folder of the person's
    own or the project, never into a game."""
    target = out.resolve()
    for key, folder in game_folders(settings):
        try:
            if target.is_relative_to(folder.resolve()):
                return gametree.GAMES.get(key, key)
        except OSError:
            continue
    return ""
