"""The sequence editor along the bottom, as the studio's Sequence Editor: the config's sequences, play, pause, speed,
and the chosen sequence's tracks with their elements as bars (blue an animation, orange an effect, grey a state).
A bar is dragged to move it, by its ends to change its start or end; a double-click selects the element so its
values show in the properties; a click elsewhere seeks.

``SequenceEditor.show_doc(doc, scene)`` fills it from the config's document; ``edits`` gives the changes to make
(``models.cfgdoc.Edit``), ``changed(seconds, animations)`` what the 3D view plays. docs/tool-suite.md section 23.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..infra.i18n import number as shown_number
from ..infra.i18n import tr
from ..models import cfgdoc, cfgparts
from ..models.cfgdoc import Doc, Node, Part
from ..sources.assetdb import decimal, number
from ..suite import theme
from ..viewer.panels import TRACK_COLORS, sequence_animations

EDGE = 5  # pixels: a press this near a bar's end moves that end
SNAP_MS = 10
ROW_H = 22


@dataclass
class Bar:
    """One track element: the track's row, its nodes, its times and type."""

    row: int
    node: Node
    path: str
    kind: int
    start: int
    end: int


def _leaf(node: Node, name: str) -> Node | None:
    return next((c for c in node.children if cfgdoc.strip(c.name) == name and not c.children), None)


def _int(node: Node, name: str) -> int:
    leaf = _leaf(node, name)
    return number(leaf.text if leaf is not None else None)


def bars(sequence: Part) -> list[Bar]:
    """The elements of a sequence part's tracks, a row per track."""
    out = []
    tracks = [c for c in sequence.children if c.node.name == "Track"]
    for row, track in enumerate(tracks):
        for element in (c for c in track.children if c.node.name == "TrackElement"):
            n = element.node
            out.append(Bar(row, n, element.path, _int(n, "Type"), _int(n, "StartTime"), _int(n, "EndTime")))
    return out


