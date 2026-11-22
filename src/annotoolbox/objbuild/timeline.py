"""The sequence editor along the bottom, as the studio's Sequence Editor: the config's sequences, play, pause, speed,
and the chosen sequence's tracks with their elements as bars (blue an animation, orange an effect, grey a state).
A bar is dragged to move it, by its ends to change its start or end; a double-click selects the element so its
values show in the properties; a click elsewhere seeks.

``CSequenceEditor.show_doc(document, scene)`` fills it from the config's document; ``edits`` gives the changes to make
(``models.cfgdoc.SEdit``), ``changed(seconds, animations)`` what the 3D view plays. docs/tool-suite.md section 23.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..infra.i18n import number as shown_number
from ..infra.i18n import tr
from ..models import cfgdoc, cfgparts
from ..models.cfgdoc import SDoc, SNode, SPart
from ..sources.assetdb import decimal, number
from ..suite import theme
from ..viewer.panels import TRACK_COLORS, sequence_animations

EDGE = 5  # pixels: a press this near a bar's end moves that end
SNAP_MS = 10
ROW_H = 22


@dataclass
class SBar:
    """One track element: the track's row, its nodes, its times and type."""

    row: int
    node: SNode
    path: str
    kind: int
    start: int
    end: int


def _leaf(node: SNode, name: str) -> SNode | None:
    return next((child for child in node.children if cfgdoc.strip(child.name) == name and not child.children), None)


def _int(node: SNode, name: str) -> int:
    leaf = _leaf(node, name)
    return number(leaf.text if leaf is not None else None)


def bars(sequence: SPart) -> list[SBar]:
    """The elements of a sequence part's tracks, a row per track."""
    out = []
    tracks = [child for child in sequence.children if child.node.name == "Track"]
    for row, track in enumerate(tracks):
        for element in (child for child in track.children if child.node.name == "TrackElement"):
            node = element.node
            out.append(
                SBar(row, node, element.path, _int(node, "Type"), _int(node, "StartTime"), _int(node, "EndTime"))
            )
    return out


