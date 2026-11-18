"""The window side of the update check (``updates`` does the work): the Help menu entries, the notice in the status
bar and the dialog with the release notes.

``attach(window)`` adds ``Help > Check for updates...`` and ``Check for updates at start`` to a suite window and returns the
``UpdateChecker``; ``checker.check_at_start()`` is the quiet check of a program start (at most once a day). Nothing is
downloaded before the person presses "Download and install"; a new link helper is only announced, with the link
to the release page. ``docs/installer.md`` "Updates".
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .. import __version__
from ..infra.i18n import tr
from . import updates
from .updates import Release, UpdateError

log = logging.getLogger(__name__)


class UpdateDialog(QDialog):
    """The version and the release notes; "Download and install" (the installed program), else the release page (the
    Toolbox ships through its installer only)."""

    def __init__(self, parent, release: Release) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("common.title.update_available"))
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("common.version_available_sentence", version=release.version)))
        notes = QPlainTextEdit(release.notes or tr("common.no_release_notes"), readOnly=True)
        layout.addWidget(notes)
        self.install = updates.can_self_update()
        self.page = False
        if self.install:
            buttons = QDialogButtonBox()
            go = buttons.addButton(tr("common.download_install"), QDialogButtonBox.ButtonRole.AcceptRole)
            go.setDefault(True)
            buttons.addButton(tr("objects.later"), QDialogButtonBox.ButtonRole.RejectRole)
        else:
            layout.addWidget(QLabel(tr("common.update_with_installer")))
            buttons = QDialogButtonBox()
            buttons.addButton(tr("common.open_release_page"), QDialogButtonBox.ButtonRole.AcceptRole)
            buttons.addButton(tr("common.close"), QDialogButtonBox.ButtonRole.RejectRole)
            self.page = True
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class UpdateChecker(QObject):
    """Checks on a background thread and tells the window what it found."""

    checked = Signal(object, bool, str)  # the release or None, asked by hand, the error ("" when it worked)
    downloaded = Signal(object, str)  # the downloaded update (``updates.stage``) or None, the error
    progress = Signal(int)  # the download's percent
    helper = Signal(object)  # the release's link helper check (``updates.HelperCheck``) or None

    def __init__(self, window, net: updates.Net = updates.NET, today: Callable[[], date] = date.today) -> None:
        super().__init__(window)
        self.window, self.net, self.today = window, net, today
        self.release: Release | None = None
        self.busy = False
        self._thread: threading.Thread | None = None  # the check or download that runs (a test waits for it)
        self.notice = QPushButton(flat=True)
        self.notice.clicked.connect(lambda _c=False: self.show_release())
        self.notice.hide()
        window.statusBar().addPermanentWidget(self.notice)
        self.helper_notice = QPushButton(flat=True)
        self.helper_notice.clicked.connect(lambda _c=False: self.open_release_page())
        self.helper_notice.hide()
        window.statusBar().addPermanentWidget(self.helper_notice)
        self.page_url = ""
        self.helper.connect(self._helper)
        self.checked.connect(self._checked)
        self.downloaded.connect(self._downloaded)
        self.progress.connect(self._progress)

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
        s = self.window.settings
        game, seen = getattr(s, "anno117", ""), getattr(s, "helper_seen", "")

        def work() -> None:
            try:
                latest = updates.latest(self.net)
                newer = latest is not None and updates.is_newer(latest.version, __version__)
            except Exception as exc:  # whatever went wrong, the window hears of it: a silent end would leave it busy
                self._tell(self.checked, None, by_hand, str(exc) or type(exc).__name__)
                return
            self._tell(self.checked, latest if newer else None, by_hand, "")
            if latest is None:
                return
            try:
                self._tell(self.helper, (latest, updates.helper_check(latest, game, seen, self.net)))
            except Exception as exc:  # the helper's notice is a side question: one log line
                log.info("link helper check failed: %s", exc)

        self._thread = threading.Thread(target=work, name="update-check", daemon=True)
        self._thread.start()

    @staticmethod
    def _tell(signal, *values) -> None:
        """Emit from a worker thread; a window that closed meanwhile (its checker deleted) is nobody to tell."""
        try:
            signal.emit(*values)
        except RuntimeError:
            log.info("the update check ended after its window closed")

    def _checked(self, release, by_hand: bool, error: str) -> None:
        self.busy = False
        if error:
            log.info("update check failed: %s", error)
            if by_hand:
                QMessageBox.information(
                    self.window, tr("common.check_updates"), tr("common.could_not_check", error=error)
                )
            return
        s = self.window.settings
        s.last_update_check = self.today().isoformat()
        if release is None:
            if by_hand:
                QMessageBox.information(self.window, tr("common.check_updates"), tr("common.version_up_to_date"))
            return
        self.release = release
        self.notice.setText(tr("common.version_available", version=release.version))
        self.notice.setToolTip(tr("common.tip.click_release_notes"))
        self.notice.show()
        if by_hand:
            self.show_release()

    def _helper(self, result) -> None:
        """A helper that differs from the installed one (or from the last release's): a notice with the release's page.
        The helper is never downloaded here; it goes into the game's folder, so the person fetches it."""
        release, found = result
        if found is None:
            return
        self.window.settings.helper_seen = found.sha256
        if not found.changed:
            return
        self.page_url = release.page_url
        self.helper_notice.setText(tr("common.new_link_helper_available"))
        self.helper_notice.setToolTip(tr("common.tip.click_open_release_page_download"))
        self.helper_notice.setVisible(bool(self.page_url))

    def open_release_page(self) -> None:
        if self.page_url.startswith("https://"):
            QDesktopServices.openUrl(QUrl(self.page_url))

    def show_release(self) -> None:
        if self.release is None:
            return
        dialog = UpdateDialog(self.window, self.release)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.install:
            self.fetch(self.release)
        elif dialog.page:
            self.page_url = self.release.page_url
            self.open_release_page()

    def fetch(self, release: Release) -> None:
        """Check and download the update (on a thread; the installer's updater fetches only what changed), then hand over."""
        if self.busy:
            return
        self.busy = True
        self.notice.setText(tr("common.downloading_version", version=release.version))
        self.notice.setEnabled(False)

        def work() -> None:
            try:
                info = updates.stage(release, net=self.net, progress=self.progress.emit)
            except Exception as exc:  # as in ``start``: the window always hears back
                self._tell(self.downloaded, None, str(exc) or type(exc).__name__)
                return
            self._tell(self.downloaded, info, "")

        self._thread = threading.Thread(target=work, name="update-download", daemon=True)
        self._thread.start()

    def _progress(self, percent: int) -> None:
        self.notice.setText(tr("common.downloading_version_percent", version=self.release.version, percent=percent))

    def _downloaded(self, info, error: str) -> None:
        self.busy = False
        self.notice.setEnabled(True)
        if error or info is None:
            self.notice.setText(tr("common.version_available", version=self.release.version))
            QMessageBox.warning(self.window, tr("common.update"), tr("common.download_failed_error", error=error))
            return
        self.install(info)

    def install(self, info) -> bool:
        """Close the window the usual way (its own questions about unsaved work run), then hand over to the installer's
        updater, which restarts the program. False, and nothing started, when the person cancels one of the questions."""
        if not self.window.close():
            self.notice.setText(tr("common.version_downloaded", version=self.release.version))
            return False
        try:
            updates.apply(info)
        except (UpdateError, OSError) as exc:
            QMessageBox.warning(self.window, tr("common.update"), tr("common.update_could_not_started", error=exc))
            return False
        return True


def attach(window) -> UpdateChecker | None:
    """The Help menu entries and the checker of ``window`` (a suite window); None when this build has no release page."""
    if not updates.enabled():
        return None
    checker = UpdateChecker(window)
    menu = window.menuBar().addMenu(tr("islands.help"))
    now = QAction(tr("common.check_updates_dots"), window)
    now.triggered.connect(lambda _c=False: checker.start(True))
    menu.addAction(now)
    auto = QAction(tr("common.check_updates_start"), window, checkable=True)
    auto.setChecked(window.settings.check_updates)

    def toggled(on: bool) -> None:
        window.settings.check_updates = on
        window.save_settings()

    auto.toggled.connect(toggled)
    menu.addAction(auto)
    return checker
