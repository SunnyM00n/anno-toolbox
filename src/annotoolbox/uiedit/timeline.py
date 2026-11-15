"""The sequence editor (Qt): the keys of the open animation's tracks on a timeline - picked, dragged in time, added
by a double-click, removed with Del, their value and interpolator typed in - and the file's tweens with their start
and end values, duration, delay and ease.

``SequenceEditor`` is the widget; ``show(animation, describe, tweens, eases)`` fills it and every wish is a signal the
window turns into a ``uidoc`` edit. docs/ui-editor.md "The sequence editor".
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
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
from .panels import TYPE_CODES, parse, show
from .tweens import TARGETS

LABEL_W, ROW_H, KEY_R = 170.0, 20.0, 5.0
VALUE_CODES = {"e613cfd0": fields.FLOAT, "e86c4065": fields.VEC2, "7563a113": fields.VEC3, "10ae0bd3": fields.VEC4,
               "7ab8a12e": fields.BOOL, "21fb1be8": fields.I32, "fc284752": fields.U32, "dda23f23": fields.U32,
               "5da1d9a7": fields.STRING, "eabeeed5": fields.I64, "376db26f": fields.U64}  # fmt: skip
COLOR_TRACK = "ab390ed2"
TWEEN_COLUMNS = 8  # Tween, Start, End, Duration, Delay, Ease, Loops, Yoyo (named by ``_tween_headers``)


def _tween_headers() -> list[str]:
    return [tr("Tween"), tr("Start", context="tween"), tr("End"), tr("Duration"), tr("Delay"), tr("Ease"),
            tr("Loops"), tr("Yoyo")]  # fmt: skip


def parse_key_value(kind: str, text: str):
    """A key's value from text for a track of type ``kind`` (a colour track takes ``r, g, b``)."""
    if kind == COLOR_TRACK:
        parts = [int(float(p)) for p in text.replace(",", " ").split()]
        return tuple((parts + [255, 255, 255])[:3])
    return parse(text, VALUE_CODES.get(kind, fields.FLOAT))


