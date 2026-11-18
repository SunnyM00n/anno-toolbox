"""The Saves app's window (Qt): the saves found for a game on the left, the open save on the right as a summary, a
tree, a form of the values that may be changed and the mods it needs; a change is written as a new file next to it.

``SavesWindow(settings, settings_path)`` is the suite's Saves app. Every read and write is off
the window's thread. The logic is in ``container``, ``summary``, ``edits`` and ``needed``; ``docs/save-editor.md``.
"""

from __future__ import annotations

import html
import struct
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QKeySequence
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
from ..suite import gamesync, links, widgets
from ..suite.background import Background, JobTalk, Stopped
from . import container, edits, locations, names, needed, nodes, summary
from .records import Section, Summary, shown
from .savetree import SaveTreeModel

APP = "saves"  # the key of its remembered state in the settings
TITLE = N_("saves.saves")
PATH_ROLE = Qt.ItemDataRole.UserRole
STATES = {
    needed.INSTALLED: N_("common.installed_lower"),
    needed.OTHER: N_("saves.another_version_installed"),
    needed.MISSING: N_("ui.missing"),
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
        self.inputs: dict[str, QLineEdit | QComboBox] = {}
        self.form_labels: dict[str, QLabel] = {}
        self.needs: list[needed.Need] = []
        self.busy = False
        self.reading: Path | None = None  # the save being read; ``busy`` without it is a write
        self.talk: JobTalk | None = None  # that read's stop
        self._build()
        self.stop_button = widgets.stop_button(self, self.stop_reading)
        self.progress_text.connect(self.statusBar().showMessage)
        self.setWindowTitle(tr(TITLE))
        game = settings.apps.get(APP, {}).get("game", "anno117")
        self.game_box.blockSignals(True)  # the listing below is the one this start makes
        self.game_box.setCurrentIndex(max(0, self.game_box.findData(game)))
        self.game_box.blockSignals(False)
        self.open_game(self.game_box.currentData())

    # the window

    def _build(self) -> None:
        bar = QToolBar(tr("saves.saves"), self, movable=False)
        self.addToolBar(bar)
        self.game_box = QComboBox()
        for key in locations.LOCATIONS:
            self.game_box.addItem(gametree.GAMES.get(key, key), key)
        bar.addWidget(QLabel(tr("common.game") + " "))
        bar.addWidget(self.game_box)
        refresh = QPushButton(tr("saves.look_again"))
        refresh.clicked.connect(lambda _c=False: self.open_game(self.game_box.currentData()))
        opener = QPushButton(tr("saves.open_file"))
        opener.clicked.connect(lambda _c=False: self.browse())
        for button, keys, tip in (
            (refresh, "F5", N_("saves.tip.look_again")),
            (opener, "Ctrl+O", N_("saves.tip.open_file")),
        ):
            button.setShortcut(QKeySequence(keys))
            button.setToolTip(tr(tip))
        bar.addWidget(refresh)
        bar.addWidget(opener)
        self.list = QTreeWidget()
        self.list.setHeaderLabels([tr("common.save"), tr("saves.bytes"), tr("saves.changed")])
        self.list.itemActivated.connect(self._picked)
        self.list.itemDoubleClicked.connect(self._picked)
        self.where = QLabel(wordWrap=True)
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.where)
        lay.addWidget(self.list)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._summary_tab(), tr("saves.summary"))
        self.tabs.addTab(self._tree_tab(), tr("saves.tree"))
        self.tabs.addTab(self._edit_tab(), tr("common.edit"))
        self.tabs.addTab(self._mods_tab(), tr("common.mods"))
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        self.setCentralWidget(split)
        self.statusBar().showMessage(tr("saves.pick_save_left_open_file"))
        self.game_box.currentIndexChanged.connect(lambda _n: self.open_game(self.game_box.currentData()))

    def _summary_tab(self) -> QWidget:
        self.summary_view = QTextBrowser(openExternalLinks=False)
        return self.summary_view

    def _tree_tab(self) -> QWidget:
        self.tree_model = SaveTreeModel(self.background, self.sync, self)
        self.tree_model.loading.connect(
            lambda path: self.statusBar().showMessage(tr("saves.reading_path", path=path) if path else "")
        )
        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setUniformRowHeights(True)
        self.tree.selectionModel().currentChanged.connect(lambda cur, _old: self._show_value(cur))
        self.find_box = QLineEdit(placeholderText=tr("saves.find_name_what_open_enter"))
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
        self.write_button = QPushButton(tr("saves.write_new_save"))
        self.write_button.clicked.connect(lambda _c=False: self.write_new())
        self._write_state()
        lay.addWidget(self.edit_note)
        lay.addWidget(scroll)
        lay.addWidget(self.write_button)
        return page

    def _mods_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.mods_note = QLabel(wordWrap=True)
        self.mods_table = QTableWidget(0, 4)
        self.mods_table.setHorizontalHeaderLabels(
            [tr("mods.mod"), tr("saves.in_the_save"), tr("common.installed"), tr("common.state")]
        )
        self.mods_table.horizontalHeader().setStretchLastSection(True)
        self.mods_button = QPushButton(tr("saves.show_mods_app"))
        self.mods_button.clicked.connect(lambda _c=False: self.show_in_mods())
        lay.addWidget(self.mods_note)
        lay.addWidget(self.mods_table)
        lay.addWidget(self.mods_button)
        return page

    # the saves of a game

    def open_game(self, key: str, message: str = "") -> None:
        """List the saves found in ``key``'s folder (off the window's thread); ``message`` is said when it is done."""
        if not gamesync.may_switch(self, key):  # another app's question was cancelled: the box goes back
            self.game_box.blockSignals(True)
            self.game_box.setCurrentIndex(self.game_box.findData(self.settings.apps.get(APP, {}).get("game")))
            self.game_box.blockSignals(False)
            return
        self.settings.apps.setdefault(APP, {})["game"] = key
        found = locations.folder(key, self.documents)
        self.where.setText(
            tr("saves.saves_game_folder", game=gametree.GAMES.get(key, key), folder=found)
            if found
            else tr("saves.keeps_no_saves_computer_folder", game=gametree.GAMES.get(key, key))
        )
        self.background.run(
            lambda: locations.find(key, self.documents), lambda refs: self._listed(key, refs, message), self.sync
        )

    def _listed(self, key: str, refs: list[locations.SaveRef] | Exception, message: str = "") -> None:
        if self.game_box.currentData() != key:
            return
        if isinstance(refs, Exception):
            self.list.clear()
            self.statusBar().showMessage(str(refs))
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
        self.statusBar().showMessage(message or tr("saves.n_saves_found", n=len(refs)))

    def _picked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        path = item.data(0, PATH_ROLE)
        if path:
            self.open_path(Path(path), self.game_box.currentData())

    def browse(self) -> None:
        path, _f = QFileDialog.getOpenFileName(self, tr(TITLE), "", tr("saves.saves_a8s_a7s_a6s_sww"))
        if path:
            self.open_path(Path(path), locations.game_of(Path(path), self.game_box.currentData()))

    # one save

    def open_path(self, path: Path, game: str) -> None:
        """Read the save once (the copy the app works on) and show it; off the window's thread. A save picked
        while another is read takes its place (said); one picked while a file is written waits for the person."""
        if self.busy and self.reading is None:
            self.statusBar().showMessage(tr("saves.wait_for_write", name=path.name))
            return
        before = self.reading
        if self.talk is not None:
            self.talk.stop()  # the read before ends at its next step; its answer is dropped
        talk = self.talk = JobTalk(self)
        self.busy, self.reading = True, path
        self._write_state()
        self.stop_button.show()
        self.statusBar().showMessage(
            tr("saves.reading_instead", name=path.name, before=before.name)
            if before is not None
            else tr("saves.reading_name", name=path.name)
        )

        def work() -> tuple[container.SaveFile, Summary, list[edits.Field]]:
            save = container.open_file(path, game)
            talk.say()  # between the steps: where a stopped read ends
            found = summary.summarize(save)
            talk.say()
            return save, found, _fields_or_note(save, found)

        self.background.run(work, lambda result: self._opened(result) if talk is self.talk else None, self.sync)

    def stop_reading(self) -> None:
        """Give up the read under way: the save shown before stays the open one."""
        if self.reading is None:
            return
        name = self.reading.name
        self.talk.stop()
        self._read_ended()
        self._write_state()
        self.statusBar().showMessage(tr("saves.read_stopped", name=name))

    def _read_ended(self) -> None:
        self.busy, self.reading, self.talk = False, None, None
        self.stop_button.hide()

    def _opened(self, result) -> None:
        self._read_ended()
        if isinstance(result, Stopped):
            return
        if isinstance(result, Exception):
            self._write_state()  # the save shown before is still the open one
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
        self.statusBar().showMessage(tr("saves.name_read", name=self.save.path.name))
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
            self.statusBar().showMessage(tr("saves.names_not_read", error=str(result)))
        elif result is not None:
            self.names = result
            self._apply_names()
            self._name_form()

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

    def _islands(self, save: container.SaveFile, section: Section | Exception | None) -> None:
        if save is not self.save or self.summary is None:
            return
        if isinstance(section, Exception):
            self.summary.note(N_("saves.sessions_islands_not_read"), error=str(section))
        elif section is not None:
            self.summary.sections.insert(len(self.summary.sections) - 1, section)
        self.show_summary()
        self._apply_names()
        self.statusBar().showMessage(tr("saves.name_read", name=save.path.name))

    def show_summary(self) -> None:
        s, save = self.summary, self.save
        if s is None or save is None:
            return
        out = [f"<h2>{html.escape(save.path.name)}</h2>", f"<p>{html.escape(str(save.path))}</p>"]
        out += [f"<p><i>{html.escape(tr(text, **places))}</i></p>" for text, places in s.notes]
        for section in s.sections:
            out.append(_section_html(section))
        self.summary_view.setHtml("".join(out))

    def _show_value(self, index: QModelIndex) -> None:
        item = self.tree_model.item(index)
        if item is None:
            return
        data = nodes.value_of(item)
        lines = [item.path()]
        if item.error:
            lines.append(item.error)
        if data is not None:
            lines.append(tr("viewer.n_bytes", n=len(data)))
            lines += [f"{label}: {text}" for label, text in nodes.readings(data)]
        self.value_view.setPlainText("\n".join(lines))

    def find_next(self) -> None:
        """Select the next item whose name holds the text, among the ones opened so far."""
        text = self.find_box.text().strip()
        if not text:
            return
        hits = nodes.search(self.tree_model.top, text)
        if not hits:
            self.statusBar().showMessage(tr("saves.not_what_open", text=text))
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
        self.inputs, self.form_labels = {}, {}
        for f in self.fields:
            box = self._choice_box(f) if f.choices else QLineEdit(str(f.value))
            box.setToolTip(tr("saves.tip.in_the_save_value", value=f.value))
            label = QLabel()
            self.form.addRow(label, box)
            self.inputs[f.key], self.form_labels[f.key] = box, label
        self._name_form()
        editable = bool(self.fields)
        self._write_state()
        self.edit_note.setText(
            tr("saves.change_written_new_file_next") if editable else tr("saves.saves_game_read_only_here")
        )

    def _write_state(self) -> None:
        """The Write button is on for a save of a game whose layout is known and says why it is not otherwise."""
        if self.busy:
            why = tr("saves.tip.write_busy")
        elif self.fields:
            why = ""
        elif self.save is None:
            why = tr("saves.tip.write_open_save_first")
        else:
            why = tr("saves.saves_game_read_only_here")
        widgets.enable_unless(self.write_button, why, tr("saves.tip.write_new_save"))

    def _choice_box(self, field: edits.Field) -> QComboBox:
        box = QComboBox()
        for guid in field.choices:
            box.addItem(str(guid), str(guid))
        box.setCurrentIndex(max(0, box.findData(str(field.value))))
        return box

    def _name_form(self) -> None:
        """The form's labels and lists with the names of the GUIDs they show, once the game's names are read."""
        name = self.names.asset if self.names is not None else None
        for f in self.fields:
            who = edits.who_text(f, name)
            self.form_labels[f.key].setText(tr(f.label, who=who) if who else tr(f.label))
            box = self.inputs[f.key]
            if isinstance(box, QComboBox):
                for i in range(box.count()):
                    box.setItemText(i, edits.guid_label(int(box.itemData(i)), name))

    def changes(self) -> dict[str, str | int]:
        """The form's values that differ from the save's, as ``edits`` takes them."""
        typed = {k: b.currentData() if isinstance(b, QComboBox) else b.text() for k, b in self.inputs.items()}
        return edits.changed_values(self.fields, typed)

    def write_new(self) -> None:
        if self.save is None or self.busy:
            return
        try:
            change = self.changes()
        except ValueError as e:
            QMessageBox.warning(self, tr(TITLE), tr("saves.not_number_error", error=str(e)))
            return
        self.busy = True
        self._write_state()
        self.statusBar().showMessage(tr("saves.writing"))
        save = self.save
        settings = self.settings
        self.background.run(lambda: edits.write_beside(save, change, settings), self._written, self.sync)

    def _written(self, result) -> None:
        self.busy = False
        self._write_state()
        if isinstance(result, Exception):
            self.statusBar().showMessage(str(result))
            QMessageBox.warning(self, tr(TITLE), str(result))
            return
        said = tr("saves.written_path", path=result)
        self.statusBar().showMessage(said)
        self.open_game(self.game_box.currentData(), said)

    # the mods this save needs

    def _check_mods(self) -> None:
        self.mods_table.setRowCount(0)
        self.needs = []
        s = self.summary
        if s is None or self.save is None or self.save.game not in needed.MOD_GAMES:
            self.mods_note.setText(tr("saves.saves_game_carry_no_list"))
            self.mods_button.setEnabled(False)
            return
        mods, game, save = list(s.mods), self.save.game, self.save
        self.mods_note.setText(tr("saves.mods_save_looking_installed", n=len(mods)))
        self.background.run(
            lambda: needed.check(mods, needed.installed_mods(self.settings, game)),
            lambda r: self._needs(save, r),
            self.sync,
        )

    def _needs(self, save: container.SaveFile, result) -> None:
        if save is not self.save:
            return  # another save was opened while the mods were looked up
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
            tr("saves.save_made_without_mods")
            if not result
            else tr("saves.mods_save_not_installed_version", n=len(result), missing=missing)
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
            self.statusBar().showMessage(tr("saves.mods_app_not_part_window"))


def _fields_or_note(save: container.SaveFile, found: Summary) -> list[edits.Field]:
    """The save's editable values; a save whose values do not read is shown read only, with the reason as a note
    of its summary ``found`` (its summary and tree still open)."""
    try:
        return edits.fields(save)
    except (ValueError, struct.error) as e:
        found.note(N_("saves.values_not_read"), error=str(e))
        return []


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
