"""The ribbon's icons, drawn as vectors on a 64 x 64 grid (no image files): a light stroke with orange and
red accents on a transparent tile, so they sit on either theme (``theme``).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

g_foreground, ACCENT, DANGER, WATER, GREEN = "#d6d6d6", "#f28c28", "#e0523c", "#5aa9e6", "#6abf5a"
SIZE_PX = 64


def set_foreground(color: str) -> None:
    """The stroke colour of the icons drawn from now on (the theme's: light on dark, dark on light)."""
    global g_foreground
    g_foreground = color


def _stroke(painter: QPainter, color: str | None = None, width_px: float = 4.0) -> None:
    pen = QPen(QColor(color or g_foreground), width_px)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _fill(painter: QPainter, color: str | None = None) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(color or g_foreground)))


def _line(painter: QPainter, *points: tuple[float, float]) -> None:
    painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))


def _poly(painter: QPainter, *points: tuple[float, float]) -> None:
    painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))


def _path(painter: QPainter, start: tuple[float, float], *steps: tuple) -> None:
    path = QPainterPath(QPointF(*start))
    for step in steps:
        {2: path.lineTo, 4: path.quadTo, 6: path.cubicTo}[len(step)](*step)
    painter.drawPath(path)


def _undo(painter: QPainter) -> None:
    _stroke(painter, ACCENT)
    _path(painter, (24, 26), (38, 26), (54, 26, 54, 48, 38, 48), (22, 48))
    _fill(painter, ACCENT)
    _poly(painter, (10, 26), (26, 14), (26, 38))


def _redo(painter: QPainter) -> None:
    painter.translate(SIZE_PX, 0)
    painter.scale(-1, 1)
    _undo(painter)


