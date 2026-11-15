"""The Anno games the editor and the tools open: how an install is told apart, its engine generation, its
archives in override order, its index (``Game.index``), where its islands and its Documents folder are, which
editor opens the islands.

The generation is the studio's own numbering (1701 is 3, 117 is 8) and splits the games into two
engine families, ``OLD`` (3-5: RDA up to V2.0, islands as tagged text) and ``NEW`` (6-8: RDA V2.2,
level data in FileDB). A game whose islands no editor reads yet is still listed (``editor`` None), so
the start page can say so (island-editor "Other games"). 117 and 2070 have records like the others.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


def _numbered(folder: Path, stem: str = "data") -> list[Path]:
    """``<stem>0.rda``, ``<stem>1.rda``, ... in number order (a text sort puts data10 before data2)."""
    found = [(int(m[1]), p) for p in folder.glob(f"{stem}*.rda") if (m := re.fullmatch(rf"{stem}(\d+)", p.stem))]
    return [p for _, p in sorted(found)]


def _archives_1404(root: Path) -> list[Path]:
    # the base game, then the add-on (Venice: its files under addondata/), then the History Edition's own
    return (
        _numbered(root / "maindata")
        + [root / "maindata" / "eng0.rda"]
        + _numbered(root / "addon")
        + [root / "addon" / "eng0.rda", root / "ahe" / "a4he0.rda"]
    )


def _archives_1701(root: Path) -> list[Path]:
    return _numbered(root) + sorted(root.glob("data*_eng.rda")) + [root / "ahe" / "a3he0.rda"]


def _archives_numbered(root: Path) -> list[Path]:
    return _numbered(root / "maindata")


def _archives_117(root: Path) -> list[Path]:
    maindata = root / "maindata"
    return [*maindata.glob("*.rda"), *maindata.glob("*.db")] if maindata.is_dir() else []


def _archives_2070(root: Path) -> list[Path]:
    from ..sources.overlay import archive_root

    return (
        [a for a in archive_root(root).iterdir() if a.suffix.lower() == ".rda"] if (root / "maindata").is_dir() else []
    )


def _index_117(folder: Path, cache: Path | None, replace=None):
    from ..sources.anno117 import Anno117Files

    return Anno117Files(folder, cache_dir=cache)


def _index_2070(folder: Path, cache: Path | None, replace=None):
    from ..sources.overlay import SourceTree

    return SourceTree(folder, cache_dir=cache)


OLD, NEW = "old", "new"  # engine families: tagged-text islands / FileDB level data


def family(engine: int) -> str:
    return OLD if engine <= 5 else NEW


@dataclass(frozen=True)
class Game:
    key: str
    name: str
    engine: int  # the generation (module docstring)
    exes: tuple[str, ...]  # any of these, relative to the install, tells the game
    archives: object  # install folder -> archive files, earliest first
    islands: tuple[str, ...]  # path prefixes of its islands
    suffix: str
    editor: str | None  # the editor kind that opens its islands, None while none reads them
    documents: str = ""  # the game's folder under the user's Documents (mods, logs, saves), "" where it has none
    needs: tuple[str, ...] = ()  # more files an install holds besides one of ``exes`` (2070: its first archive)
    archive_dir: str = ""  # a folder that holds the archives and tells the game from loose files, "" for the exe test
    opener: object = None  # (folder, cache, replace) -> the game's own index, None for an archive list
    island_folder: str = ""  # a folder name an island's path must hold (117 keeps other .a7m files in data/)
    mod_loader: bool = False  # whether the game loads mods from a folder (117, 1800); the others read archives

    @property
    def family(self) -> str:
        return family(self.engine)

    @property
    def granny(self) -> bool:
        """Whether its models are Granny files (1404, 1701) and not RDM."""
        return self.engine <= 4

    @property
    def edit_prefix(self) -> str:
        """The start of the edit files of its islands: island-edits/<year>_<island>.json (``117_``, ``2070_``)."""
        return self.key.removeprefix("anno") + "_"

    def is_install(self, folder: Path) -> bool:
        return any((folder / exe).is_file() for exe in self.exes) and all((folder / n).is_file() for n in self.needs)

    def present(self, folder: Path) -> bool:
        """Whether ``folder`` holds the game's archives (else it is read as loose files)."""
        return (folder / self.archive_dir).is_dir() if self.archive_dir else self.is_install(folder)

    def index(self, folder: Path, cache: Path | None = None, replace=None):
        """The game's files as it sees them: 117's archive map, 2070's overlay, the others' archives in order.
        ``replace``: archive file -> the file to read instead, or None to leave it out (the archives the island
        editor wrote are read as the game's own: ``editor/oldenginebuild``); ``cache`` where an index is kept."""
        folder = Path(folder)
        if self.opener is not None:
            return self.opener(folder, cache, replace)
        if self.key == "anno1701":  # its own rule: the newer of two copies wins (isd1701)
            from .isd1701 import source_tree

            return source_tree(folder, replace=replace)
        from ..sources.overlay import SourceTree

        files = self.archives(folder)
        return SourceTree.from_archives([r for f in files if (r := replace(f) if replace else f) is not None], cache)

    def tree(self, folder: Path, replace=None):
        """``index`` without a cache folder, as the island editor and its tests ask for it."""
        return self.index(folder, replace=replace)


