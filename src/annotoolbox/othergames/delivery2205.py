"""Anno 2205's archive index ``maindata/file.db`` as one delivery for every change of ours: the edited sessions and
the changed UI each lie in an archive of their own, and the index the game reads is always the game's own one
pointed at every archive of ours that is installed. So the two install and uninstall in any order.

``install`` puts one archive in and recomposes the index; ``uninstall`` takes one out and recomposes it from what is
left (the game's own index back when nothing is). The game's own index is kept once, outside the workspace
(``gamebackups``), freshly whenever the one in the game is not ours (the launcher repaired it).
docs/formats/anno2205-ui-format.md, "Getting a changed file into the game".
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..formats.rda import CRdaArchive
from ..infra.buildcache import copy_atomic, write_atomic
from . import gamebackups
from .index2205 import FILE_DB, CArchiveIndex

SESSIONS = "maindata/anno-toolbox-sessions.rda"  # the island editor's (and a project's) archive
UI = "maindata/flash-ui.rda"  # the UI editor's archive
ARCHIVES = (SESSIONS, UI)  # every archive of ours, in the order the index points at them (a later one wins)

Log = Callable[[str], None]


def backup_file(game: Path) -> Path:
    """Where the game's own ``file.db`` of the install ``game`` is kept."""
    return gamebackups.folder("anno2205", game) / "file.db"


def listed(index_raw: bytes) -> list[str]:
    """The archives of ours an index points at."""
    names = CArchiveIndex(index_raw).archive_names()
    return [archive for archive in ARCHIVES if archive in names]


def is_ours(index_raw: bytes) -> bool:
    return bool(listed(index_raw))


def is_games_own(path: Path) -> bool:
    """Whether the index file at ``path`` reads and names no archive of ours."""
    try:
        return not is_ours(path.read_bytes())
    except (OSError, ValueError):
        return False


def game_index(game: Path, old_backups: tuple[Path, ...] = ()) -> bytes:
    """The game's own ``file.db``: the one in ``game`` while it is not ours, else the kept copy. ``old_backups``: where
    an earlier version kept it (moved over when it is the game's own). A ``RuntimeError`` when there is none."""
    current = (game / FILE_DB).read_bytes()
    return current if not is_ours(current) else _kept(game, old_backups)


def _kept(game: Path, old_backups: tuple[Path, ...]) -> bytes:
    """The kept copy of the game's own index, an old one moved in first; refused when there is none."""
    backup = backup_file(game)
    for old in old_backups:
        gamebackups.moved_in(backup, old, is_games_own)
    if not backup.is_file():
        raise gamebackups.no_copy(game / FILE_DB, backup)
    return backup.read_bytes()


def compose(own: bytes, game: Path, archives: list[str]) -> bytes:
    """The game's own index pointed at every member of ``archives`` (archives of ours as they lie in ``game``)."""
    index = CArchiveIndex(own)
    for name in (archive for archive in ARCHIVES if archive in archives):
        for path, entry in CRdaArchive(game / name).entries.items():
            if index.entry(path) is None:
                raise ValueError(f"the game's archive index has no entry {path}: not a file of this install")
            index.point(path, name, entry)
    return index.write()


def install(game: Path, name: str, built: Path, log: Log = print, old_backups: tuple[Path, ...] = ()) -> None:
    """Put the archive ``built`` into ``game`` as our archive ``name`` and point the index at it and at the other
    archives of ours the index names now. The game's own index is kept first."""
    if name not in ARCHIVES:
        raise ValueError(f"{name} is not an archive of ours")
    if not built.is_file():
        raise RuntimeError("nothing built yet")
    current = (game / FILE_DB).read_bytes()
    installed = listed(current)  # the index is read once: the game's is tens of MB
    if not installed:  # the game's own (first install, or the launcher repaired it): kept afresh
        gamebackups.keep(game / FILE_DB, backup_file(game))
        log(f"kept the game's {FILE_DB} in {backup_file(game)}")
    own = _kept(game, old_backups) if installed else current
    others = [archive for archive in installed if archive != name and (game / archive).is_file()]
    copy_atomic(built, game / name)
    write_atomic(game / FILE_DB, compose(own, game, [*others, name]))
    log(f"installed {name} and {FILE_DB} into {game}")


def uninstall(game: Path, name: str, log: Log = print, old_backups: tuple[Path, ...] = ()) -> bool:
    """Take our archive ``name`` out of ``game``: the index points at the others of ours still installed, or is the
    game's own again when none is (the kept copy is then removed). False when the index does not name it."""
    installed = listed((game / FILE_DB).read_bytes())
    if name not in installed:  # the launcher repaired the index: an archive of ours may still lie there
        if (game / name).is_file():
            (game / name).unlink()
            log(f"removed {name}, which the index no longer names")
            return True
        log(f"{name} is not installed in {game}: nothing to undo")
        return False
    own = _kept(game, old_backups)
    rest = [archive for archive in installed if archive != name and (game / archive).is_file()]
    write_atomic(game / FILE_DB, compose(own, game, rest) if rest else own)
    (game / name).unlink(missing_ok=True)
    if not rest:
        backup_file(game).unlink(missing_ok=True)
    log(f"took {name} out of {game}" + ("" if rest else f"; the game's own {FILE_DB} is back"))
    return True
