"""The "Manage apps" window: the apps other people added (``suite.appplugins``), what became of each, a switch to
enable or disable one (it takes effect at the next start), the apps folder and "open a file with a program".
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..infra.i18n import N_, tr
from . import appplugins, appprograms

STATUS = {
    appplugins.OK: N_("appapi.status_ok"),
    appplugins.FAILED: N_("appapi.status_failed"),
    appplugins.REFUSED: N_("appapi.status_refused"),
    appplugins.DISABLED: N_("appapi.status_disabled"),
}
KIND = {appplugins.PYTHON: N_("appapi.kind_python"), appplugins.PROGRAM: N_("appapi.kind_program")}


class _CIdle:
    """What the window's own-window bookkeeping asks of a dialog that runs no background work."""

    @staticmethod
    def busy() -> bool:
        return False


class CManageDialog(QDialog):
    """The list and its buttons; ``rows`` is what the list shows (``appplugins.SFound``)."""

    m_background = _CIdle()

    def __init__(self, shell) -> None:
        super().__init__(shell)
        self.m_shell = shell
        self.setWindowTitle(tr("appapi.manage_title"))
        self.resize(900, 420)
        layout = QVBoxLayout(self)
        note = QLabel(tr("appapi.security_note"), wordWrap=True)
        layout.addWidget(note)
        self.m_table = QTableWidget(0, 5)
        self.m_table.setHorizontalHeaderLabels(
            [
                tr("appapi.col_name"),
                tr("appapi.col_kind"),
                tr("appapi.col_status"),
                tr("appapi.col_source"),
                tr("appapi.col_note"),
            ]
        )
        self.m_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.m_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.m_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.m_table)
        self.m_hint = QLabel(wordWrap=True)
        layout.addWidget(self.m_hint)
        buttons = QHBoxLayout()
        self.m_toggle = QPushButton(tr("appapi.toggle"))
        self.m_toggle.clicked.connect(lambda _c=False: self.toggle_selected())
        folder = QPushButton(tr("appapi.open_folder"))
        folder.clicked.connect(lambda _c=False: self.open_folder())
        with_ = QPushButton(tr("appapi.open_file_with_program"))
        with_.clicked.connect(lambda _c=False: self.open_file_with())
        close = QPushButton(tr("common.close"))
        close.clicked.connect(self.accept)
        for button in (self.m_toggle, folder, with_):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.m_rows: list[appplugins.SFound] = []
        self.refresh()

    def refresh(self) -> None:
        """Every candidate again: the ones loaded at the start and the ones switched off since."""
        found = appplugins.loaded()
        disabled_ids = appplugins.disabled()
        self.m_rows = list(found)
        self.m_table.setRowCount(len(self.m_rows))
        for index, item in enumerate(self.m_rows):
            if item.id in disabled_ids:
                status = tr("appapi.status_disabled")
            elif item.status == appplugins.DISABLED:  # switched on since the start: its code loads at the next one
                status = tr("appapi.status_next_start")
            else:
                status = tr(STATUS[item.status])
            cells = (tr(item.title), tr(KIND[item.kind]), status, item.source, item.error)
            for column, text in enumerate(cells):
                self.m_table.setItem(index, column, QTableWidgetItem(text))
        self.m_hint.setText(tr("appapi.empty") if not self.m_rows else tr("appapi.restart_hint"))

    def toggle_selected(self) -> None:
        row = self.m_table.currentRow()
        if not 0 <= row < len(self.m_rows):
            return
        item = self.m_rows[row]
        appplugins.set_disabled(item.id, item.id not in appplugins.disabled())
        self.refresh()
        self.m_table.selectRow(row)

    def open_folder(self) -> bool:
        folder = appplugins.apps_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.m_hint.setText(tr("appapi.folder_not_made", error=exc))
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_file_with(self, file: str = "") -> None:
        """Pick a file, then a program of the list that opens its kind of file."""
        file = file or QFileDialog.getOpenFileName(self, tr("appapi.open_file_with_program"))[0]
        if not file:
            return
        matches = appprograms.for_file(appplugins.programs(appplugins.loaded()), file)
        if not matches:
            self.m_hint.setText(tr("appapi.no_program_for", file=file))
            return
        menu = QMenu(self)
        for program in matches:
            menu.addAction(program.title, lambda program_to_start=program: self.start(program_to_start, file))
        menu.exec(self.cursor().pos())

    def start(self, program: appprograms.SProgram, file: str) -> None:
        try:
            appprograms.launch(self.m_shell.m_settings, program, file)
            self.m_hint.setText(tr("appapi.program_started", title=program.title))
        except (OSError, ValueError) as exc:
            self.m_hint.setText(tr("appapi.program_not_started", error=exc))
