"""The dialogs of the property editor's typed fields: a file among the game's (a search over a list, the value
editable), the bits of a flag field, and a colour.

``pick_file``, ``pick_flags`` and ``pick_colour`` return what was chosen, None when cancelled.
docs/tool-suite.md section 23, "The typed editors".
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QLabel, QListWidget, QListWidgetItem, QVBoxLayout

from ..infra.i18n import tr
from ..suite.widgets import ok_cancel, pick_from_list, pick_qcolor


def pick_file(parent, name: str, hint: str, find: Callable[[str], list[str]], current: str) -> str | None:
    """The file name for a field: ``find(query)`` lists the game's files, ``current`` is the value now."""

    def rows(query: str) -> list[tuple[str, object]]:
        return [(path, path) for path in find(query)]

    title = tr("Pick a file for {name}", name=name)
    return pick_from_list(parent, title, hint, rows, value=current, size=(640, 480))


class FlagsDialog(QDialog):
    """The bits of a number as a check list (bit 0 first); the bits the names do not cover stay as they are."""

    def __init__(self, parent, name: str, names: tuple[str, ...], value: int) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Bits of {name}", name=name))
        self.names, self.kept = names, value & ~((1 << len(names)) - 1)
        self.list = QListWidget()
        for bit, text in enumerate(names):
            item = QListWidgetItem(f"{bit}  {text}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if value >> bit & 1 else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.total = QLabel()
        layout = QVBoxLayout(self)
        for widget in (self.list, self.total, ok_cancel(self)):
            layout.addWidget(widget)
        self.list.itemChanged.connect(lambda _i: self.total.setText(tr("= {value}", value=self.value())))
        self.total.setText(tr("= {value}", value=self.value()))
        self.resize(360, 520)

    def value(self) -> int:
        bits = sum(1 << k for k in range(self.list.count()) if self.list.item(k).checkState() == Qt.CheckState.Checked)
        return bits | self.kept


def pick_flags(parent, name: str, names: tuple[str, ...], value: int) -> int | None:
    dialog = FlagsDialog(parent, name, names, value)
    return dialog.value() if dialog.exec() else None


def pick_colour(parent, channels: list[int]) -> list[int] | None:
    """A colour's channels (r, g, b and a when there are four, 0 to 255) from the system's dialog."""
    chosen = pick_qcolor(parent, QColor(*channels), alpha=len(channels) == 4)
    if chosen is None:
        return None
    out = [chosen.red(), chosen.green(), chosen.blue()]
    return out + [chosen.alpha()] if len(channels) == 4 else out
