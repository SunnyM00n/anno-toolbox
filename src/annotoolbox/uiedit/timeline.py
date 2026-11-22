"""The sequence editor (Qt): the keys of the open animation's tracks on a timeline - picked, dragged in time, added
by a double-click, removed with Del, their value and interpolator typed in - and the file's tweens with their start
and end values, duration, delay and ease.

``CSequenceEditor`` is the widget; ``show(animation, describe, tweens, eases)`` fills it and every wish is a signal the
window turns into a ``uidoc`` edit. docs/ui-editor.md "The sequence editor".
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import tr
from . import animations, fields
from .panels import TYPE_CODES, parse, show, whole
from .tweens import TARGETS

LABEL_W, ROW_H, KEY_R = 170.0, 20.0, 5.0
VALUE_CODES = {"e613cfd0": fields.FLOAT, "e86c4065": fields.VEC2, "7563a113": fields.VEC3, "10ae0bd3": fields.VEC4,
               "7ab8a12e": fields.BOOL, "21fb1be8": fields.I32, "fc284752": fields.U32, "dda23f23": fields.U32,
               "5da1d9a7": fields.STRING, "eabeeed5": fields.I64, "376db26f": fields.U64}  # fmt: skip
COLOR_TRACK = "ab390ed2"
DELETE_KEYS = (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
TWEEN_COLUMNS = 8  # Tween, Start, End, Duration, Delay, Ease, Loops, Yoyo (named by ``_tween_headers``)


def _tween_headers() -> list[str]:
    return [tr("ui.tween"), tr("ui.tween.start"), tr("ui.end"), tr("ui.duration"), tr("ui.delay"), tr("ui.ease"),
            tr("ui.loops"), tr("ui.yoyo")]  # fmt: skip


def parse_key_value(kind: str, text: str):
    """A key's value from text for a track of type ``kind`` (a colour track takes ``r, g, b``)."""
    if kind == COLOR_TRACK:
        parts = [whole(piece) for piece in text.replace(",", " ").split()]
        return tuple((parts + [255, 255, 255])[:3])
    return parse(text, VALUE_CODES.get(kind, fields.FLOAT))


