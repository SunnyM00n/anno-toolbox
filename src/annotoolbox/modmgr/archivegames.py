"""The archive games' side of the mod manager (2205, 2070, 1404, 1701), Qt-free: what the Toolbox put into each install
and how to take it out (``rows``, ``restore``), and loose-file mods through ``engine.ini`` ``PreferLocalFiles``
(``CLooseGame``: the switch, the install of a folder of loose files, its removal).

These games have no mod folder; the island delivery (``editor.archivedelivery``) rewrites or adds an archive and keeps
the game's own file. Loose files are the alternative that touches no archive; whether each game reads them is
**not confirmed**, so ``LOOSE_CONFIRMED`` is empty and the window offers the actions only for a game listed there
(docs/mod-manager.md "Installs and loose files" says what to check). Stdlib only.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..infra import buildcache
from ..infra.i18n import N_, tr
from ..paths import documents_dir

Log = Callable[[str], None]

GAMES = ("anno2205", "anno2070", "anno1404", "anno1701")

# A game key -> the date the tester saw a loose file win over the archive in that game. Add a game here, and only
# then, after the check in docs/mod-manager.md; the window's loose-file actions follow this table.
LOOSE_CONFIRMED: dict[str, str] = {}

# The folder under Documents that holds ``engine.ini`` (2205's was seen; the others are the games' usual names and
# the first one that holds the file is used).
USER_FOLDERS = {
    "anno2205": ("Anno 2205",),
    "anno2070": ("Anno 2070", "ANNO 2070"),
    "anno1404": ("Anno 1404 History Edition", "Anno 1404", "ANNO 1404"),
    "anno1701": ("Anno 1701 History Edition", "Anno 1701", "ANNO 1701"),
}
INI_NAMES = ("config/engine.ini", "Engine.ini", "engine.ini")
FLAG = re.compile(r'(?P<head>["<]?PreferLocalFiles["]?\s*[:=>]?\s*)(?P<value>true|false|0|1)\b', re.IGNORECASE)
LOOSE_DIR = "loose-mods"  # in the workspace: one folder per game, a manifest per mod, the files it replaced
INI_KEPT = "engine.ini-original"
ON_WORDS = {"true", "1"}


class CLooseError(RuntimeError):
    """A loose-file action that cannot be done; the sentence says why."""


# What the editor installed


@dataclass
class SInstallRow:
    key: str
    state: str  # "installed" (ours is in the game), "clean", "no folder"
    what: str = ""  # what the Toolbox installs there, in the interface language
    files: list[Path] = field(default_factory=list)  # ours, in the install
    kept: list[Path] = field(default_factory=list)  # the game's own files kept for the restore (the ones that exist)
    restore_label: str = ""

    @property
    def installed(self) -> bool:
        return self.state == "installed"


def _target(key: str, settings) -> Path:
    """The folder the editor writes into: 2205 has its own setting, the others use their install."""
    from ..editor import archivedelivery

    return archivedelivery.target_2205(settings) if key == "anno2205" else Path(getattr(settings, key))


def _ours_and_kept(key: str, settings) -> tuple[list[Path], list[Path]]:
    from ..editor import archivedelivery as ad
    from ..editor import oldenginebuild
    from ..editor.formats.a7m import edit2205 as a7m_edit2205
    from ..editor.formats.isd import build as isd_build

    folder = _target(key, settings)
    if key == "anno2070":
        return [folder / isd_build.PATCH], [ad.patch_backup(settings)]
    if key == "anno2205":
        return [folder / a7m_edit2205.ARCHIVE, folder / "maindata" / "file.db"], [ad.index_backup(settings)]
    ours = [file for file in oldenginebuild.last_archives(key, folder) if oldenginebuild.is_ours(file)]
    backups = [ad.old_engine_backups(settings, key) / oldenginebuild.backup_name(key, file) for file in ours]
    return ours, backups


def rows(settings) -> list[SInstallRow]:
    """One row per archive game: whether an archive of the editor is installed, which files, which copies of the game's
    own are kept (the restore needs them)."""
    from ..editor import archivedelivery

    out = []
    for key in GAMES:
        delivery = archivedelivery.ISLAND_DELIVERIES[key]
        folder = str(getattr(settings, key, "") or "").strip()
        if not folder or not delivery.game.is_install(Path(folder)):
            out.append(SInstallRow(key, "no folder"))
            continue
        try:
            ours = delivery.is_ours(settings)
        except (OSError, ValueError, RuntimeError):
            ours = False
        if not ours:
            out.append(SInstallRow(key, "clean", tr(delivery.what)))
            continue
        files, backups = _ours_and_kept(key, settings)
        out.append(
            SInstallRow(
                key,
                "installed",
                tr(delivery.what),
                files,
                [backup for backup in backups if backup.is_file()],
                tr(delivery.restore_label),
            )
        )
    return out


def restore(settings, key: str, log: Log = print) -> bool:
    """Put the game's own files back (the editor's own restore); False when nothing of ours was installed."""
    from ..editor import archivedelivery

    return bool(archivedelivery.ISLAND_DELIVERIES[key].restore(settings, log))


# engine.ini and PreferLocalFiles


def engine_ini(key: str) -> Path | None:
    """The game's ``engine.ini`` under Documents, None while the game has not written one (start it once)."""
    for folder in USER_FOLDERS[key]:
        for name in INI_NAMES:
            path = documents_dir() / folder / name
            if path.is_file():
                return path
    return None


