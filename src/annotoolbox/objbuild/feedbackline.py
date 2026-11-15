"""The feedback timeline along the bottom, beside the sequence editor: an item of the feedback file, a state of the
object and one of its loops (enter, repeat, leave), the loop's elements as bars over the time one play of the actor
gives them. A bar's right end is dragged to change its length, its body to move it among the others; Add, Remove,
Earlier and Later work on the chosen bar; a double-click shows its values in the properties.

``FeedbackLine.show_doc(doc)`` fills it from the feedback document; ``edits`` gives the changes to make
(``models.cfgdoc.Edit``), ``select(path)`` a part to show. The rules: ``models.feedbackedit``.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..infra.i18n import N_, tr
from ..models import feedbackedit
from ..models.cfgdoc import Doc
from ..models.feedbackedit import Bar
from ..suite import theme

EDGE = 5  # pixels: a press this near a bar's right end changes its length
ROW_H = 26
COLORS = {0: "#4f9fd9", 4: "#4f9fd9", 11: "#4f9fd9", 13: "#4f9fd9", 1: "#e0a030", 12: "#e0a030", 2: "#8a8a8a",
          7: "#c0504d", 6: "#9b7fd0"}  # fmt: skip
KIND_NAMES, LOOP_NAMES = feedbackedit.KINDS, feedbackedit.LOOPS
ADDABLE = (2, 1, 0, 6, 12, 10, 7)  # the kinds Add offers, the commonest first


class BarView(QWidget):
    """The loop's bars in a row; ``retime(bar, seconds)``, ``reorder(bar, place)``, ``chosen(bar)``, ``opened(bar)``."""

    retime = Signal(object, float)
    reorder = Signal(object, int)
    chosen = Signal(object)
    opened = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.bars: list[Bar] = []
        self.current: Bar | None = None
        self.length = 1.0
        self._drag: tuple[Bar, str, float] | None = None
        self._x = 0.0
        self.setMinimumHeight(ROW_H + 18)
        self.setMouseTracking(True)

    def set_bars(self, bars: list[Bar]) -> None:
        self.bars = bars
        self.length = max([1.0, *(b.end for b in bars)]) * 1.1
        old = self.current.node if self.current is not None else None
        self.current = next((b for b in bars if b.node is old), None)
        self.update()

    def _px(self, seconds: float) -> float:
        return seconds / self.length * self.width()

    def _rect(self, b: Bar, end: float | None = None) -> QRectF:
        right = self._px(end if end is not None else b.end)
        return QRectF(self._px(b.start), 4, max(4.0, right - self._px(b.start) - 1), ROW_H - 4)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.colors()["field"]))
        p.setPen(QColor("#777"))
        for second in range(int(self.length) + 1):
            x = int(self._px(second))
            p.drawLine(x, 0, x, 3)
        for b in self.bars:
            end = None
            if self._drag is not None and self._drag[0] is b and self._drag[1] == "end":
                end = max(b.start + 0.05, b.end + (self._x - self._drag[2]) / max(1, self.width()) * self.length)
            r = self._rect(b, end)
            color = QColor(COLORS.get(b.kind, "#888"))
            if not b.played:
                color.setAlpha(110)
            p.fillRect(r, color)
            if self.current is not None and b.node is self.current.node:
                p.setPen(QColor("#f28c28"))
                p.drawRect(r)
            p.setPen(QColor("#000"))
            p.drawText(r.adjusted(3, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, tr(KIND_NAMES.get(b.kind, N_("Element"))))
        if self._drag is not None and self._drag[1] == "move":
            p.setPen(QColor("#f28c28"))
            p.drawLine(int(self._x), 0, int(self._x), self.height())
        p.end()

    def _hit(self, x: float, y: float) -> tuple[Bar, str] | None:
        for b in self.bars:
            r = self._rect(b)
            if r.top() <= y <= r.bottom() and r.left() <= x <= r.right() + EDGE:
                return b, "end" if abs(x - r.right()) <= EDGE else "move"
        return None

    def _place(self, x: float) -> int:
        """The bar index a drop at ``x`` puts the dragged bar at."""
        seconds = x / max(1, self.width()) * self.length
        return sum(1 for b in self.bars if (b.start + b.end) / 2 < seconds)

    def mousePressEvent(self, e) -> None:
        hit = self._hit(e.position().x(), e.position().y())
        if hit is not None:
            self.current = hit[0]
            self._drag, self._x = (hit[0], hit[1], e.position().x()), e.position().x()
            self.chosen.emit(hit[0])
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is None:
            hit = self._hit(e.position().x(), e.position().y())
            shape = Qt.CursorShape.SizeHorCursor if hit and hit[1] == "end" else Qt.CursorShape.ArrowCursor
            self.setCursor(shape)
            return
        self._x = e.position().x()
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is None:
            return
        bar, part, x0 = self._drag
        self._drag = None
        x = e.position().x()
        if part == "end" and abs(x - x0) > 1:
            seconds = (bar.end - bar.start) + (x - x0) / max(1, self.width()) * self.length
            self.retime.emit(bar, max(seconds, 0.0))
        elif part == "move" and abs(x - x0) > 3:
            here = self.bars.index(bar)
            place = self._place(x)
            self.reorder.emit(bar, place - 1 if place > here else place)
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:
        hit = self._hit(e.position().x(), e.position().y())
        if hit is not None:
            self.opened.emit(hit[0])


class FeedbackLine(QWidget):
    """The feedback file's loops: ``edits`` (a list of ``Edit``), ``select(path)``, ``said(text)``."""

    edits = Signal(object)
    select = Signal(str)
    said = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.doc: Doc | None = None
        self.items: list[feedbackedit.Item] = []
        self.item, self.state, self.loop = QComboBox(minimumWidth=200), QComboBox(), QComboBox()
        for k, name in enumerate(LOOP_NAMES):
            self.loop.addItem(tr(name), k)
        self.loop.setCurrentIndex(1)
        self.kind = QComboBox()
        for kind in ADDABLE:
            self.kind.addItem(tr(KIND_NAMES[kind]), kind)
        self.add, self.remove = QPushButton(tr("Add")), QPushButton(tr("Remove"))
        self.earlier, self.later = QPushButton(tr("Earlier")), QPushButton(tr("Later"))
        self.view = BarView()
        bar = QHBoxLayout()
        for w in (QLabel(tr("Actor")), self.item, QLabel(tr("State")), self.state, QLabel(tr("Loop")), self.loop,
                  self.kind, self.add, self.remove, self.earlier, self.later):  # fmt: skip
            bar.addWidget(w)
        bar.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.view)
        self.item.currentIndexChanged.connect(lambda _i: self._states())
        self.state.currentIndexChanged.connect(lambda _i: self.refresh())
        self.loop.currentIndexChanged.connect(lambda _i: self.refresh())
        self.add.clicked.connect(self._add)
        self.remove.clicked.connect(self._remove)
        self.earlier.clicked.connect(lambda: self._step(-1))
        self.later.clicked.connect(lambda: self._step(1))
        self.view.retime.connect(self._retime)
        self.view.reorder.connect(lambda bar, place: self._emit([feedbackedit.moved(bar, place)]))
        self.view.opened.connect(lambda bar: self.select.emit(feedbackedit.part_path(self.doc, bar.node)))

    def show_doc(self, doc: Doc | None) -> None:
        """The feedback document of the object (None: it has none); the choices are kept where they still fit."""
        keep = self.item.currentIndex()
        self.doc = doc
        self.items = feedbackedit.items(doc) if doc is not None else []
        self.item.blockSignals(True)
        self.item.clear()
        for it in self.items:
            self.item.addItem(tr("{n}: {states} states", n=it.index, states=len(it.states)), it.index)
        self.item.setCurrentIndex(min(max(keep, 0), len(self.items) - 1) if self.items else -1)
        self.item.blockSignals(False)
        for w in (self.add, self.remove, self.earlier, self.later):
            w.setEnabled(bool(self.items) and doc is not None and doc.exact)
        self._states()

    def _states(self) -> None:
        keep = self.state.currentData()
        self.state.blockSignals(True)
        self.state.clear()
        it = self._item()
        for state in sorted(it.states) if it is not None else []:
            self.state.addItem(tr("Not working") if state == 0 else tr("Working") if state == 1 else str(state), state)
        found = self.state.findData(keep)
        self.state.setCurrentIndex(found if found >= 0 else min(1, self.state.count() - 1))
        self.state.blockSignals(False)
        self.refresh()

    def _item(self) -> feedbackedit.Item | None:
        k = self.item.currentIndex()
        return self.items[k] if 0 <= k < len(self.items) else None

    def refresh(self) -> None:
        it, state = self._item(), self.state.currentData()
        if self.doc is None or it is None or state is None:
            self.view.set_bars([])
            return
        self.view.set_bars(feedbackedit.bars(self.doc, it, int(state), int(self.loop.currentData())))

    def _emit(self, edits: list) -> None:
        edits = [e for e in edits if e is not None]
        if edits:
            self.edits.emit(edits)

    def _retime(self, bar: Bar, seconds: float) -> None:
        found = feedbackedit.retimed(bar, seconds)
        if not found:
            self.said.emit(
                tr("the length of this element is not its own (a walk at its sequence's speed, an instant one)")
            )
        self._emit(found)

    def _add(self) -> None:
        it, state = self._item(), self.state.currentData()
        parent = (
            feedbackedit.elements_of(it, int(state), int(self.loop.currentData())) if it and state is not None else None
        )
        kind = int(self.kind.currentData())
        node = feedbackedit.new_element(self.doc, kind) if parent is not None else None
        if node is None:
            self.said.emit(tr("no element of that kind in this file to copy"))
            return
        after = self.view.current.node if self.view.current is not None else None
        self._emit([feedbackedit.added(parent, after, node, kind)])

    def _remove(self) -> None:
        if self.view.current is not None:
            self._emit([feedbackedit.removed(self.view.current)])

    def _step(self, step: int) -> None:
        bar = self.view.current
        if bar is not None:
            self._emit([feedbackedit.moved(bar, self.view.bars.index(bar) + step)])
