"""The window side of the update check (``updates`` does the work): the Help menu entries, the notice in the status
bar and the dialog with the release notes.

``attach(window)`` adds ``Help > Check for updates...`` and ``Check for updates at start`` to a suite window and returns the
``UpdateChecker``; ``checker.check_at_start()`` is the quiet check of a program start (at most once a day). Nothing is
downloaded before the person presses "Download and install". ``docs/installer.md`` "Updates".
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..infra.i18n import tr
from . import updates
from .updates import Release, UpdateError

log = logging.getLogger(__name__)


class UpdateDialog(QDialog):
    """The version and the release notes; "Download and install" (the installed program) or the pip command."""

    def __init__(self, parent, release: Release) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Update available"))
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("Version {version} is available.", version=release.version)))
        notes = QPlainTextEdit(release.notes or tr("(no release notes)"), readOnly=True)
        layout.addWidget(notes)
        self.install = updates.frozen()
        if self.install:
            buttons = QDialogButtonBox()
            go = buttons.addButton(tr("Download and install"), QDialogButtonBox.ButtonRole.AcceptRole)
            go.setDefault(True)
            buttons.addButton(tr("Later"), QDialogButtonBox.ButtonRole.RejectRole)
        else:
            layout.addWidget(QLabel(tr("To update this installation, run:")))
            layout.addWidget(QLineEdit(updates.pip_command(release.version), readOnly=True))
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class UpdateChecker(QObject):
    """Checks on a background thread and tells the window what it found."""

    checked = Signal(object, bool, str)  # the release or None, asked by hand, the error ("" when it worked)
    downloaded = Signal(object, str)  # the installer's path or None, the error

    def __init__(self, window, net: updates.Net = updates.NET, today: Callable[[], date] = date.today) -> None:
        super().__init__(window)
        self.window, self.net, self.today = window, net, today
        self.release: Release | None = None
        self.busy = False
        self.notice = QPushButton(flat=True)
        self.notice.clicked.connect(lambda _c=False: self.show_release())
        self.notice.hide()
        window.statusBar().addPermanentWidget(self.notice)
        self.checked.connect(self._checked)
        self.downloaded.connect(self._downloaded)

    def check_at_start(self) -> bool:
        """The quiet check of a program start: only when the setting is on, a day has passed and this build has a
        release page. Returns whether a check was started."""
        s = self.window.settings
        if not (updates.enabled() and s.check_updates and updates.due(s.last_update_check, self.today())):
            return False
        self.start(False)
        return True

    def start(self, by_hand: bool) -> None:
        if self.busy:
            return
        self.busy = True

        def work() -> None:
            try:
                self.checked.emit(updates.check(net=self.net), by_hand, "")
            except UpdateError as exc:
                self.checked.emit(None, by_hand, str(exc))

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _checked(self, release, by_hand: bool, error: str) -> None:
        self.busy = False
        if error:
            log.info("update check failed: %s", error)
            if by_hand:
                QMessageBox.information(
                    self.window, tr("Check for updates"), tr("Could not check: {error}", error=error)
                )
            return
        s = self.window.settings
        s.last_update_check = self.today().isoformat()
        if release is None:
            if by_hand:
                QMessageBox.information(self.window, tr("Check for updates"), tr("This version is up to date."))
            return
        self.release = release
        self.notice.setText(tr("Version {version} is available", version=release.version))
        self.notice.setToolTip(tr("click for the release notes"))
        self.notice.show()
        if by_hand:
            self.show_release()

    def show_release(self) -> None:
        if self.release is None:
            return
        dialog = UpdateDialog(self.window, self.release)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.install:
            self.fetch(self.release)

    def fetch(self, release: Release) -> None:
        """Download and check the installer (on a thread), then hand it over."""
        if self.busy:
            return
        self.busy = True
        self.notice.setText(tr("Downloading version {version}...", version=release.version))
        self.notice.setEnabled(False)

        def work() -> None:
            try:
                self.downloaded.emit(str(updates.download(release, net=self.net)), "")
            except (UpdateError, OSError) as exc:
                self.downloaded.emit(None, str(exc))

        threading.Thread(target=work, name="update-download", daemon=True).start()

    def _downloaded(self, path, error: str) -> None:
        self.busy = False
        self.notice.setEnabled(True)
        if error or not path:
            self.notice.setText(tr("Version {version} is available", version=self.release.version))
            QMessageBox.warning(self.window, tr("Update"), tr("The download failed: {error}", error=error))
            return
        self.install(path)

    def install(self, path) -> bool:
        """Close the window the usual way (its own questions about unsaved work run), then start the installer and
        quit. False, and nothing started, when the person cancels one of the questions."""
        from pathlib import Path

        if not self.window.close():
            self.notice.setText(tr("Version {version} is downloaded", version=self.release.version))
            return False
        updates.start_installer(Path(path))
        app = QApplication.instance()
        if app is not None:
            app.quit()
        return True


def attach(window) -> UpdateChecker | None:
    """The Help menu entries and the checker of ``window`` (a suite window); None when this build has no release page."""
    if not updates.enabled():
        return None
    checker = UpdateChecker(window)
    menu = window.menuBar().addMenu(tr("&Help"))
    now = QAction(tr("Check for updates..."), window)
    now.triggered.connect(lambda _c=False: checker.start(True))
    menu.addAction(now)
    auto = QAction(tr("Check for updates at start"), window, checkable=True)
    auto.setChecked(window.settings.check_updates)

    def toggled(on: bool) -> None:
        window.settings.check_updates = on
        window.save_settings()

    auto.toggled.connect(toggled)
    menu.addAction(auto)
    return checker
