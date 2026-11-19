"""The installer's events: what the program does when it is started by the installer, before anything else.

``run()`` is the very first call of the program (before Qt and before the arguments are read): the installer starts
the program with an event argument after an install, around an update and before an uninstall, and expects a quick exit.
Outside an installed program it does nothing. The logic is in plain functions with their folders passed in:
``mark_first_run`` (the setup page's trigger), ``tidy_old_downloads`` (the old updater's leftovers),
``request_uninstall`` / ``before_uninstall`` (the shortcuts removed, the settings and caches only when the person asked).
``docs/installer.md``, "The events".
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..infra.tools import NO_WINDOW
from ..paths import local_data_dir
from . import install, shortcuts

log = logging.getLogger(__name__)
FIRST_RUN = "first-run"  # in the data folder: the installer's first start has not been answered by the setup page yet
UNINSTALL_REQUEST = "uninstall-request.json"  # in the data folder: "delete my settings and caches", left by the dialog
REQUEST_LIFETIME = 600.0  # seconds a request is honoured: a cancelled uninstall must not delete data months later
OLD_DOWNLOADS = "updates"  # the old updater's download folder, in the data folder
SHORTCUTS_MADE = "shortcuts-made"  # in the data folder: the program has made its shortcuts once (0.2.0 never could)


def first_run_pending(folder: Path | None = None) -> bool:
    return ((folder or local_data_dir()) / FIRST_RUN).is_file()


def mark_first_run(folder: Path | None = None) -> None:
    folder = folder or local_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / FIRST_RUN).write_text("1", encoding="utf-8", newline="\n")


def clear_first_run(folder: Path | None = None) -> None:
    ((folder or local_data_dir()) / FIRST_RUN).unlink(missing_ok=True)


def shortcuts_made(folder: Path | None = None) -> bool:
    return ((folder or local_data_dir()) / SHORTCUTS_MADE).is_file()


def mark_shortcuts_made(folder: Path | None = None) -> None:
    folder = folder or local_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SHORTCUTS_MADE).write_text("1", encoding="utf-8", newline="\n")


def tidy_old_downloads(folder: Path | None = None) -> None:
    """The old updater's downloaded installers and patches (100 MB or more each): not needed any more."""
    shutil.rmtree((folder or local_data_dir()) / OLD_DOWNLOADS, ignore_errors=True)


def request_uninstall(delete_data: bool, folder: Path | None = None, now: float | None = None) -> None:
    """The uninstall dialog's answer, left for the event that runs when the uninstaller starts the program."""
    folder = folder or local_data_dir()
    path = folder / UNINSTALL_REQUEST
    if not delete_data:
        path.unlink(missing_ok=True)
        return
    folder.mkdir(parents=True, exist_ok=True)
    request = {"delete_data": True, "at": now if now is not None else time.time()}
    path.write_text(json.dumps(request), encoding="utf-8", newline="\n")


def cancel_request(folder: Path | None = None) -> None:
    ((folder or local_data_dir()) / UNINSTALL_REQUEST).unlink(missing_ok=True)


def delete_requested(folder: Path | None = None, now: float | None = None) -> bool:
    """Whether a fresh request to delete the settings and caches is there."""
    try:
        data = json.loads(((folder or local_data_dir()) / UNINSTALL_REQUEST).read_text(encoding="utf-8"))
        age = (now if now is not None else time.time()) - float(data["at"])
        return data.get("delete_data") is True and 0 <= age <= REQUEST_LIFETIME
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _spawn_remove(path: Path) -> None:
    """``rmdir`` apart from this program, so a big cache does not run into the event's time limit."""
    if sys.platform != "win32":
        subprocess.Popen(["rm", "-rf", "--", str(path)], close_fds=True, start_new_session=True)
        return
    comspec = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
    subprocess.Popen([comspec, "/c", "rmdir", "/s", "/q", str(path)], close_fds=True, **NO_WINDOW)


def delete_data(folder: Path, spawn: Callable[[Path], None] = _spawn_remove) -> Path | None:
    """Move the data folder out of the way at once (a rename is instant) and have it removed apart from here.
    Returns the renamed folder, None when there was none or it could not be moved."""
    if not folder.is_dir():
        return None
    aside = folder.with_name(f"{folder.name}.deleting-{int(time.time())}")
    try:
        folder.rename(aside)
    except OSError as exc:
        log.warning("could not move %s aside: %s", folder, exc)
        return None
    spawn(aside)
    return aside


def before_uninstall(
    folder: Path | None = None,
    where: shortcuts.Places | None = None,
    root: Path | None = None,
    now: float | None = None,
    spawn: Callable[[Path], None] = _spawn_remove,
) -> dict[str, object]:
    """The uninstall event: our shortcuts go; the settings and caches go only on a fresh request. Returns what it did."""
    folder = folder or local_data_dir()
    where = where if where is not None else shortcuts.places()
    root = root if root is not None else install.root() or install.appimage()  # the AppImage file on Linux
    removed = shortcuts.remove_ours(where, root) if where is not None and root is not None else []
    wanted = delete_requested(folder, now)
    cancel_request(folder)
    clear_first_run(folder)
    return {"shortcuts": removed, "data": delete_data(folder, spawn) if wanted else None}


def _safely(name: str, action: Callable[[], object]) -> Callable[..., None]:
    """``action`` as an event of the installer (it passes the version): a failure is kept, never raised."""

    def event(*_args: object) -> None:
        try:
            action()
        except Exception:  # an event never stops the installer
            log.exception("install event %s failed", name)
            _keep_failure()

    return event


def _keep_failure() -> None:
    """The failure's traceback into the data folder's ``logs``: an event's process has no console and ends at once,
    so a log line alone would be seen by nobody."""
    from .toolbox import write_error_log

    write_error_log(local_data_dir() / "logs", *sys.exc_info())


def run(app_factory: Callable[[], Any] | None = None) -> bool:
    """Start the installer's events; True when they are armed. The installed program only (not a source run, not the
    plain command line of a Python environment); a missing ``velopack`` module is no error."""
    if app_factory is None:
        if not getattr(sys, "frozen", False) or (sys.platform != "win32" and install.appimage() is None):
            return False  # a source run, or on Linux a program that is not an AppImage (the tarball): nothing to arm
        try:
            import velopack
        except ImportError:
            return False
        app_factory = velopack.App
    app = app_factory()
    app = app.on_first_run(_safely("first-run", mark_first_run))
    app = app.on_after_install_fast_callback(_safely("after-install", tidy_old_downloads))
    app = app.on_after_update_fast_callback(_safely("after-update", tidy_old_downloads))
    app = app.on_before_uninstall_fast_callback(_safely("before-uninstall", before_uninstall))
    app.run()
    return True


def data_size(folder: Path) -> int:
    """The bytes under ``folder`` (settings, caches), as the uninstall dialog names them; unreadable files count 0."""
    total = 0
    stack = [folder]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total