class CKeyCanvas(QWidget):
    """The tracks as rows with their keys as diamonds and the play head; the editing gestures."""

    picked = Signal(int, int)  # track row, key index (-1: none)
    moved = Signal(int, int, float)  # track row, key index, new time
    added = Signal(int, float)  # track row, time
    removed = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(80)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.m_tracks: list[animations.STrack] = []
        self.m_labels: list[str] = []
        self.m_seconds, self.m_time = 1.0, 0.0
        self.m_current: tuple[int, int] = (-1, -1)
        self._m_drag: tuple[int, int] | None = None
        self._m_drag_t = 0.0

    def set_tracks(self, tracks, labels, seconds: float) -> None:
        self.m_tracks, self.m_labels, self.m_seconds = tracks, labels, max(seconds, 1e-3)
        self.setMinimumHeight(int(ROW_H * (len(tracks) + 1)) + 4)
        if self.m_current[0] >= len(tracks):
            self.m_current = (-1, -1)
        self.update()

    def x_of(self, time_s: float) -> float:
        return LABEL_W + (self.width() - LABEL_W - 10) * time_s / self.m_seconds

    def t_of(self, x: float) -> float:
        return max(0.0, min(self.m_seconds, (x - LABEL_W) * self.m_seconds / max(1.0, self.width() - LABEL_W - 10)))

    def hit(self, position: QPointF) -> tuple[int, int]:
        row = int((position.y() - ROW_H) // ROW_H)
        if not 0 <= row < len(self.m_tracks):
            return (-1, -1)
        for i, key in enumerate(self.m_tracks[row].keys):
            if abs(self.x_of(key.time) - position.x()) <= KEY_R + 2:
                return (row, i)
        return (row, -1)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        painter.setPen(palette.text().color())
        for tick in range(int(self.m_seconds * 10) + 1):  # a tick every 0.1 s, a label every second
            x = self.x_of(tick / 10)
            painter.drawLine(QPointF(x, ROW_H - (6 if tick % 10 == 0 else 3)), QPointF(x, ROW_H))
            if tick % 10 == 0:
                painter.drawText(QPointF(x + 2, ROW_H - 7), f"{tick // 10} s")
        for row, (track, label) in enumerate(zip(self.m_tracks, self.m_labels, strict=False)):
            y = ROW_H * (row + 1)
            if row % 2:
                painter.fillRect(QRectF(0, y, self.width(), ROW_H), palette.alternateBase())
            painter.setPen(palette.text().color())
            painter.drawText(QRectF(4, y, LABEL_W - 8, ROW_H), int(Qt.AlignmentFlag.AlignVCenter), label)
            for i, key in enumerate(track.keys):
                x = self.x_of(self._m_drag_t if self._m_drag == (row, i) else key.time)
                centre_y = y + ROW_H / 2
                diamond = QPolygonF([QPointF(x, centre_y - KEY_R), QPointF(x + KEY_R, centre_y), QPointF(x, centre_y + KEY_R),
                                     QPointF(x - KEY_R, centre_y)])  # fmt: skip
                is_current = (row, i) == self.m_current
                painter.setBrush(QBrush(QColor(255, 150, 40) if is_current else QColor(120, 170, 220)))
                painter.setPen(QPen(QColor(30, 30, 30), 1))
                painter.drawPolygon(diamond)
        x = self.x_of(self.m_time)
        painter.setPen(QPen(QColor(220, 60, 60), 1.5))
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        painter.end()

    def mousePressEvent(self, event) -> None:
        row, i = self.hit(event.position())
        self.m_current = (row, i)
        if i >= 0:
            self._m_drag, self._m_drag_t = (row, i), self.m_tracks[row].keys[i].time
        self.picked.emit(row, i)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._m_drag is not None:
            self._m_drag_t = round(self.t_of(event.position().x()), 3)
            self.update()

    def mouseReleaseEvent(self, _event) -> None:
        if self._m_drag is not None:
            row, i = self._m_drag
            self._m_drag = None
            if abs(self._m_drag_t - self.m_tracks[row].keys[i].time) > 1e-4:
                self.moved.emit(row, i, self._m_drag_t)
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        row, i = self.hit(event.position())
        if row >= 0 and i < 0:
            self.added.emit(row, round(self.t_of(event.position().x()), 3))

    def event(self, event) -> bool:
        """Del with a key picked is this widget's: the window's shortcut of the same key removes the selected element."""
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in DELETE_KEYS and self.m_current[1] >= 0:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in DELETE_KEYS and self.m_current[1] >= 0:
            self.removed.emit(*self.m_current)
        else:
            super().keyPressEvent(event)


class CSequenceEditor(QWidget):
    """The keys of the open animation and the file's tweens; each wish a signal with the objects it concerns."""

    add_key = Signal(object, float, object)  # track node, time, value
    set_key = Signal(object, object, object, object, object)  # track node, key node, time, value, interpolator
    remove_key = Signal(object, object)  # track node, key node
    edit_tween = Signal(object, object, int, object)  # instance node, property path, type code, value

    def __init__(self) -> None:
        super().__init__()
        self.m_canvas = CKeyCanvas()
        self.m_hint = QLabel(tr("ui.pick_animation_states_events_double"))
        self.m_time_edit = QLineEdit(placeholderText=tr("ui.time_s"))
        self.m_value_edit = QLineEdit(placeholderText=tr("common.value_lower"))
        self.m_interp = QComboBox()
        self.m_interp.addItems(list(animations.INTERPOLATORS))
        self.m_apply = QPushButton(tr("ui.apply_to_key"))
        form = QHBoxLayout()
        for widget in (QLabel(tr("ui.key")), self.m_time_edit, self.m_value_edit, self.m_interp, self.m_apply):
            form.addWidget(widget)
        self.m_tweens = QTableWidget(0, TWEEN_COLUMNS)
        self.m_tweens.setHorizontalHeaderLabels(_tween_headers())
        self.m_tweens.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        top = QWidget()
        lay = QVBoxLayout(top)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.m_hint)
        lay.addWidget(self.m_canvas, 1)
        lay.addLayout(form)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self.m_tweens)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)
        self.m_animation: animations.SAnimation | None = None
        self.m_tween_list: list = []
        self._m_filling = False
        self.m_canvas.picked.connect(self._picked)
        self.m_canvas.moved.connect(self._moved)
        self.m_canvas.added.connect(self._added)
        self.m_canvas.removed.connect(self._removed)
        self.m_apply.clicked.connect(self._apply)
        self.m_tweens.itemChanged.connect(self._tween_changed)

    def show_sequence(self, animation, describe: Callable[[animations.STrack], str], tweens=(), eases=None) -> None:
        self.m_animation = animation
        tracks = animation.tracks if animation is not None else []
        self.m_canvas.set_tracks(tracks, [describe(track) for track in tracks], animation.seconds if animation else 1.0)
        self.m_hint.setText(tr("ui.tracks_s_double_click_track", name=animation.name, n=len(tracks), seconds=animation.seconds)
                          if animation else
                          tr("ui.pick_animation_states_events_double"))  # fmt: skip
        self._show_key()
        self._fill_tweens(list(tweens), eases or {})

    def set_time(self, time_s: float) -> None:
        self.m_canvas.m_time = time_s
        self.m_canvas.update()

    def _key(self):
        row, i = self.m_canvas.m_current
        if row < 0 or i < 0 or row >= len(self.m_canvas.m_tracks) or i >= len(self.m_canvas.m_tracks[row].keys):
            return None, None
        track = self.m_canvas.m_tracks[row]
        return track, track.keys[i]

    def _show_key(self) -> None:
        track, key = self._key()
        for widget in (self.m_time_edit, self.m_value_edit, self.m_interp, self.m_apply):
            widget.setEnabled(key is not None)
        if key is not None:
            self.m_time_edit.setText(f"{key.time:g}")
            self.m_value_edit.setText(show(key.value))
            self.m_interp.setCurrentIndex(key.interpolator if 0 <= key.interpolator < self.m_interp.count() else 1)

    def _picked(self, _row: int, _i: int) -> None:
        self._show_key()

    def _moved(self, row: int, i: int, time_s: float) -> None:
        track = self.m_canvas.m_tracks[row]
        self.set_key.emit(track.node, track.keys[i].node, time_s, None, None)

    def _added(self, row: int, time_s: float) -> None:
        track = self.m_canvas.m_tracks[row]
        if track.is_event:
            return
        value = track.value_at(time_s)
        if value is None:
            value = parse_key_value(track.kind, "")
        self.add_key.emit(track.node, time_s, value)

    def _removed(self, row: int, i: int) -> None:
        track = self.m_canvas.m_tracks[row]
        self.m_canvas.m_current = (row, -1)
        self.remove_key.emit(track.node, track.keys[i].node)

    def _apply(self) -> None:
        track, key = self._key()
        if key is None:
            return
        try:
            time_s = float(self.m_time_edit.text())
            value = None if track.is_event else parse_key_value(track.kind, self.m_value_edit.text())
        except ValueError:
            self.m_hint.setText(tr("ui.value_not_read_numbers_x"))
            return
        self.set_key.emit(track.node, key.node, time_s, value, self.m_interp.currentIndex())

    def _fill_tweens(self, tweens, eases: dict[int, str]) -> None:
        self._m_filling = True
        self.m_tween_list = tweens
        self.m_tweens.setRowCount(len(tweens))
        for row, tween in enumerate(tweens):
            start, end, _ = TARGETS[tween.kind]
            ease = tween.get("EaseType", "Linear")
            cells = [(tween.name, None), (show(tween.get(start)), start), (show(tween.get(end)), end),
                     (show(tween.get("Duration")), "Duration"), (show(tween.get("Delay")), "Delay"),
                     (ease if isinstance(ease, str) else eases.get(ease, str(ease)), None),
                     (show(tween.get("LoopCount")), "LoopCount"), (show(tween.get("Yoyo")), "Yoyo")]  # fmt: skip
            for column, (text, name) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, name)
                if name is None:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.m_tweens.setItem(row, column, item)
        self._m_filling = False

    def _tween_changed(self, item: QTableWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if self._m_filling or not name or item.row() >= len(self.m_tween_list):
            return
        tween = self.m_tween_list[item.row()]
        code = TYPE_CODES.get(tween.types.get(name, 3), fields.FLOAT)
        try:
            value = parse(item.text(), code)
        except ValueError:
            item.setForeground(QBrush(QColor(220, 60, 60)))
            return
        self.edit_tween.emit(tween.node, [tween.component, tween.uids[name]], code, value)
