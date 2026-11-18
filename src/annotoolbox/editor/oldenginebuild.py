"""The old-engine games' builds: edited 1404 and 1701 islands put where the games load them over the original.

1404 has no patch archive: it mounts ``maindata/data0..5`` and its language archive, then (Venice) ``addon/data0..5``
and its language archive, a later archive replacing earlier files path by path (engine-notes "Anno 1404 archives").
An edited island goes into the last ``data`` archive of the group its winning copy comes from, rewritten with its own
files; the game's copy is kept outside the workspace (``othergames.gamebackups``). 1701 mounts ``data<n>.rda`` for n = 0, 1, ... up to the first gap and
keeps the newer copy of a file, so the edited islands are one new ``data<n>.rda`` whose files are newer than the
originals (``isd1701``). ``edited_*`` and ``archives_*`` only return bytes; ``install_archives`` writes into the game, ``restore`` undoes it.
"""

from __future__ import annotations

import functools
import re
import time
from collections.abc import Callable
from pathlib import Path

from ..formats.rda import RdaArchive, write_v11, write_v20
from ..infra.buildcache import copy_atomic, write_atomic
from ..infra.i18n import tr
from ..islands import islandedits
from ..othergames import gamebackups, grid1701, installs, isd1701
from .editchange import count_line
from .formats.isc import edit as isc_edit
from .formats.isd.build import MARKER, edited_files, is_ours

PREFIXES = {"anno1404": "1404_", "anno1701": "1701_"}  # the edit files: island-edits/<year>_<island>.json


def backup_name(game_key: str, archive: Path) -> str:
    return f"{game_key}-{archive.parent.name}-{archive.name}"


def _data_archives(game_key: str, install: Path) -> list[Path]:
    """The ``data<n>`` archives the game mounts, in order."""
    files = installs.GAMES[game_key].archives(install)
    return [f for f in files if f.is_file() and re.fullmatch(r"data\d+", f.stem)]


def last_archives(game_key: str, install: Path) -> list[Path]:
    """The archives the editor may have rewritten or added: 1404's last ``data`` archive of each group, 1701's last."""
    if game_key == "anno1701":
        return isd1701.archives(install)[-1:]
    last: dict[Path, Path] = {}
    for f in _data_archives(game_key, install):
        last[f.parent] = f
    return list(last.values())


def replacement(game_key: str, install: Path, backup_dir: Path) -> Callable[[Path], Path | None]:
    """For ``Game.tree``: a 1404 archive of ours is read as the game's own (its backup), 1701's added archive is left
    out, so the editor and a build always start from the game's files. Refused when a 1404 archive of ours has no
    backup: the files the game had in it would be missing from every build."""
    ours = {f for f in last_archives(game_key, install) if is_ours(f)}
    for archive in ours if game_key != "anno1701" else ():
        if not (backup := backup_dir / backup_name(game_key, archive)).is_file():
            raise gamebackups.no_copy(archive, backup)

    def replace(archive: Path) -> Path | None:
        if archive not in ours:
            return archive
        return None if game_key == "anno1701" else backup_dir / backup_name(game_key, archive)

    return replace


def _stamps(archive: RdaArchive) -> dict[str, int]:
    return {e.path: e.mtime for e in archive}


def _rewritten(original: Path | None, extra: dict[str, bytes], islands: list[str], stamp: int) -> bytes:
    """A 1404 archive: the original's files, ours over them, and the marker (which islands it carries)."""
    files: dict[str, bytes] = {}
    stamps: dict[str, int] = {}
    if original is not None:
        arc = RdaArchive(original)
        files, stamps = {e.path: arc.read_entry(e) for e in arc if e.path != MARKER}, _stamps(arc)
    for path in extra:
        for old in [p for p in files if p.lower() == path.lower()]:
            del files[old]
    files |= extra
    files[MARKER] = ("\r\n".join(islands) + "\r\n").encode("utf-8")
    return write_v20(files, {**stamps, **dict.fromkeys(extra, stamp), MARKER: stamp})


def edited_1404(tree, edits_dir: Path, log: Callable[[str], None] = print) -> tuple[dict[str, bytes], list[str]]:
    """The edited 1404 islands as files (game path -> bytes) and their paths; ``tree``: the game's files
    (``Game.tree`` with ``replacement``)."""
    return edited_files(tree, edits_dir, _types(tree, log), log, prefix=PREFIXES["anno1404"])


def _types(tree, log: Callable[[str], None]) -> dict:
    """1404's assets as 2070's reader takes them (what a placed object is a copy of); none without the asset file."""
    from ..islands.objects2070 import read_types

    try:
        return read_types(tree)
    except (KeyError, FileNotFoundError, OSError) as exc:
        log(tr("islands.no_asset_file_placed_objects", error=exc))
        return {}