class KeyCanvas(QWidget):
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
        self.tracks: list[animations.Track] = []
        self.labels: list[str] = []
        self.seconds, self.time = 1.0, 0.0
        self.current: tuple[int, int] = (-1, -1)
        self._drag: tuple[int, int] | None = None
        self._drag_t = 0.0

    def set_tracks(self, tracks, labels, seconds: float) -> None:
        self.tracks, self.labels, self.seconds = tracks, labels, max(seconds, 1e-3)
        self.setMinimumHeight(int(ROW_H * (len(tracks) + 1)) + 4)
        if self.current[0] >= len(tracks):
            self.current = (-1, -1)
        self.update()

    def x_of(self, t: float) -> float:
        return LABEL_W + (self.width() - LABEL_W - 10) * t / self.seconds

    def t_of(self, x: float) -> float:
        return max(0.0, min(self.seconds, (x - LABEL_W) * self.seconds / max(1.0, self.width() - LABEL_W - 10)))

    def hit(self, pos: QPointF) -> tuple[int, int]:
        row = int((pos.y() - ROW_H) // ROW_H)
        if not 0 <= row < len(self.tracks):
            return (-1, -1)
        for i, k in enumerate(self.tracks[row].keys):
            if abs(self.x_of(k.time) - pos.x()) <= KEY_R + 2:
                return (row, i)
        return (row, -1)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        p.fillRect(self.rect(), pal.base())
        p.setPen(pal.text().color())
        for s in range(int(self.seconds * 10) + 1):  # a tick every 0.1 s, a label every second
            x = self.x_of(s / 10)
            p.drawLine(QPointF(x, ROW_H - (6 if s % 10 == 0 else 3)), QPointF(x, ROW_H))
            if s % 10 == 0:
                p.drawText(QPointF(x + 2, ROW_H - 7), f"{s // 10} s")
        for row, (track, label) in enumerate(zip(self.tracks, self.labels, strict=False)):
            y = ROW_H * (row + 1)
            if row % 2:
                p.fillRect(QRectF(0, y, self.width(), ROW_H), pal.alternateBase())
            p.setPen(pal.text().color())
            p.drawText(QRectF(4, y, LABEL_W - 8, ROW_H), int(Qt.AlignmentFlag.AlignVCenter), label)
            for i, k in enumerate(track.keys):
                x = self.x_of(self._drag_t if self._drag == (row, i) else k.time)
                cy = y + ROW_H / 2
                diamond = QPolygonF([QPointF(x, cy - KEY_R), QPointF(x + KEY_R, cy), QPointF(x, cy + KEY_R),
                                     QPointF(x - KEY_R, cy)])  # fmt: skip
                on = (row, i) == self.current
                p.setBrush(QBrush(QColor(255, 150, 40) if on else QColor(120, 170, 220)))
                p.setPen(QPen(QColor(30, 30, 30), 1))
                p.drawPolygon(diamond)
        x = self.x_of(self.time)
        p.setPen(QPen(QColor(220, 60, 60), 1.5))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.end()

    def mousePressEvent(self, event) -> None:
        row, i = self.hit(event.position())
        self.current = (row, i)
        if i >= 0:
            self._drag, self._drag_t = (row, i), self.tracks[row].keys[i].time
        self.picked.emit(row, i)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self._drag_t = round(self.t_of(event.position().x()), 3)
            self.update()

    def mouseReleaseEvent(self, _event) -> None:
        if self._drag is not None:
            row, i = self._drag
            self._drag = None
            if abs(self._drag_t - self.tracks[row].keys[i].time) > 1e-4:
                self.moved.emit(row, i, self._drag_t)
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        row, i = self.hit(event.position())
        if row >= 0 and i < 0:
            self.added.emit(row, round(self.t_of(event.position().x()), 3))

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.current[1] >= 0:
            self.removed.emit(*self.current)
        else:
            super().keyPressEvent(event)


class SequenceEditor(QWidget):
    """The keys of the open animation and the file's tweens; each wish a signal with the objects it concerns."""

    add_key = Signal(object, float, object)  # track node, time, value
    set_key = Signal(object, object, object, object, object)  # track node, key node, time, value, interpolator
    remove_key = Signal(object, object)  # track node, key node
    edit_tween = Signal(object, object, int, object)  # instance node, property path, type code, value

    def __init__(self) -> None:
        super().__init__()
        self.canvas = KeyCanvas()
        self.hint = QLabel(tr("Pick an animation in States and events; double-click a track to add a key."))
        self.time_edit = QLineEdit(placeholderText=tr("time (s)"))
        self.value_edit = QLineEdit(placeholderText=tr("value"))
        self.interp = QComboBox()
        self.interp.addItems(list(animations.INTERPOLATORS))
        self.apply = QPushButton(tr("Apply to the key"))
        form = QHBoxLayout()
        for w in (QLabel(tr("Key:")), self.time_edit, self.value_edit, self.interp, self.apply):
            form.addWidget(w)
        self.tweens = QTableWidget(0, TWEEN_COLUMNS)
        self.tweens.setHorizontalHeaderLabels(_tween_headers())
        self.tweens.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        top = QWidget()
        lay = QVBoxLayout(top)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.hint)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(form)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self.tweens)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)
        self.animation: animations.Animation | None = None
        self.tween_list: list = []
        self._filling = False
        self.canvas.picked.connect(self._picked)
        self.canvas.moved.connect(self._moved)
        self.canvas.added.connect(self._added)
        self.canvas.removed.connect(self._removed)
        self.apply.clicked.connect(self._apply)
        self.tweens.itemChanged.connect(self._tween_changed)

    def show_sequence(self, animation, describe: Callable[[animations.Track], str], tweens=(), eases=None) -> None:
        self.animation = animation
        tracks = animation.tracks if animation is not None else []
        self.canvas.set_tracks(tracks, [describe(t) for t in tracks], animation.seconds if animation else 1.0)
        self.hint.setText(tr("{name}: {n} tracks, {seconds:.2f} s; double-click a track to add a key, drag a key to "
                             "move it, Del removes it.", name=animation.name, n=len(tracks), seconds=animation.seconds)
                          if animation else
                          tr("Pick an animation in States and events; double-click a track to add a key."))  # fmt: skip
        self._show_key()
        self._fill_tweens(list(tweens), eases or {})

    def set_time(self, t: float) -> None:
        self.canvas.time = t
        self.canvas.update()

    def _key(self):
        row, i = self.canvas.current
        if row < 0 or i < 0 or row >= len(self.canvas.tracks) or i >= len(self.canvas.tracks[row].keys):
            return None, None
        track = self.canvas.tracks[row]
        return track, track.keys[i]

    def _show_key(self) -> None:
        track, key = self._key()
        for w in (self.time_edit, self.value_edit, self.interp, self.apply):
            w.setEnabled(key is not None)
        if key is not None:
            self.time_edit.setText(f"{key.time:g}")
            self.value_edit.setText(show(key.value))
            self.interp.setCurrentIndex(key.interpolator if 0 <= key.interpolator < self.interp.count() else 1)

    def _picked(self, _row: int, _i: int) -> None:
        self._show_key()

    def _moved(self, row: int, i: int, t: float) -> None:
        track = self.canvas.tracks[row]
        self.set_key.emit(track.node, track.keys[i].node, t, None, None)

    def _added(self, row: int, t: float) -> None:
        track = self.canvas.tracks[row]
        if track.is_event:
            return
        value = track.value_at(t)
        if value is None:
            value = parse_key_value(track.kind, "")
        self.add_key.emit(track.node, t, value)

    def _removed(self, row: int, i: int) -> None:
        track = self.canvas.tracks[row]
        self.canvas.current = (row, -1)
        self.remove_key.emit(track.node, track.keys[i].node)

    def _apply(self) -> None:
        track, key = self._key()
        if key is None:
            return
        try:
            t = float(self.time_edit.text())
            value = None if track.is_event else parse_key_value(track.kind, self.value_edit.text())
        except ValueError:
            self.hint.setText(tr("That value does not read: numbers, or x, y, z."))
            return
        self.set_key.emit(track.node, key.node, t, value, self.interp.currentIndex())

    def _fill_tweens(self, tweens, eases: dict[int, str]) -> None:
        self._filling = True
        self.tween_list = tweens
        self.tweens.setRowCount(len(tweens))
        for row, tw in enumerate(tweens):
            start, end, _ = TARGETS[tw.kind]
            ease = tw.get("EaseType", "Linear")
            cells = [(tw.name, None), (show(tw.get(start)), start), (show(tw.get(end)), end),
                     (show(tw.get("Duration")), "Duration"), (show(tw.get("Delay")), "Delay"),
                     (ease if isinstance(ease, str) else eases.get(ease, str(ease)), None),
                     (show(tw.get("LoopCount")), "LoopCount"), (show(tw.get("Yoyo")), "Yoyo")]  # fmt: skip
            for col, (text, name) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, name)
                if name is None:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.tweens.setItem(row, col, item)
        self._filling = False

    def _tween_changed(self, item: QTableWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if self._filling or not name or item.row() >= len(self.tween_list):
            return
        tw = self.tween_list[item.row()]
        code = TYPE_CODES.get(tw.types.get(name, 3), fields.FLOAT)
        try:
            value = parse(item.text(), code)
        except ValueError:
            item.setForeground(QBrush(QColor(220, 60, 60)))
            return
        self.edit_tween.emit(tw.node, [tw.component, tw.uids[name]], code, value)
