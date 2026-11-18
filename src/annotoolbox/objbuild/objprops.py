"""The property editor: the selected part's fields in file order, grouped by 117's material headings, each edited the
way its kind needs - a switch ticks, a number is checked, a choice is a drop-down, bits, colours and file names open
a picker on a double-click of their value, a binary value shows its bytes (short ones edited as hex).

``PropertyEditor.show_fields(fields, read_only, part_kind)`` fills it; ``edited(node, value)`` is a change the user
made, ``edited_many`` several at once (a colour), ``open_file(name)`` a file name double-clicked. ``game``, ``files``
(the game's paths) and ``source_names`` tell the pickers what to offer. docs/tool-suite.md section 23.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QComboBox, QHeaderView, QTreeWidget, QTreeWidgetItem

from ..infra.i18n import tr
from ..models import cfgfields, cfgparts
from ..models.cfgdoc import BARE_AMPERSAND, Field, strip
from . import pickers

FIELD = Qt.ItemDataRole.UserRole + 1
SHORT_BINARY = 64  # bytes: a longer binary value is shown, not edited
LINK = "#4f9fd9"
SWATCH = 14  # pixels


@dataclass(frozen=True)
class Entry:
    """What a row stands for: its field, how it is edited (``switch``, ``number``, ``file``, ``colour``, ``vector``,
    ``kind``, ``text``, ``binary``, ``choice``, ``flags``, ``swatch``: the first channel of a colour group) and, for a
    swatch, the group's channels with the scale of its numbers."""

    field: Field
    kind: str
    group: tuple[tuple[str, Field], ...] = ()
    scale: int = 1


