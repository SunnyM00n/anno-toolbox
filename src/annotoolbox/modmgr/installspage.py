"""The Mods app's "Installs" page (Qt): what the Toolbox put into 2205, 2070, 1404 and 1701, a restore button per game,
and loose-file mods through ``engine.ini`` ``PreferLocalFiles`` once the owner has confirmed the game reads them.

All logic is in ``archivegames``; slow work (copying archives) runs on ``Background``. docs/mod-manager.md "Installs
and loose files".
"""

from __future__ import annotations

from pathlib import Path

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
from . import archivegames as ag

STATES = {"installed": N_("installed"), "clean": N_("nothing of ours"), "no folder": N_("no folder set")}


class InstallsPage(QWidget):
    def __init__(self, settings, background, say=lambda _t: None, sync: bool = False) -> None:
        super().__init__()
        self.settings, self.background, self.say, self.sync = settings, background, say, sync
        self.busy = False
        self.installs = QTreeWidget()
        self.installs.setHeaderLabels([tr("Game"), tr("State"), tr("What"), tr("Files of ours"), tr("Game's own kept")])
        self.installs.setRootIsDecorated(False)
        self.restore_button = QPushButton(tr("Restore the game's own files"))
        self.restore_button.clicked.connect(self.restore_selected)
        refresh = QPushButton(tr("Refresh"))
        refresh.clicked.connect(self.refresh)
        bar = QHBoxLayout()
        bar.addWidget(self.restore_button)
        bar.addWidget(refresh)
        bar.addStretch()

        self.loose_box = QGroupBox(tr("Loose files (PreferLocalFiles)"))
        self.game_box = QComboBox()
        for key in ag.GAMES:
            self.game_box.addItem(GAME_NAMES[key], key)
        self.loose_state = QLabel(wordWrap=True)
        self.mods = QTreeWidget()
        self.mods.setHeaderLabels([tr("Loose mod"), tr("Installed"), tr("Files"), tr("Files it replaced")])
        self.mods.setRootIsDecorated(False)
        self.switch_button = QPushButton()
        self.add_button = QPushButton(tr("Install a folder of loose files..."))
        self.remove_button = QPushButton(tr("Remove the selected loose mod"))
        self.switch_button.clicked.connect(self.toggle_switch)
        self.add_button.clicked.connect(self.install_loose)
        self.remove_button.clicked.connect(self.remove_loose)
        row = QHBoxLayout()
        for w in (self.switch_button, self.add_button, self.remove_button):
            row.addWidget(w)
        row.addStretch()
        lay = QVBoxLayout(self.loose_box)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Game") + " "))
        top.addWidget(self.game_box)
        top.addStretch()
        lay.addLayout(top)
        lay.addWidget(self.loose_state)
        lay.addWidget(self.mods)
        lay.addLayout(row)

        box = QVBoxLayout(self)
        box.addWidget(QLabel(tr("What the Toolbox installed into the games that load archives:")))
        box.addWidget(self.installs, 1)
        box.addLayout(bar)
        box.addWidget(self.loose_box, 2)
        self.game_box.currentIndexChanged.connect(lambda _n: self.show_loose())
        self.installs.itemSelectionChanged.connect(self._selection)
        self.refresh()

    # what is installed

    def refresh(self) -> None:
        self.rows = {r.key: r for r in ag.rows(self.settings)}
        self.installs.clear()
        for key, r in self.rows.items():
            files = ", ".join(f.name for f in r.files)
            kept = ", ".join(f.name for f in r.kept)
            item = QTreeWidgetItem([GAME_NAMES[key], tr(STATES[r.state]), r.what, files, kept])
            item.setData(0, 256, key)
            self.installs.addTopLevelItem(item)
        self._selection()
        self.show_loose()

    def _chosen(self) -> str:
        item = self.installs.currentItem()
        return item.data(0, 256) if item is not None else ""

    def _selection(self) -> None:
        row = self.rows.get(self._chosen())
        self.restore_button.setEnabled(bool(row and row.installed))

    def _run(self, work, done_text: str) -> None:
        if self.busy:
            self.say(tr("another change is still running"))
            return
        self.busy = True
        self.background.run(work, lambda result: self._done(result, done_text), wait=self.sync)

    def _done(self, result, done_text: str) -> None:
        self.busy = False
        if isinstance(result, Exception):
            QMessageBox.warning(self, tr("Installs"), str(result))
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
            tr("Installs"),
            tr("Put {game}'s own files back? The Toolbox's islands leave the game.", game=GAME_NAMES[key]),
        )
        if ask == QMessageBox.StandardButton.Yes:
            settings = self.settings

            def work() -> None:
                if not ag.restore(settings, key):
                    raise ag.LooseError(tr("nothing of the Toolbox's is installed in {game}", game=GAME_NAMES[key]))

            self._run(work, tr("restored the files of {game}", game=GAME_NAMES[key]))

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
            tr("engine.ini: {where}", where=ini if ini else tr("not found (the game writes it at its first start)"))
            + (
                "; " + tr("PreferLocalFiles is {state}", state=tr("on") if switch else tr("off"))
                if switch is not None and ini
                else ""
            )
        )
        if not game.confirmed:
            lines.append(
                tr(
                    "Not confirmed in this game yet: the actions below stay off until the check in docs/mod-manager.md has passed."
                )
            )
        self.loose_state.setText("\n".join(lines))
        self.mods.clear()
        for m in game.mods():
            self.mods.addTopLevelItem(QTreeWidgetItem([m.name, m.installed, str(len(m.files)), str(m.replaced)]))
        ready = game.confirmed and Path(str(getattr(self.settings, key, "") or "")).is_dir()
        self.switch_button.setText(tr("Turn PreferLocalFiles off") if switch else tr("Turn PreferLocalFiles on"))
        for w in (self.switch_button, self.add_button, self.remove_button):
            w.setEnabled(ready)
        self.switch_button.setEnabled(ready and ini is not None and switch is not None)

    def toggle_switch(self) -> None:
        game = self._game()
        on = not game.switch_state()
        self._run(
            lambda: game.set_switch(on), tr("PreferLocalFiles is now {state}", state=tr("on") if on else tr("off"))
        )

    def install_loose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("A folder laid out like the game's (data/...)"))
        if folder:
            game = self._game()
            self._run(lambda: game.install_mod(Path(folder)), tr("installed {name}", name=Path(folder).name))

    def remove_loose(self) -> None:
        item = self.mods.currentItem()
        if item is None:
            return
        game, name = self._game(), item.text(0)
        self._run(lambda: game.remove_mod(name), tr("removed {name}", name=name))
