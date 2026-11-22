"""The window side of the update check (``updates`` does the work): the Help menu entries, the notice in the status
bar and the dialog with the release notes.

``attach(window)`` adds ``Help > Check for updates...`` and ``Check for updates at start`` to a suite window and returns the
``CUpdateChecker``; ``checker.check_at_start()`` is the quiet check of a program start (at most once a day). Nothing is
downloaded before the person presses "Download and install"; the doing of the update is in the launcher window
(``updaterun``), not here; a new link helper is only announced, with the link
to the release page. ``docs/installer.md`` "Updates".
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from datetime import date

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
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
from . import centralmenu, updaterun, updates
from .updates import SRelease

log = logging.getLogger(__name__)


class CUpdateDialog(QDialog):
    """The version and the release notes; "Download and install" (the installed program), else the release page (the
    Toolbox ships through its installer only)."""

    def __init__(self, parent, release: SRelease) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("common.title.update_available"))
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("common.version_available_sentence", version=release.version)))
        notes = QPlainTextEdit(release.notes or tr("common.no_release_notes"), readOnly=True)
        layout.addWidget(notes)
        self.m_install = updates.can_self_update()
        self.m_page = False
        if self.m_install:
            buttons = QDialogButtonBox()
            go = buttons.addButton(tr("common.download_install"), QDialogButtonBox.ButtonRole.AcceptRole)
            go.setDefault(True)
            buttons.addButton(tr("objects.later"), QDialogButtonBox.ButtonRole.RejectRole)
        else:
            layout.addWidget(
                QLabel(
                    tr("common.update_with_installer") if sys.platform == "win32" else tr("common.update_with_download")
                )
            )
            buttons = QDialogButtonBox()
            buttons.addButton(tr("common.open_release_page"), QDialogButtonBox.ButtonRole.AcceptRole)
            buttons.addButton(tr("common.close"), QDialogButtonBox.ButtonRole.RejectRole)
            self.m_page = True
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CUpdateChecker(QObject):
    """Checks on a background thread and tells the window what it found."""

    checked = Signal(object, bool, str)  # the release or None, asked by hand, the error ("" when it worked)
    helper = Signal(object)  # the release's link helper check (``updates.SHelperCheck``) or None

    def __init__(self, window, net: updates.SNet = updates.NET, today: Callable[[], date] = date.today) -> None:
        super().__init__(window)
        self.m_window, self.m_net, self.m_today = window, net, today
        self.m_release: SRelease | None = None
        self.m_busy = False
        self.m_run = None  # the update in the launcher window, once started
        self._m_thread: threading.Thread | None = None  # the check or download that runs (a test waits for it)
        self.m_notice = QPushButton(flat=True)
        self.m_notice.clicked.connect(lambda _c=False: self.show_release())
        self.m_notice.hide()
        window.statusBar().addPermanentWidget(self.m_notice)
        self.m_helper_notice = QPushButton(flat=True)
        self.m_helper_notice.clicked.connect(lambda _c=False: self.open_release_page())
        self.m_helper_notice.hide()
        window.statusBar().addPermanentWidget(self.m_helper_notice)
        self.m_page_url = ""
        self.helper.connect(self._helper)
        self.checked.connect(self._checked)

    def check_at_start(self) -> bool:
        """The quiet check of a program start: only when the setting is on, a day has passed and this build has a
        release page. Returns whether a check was started."""
        settings = self.m_window.m_settings
        if not (
            updates.enabled() and settings.check_updates and updates.due(settings.last_update_check, self.m_today())
        ):
            return False
        self.start(False)
        return True

    def start(self, by_hand: bool) -> None:
        if self.m_busy:
            return
        self.m_busy = True
        settings = self.m_window.m_settings
        game, seen = getattr(settings, "anno117", ""), getattr(settings, "helper_seen", "")

        def work() -> None:
            try:
                latest = updates.latest(self.m_net)
                newer = latest is not None and updates.is_newer(latest.version, __version__)
            except Exception as exc:  # whatever went wrong, the window hears of it: a silent end would leave it busy
                self._tell(self.checked, None, by_hand, str(exc) or type(exc).__name__)
                return
            self._tell(self.checked, latest if newer else None, by_hand, "")
            if latest is None:
                return
            try:
                self._tell(self.helper, (latest, updates.helper_check(latest, game, seen, self.m_net)))
            except Exception as exc:  # the helper's notice is a side question: one log line
                log.info("link helper check failed: %s", exc)

        self._m_thread = threading.Thread(target=work, name="update-check", daemon=True)
        self._m_thread.start()

    @staticmethod
    def _tell(signal, *values) -> None:
        """Emit from a worker thread; a window that closed meanwhile (its checker deleted) is nobody to tell."""
        try:
            signal.emit(*values)
        except RuntimeError:
            log.info("the update check ended after its window closed")

    def _checked(self, release, by_hand: bool, error: str) -> None:
        self.m_busy = False
        if error:
            log.info("update check failed: %s", error)
            if by_hand:
                QMessageBox.information(
                    self.m_window, tr("common.check_updates"), tr("common.could_not_check", error=error)
                )
            return
        settings = self.m_window.m_settings
        settings.last_update_check = self.m_today().isoformat()
        if release is None:
            if by_hand:
                QMessageBox.information(self.m_window, tr("common.check_updates"), tr("common.version_up_to_date"))
            return
        self.m_release = release
        self.m_notice.setText(tr("common.version_available", version=release.version))
        self.m_notice.setToolTip(tr("common.tip.click_release_notes"))
        self.m_notice.show()
        if by_hand:
            self.show_release()

    def _helper(self, result) -> None:
        """A helper that differs from the installed one (or from the last release's): a notice with the release's page.
        The helper is never downloaded here; it goes into the game's folder, so the person fetches it."""
        release, found = result
        if found is None:
            return
        self.m_window.m_settings.helper_seen = found.sha256
        if not found.changed:
            return
        self.m_page_url = release.page_url
        self.m_helper_notice.setText(tr("common.new_link_helper_available"))
        self.m_helper_notice.setToolTip(tr("common.tip.click_open_release_page_download"))
        self.m_helper_notice.setVisible(bool(self.m_page_url))

    def open_release_page(self) -> None:
        if self.m_page_url.startswith("https://"):
            QDesktopServices.openUrl(QUrl(self.m_page_url))

    def show_release(self) -> None:
        if self.m_release is None:
            return
        dialog = CUpdateDialog(self.m_window, self.m_release)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.m_install:
            self.fetch(self.m_release)
        elif dialog.m_page:
            self.m_page_url = self.m_release.page_url
            self.open_release_page()

    def fetch(self, release: SRelease) -> bool:
        """Hand the update to the launcher window (``updaterun``): the window closes the usual way first (its own
        questions about unsaved work run), then the launcher window checks, downloads and installs, and the installer's
        updater restarts the program. False, and nothing started, when the person cancels one of the questions."""
        if self.m_busy:
            return False
        qt = QApplication.instance()
        if qt is not None:
            qt.setQuitOnLastWindowClosed(False)  # the launcher window takes over; it ends the program itself
        if not self.m_window.close():
            if qt is not None:
                qt.setQuitOnLastWindowClosed(True)
            return False
        self.m_busy = True
        try:
            self.m_run = updaterun.begin(release, self.m_net)
        except Exception:  # the window is closed already: end the program rather than leave it running unseen
            log.exception("the update could not be started")
            self.m_busy = False
            if qt is not None:
                qt.quit()
            return False
        return True


def attach(window) -> CUpdateChecker | None:
    """The checker of ``window`` and its Help menu entries of ``window`` (a suite window); None when this build has no release page."""
    if not updates.enabled():
        return None
    checker = CUpdateChecker(window)
    menu = centralmenu.menu_of(window, centralmenu.HELP)
    now = QAction(tr("common.check_updates_dots"), window)
    now.triggered.connect(lambda _c=False: checker.start(True))
    menu.addAction(now)
    auto = QAction(tr("common.check_updates_start"), window, checkable=True)
    auto.setChecked(window.m_settings.check_updates)

    def toggled(on: bool) -> None:
        window.m_settings.check_updates = on
        window.save_settings()

    auto.toggled.connect(toggled)
    menu.addAction(auto)
    return checker
