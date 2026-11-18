"""Where the island editor and the UI editor keep a game's own files before an archive or an index of theirs replaces
them: one folder per install under the tool's data folder, never in the workspace, so a changed workspace setting
cannot lose the copy a restore needs.

``folder`` is the place of one install's copies, ``keep`` takes a fresh copy of a game file (checked whole before
anything is changed), ``moved_in`` carries a copy an earlier version kept elsewhere (the workspace) over once.
docs/island-editor-tools.md "Build, install, restore".
"""

from __future__ import annotations

import filecmp
import hashlib
import os
from collections.abc import Callable
from pathlib import Path

from ..infra.buildcache import copy_atomic
from ..infra.i18n import tr
from ..paths import local_data_dir

ENV_VAR = "ANNO_GAME_BACKUPS"  # moves the folder (the tests point it at a temporary one)


def root() -> Path:
    return Path(os.environ.get(ENV_VAR) or local_data_dir() / "game-backups")


def folder(key: str, install: Path) -> Path:
    """The copies of one install's files: by the game and its folder, so two installs never share a copy."""
    where = os.path.normcase(os.path.abspath(install))
    return root() / f"{key}-{hashlib.sha1(where.encode('utf-8')).hexdigest()[:10]}"


def keep(target: Path, backup: Path) -> None:
    """Copy the game's ``target`` to ``backup``; an ``OSError`` when the copy is not whole (nothing else is changed)."""
    copy_atomic(target, backup)
    if not filecmp.cmp(target, backup, shallow=False):
        backup.unlink(missing_ok=True)
        raise OSError(tr("islands.copy_incomplete_nothing_changed", target=target, backup=backup))


def no_copy(target: Path, backup: Path) -> RuntimeError:
    """The refusal when a file of ours lies in the game and the game's own copy is missing: building or installing
    on it would lose the game's files."""
    return RuntimeError(
        tr(
            "islands.editors_no_copy_games_own",
            target=target,
            backup=backup,
        )
    )


def moved_in(new: Path, old: Path, usable: Callable[[Path], bool] = lambda _p: True) -> Path:
    """``new``, after a copy an earlier version kept at ``old`` was moved there (copied, compared, then removed)
    when ``new`` has none; ``usable`` says whether the old copy is the game's own at all."""
    if new.is_file() or not old.is_file() or not usable(old):
        return new
    keep(old, new)
    old.unlink()
    return new
