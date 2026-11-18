"""The setup page and the uninstall dialog of a suite window (the choices and the work behind them are in
``setupchoices``, ``shortcuts``, ``migrate`` and ``installevents``).

``attach(window)`` adds ``Help > Setup...`` (the page again) and, in an installed program only, ``Help > Uninstall...``.
``first_start(window, settings_existed)`` opens the page at the start that asks for it. The page reuses what the suite
has: the settings' detected game folders and their check, and the "Games and folders" dialog for the rest.
``docs/installer.md``, "First-run setup" and "Uninstall".
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..infra.i18n import languages, tr
from ..paths import local_data_dir
from . import install, installevents, migrate, setupchoices, shortcuts, updates
from .setupchoices import Choices

log = logging.getLogger(__name__)
GUIDE = "docs/live-link.md"  # the link helper's guide


def guide_url() -> str:
    return f"https://github.com/{updates.RELEASES}/blob/main/{GUIDE}" if updates.enabled() else ""


def carry_out(
    choices: Choices,
    launcher: Path | None,
    where: shortcuts.Places | None,
    remove: Callable[[str], str] = migrate.remove_product,
) -> list[str]:
    """The work the page's answers ask for outside the settings: old installs removed first (their shortcuts go with
    them), then the shortcuts made or removed. Returns what went wrong."""
    problems = [
        tr("common.older_installation_could_not_removed", code=c) for c in choices.remove_old if remove(c) != "removed"
    ]
    if launcher is not None and where is not None:
        problems += shortcuts.sync(choices.start_menu, choices.desktop, launcher, where)
    return problems


class SetupDialog(QDialog):
    """The page: language, the games' folders (each confirmed or changed), the shortcuts, the update check."""

    def __init__(
        self, parent, settings, carried=None, old: list[str] | None = None, can_shortcut: bool = False, more=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("islands.setup"))
        self.settings, self.old, self.more = settings, old or [], more
        start = setupchoices.defaults(settings, carried)
        layout = QVBoxLayout(self)
        intro = QLabel(tr("common.welcome_confirm_change_once_help"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.language = QComboBox()
        for code, name in languages().items():
            self.language.addItem(name, code)  # i18n: ok (each language's own name)
        self.language.setCurrentIndex(max(0, self.language.findData(start.language)))
        self.language.setToolTip(tr("islands.tip.language_apps_windows_change_applies"))
        form.addRow(tr("common.language"), self.language)
        self.edits: dict[str, QLineEdit] = {}
        self.states: dict[str, QLabel] = {}
        for key in setupchoices.game_keys():
            edit = QLineEdit(start.folders.get(key, ""))
            pick = QPushButton(tr("islands.browse"))
            pick.clicked.connect(lambda _=False, e=edit: self._pick(e))
            state = QLabel()
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            row.addWidget(pick)
            row.addWidget(state)
            form.addRow(self._game_name(key), row)
            edit.textChanged.connect(lambda _t, k=key: self._state(k))
            self.edits[key], self.states[key] = edit, state
            self._state(key)
        layout.addLayout(form)
        self.start_menu = QCheckBox(tr("common.start_menu_entry"), checked=start.start_menu)
        self.desktop = QCheckBox(tr("common.desktop_shortcut"), checked=start.desktop)
        if can_shortcut:
            layout.addWidget(self.start_menu)
            layout.addWidget(self.desktop)
        self.check = QCheckBox(tr("common.check_updates_start"), checked=start.check_updates)
        layout.addWidget(self.check)
        self.remove_old = QCheckBox(tr("common.remove_older_installation_your_settings"), checked=True)
        self.remove_old.setVisible(bool(self.old))
        layout.addWidget(self.remove_old)
        url = guide_url()
        helper = QLabel(
            tr("common.link_helper_connects_toolbox_running", guide=GUIDE)
            + (f' <a href="{url}">{url}</a>' if url else "")
        )
        helper.setWordWrap(True)
        helper.setOpenExternalLinks(True)
        layout.addWidget(helper)
        buttons = QDialogButtonBox()
        if more is not None:
            buttons.addButton(tr("islands.games_folders"), QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(
                self._more
            )
        buttons.addButton(tr("common.save"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr("objects.later"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(780, 0)

    @staticmethod
    def _game_name(key: str) -> str:
        from ..othergames import installs

        return installs.GAMES[key].name

    def _state(self, key: str) -> None:
        text = self.edits[key].text().strip()
        problem = setupchoices.folder_problem(key, text)
        self.states[key].setText(
            tr("common.not_set") if not text else tr("viewer.not_found") if problem else tr("common.found")
        )
        self.states[key].setToolTip(problem)

    def _pick(self, edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("islands.choose_folder"), edit.text())
        if folder:
            edit.setText(folder)

    def _more(self) -> None:
        """The island editor's "Games and folders" dialog (the one place that edits all of them), then the rows it
        changed again; a folder typed here and not saved yet stays."""
        if self.more is None:
            return
        before = {key: getattr(self.settings, key, "") for key in self.edits}
        self.more()
        for key, edit in self.edits.items():
            if (now := getattr(self.settings, key, "")) != before[key]:
                edit.setText(now)

    def choices(self) -> Choices:
        return Choices(
            self.language.currentData(),
            self.check.isChecked(),
            self.start_menu.isChecked(),
            self.desktop.isChecked(),
            {key: edit.text().strip() for key, edit in self.edits.items()},
            tuple(self.old) if self.old and self.remove_old.isChecked() else (),
        )


class SetupRunner(QObject):
    """Opens the page and carries its answers out; the shortcuts and the removal run on a thread of their own."""

    done = Signal(object)  # the list of what went wrong

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.done.connect(self._done)

    def show(self, carried=None, look: bool = False) -> bool:
        """The page; True when it was saved. Either answer marks it done (it is not asked again). ``look``: look for
        the games that are not set first (the start looks once a day only)."""
        settings = self.window.settings
        if look:
            settings.find_games()
        old = migrate.old_products(migrate.WinRegistry())
        dialog = SetupDialog(
            self.window, settings, carried, old, install.launcher() is not None, self._folders_dialog()
        )
        saved = dialog.exec() == QDialog.DialogCode.Accepted
        if saved:
            choices = dialog.choices()
            if problems := setupchoices.apply(settings, choices):
                QMessageBox.information(self.window, tr("islands.setup"), "\n".join(problems))
            self._work(choices)
        else:
            settings.setup_done = True
        installevents.clear_first_run()
        self.window.save_settings()
        return saved

    def _folders_dialog(self):
        """The islands page's settings dialog, when this window has that page (it shares this window's settings)."""
        page = self.window.page_of("islands") if hasattr(self.window, "page_of") else None
        return getattr(page, "edit_settings", None)

    def _work(self, choices: Choices) -> None:
        launcher, where = install.launcher(), shortcuts.places()
        threading.Thread(
            target=lambda: self.done.emit(carry_out(choices, launcher, where)), name="setup-work", daemon=True
        ).start()

    def _done(self, problems) -> None:
        if problems:
            QMessageBox.warning(self.window, tr("islands.setup"), "\n".join(problems))


def first_start(window, settings_existed: bool, carried=None) -> bool:
    """At a program start: the old install's leftover removed quietly (a thread), then the page when it is asked for.
    ``carried``: the shortcut choice read before anything was removed. Returns whether the page opened."""
    threading.Thread(target=migrate.remove_leftover, name="migration-cleanup", daemon=True).start()
    if not setupchoices.pending(window.settings, installevents.first_run_pending(), settings_existed):
        return False
    SetupRunner(window).show(carried)
    return True


class UninstallDialog(QDialog):
    """The question before the uninstall: the settings and caches stay unless the person ticks the box. ``size``:
    the data folder's bytes, None to have them counted off the window's thread (a cache holds many thousand files)."""

    measured = Signal(int)

    def __init__(
        self, parent, folder: Path, size: int | None = None, measure: Callable[[Path], int] = installevents.data_size
    ) -> None:
        super().__init__(parent)
        self.folder = folder
        self.setWindowTitle(tr("ui.uninstall"))
        layout = QVBoxLayout(self)
        text = QLabel(tr("common.program_closes_removed_computer_your"))
        text.setWordWrap(True)
        layout.addWidget(text)
        self.delete = QCheckBox(tr("common.also_delete_my_settings_caches"))
        layout.addWidget(self.delete)
        self.where = QLabel(tr("common.folder_size_measuring", folder=folder))
        self.where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.where)
        self.measured.connect(self._show_size)
        if size is not None:
            self._show_size(size)
        else:
            threading.Thread(target=self._measure, args=(measure,), name="data-size", daemon=True).start()
        buttons = QDialogButtonBox()
        buttons.addButton(tr("ui.uninstall"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr("common.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _measure(self, measure: Callable[[Path], int]) -> None:
        size = measure(self.folder)
        try:
            self.measured.emit(size)
        except RuntimeError:  # the dialog was answered and deleted meanwhile: nobody to tell
            pass

    def _show_size(self, size: int) -> None:
        self.where.setText(tr("common.folder_size_mb", folder=self.folder, size=round(size / 2**20)))


def uninstall(window) -> bool:
    """Ask, then close the window the usual way and start the installer's uninstaller. False when the person cancels,
    or the program could not be closed or the uninstaller could not be started."""
    where = install.root()
    if where is None:
        return False
    folder = local_data_dir()
    dialog = UninstallDialog(window, folder)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    if not window.close():
        return False
    from .toolbox import stop_log

    stop_log()  # the log file is in the data folder, which the uninstaller moves away when asked to delete it
    installevents.request_uninstall(dialog.delete.isChecked(), folder)
    try:
        install.start_uninstall(where)
    except OSError as exc:
        installevents.cancel_request(folder)
        QMessageBox.warning(window, tr("ui.uninstall"), tr("common.could_not_start_uninstaller", error=exc))
        return False
    app = QApplication.instance()
    if app is not None:
        app.quit()
    return True


def attach(window) -> SetupRunner:
    """The Help menu's entries; the menu is the update checker's when it made one."""
    runner = SetupRunner(window)
    menu = next((a.menu() for a in window.menuBar().actions() if a.text() == tr("islands.help")), None)
    menu = menu or window.menuBar().addMenu(tr("islands.help"))
    setup = QAction(tr("common.setup"), window)
    setup.triggered.connect(lambda _c=False: runner.show(look=True))
    menu.addAction(setup)
    if install.installed():
        gone = QAction(tr("common.uninstall"), window)
        gone.triggered.connect(lambda _c=False: uninstall(window))
        menu.addAction(gone)
    return runner