class CTrackView(QWidget):
    seek = Signal(float)
    moved = Signal(object, int, int)  # bar, new start, new end (ms)
    chosen = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_bars: list[SBar] = []
        self.m_rows, self.m_length_ms, self.m_time, self.m_speed = 1, 1000, 0.0, 1.0
        self._m_drag: tuple[SBar, str, float, int, int] | None = None
        self._m_preview: tuple[int, int] | None = None
        self.setMinimumHeight(ROW_H * 2 + 8)
        self.setMouseTracking(True)

    def set_bars(self, found: list[SBar], rows: int, speed: float) -> None:
        self.m_bars, self.m_rows, self.m_speed = found, max(1, rows), speed if speed > 0 else 1.0
        self.m_length_ms = max([1000, *(bar.end for bar in found)]) * 1.1
        self.setMinimumHeight(ROW_H * self.m_rows + 8)
        self.update()

    def set_time(self, seconds: float) -> None:
        self.m_time = seconds
        self.update()

    def _x(self, time_ms: float) -> float:
        return time_ms / self.m_length_ms * self.width()

    def _ms(self, x: float) -> int:
        return int(round(x / max(1, self.width()) * self.m_length_ms / SNAP_MS)) * SNAP_MS

    def _rect(self, bar: SBar, span: tuple[int, int] | None = None) -> QRectF:
        start, end = span or (bar.start, bar.end)
        return QRectF(self._x(start), 4 + bar.row * ROW_H, max(3.0, self._x(end) - self._x(start)), ROW_H - 4)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.colors()["field"]))
        painter.setPen(QColor("#777"))
        for second in range(int(self.m_length_ms // 1000) + 1):
            x = self._x(second * 1000)
            painter.drawLine(int(x), 0, int(x), 3)
        for bar in self.m_bars:
            span = self._m_preview if self._m_drag is not None and self._m_drag[0] is bar else None
            painter.fillRect(self._rect(bar, span), QColor(TRACK_COLORS.get(bar.kind, "#888")))
        played = self.m_time * self.m_speed * 1000
        duration_ms = max([1, *(bar.end for bar in self.m_bars)])
        x = self._x(played % duration_ms)
        painter.setPen(QColor("#f28c28"))
        painter.drawLine(int(x), 0, int(x), self.height())
        painter.end()

    def _hit(self, x: float, y: float) -> tuple[SBar, str] | None:
        for bar in reversed(self.m_bars):
            rect = self._rect(bar)
            if rect.top() <= y <= rect.bottom() and rect.left() - EDGE <= x <= rect.right() + EDGE:
                if abs(x - rect.left()) <= EDGE:
                    return bar, "start"
                if abs(x - rect.right()) <= EDGE:
                    return bar, "end"
                return bar, "move"
        return None

    def mousePressEvent(self, event) -> None:
        x, y = event.position().x(), event.position().y()
        hit = self._hit(x, y)
        if hit is None:
            self.seek.emit(self._ms(x) / 1000 / self.m_speed)
            return
        bar, part = hit
        self._m_drag, self._m_preview = (bar, part, x, bar.start, bar.end), (bar.start, bar.end)

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        if self._m_drag is None:
            hit = self._hit(x, event.position().y())
            shape = Qt.CursorShape.SizeHorCursor if hit and hit[1] != "move" else Qt.CursorShape.ArrowCursor
            self.setCursor(shape)
            return
        bar, part, press_x, start, end = self._m_drag
        step = self._ms(x) - self._ms(press_x)
        if part == "move":
            step = max(step, -start)
            self._m_preview = (start + step, end + step)
        elif part == "start":
            self._m_preview = (min(max(0, start + step), end - SNAP_MS), end)
        else:
            self._m_preview = (start, max(end + step, start + SNAP_MS))
        self.update()

    def mouseReleaseEvent(self, _event) -> None:
        if self._m_drag is not None:
            bar = self._m_drag[0]
            start, end = self._m_preview
            self._m_drag = None
            if (start, end) != (bar.start, bar.end):
                self.moved.emit(bar, start, end)
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        hit = self._hit(event.position().x(), event.position().y())
        if hit is not None:
            self.chosen.emit(hit[0].path)


class CSequenceEditor(QWidget):
    """The config's sequences: ``edits`` (a list of ``SEdit``), ``changed(seconds, animations)``, ``playing``,
    ``select(path)`` (an element to show in the properties)."""

    edits = Signal(object)
    changed = Signal(float, object)
    playing = Signal(bool)
    select = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_document: SDoc | None = None
        self.m_scene = None
        self.m_sequences: list[SPart] = []
        self.m_choice = QComboBox(minimumWidth=260)
        self.m_play = QPushButton(tr("common.play"), checkable=True)
        self.m_speed = QDoubleSpinBox(minimum=0.1, maximum=4.0, singleStep=0.1, value=1.0, prefix=tr("common.speed"))
        self.m_clock = QLabel(tr("common.seconds_s", seconds=shown_number(0, 2)))
        self.m_add_track = QPushButton(tr("objects.add_track"))
        self.m_add_element = QPushButton(tr("objects.add_element"))
        self.m_view = CTrackView()
        bar = QHBoxLayout()
        for widget in (
            QLabel(tr("common.sequence")),
            self.m_choice,
            self.m_play,
            self.m_speed,
            self.m_clock,
            self.m_add_track,
            self.m_add_element,
        ):
            bar.addWidget(widget)
        bar.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.m_view)
        self.m_choice.currentIndexChanged.connect(lambda _i: self._chosen())
        self.m_play.toggled.connect(self.playing.emit)
        self.m_view.seek.connect(lambda time_ms: self.changed.emit(time_ms, self.animations(time_ms)))
        self.m_view.moved.connect(self._moved)
        self.m_view.chosen.connect(self.select.emit)
        self.m_add_track.clicked.connect(self._add_track)
        self.m_add_element.clicked.connect(self._add_element)

    def show_doc(self, document: SDoc | None, scene) -> None:
        """The sequences of a config document (None: none), drawn with ``scene``'s parts (for the animations)."""
        keep = self.m_choice.currentIndex()
        self.m_document, self.m_scene = document, scene
        root = document.parts() if document is not None else None
        folder = (
            next((child for child in root.children if child.is_list and child.section == "Sequences"), None)
            if root
            else None
        )
        self.m_sequences = folder.children if folder is not None else []
        self.m_choice.blockSignals(True)
        self.m_choice.clear()
        for part in self.m_sequences:
            sid = _int(part.node, "SequenceID")
            text = f"{sid} ({sid >> 24} / {(sid >> 8) & 0xFFFF} / {sid & 0xFF})" if sid >= 1 << 24 else str(sid)
            self.m_choice.addItem(text)
        self.m_choice.setCurrentIndex(min(max(keep, 0), len(self.m_sequences) - 1) if self.m_sequences else -1)
        self.m_choice.blockSignals(False)
        for widget in (self.m_add_track, self.m_add_element):
            widget.setEnabled(bool(self.m_sequences))
        self._refresh()

    def _current(self) -> SPart | None:
        k = self.m_choice.currentIndex()
        return self.m_sequences[k] if 0 <= k < len(self.m_sequences) else None

    def _refresh(self) -> None:
        seq = self._current()
        found = bars(seq) if seq is not None else []
        rows = len([child for child in seq.children if child.node.name == "Track"]) if seq is not None else 1
        speed = _float(seq.node, "Speed") if seq is not None else 1.0
        self.m_view.set_bars(found, rows, speed)

    def _chosen(self) -> None:
        self._refresh()
        self.changed.emit(0.0, self.animations(0.0))

    def animations(self, seconds: float) -> dict:
        """What the view plays at ``seconds`` (``viewer.panels.sequence_animations`` over the scene's sequences)."""
        k = self.m_choice.currentIndex()
        if self.m_scene is None or not 0 <= k < len(self.m_scene.m_sequences):
            return {}
        return sequence_animations(self.m_scene, self.m_scene.m_sequences[k], seconds)

    def tick(self, seconds: float) -> None:
        self.m_view.set_time(seconds)
        seq = self._current()
        speed = _float(seq.node, "Speed") if seq is not None else 1.0
        length = max([1, *(bar.end for bar in self.m_view.m_bars)]) / 1000
        self.m_clock.setText(tr("common.seconds_s", seconds=shown_number((seconds * (speed or 1.0)) % length, 2)))

    def _moved(self, bar: SBar, start: int, end: int) -> None:
        changes = []
        for name, value, old in (("StartTime", start, bar.start), ("EndTime", end, bar.end)):
            leaf = _leaf(bar.node, name)
            if leaf is not None and value != old:
                changes.append(cfgdoc.set_value(leaf, str(value), f"{name} = {value}"))
        self.edits.emit(changes)

    def _add_track(self) -> None:
        seq = self._current()
        track = cfgparts.new_part(self.m_document, "Track") if seq is not None else None
        if track is None:
            return
        one_element(track)
        tracks = [child.node for child in seq.children if child.node.name == "Track"]
        ids = [_int(track_node, "TrackID") for track_node in tracks]
        trackid = _leaf(track, "TrackID")
        if trackid is not None:
            trackid.text = str(max(ids, default=0) + 1)
        index = _after(seq.node, tracks[-1] if tracks else None)
        self.edits.emit([cfgdoc.insert(seq.node, index, [track], tr("objects.add_track_lower"))])

    def _add_element(self) -> None:
        seq = self._current()
        tracks = [child.node for child in seq.children if child.node.name == "Track"] if seq is not None else []
        element = cfgparts.new_part(self.m_document, "TrackElement") if tracks else None
        if element is None:
            return
        elements = [child for child in tracks[-1].children if child.name == "TrackElement"]
        index = _after(tracks[-1], elements[-1] if elements else None)
        self.edits.emit([cfgdoc.insert(tracks[-1], index, [element], tr("objects.add_track_element"))])


def one_element(track: SNode) -> None:
    """A track copied from the file keeps its first element only: the new track must not play every animation of
    the one it was copied from."""
    elements = [child for child in track.children if cfgdoc.strip(child.name) == "TrackElement"]
    for extra in elements[1:]:
        track.children.remove(extra)


def _float(node: SNode, name: str) -> float:
    leaf = _leaf(node, name)
    return decimal(leaf.text if leaf is not None else None, 1.0)


def _after(parent: SNode, last: SNode | None) -> int:
    """Where a new child goes: after ``last``, else before 2070's closing ``m_ConfigType``, else at the end."""
    if last is not None:
        return next(i for i, child in enumerate(parent.children) if child is last) + 1
    closing = [i for i, child in enumerate(parent.children) if child.name == "m_ConfigType"]
    return closing[-1] if closing else len(parent.children)
