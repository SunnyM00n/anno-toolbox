"""The Mods app's "Installs" page (Qt): what the Toolbox put into 2205, 2070, 1404 and 1701, a restore button per game,
and loose-file mods through ``engine.ini`` ``PreferLocalFiles`` once the owner has confirmed the game reads them.

All logic is in ``archivegames``; slow work (copying archives) runs on ``Background``. docs/mod-manager.md "Installs
and loose files".
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..sources.gametree import GAMES as GAME_NAMES
from ..suite import widgets
from . import archivegames as ag

KEY = Qt.ItemDataRole.UserRole  # a row's game key
STATES = {
    "installed": N_("common.installed_lower"),
    "clean": N_("common.nothing_ours"),
    "no folder": N_("common.no_folder_set"),
}


class CInstallsPage(QWidget):
    def __init__(self, settings, background, say=lambda _t: None, is_synchronous: bool = False) -> None:
        super().__init__()
        self.m_settings, self.m_background, self.m_say, self.m_is_synchronous = (
            settings,
            background,
            say,
            is_synchronous,
        )
        self.m_busy = False
        self.m_loose_why = self.m_switch_why = ""  # why the loose-file buttons are off (show_loose)
        self.m_installs = QTreeWidget()
        self.m_installs.setHeaderLabels(
            [
                tr("common.game"),
                tr("common.state"),
                tr("mods.what"),
                tr("common.files_ours"),
                tr("common.games_own_kept"),
            ]
        )
        self.m_installs.setRootIsDecorated(False)
        self.m_restore_button = QPushButton(tr("common.restore_games_own_files"))
        self.m_restore_button.clicked.connect(self.restore_selected)
        refresh = QPushButton(tr("common.refresh"))
        refresh.clicked.connect(self.refresh)
        bar = QHBoxLayout()
        bar.addWidget(self.m_restore_button)
        bar.addWidget(refresh)
        bar.addStretch()

        self.m_loose_box = QGroupBox(tr("common.loose_files_preferlocalfiles"))
        self.m_game_box = QComboBox()
        for key in ag.GAMES:
            self.m_game_box.addItem(GAME_NAMES[key], key)
        self.m_loose_state = QLabel(wordWrap=True)
        self.m_mods = QTreeWidget()
        self.m_mods.setHeaderLabels(
            [tr("common.loose_mod"), tr("common.installed"), tr("common.files"), tr("common.files_replaced")]
        )
        self.m_mods.setRootIsDecorated(False)
        self.m_switch_button = QPushButton()
        self.m_add_button = QPushButton(tr("common.install_folder_loose_files"))
        self.m_remove_button = QPushButton(tr("common.remove_selected_loose_mod"))
        self.m_switch_button.clicked.connect(self.toggle_switch)
        self.m_add_button.clicked.connect(self.install_loose)
        self.m_remove_button.clicked.connect(self.remove_loose)
        row = QHBoxLayout()
        for button in (self.m_switch_button, self.m_add_button, self.m_remove_button):
            row.addWidget(button)
        row.addStretch()
        layout = QVBoxLayout(self.m_loose_box)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("common.game") + " "))
        top.addWidget(self.m_game_box)
        top.addStretch()
        layout.addLayout(top)
        layout.addWidget(self.m_loose_state)
        layout.addWidget(self.m_mods)
        layout.addLayout(row)

        box = QVBoxLayout(self)
        box.addWidget(QLabel(tr("common.what_toolbox_installed_games_load")))
        box.addWidget(self.m_installs, 1)
        box.addLayout(bar)
        box.addWidget(self.m_loose_box, 2)
        self.m_game_box.currentIndexChanged.connect(lambda _n: self.show_loose())
        self.m_installs.itemSelectionChanged.connect(self._buttons)
        self.m_mods.itemSelectionChanged.connect(self._buttons)
        self.refresh()

    # what is installed

    def refresh(self) -> None:
        self.m_rows = {row.key: row for row in ag.rows(self.m_settings)}
        self.m_installs.clear()
        for key, row in self.m_rows.items():
            files = ", ".join(file.name for file in row.files)
            kept = ", ".join(file.name for file in row.kept)
            item = QTreeWidgetItem([GAME_NAMES[key], tr(STATES[row.state]), row.what, files, kept])
            item.setData(0, KEY, key)
            self.m_installs.addTopLevelItem(item)
        self.show_loose()  # which ends with the buttons

    def _chosen(self) -> str:
        item = self.m_installs.currentItem()
        return item.data(0, KEY) if item is not None else ""

    def _buttons(self) -> None:
        """Each button is on only when it can act, and its tool tip says why it is not."""
        busy = tr("common.another_change_still_running") if self.m_busy else ""
        row = self.m_rows.get(self._chosen())
        if row is None:
            restore = tr("mods.tip.installs_pick_game")
        elif row.installed:
            restore = ""
        elif row.state == "no folder":
            restore = tr("mods.tip.installs_no_folder")
        else:
            restore = tr("mods.tip.installs_nothing_ours")
        widgets.enable_unless(self.m_restore_button, busy or restore)
        widgets.enable_unless(self.m_add_button, busy or self.m_loose_why)
        widgets.enable_unless(self.m_switch_button, busy or self.m_loose_why or self.m_switch_why)
        picked = "" if self.m_mods.currentItem() is not None else tr("mods.tip.loose_pick_mod")
        widgets.enable_unless(self.m_remove_button, busy or self.m_loose_why or picked)

    def _run(self, work, done_text: str) -> None:
        if self.m_busy:
            self.m_say(tr("common.another_change_still_running"))
            return
        self.m_busy = True
        self._buttons()
        self.m_background.run(work, lambda result: self._done(result, done_text), is_waiting=self.m_is_synchronous)

    def _done(self, result, done_text: str) -> None:
        self.m_busy = False
        if isinstance(result, Exception):
            QMessageBox.warning(self, tr("common.installs"), str(result))
            self.m_say(str(result))
        else:
            self.m_say(done_text)
        self.refresh()

    def restore_selected(self) -> None:
        key = self._chosen()
        row = self.m_rows.get(key)
        if not row or not row.installed:
            return
        ask = QMessageBox.question(
            self,
            tr("common.installs"),
            tr("common.put_games_own_files_back", game=GAME_NAMES[key]),
        )
        if ask == QMessageBox.StandardButton.Yes:
            settings = self.m_settings

            def work() -> None:
                if not ag.restore(settings, key):
                    raise ag.CLooseError(tr("common.nothing_toolboxs_installed", game=GAME_NAMES[key]))

            self._run(work, tr("common.restored_files_game", game=GAME_NAMES[key]))

    # loose files

    def _game(self) -> ag.CLooseGame:
        return ag.loose_for(self.m_settings, self.m_game_box.currentData())

    def show_loose(self) -> None:
        key = self.m_game_box.currentData()
        game = self._game()
        ini = game.ini
        switch = game.switch_state()
        lines = [tr(ag.LOOSE_NOTES[key])]
        lines.append(
            tr("common.engine_ini_where", where=ini if ini else tr("common.not_found_game_writes_first"))
            + (
                "; " + tr("common.preferlocalfiles_is_state", state=tr("mods.on") if switch else tr("mods.off"))
                if switch is not None and ini
                else ""
            )
        )
        if not game.confirmed:
            lines.append(tr("common.not_confirmed_game_yet_actions"))
        self.m_loose_state.setText("\n".join(lines))
        self.m_mods.clear()
        for mod in game.mods():
            self.m_mods.addTopLevelItem(
                QTreeWidgetItem([mod.name, mod.installed, str(len(mod.files)), str(mod.replaced)])
            )
        folder = str(getattr(self.m_settings, key, "") or "").strip()  # none set is not "the folder we stand in"
        if not folder or not Path(folder).is_dir():
            self.m_loose_why = tr("mods.tip.loose_no_folder")
        else:
            self.m_loose_why = "" if game.confirmed else tr("common.not_confirmed_game_yet_actions")
        self.m_switch_why = "" if ini is not None and switch is not None else tr("mods.tip.loose_no_ini")
        self.m_switch_button.setText(
            tr("common.turn_preferlocalfiles_off") if switch else tr("common.turn_preferlocalfiles")
        )
        self._buttons()

    def toggle_switch(self) -> None:
        game = self._game()
        is_on = not game.switch_state()
        self._run(
            lambda: game.set_switch(is_on),
            tr("common.preferlocalfiles_now_state", state=tr("mods.on") if is_on else tr("mods.off")),
        )

    def install_loose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("common.folder_laid_out_like_games"))
        if folder:
            game = self._game()
            self._run(lambda: game.install_mod(Path(folder)), tr("common.installed_name", name=Path(folder).name))

    def remove_loose(self) -> None:
        item = self.m_mods.currentItem()
        if item is None:
            return
        game, name = self._game(), item.text(0)
        self._run(lambda: game.remove_mod(name), tr("common.removed_name", name=name))
