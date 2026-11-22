"""The setup page and the uninstall dialog of a suite window (the choices and the work behind them are in
``setupchoices``, ``shortcuts``, ``migrate`` and ``installevents``).

``attach(window)`` adds ``Help > Setup...`` (the page again) and, in an installed program only, ``Help > Uninstall...``.
``first_start(window, settings_existed)`` opens the page at the start that asks for it. The page reuses what the suite
has: the settings' detected game folders and their check, and the "Games and folders" dialog for the rest.
``docs/installer.md``, "First-run setup" and "Uninstall".
"""

from __future__ import annotations

import logging
import sys
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
from ..paths import cache_dir, config_dir, local_data_dir
from . import centralmenu, install, installevents, linuxnotice, migrate, setupchoices, shortcuts, updates
from .settings import found_text
from .setupchoices import SChoices

log = logging.getLogger(__name__)
GUIDE = "docs/live-link.md"  # the link helper's guide


def guide_url() -> str:
    return f"https://github.com/{updates.RELEASES}/blob/main/{GUIDE}" if updates.enabled() else ""


def carry_out(
    choices: SChoices,
    launcher: Path | None,
    where: shortcuts.SPlaces | None,
    remove: Callable[[str], str] = migrate.remove_product,
    made: Callable[[], None] = installevents.mark_shortcuts_made,
) -> list[str]:
    """The work the page's answers ask for outside the settings: old installs removed first (their shortcuts go with
    them), then the shortcuts made or removed (``made`` records it). Returns what went wrong."""
    problems = [
        tr("common.older_installation_could_not_removed", code=old)
        for old in choices.remove_old
        if remove(old) != "removed"
    ]
    if launcher is not None and where is not None:
        problems += shortcuts.sync(choices.start_menu, choices.desktop, launcher, where)
        made()
    return problems


def repair_shortcuts(
    start_menu: bool, desktop: bool, launcher: Path | None, where: shortcuts.SPlaces | None, folder: Path | None = None
) -> list[str] | None:
    """Make the chosen shortcuts once when the program never made any: 0.2.0 could not find its launcher (the packer
    names it after the product), so its answered setup page made none. A shortcut removed after the program made it
    is never brought back. Returns what went wrong, None when there was nothing to repair."""
    if launcher is None or where is None or installevents.shortcuts_made(folder):
        return None
    problems = shortcuts.sync(start_menu, desktop, launcher, where) if start_menu or desktop else []
    installevents.mark_shortcuts_made(folder)
    return problems


