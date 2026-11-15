"""The edited islands of the games that load archives (2070, 1404, 1701, 2205): build, install, restore and "is ours
installed", once per game as an ``IslandDelivery`` in ``ISLAND_DELIVERIES``; ``DELIVERIES`` is the suite's view of the
same records and ``island_files`` the islands' share of a project. The editor's build (``editorapp.App.build_archives``)
runs ``edited``, ``package`` and, when asked, ``install``. The game's own files are kept outside the workspace
(``gamebackups``), so the restore buttons undo a project's install whatever the workspace is now
(docs/island-editor-tools.md "Build, install, restore"; docs/tool-suite.md, section 18)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..infra.i18n import N_, tr
from ..othergames import delivery2205, gamebackups, installs
from ..suite.project import ARCHIVE_GAMES, Delivery, Project
from . import oldenginebuild
from .formats.a7m import edit2205 as a7m_edit2205
from .formats.isd import build as isd_build
from .formats.isd import edit as isd_edit

Log = Callable[[str], None]

EDIT_PREFIXES = {
    "anno2070": isd_edit.EDIT_PREFIX,
    "anno1404": oldenginebuild.PREFIXES["anno1404"],
    "anno1701": oldenginebuild.PREFIXES["anno1701"],
    "anno2205": a7m_edit2205.EDIT_PREFIX,
}


def _old_backup(settings, name: str) -> Path | None:
    """Where an earlier version kept a game's own file: in the workspace (None when that is not set)."""
    workspace = Path(settings.workspace.strip() or ".")
    return workspace / name if workspace.is_absolute() else None


def patch_backup(settings) -> Path:
    """2070: the game's own patch9.rda, kept before ours replaces it (``gamebackups``, outside the workspace)."""
    new = gamebackups.folder("anno2070", Path(settings.anno2070)) / "patch9.rda"
    old = _old_backup(settings, "anno2070-patch9-original.rda")
    return gamebackups.moved_in(new, old, lambda p: not isd_build.is_ours(p)) if old else new


def old_engine_backups(settings, key: str) -> Path:
    """1404 and 1701: the folder of the game's own archives, kept before an archive of ours replaces them."""
    new = gamebackups.folder(key, Path(getattr(settings, key)))
    old = _old_backup(settings, "old-engine-originals")
    for kept in sorted(old.glob(f"{key}-*.rda")) if old and old.is_dir() else ():
        gamebackups.moved_in(new / kept.name, kept, lambda p: not oldenginebuild.is_ours(p))
    return new


def index_backup(settings) -> Path:
    """2205: the game's own archive index, kept before ours replaces it (``delivery2205``, shared with the UI)."""
    return delivery2205.backup_file(target_2205(settings))


def tree2070(root: Path):
    """2070's files; the patch9.rda the editor wrote is left out, so a build starts from the game's own."""
    from ..sources.overlay import SourceTree

    return SourceTree(root, skip=("patch9",) if isd_build.is_ours(root / isd_build.PATCH) else ())


def _islands(files: dict[str, bytes]) -> list[str]:
    return [p for p in files if p.endswith(".isd")]


# 2070
def _tree_2070(settings):
    return tree2070(Path(settings.anno2070))


def _edited_2070(tree, edits_dir: Path, log: Log) -> tuple[dict[str, bytes], list[str]]:
    from ..islands.objects2070 import read_types

    return isd_build.edited_files(tree, edits_dir, read_types(tree), log)


def _package_2070(files, written, settings, _log, _tree=None) -> dict[str, bytes]:
    original = isd_build.own_patch(Path(settings.anno2070), patch_backup(settings))
    return {isd_build.PATCH: isd_build.patch_bytes(files, written, original)}


def _install_2070(built: Path, settings, log: Log) -> str:
    patch = (built / isd_build.PATCH).read_bytes()
    isd_build.install_patch(Path(settings.anno2070), patch, patch_backup(settings), log)
    return tr("installed: start a new game in {game}", game="Anno 2070")


def _restore_2070(settings, log: Log) -> bool:
    return isd_build.uninstall_patch(Path(settings.anno2070), patch_backup(settings), log)


def _is_ours_2070(settings) -> bool:
    folder = settings.anno2070.strip()
    return bool(folder) and isd_build.is_ours(Path(folder) / isd_build.PATCH)


# 1404 and 1701
def _tree_old(key: str):
    def tree(settings):
        folder = Path(getattr(settings, key))
        replace = oldenginebuild.replacement(key, folder, old_engine_backups(settings, key))
        return installs.GAMES[key].tree(folder, replace)

    return tree


def _package_old(key: str):
    def package(files, written, settings, log, tree=None) -> dict[str, bytes]:
        tree = tree or _tree_old(key)(settings)
        folder = Path(getattr(settings, key))
        if key == "anno1701":
            return oldenginebuild.archives_1701(tree, folder, files, written)
        replace = oldenginebuild.replacement(key, folder, old_engine_backups(settings, key))
        return oldenginebuild.archives_1404(tree, folder, files, written, replace, log)

    return package


def _install_old(key: str):
    def install(built: Path, settings, log: Log) -> str:
        archives = {p.relative_to(built).as_posix(): p.read_bytes() for p in built.rglob("*.rda")}
        if not archives:
            raise RuntimeError(tr("nothing built yet: build the project first"))
        folder = Path(getattr(settings, key))
        oldenginebuild.install_archives(key, folder, archives, old_engine_backups(settings, key), log)
        return tr("installed: start a new game in {game}", game=installs.GAMES[key].name)

    return install


def _restore_old(key: str):
    return lambda settings, log: oldenginebuild.restore(
        key, Path(getattr(settings, key)), old_engine_backups(settings, key), log
    )