def _save(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRoundedRect(QRectF(12, 10, 40, 44), 3, 3)
    painter.drawRect(QRectF(22, 10, 20, 14))
    _stroke(painter, ACCENT)
    painter.drawRect(QRectF(21, 34, 22, 20))


def _build(painter: QPainter) -> None:
    _stroke(painter, ACCENT)
    _line(painter, (32, 8), (32, 36))
    _line(painter, (20, 24), (32, 36), (44, 24))
    _stroke(painter)
    _line(painter, (12, 42), (12, 54), (52, 54), (52, 42))


def _select(painter: QPainter) -> None:
    _fill(painter)
    _poly(painter, (18, 8), (18, 48), (29, 38), (37, 56), (44, 53), (36, 35), (50, 35))


def _deselect(painter: QPainter) -> None:
    pen = QPen(QColor(g_foreground), 3)
    pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(QRectF(10, 10, 38, 38))
    _stroke(painter, DANGER)
    _line(painter, (34, 34), (58, 58))
    _line(painter, (58, 34), (34, 58))


def _focus(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawEllipse(QPointF(32, 32), 16, 16)
    for start, end in (((32, 6), (32, 18)), ((32, 46), (32, 58)), ((6, 32), (18, 32)), ((46, 32), (58, 32))):
        _line(painter, start, end)
    _fill(painter, ACCENT)
    painter.drawEllipse(QPointF(32, 32), 4, 4)


def _move(painter: QPainter) -> None:
    _stroke(painter)
    _line(painter, (32, 12), (32, 52))
    _line(painter, (12, 32), (52, 32))
    _fill(painter, ACCENT)
    for points in (
        ((32, 4), (25, 14), (39, 14)),
        ((32, 60), (25, 50), (39, 50)),
        ((4, 32), (14, 25), (14, 39)),
        ((60, 32), (50, 25), (50, 39)),
    ):
        _poly(painter, *points)


def _turn(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawArc(QRectF(12, 12, 40, 40), 60 * 16, 270 * 16)
    _fill(painter, ACCENT)
    _poly(painter, (40, 38), (58, 38), (50, 52))


def _tilt(painter: QPainter) -> None:
    _stroke(painter, g_foreground, 2.5)
    _line(painter, (6, 54), (58, 54))
    painter.save()
    painter.translate(32, 34)
    painter.rotate(-24)
    _stroke(painter, ACCENT)
    painter.drawRect(QRectF(-14, -10, 28, 20))
    painter.restore()


def _scale(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRect(QRectF(8, 30, 24, 24))
    _stroke(painter, ACCENT)
    _line(painter, (30, 34), (50, 14))
    _fill(painter, ACCENT)
    _poly(painter, (56, 8), (42, 10), (54, 22))


def _delete(painter: QPainter) -> None:
    _stroke(painter, DANGER, 6)
    _line(painter, (14, 14), (50, 50))
    _line(painter, (50, 14), (14, 50))


def _duplicate(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRect(QRectF(8, 8, 30, 30))
    _stroke(painter, ACCENT)
    painter.setBrush(
        QColor("#303030" if QColor(g_foreground).lightness() > 128 else "#efefef")
    )  # the panel behind the front sheet
    painter.drawRect(QRectF(26, 26, 30, 30))


def _place(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRect(QRectF(8, 20, 34, 34))
    _stroke(painter, ACCENT, 5)
    _line(painter, (48, 8), (48, 28))
    _line(painter, (38, 18), (58, 18))


def _models(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRect(QRectF(10, 14, 12, 38))
    painter.drawRect(QRectF(26, 8, 12, 44))
    _stroke(painter, ACCENT)
    _line(painter, (44, 52), (54, 14))


def _eye(painter: QPainter) -> None:
    _stroke(painter)
    _path(painter, (6, 32), (32, 6, 58, 32))
    _path(painter, (6, 32), (32, 58, 58, 32))
    _fill(painter, ACCENT)
    painter.drawEllipse(QPointF(32, 32), 7, 7)


def _hide(painter: QPainter) -> None:
    _eye(painter)
    _stroke(painter, DANGER)
    _line(painter, (12, 54), (52, 10))


def _tree(painter: QPainter) -> None:
    _fill(painter, "#7a5a36")
    painter.drawRect(QRectF(29, 44, 6, 14))
    _fill(painter, GREEN)
    _poly(painter, (32, 6), (14, 30), (50, 30))
    _poly(painter, (32, 18), (10, 46), (54, 46))


def _plant(painter: QPainter) -> None:
    _tree(painter)
    _stroke(painter, ACCENT, 5)
    _line(painter, (52, 4), (52, 22))
    _line(painter, (43, 13), (61, 13))


def _clear(painter: QPainter) -> None:
    _tree(painter)
    _stroke(painter, DANGER, 5)
    _line(painter, (44, 6), (60, 22))
    _line(painter, (60, 6), (44, 22))


def _hill(painter: QPainter) -> None:
    _stroke(painter)
    _line(painter, (6, 54), (22, 30), (32, 42), (44, 22), (58, 54))


def _raise(painter: QPainter) -> None:
    _hill(painter)
    _stroke(painter, ACCENT, 4)
    _line(painter, (14, 26), (14, 6))
    _fill(painter, ACCENT)
    _poly(painter, (14, 2), (7, 12), (21, 12))


def _lower(painter: QPainter) -> None:
    _hill(painter)
    _stroke(painter, ACCENT, 4)
    _line(painter, (14, 6), (14, 26))
    _fill(painter, ACCENT)
    _poly(painter, (14, 30), (7, 20), (21, 20))


def _flatten(painter: QPainter) -> None:
    _stroke(painter)
    _line(painter, (6, 56), (20, 38), (44, 38), (58, 56))
    _stroke(painter, ACCENT, 3)
    _line(painter, (4, 24), (60, 24))


def _smooth(painter: QPainter) -> None:
    _stroke(painter, ACCENT)
    _path(painter, (6, 40), (18, 10, 26, 10, 32, 34), (38, 58, 48, 58, 58, 28))


def _cells(painter: QPainter) -> None:
    _stroke(painter, g_foreground, 3)
    for k in (8, 24, 40, 56):
        _line(painter, (8, k), (56, k))
        _line(painter, (k, 8), (k, 56))


def _cell_on(painter: QPainter) -> None:
    _cells(painter)
    _fill(painter, ACCENT)
    _poly(painter, (24, 24), (40, 24), (40, 40), (24, 40))


def _ford(painter: QPainter) -> None:
    _stroke(painter, WATER, 4)
    for y in (18, 46):
        _path(painter, (6, y), (18, y - 6, 30, y + 6, 42, y), (50, y - 5, 58, y))
    _stroke(painter, g_foreground, 3)
    for x in (14, 30, 46):
        _fill(painter, g_foreground)
        _poly(painter, (x - 4, 28), (x + 4, 28), (x + 4, 36), (x - 4, 36))


def _ford_on(painter: QPainter) -> None:
    _ford(painter)
    _stroke(painter, ACCENT, 4)
    _line(painter, (10, 58), (54, 58))


def _ford_off(painter: QPainter) -> None:
    _ford(painter)
    _stroke(painter, DANGER, 4)
    _line(painter, (12, 24), (52, 40))
    _line(painter, (52, 24), (12, 40))


def _zone(painter: QPainter) -> None:
    _cells(painter)
    for (x, y), colour in zip(
        ((12, 12), (36, 12), (12, 36), (36, 36)), (WATER, ACCENT, g_foreground, DANGER), strict=True
    ):
        _fill(painter, colour)
        _poly(painter, (x, y), (x + 16, y), (x + 16, y + 16), (x, y + 16))


def _surf_on(painter: QPainter) -> None:
    _stroke(painter, WATER, 4)
    for y in (22, 36, 50):
        _path(painter, (6, y), (18, y - 8, 30, y + 8, 42, y), (50, y - 6, 56, y - 4))
    _stroke(painter, g_foreground, 3)
    _line(painter, (6, 58), (58, 58))


def _surf_off(painter: QPainter) -> None:
    _stroke(painter, WATER, 4)
    for y in (24, 40):
        _line(painter, (6, y), (58, y))
    _stroke(painter, g_foreground, 3)
    _line(painter, (6, 58), (58, 58))


def _cell_off(painter: QPainter) -> None:
    _cells(painter)
    _stroke(painter, DANGER, 4)
    _line(painter, (24, 24), (40, 40))
    _line(painter, (40, 24), (24, 40))


def _spray(painter: QPainter) -> None:
    _stroke(painter, g_foreground, 3)
    _line(painter, (10, 54), (10, 40), (22, 40), (22, 54))
    _fill(painter, GREEN)
    for x, y in ((30, 14), (42, 10), (50, 22), (38, 28), (52, 38), (28, 40), (44, 48)):
        painter.drawEllipse(QPointF(x, y), 4.5, 4.5)
    _stroke(painter, ACCENT, 3)
    _line(painter, (14, 34), (24, 22))


def _noise(painter: QPainter) -> None:
    _stroke(painter)
    _line(painter, (6, 40), (14, 28), (20, 44), (28, 24), (36, 46), (44, 30), (50, 42), (58, 34))
    _stroke(painter, ACCENT, 3)
    _line(painter, (6, 52), (58, 52))


def _terrace(painter: QPainter) -> None:
    _stroke(painter)
    _line(painter, (6, 54), (6, 44), (20, 44), (20, 32), (34, 32), (34, 20), (48, 20), (48, 10), (58, 10))
    _stroke(painter, ACCENT, 3)
    _line(painter, (6, 56), (58, 56))


def _ramp(painter: QPainter) -> None:
    _stroke(painter)
    _line(painter, (6, 54), (58, 54), (58, 12), (6, 54))
    _stroke(painter, ACCENT, 4)
    _line(painter, (10, 48), (52, 18))


def _despike(painter: QPainter) -> None:
    _stroke(painter)
    _line(painter, (6, 46), (24, 46), (28, 8), (32, 46), (58, 46))
    _stroke(painter, ACCENT, 3)
    _line(painter, (10, 30), (50, 30))
    _line(painter, (24, 18), (34, 18))


def _paint(painter: QPainter) -> None:
    _stroke(painter, g_foreground, 6)
    _line(painter, (50, 8), (32, 30))
    _fill(painter, ACCENT)
    painter.drawEllipse(QPointF(24, 42), 13, 13)


def _materials(painter: QPainter) -> None:
    for (x, y), colour in zip(
        ((8, 8), (34, 8), (8, 34), (34, 34)), ("#8a8a80", ACCENT, "#6a8a4a", "#5a6a7a"), strict=True
    ):
        _fill(painter, colour)
        painter.drawRoundedRect(QRectF(x, y, 22, 22), 3, 3)


def _water(painter: QPainter) -> None:
    _stroke(painter, WATER)
    for y in (22, 40):
        _path(painter, (6, y), (16, y - 12, 24, y - 12, 32, y), (40, y + 12, 48, y + 12, 58, y))


def _point(painter: QPainter) -> None:
    _stroke(painter, WATER)
    _line(painter, (6, 52), (22, 38), (46, 28), (58, 10))
    _fill(painter, "#ffffff")
    painter.drawEllipse(QPointF(22, 38), 7, 7)


def _point_delete(painter: QPainter) -> None:
    _point(painter)
    _stroke(painter, DANGER, 5)
    _line(painter, (40, 46), (58, 62))
    _line(painter, (58, 46), (40, 62))


def _point_insert(painter: QPainter) -> None:
    _point(painter)
    _stroke(painter, ACCENT, 5)
    _line(painter, (50, 40), (50, 60))
    _line(painter, (40, 50), (60, 50))


def _road(painter: QPainter) -> None:
    _stroke(painter)
    _poly(painter, (22, 6), (42, 6), (58, 58), (6, 58))
    pen = QPen(QColor(ACCENT), 3)
    pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    _line(painter, (32, 8), (32, 56))


def _slots(painter: QPainter) -> None:
    _stroke(painter, g_foreground, 2.5)
    for k in (8, 28, 48):
        _line(painter, (k, 8), (k, 48))
        _line(painter, (8, k), (48, k))
    _stroke(painter, ACCENT, 6)
    _line(painter, (36, 46), (46, 58), (62, 36))


def _falls(painter: QPainter) -> None:
    _fill(painter, "#7a7a72")
    _poly(painter, (4, 6), (30, 6), (30, 58), (4, 58))
    _stroke(painter, WATER, 4)
    for x, top_y, bottom_y in ((38, 8, 50), (48, 14, 54), (58, 6, 46)):
        _line(painter, (x, top_y), (x, bottom_y))
    _stroke(painter, ACCENT, 3)
    _line(painter, (34, 56), (62, 56))


def _rules(painter: QPainter) -> None:
    _stroke(painter, g_foreground, 3)
    for y in (14, 32, 50):
        _line(painter, (28, y), (58, y))
    _stroke(painter, GREEN, 4)
    _line(painter, (6, 14), (12, 20), (20, 8))
    _line(painter, (6, 32), (12, 38), (20, 26))
    _stroke(painter, DANGER, 4)
    _line(painter, (7, 45), (19, 57))
    _line(painter, (19, 45), (7, 57))


def _block(painter: QPainter) -> None:
    _stroke(painter, g_foreground, 2.5)
    for k in (8, 28, 48):
        _line(painter, (k, 8), (k, 48))
        _line(painter, (8, k), (48, k))
    _stroke(painter, DANGER, 6)
    _line(painter, (34, 34), (58, 58))
    _line(painter, (58, 34), (34, 58))


def _free(painter: QPainter) -> None:
    _stroke(painter, g_foreground, 2.5)
    for k in (8, 28, 48):
        _line(painter, (k, 8), (k, 48))
        _line(painter, (8, k), (48, k))
    _stroke(painter, GREEN, 6)
    _line(painter, (34, 48), (44, 58), (60, 36))


def _app_islands(painter: QPainter) -> None:
    _fill(painter, GREEN)
    _poly(painter, (8, 42), (20, 22), (30, 30), (42, 14), (56, 42))
    _stroke(painter, WATER)
    _path(painter, (4, 50), (14, 44, 22, 44, 32, 50), (42, 56, 50, 56, 60, 50))


def _app_assets(painter: QPainter) -> None:
    _stroke(painter)
    _line(painter, (12, 10), (12, 54))
    for y in (18, 34, 50):
        _line(painter, (12, y), (24, y))
    _stroke(painter, ACCENT)
    for y in (18, 34, 50):
        painter.drawRoundedRect(QRectF(28, y - 6, 26, 12), 2, 2)


def _app_phoenix(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRoundedRect(QRectF(6, 10, 52, 44), 4, 4)
    _stroke(painter, ACCENT)
    painter.drawEllipse(QRectF(12, 18, 10, 10))
    painter.drawEllipse(QRectF(42, 36, 10, 10))
    _path(painter, (22, 23), (32, 23, 32, 41, 42, 41))


def _app_viewer(painter: QPainter) -> None:
    _stroke(painter)
    _poly(painter, (32, 8), (54, 20), (54, 44), (32, 56), (10, 44), (10, 20))
    _line(painter, (10, 20), (32, 32), (54, 20))
    _line(painter, (32, 32), (32, 56))
    _stroke(painter, ACCENT)
    painter.drawEllipse(QRectF(40, 40, 16, 16))


def _app_converter(painter: QPainter) -> None:
    _stroke(painter)
    _path(painter, (10, 26), (18, 12, 40, 10, 50, 22))
    _poly(painter, (50, 12), (52, 24), (40, 24))
    _stroke(painter, ACCENT)
    _path(painter, (54, 38), (46, 52, 24, 54, 14, 42))
    _poly(painter, (14, 52), (12, 40), (24, 40))


def _app_objects(painter: QPainter) -> None:
    _stroke(painter)
    _poly(painter, (24, 14), (40, 22), (40, 40), (24, 48), (8, 40), (8, 22))
    _line(painter, (8, 22), (24, 30), (40, 22))
    _line(painter, (24, 30), (24, 48))
    _stroke(painter, ACCENT)
    _line(painter, (46, 12), (58, 12))
    _line(painter, (46, 28), (58, 28))
    _line(painter, (46, 44), (58, 44))


def _app_mods(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRoundedRect(QRectF(8, 10, 48, 12), 3, 3)
    painter.drawRoundedRect(QRectF(8, 26, 48, 12), 3, 3)
    painter.drawRoundedRect(QRectF(8, 42, 48, 12), 3, 3)
    _stroke(painter, ACCENT)
    _line(painter, (14, 16), (20, 16))
    _line(painter, (14, 32), (20, 32))
    _line(painter, (14, 48), (20, 48))


def _app_sound(painter: QPainter) -> None:
    _stroke(painter)
    _poly(painter, (8, 24), (20, 24), (34, 12), (34, 52), (20, 40), (8, 40))
    _stroke(painter, ACCENT)
    _path(painter, (42, 24), (48, 32), (42, 40))
    _path(painter, (48, 16), (58, 32), (48, 48))


def _app_moddiff(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRoundedRect(QRectF(6, 10, 22, 44), 3, 3)
    painter.drawRoundedRect(QRectF(36, 10, 22, 44), 3, 3)
    _stroke(painter, ACCENT)
    _line(painter, (12, 22), (22, 22))
    _line(painter, (42, 22), (52, 22))
    _line(painter, (12, 34), (22, 34))
    _line(painter, (42, 34), (52, 34))
    _line(painter, (42, 44), (52, 44))


def _app_texts(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRoundedRect(QRectF(6, 10, 22, 44), 3, 3)
    painter.drawRoundedRect(QRectF(36, 10, 22, 44), 3, 3)
    _stroke(painter, ACCENT)
    _line(painter, (11, 20), (23, 20))
    _line(painter, (11, 30), (19, 30))
    _line(painter, (41, 20), (53, 20))
    _line(painter, (41, 30), (49, 30))
    _path(painter, (28, 40), (32, 44), (36, 40))


def _app_maps(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRect(QRectF(8, 8, 48, 48))
    painter.drawRect(QRectF(14, 14, 36, 36))
    _stroke(painter, ACCENT)
    painter.drawRect(QRectF(18, 20, 10, 10))
    painter.drawRect(QRectF(34, 32, 12, 12))
    painter.drawEllipse(QRectF(34, 18, 6, 6))


def _app_saves(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRoundedRect(QRectF(10, 8, 36, 46), 3, 3)
    _line(painter, (16, 18), (40, 18))
    _line(painter, (16, 26), (34, 26))
    _stroke(painter, ACCENT)
    painter.drawEllipse(QRectF(32, 32, 22, 22))
    _path(painter, (43, 37), (43, 43), (48, 46))


def _app_particles(painter: QPainter) -> None:
    _stroke(painter)
    _path(painter, (14, 54), (14, 40, 26, 34), (24, 22, 34, 14))
    _fill(painter, ACCENT)
    for x, y, radius_px in ((34, 14, 5), (46, 22, 4), (40, 34, 3.5), (52, 38, 3), (28, 42, 2.5), (48, 50, 2.5)):
        painter.drawEllipse(QRectF(x - radius_px, y - radius_px, 2 * radius_px, 2 * radius_px))


def _project(painter: QPainter) -> None:
    _stroke(painter)
    _poly(painter, (8, 18), (24, 18), (30, 24), (56, 24), (56, 52), (8, 52))
    _stroke(painter, ACCENT)
    _line(painter, (20, 38), (44, 38))
    _line(painter, (32, 30), (32, 46))


def _app_external(painter: QPainter) -> None:
    _stroke(painter)
    painter.drawRoundedRect(QRectF(10, 20, 30, 34), 4, 4)
    _stroke(painter, ACCENT)
    _line(painter, (40, 32), (54, 32), (54, 12), (34, 12), (34, 20))


ICONS = {
    "app_islands": _app_islands,
    "app_assets": _app_assets,
    "app_phoenix": _app_phoenix,
    "app_viewer": _app_viewer,
    "app_converter": _app_converter,
    "app_mods": _app_mods,
    "app_objects": _app_objects,
    "app_sound": _app_sound,
    "app_moddiff": _app_moddiff,
    "app_texts": _app_texts,
    "app_maps": _app_maps,
    "app_saves": _app_saves,
    "app_particles": _app_particles,
    "app_external": _app_external,
    "project": _project,
    "undo": _undo,
    "redo": _redo,
    "save": _save,
    "build": _build,
    "select": _select,
    "deselect": _deselect,
    "focus": _focus,
    "move": _move,
    "turn": _turn,
    "tilt": _tilt,
    "scale": _scale,
    "delete": _delete,
    "duplicate": _duplicate,
    "place": _place,
    "models": _models,
    "eye": _eye,
    "hide": _hide,
    "plant": _plant,
    "clear": _clear,
    "raise": _raise,
    "lower": _lower,
    "flatten": _flatten,
    "smooth": _smooth,
    "spray": _spray,
    "cell_on": _cell_on,
    "cell_off": _cell_off,
    "zone": _zone,
    "ford_on": _ford_on,
    "ford_off": _ford_off,
    "surf_on": _surf_on,
    "surf_off": _surf_off,
    "noise": _noise,
    "terrace": _terrace,
    "ramp": _ramp,
    "despike": _despike,
    "paint": _paint,
    "materials": _materials,
    "water": _water,
    "point_delete": _point_delete,
    "point_insert": _point_insert,
    "road": _road,
    "slots": _slots,
    "falls": _falls,
    "rules": _rules,
    "block": _block,
    "free": _free,
}


def icon(key: str) -> QIcon:
    """The icon of that name, or the picture file an external app names by its path (an empty one for an unknown
    name); needs a ``QGuiApplication``."""
    draw = ICONS.get(key)
    if draw is None:
        return QIcon(key) if os.path.isabs(key) and os.path.isfile(key) else QIcon()
    pix = QPixmap(SIZE_PX, SIZE_PX)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw(painter)
    painter.end()
    return QIcon(pix)
