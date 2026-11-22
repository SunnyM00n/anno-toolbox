"""What the status bar says while an island comes into the island window: the steps read off the window's thread,
the panels being made, then what still loads in the background (the ground materials, the model list, the models
and textures the view draws). Plan and use: ``docs/island-editor.md`` ("Opening an island").
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QProgressBar

from ...infra.i18n import tr

STEP_MS = 250  # how often the status bar looks at what still loads


class COpenProgress(QObject):
    """A busy bar and a line in the window's status bar for as long as the island being opened has work left.
    ``step`` may be emitted from any thread (the one that reads the island): it arrives on the window's."""

    step = Signal(str)

    def __init__(self, window) -> None:
        super().__init__(window)
        self.m_window = window
        self._m_doc = None
        self.m_bar = QProgressBar(maximum=0, textVisible=False, fixedWidth=90, maximumHeight=12)
        self.m_bar.hide()
        window.statusBar().addPermanentWidget(self.m_bar)
        self.step.connect(self._say)
        self._m_timer = QTimer(self, interval=STEP_MS, timeout=self._look)

    def begin(self, name: str) -> None:
        """The island ``name`` is being asked for."""
        self.m_bar.show()
        self._say(tr("islands.opening_name", name=name))

    def reading(self, name: str) -> None:
        """Said by the reading thread when the island's file is open and its data are made."""
        self.step.emit(tr("islands.opening_name_reading", name=name))

    def building(self, name: str) -> None:
        """The island is read; the window makes its panels (this blocks the window's thread for a moment)."""
        self._say(tr("islands.opening_name_panels", name=name))
        self.m_window.statusBar().repaint()

    def follow(self, doc) -> None:
        """The island's panels are in: say what still loads until nothing does."""
        self._m_doc = doc
        self._look()
        if self.m_bar.isVisible():
            self._m_timer.start()

    def end(self) -> None:
        """Nothing is being opened any more (read, failed or closed)."""
        self._m_timer.stop()
        self.m_bar.hide()
        self._m_doc = None  # (a closed island is let go of)

    def _look(self) -> None:
        doc = self.m_window.m_doc
        mine = doc is not None and doc is self._m_doc
        left = doc.loading_text() if mine else ""
        if left:
            self.m_bar.show()
            self._say(tr("islands.name_loading_what", name=doc.m_title, what=left))
            return
        self._m_timer.stop()
        self.m_bar.hide()
        if mine:
            self._say(doc.m_last_said)
        self._m_doc = None

    def _say(self, text: str) -> None:
        self.m_window.say(text)