class TrackView(QWidget):
    seek = Signal(float)
    moved = Signal(object, int, int)  # bar, new start, new end (ms)
    chosen = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.bars: list[Bar] = []
        self.rows, self.length_ms, self.time, self.speed = 1, 1000, 0.0, 1.0
        self._drag: tuple[Bar, str, float, int, int] | None = None
        self._preview: tuple[int, int] | None = None
        self.setMinimumHeight(ROW_H * 2 + 8)
        self.setMouseTracking(True)

    def set_bars(self, found: list[Bar], rows: int, speed: float) -> None:
        self.bars, self.rows, self.speed = found, max(1, rows), speed if speed > 0 else 1.0
        self.length_ms = max([1000, *(b.end for b in found)]) * 1.1
        self.setMinimumHeight(ROW_H * self.rows + 8)
        self.update()

    def set_time(self, seconds: float) -> None:
        self.time = seconds
        self.update()

    def _x(self, ms: float) -> float:
        return ms / self.length_ms * self.width()

    def _ms(self, x: float) -> int:
        return int(round(x / max(1, self.width()) * self.length_ms / SNAP_MS)) * SNAP_MS

    def _rect(self, b: Bar, span: tuple[int, int] | None = None) -> QRectF:
        start, end = span or (b.start, b.end)
        return QRectF(self._x(start), 4 + b.row * ROW_H, max(3.0, self._x(end) - self._x(start)), ROW_H - 4)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.colors()["field"]))
        p.setPen(QColor("#777"))
        for second in range(int(self.length_ms // 1000) + 1):
            x = self._x(second * 1000)
            p.drawLine(int(x), 0, int(x), 3)
        for b in self.bars:
            span = self._preview if self._drag is not None and self._drag[0] is b else None
            p.fillRect(self._rect(b, span), QColor(TRACK_COLORS.get(b.kind, "#888")))
        played = self.time * self.speed * 1000
        duration = max([1, *(b.end for b in self.bars)])
        x = self._x(played % duration)
        p.setPen(QColor("#f28c28"))
        p.drawLine(int(x), 0, int(x), self.height())
        p.end()

    def _hit(self, x: float, y: float) -> tuple[Bar, str] | None:
        for b in reversed(self.bars):
            r = self._rect(b)
            if r.top() <= y <= r.bottom() and r.left() - EDGE <= x <= r.right() + EDGE:
                if abs(x - r.left()) <= EDGE:
                    return b, "start"
                if abs(x - r.right()) <= EDGE:
                    return b, "end"
                return b, "move"
        return None

    def mousePressEvent(self, e) -> None:
        x, y = e.position().x(), e.position().y()
        hit = self._hit(x, y)
        if hit is None:
            self.seek.emit(self._ms(x) / 1000 / self.speed)
            return
        bar, part = hit
        self._drag, self._preview = (bar, part, x, bar.start, bar.end), (bar.start, bar.end)

    def mouseMoveEvent(self, e) -> None:
        x = e.position().x()
        if self._drag is None:
            hit = self._hit(x, e.position().y())
            shape = Qt.CursorShape.SizeHorCursor if hit and hit[1] != "move" else Qt.CursorShape.ArrowCursor
            self.setCursor(shape)
            return
        bar, part, x0, start, end = self._drag
        step = self._ms(x) - self._ms(x0)
        if part == "move":
            step = max(step, -start)
            self._preview = (start + step, end + step)
        elif part == "start":
            self._preview = (min(max(0, start + step), end - SNAP_MS), end)
        else:
            self._preview = (start, max(end + step, start + SNAP_MS))
        self.update()

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag is not None:
            bar = self._drag[0]
            start, end = self._preview
            self._drag = None
            if (start, end) != (bar.start, bar.end):
                self.moved.emit(bar, start, end)
            self.update()

    def mouseDoubleClickEvent(self, e) -> None:
        hit = self._hit(e.position().x(), e.position().y())
        if hit is not None:
            self.chosen.emit(hit[0].path)


class SequenceEditor(QWidget):
    """The config's sequences: ``edits`` (a list of ``Edit``), ``changed(seconds, animations)``, ``playing``,
    ``select(path)`` (an element to show in the properties)."""

    edits = Signal(object)
    changed = Signal(float, object)
    playing = Signal(bool)
    select = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.doc: Doc | None = None
        self.scene = None
        self.sequences: list[Part] = []
        self.choice = QComboBox(minimumWidth=260)
        self.play = QPushButton(tr("Play"), checkable=True)
        self.speed = QDoubleSpinBox(minimum=0.1, maximum=4.0, singleStep=0.1, value=1.0, prefix=tr("speed "))
        self.clock = QLabel(tr("{seconds} s", seconds=shown_number(0, 2)))
        self.add_track = QPushButton(tr("Add track"))
        self.add_element = QPushButton(tr("Add element"))
        self.view = TrackView()
        bar = QHBoxLayout()
        for w in (
            QLabel(tr("Sequence")),
            self.choice,
            self.play,
            self.speed,
            self.clock,
            self.add_track,
            self.add_element,
        ):
            bar.addWidget(w)
        bar.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.view)
        self.choice.currentIndexChanged.connect(lambda _i: self._chosen())
        self.play.toggled.connect(self.playing.emit)
        self.view.seek.connect(lambda t: self.changed.emit(t, self.animations(t)))
        self.view.moved.connect(self._moved)
        self.view.chosen.connect(self.select.emit)
        self.add_track.clicked.connect(self._add_track)
        self.add_element.clicked.connect(self._add_element)

    def show_doc(self, doc: Doc | None, scene) -> None:
        """The sequences of a config document (None: none), drawn with ``scene``'s parts (for the animations)."""
        keep = self.choice.currentIndex()
        self.doc, self.scene = doc, scene
        root = doc.parts() if doc is not None else None
        folder = next((c for c in root.children if c.is_list and c.section == "Sequences"), None) if root else None
        self.sequences = folder.children if folder is not None else []
        self.choice.blockSignals(True)
        self.choice.clear()
        for part in self.sequences:
            sid = _int(part.node, "SequenceID")
            text = f"{sid} ({sid >> 24} / {(sid >> 8) & 0xFFFF} / {sid & 0xFF})" if sid >= 1 << 24 else str(sid)
            self.choice.addItem(text)
        self.choice.setCurrentIndex(min(max(keep, 0), len(self.sequences) - 1) if self.sequences else -1)
        self.choice.blockSignals(False)
        for w in (self.add_track, self.add_element):
            w.setEnabled(bool(self.sequences))
        self._refresh()

    def _current(self) -> Part | None:
        k = self.choice.currentIndex()
        return self.sequences[k] if 0 <= k < len(self.sequences) else None

    def _refresh(self) -> None:
        seq = self._current()
        found = bars(seq) if seq is not None else []
        rows = len([c for c in seq.children if c.node.name == "Track"]) if seq is not None else 1
        speed = _float(seq.node, "Speed") if seq is not None else 1.0
        self.view.set_bars(found, rows, speed)

    def _chosen(self) -> None:
        self._refresh()
        self.changed.emit(0.0, self.animations(0.0))

    def animations(self, seconds: float) -> dict:
        """What the view plays at ``seconds`` (``viewer.panels.sequence_animations`` over the scene's sequences)."""
        k = self.choice.currentIndex()
        if self.scene is None or not 0 <= k < len(self.scene.sequences):
            return {}
        return sequence_animations(self.scene, self.scene.sequences[k], seconds)

    def tick(self, seconds: float) -> None:
        self.view.set_time(seconds)
        seq = self._current()
        speed = _float(seq.node, "Speed") if seq is not None else 1.0
        length = max([1, *(b.end for b in self.view.bars)]) / 1000
        self.clock.setText(tr("{seconds} s", seconds=shown_number((seconds * (speed or 1.0)) % length, 2)))

    def _moved(self, bar: Bar, start: int, end: int) -> None:
        changes = []
        for name, value, old in (("StartTime", start, bar.start), ("EndTime", end, bar.end)):
            leaf = _leaf(bar.node, name)
            if leaf is not None and value != old:
                changes.append(cfgdoc.set_value(leaf, str(value), f"{name} = {value}"))
        self.edits.emit(changes)

    def _add_track(self) -> None:
        seq = self._current()
        track = cfgparts.new_part(self.doc, "Track") if seq is not None else None
        if track is None:
            return
        one_element(track)
        tracks = [c.node for c in seq.children if c.node.name == "Track"]
        ids = [_int(t, "TrackID") for t in tracks]
        trackid = _leaf(track, "TrackID")
        if trackid is not None:
            trackid.text = str(max(ids, default=0) + 1)
        at = _after(seq.node, tracks[-1] if tracks else None)
        self.edits.emit([cfgdoc.insert(seq.node, at, [track], tr("add a track"))])

    def _add_element(self) -> None:
        seq = self._current()
        tracks = [c.node for c in seq.children if c.node.name == "Track"] if seq is not None else []
        element = cfgparts.new_part(self.doc, "TrackElement") if tracks else None
        if element is None:
            return
        elements = [c for c in tracks[-1].children if c.name == "TrackElement"]
        at = _after(tracks[-1], elements[-1] if elements else None)
        self.edits.emit([cfgdoc.insert(tracks[-1], at, [element], tr("add a track element"))])


def one_element(track: Node) -> None:
    """A track copied from the file keeps its first element only: the new track must not play every animation of
    the one it was copied from."""
    elements = [c for c in track.children if cfgdoc.strip(c.name) == "TrackElement"]
    for extra in elements[1:]:
        track.children.remove(extra)


def _float(node: Node, name: str) -> float:
    leaf = _leaf(node, name)
    return decimal(leaf.text if leaf is not None else None, 1.0)


def _after(parent: Node, last: Node | None) -> int:
    """Where a new child goes: after ``last``, else before 2070's closing ``m_ConfigType``, else at the end."""
    if last is not None:
        return next(i for i, c in enumerate(parent.children) if c is last) + 1
    closing = [i for i, c in enumerate(parent.children) if c.name == "m_ConfigType"]
    return closing[-1] if closing else len(parent.children)
