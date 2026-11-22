"""The page the switcher shows for a standalone program (``suite.appprograms``): what it is, the command line it would
run now, a Start button and, when it opens files, one that picks a file for it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from ..infra.i18n import tr
from . import appprograms
from .appprograms import SProgram


class CLauncherPage(QWidget):
    """A program's page: nothing of it runs inside the Toolbox."""

    def __init__(self, program: SProgram, shell) -> None:
        super().__init__()
        self.m_program, self.m_shell = program, shell
        self.setWindowTitle(program.title)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        head = QLabel(program.title, textFormat=Qt.TextFormat.PlainText)
        font = head.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        head.setFont(font)
        layout.addWidget(head)
        layout.addWidget(QLabel(program.summary or tr("appapi.program_no_summary")))
        self.m_command = QLabel(wordWrap=True, textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.m_command)
        self.m_start = QPushButton(tr("appapi.start_program", title=program.title))
        self.m_start.clicked.connect(lambda _c=False: self.run())
        layout.addWidget(self.m_start)
        if any("{file}" in part for part in program.command):
            pick = QPushButton(tr("appapi.open_file_with", title=program.title))
            pick.clicked.connect(lambda _c=False: self.pick_file())
            layout.addWidget(pick)
        self.m_result = QLabel(wordWrap=True)
        layout.addWidget(self.m_result)
        self.refresh()

    def refresh(self) -> None:
        """The command line as it would run now (it follows the selected game and the open project)."""
        try:
            arguments = appprograms.command_line(
                self.m_program, appprograms.values_for(self.m_shell.m_settings, self.m_program)
            )
            self.m_command.setText(tr("appapi.would_run", command=" ".join(arguments)))
            self.m_start.setEnabled(True)
        except ValueError as exc:
            self.m_command.setText(str(exc))
            self.m_start.setEnabled(False)

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)

    def run(self, file: str = "") -> bool:
        """Start the program (with ``file`` for its ``{file}``); the result, or why not, is said on the page."""
        try:
            appprograms.launch(self.m_shell.m_settings, self.m_program, file)
        except (OSError, ValueError) as exc:
            self.m_result.setText(tr("appapi.program_not_started", error=exc))
            return False
        self.m_result.setText(tr("appapi.program_started", title=self.m_program.title))
        return True

    def pick_file(self) -> None:
        types = " ".join("*" + file_type for file_type in self.m_program.file_types)
        filter_ = f"{self.m_program.title} ({types});;{tr('appapi.all_files')} (*)" if types else ""
        name, _ = QFileDialog.getOpenFileName(
            self, tr("appapi.open_file_with", title=self.m_program.title), "", filter_
        )
        if name:
            self.run(name)
