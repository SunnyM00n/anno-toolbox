"""The Saves app's window (Qt): the saves found for a game on the left, the open save on the right as a summary, a
tree, a form of the values that may be changed, its ships (``shipspanel``) and the mods it needs; a change is written
as a new file next to it.

``CSavesWindow(settings, settings_path)`` is the suite's Saves app. Every read and write is off
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
from ..suite.background import CBackground, CJobTalk, CStopped
from . import container, edits, locations, names, needed, nodes, summary
from .records import CSummary, SSection, shown
from .savetree import CSaveTreeModel
from .shipspanel import CShipsPanel

APP = "saves"  # the key of its remembered state in the settings
TITLE = N_("saves.saves")
PATH_ROLE = Qt.ItemDataRole.UserRole
STATES = {
    needed.INSTALLED: N_("common.installed_lower"),
    needed.OTHER: N_("saves.another_version_installed"),
    needed.MISSING: N_("ui.missing"),
}


class CSavesWindow(QMainWindow):
    progress_text = Signal(str)  # said by a running job from its thread

    def __init__(
        self, settings, settings_path: Path | None = None, is_synchronous: bool = False, documents=None
    ) -> None:
        super().__init__()
        self.m_settings, self.m_settings_path = settings, settings_path
        self.m_background = CBackground(self)
        self.m_is_synchronous, self.m_documents = (
            is_synchronous,
            documents,
        )  # documents: the Documents folder (a test's), None the user's
        self.m_save: container.SSaveFile | None = None
        self.m_summary: CSummary | None = None
        self.m_fields: list[edits.SField] = []
        self.m_names: names.CGameNames | None = None
        self.m_inputs: dict[str, QLineEdit | QComboBox] = {}
        self.m_form_labels: dict[str, QLabel] = {}
        self.m_needs: list[needed.SNeed] = []
        self.m_busy = False
        self.m_reading: Path | None = None  # the save being read; ``busy`` without it is a write
        self.m_talk: CJobTalk | None = None  # that read's stop
        self._build()
        self.m_stop_button = widgets.stop_button(self, self.stop_reading)
        self.progress_text.connect(self.statusBar().showMessage)
        self.setWindowTitle(tr(TITLE))
        game = settings.apps.get(APP, {}).get("game", "anno117")
        self.m_game_box.blockSignals(True)  # the listing below is the one this start makes
        self.m_game_box.setCurrentIndex(max(0, self.m_game_box.findData(game)))
        self.m_game_box.blockSignals(False)
        self.open_game(self.m_game_box.currentData())

    # the window

    def _build(self) -> None:
        bar = QToolBar(tr("saves.saves"), self, movable=False)
        self.addToolBar(bar)
        self.m_game_box = QComboBox()
        for key in locations.LOCATIONS:
            self.m_game_box.addItem(gametree.GAMES.get(key, key), key)
        bar.addWidget(QLabel(tr("common.game") + " "))
        bar.addWidget(self.m_game_box)
        refresh = QPushButton(tr("saves.look_again"))
        refresh.clicked.connect(lambda _checked=False: self.open_game(self.m_game_box.currentData()))
        opener = QPushButton(tr("saves.open_file"))
        opener.clicked.connect(lambda _checked=False: self.browse())
        for button, keys, tip in (
            (refresh, "F5", N_("saves.tip.look_again")),
            (opener, "Ctrl+O", N_("saves.tip.open_file")),
        ):
            button.setShortcut(QKeySequence(keys))
            button.setToolTip(tr(tip))
        bar.addWidget(refresh)
        bar.addWidget(opener)
        self.m_list = QTreeWidget()
        self.m_list.setHeaderLabels([tr("common.save"), tr("saves.bytes"), tr("saves.changed")])
        self.m_list.itemActivated.connect(self._picked)
        self.m_list.itemDoubleClicked.connect(self._picked)
        self.m_where = QLabel(wordWrap=True)
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.m_where)
        lay.addWidget(self.m_list)
        self.m_tabs = QTabWidget()
        self.m_ships_panel = CShipsPanel(self)  # before the Edit tab: the write state covers its button
        self.m_tabs.addTab(self._summary_tab(), tr("saves.summary"))
        self.m_tabs.addTab(self._tree_tab(), tr("saves.tree"))
        self.m_tabs.addTab(self._edit_tab(), tr("common.edit"))
        self.m_tabs.addTab(self.m_ships_panel, tr("saves.ships"))
        self.m_tabs.addTab(self._mods_tab(), tr("common.mods"))
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(self.m_tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        self.setCentralWidget(split)
        self.statusBar().showMessage(tr("saves.pick_save_left_open_file"))
        self.m_game_box.currentIndexChanged.connect(lambda _index: self.open_game(self.m_game_box.currentData()))

    def _summary_tab(self) -> QWidget:
        self.m_summary_view = QTextBrowser(openExternalLinks=False)
        return self.m_summary_view

    def _tree_tab(self) -> QWidget:
        self.m_tree_model = CSaveTreeModel(self.m_background, self.m_is_synchronous, self)
        self.m_tree_model.loading.connect(
            lambda path: self.statusBar().showMessage(tr("saves.reading_path", path=path) if path else "")
        )
        self.m_tree = QTreeView()
        self.m_tree.setModel(self.m_tree_model)
        self.m_tree.setUniformRowHeights(True)
        self.m_tree.selectionModel().currentChanged.connect(lambda current, _old: self._show_value(current))
        self.m_find_box = QLineEdit(placeholderText=tr("saves.find_name_what_open_enter"))
        self.m_find_box.returnPressed.connect(self.find_next)
        self.m_value_view = QPlainTextEdit(readOnly=True)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.m_tree)
        split.addWidget(self.m_value_view)
        split.setStretchFactor(0, 3)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(self.m_find_box)
        lay.addWidget(split)
        return page

    def _edit_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.m_edit_note = QLabel(wordWrap=True)
        self.m_form_host = QWidget()
        self.m_form = QFormLayout(self.m_form_host)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(self.m_form_host)
        self.m_write_button = QPushButton(tr("saves.write_new_save"))
        self.m_write_button.clicked.connect(lambda _checked=False: self.write_new())
        self._write_state()
        lay.addWidget(self.m_edit_note)
        lay.addWidget(scroll)
        lay.addWidget(self.m_write_button)
        return page

    def _mods_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.m_mods_note = QLabel(wordWrap=True)
        self.m_mods_table = QTableWidget(0, 4)
        self.m_mods_table.setHorizontalHeaderLabels(
            [tr("mods.mod"), tr("saves.in_the_save"), tr("common.installed"), tr("common.state")]
        )
        self.m_mods_table.horizontalHeader().setStretchLastSection(True)
        self.m_mods_button = QPushButton(tr("saves.show_mods_app"))
        self.m_mods_button.clicked.connect(lambda _checked=False: self.show_in_mods())
        lay.addWidget(self.m_mods_note)
        lay.addWidget(self.m_mods_table)
        lay.addWidget(self.m_mods_button)
        return page

    # the saves of a game

    def open_game(self, key: str, message: str = "") -> None:
        """List the saves found in ``key``'s folder (off the window's thread); ``message`` is said when it is done."""
        if not gamesync.may_switch(self, key):  # another app's question was cancelled: the box goes back
            self.m_game_box.blockSignals(True)
            self.m_game_box.setCurrentIndex(self.m_game_box.findData(self.m_settings.apps.get(APP, {}).get("game")))
            self.m_game_box.blockSignals(False)
            return
        self.m_settings.apps.setdefault(APP, {})["game"] = key
        found = locations.folder(key, self.m_documents)
        self.m_where.setText(
            tr("saves.saves_game_folder", game=gametree.GAMES.get(key, key), folder=found)
            if found
            else tr("saves.keeps_no_saves_computer_folder", game=gametree.GAMES.get(key, key))
        )
        self.m_background.run(
            lambda: locations.find(key, self.m_documents),
            lambda refs: self._listed(key, refs, message),
            self.m_is_synchronous,
        )

    def _listed(self, key: str, refs: list[locations.SSaveRef] | Exception, message: str = "") -> None:
        if self.m_game_box.currentData() != key:
            return
        if isinstance(refs, Exception):
            self.m_list.clear()
            self.statusBar().showMessage(str(refs))
            return
        self.m_list.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for ref in refs:
            group = groups.get(ref.group)
            if group is None:
                group = groups[ref.group] = QTreeWidgetItem(self.m_list, [ref.group])
                group.setExpanded(True)
            when = datetime.fromtimestamp(ref.mtime).strftime("%Y-%m-%d %H:%M")
            item = QTreeWidgetItem(group, [ref.name, f"{ref.size_bytes:,}", when])
            item.setData(0, PATH_ROLE, str(ref.path))
        self.m_list.resizeColumnToContents(0)
        self.statusBar().showMessage(message or tr("saves.n_saves_found", n=len(refs)))

    def _picked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        path = item.data(0, PATH_ROLE)
        if path:
            self.open_path(Path(path), self.m_game_box.currentData())

    def browse(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, tr(TITLE), "", tr("saves.saves_a8s_a7s_a6s_sww"))
        if path:
            self.open_path(Path(path), locations.game_of(Path(path), self.m_game_box.currentData()))

    # one save

    def open_path(self, path: Path, game: str) -> None:
        """Read the save once (the copy the app works on) and show it; off the window's thread. A save picked
        while another is read takes its place (said); one picked while a file is written waits for the person."""
        if self.m_busy and self.m_reading is None:
            self.statusBar().showMessage(tr("saves.wait_for_write", name=path.name))
            return
        before = self.m_reading
        if self.m_talk is not None:
            self.m_talk.stop()  # the read before ends at its next step; its answer is dropped
        talk = self.m_talk = CJobTalk(self)
        self.m_busy, self.m_reading = True, path
        self._write_state()
        self.m_stop_button.show()
        self.statusBar().showMessage(
            tr("saves.reading_instead", name=path.name, before=before.name)
            if before is not None
            else tr("saves.reading_name", name=path.name)
        )

        def work() -> tuple[container.SSaveFile, CSummary, list[edits.SField]]:
            save = container.open_file(path, game)
            talk.say()  # between the steps: where a stopped read ends
            found = summary.summarize(save)
            talk.say()
            return save, found, _fields_or_note(save, found)

        self.m_background.run(
            work, lambda result: self._opened(result) if talk is self.m_talk else None, self.m_is_synchronous
        )

    def stop_reading(self) -> None:
        """Give up the read under way: the save shown before stays the open one."""
        if self.m_reading is None:
            return
        name = self.m_reading.name
        self.m_talk.stop()
        self._read_ended()
        self._write_state()
        self.statusBar().showMessage(tr("saves.read_stopped", name=name))

    def _read_ended(self) -> None:
        self.m_busy, self.m_reading, self.m_talk = False, None, None
        self.m_stop_button.hide()

    def _opened(self, result) -> None:
        self._read_ended()
        if isinstance(result, CStopped):
            return
        if isinstance(result, Exception):
            self._write_state()  # the save shown before is still the open one
            self.statusBar().showMessage(str(result))
            QMessageBox.warning(self, tr(TITLE), str(result))
            return
        self.m_save, self.m_summary, self.m_fields = result
        self.m_names = None
        self.show_summary()
        self.m_tree_model.set_roots(nodes.roots(self.m_save))
        self.m_value_view.clear()
        self._fill_form()
        self._check_mods()
        self.m_ships_panel.set_save(self.m_save)
        self.statusBar().showMessage(tr("saves.name_read", name=self.m_save.path.name))
        self._open_names(self.m_save)
        if self.m_save.game == "anno117":
            save = self.m_save
            self.m_background.run(
                lambda: summary.sessions(save, self.say),
                lambda sessions: self._islands(save, sessions),
                self.m_is_synchronous,
            )

    def _open_names(self, save: container.SSaveFile) -> None:
        """Open the installed game's assets for the numbers' names (off the window's thread; the first time it
        indexes the game)."""
        game = save.game
        self.m_background.run(
            lambda: names.open_names(self.m_settings, game),
            lambda result: self._names_ready(save, result),
            self.m_is_synchronous,
        )

    def _names_ready(self, save: container.SSaveFile, result) -> None:
        if save is not self.m_save:
            return
        if isinstance(result, Exception):
            self.statusBar().showMessage(tr("saves.names_not_read", error=str(result)))
        elif result is not None:
            self.m_names = result
            self._apply_names()
            self._name_form()
            self.m_ships_panel.show_names()

    def _apply_names(self) -> None:
        """Name every id of the summary as it is now (off the window's thread), then show it."""
        summary_, found, save = self.m_summary, self.m_names, self.m_save
        if summary_ is None or found is None:
            return
        self.m_background.run(
            lambda: names.apply(summary_, found), lambda _result: self._shown_for(save), self.m_is_synchronous
        )

    def _shown_for(self, save: container.SSaveFile) -> None:
        if save is self.m_save:
            self.show_summary()

    def say(self, text: str, **places) -> None:
        """A job's progress on the status line (a string id and its places; any thread)."""
        self.progress_text.emit(tr(text, **places))

    def _islands(self, save: container.SSaveFile, section: SSection | Exception | None) -> None:
        if save is not self.m_save or self.m_summary is None:
            return
        if isinstance(section, Exception):
            self.m_summary.note(N_("saves.sessions_islands_not_read"), error=str(section))
        elif section is not None:
            self.m_summary.m_sections.insert(len(self.m_summary.m_sections) - 1, section)
        self.show_summary()
        self._apply_names()
        self.statusBar().showMessage(tr("saves.name_read", name=save.path.name))

    def show_summary(self) -> None:
        summary_data, save = self.m_summary, self.m_save
        if summary_data is None or save is None:
            return
        out = [f"<h2>{html.escape(save.path.name)}</h2>", f"<p>{html.escape(str(save.path))}</p>"]
        out += [f"<p><i>{html.escape(tr(text, **places))}</i></p>" for text, places in summary_data.m_notes]
        for section in summary_data.m_sections:
            out.append(_section_html(section))
        self.m_summary_view.setHtml("".join(out))

    def _show_value(self, index: QModelIndex) -> None:
        item = self.m_tree_model.item(index)
        if item is None:
            return
        data = nodes.value_of(item)
        lines = [item.path()]
        if item.m_error:
            lines.append(item.m_error)
        if data is not None:
            lines.append(tr("viewer.n_bytes", n=len(data)))
            lines += [f"{label}: {text}" for label, text in nodes.readings(data)]
        self.m_value_view.setPlainText("\n".join(lines))

    def find_next(self) -> None:
        """Select the next item whose name holds the text, among the ones opened so far."""
        text = self.m_find_box.text().strip()
        if not text:
            return
        hits = nodes.search(self.m_tree_model.m_top, text)
        if not hits:
            self.statusBar().showMessage(tr("saves.not_what_open", text=text))
            return
        current = self.m_tree_model.item(self.m_tree.currentIndex())
        after = next((i + 1 for i, hit in enumerate(hits) if hit is current), 0)
        index = self.m_tree_model.index_of(hits[after % len(hits)])
        self.m_tree.setCurrentIndex(index)
        self.m_tree.scrollTo(index)

    # the form and the write

    def _fill_form(self) -> None:
        while self.m_form.rowCount():
            self.m_form.removeRow(0)
        self.m_inputs, self.m_form_labels = {}, {}
        for field in self.m_fields:
            box = self._choice_box(field) if field.choices else QLineEdit(str(field.value))
            box.setToolTip(tr("saves.tip.in_the_save_value", value=field.value))
            label = QLabel()
            self.m_form.addRow(label, box)
            self.m_inputs[field.key], self.m_form_labels[field.key] = box, label
        self._name_form()
        editable = bool(self.m_fields)
        self._write_state()
        self.m_edit_note.setText(
            tr("saves.change_written_new_file_next") if editable else tr("saves.saves_game_read_only_here")
        )

    def _write_state(self) -> None:
        """The Write button is on for a save of a game whose layout is known and says why it is not otherwise."""
        if self.m_busy:
            why = tr("saves.tip.write_busy")
        elif self.m_fields:
            why = ""
        elif self.m_save is None:
            why = tr("saves.tip.write_open_save_first")
        else:
            why = tr("saves.saves_game_read_only_here")
        widgets.enable_unless(self.m_write_button, why, tr("saves.tip.write_new_save"))
        self.m_ships_panel.update_button()

    def _choice_box(self, field: edits.SField) -> QComboBox:
        box = QComboBox()
        for guid in field.choices:
            box.addItem(str(guid), str(guid))
        box.setCurrentIndex(max(0, box.findData(str(field.value))))
        return box

    def _name_form(self) -> None:
        """The form's labels and lists with the names of the GUIDs they show, once the game's names are read."""
        name = self.m_names.asset if self.m_names is not None else None
        for field in self.m_fields:
            who = edits.who_text(field, name)
            self.m_form_labels[field.key].setText(tr(field.label, who=who) if who else tr(field.label))
            box = self.m_inputs[field.key]
            if isinstance(box, QComboBox):
                for i in range(box.count()):
                    box.setItemText(i, edits.guid_label(int(box.itemData(i)), name))

    def changes(self) -> dict[str, str | int]:
        """The form's values that differ from the save's, as ``edits`` takes them."""
        typed = {
            k: widget.currentData() if isinstance(widget, QComboBox) else widget.text()
            for k, widget in self.m_inputs.items()
        }
        return edits.changed_values(self.m_fields, typed)

    def write_new(self) -> None:
        if self.m_save is None or self.m_busy:
            return
        try:
            change = self.changes()
        except ValueError as e:
            QMessageBox.warning(self, tr(TITLE), tr("saves.not_number_error", error=str(e)))
            return
        save = self.m_save
        settings = self.m_settings
        self.run_write(lambda: edits.write_beside(save, change, settings))

    def run_write(self, work) -> None:
        """Write a new file off the window's thread (``work`` returns its path), the window busy until it is done;
        the result is said and the saves are listed again. Refused (said) while a save is read or written: a ship's
        delete is asked for after its plan, and another job may have started in the meantime."""
        if self.m_busy:
            self.statusBar().showMessage(tr("saves.tip.write_busy"))
            return
        self.m_busy = True
        self._write_state()
        self.statusBar().showMessage(tr("saves.writing"))
        self.m_background.run(work, self._written, self.m_is_synchronous)

    def _written(self, result) -> None:
        self.m_busy = False
        self._write_state()
        if isinstance(result, Exception):
            self.statusBar().showMessage(str(result))
            QMessageBox.warning(self, tr(TITLE), str(result))
            return
        said = tr("saves.written_path", path=result)
        self.statusBar().showMessage(said)
        self.open_game(self.m_game_box.currentData(), said)

    # the mods this save needs

    def _check_mods(self) -> None:
        self.m_mods_table.setRowCount(0)
        self.m_needs = []
        summary_data = self.m_summary
        if summary_data is None or self.m_save is None or self.m_save.game not in needed.MOD_GAMES:
            self.m_mods_note.setText(tr("saves.saves_game_carry_no_list"))
            self.m_mods_button.setEnabled(False)
            return
        mods, game, save = list(summary_data.m_mods), self.m_save.game, self.m_save
        self.m_mods_note.setText(tr("saves.mods_save_looking_installed", n=len(mods)))
        self.m_background.run(
            lambda: needed.check(mods, needed.installed_mods(self.m_settings, game)),
            lambda result: self._needs(save, result),
            self.m_is_synchronous,
        )

    def _needs(self, save: container.SSaveFile, result) -> None:
        if save is not self.m_save:
            return  # another save was opened while the mods were looked up
        if isinstance(result, Exception):
            self.m_mods_note.setText(str(result))
            return
        self.m_needs = result
        self.m_mods_table.setRowCount(len(result))
        for row, need in enumerate(result):
            for column, text in enumerate((need.mod_id, need.version, need.installed, tr(STATES[need.state]))):
                self.m_mods_table.setItem(row, column, QTableWidgetItem(text))
        missing = sum(entry.state != needed.INSTALLED for entry in result)
        self.m_mods_note.setText(
            tr("saves.save_made_without_mods")
            if not result
            else tr("saves.mods_save_not_installed_version", n=len(result), missing=missing)
        )
        self.m_mods_button.setEnabled(links.available(links.SHOW_MODS))

    def show_in_mods(self) -> None:
        """Open the Mods app on the save's game, the selected (or first installed) mod chosen."""
        if self.m_save is None:
            return
        row = self.m_mods_table.currentRow()
        ids = [need.mod_id for need in self.m_needs]
        if 0 <= row < len(ids):
            ids.insert(0, ids.pop(row))
        if not links.show_mods(self.m_save.game, ids):
            self.statusBar().showMessage(tr("saves.mods_app_not_part_window"))


def _fields_or_note(save: container.SSaveFile, found: CSummary) -> list[edits.SField]:
    """The save's editable values; a save whose values do not read is shown read only, with the reason as a note
    of its summary ``found`` (its summary and tree still open)."""
    try:
        return edits.fields(save)
    except (ValueError, struct.error) as e:
        found.note(N_("saves.values_not_read"), error=str(e))
        return []


def _section_html(section: SSection) -> str:
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
        out += [f"<th>{html.escape(tr(header))}</th>" for header in section.table.headers]
        out.append("</tr>")
        for row in section.table.rows:
            out.append("<tr>" + "".join(f"<td>{html.escape(shown(cell))}</td>" for cell in row) + "</tr>")
        out.append("</table>")
    return "".join(out)