def _is_ours_old(key: str):
    def is_ours(settings) -> bool:
        folder = Path(getattr(settings, key))
        return installs.GAMES[key].is_install(folder) and any(
            oldenginebuild.is_ours(f) for f in oldenginebuild.last_archives(key, folder)
        )

    return is_ours


# 2205
def target_2205(settings) -> Path:
    return Path(settings.patch2205 if settings.patch2205.strip() else settings.anno2205)


def _tree_2205(settings):
    from .formats.a7m.reader import GameFiles

    return GameFiles(installs.GAMES["anno2205"].tree(Path(settings.anno2205)))


def _package_2205(files, _written, settings, _log, _tree=None) -> dict[str, bytes]:
    if not files:
        return {}
    index = a7m_edit2205.original_index(target_2205(settings), _old_index_backup(settings))
    return a7m_edit2205.pack(files, index)


def _old_index_backup(settings) -> Path | None:
    return _old_backup(settings, "anno2205-file.db-original")


def _install_ready_2205(settings) -> None:
    target = target_2205(settings)
    if not settings.patch2205.strip() or not target.is_absolute() or not installs.GAMES["anno2205"].is_install(target):
        raise RuntimeError(tr("set the Anno 2205 folder to patch (Games and folders) to an absolute path to install"))


def _install_2205(built: Path, settings, log: Log) -> str:
    _install_ready_2205(settings)
    a7m_edit2205.install(built, target_2205(settings), _old_index_backup(settings), log)
    return tr("installed into {target}: start a new game", target=target_2205(settings))


def _restore_2205(settings, log: Log) -> bool:
    if not settings.patch2205.strip():
        raise RuntimeError(tr("the Anno 2205 folder to patch is not set (Games and folders)"))
    return a7m_edit2205.uninstall(Path(settings.patch2205), _old_index_backup(settings), log)


def _is_ours_2205(settings) -> bool:
    folder = settings.patch2205.strip()
    return bool(folder) and (Path(folder) / a7m_edit2205.ARCHIVE).is_file()


@dataclass(frozen=True)
class IslandDelivery:
    """What differs per game in building, installing and restoring its edited islands; ``Game`` (``installs``) holds the
    rest. ``edited(tree, edits_dir, log)`` -> (files by game path, the islands'/sessions' paths), ``package(files,
    written, settings, log, tree=None)`` -> the archives (path in the install -> bytes; none when there is nothing to
    write), ``install(built_folder, settings, log)`` -> a line to show, ``restore(settings, log)``,
    ``is_ours(settings)``: an archive of the editor is in the game."""

    key: str
    what: str  # what the edits are: "islands", "sessions" (marked, shown through ``tr``)
    out_name: str  # the build's folder in the workspace (the archives lie in it by their path in the install)
    tree: Callable
    edited: Callable
    package: Callable
    install: Callable
    restore: Callable
    is_ours: Callable
    restore_label: str  # the button that restores the game's own files
    install_ready: Callable = lambda settings: None  # raises RuntimeError with what to set before an install

    @property
    def game(self) -> installs.Game:
        return installs.GAMES[self.key]

    @property
    def what_one(self) -> str:
        """``what`` in the singular, in the interface language."""
        return tr(WHAT_ONE[self.what])

    def ready(self, settings) -> None:
        """Raise ``RuntimeError`` when the game's folder is not set to an install."""
        if not self.game.is_install(Path(getattr(settings, self.key))):
            raise RuntimeError(tr("{game} not found: set its folder (Games and folders)", game=self.game.name))

    def project_delivery(self) -> Delivery:
        """The suite's view of the record: the parts' merged files packed, the built folder installed."""

        def package(files, _project, settings, log) -> dict[str, bytes]:
            return self.package(files, _islands(files), settings, log)

        return Delivery(package, lambda project, settings, log: self.install(project.build_dir, settings, log))


WHAT_ONE = {"islands": N_("island"), "sessions": N_("session")}

ISLAND_DELIVERIES = {
    d.key: d
    for d in (
        IslandDelivery(
            "anno2070", N_("islands"), "anno2070-patch", _tree_2070, _edited_2070, _package_2070, _install_2070,
            _restore_2070, _is_ours_2070, N_("Restore the game's own patch9.rda"),
        ),
        IslandDelivery(
            "anno1404", N_("islands"), "anno1404-archives", _tree_old("anno1404"), oldenginebuild.edited_1404,
            _package_old("anno1404"), _install_old("anno1404"), _restore_old("anno1404"), _is_ours_old("anno1404"),
            N_("Restore the game's own archives"),
        ),
        IslandDelivery(
            "anno1701", N_("islands"), "anno1701-archives", _tree_old("anno1701"), oldenginebuild.edited_1701,
            _package_old("anno1701"), _install_old("anno1701"), _restore_old("anno1701"), _is_ours_old("anno1701"),
            N_("Restore the game's own archives"),
        ),
        IslandDelivery(
            "anno2205", N_("sessions"), a7m_edit2205.OUT_NAME, _tree_2205, a7m_edit2205.edited_members, _package_2205,
            _install_2205, _restore_2205, _is_ours_2205, N_("Restore the game's own file.db"), _install_ready_2205,
        ),
    )
}  # fmt: skip

DELIVERIES = {key: d.project_delivery() for key, d in ISLAND_DELIVERIES.items() if key in ARCHIVE_GAMES}
assert set(DELIVERIES) == set(ARCHIVE_GAMES)


def island_files(project: Project, settings, log: Log) -> dict[str, bytes]:
    """The project's edited islands as files by game path (2205: the changed members of the session maps)."""
    d = ISLAND_DELIVERIES[project.game]
    return d.edited(d.tree(settings), project.island_edits, log)[0]