class CSetupDialog(QDialog):
    """The page: language, the games' folders (each confirmed or changed), the shortcuts, the update check."""

    def __init__(
        self, parent, settings, carried=None, old: list[str] | None = None, can_shortcut: bool = False, more=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("islands.setup"))
        self.m_settings, self.m_old, self.m_more = settings, old or [], more
        start = setupchoices.defaults(settings, carried)
        layout = QVBoxLayout(self)
        intro = QLabel(tr("common.welcome_confirm_change_once_help"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if (experimental := linuxnotice.label()) is not None:
            layout.addWidget(experimental)
        form = QFormLayout()
        self.m_language = QComboBox()
        for code, name in languages().items():
            self.m_language.addItem(name, code)  # i18n: ok (each language's own name)
        self.m_language.setCurrentIndex(max(0, self.m_language.findData(start.language)))
        self.m_language.setToolTip(tr("islands.tip.language_apps_windows_change_applies"))
        form.addRow(tr("common.language"), self.m_language)
        self.m_edits: dict[str, QLineEdit] = {}
        self.m_states: dict[str, QLabel] = {}
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
            self.m_edits[key], self.m_states[key] = edit, state
            self._state(key)
        layout.addLayout(form)
        origins = [
            f"{self._game_name(k)}: {text}" for k in self.m_edits if (text := found_text(start.folders.get(k, "")))
        ]
        if origins:  # off Windows: which launcher's library or prefix each game was found in
            where = QLabel("\n".join(origins))
            where.setWordWrap(True)
            layout.addWidget(where)
        menu_label = tr("common.start_menu_entry") if sys.platform == "win32" else tr("common.applications_menu_entry")
        self.m_start_menu = QCheckBox(menu_label, checked=start.start_menu)
        self.m_desktop = QCheckBox(tr("common.desktop_shortcut"), checked=start.desktop)
        if can_shortcut:
            layout.addWidget(self.m_start_menu)
            layout.addWidget(self.m_desktop)
        self.m_check = QCheckBox(tr("common.check_updates_start"), checked=start.check_updates)
        layout.addWidget(self.m_check)
        self.m_remove_old = QCheckBox(tr("common.remove_older_installation_your_settings"), checked=True)
        self.m_remove_old.setVisible(bool(self.m_old))
        layout.addWidget(self.m_remove_old)
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
        text = self.m_edits[key].text().strip()
        problem = setupchoices.folder_problem(key, text)
        self.m_states[key].setText(
            tr("common.not_set") if not text else tr("viewer.not_found") if problem else tr("common.found")
        )
        self.m_states[key].setToolTip(problem or found_text(text))
        self.m_edits[key].setToolTip(found_text(text))

    def _pick(self, edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("islands.choose_folder"), edit.text())
        if folder:
            edit.setText(folder)

    def _more(self) -> None:
        """The island editor's "Games and folders" dialog (the one place that edits all of them), then the rows it
        changed again; a folder typed here and not saved yet stays."""
        if self.m_more is None:
            return
        before = {key: getattr(self.m_settings, key, "") for key in self.m_edits}
        self.m_more()
        for key, edit in self.m_edits.items():
            if (now := getattr(self.m_settings, key, "")) != before[key]:
                edit.setText(now)

    def choices(self) -> SChoices:
        return SChoices(
            self.m_language.currentData(),
            self.m_check.isChecked(),
            self.m_start_menu.isChecked(),
            self.m_desktop.isChecked(),
            {key: edit.text().strip() for key, edit in self.m_edits.items()},
            tuple(self.m_old) if self.m_old and self.m_remove_old.isChecked() else (),
        )


class CSetupRunner(QObject):
    """Opens the page and carries its answers out; the shortcuts and the removal run on a thread of their own."""

    done = Signal(object)  # the list of what went wrong

    def __init__(self, window) -> None:
        super().__init__(window)
        self.m_window = window
        self.done.connect(self._done)

    def show(self, carried=None, look: bool = False) -> bool:
        """The page; True when it was saved. Either answer marks it done (it is not asked again). ``look``: look for
        the games that are not set first (the start looks once a day only)."""
        settings = self.m_window.m_settings
        if look:
            settings.find_games()
        old = migrate.old_products(migrate.CWinRegistry())
        dialog = CSetupDialog(
            self.m_window, settings, carried, old, install.launcher() is not None, self._folders_dialog()
        )
        saved = dialog.exec() == QDialog.DialogCode.Accepted
        if saved:
            choices = dialog.choices()
            if problems := setupchoices.apply(settings, choices):
                QMessageBox.information(self.m_window, tr("islands.setup"), "\n".join(problems))
            self._work(choices)
        else:
            settings.setup_done = True
        installevents.clear_first_run()
        self.m_window.save_settings()
        return saved

    def _folders_dialog(self):
        """The islands page's settings dialog, when this window has that page (it shares this window's settings)."""
        page = self.m_window.page_of("islands") if hasattr(self.m_window, "page_of") else None
        return getattr(page, "edit_settings", None)

    def _work(self, choices: SChoices) -> None:
        launcher, where = install.launcher(), shortcuts.places()
        threading.Thread(
            target=lambda: self.done.emit(carry_out(choices, launcher, where)), name="setup-work", daemon=True
        ).start()

    def _done(self, problems) -> None:
        if problems:
            QMessageBox.warning(self.m_window, tr("islands.setup"), "\n".join(problems))


def first_start(window, settings_existed: bool, carried=None) -> bool:
    """At a program start: the old install's leftover removed quietly (a thread), then the page when it is asked for.
    ``carried``: the shortcut choice read before anything was removed. Returns whether the page opened."""
    threading.Thread(target=migrate.remove_leftover, name="migration-cleanup", daemon=True).start()
    if not setupchoices.pending(window.m_settings, installevents.first_run_pending(), settings_existed):
        if install.installed():
            settings = window.m_settings
            threading.Thread(
                target=repair_shortcuts,
                args=(settings.shortcut_start_menu, settings.shortcut_desktop, install.launcher(), shortcuts.places()),
                name="shortcut-repair",
                daemon=True,
            ).start()
        return False
    CSetupRunner(window).show(carried)
    return True


class CUninstallDialog(QDialog):
    """The question before the uninstall: the settings and caches stay unless the person ticks the box. ``size``:
    the data folder's bytes, None to have them counted off the window's thread (a cache holds many thousand files)."""

    measured = Signal(int)

    def __init__(
        self,
        parent,
        folder: Path,
        size_bytes: int | None = None,
        measure: Callable[[Path], int] = installevents.data_size,
        appimage: bool = False,
        more: tuple[Path, ...] = (),
    ) -> None:
        """``appimage``: the program is one AppImage file; ``more``: further data folders (Linux keeps settings, data
        and caches apart), counted and named with ``folder``."""
        super().__init__(parent)
        self.m_folder = folder
        self.m_more = more
        self.setWindowTitle(tr("ui.uninstall"))
        layout = QVBoxLayout(self)
        text = QLabel(
            tr("common.program_closes_appimage_removed")
            if appimage
            else tr("common.program_closes_removed_computer_your")
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        self.m_delete = QCheckBox(tr("common.also_delete_my_settings_caches"))
        layout.addWidget(self.m_delete)
        self.m_where = QLabel(tr("common.folder_size_measuring", folder=folder))
        self.m_where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.m_where)
        self.measured.connect(self._show_size)
        if size_bytes is not None:
            self._show_size(size_bytes)
        else:
            threading.Thread(target=self._measure, args=(measure,), name="data-size", daemon=True).start()
        buttons = QDialogButtonBox()
        buttons.addButton(tr("ui.uninstall"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr("common.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _measure(self, measure: Callable[[Path], int]) -> None:
        size_bytes = sum(measure(path) for path in (self.m_folder, *self.m_more))
        try:
            self.measured.emit(size_bytes)
        except RuntimeError:  # the dialog was answered and deleted meanwhile: nobody to tell
            pass

    def _show_size(self, size_bytes: int) -> None:
        names = ", ".join(str(path) for path in (self.m_folder, *self.m_more))
        self.m_where.setText(tr("common.folder_size_mb", folder=names, size=round(size_bytes / 2**20)))


def uninstall(window) -> bool:
    """Ask, then close the window the usual way and start the installer's uninstaller. False when the person cancels,
    or the program could not be closed or the uninstaller could not be started."""
    where, image = install.root(), install.appimage()
    if where is None and image is None:
        return False
    folder = local_data_dir()
    more = tuple(dict.fromkeys(path for path in (config_dir(), cache_dir()) if path != folder)) if image else ()
    dialog = CUninstallDialog(window, folder, appimage=image is not None, more=more)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    if not window.close():
        return False
    from .toolbox import stop_log

    stop_log()  # the log file is in the data folder, which the uninstaller moves away when asked to delete it
    if image is not None:
        return uninstall_appimage(window, image, [folder, *more] if dialog.m_delete.isChecked() else [])
    installevents.request_uninstall(dialog.m_delete.isChecked(), folder)
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


def uninstall_appimage(window, image: Path, folders: list[Path]) -> bool:
    """The uninstall of an AppImage (Linux has no uninstaller): the file goes first (when it cannot, nothing else is
    touched), then the desktop entries that point at it and the data ``folders`` the person ticked (moved aside at
    once, removed apart from here); the program ends."""
    try:
        install.remove_appimage(image)
    except OSError as exc:
        QMessageBox.warning(window, tr("ui.uninstall"), tr("common.could_not_start_uninstaller", error=exc))
        return False
    where = shortcuts.places()
    if where is not None:
        shortcuts.remove_ours(where, image)
    for folder in folders:
        installevents.delete_data(folder)
    app = QApplication.instance()
    if app is not None:
        app.quit()
    return True


def attach(window) -> CSetupRunner:
    """The Setup and Uninstall entries, in the Settings menu."""
    runner = CSetupRunner(window)
    menu = centralmenu.menu_of(window, centralmenu.SETTINGS)
    setup = QAction(tr("common.setup"), window)
    setup.triggered.connect(lambda _c=False: runner.show(look=True))
    menu.addAction(setup)
    if install.installed():
        gone = QAction(tr("common.uninstall"), window)
        gone.triggered.connect(lambda _c=False: uninstall(window))
        menu.addAction(gone)
    return runner
