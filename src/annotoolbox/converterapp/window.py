"""The Converter app's window: the entries by category on the left; on the right the chosen entry's sources, target and
options (its command's own, as a form), Start and Stop, the progress and the log.

``ConverterWindow`` is the page; ``make(shell)`` is what the suite calls. A page an extension registers at
``features.CONVERTER_PAGES`` is a tab beside the entries. The run is ``runner.run`` on a background thread; the form is
``commandform``. docs/converter-app.md, "The window".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr, trn
from ..sources import gametree
from ..suite import features, project
from ..suite.background import Background
from ..suite.widgets import pick_from_list
from . import catalogue, commandform, gamefiles, runner
from .form import OptionsForm

APP = "converter"
TITLE = N_("convert.converter")
ROLE = Qt.ItemDataRole.UserRole


class _Talk(QObject):
    """A job's lines and steps, from its thread to the window's."""

    said = Signal(str)
    stepped = Signal(int, int)


class _FitPage(QWidget):
    """The scrolled page of the chosen entry: as tall as its wrapped texts need at its width, so the scroll area
    scrolls instead of pressing the option rows together (a wrapped label's height depends on its width)."""

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()

    def fit(self) -> None:
        lay = self.layout()
        if lay is not None and lay.hasHeightForWidth():
            wanted = lay.totalHeightForWidth(self.width())
            if wanted != self.minimumHeight():
                self.setMinimumHeight(wanted)


class ConverterWindow(QMainWindow):
    """The entries, the chosen one's form, the run; the pages extensions add beside them."""

    def __init__(self, settings, settings_path: Path | None = None, shell=None) -> None:
        super().__init__()
        self.setWindowTitle(tr(TITLE))
        self.settings, self.settings_path = settings, settings_path
        self.background = Background(self)
        self.talk_qt = _Talk(self)
        self.talk_qt.said.connect(self._log)
        self.talk_qt.stepped.connect(self._step)
        self.talk: runner.Talk | None = None
        self.entries = {c.key: c for c in catalogue.entries()}
        self.change = None
        self.tree_cache: dict[str, object] = {}
        entries_page = self._build()
        host = shell or SimpleNamespace(settings=settings, settings_path=settings_path)
        self.pages = [(spec.title, spec.make(host)) for spec in features.extensions(features.CONVERTER_PAGES).values()]
        if self.pages:
            self.tabs = QTabWidget()
            self.tabs.addTab(entries_page, tr("convert.files_page"))
            for title, page in self.pages:
                if isinstance(page, QMainWindow):
                    page.setWindowFlags(Qt.WindowType.Widget)
                self.tabs.addTab(page, tr(title))
            self.setCentralWidget(self.tabs)
        else:
            self.tabs = None
            self.setCentralWidget(entries_page)
        self._menu()
        self.choose(self.state.get("entry") or next(iter(self.entries), ""))

    @property
    def state(self) -> dict:
        return self.settings.apps.setdefault(APP, {})

    # building
    def _build(self) -> QWidget:
        left = QWidget()
        lay = QVBoxLayout(left)
        self.filter = QLineEdit(placeholderText=tr("convert.filter"))
        self.filter.textChanged.connect(self._fill_tree)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(lambda item, _old: self._picked(item))
        lay.addWidget(self.filter)
        lay.addWidget(self.tree, 1)
        self._fill_tree()
        right = QSplitter(Qt.Orientation.Vertical)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(self._detail())
        bottom = QWidget()
        bottom_lay = QVBoxLayout(bottom)
        bottom_lay.setContentsMargins(0, 0, 0, 0)
        bottom_lay.addLayout(self._run_row())
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(20000)
        bottom_lay.addWidget(self.log, 1)
        right.addWidget(scroll)
        right.addWidget(bottom)
        right.setSizes([620, 220])
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([300, 900])
        return split

    def _detail(self) -> QWidget:
        page = self.detail_page = _FitPage()
        lay = QVBoxLayout(page)
        self.heading = QLabel(wordWrap=True)
        font = self.heading.font()
        font.setBold(True)
        self.heading.setFont(font)
        self.summary = QLabel(wordWrap=True)
        self.command = QLabel(wordWrap=True, textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        for label in (self.heading, self.summary, self.command):
            lay.addWidget(label)
        lay.addWidget(self._sources_box())
        lay.addWidget(self._target_box())
        self.options_box = QGroupBox(tr("common.options"))
        self.form = OptionsForm(self.options_box, self.settings)
        self.form.changed.connect(self._show_command)
        lay.addWidget(self.options_box)
        lay.addStretch(1)
        return page

    def _sources_box(self) -> QGroupBox:
        box = QGroupBox(tr("convert.sources"))
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("common.game")))
        self.game_box = QComboBox()
        self.game_box.currentIndexChanged.connect(lambda _i: self.change is not None and self._show_command())
        row.addWidget(self.game_box, 1)
        lay.addLayout(row)
        self.sources = QListWidget()
        self.sources.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.sources.setMaximumHeight(140)
        lay.addWidget(self.sources)
        buttons = QHBoxLayout()
        self.add_files_button = self._button(buttons, N_("convert.add_files"), self.add_files)
        self.add_folder_button = self._button(buttons, N_("convert.add_folder"), self.add_folder)
        self.from_game_button = self._button(buttons, N_("convert.from_game"), self.from_game)
        self.type_button = self._button(buttons, N_("convert.type_value"), self.type_value)
        self._button(buttons, N_("convert.remove"), self.remove_sources)
        self._button(buttons, N_("convert.clear"), self.sources.clear)
        lay.addLayout(buttons)
        return box

    def _target_box(self) -> QGroupBox:
        box = QGroupBox(tr("convert.target"))
        row = QHBoxLayout(box)
        self.out = QLineEdit(self.state.get("out") or str(self.project_folder()))
        row.addWidget(QLabel(tr("convert.output_folder")))
        row.addWidget(self.out, 1)
        self._button(row, N_("convert.pick"), self.pick_out)
        self._button(row, N_("convert.project_folder"), lambda: self.out.setText(str(self.project_folder())))
        return box

    def _run_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.start_button = self._button(row, N_("convert.start"), self.start)
        self.stop_button = self._button(row, N_("convert.stop"), self.stop)
        self.stop_button.setEnabled(False)
        self.progress = QProgressBar(textVisible=False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        row.addWidget(self.progress, 1)
        return row

    @staticmethod
    def _button(row, text: str, slot) -> QPushButton:
        button = QPushButton(tr(text))
        button.clicked.connect(lambda _c=False: slot())
        row.addWidget(button)
        return button

    def _menu(self) -> None:
        menu = self.menuBar().addMenu(tr("common.menu.file"))
        for text, slot, key in (
            (N_("convert.add_files"), self.add_files, QKeySequence.StandardKey.Open),
            (N_("convert.add_folder"), self.add_folder, None),
            (N_("convert.from_game"), self.from_game, "Ctrl+G"),
            (N_("convert.type_value"), self.type_value, None),
            (None, None, None),
            (N_("convert.start"), self.start, "F5"),
            (N_("convert.stop"), self.stop, "Shift+F5"),
        ):
            if text is None:
                menu.addSeparator()
                continue
            action = QAction(tr(text), self)
            if key is not None:
                action.setShortcut(QKeySequence(key))
            action.triggered.connect(lambda _c=False, s=slot: s())
            menu.addAction(action)

    def _fill_tree(self) -> None:
        words = self.filter.text().lower().split()
        current = self.change.key if self.change is not None else ""
        self.tree.blockSignals(True)
        self.tree.clear()
        for category, found in catalogue.by_category(list(self.entries.values())):
            top = QTreeWidgetItem([tr(catalogue.CATEGORIES[category])])
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for change in found:
                text = tr(change.title)
                if words and not all(w in f"{text} {tr(change.summary)}".lower() for w in words):
                    continue
                item = QTreeWidgetItem([text])
                item.setData(0, ROLE, change.key)
                item.setToolTip(0, tr(change.summary))
                if commandform.parser_of(change) is None:
                    item.setForeground(0, self.palette().placeholderText())
                top.addChild(item)
                if change.key == current:
                    self.tree.setCurrentItem(item)
            if top.childCount():
                self.tree.addTopLevelItem(top)
                top.setExpanded(True)
        self.tree.blockSignals(False)

    # choosing
    def _picked(self, item: QTreeWidgetItem | None) -> None:
        key = item.data(0, ROLE) if item is not None else None
        if key:
            self.choose(key)

    def choose(self, key: str) -> None:
        """Show the entry ``key``: its texts, its sources and its options."""
        change = self.entries.get(key)
        if change is None:
            return
        self.change = change
        self.state["entry"] = key
        self._select_item(key)
        self.heading.setText(tr(change.title))
        self.summary.setText(tr(change.summary))
        parser = commandform.parser_of(change)
        self.form.show_fields(change, parser)
        self._fill_games(change)
        self._show_source_buttons(change)
        self.sources.clear()
        self.start_button.setEnabled(parser is not None and self.talk is None)
        self._show_command()
        QTimer.singleShot(0, self.detail_page, self.detail_page.fit)  # once the new rows are laid out

    def _select_item(self, key: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                if top.child(j).data(0, ROLE) == key and self.tree.currentItem() is not top.child(j):
                    self.tree.blockSignals(True)
                    self.tree.setCurrentItem(top.child(j))
                    self.tree.blockSignals(False)

    def _fill_games(self, change) -> None:
        self.game_box.clear()
        if change.source in ("files", "folder"):
            self.game_box.addItem(tr("convert.files_on_disk"), "")
        for key in change.games:
            self.game_box.addItem(gametree.GAMES.get(key, key), key)
        self.game_box.setEnabled(self.game_box.count() > 1)

    def _show_source_buttons(self, change) -> None:
        on_disk = change.source in ("files", "folder")
        self.add_files_button.setVisible(change.source == "files")
        self.add_folder_button.setVisible(on_disk)
        from_game = change.source in ("files", "game_paths") and bool(change.games)
        self.from_game_button.setVisible(from_game or change.candidates is not None)
        self.type_button.setVisible(change.source in ("values", "game_paths"))

    def _show_command(self) -> None:
        change = self.change
        parser = commandform.parser_of(change) if change is not None else None
        if parser is None:
            self.command.setText(tr("convert.not_in_build", command=" ".join(change.command)) if change else "")
            return
        line = commandform.argv(change, parser, ["<source>"], "<target>", self.values())
        self.command.setText(tr("convert.command_line", line=commandform.shown(program(), line)))

    def values(self) -> dict:
        """The form's values, with what the window fills where the form left them empty: the game folders of the
        settings and the entry's own ``complete`` (the source game and the installs it needs)."""
        change, values = self.change, self.form.values()
        values.update({k: getattr(self.settings, v, "") for k, v in change.folders.items() if not values.get(k)})
        if change.complete is not None:
            game = self.game_box.currentData() or ""
            values.update({k: v for k, v in change.complete(self.settings, values, game).items() if not values.get(k)})
        return values

    # sources and target
    def project_folder(self) -> Path:
        return project.folder_of(self.settings) / gamefiles.FOLDER

    def add_sources(self, items: list[str]) -> None:
        known = {self.sources.item(i).text() for i in range(self.sources.count())}
        for item in items:
            if item and item not in known:
                self.sources.addItem(item)
                known.add(item)

    def source_list(self) -> list[str]:
        return [self.sources.item(i).text() for i in range(self.sources.count())]

    def add_files(self) -> None:
        if self.change is None or self.change.source != "files":
            return
        pattern = " ".join(f"*{s}" for s in self.change.suffixes) or "*"
        names, _ = QFileDialog.getOpenFileNames(
            self, tr("convert.choose_files"), "", f"{tr(self.change.title)} ({pattern})"
        )
        self.add_sources(names)

    def add_folder(self) -> None:
        if self.change is None or self.change.source not in ("files", "folder"):
            return
        name = QFileDialog.getExistingDirectory(self, tr("convert.choose_folder"))
        self.add_sources([name] if name else [])

    def type_value(self) -> None:
        if self.change is None or self.change.source not in ("values", "game_paths"):
            return
        text, ok = QInputDialog.getText(self, tr("convert.type_title"), tr("convert.type_hint"))
        if ok:
            self.add_sources(commandform.values_of(text))

    def remove_sources(self) -> None:
        for item in self.sources.selectedItems():
            self.sources.takeItem(self.sources.row(item))

    def pick_out(self) -> None:
        name = QFileDialog.getExistingDirectory(self, tr("convert.output_folder"), self.out.text())
        if name:
            self.out.setText(name)

    def from_game(self) -> None:
        """Pick sources from the chosen game: its files (copied out as stored), its paths, or the entry's own list."""
        change, game = self.change, self.game_box.currentData() or ""
        if change is None or self.talk is not None:
            return
        if change.candidates is not None:
            self._busy(tr("convert.reading_game", game=gametree.GAMES.get(game, game or "?")))
            self.background.run(lambda: change.candidates(self.settings), self._candidates_read)
            return
        if not game:
            self.statusBar().showMessage(tr("convert.choose_game"))
            return
        if game in self.tree_cache:
            self._pick_paths(game, self.tree_cache[game])
            return
        self._busy(tr("convert.reading_game", game=gametree.GAMES.get(game, game)))
        self.background.run(lambda: gamefiles.open_tree(self.settings, game), lambda r: self._tree_read(game, r))

    def _busy(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def _candidates_read(self, result) -> None:
        if isinstance(result, Exception):
            self.statusBar().showMessage(tr("convert.game_not_read", error=result))
            return
        self.statusBar().clearMessage()
        rows = result
        chosen = pick_from_list(self, tr(self.change.title), tr("convert.pick_hint_values"),
                                lambda q: [r for r in rows if q.lower() in r[0].lower()][:400])  # fmt: skip
        self.add_sources([chosen] if chosen else [])

    def _tree_read(self, game: str, result) -> None:
        if isinstance(result, Exception):
            self.statusBar().showMessage(tr("convert.game_not_read", error=result))
            return
        self.tree_cache[game] = result
        self.statusBar().clearMessage()
        self._pick_paths(game, result)

    def _pick_paths(self, game: str, tree) -> None:
        change = self.change
        hint = tr("convert.pick_hint_paths") if change.source == "game_paths" else tr("convert.pick_hint_files")
        rows = lambda q: [(p, p) for p in gamefiles.paths(tree, change.suffixes, q)]  # noqa: E731
        chosen = pick_from_list(self, tr("convert.pick_title", game=gametree.GAMES.get(game, game)), hint, rows)
        if not chosen:
            return
        if change.source == "game_paths":
            self.add_sources([chosen])
            return
        try:
            folder = gamefiles.fresh_folder(self.settings, game)
            gamefiles.extract(tree, [chosen], folder)
        except (OSError, ValueError, KeyError) as exc:
            self.statusBar().showMessage(tr("convert.game_not_read", error=exc))
            return
        self.add_sources([str(folder)])  # the folder, so the game path (and the kind it says) is kept
        self._log(tr("convert.picked", path=chosen, folder=folder))

    # running
    def lines(self) -> list[list[str]]:
        """The command lines a Start runs: one per source when the entry runs per source, else one for all."""
        change, parser = self.change, commandform.parser_of(self.change)
        out, values, sources = Path(self.out.text().strip()), self.values(), self.source_list()
        if not change.per_input:
            return [commandform.argv(change, parser, sources, str(out), values)]
        return [
            commandform.argv(change, parser, [s], str(commandform.target_of(change, out, s)), values) for s in sources
        ]

    def start(self) -> bool:
        """Run the entry on its sources; False (and the reason in the status line) when it cannot start."""
        change = self.change
        if change is None or self.talk is not None or commandform.parser_of(change) is None:
            return False
        why = self._refused()
        if why:
            self.statusBar().showMessage(why)
            return False
        self.state["out"] = self.out.text().strip()
        lines = self.lines()
        self.talk = runner.Talk(self.talk_qt.said.emit, self.talk_qt.stepped.emit)
        talk, parser = self.talk, commandform.parser_of(change)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.statusBar().showMessage(tr("convert.running"))
        self.background.run(lambda: runner.run(change, parser, lines, talk, program()), self._finished)
        return True

    def _refused(self) -> str:
        if not self.source_list():
            return tr("convert.no_sources")
        out = self.out.text().strip()
        if not out:
            return tr("convert.no_target")
        game = gamefiles.refused_target(Path(out), self.settings)
        return tr("convert.target_in_game", game=game) if game else ""

    def stop(self) -> None:
        if self.talk is not None:
            self.talk.stop.set()
            self.statusBar().showMessage(tr("convert.stopping"))

    def _log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _step(self, done: int, total: int) -> None:
        if total > 1:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _finished(self, outcome) -> None:
        self.talk = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if isinstance(outcome, BaseException):
            message = tr("convert.job_failed", error=outcome)
        elif outcome.stopped:
            message = tr("convert.stopped")
        elif outcome.failed:
            message = tr("convert.failed_count", failed=outcome.failed, total=len(outcome.statuses))
        else:
            message = trn("convert.done", len(outcome.statuses), out=self.out.text().strip())
        self._log(message)
        self.statusBar().showMessage(message)

    def running(self) -> bool:
        return self.talk is not None

    def closeEvent(self, event) -> None:
        if self.running():
            answer = QMessageBox.question(self, tr(TITLE), tr("convert.running_close"))
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.stop()
        for _title, page in self.pages:
            if not page.close():
                event.ignore()
                return
        super().closeEvent(event)


def program() -> str:
    from ..suite import toolbox

    return toolbox.PROG


def make(shell) -> ConverterWindow:
    return ConverterWindow(shell.settings, shell.settings_path, shell)
