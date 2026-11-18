"""The island's slot list as a dialog (Edit > Slot list): per slot asset the slots the island's file lists, the ones
standing after the edits (what the build lists) and what the map generator reads of the asset (island-editor "The slot
list"). A readout: the slots themselves are placed, moved and deleted like any object.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...infra.i18n import N_, tr


def generator_text(row: dict) -> str:
    """What the generator does with an asset's slots: every one (``ActivateAll``), a number of them, or unknown."""
    if row.get("all") not in (None, "", "0"):
        return tr("common.all_of_them")
    if row.get("count") not in (None, ""):
        return tr("common.n_of_them", n=row["count"])
    return ""


def status_text(row: dict) -> str:
    """How the standing slots stand to the island's list: ``""`` when they agree, else what the build does."""
    if row["now"] == row["listed"]:
        return ""
    return tr("common.build_lists_n", n=row["now"])


class SlotListDialog(QDialog):
    """The rows of ``EditSession.slot_rows`` in a table."""

    COLUMNS = (
        N_("common.slot_asset"),
        N_("common.listed"),
        N_("common.standing"),
        N_("common.generator_keeps"),
        N_("common.build"),
    )

    def __init__(self, parent, rows: list[dict]) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("common.title.slot_list"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("common.map_generator_offers_islands_slots")))
        self.table = QTableWidget(len(rows), len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in self.COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for r, row in enumerate(rows):
            texts = (row["name"] or str(row["guid"]), row["listed"], row["now"], generator_text(row), status_text(row))
            for c, text in enumerate(texts):
                self.table.setItem(r, c, QTableWidgetItem(str(text)))
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("common.close"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(640, 320)


def show(main) -> None:
    """Open the dialog for the open island, or say that it has no slot list."""
    doc = main.doc
    rows = doc.session.slot_rows() if doc is not None else []
    if not rows:
        main.say(tr("common.island_lists_no_slots"))
        return
    SlotListDialog(main, rows).exec()
