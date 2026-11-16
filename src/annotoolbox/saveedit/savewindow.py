"""The Saves app's window (Qt): the saves found for a game on the left, the open save on the right as a summary, a
tree, a form of the values that may be changed and the mods it needs; a change is written as a new file next to it.

``SavesWindow(settings, settings_path)`` is the suite's Saves app. Every read and write is off
the window's thread. The logic is in ``container``, ``summary``, ``edits`` and ``needed``; ``docs/save-editor.md``.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QToolBar,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..sources import gametree
from ..suite import links
from ..suite.background import Background
from . import container, edits, locations, names, needed, nodes, summary
from .records import Section, Summary, shown
from .savetree import SaveTreeModel

APP = "saves"  # the key of its remembered state in the settings
TITLE = N_("Saves")
PATH_ROLE = Qt.ItemDataRole.UserRole
STATES = {
    needed.INSTALLED: N_("installed"),
    needed.OTHER: N_("another version installed"),
    needed.MISSING: N_("missing"),
}


class SavesWindow(QMainWindow):
    progress_text = Signal(str)  # said by a running job from its thread

    def __init__(self, settings, settings_path: Path | None = None, sync: bool = False, documents=None) -> None:
        super().__init__()
        self.settings, self.settings_path = settings, settings_path
        self.background = Background(self)
        self.sync, self.documents = sync, documents  # documents: the Documents folder (a test's), None the user's
        self.save: container.SaveFile | None = None
        self.summary: Summary | None = None
        self.fields: list[edits.Field] = []
        self.names: names.GameNames | None = None
        self.inputs: dict[str, QLineEdit] = {}
        self.needs: list[needed.Need] = []
        self.busy = False
        self._build()
        self.progress_text.connect(self.statusBar().showMessage)
        self.setWindowTitle(tr(TITLE))
        game = settings.apps.get(APP, {}).get("game", "anno117")
        self.game_box.setCurrentIndex(max(0, self.game_box.findData(game)))
        self.open_game(self.game_box.currentData())

    # the window

    def _build(self) -> None:
        bar = QToolBar(tr("Saves"), self, movable=False)
        self.addToolBar(bar)
        self.game_box = QComboBox()
        for key in locations.LOCATIONS:
            self.game_box.addItem(gametree.GAMES.get(key, key), key)
        bar.addWidget(QLabel(tr("Game") + " "))
        bar.addWidget(self.game_box)
        refresh = QPushButton(tr("Look again"))
        refresh.clicked.connect(lambda _c=False: self.open_game(self.game_box.currentData()))
        opener = QPushButton(tr("Open a file..."))
        opener.clicked.connect(lambda _c=False: self.browse())
        bar.addWidget(refresh)
        bar.addWidget(opener)
        self.list = QTreeWidget()
        self.list.setHeaderLabels([tr("Save"), tr("Bytes"), tr("Changed")])
        self.list.itemActivated.connect(self._picked)
        self.list.itemDoubleClicked.connect(self._picked)
        self.where = QLabel(wordWrap=True)
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.where)
        lay.addWidget(self.list)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._summary_tab(), tr("Summary"))
        self.tabs.addTab(self._tree_tab(), tr("Tree"))
        self.tabs.addTab(self._edit_tab(), tr("Edit"))
        self.tabs.addTab(self._mods_tab(), tr("Mods"))
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        self.setCentralWidget(split)
        self.statusBar().showMessage(tr("Pick a save on the left, or open a file."))
        self.game_box.currentIndexChanged.connect(lambda _n: self.open_game(self.game_box.currentData()))

    def _summary_tab(self) -> QWidget:
        self.summary_view = QTextBrowser(openExternalLinks=False)
        return self.summary_view

    def _tree_tab(self) -> QWidget:
        self.tree_model = SaveTreeModel(self.background, self.sync, self)
        self.tree_model.loading.connect(
            lambda path: self.statusBar().showMessage(tr("reading {path}...", path=path) if path else "")
        )
        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setUniformRowHeights(True)
        self.tree.selectionModel().currentChanged.connect(lambda cur, _old: self._show_value(cur))
        self.find_box = QLineEdit(placeholderText=tr("Find a name in what is open (Enter: the next)"))
        self.find_box.returnPressed.connect(self.find_next)
        self.value_view = QPlainTextEdit(readOnly=True)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tree)
        split.addWidget(self.value_view)
        split.setStretchFactor(0, 3)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(self.find_box)
        lay.addWidget(split)
        return page

    def _edit_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.edit_note = QLabel(wordWrap=True)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(self.form_host)
        self.write_button = QPushButton(tr("Write a new save"))
        self.write_button.setEnabled(False)
        self.write_button.clicked.connect(lambda _c=False: self.write_new())
        lay.addWidget(self.edit_note)
        lay.addWidget(scroll)
        lay.addWidget(self.write_button)
        return page

    def _mods_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.mods_note = QLabel(wordWrap=True)
        self.mods_table = QTableWidget(0, 4)
        self.mods_table.setHorizontalHeaderLabels([tr("Mod"), tr("In the save"), tr("Installed"), tr("State")])
        self.mods_table.horizontalHeader().setStretchLastSection(True)
        self.mods_button = QPushButton(tr("Show in the Mods app"))
        self.mods_button.clicked.connect(lambda _c=False: self.show_in_mods())
        lay.addWidget(self.mods_note)
        lay.addWidget(self.mods_table)
        lay.addWidget(self.mods_button)
        return page

    # the saves of a game

    def open_game(self, key: str, message: str = "") -> None:
        """List the saves found in ``key``'s folder (off the window's thread); ``message`` is said when it is done."""
        self.settings.apps.setdefault(APP, {})["game"] = key
        found = locations.folder(key, self.documents)
        self.where.setText(
            tr("Saves of {game} in {folder}", game=gametree.GAMES.get(key, key), folder=found)
            if found
            else tr("{game} keeps no saves on this computer (no folder found).", game=gametree.GAMES.get(key, key))
        )
        self.background.run(
            lambda: locations.find(key, self.documents), lambda refs: self._listed(key, refs, message), self.sync
        )

    def _listed(self, key: str, refs, message: str = "") -> None:
        if isinstance(refs, Exception) or self.game_box.currentData() != key:
            return
        self.list.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for ref in refs:
            group = groups.get(ref.group)
            if group is None:
                group = groups[ref.group] = QTreeWidgetItem(self.list, [ref.group])
                group.setExpanded(True)
            when = datetime.fromtimestamp(ref.mtime).strftime("%Y-%m-%d %H:%M")
            item = QTreeWidgetItem(group, [ref.name, f"{ref.size:,}", when])
            item.setData(0, PATH_ROLE, str(ref.path))
        self.list.resizeColumnToContents(0)
        self.statusBar().showMessage(message or tr("{n} save(s) found", n=len(refs)))

    def _picked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        path = item.data(0, PATH_ROLE)
        if path:
            self.open_path(Path(path), self.game_box.currentData())

    def browse(self) -> None:
        path, _f = QFileDialog.getOpenFileName(
            self, tr(TITLE), "", tr("Saves (*.a8s *.a7s *.a6s *.sww);;All files (*)")
        )
        if path:
            self.open_path(Path(path), locations.game_of(Path(path)) or self.game_box.currentData())

    # one save

    def open_path(self, path: Path, game: str) -> None:
        """Read the save once (the copy the app works on) and show it; off the window's thread."""
        if self.busy:
            return
        self.busy = True
        self.write_button.setEnabled(False)
        self.statusBar().showMessage(tr("reading {name}...", name=path.name))

        def work():
            save = container.open_file(path, game)
            return save, summary.summarize(save), edits.fields(save)

        self.background.run(work, self._opened, self.sync)

    def _opened(self, result) -> None:
        self.busy = False
        if isinstance(result, Exception):
            self.statusBar().showMessage(str(result))
            QMessageBox.warning(self, tr(TITLE), str(result))
            return
        self.save, self.summary, self.fields = result
        self.names = None
        self.show_summary()
        self.tree_model.set_roots(nodes.roots(self.save))
        self.value_view.clear()
        self._fill_form()
        self._check_mods()
        self.statusBar().showMessage(tr("{name} read", name=self.save.path.name))
        self._open_names(self.save)
        if self.save.game == "anno117":
            save = self.save
            self.background.run(lambda: summary.sessions(save, self._say), lambda r: self._islands(save, r), self.sync)

    def _open_names(self, save: container.SaveFile) -> None:
        """Open the installed game's assets for the numbers' names (off the window's thread; the first time it
        indexes the game)."""
        game = save.game
        self.background.run(
            lambda: names.open_names(self.settings, game), lambda r: self._names_ready(save, r), self.sync
        )

    def _names_ready(self, save: container.SaveFile, result) -> None:
        if save is not self.save:
            return
        if isinstance(result, Exception):
            self.statusBar().showMessage(tr("The names were not read: {error}", error=str(result)))
        elif result is not None:
            self.names = result
            self._apply_names()

    def _apply_names(self) -> None:
        """Name every id of the summary as it is now (off the window's thread), then show it."""
        summary_, found, save = self.summary, self.names, self.save
        if summary_ is None or found is None:
            return
        self.background.run(lambda: names.apply(summary_, found), lambda _r: self._shown_for(save), self.sync)

    def _shown_for(self, save: container.SaveFile) -> None:
        if save is self.save:
            self.show_summary()

    def _say(self, text: str, **places) -> None:
        self.progress_text.emit(tr(text, **places))

    def _islands(self, save, section) -> None:
        if save is not self.save or self.summary is None:
            return
        if isinstance(section, Exception):
            self.summary.note(N_("The sessions' islands did not read: {error}"), error=str(section))
        elif section is not None:
            self.summary.sections.insert(len(self.summary.sections) - 1, section)
        self.show_summary()
        self._apply_names()
        self.statusBar().showMessage(tr("{name} read", name=save.path.name))

    def show_summary(self) -> None:
        s, save = self.summary, self.save
        if s is None or save is None:
            return
        out = [f"<h2>{html.escape(save.path.name)}</h2>", f"<p>{html.escape(str(save.path))}</p>"]
        out += [f"<p><i>{html.escape(tr(text, **places))}</i></p>" for text, places in s.notes]
        for section in s.sections:
            out.append(_section_html(section))
        self.summary_view.setHtml("".join(out))

    def _show_value(self, index) -> None:
        item = self.tree_model.item(index)
        if item is None:
            return
        data = nodes.value_of(item)
        lines = [item.path()]
        if item.error:
            lines.append(item.error)
        if data is not None:
            lines.append(tr("{n} bytes", n=len(data)))
            lines += [f"{label}: {text}" for label, text in nodes.readings(data)]
        self.value_view.setPlainText("\n".join(lines))

    def find_next(self) -> None:
        """Select the next item whose name holds the text, among the ones opened so far."""
        text = self.find_box.text().strip()
        if not text:
            return
        hits = nodes.search(self.tree_model.top, text)
        if not hits:
            self.statusBar().showMessage(tr("{text}: not in what is open", text=text))
            return
        current = self.tree_model.item(self.tree.currentIndex())
        after = next((i + 1 for i, h in enumerate(hits) if h is current), 0)
        index = self.tree_model.index_of(hits[after % len(hits)])
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)

    # the form and the write

    def _fill_form(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.inputs = {}
        for f in self.fields:
            box = QLineEdit(str(f.value))
            box.setToolTip(tr("in the save: {value}", value=f.value))
            self.form.addRow(tr(f.label, who=f.who) if f.who else tr(f.label), box)
            self.inputs[f.key] = box
        editable = bool(self.fields)
        self.write_button.setEnabled(editable)
        self.edit_note.setText(
            tr(
                "A change is written as a new file next to this save, never over it. Not tried in the game yet: "
                "load the new file once and report what you see (docs/save-editor.md)."
            )
            if editable
            else tr("Saves of this game are read only here: no sample pinned their layout.")
        )

    def changes(self) -> dict[str, str | int]:
        """The form's values that differ from the save's, as ``edits`` takes them."""
        return edits.changed_values(self.fields, {key: box.text() for key, box in self.inputs.items()})

    def write_new(self) -> None:
        if self.save is None or self.busy:
            return
        try:
            change = self.changes()
        except ValueError as e:
            QMessageBox.warning(self, tr(TITLE), tr("Not a number: {error}", error=str(e)))
            return
        self.busy = True
        self.write_button.setEnabled(False)
        self.statusBar().showMessage(tr("writing..."))
        save = self.save
        settings = self.settings
        self.background.run(lambda: edits.write_beside(save, change, settings), self._written, self.sync)

    def _written(self, result) -> None:
        self.busy = False
        self.write_button.setEnabled(bool(self.fields))
        if isinstance(result, Exception):
            self.statusBar().showMessage(str(result))
            QMessageBox.warning(self, tr(TITLE), str(result))
            return
        said = tr("written: {path}", path=result)
        self.statusBar().showMessage(said)
        self.open_game(self.game_box.currentData(), said)

    # the mods this save needs

    def _check_mods(self) -> None:
        self.mods_table.setRowCount(0)
        self.needs = []
        s = self.summary
        if s is None or self.save is None or self.save.game not in needed.MOD_GAMES:
            self.mods_note.setText(tr("Saves of this game carry no list of mods."))
            self.mods_button.setEnabled(False)
            return
        mods, game = list(s.mods), self.save.game
        self.mods_note.setText(tr("{n} mod(s) in the save; looking at the installed mods...", n=len(mods)))
        self.background.run(
            lambda: needed.check(mods, needed.installed_mods(self.settings, game)), self._needs, self.sync
        )

    def _needs(self, result) -> None:
        if isinstance(result, Exception):
            self.mods_note.setText(str(result))
            return
        self.needs = result
        self.mods_table.setRowCount(len(result))
        for row, need in enumerate(result):
            for col, text in enumerate((need.mod_id, need.version, need.installed, tr(STATES[need.state]))):
                self.mods_table.setItem(row, col, QTableWidgetItem(text))
        missing = sum(n.state != needed.INSTALLED for n in result)
        self.mods_note.setText(
            tr("The save was made without mods.")
            if not result
            else tr("{n} mod(s) in the save, {missing} not installed in that version.", n=len(result), missing=missing)
        )
        self.mods_button.setEnabled(links.available(links.SHOW_MODS))

    def show_in_mods(self) -> None:
        """Open the Mods app on the save's game, the selected (or first installed) mod chosen."""
        if self.save is None:
            return
        row = self.mods_table.currentRow()
        ids = [n.mod_id for n in self.needs]
        if 0 <= row < len(ids):
            ids.insert(0, ids.pop(row))
        if not links.show_mods(self.save.game, ids):
            self.statusBar().showMessage(tr("The Mods app is not part of this window."))


def _section_html(section: Section) -> str:
    out = [f"<h3>{html.escape(tr(section.title))}</h3>"]
    rows = [(tr(label), shown(value)) for label, value in section.rows]
    rows += [(name, shown(value)) for name, value in section.named]  # named: the save's own spelling
    if rows:
        out.append("<table cellspacing='0' cellpadding='3'>")
        for label, value in rows:
            out.append(f"<tr><td><b>{html.escape(label)}</b></td><td>{html.escape(value)}</td></tr>")
        out.append("</table>")
    if section.table is not None and section.table.rows:
        out.append("<table border='1' cellspacing='0' cellpadding='3'><tr>")
        out += [f"<th>{html.escape(tr(h))}</th>" for h in section.table.headers]
        out.append("</tr>")
        for row in section.table.rows:
            out.append("<tr>" + "".join(f"<td>{html.escape(shown(c))}</td>" for c in row) + "</tr>")
        out.append("</table>")
    return "".join(out)
