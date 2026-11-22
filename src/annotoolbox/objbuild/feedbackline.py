"""The feedback timeline along the bottom, beside the sequence editor: an item of the feedback file, a state of the
object and one of its loops (enter, repeat, leave), the loop's elements as bars over the time one play of the actor
gives them. A bar's right end is dragged to change its length, its body to move it among the others; Add, Remove,
Earlier and Later work on the chosen bar; a double-click shows its values in the properties.

``CFeedbackLine.show_doc(document)`` fills it from the feedback document; ``edits`` gives the changes to make
(``models.cfgdoc.SEdit``), ``select(path)`` a part to show. The rules: ``models.feedbackedit``.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..infra.i18n import N_, tr
from ..models import feedbackedit
from ..models.cfgdoc import SDoc
from ..models.feedbackedit import SBar
from ..suite import theme

EDGE = 5  # pixels: a press this near a bar's right end changes its length
ROW_H = 26
COLORS = {0: "#4f9fd9", 4: "#4f9fd9", 11: "#4f9fd9", 13: "#4f9fd9", 1: "#e0a030", 12: "#e0a030", 2: "#8a8a8a",
          7: "#c0504d", 6: "#9b7fd0"}  # fmt: skip
KIND_NAMES, LOOP_NAMES = feedbackedit.KINDS, feedbackedit.LOOPS
ADDABLE = (2, 1, 0, 6, 12, 10, 7)  # the kinds Add offers, the commonest first


class CBarView(QWidget):
    """The loop's bars in a row; ``retime(bar, seconds)``, ``reorder(bar, place)``, ``chosen(bar)``, ``opened(bar)``."""

    retime = Signal(object, float)
    reorder = Signal(object, int)
    chosen = Signal(object)
    opened = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_bars: list[SBar] = []
        self.m_current: SBar | None = None
        self.m_length_s = 1.0
        self._m_drag: tuple[SBar, str, float] | None = None
        self._m_x = 0.0
        self.setMinimumHeight(ROW_H + 18)
        self.setMouseTracking(True)

    def set_bars(self, bars: list[SBar]) -> None:
        self.m_bars = bars
        self.m_length_s = max([1.0, *(bar.end for bar in bars)]) * 1.1
        old = self.m_current.node if self.m_current is not None else None
        self.m_current = next((bar for bar in bars if bar.node is old), None)
        self.update()

    def _px(self, seconds: float) -> float:
        return seconds / self.m_length_s * self.width()

    def _rect(self, bar: SBar, end: float | None = None) -> QRectF:
        right = self._px(end if end is not None else bar.end)
        return QRectF(self._px(bar.start), 4, max(4.0, right - self._px(bar.start) - 1), ROW_H - 4)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.colors()["field"]))
        painter.setPen(QColor("#777"))
        for second in range(int(self.m_length_s) + 1):
            x = int(self._px(second))
            painter.drawLine(x, 0, x, 3)
        for bar in self.m_bars:
            end = None
            if self._m_drag is not None and self._m_drag[0] is bar and self._m_drag[1] == "end":
                end = max(
                    bar.start + 0.05, bar.end + (self._m_x - self._m_drag[2]) / max(1, self.width()) * self.m_length_s
                )
            rect = self._rect(bar, end)
            color = QColor(COLORS.get(bar.kind, "#888"))
            if not bar.played:
                color.setAlpha(110)
            painter.fillRect(rect, color)
            if self.m_current is not None and bar.node is self.m_current.node:
                painter.setPen(QColor("#f28c28"))
                painter.drawRect(rect)
            painter.setPen(QColor("#000"))
            painter.drawText(
                rect.adjusted(3, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, tr(KIND_NAMES.get(bar.kind, N_("ui.element")))
            )
        if self._m_drag is not None and self._m_drag[1] == "move":
            painter.setPen(QColor("#f28c28"))
            painter.drawLine(int(self._m_x), 0, int(self._m_x), self.height())
        painter.end()

    def _hit(self, x: float, y: float) -> tuple[SBar, str] | None:
        for bar in self.m_bars:
            rect = self._rect(bar)
            if rect.top() <= y <= rect.bottom() and rect.left() <= x <= rect.right() + EDGE:
                return bar, "end" if abs(x - rect.right()) <= EDGE else "move"
        return None

    def _place(self, x: float) -> int:
        """The bar index a drop at ``x`` puts the dragged bar at."""
        seconds = x / max(1, self.width()) * self.m_length_s
        return sum(1 for bar in self.m_bars if (bar.start + bar.end) / 2 < seconds)

    def mousePressEvent(self, event) -> None:
        hit = self._hit(event.position().x(), event.position().y())
        if hit is not None:
            self.m_current = hit[0]
            self._m_drag, self._m_x = (hit[0], hit[1], event.position().x()), event.position().x()
            self.chosen.emit(hit[0])
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._m_drag is None:
            hit = self._hit(event.position().x(), event.position().y())
            shape = Qt.CursorShape.SizeHorCursor if hit and hit[1] == "end" else Qt.CursorShape.ArrowCursor
            self.setCursor(shape)
            return
        self._m_x = event.position().x()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._m_drag is None:
            return
        bar, part, press_x = self._m_drag
        self._m_drag = None
        x = event.position().x()
        if part == "end" and abs(x - press_x) > 1:
            seconds = (bar.end - bar.start) + (x - press_x) / max(1, self.width()) * self.m_length_s
            self.retime.emit(bar, max(seconds, 0.0))
        elif part == "move" and abs(x - press_x) > 3:
            here = self.m_bars.index(bar)
            place = self._place(x)
            self.reorder.emit(bar, place - 1 if place > here else place)
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        hit = self._hit(event.position().x(), event.position().y())
        if hit is not None:
            self.opened.emit(hit[0])


class CFeedbackLine(QWidget):
    """The feedback file's loops: ``edits`` (a list of ``SEdit``), ``select(path)``, ``said(text)``."""

    edits = Signal(object)
    select = Signal(str)
    said = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_document: SDoc | None = None
        self.m_items: list[feedbackedit.SItem] = []
        self.m_item, self.m_state, self.m_loop = QComboBox(minimumWidth=200), QComboBox(), QComboBox()
        for k, name in enumerate(LOOP_NAMES):
            self.m_loop.addItem(tr(name), k)
        self.m_loop.setCurrentIndex(1)
        self.m_kind = QComboBox()
        for kind in ADDABLE:
            self.m_kind.addItem(tr(KIND_NAMES[kind]), kind)
        self.m_add, self.m_remove = QPushButton(tr("objects.add")), QPushButton(tr("common.remove"))
        self.m_earlier, self.m_later = QPushButton(tr("objects.earlier")), QPushButton(tr("objects.later"))
        self.m_view = CBarView()
        bar = QHBoxLayout()
        for widget in (QLabel(tr("objects.actor")), self.m_item, QLabel(tr("common.state")), self.m_state, QLabel(tr("objects.loop")), self.m_loop,
                  self.m_kind, self.m_add, self.m_remove, self.m_earlier, self.m_later):  # fmt: skip
            bar.addWidget(widget)
        bar.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.m_view)
        self.m_item.currentIndexChanged.connect(lambda _i: self._states())
        self.m_state.currentIndexChanged.connect(lambda _i: self.refresh())
        self.m_loop.currentIndexChanged.connect(lambda _i: self.refresh())
        self.m_add.clicked.connect(self._add)
        self.m_remove.clicked.connect(self._remove)
        self.m_earlier.clicked.connect(lambda: self._step(-1))
        self.m_later.clicked.connect(lambda: self._step(1))
        self.m_view.retime.connect(self._retime)
        self.m_view.reorder.connect(lambda bar, place: self._emit([feedbackedit.moved(bar, place)]))
        self.m_view.opened.connect(lambda bar: self.select.emit(feedbackedit.part_path(self.m_document, bar.node)))

    def show_doc(self, document: SDoc | None) -> None:
        """The feedback document of the object (None: it has none); the choices are kept where they still fit."""
        keep = self.m_item.currentIndex()
        self.m_document = document
        self.m_items = feedbackedit.items(document) if document is not None else []
        self.m_item.blockSignals(True)
        self.m_item.clear()
        for item in self.m_items:
            self.m_item.addItem(tr("objects.n_states", n=item.index, states=len(item.states)), item.index)
        self.m_item.setCurrentIndex(min(max(keep, 0), len(self.m_items) - 1) if self.m_items else -1)
        self.m_item.blockSignals(False)
        for widget in (self.m_add, self.m_remove, self.m_earlier, self.m_later):
            widget.setEnabled(bool(self.m_items) and document is not None and document.exact)
        self._states()

    def _states(self) -> None:
        keep = self.m_state.currentData()
        self.m_state.blockSignals(True)
        self.m_state.clear()
        item = self._item()
        for state in sorted(item.states) if item is not None else []:
            self.m_state.addItem(
                tr("objects.not_working") if state == 0 else tr("objects.working") if state == 1 else str(state), state
            )
        found = self.m_state.findData(keep)
        self.m_state.setCurrentIndex(found if found >= 0 else min(1, self.m_state.count() - 1))
        self.m_state.blockSignals(False)
        self.refresh()

    def _item(self) -> feedbackedit.SItem | None:
        k = self.m_item.currentIndex()
        return self.m_items[k] if 0 <= k < len(self.m_items) else None

    def refresh(self) -> None:
        item, state = self._item(), self.m_state.currentData()
        if self.m_document is None or item is None or state is None:
            self.m_view.set_bars([])
            return
        self.m_view.set_bars(feedbackedit.bars(self.m_document, item, int(state), int(self.m_loop.currentData())))

    def _emit(self, edits: list) -> None:
        edits = [event for event in edits if event is not None]
        if edits:
            self.edits.emit(edits)

    def _retime(self, bar: SBar, seconds: float) -> None:
        found = feedbackedit.retimed(bar, seconds)
        if not found:
            self.said.emit(tr("objects.length_element_not_own_walk"))
        self._emit(found)

    def _add(self) -> None:
        item, state = self._item(), self.m_state.currentData()
        parent = (
            feedbackedit.elements_of(item, int(state), int(self.m_loop.currentData()))
            if item and state is not None
            else None
        )
        kind = int(self.m_kind.currentData())
        node = feedbackedit.new_element(self.m_document, kind) if parent is not None else None
        if node is None:
            self.said.emit(tr("objects.no_element_kind_file_copy"))
            return
        after = self.m_view.m_current.node if self.m_view.m_current is not None else None
        self._emit([feedbackedit.added(parent, after, node, kind)])

    def _remove(self) -> None:
        if self.m_view.m_current is not None:
            self._emit([feedbackedit.removed(self.m_view.m_current)])

    def _step(self, step: int) -> None:
        bar = self.m_view.m_current
        if bar is not None:
            self._emit([feedbackedit.moved(bar, self.m_view.m_bars.index(bar) + step)])