def archives_1404(
    tree, install: Path, files: dict[str, bytes], written: list[str], replace, log: Callable[[str], None] = print
) -> dict[str, bytes]:
    """The archives that carry ``files`` (game path -> bytes): each goes into the last ``data`` archive of the group
    its winning copy is read from. ``written``: the islands among them, for the marker."""
    last = {f.parent: f for f in last_archives("anno1404", install)}
    mounted = {replace(f): f for f in _data_archives("anno1404", install) if replace(f) is not None}
    targets: dict[Path, dict[str, bytes]] = {}
    for path, data in files.items():
        winner = mounted.get(tree.archive_file(path))
        if winner is None or winner.parent not in last:
            raise ValueError(
                tr(
                    "islands.read_not_data_archive_game",
                    path=path,
                    archive=tree.archive_file(path),
                )
            )
        targets.setdefault(last[winner.parent], {})[path] = data
    out = {}
    stamp = int(time.time())
    for target, extra in targets.items():
        islands = [p for p in written if p in extra]
        out[target.relative_to(install).as_posix()] = _rewritten(replace(target), extra, islands, stamp)
        log(tr("islands.name_folder_n_files", name=target.name, folder=target.parent.name, n=len(extra)))
    return out


def next_archive_1701(install: Path) -> str:
    """The name of the archive the editor's islands go in: the one it wrote before, else the first number the game
    does not mount (``data<n>.rda`` with n one past the last)."""
    mounted = isd1701.archives(install)
    if mounted and is_ours(mounted[-1]):
        return mounted[-1].name
    return f"data{len(mounted)}.rda"


def edited_1701(tree, edits_dir: Path, log: Callable[[str], None] = print) -> tuple[dict[str, bytes], list[str]]:
    """The edited 1701 islands as files (game path -> bytes) and their paths."""
    files: dict[str, bytes] = {}
    written = []
    shapes = functools.cache(
        lambda: grid1701.scan_shapes(tree)
    )  # the shipped islands' footprints, made if one is needed
    for path in sorted(edits_dir.glob(f"{PREFIXES['anno1701']}*.json")):
        edits = islandedits.load(path)
        source = edits.get("source")
        entry = tree.entry_of(source) if source else None
        if entry is None:
            log(tr("islands.no_source_island_skipped", name=path.name))
            continue
        isl = isd1701.load(tree, source)
        report = isc_edit.apply(isl, edits, shapes)
        files[entry.path] = isl.write()
        files |= _changed_header(tree, source, isl)
        written.append(entry.path)
        log(f"{source}: " + count_line(report))
        for miss in report["unmatched"]:
            log(tr("islands.not_found_name", name=miss))
    return files, written


def _changed_header(tree, source: str, isl: isd1701.Island1701) -> dict[str, bytes]:
    """The island's ``.isc`` (rivers, coast lines, properties) as a file of the build, only when an edit changed it."""
    header = source[: -len(".isd")] + ".isc"
    entry, data = tree.entry_of(header), isl.write_header()
    return {entry.path: data} if entry is not None and data is not None and data != tree.read_bytes(header) else {}


def archives_1701(tree, install: Path, files: dict[str, bytes], written: list[str]) -> dict[str, bytes]:
    """The new archive (name -> bytes) that carries ``files``, newer than the originals they replace."""
    newest = max((tree.entry_of(p).mtime for p in written), default=0)
    stamp = max(int(time.time()), newest + 1)  # the newer copy wins, whatever the archive
    files = {**files, MARKER: ("\r\n".join(written) + "\r\n").encode("utf-8")}
    return {next_archive_1701(install): write_v11(files, stamp)}


def install_archives(
    game_key: str, install: Path, archives: dict[str, bytes], backup_dir: Path, log: Callable[[str], None] = print
) -> list[Path]:
    """Write the archives into the game. The game's own file is kept in ``backup_dir`` first (again whenever it is
    not ours: an update or a repair replaced it), and replaced through a temporary name. Every target is checked
    before the first is written: a 1404 archive of ours without the game's copy refuses the whole install."""
    for rel in archives:
        target = install / rel
        backup = backup_dir / backup_name(game_key, target)
        if target.is_file() and not is_ours(target):
            gamebackups.keep(target, backup)
            log(tr("islands.kept_games_file_backup", file=rel, backup=backup))
        elif target.is_file() and game_key != "anno1701" and not backup.is_file():
            raise gamebackups.no_copy(target, backup)
    out = []
    for rel, data in archives.items():
        target = install / rel
        write_atomic(target, data)
        log(tr("islands.wrote_target", target=target))
        out.append(target)
    return out


def restore(game_key: str, install: Path, backup_dir: Path, log: Callable[[str], None] = print) -> bool:
    """Undo ``install_archives``: 1404's rewritten archives get the game's own back, 1701's added archive is removed.
    False when nothing of ours is installed."""
    done = False
    for target in last_archives(game_key, install):
        if not is_ours(target):
            continue
        if game_key == "anno1701":
            target.unlink()  # a file the editor added; the game never had it
        else:
            backup = backup_dir / backup_name(game_key, target)
            if not backup.is_file():
                raise FileNotFoundError(
                    tr(
                        "islands.no_copy_games_verify_files",
                        file=target.name,
                        backup=backup,
                    )
                )
            copy_atomic(backup, target)
        log(tr("islands.restored_target", target=target))
        done = True
    if not done:
        log(tr("islands.nothing_editors_installed"))
    return done
