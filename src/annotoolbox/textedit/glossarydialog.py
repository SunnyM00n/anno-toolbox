"""The glossary dialog of the Texts window: the person's terms for the target language in a table (a source term and its
translation per row), the game's own terms listed beside as a hint of what is already known.

``CGlossaryDialog(parent, own, game)``; ``terms()`` is the table after OK.
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


class CGlossaryDialog(QDialog):
    def __init__(self, parent, own: dict[str, str], game: dict[str, str]) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("texts.title.glossary"))
        self.resize(560, 480)
        self.m_table = QTableWidget(0, 2)
        self.m_table.setHorizontalHeaderLabels([tr("texts.source_term"), tr("texts.translation")])
        self.m_table.horizontalHeader().setStretchLastSection(True)
        for term, wanted in sorted(own.items()):
            self._add(term, wanted)
        add, remove = QPushButton(tr("texts.add_term")), QPushButton(tr("texts.remove_row"))
        add.clicked.connect(lambda: self._add("", ""))
        remove.clicked.connect(lambda: self.m_table.removeRow(self.m_table.currentRow()))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("texts.your_terms_they_win_over", count=len(game))))
        lay.addWidget(self.m_table)
        lay.addLayout(row)
        lay.addWidget(buttons)

    def _add(self, term: str, wanted: str) -> None:
        row = self.m_table.rowCount()
        self.m_table.insertRow(row)
        self.m_table.setItem(row, 0, QTableWidgetItem(term))
        self.m_table.setItem(row, 1, QTableWidgetItem(wanted))

    def terms(self) -> dict[str, str]:
        found = {}
        for row in range(self.m_table.rowCount()):
            term, wanted = (
                self.m_table.item(row, column).text().strip() if self.m_table.item(row, column) else ""
                for column in (0, 1)
            )
            if term and wanted:
                found[term] = wanted
        return found