class PropertyEditor(QTreeWidget):
    edited = Signal(object, str)
    edited_many = Signal(list)  # [(node, value)]: one undo step
    open_file = Signal(str)
    refused = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.game, self.part_kind, self.source_names, self.read_only = "", "", False, False
        self.files: Callable[[], Iterable[str]] = tuple
        self.setColumnCount(2)
        self.setHeaderLabels([tr("common.field"), tr("common.value_lower")])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setStretchLastSection(True)
        self.setEditTriggers(QTreeWidget.EditTrigger.EditKeyPressed)  # a double-click goes through ``_double``
        self.itemChanged.connect(self._changed)
        self.itemDoubleClicked.connect(self._double)

    def show_fields(self, fields: list[Field], read_only: bool = False, part_kind: str = "") -> None:
        self.part_kind, self.read_only = part_kind, read_only
        self.blockSignals(True)
        self.clear()
        swatches = self._swatches(fields)
        groups: dict[str, QTreeWidgetItem] = {}
        for f in fields:
            parent = self.invisibleRootItem()
            if f.group:
                if f.group not in groups:
                    groups[f.group] = QTreeWidgetItem([f.group])
                    self.addTopLevelItem(groups[f.group])
                parent = groups[f.group]
            entry = swatches.get(id(f.node)) or Entry(f, self._kind(f))
            item = self._row(entry, read_only)
            parent.addChild(item)
            if entry.kind == "choice":
                self.setItemWidget(item, 1, self._combo(entry, read_only))
        self.expandAll()
        self.blockSignals(False)

    def _kind(self, f: Field) -> str:
        if f.binary:
            return "binary"
        name = strip(f.name)
        if cfgfields.choices(name, self.part_kind, self.game):
            return "choice"
        if cfgfields.flag_names(name, self.game) and cfgfields.flags_value(f.value) is not None:
            return "flags"
        return cfgparts.field_kind(f.name, f.value)

    def _swatches(self, fields: list[Field]) -> dict[int, Entry]:
        """The first channel of each colour group a picker can edit, by its node."""
        out = {}
        for group in cfgfields.colour_groups(fields).values():
            scale = cfgfields.colour_scale(group)
            if scale is not None:
                first = group["r"]
                out[id(first.node)] = Entry(first, "swatch", tuple(group.items()), scale)
        return out

    def _row(self, entry: Entry, read_only: bool) -> QTreeWidgetItem:
        f, kind = entry.field, entry.kind
        item = QTreeWidgetItem([f.name, self._text(entry)])
        item.setData(0, FIELD, entry)
        flags = item.flags()
        if kind == "switch":
            flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setCheckState(1, Qt.CheckState.Checked if f.value == "1" else Qt.CheckState.Unchecked)
        elif kind not in ("kind", "choice", "flags") and not (kind == "binary" and len(f.node.data) > SHORT_BINARY):
            flags |= Qt.ItemFlag.ItemIsEditable
        if read_only:
            flags &= ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsUserCheckable)
        item.setFlags(flags)
        if kind == "file":
            item.setForeground(1, QColor(LINK))
            item.setToolTip(0, tr("objects.tip.double_click_name_open_file"))
            item.setToolTip(1, tr("objects.tip.double_click_pick_one_games"))
        elif kind == "binary":
            item.setToolTip(1, tr("objects.tip.binary_value_hex"))
        elif kind == "flags":
            item.setToolTip(1, tr("objects.tip.double_click_tick_bits"))
        elif kind == "swatch":
            item.setIcon(1, self._swatch(entry))
            item.setToolTip(1, tr("objects.tip.double_click_pick_colour_f2"))
        return item

    def _text(self, entry: Entry) -> str:
        f, kind = entry.field, entry.kind
        if kind == "switch":
            return ""
        if f.binary:
            return f.value if len(f.node.data) <= SHORT_BINARY else tr(
                "objects.n_bytes_value", n=len(f.node.data), value=f.value[: 3 * SHORT_BINARY]
            )  # fmt: skip
        if kind == "flags":
            names = cfgfields.flag_names(strip(f.name), self.game)
            bits = cfgfields.flags_value(f.value) or 0
            return f"{f.value}  ({', '.join(n for k, n in enumerate(names) if bits >> k & 1)})"
        return f.value

    def _swatch(self, entry: Entry) -> QPixmap:
        group = dict(entry.group)
        shown = cfgfields.colour_channels(group, entry.scale)
        pixmap = QPixmap(SWATCH, SWATCH)
        pixmap.fill(QColor(*shown[:3]))
        return pixmap

    def _combo(self, entry: Entry, read_only: bool) -> QComboBox:
        f = entry.field
        combo = QComboBox()
        for value, label in cfgfields.choices(strip(f.name), self.part_kind, self.game):
            combo.addItem(f"{value}  {label}", value)
        at = combo.findData(f.value.strip())
        if at < 0:
            combo.addItem(tr("objects.value_not_list", value=f.value), f.value)
            at = combo.count() - 1
        combo.setCurrentIndex(at)
        combo.setEnabled(not read_only)
        combo.activated.connect(lambda k, c=combo, f=f: self._chosen(f, c.itemData(k)))
        return combo

    def _chosen(self, f: Field, value: str) -> None:
        if value != f.value:
            self.edited.emit(f.node, value)

    def _changed(self, item: QTreeWidgetItem, column: int) -> None:
        entry = item.data(0, FIELD)
        if entry is None or column != 1:
            return
        f, kind = entry.field, entry.kind
        if kind == "switch":
            value = "1" if item.checkState(1) == Qt.CheckState.Checked else "0"
        else:
            value = item.text(1).strip() if kind in ("number", "colour", "vector", "swatch") else item.text(1)
        why = problem("number" if kind == "swatch" else kind, value)
        if why:
            self.blockSignals(True)
            item.setText(1, f.value)
            self.blockSignals(False)
            self.refused.emit(f"{f.name}: {why}")
            return
        if value != f.value:
            self.edited.emit(f.node, value)

    def _double(self, item: QTreeWidgetItem, column: int) -> None:
        entry = item.data(0, FIELD)
        if entry is None:
            return
        if column == 0:
            if entry.kind == "file" and entry.field.value.strip():
                self.open_file.emit(entry.field.value.strip())
        elif self.read_only:
            return
        elif entry.kind == "file":
            self._pick_file(entry.field)
        elif entry.kind == "flags":
            self._pick_flags(entry.field)
        elif entry.kind == "swatch":
            self._pick_colour(entry)
        elif item.flags() & Qt.ItemFlag.ItemIsEditable:
            self.editItem(item, 1)

    def _pick_file(self, f: Field) -> None:
        suffixes = cfgfields.file_suffixes(strip(f.name), f.value, self.part_kind)

        def find(query: str) -> list[str]:
            return cfgfields.file_candidates(self.files(), suffixes, self.source_names, query)

        hint = ", ".join(suffixes) or tr("assets.any_file")
        found = pickers.pick_file(self, strip(f.name), hint, find, f.value)
        if found:
            value = cfgfields.file_value(found, f.value)
            why = problem("file", value)
            if why:
                self.refused.emit(f"{f.name}: {why}")
            elif value != f.value:
                self.edited.emit(f.node, value)

    def _pick_flags(self, f: Field) -> None:
        names = cfgfields.flag_names(strip(f.name), self.game)
        found = pickers.pick_flags(self, strip(f.name), names, cfgfields.flags_value(f.value) or 0)
        if found is not None and str(found) != f.value.strip():
            self.edited.emit(f.node, str(found))

    def _pick_colour(self, entry: Entry) -> None:
        group = dict(entry.group)
        picked = pickers.pick_colour(self, cfgfields.colour_channels(group, entry.scale))
        if picked is not None:
            edits = [(f.node, text) for f, text in cfgfields.colour_edits(group, picked, entry.scale)]
            if edits:
                self.edited_many.emit(edits)


def problem(kind: str, value: str) -> str:
    """Why a value typed for a field of ``kind`` is refused ("" when it is taken)."""
    if kind in ("number", "colour", "vector") and value:
        try:
            number = float(value)
        except ValueError:
            return tr("objects.value_no_number", value=repr(value))
        if not math.isfinite(number):
            return tr("objects.no_number_config_hold", value=repr(value))
    if kind == "binary":
        try:
            bytes.fromhex(value)
        except ValueError:
            return tr("objects.hex_digits_pairs_like_0a")
        return ""
    if "<" in value or ">" in value or "CDATA[" in value:
        return tr("objects.break_configs_tags_leave_out")
    if BARE_AMPERSAND.search(value):
        return tr("objects.bare_break_config_write_amp")
    return ""
