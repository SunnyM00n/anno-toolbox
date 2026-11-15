"""The glossary dialog of the Texts window: the person's terms for the target language in a table (a source term and its
translation per row), the game's own terms listed beside as a hint of what is already known.

``GlossaryDialog(parent, own, game)``; ``terms()`` is the table after OK.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..infra.i18n import tr


class GlossaryDialog(QDialog):
    def __init__(self, parent, own: dict[str, str], game: dict[str, str]) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Glossary"))
        self.resize(560, 480)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Source term"), tr("Translation")])
        self.table.horizontalHeader().setStretchLastSection(True)
        for term, wanted in sorted(own.items()):
            self._add(term, wanted)
        add, remove = QPushButton(tr("Add a term")), QPushButton(tr("Remove the row"))
        add.clicked.connect(lambda: self._add("", ""))
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(tr("Your terms (they win over the {count} terms read from the game's texts):", count=len(game)))
        )
        lay.addWidget(self.table)
        lay.addLayout(row)
        lay.addWidget(buttons)

    def _add(self, term: str, wanted: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(term))
        self.table.setItem(row, 1, QTableWidgetItem(wanted))

    def terms(self) -> dict[str, str]:
        found = {}
        for row in range(self.table.rowCount()):
            term, wanted = (self.table.item(row, c).text().strip() if self.table.item(row, c) else "" for c in (0, 1))
            if term and wanted:
                found[term] = wanted
        return found
