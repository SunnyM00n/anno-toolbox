"""Any installed Anno game as one tree of game paths: its archives in the game's own order, a mod folder or a folder of
loose files over them, and for each path the archive (or folder) it is read from.

``open_game(key, folder, cache)`` is the entry (117 by its archive map, 2070 by its overlay, the others by their
archive order: ``othergames.installs``); ``game_stamp`` says what changes with the game without opening it;
``GameTree.over(folder)`` lays a mod folder on top, ``loose_tree`` reads a plain folder (``local_of`` then gives a
path's real file). ``origins`` names every archive that carries a path (the winner first), ``deleted`` the paths 117's
archive map takes away. Paths are keys: lower case, forward slashes (``overlay.normalize``). docs/tool-suite.md 6.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable
from pathlib import Path

from ..othergames import installs, linuxfinder
from ..paths import documents_dir
from .overlay import normalize

GAMES = {
    "anno117": "Anno 117",
    "anno1800": "Anno 1800",
    "anno2205": "Anno 2205",
    "anno2070": "Anno 2070",
    "anno1404": "Anno 1404",
    "anno1701": "Anno 1701",
}
DOCUMENTS = {key: game.documents for key, game in installs.GAMES.items() if game.documents}  # under Documents
EXES = {key: game.exes[0] for key, game in installs.GAMES.items()}  # relative to the install


class GameTree:
    """A game's files by key: ``paths`` (sorted), ``exists``, ``read``, ``origin`` (where a path is read from);
    ``holders(key)`` lists every archive carrying a path (read order), ``deleted`` what the game has taken away."""

    def __init__(
        self,
        name: str,
        read: Callable[[str], bytes],
        paths: Iterable[str],
        origin: Callable[[str], str | None],
        game: str = "",
        holders: Callable[[str], list[str]] | None = None,
        deleted: Iterable[str] = (),
        stamp: str = "",
    ) -> None:
        self.name, self.game, self.stamp = name, game, stamp  # stamp: what changes when the files do
        self._read, self._origin, self._holders = read, origin, holders
        self.local_of: Callable[[str], Path | None] | None = None  # a key's real file, where the files are loose
        self.folder: Path | None = None  # the install it was opened from (its tools: the shader compiler)
        self.paths = sorted({normalize(p) for p in paths})
        self._known = set(self.paths)
        self.deleted = sorted({normalize(p) for p in deleted} - self._known)

    def exists(self, path: str) -> bool:
        return normalize(path) in self._known

    def read(self, path: str) -> bytes:
        if not self.exists(path):
            raise FileNotFoundError(path)
        return self._read(normalize(path))

    def origin(self, path: str) -> str | None:
        """The archive file (``data3.rda``) or the folder a path is read from; None when the game has no such file."""
        return self._origin(normalize(path)) if self.exists(path) else None

    def origins(self, path: str) -> list[str]:
        """Every archive (or folder) that carries ``path``: the one it is read from first, then the others it
        overrides, the latest first; a deleted path lists the archives that still carry it."""
        key = normalize(path)
        held = self._holders(key) if self._holders is not None else []
        winner = self.origin(key)
        rest = [h for h in reversed(held) if h != winner]
        return ([winner] if winner else []) + rest

    def local(self, path: str) -> Path | None:
        """The real file a path is read from when the tree is loose files (a folder, a mod folder), else None."""
        return self.local_of(normalize(path)) if self.local_of is not None and self.exists(path) else None

    def under(self, prefix: str) -> list[str]:
        """The keys below a folder (``data/graphics/``), in order."""
        prefix = normalize(prefix)
        return [p for p in self.paths if p.startswith(prefix)]

    def over(self, folder: Path, label: str = "") -> GameTree:
        """This tree with the loose files of ``folder`` (a mod folder: its files win) laid over it."""
        top = loose_tree(folder, label)
        merged = GameTree(
            f"{self.name} + {top.name}",
            lambda key: top.read(key) if top.exists(key) else self.read(key),
            [*self.paths, *top.paths],
            lambda key: top.origin(key) if top.exists(key) else self.origin(key),
            self.game,
            lambda key: [*self.origins(key)[::-1], *top.origins(key)],
            self.deleted,
            stamp_of([], f"{self.stamp}|{top.stamp}"),
        )
        merged.local_of = lambda key: top.local(key) if top.exists(key) else None
        return merged


def loose_tree(folder: Path, label: str = "") -> GameTree:
    """The files below ``folder`` by their path relative to it (a mod folder, an unpacked game, a test tree)."""
    root = Path(folder)
    files = {normalize(p.relative_to(root).as_posix()): p for p in sorted(root.rglob("*")) if p.is_file()}
    name = label or root.name
    tree = GameTree(name, lambda key: files[key].read_bytes(), files, lambda key: name, stamp=stamp_of(files.values()))
    tree.local_of = files.get
    return tree


def stamp_of(files: Iterable[Path], extra: str = "") -> str:
    """What changes when any of ``files`` does (their names, sizes and times), for an index kept beside a game."""
    parts = [extra] if extra else []
    for f in sorted(files):
        try:
            st = f.stat()
        except OSError:
            continue
        parts.append(f"{f.name}:{st.st_size}:{st.st_mtime_ns}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _record(key: str) -> installs.Game:
    if key not in GAMES:
        raise ValueError(f"unknown game {key!r}: one of {', '.join(GAMES)}")
    return installs.GAMES[key]


def user_dir(key: str) -> Path:
    """Where the game keeps its mods, logs and saves (the moved Documents folder included; off Windows the folder in
    the Wine prefix of an install found, ``othergames.linuxfinder``, else the one under Documents)."""
    if key not in DOCUMENTS:
        raise ValueError(f"{key} has no folder under Documents")
    if linuxfinder.applies() and (found := linuxfinder.user_dir(key)) is not None:
        return found
    return documents_dir() / DOCUMENTS[key]


def game_stamp(key: str, folder: Path) -> str:
    """What changes when the installed game ``key`` at ``folder`` does (its archives' names, sizes and times), read
    without opening an archive; a folder that is no install stamps its loose files."""
    folder = Path(folder)
    game = _record(key)
    archives = game.archives(folder) if game.present(folder) else []
    return stamp_of(a for a in archives if a.is_file()) if archives else stamp_of(_some_files(folder))


LOOSE_OPEN_FILES = 200_000  # the most a folder that is no install may hold to be read as loose files
LOOSE_STAMP_FILES = 2000  # a folder that is no install is stamped by its first files only: never a whole game folder


def _some_files(folder: Path, cap: int = LOOSE_STAMP_FILES) -> list[Path]:
    """Up to ``cap`` files below ``folder``, breadth first, so the top of a tree is always in."""
    found: list[Path] = []
    queue = [folder]
    while queue and len(found) < cap:
        try:
            with os.scandir(queue.pop(0)) as entries:
                for e in sorted(entries, key=lambda x: x.name):
                    if e.is_dir(follow_symlinks=False):
                        queue.append(Path(e.path))
                    elif e.is_file() and len(found) < cap:
                        found.append(Path(e.path))
        except OSError:
            continue
    return found


def open_game(key: str, folder: Path, cache: Path | None = None) -> GameTree:
    """The installed game ``key`` (a key of ``GAMES``) at ``folder``; a folder without the game's archives is read as
    loose files. Opening indexes every archive (117: a few seconds)."""
    folder = Path(folder)
    game = _record(key)
    if not game.present(folder):
        if len(_some_files(folder, LOOSE_OPEN_FILES + 1)) > LOOSE_OPEN_FILES:  # a drive root, a home folder
            raise ValueError(f"{folder} is no {key} install and holds more than {LOOSE_OPEN_FILES} files: not read")
        return _named(loose_tree(folder), key)
    index = game.index(folder, cache)
    stamp = game_stamp(key, folder)
    if hasattr(index, "holders"):  # 117's archive map
        found = GameTree(
            GAMES[key], index.read, index.iter_paths(""), index.archive_of, key, index.holders, index.deleted(), stamp
        )
    else:  # an overlay of archives
        found = GameTree(
            GAMES[key], index.read_bytes, index.iter_paths(), _archive_file(index), key, _candidates(index), (), stamp
        )
    found.folder = folder
    return found


def _archive_file(tree) -> Callable[[str], str | None]:
    def origin(key: str) -> str | None:
        found = tree.archive_file(key)
        return found.name if found is not None else None

    return origin


def _candidates(tree) -> Callable[[str], list[str]]:
    """Every archive of an overlay carrying a path, lowest priority first (``SourceTree.candidates``)."""

    def holders(key: str) -> list[str]:
        return [where.split("!", 1)[0] if "!" in where else name for name, where in tree.candidates(key)]

    return holders


def _named(tree: GameTree, key: str) -> GameTree:
    tree.game = key
    return tree
