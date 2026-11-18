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


class InstallsPage(QWidget):
    def __init__(self, settings, background, say=lambda _t: None, sync: bool = False) -> None:
        super().__init__()
        self.settings, self.background, self.say, self.sync = settings, background, say, sync
        self.busy = False
        self.loose_why = self.switch_why = ""  # why the loose-file buttons are off (show_loose)
        self.installs = QTreeWidget()
        self.installs.setHeaderLabels(
            [
                tr("common.game"),
                tr("common.state"),
                tr("mods.what"),
                tr("common.files_ours"),
                tr("common.games_own_kept"),
            ]
        )
        self.installs.setRootIsDecorated(False)
        self.restore_button = QPushButton(tr("common.restore_games_own_files"))
        self.restore_button.clicked.connect(self.restore_selected)
        refresh = QPushButton(tr("common.refresh"))
        refresh.clicked.connect(self.refresh)
        bar = QHBoxLayout()
        bar.addWidget(self.restore_button)
        bar.addWidget(refresh)
        bar.addStretch()

        self.loose_box = QGroupBox(tr("common.loose_files_preferlocalfiles"))
        self.game_box = QComboBox()
        for key in ag.GAMES:
            self.game_box.addItem(GAME_NAMES[key], key)
        self.loose_state = QLabel(wordWrap=True)
        self.mods = QTreeWidget()
        self.mods.setHeaderLabels(
            [tr("common.loose_mod"), tr("common.installed"), tr("common.files"), tr("common.files_replaced")]
        )
        self.mods.setRootIsDecorated(False)
        self.switch_button = QPushButton()
        self.add_button = QPushButton(tr("common.install_folder_loose_files"))
        self.remove_button = QPushButton(tr("common.remove_selected_loose_mod"))
        self.switch_button.clicked.connect(self.toggle_switch)
        self.add_button.clicked.connect(self.install_loose)
        self.remove_button.clicked.connect(self.remove_loose)
        row = QHBoxLayout()
        for w in (self.switch_button, self.add_button, self.remove_button):
            row.addWidget(w)
        row.addStretch()
        lay = QVBoxLayout(self.loose_box)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("common.game") + " "))
        top.addWidget(self.game_box)
        top.addStretch()
        lay.addLayout(top)
        lay.addWidget(self.loose_state)
        lay.addWidget(self.mods)
        lay.addLayout(row)

        box = QVBoxLayout(self)
        box.addWidget(QLabel(tr("common.what_toolbox_installed_games_load")))
        box.addWidget(self.installs, 1)
        box.addLayout(bar)
        box.addWidget(self.loose_box, 2)
        self.game_box.currentIndexChanged.connect(lambda _n: self.show_loose())
        self.installs.itemSelectionChanged.connect(self._selection)
        self.mods.itemSelectionChanged.connect(self._buttons)
        self.refresh()

    # what is installed

    def refresh(self) -> None:
        self.rows = {r.key: r for r in ag.rows(self.settings)}
        self.installs.clear()
        for key, r in self.rows.items():
            files = ", ".join(f.name for f in r.files)
            kept = ", ".join(f.name for f in r.kept)
            item = QTreeWidgetItem([GAME_NAMES[key], tr(STATES[r.state]), r.what, files, kept])
            item.setData(0, KEY, key)
            self.installs.addTopLevelItem(item)
        self._selection()
        self.show_loose()

    def _chosen(self) -> str:
        item = self.installs.currentItem()
        return item.data(0, KEY) if item is not None else ""

    def _selection(self) -> None:
        self._buttons()

    def _buttons(self) -> None:
        """Each button is on only when it can act, and its tool tip says why it is not."""
        busy = tr("common.another_change_still_running") if self.busy else ""
        row = self.rows.get(self._chosen())
        if row is None:
            restore = tr("mods.tip.installs_pick_game")
        elif row.installed:
            restore = ""
        elif row.state == "no folder":
            restore = tr("mods.tip.installs_no_folder")
        else:
            restore = tr("mods.tip.installs_nothing_ours")
        widgets.enable_unless(self.restore_button, busy or restore)
        widgets.enable_unless(self.add_button, busy or self.loose_why)
        widgets.enable_unless(self.switch_button, busy or self.loose_why or self.switch_why)
        picked = "" if self.mods.currentItem() is not None else tr("mods.tip.loose_pick_mod")
        widgets.enable_unless(self.remove_button, busy or self.loose_why or picked)

    def _run(self, work, done_text: str) -> None:
        if self.busy:
            self.say(tr("common.another_change_still_running"))
            return
        self.busy = True
        self._buttons()
        self.background.run(work, lambda result: self._done(result, done_text), wait=self.sync)

    def _done(self, result, done_text: str) -> None:
        self.busy = False
        if isinstance(result, Exception):
            QMessageBox.warning(self, tr("common.installs"), str(result))
            self.say(str(result))
        else:
            self.say(done_text)
        self.refresh()

    def restore_selected(self) -> None:
        key = self._chosen()
        row = self.rows.get(key)
        if not row or not row.installed:
            return
        ask = QMessageBox.question(
            self,
            tr("common.installs"),
            tr("common.put_games_own_files_back", game=GAME_NAMES[key]),
        )
        if ask == QMessageBox.StandardButton.Yes:
            settings = self.settings

            def work() -> None:
                if not ag.restore(settings, key):
                    raise ag.LooseError(tr("common.nothing_toolboxs_installed", game=GAME_NAMES[key]))

            self._run(work, tr("common.restored_files_game", game=GAME_NAMES[key]))

    # loose files

    def _game(self) -> ag.LooseGame:
        return ag.loose_for(self.settings, self.game_box.currentData())

    def show_loose(self) -> None:
        key = self.game_box.currentData()
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
        self.loose_state.setText("\n".join(lines))
        self.mods.clear()
        for m in game.mods():
            self.mods.addTopLevelItem(QTreeWidgetItem([m.name, m.installed, str(len(m.files)), str(m.replaced)]))
        folder = str(getattr(self.settings, key, "") or "").strip()  # none set is not "the folder we stand in"
        if not folder or not Path(folder).is_dir():
            self.loose_why = tr("mods.tip.loose_no_folder")
        else:
            self.loose_why = "" if game.confirmed else tr("common.not_confirmed_game_yet_actions")
        self.switch_why = "" if ini is not None and switch is not None else tr("mods.tip.loose_no_ini")
        self.switch_button.setText(
            tr("common.turn_preferlocalfiles_off") if switch else tr("common.turn_preferlocalfiles")
        )
        self._buttons()

    def toggle_switch(self) -> None:
        game = self._game()
        on = not game.switch_state()
        self._run(
            lambda: game.set_switch(on),
            tr("common.preferlocalfiles_now_state", state=tr("mods.on") if on else tr("mods.off")),
        )

    def install_loose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("common.folder_laid_out_like_games"))
        if folder:
            game = self._game()
            self._run(lambda: game.install_mod(Path(folder)), tr("common.installed_name", name=Path(folder).name))

    def remove_loose(self) -> None:
        item = self.mods.currentItem()
        if item is None:
            return
        game, name = self._game(), item.text(0)
        self._run(lambda: game.remove_mod(name), tr("common.removed_name", name=name))
