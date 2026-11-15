"""The Mod diff app's window (Qt): a question chosen in the tool bar (a mod against the game, a profile's mods against each
other, two versions of a mod, two mods, an archive or loose files against the game), run off the window's thread, and
the answer in three tabs: the changes as a tree, the conflicts, what the loader would drop.

``ModDiffWindow(settings, settings_path)`` is the suite's Mod diff app. All logic is in
``jobs`` and ``compare``; this module shows it. ``docs/mod-diff.md`` is the guide.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..assetedit import assetgame
from ..infra.i18n import N_, tr
from ..sources import gametree
from ..suite.background import Background
from . import export, jobs
from .difftree import DiffTree
from .model import Report
from .tables import ConflictTable, DroppedList

APP = "moddiff"  # the key of its remembered state in the settings
TITLE = N_("Mod diff")
MODE_NAMES = {
    jobs.MOD: N_("A mod against the game"),
    jobs.PROFILE: N_("Conflicts in a profile"),
    jobs.VERSIONS: N_("Two versions of a mod"),
    jobs.TWO: N_("Two mods"),
    jobs.ARCHIVE: N_("An archive or loose files"),
    jobs.ARCHIVES: N_("Two archives or folders"),
}
CAPTIONS = {
    jobs.MOD: (N_("Mod"), ""),
    jobs.PROFILE: ("", ""),
    jobs.VERSIONS: (N_("First version"), N_("Second version")),
    jobs.TWO: (N_("First mod"), N_("Second mod")),
    jobs.ARCHIVE: (N_("Archive or folder"), ""),
    jobs.ARCHIVES: (N_("First archive or folder"), N_("Second archive or folder")),
}
WHOLE_FILES = (jobs.ARCHIVE, jobs.ARCHIVES)  # the questions about files a mod carries whole, not ModOps


class ModDiffWindow(QMainWindow):
    progress_text = Signal(str)  # said by the running job from its thread

    def __init__(self, settings, settings_path: Path | None = None, sync: bool = False) -> None:
        super().__init__()
        self.settings, self.settings_path = settings, settings_path
        self.background = Background(self)
        self.sync = sync  # run the background jobs here and now (a test)
        self.games: dict[str, object] = {}  # key -> the game opened for a comparison
        self.opening = threading.Lock()  # one thread opens a game at a time (the look-ahead and a comparison)
        self.managers: dict[str, object] = {}  # key -> its mod manager (None: the game has no mod loader)
        self.report: Report | None = None
        self.running = False
        self.warmed = False  # the look-ahead at the first game has started
        self._building = False
        self._build()
        self._menus()
        self.progress_text.connect(self.statusBar().showMessage)
        saved = settings.apps.get(APP, {})
        self.mode_box.setCurrentIndex(max(0, self.mode_box.findData(saved.get("mode", jobs.MOD))))
        self._mode_chosen()
        self.setWindowTitle(tr(TITLE))

    # the window

    def _build(self) -> None:
        self._question_bar()
        self._body()
        self.statusBar().showMessage("")
        self.mode_box.currentIndexChanged.connect(lambda _n: self._mode_chosen())
        self.game_box.currentIndexChanged.connect(lambda _n: self._game_chosen())
        self.filter.textChanged.connect(self.tree.set_filter)
        self.tree.selected.connect(self.detail.setPlainText)
        self.conflicts.opened.connect(self._jump)

    def _question_bar(self) -> None:
        """The tool bar: the question, the game, the profile, the two fields with their choosers and Compare."""
        bar = QToolBar(tr("Question"), self, movable=False)
        self.addToolBar(bar)
        self.mode_box, self.game_box, self.profile_box = QComboBox(), QComboBox(), QComboBox()
        for mode, name in MODE_NAMES.items():
            self.mode_box.addItem(tr(name), mode)
        self.fields: dict[str, tuple[QLabel, QComboBox, QToolButton]] = {}
        self.zip_actions: list[QAction] = []
        bar.addWidget(self.mode_box)
        bar.addWidget(QLabel("  " + tr("Game") + " "))
        bar.addWidget(self.game_box)
        self.profile_label = QLabel("  " + tr("Profile") + " ")
        self.profile_label_action = bar.addWidget(self.profile_label)
        self.profile_action = bar.addWidget(self.profile_box)
        for slot in ("a", "b"):
            label, box, browse = QLabel(), QComboBox(), QToolButton()
            box.setEditable(True)
            box.setMinimumWidth(220)
            browse.setText("...")
            browse.setToolTip(tr("Choose a folder"))
            browse.clicked.connect(lambda _c=False, s=slot: self.browse(s))
            pick_zip = QAction(tr("Choose a .zip of mods..."), browse)  # 1800 reads mods from a .zip in place
            pick_zip.triggered.connect(lambda _c=False, s=slot: self.browse(s, archive=tr("Mods (*.zip)")))
            browse.addAction(pick_zip)
            self.zip_actions.append(pick_zip)
            self.fields[slot] = (label, box, browse)
            bar.addWidget(label)
            bar.addWidget(box)
            bar.addWidget(browse)
        self.run_button = QPushButton(tr("Compare"))
        self.run_button.clicked.connect(lambda _c=False: self.run())
        bar.addWidget(QLabel(" "))
        bar.addWidget(self.run_button)

    def _body(self) -> None:
        """The summary over the three tabs: the changes (tree and detail), the conflicts, the dropped lines."""
        self.summary = QLabel(tr("Choose a question and press Compare."), wordWrap=True)
        self.filter = QLineEdit(placeholderText=tr("Filter by file, key, name, template, mod or path"))
        self.tree = DiffTree(self.sync)
        self.detail = QPlainTextEdit(readOnly=True)
        self.detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.tree)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        changes = QWidget()
        lay = QVBoxLayout(changes)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.filter)
        lay.addWidget(split)
        self.conflicts, self.dropped = ConflictTable(), DroppedList()
        self.tabs = QTabWidget()
        self.tabs.addTab(changes, tr("Changes"))
        self.tabs.addTab(self.conflicts, tr("Conflicts"))
        self.tabs.addTab(self.dropped, tr("Dropped"))
        body = QWidget()
        box = QVBoxLayout(body)
        box.addWidget(self.summary)
        box.addWidget(self.tabs)
        self.setCentralWidget(body)

    def _menus(self) -> None:
        menu = self.menuBar().addMenu(tr("&Report"))
        act = QAction(tr("Export the report..."), self)
        act.setShortcut(QKeySequence("Ctrl+E"))
        act.setStatusTip(tr("the comparison as Markdown or CSV"))
        act.triggered.connect(lambda _c=False: self.export_report())
        menu.addAction(act)
        self.export_action = act
        act.setEnabled(False)
        pick = QAction(tr("Choose a folder of loose files..."), self)
        pick.triggered.connect(lambda _c=False: self.browse("a", folder=True))
        menu.addAction(pick)

    # the question

    def mode(self) -> str:
        return self.mode_box.currentData()

    def _mode_chosen(self) -> None:
        mode = self.mode()
        self._building = True
        games = jobs.GAMES[mode]
        keep = self.game_box.currentData() or self.settings.apps.get(APP, {}).get("game")
        self.game_box.clear()
        for key in games:
            self.game_box.addItem(gametree.GAMES.get(key, key), key)
        self.game_box.setCurrentIndex(max(0, self.game_box.findData(keep)))
        self._building = False
        for slot, caption in zip("ab", CAPTIONS[mode], strict=True):
            label, box, browse = self.fields[slot]
            label.setText(f"  {tr(caption)} " if caption else "")
            for widget in (label, box, browse):
                widget.setVisible(bool(caption))
        self.profile_label_action.setVisible(mode == jobs.PROFILE)
        self.profile_action.setVisible(mode == jobs.PROFILE)
        for slot in "ab":
            self.fields[slot][2].setToolTip(
                tr("Choose an archive file") if mode in WHOLE_FILES else tr("Choose a folder")
            )
        self._game_chosen()

    def _game_chosen(self) -> None:
        if self._building or not self.game_box.currentData():
            return
        key = self.game_box.currentData()
        self.settings.apps.setdefault(APP, {}).update({"mode": self.mode(), "game": key})
        zips = key in jobs.ZIP_GAMES and self.mode() not in WHOLE_FILES
        for slot, action in zip("ab", self.zip_actions, strict=True):
            action.setVisible(zips)
            self.fields[slot][2].setPopupMode(
                QToolButton.ToolButtonPopupMode.MenuButtonPopup
                if zips
                else QToolButton.ToolButtonPopupMode.DelayedPopup
            )
        if key in self.managers:
            self._fill_lists(key)
            return
        self.statusBar().showMessage(tr("reading the mods of {game}", game=gametree.GAMES.get(key, key)))
        self.background.run(
            lambda: jobs.manager_for(self.settings, key), lambda m: self._managed(key, m), wait=self.sync
        )

    def _managed(self, key: str, manager) -> None:
        self.statusBar().showMessage("")
        self.managers[key] = None if isinstance(manager, Exception) else manager
        if self.game_box.currentData() == key:
            self._fill_lists(key)
        if not self.warmed and key not in self.games:  # the game of the last session opens now: its file tree takes
            self.warmed = True  # half a minute to make; the games chosen after it open when a question needs them
            self.background.run(lambda: self._game(key, self.progress_text.emit), self._warmed, wait=self.sync)

    def _warmed(self, result) -> None:
        if isinstance(result, Exception):
            self.statusBar().showMessage(str(result))
        elif not self.running:
            self.statusBar().showMessage("")

    def _game(self, key: str, say):
        """The game ``key`` opened (once, with its file tree made), off the window's thread."""
        with self.opening:
            if key not in self.games:
                game = assetgame.open_installed(self.settings, key, say)
                jobs.warm(game)
                self.games[key] = game
            return self.games[key]

    def _fill_lists(self, key: str) -> None:
        """The installed mods and the profiles of the game open in the tool bar."""
        manager = self.managers.get(key)
        ids = [c.mod_id for c in manager.catalog.cards if c.usable] if manager is not None else []
        for slot in "ab":
            box = self.fields[slot][1]
            text = box.currentText()
            box.clear()
            if self.mode() not in WHOLE_FILES:
                box.addItems(ids)
            box.setEditText(text)
        self.profile_box.clear()
        if manager is not None:
            self.profile_box.addItems(jobs.profile_names(manager))
            self.profile_box.setCurrentText(
                (manager.active() if manager.game != "anno1800" else "") or self.profile_box.currentText()
            )

    def browse(self, slot: str, folder: bool = False, archive: str = "") -> None:
        """A folder (a mod, loose files) or a file (an archive for an archive question; ``archive``: a file of that
        dialog filter, such as a ``.zip`` of 1800 mods) into the field ``slot``."""
        box = self.fields[slot][1]
        if self.mode() in WHOLE_FILES and not folder:
            archive = archive or tr("Archives (*.rda);;All files (*)")
        if archive:
            path, _f = QFileDialog.getOpenFileName(self, tr(TITLE), "", archive)
        else:
            path = QFileDialog.getExistingDirectory(self, tr(TITLE))
        if path:
            if folder and self.mode() not in WHOLE_FILES:
                self.mode_box.setCurrentIndex(self.mode_box.findData(jobs.ARCHIVE))
            box.setEditText(path)

    def job(self) -> jobs.Job | None:
        mode, key = self.mode(), self.game_box.currentData()
        a, b = (self.fields[s][1].currentText().strip() for s in "ab")
        profile = self.profile_box.currentText()
        need = {
            jobs.MOD: [a], jobs.ARCHIVE: [a], jobs.VERSIONS: [a, b], jobs.TWO: [a, b], jobs.ARCHIVES: [a, b],
            jobs.PROFILE: [profile],
        }[mode]  # fmt: skip
        if not all(need):
            self.statusBar().showMessage(tr("Choose what to compare first."))
            return None
        return jobs.Job(mode, key, a, b, profile)

    # the run

    def run(self, job: jobs.Job | None = None) -> None:
        """Run the question (the one in the tool bar unless ``job`` is given) off the window's thread."""
        job = job or self.job()
        if job is None or self.running:
            return
        self.running = True
        self.run_button.setEnabled(False)
        self.statusBar().showMessage(tr("comparing..."))
        say = self.progress_text.emit

        def work():
            game = self._game(job.game, say)
            if job.game not in self.managers:  # asked before the mods were read (a question from another app)
                self.managers[job.game] = jobs.manager_for(self.settings, job.game)
            return jobs.run_job(job, game, self.managers[job.game], say)

        self.background.run(work, self._done, wait=self.sync)

    def _done(self, result) -> None:
        self.running = False
        self.run_button.setEnabled(True)
        if isinstance(result, Exception):
            self.statusBar().showMessage(str(result))
            QMessageBox.warning(self, tr(TITLE), str(result))
            return
        self.show_report(result)

    def show_report(self, report: Report) -> None:
        self.report = report
        counts = report.counts()
        self.summary.setText(
            tr(
                "{title}: {targets} target(s), {values} value(s), {files} file(s), {conflicts} conflict(s), "
                "{overlaps} overlap(s), {dropped} operation(s) the loader would drop",
                title=report.title,
                targets=len(report.targets),
                values=counts["values"],
                files=counts["files"],
                conflicts=counts["conflicts"],
                overlaps=counts["overlaps"],
                dropped=counts["dropped"],
            )
        )
        self.tree.set_report(report)
        self.detail.clear()
        self.conflicts.set_conflicts(report.conflicts)
        self.dropped.set_lines(report.dropped, report.notes)
        self.tabs.setTabText(1, tr("Conflicts ({n})", n=len(report.conflicts)) if report.conflicts else tr("Conflicts"))
        self.tabs.setTabText(2, tr("Dropped ({n})", n=len(report.dropped)) if report.dropped else tr("Dropped"))
        self.export_action.setEnabled(True)
        self.statusBar().showMessage(tr("done"))

    def _jump(self, file: str, key: str) -> None:
        self.filter.clear()
        self.tree.settle()  # the unfiltered rows, not after the quarter second the box waits
        if self.tree.select_target(file, key):
            self.tabs.setCurrentIndex(0)

    def export_report(self) -> None:
        """Write the open comparison: Markdown (a summary, or every target and file) or CSV, off the window's thread."""
        if self.report is None:
            return
        summary, full, table = (
            tr("Markdown, summary (*.md)"),
            tr("Markdown, every target and file (*.md)"),
            tr("CSV (*.csv)"),
        )
        name = "mod-diff.md"  # i18n: ok (a file name)
        path, chosen = QFileDialog.getSaveFileName(self, tr(TITLE), name, ";;".join((summary, full, table)))
        if not path:
            return
        report, everything = self.report, chosen == full
        self.statusBar().showMessage(tr("writing the report..."))
        self.background.run(
            lambda: export.write(report, Path(path), everything), lambda r: self._exported(path, r), wait=self.sync
        )

    def _exported(self, path: str, result) -> None:
        if isinstance(result, Exception):
            QMessageBox.warning(self, tr(TITLE), str(result))
            self.statusBar().showMessage(str(result))
        else:
            self.statusBar().showMessage(tr("report written to {path}", path=path))

    def open_job(self, job: jobs.Job) -> None:
        """Show a question another app asked (the Mods app's) in the tool bar and run it."""
        self.mode_box.setCurrentIndex(max(0, self.mode_box.findData(job.mode)))
        self.game_box.setCurrentIndex(max(0, self.game_box.findData(job.game)))
        self.fields["a"][1].setEditText(job.a)
        self.fields["b"][1].setEditText(job.b)
        if job.profile:
            self.profile_box.setCurrentText(job.profile)
        self.run(jobs.Job(job.mode, job.game, job.a, job.b, job.profile or self.profile_box.currentText()))
