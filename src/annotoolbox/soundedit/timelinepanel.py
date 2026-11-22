"""The sound editor's list of a model's ``.snd`` timelines for the older games (Qt): pick a file, see per animation
sequence its length and the sounds placed on it, change the numbers, add or remove a placement.

``CTimelinePanel(say, changed)`` is the widget; ``open_document(doc)`` hands it the ``olddoc.COldSoundDoc`` whose game's
``.snd`` files it lists (the edits are the document's undo steps, so the sound window's Save and Undo carry them).
docs/sound-editor.md, "The older games".
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..formats import sndtimeline
from ..infra.i18n import N_, tr
from ..suite.widgets import ROLE, read_only_table
from . import olddoc

COLUMNS = (N_("viewer.sequence"), N_("viewer.length_ms"), N_("viewer.at_ms"), N_("viewer.sound"))
SHOWN = 300  # files listed at once: the filter narrows the thousands a game ships


class CTimelinePanel(QWidget):
    def __init__(self, say: Callable[[str], None], changed: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.m_say, self.m_changed = say, changed or (lambda: None)
        self.m_doc: olddoc.COldSoundDoc | None = None
        self.m_paths: list[str] = []
        self.m_filter = QLineEdit(placeholderText=tr("sound.filter_part_path"))
        self.m_files = QListWidget()
        self.m_table = read_only_table(COLUMNS)
        self.m_table.setEditTriggers(QTableWidget.EditTrigger.AllEditTriggers)  # the cells flagged editable take it
        buttons = QHBoxLayout()
        for text, call in (
            (N_("common.apply"), self.apply),
            (N_("sound.add_sound_dots"), self.add),
            (N_("common.remove"), self.remove),
        ):
            button = QPushButton(tr(text))
            button.clicked.connect(call)
            buttons.addWidget(button)
        left = QVBoxLayout()
        left.addWidget(self.m_filter)
        left.addWidget(self.m_files, 1)
        right = QVBoxLayout()
        right.addWidget(self.m_table, 1)
        right.addLayout(buttons)
        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addLayout(right, 2)
        self.m_filter.textChanged.connect(lambda _t: self.list_files())
        self.m_files.currentTextChanged.connect(lambda _t: self.refresh())

    def open_document(self, doc: olddoc.COldSoundDoc | None) -> None:
        self.m_doc = doc
        self.m_paths = sorted(path for path in doc.m_tree.m_paths if path.endswith(".snd")) if doc else []
        self.list_files()

    def list_files(self) -> None:
        words = self.m_filter.text().lower().split()
        found = [path for path in self.m_paths if all(word in path for word in words)]
        self.m_files.clear()
        self.m_files.addItems(found[:SHOWN])

    @property
    def path(self) -> str:
        item = self.m_files.currentItem()
        return item.text() if item is not None else ""

    def refresh(self) -> None:
        """The table of the picked file as the document has it now (after an edit, an undo or another file)."""
        self.m_table.setRowCount(0)
        if self.m_doc is None or not self.path:
            return
        try:
            timeline = self.m_doc.timeline(self.path)
        except olddoc.COldEditError as exc:
            return self.m_say(str(exc))
        for sequence in sorted(set(timeline.durations) | set(timeline.placements)):
            length = str(timeline.durations.get(sequence, ""))
            for index, place in enumerate(timeline.placements.get(sequence) or [None]):
                row = self.m_table.rowCount()
                self.m_table.insertRow(row)
                cells = [
                    str(sequence),
                    length,
                    "" if place is None else str(place.at_ms),
                    "" if place is None else str(place.sound),
                ]
                for column, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    editable = column == 1 or (column in (2, 3) and place is not None)
                    item.setFlags(
                        item.flags() | Qt.ItemFlag.ItemIsEditable
                        if editable
                        else item.flags() & ~Qt.ItemFlag.ItemIsEditable
                    )
                    self.m_table.setItem(row, column, item)
                self.m_table.item(row, 0).setData(ROLE, (sequence, index if place is not None else -1))

    def _edit(self, edit, done: str) -> None:
        try:
            self.m_doc.edit_timeline(self.path, edit)
        except olddoc.COldEditError as exc:
            return self.m_say(tr("sound.cannot_error", error=exc))
        self.refresh()
        self.m_say(done + " " + tr("sound.ctrl_plus_z_undoes"))
        self.m_changed()

    def apply(self) -> None:
        """Every number of the table that differs from the document, as one undo step."""
        if self.m_doc is None or not self.path:
            return
        try:
            now = self.m_doc.timeline(self.path)
            rows = [(self.m_table.item(row, 0).data(ROLE), [int(self.m_table.item(row, column).text() or -1) for column in (1, 2, 3)])
                    for row in range(self.m_table.rowCount())]  # fmt: skip
        except (olddoc.COldEditError, ValueError):
            return self.m_say(tr("sound.length_time_sound_whole_numbers"))

        def changes(timeline: sndtimeline.STimeline) -> sndtimeline.STimeline:
            for (sequence, index), (length, at, sound) in rows:
                if length >= 0 and length != now.durations.get(sequence):
                    timeline = sndtimeline.set_length(timeline, sequence, length)
                if index >= 0 and (at, sound) != (
                    now.placements[sequence][index].at_ms,
                    now.placements[sequence][index].sound,
                ):
                    timeline = sndtimeline.move(timeline, sequence, index, at, sound)
            return timeline

        self._edit(changes, tr("sound.changed_timeline"))

    def add(self) -> None:
        sequence = self._selected()[0]
        if sequence is None:
            return self.m_say(tr("sound.pick_row_sequence_first"))
        sound, ok = QInputDialog.getInt(
            self, tr("sound.add_sound_dots"), tr("sound.sounds_id_sounddatabase_xml"), 1, 0, 2**31 - 1
        )
        if not ok:
            return
        at, ok = QInputDialog.getInt(
            self, tr("sound.add_sound_dots"), tr("sound.ms_1_through_whole_sequence"), 0, -1, 2**31 - 1
        )
        if ok:
            self._edit(lambda tl: sndtimeline.place(tl, sequence, sound, at), tr("sound.placed_sound_id", id=sound))

    def remove(self) -> None:
        sequence, index = self._selected()
        if sequence is None or index < 0:
            return self.m_say(tr("sound.pick_row_placement_first"))
        self._edit(lambda tl: sndtimeline.unplace(tl, sequence, index), tr("sound.removed_placement"))

    def _selected(self) -> tuple[int | None, int]:
        item = self.m_table.item(self.m_table.currentRow(), 0) if self.m_table.currentRow() >= 0 else None
        return item.data(ROLE) if item is not None else (None, -1)
