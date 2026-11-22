"""The shader progress bar of the window's status bar, beside the game link: the game look's shader compiles in flight
(``infra.shadertrack``: any thread reports, the bar changes on the window's thread through a signal). A part of the
start-up stage shows "done of total" (``render.shaderstart.run``); compiles of an island opened meanwhile show as a
bar that only moves. The current compile is the tooltip; nothing compiles, no bar.

``attach(window)`` makes the bar and puts it into the window's status bar; the window keeps it as ``shader_bar``.
docs/suite.md "The shader progress bar".
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QProgressBar, QToolButton, QWidget

from ..infra import shadertrack
from ..infra.i18n import tr

WIDTH = 150  # pixels [ours]


class CShaderBar(QObject):
    """The bar and its feed; ``changed`` carries the snapshot from the thread that reports to the window's."""

    changed = Signal(object)

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.m_widget = QWidget()  # the bar and the pause button, shown together
        layout = QHBoxLayout(self.m_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.m_bar = QProgressBar()
        self.m_bar.setFixedWidth(WIDTH)
        self.m_bar.setTextVisible(True)
        self.m_pause = QToolButton()
        self.m_pause.setAutoRaise(True)
        self.m_pause.clicked.connect(lambda _c=False: shadertrack.pause(not shadertrack.paused()))
        layout.addWidget(self.m_bar)
        layout.addWidget(self.m_pause)
        self.m_widget.hide()
        self.changed.connect(self.show_state)
        self._m_remove = shadertrack.on_change(self._emit)
        self.show_state(shadertrack.snapshot())

    def _emit(self, state: shadertrack.SSnapshot) -> None:
        try:
            self.changed.emit(state)  # queued when it comes from another thread: the UI thread is never waited for
        except RuntimeError:  # the window closed meanwhile
            self.close()

    def show_state(self, state: shadertrack.SSnapshot) -> None:
        if not state.busy:
            self.m_widget.hide()
            return
        paused = state.paused
        self.m_pause.setVisible(bool(state.total))  # an island's own compiles are not the stage's to hold
        self.m_pause.setText(tr("common.shader_resume") if paused else tr("common.shader_pause"))
        self.m_pause.setToolTip(tr("common.shader_resume_tip") if paused else tr("common.shader_pause_tip"))
        if state.total:  # a part of the start-up stage: n of m
            self.m_bar.setRange(0, state.total)
            self.m_bar.setValue(state.done)
            self.m_bar.setFormat(tr("common.shader_progress_format"))
        else:  # compiles of the island's own: no total to count to
            self.m_bar.setRange(0, 0)
            self.m_bar.setFormat(tr("common.shader_compiling_format", count=len(state.active)))
        current = state.active[-1] if state.active else ""
        self.m_bar.setToolTip(
            tr("common.shader_progress_tip_now", what=current) if current else tr("common.shader_progress_tip")
        )
        self.m_widget.show()

    def close(self) -> None:
        self._m_remove()


def attach(window: QMainWindow) -> CShaderBar:
    """The bar in ``window``'s status bar (permanent: on the right, before what is added after it)."""
    shader = CShaderBar(window)
    window.statusBar().addPermanentWidget(shader.m_widget)
    window.destroyed.connect(lambda *_: shader.close())
    return shader