# The key order of ``sources.gametree.GAMES``. 117 and 2070 are the editor's own games: their ``editor`` is the
# kind of its own island editor, not a viewer of ``editor.gameviewers``.
GAMES = {
    g.key: g
    for g in (
        Game(
            "anno117",
            "Anno 117",
            8,
            ("Bin/Win64/Anno117.exe",),
            _archives_117,
            ("data/",),
            ".a7m",
            "117",
            documents="Anno 117 - Pax Romana",
            mod_loader=True,
            archive_dir="maindata",
            opener=_index_117,
            island_folder="/islands/",
        ),
        Game(
            "anno1800",
            "Anno 1800",
            7,
            ("Bin/Win64/Anno1800.exe",),
            _archives_numbered,
            ("data/",),  # data/sessions/islands and the DLCs' data/dlcNN/...
            ".a7m",
            "a7m",  # FileDB, all three layouts (filedb, editor.formats.a7m.reader)
            documents="Anno 1800",
            mod_loader=True,
        ),
        Game(
            "anno2205",
            "Anno 2205",
            6,
            ("Bin/Win64/Anno2205.exe",),
            _archives_numbered,
            ("data/",),
            ".a6m",
            "a6m",  # whole session maps (editor.formats.a7m.session): terrain, ground and objects
        ),
        Game(
            "anno2070",
            "Anno 2070",
            5,
            ("Anno5.exe",),
            _archives_2070,
            ("data/levels/islands/",),
            ".isd",
            "2070",
            needs=("maindata/data0.rda",),  # 2205 has a maindata/data0.rda too
            archive_dir="maindata",
            opener=_index_2070,
        ),
        Game(
            "anno1404",
            "Anno 1404 (with Venice)",
            4,
            ("Anno1404Addon.exe", "Anno1404.exe"),
            _archives_1404,
            ("data/levels/islands/", "addondata/levels/islands/"),
            ".isd",
            "isd",  # 2070's island format: isd2070 reads it as it is
        ),
        Game(
            "anno1701",
            "Anno 1701",
            3,
            ("Anno1701.exe",),
            _archives_1701,
            ("data/levels/islands/",),  # data/levels/testlevel holds 856 developer islands
            ".isd",
            "isd1701",  # the old engine's oldest dialect of the tag tree (isd1701)
        ),
    )
}


def keys(test: Callable[[Game], bool]) -> tuple[str, ...]:
    """The keys of the games that pass ``test``, in the order of ``GAMES``."""
    return tuple(key for key, game in GAMES.items() if test(game))


def detect(candidates: list[Path]) -> dict[str, str]:
    """The installs among ``candidates``, per game key (the first found)."""
    found = dict.fromkeys(GAMES, "")
    for folder in candidates:
        for key, game in GAMES.items():
            try:
                if not found[key] and game.is_install(folder):
                    found[key] = str(folder)
            except OSError:
                continue
    return found


def islands(game: Game, tree) -> list[str]:
    """The game's island files, as game paths."""
    found = {p for prefix in game.islands for p in tree.iter_paths(prefix, suffix=game.suffix)}
    return sorted(p for p in found if game.island_folder in p)