def _decode(raw: bytes) -> tuple[str, str]:
    """The text and the encoding it was in (a byte order mark decides; else UTF-8, else Latin-1, which every byte
    sequence reads and writes back unchanged), so a write puts the file back the way it was."""
    if raw[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return raw.decode("utf-16"), "utf-16"
    if raw[:3] == codecs.BOM_UTF8:
        return raw.decode("utf-8-sig"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeError:
        return raw.decode("latin-1"), "latin-1"


def prefer_local(path: Path) -> bool | None:
    """The file's ``PreferLocalFiles`` value; None when the file does not name the key."""
    match = FLAG.search(_decode(path.read_bytes())[0])
    return None if match is None else match["value"].lower() in ON_WORDS


def with_flag(text: str, is_enabled: bool) -> str:
    """``text`` with the key set (the file's own spelling, ``true``/``false`` or ``1``/``0``, kept)."""
    match = FLAG.search(text)
    if match is None:
        raise CLooseError(tr("common.engine_ini_not_name_preferlocalfiles"))
    word = match["value"].lower() in ("true", "false")
    new = ("true" if is_enabled else "false") if word else ("1" if is_enabled else "0")
    return text[: match.start("value")] + new + text[match.end("value") :]


# Loose-file mods


def _sha1(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha1").hexdigest()


def _safe(relative_path: str) -> str:
    """A relative path that stays inside the install, with forward slashes. A drive, a stream name (``a:b``), a
    character Windows reads differently and a name that ends in a dot or a space (Windows drops it) are refused."""
    normalized = relative_path.replace("\\", "/")
    parts = normalized.split("/")
    bad = any(x in ("", ".", "..") or x.endswith((".", " ")) or set(x) & set(':<>"|?*') for x in parts)
    if normalized.startswith("/") or bad:
        raise CLooseError(tr("common.not_stay_inside_games_folder", path=relative_path))
    return normalized


def _inside(root: Path, path: Path) -> Path:
    """``path`` when it really lies under ``root`` (a junction or link in between would lead out of it)."""
    if root.resolve() not in path.resolve().parents:
        raise CLooseError(tr("common.not_stay_inside_games_folder", path=path))
    return path


@dataclass(frozen=True)
class SLooseMod:
    name: str
    installed: str  # the date, "YYYY-MM-DD HH:MM"
    files: tuple[str, ...]
    replaced: int  # files of the game or of an earlier mod that were kept and come back on removal


class CLooseGame:
    """One game's loose-file mods: files copied under the install, a manifest per mod in the workspace (what was
    copied, with its hash, and the files that were there before), and the ``engine.ini`` switch. Nothing is written
    for a game that ``LOOSE_CONFIRMED`` does not list unless ``force`` is set (a test, or the check)."""

    def __init__(self, key: str, install: Path, workspace: Path, force: bool = False, ini: Path | None = None) -> None:
        self.m_key, self.m_install = key, Path(install)
        self.m_root = Path(workspace) / LOOSE_DIR / key
        self.m_force = force
        self._m_ini = ini

    @property
    def confirmed(self) -> bool:
        return self.m_force or self.m_key in LOOSE_CONFIRMED

    def _need_confirmed(self) -> None:
        if not self.confirmed:
            raise CLooseError(tr("common.loose_files_not_confirmed_game"))

    # the switch

    @property
    def ini(self) -> Path | None:
        return self._m_ini or engine_ini(self.m_key)

    def switch_state(self) -> bool | None:
        ini = self.ini
        return None if ini is None else prefer_local(ini)

    def set_switch(self, is_on: bool, log: Log = print) -> None:
        """PreferLocalFiles on or off in ``engine.ini``; the file as it was is kept once, in the workspace."""
        self._need_confirmed()
        ini = self.ini
        if ini is None:
            raise CLooseError(tr("common.no_engine_ini_found_game"))
        before = ini.read_bytes()
        text, enc = _decode(before)
        new = with_flag(text, is_on)
        kept = self.m_root / INI_KEPT
        if not kept.exists():
            buildcache.write_atomic(kept, before)
            log(tr("common.kept_file_as_backup", file=ini.name, backup=kept))
        buildcache.write_atomic(ini, new.encode(enc))
        if prefer_local(ini) is not is_on:
            buildcache.write_atomic(ini, before)  # the file as this call found it, not the first kept state
            raise CLooseError(tr("common.engine_ini_not_take_change"))
        log(tr("common.preferlocalfiles_state_file", state=tr("mods.on") if is_on else tr("mods.off"), file=ini))

    # the mods

    def _manifest(self, name: str) -> Path:
        return self.m_root / "mods" / f"{name}.json"

    def mods(self) -> list[SLooseMod]:
        out = []
        for path in sorted((self.m_root / "mods").glob("*.json")):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
                out.append(
                    SLooseMod(
                        manifest["name"], manifest["installed"], tuple(manifest["files"]), len(manifest["replaced"])
                    )
                )
            except (OSError, ValueError, KeyError, TypeError):  # TypeError: JSON, but not a manifest
                continue
        return out

    def install_mod(self, source: Path, name: str = "", log: Log = print) -> SLooseMod:
        """Copy every file of ``source`` (a folder laid out like the install: ``data/...``) under the install. A file
        already there is kept for the removal; a path another loose mod owns is refused."""
        self._need_confirmed()
        source = Path(source)
        name = name or source.name
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", name):
            raise CLooseError(tr("common.not_plain_name", name=name))
        found = sorted(file_path for file_path in source.rglob("*") if file_path.is_file())
        if not found:
            raise CLooseError(tr("common.holds_no_files", folder=source))
        rels = {file_path: _safe(file_path.relative_to(source).as_posix()) for file_path in found}
        owned = {
            file_name.lower(): installed_mod.name
            for installed_mod in self.mods()
            if installed_mod.name != name
            for file_name in installed_mod.files
        }
        clash = sorted(
            target_path.lower() for target_path in rels.values() if target_path.lower() in owned
        )  # the games' paths ignore case
        if clash:
            raise CLooseError(tr("common.belongs_loose_mod", path=clash[0], mod=owned[clash[0]]))
        if self._manifest(name).is_file():
            self.remove_mod(name, log)
        replaced: dict[str, str] = {}
        done: dict[str, str] = {}
        try:
            for file_path, relative_path in rels.items():
                self._put(file_path, relative_path, name, replaced)
                done[relative_path] = _sha1(self.m_install / relative_path)
        except BaseException:  # half an install would have no manifest to take it out by
            self._take_out(name, done, replaced)
            shutil.rmtree(
                self.m_root / "originals" / name, ignore_errors=True
            )  # the file that failed was never touched
            raise
        installed = datetime.now().strftime("%Y-%m-%d %H:%M")
        manifest = {"name": name, "installed": installed, "files": done, "replaced": replaced}
        buildcache.write_kept(self._manifest(name), manifest)
        log(tr("common.installed_name_n_files", name=name, n=len(done)))
        return SLooseMod(name, installed, tuple(done), len(replaced))

    def _put(self, source: Path, relative_path: str, name: str, replaced: dict[str, str]) -> None:
        """One file into the install; what stood there is copied to the workspace first (noted in ``replaced``)."""
        target = _inside(self.m_install, self.m_install / relative_path)
        if target.is_dir():
            raise CLooseError(tr("common.folder_game_not_file", path=relative_path))
        if target.is_file():
            keep = self.m_root / "originals" / name / relative_path
            buildcache.copy_atomic(target, keep)
            replaced[relative_path] = keep.relative_to(self.m_root).as_posix()
        buildcache.copy_atomic(source, target)

    def remove_mod(self, name: str, log: Log = print) -> list[str]:
        """Take a loose mod out: its files go, the files it replaced come back. A file changed since the install is
        left where it is; the names of those come back."""
        self._need_confirmed()
        path = self._manifest(name)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            files, replaced = dict(manifest["files"]), dict(manifest["replaced"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CLooseError(tr("common.not_installed_loose_mod", name=name)) from error
        left = self._take_out(name, files, replaced)
        path.unlink()
        log(
            tr("common.removed_name", name=name)
            + ("; " + tr("common.left_place_changed_since_no", files=", ".join(left)) if left else "")
        )
        return left

    def _take_out(self, name: str, files: dict[str, str], replaced: dict[str, str]) -> list[str]:
        """The files of a mod out of the install (``files``: path -> hash) and what they replaced back; the paths left
        where they are come back: changed since the install, or the kept original is gone."""
        left = []
        originals = self.m_root / "originals"
        for relative_path, digest in files.items():
            target = _inside(self.m_install, self.m_install / _safe(relative_path))
            keep = (
                _inside(self.m_root, self.m_root / _safe(replaced[relative_path]))
                if relative_path in replaced
                else None
            )
            if target.is_file() and _sha1(target) != digest:
                left.append(relative_path)
            elif keep is not None and not keep.is_file():
                left.append(relative_path)
            elif keep is not None:
                buildcache.copy_atomic(keep, target)  # over ours, or into its place when it is gone
                keep.unlink()
                _prune(keep.parent, originals)
            elif target.is_file():
                target.unlink()
                _prune(target.parent, self.m_install)
        _prune(originals / name, originals)
        return left


def _prune(folder: Path, stop: Path) -> None:
    """Remove ``folder`` and its parents while they are empty, never ``stop`` or above."""
    stop = stop.resolve()
    while folder.resolve() != stop and stop in folder.resolve().parents:
        try:
            os.rmdir(folder)
        except OSError:
            return
        folder = folder.parent


def loose_for(settings, key: str, **kwargs) -> CLooseGame:
    """The loose-file mods of ``key`` for the suite's settings."""
    return CLooseGame(key, Path(getattr(settings, key, "") or "."), Path(settings.workspace), **kwargs)


LOOSE_NOTES = {
    "anno2205": N_("common.engine_ini_seen_setting_read"),
    "anno2070": N_("common.2070_expected_ignore_setting_try"),
    "anno1404": N_("common.executables_know_setting_untried"),
    "anno1701": N_("common.executable_knows_setting_untried"),
}
