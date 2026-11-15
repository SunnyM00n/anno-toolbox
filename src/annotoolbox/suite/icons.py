"""The ribbon's icons, drawn as vectors on a 64 x 64 grid (no image files): a light stroke with orange and
red accents on a transparent tile, so they sit on either theme (``theme``).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

FG, ACCENT, DANGER, WATER, GREEN = "#d6d6d6", "#f28c28", "#e0523c", "#5aa9e6", "#6abf5a"
SIZE = 64


def set_foreground(color: str) -> None:
    """The stroke colour of the icons drawn from now on (the theme's: light on dark, dark on light)."""
    global FG
    FG = color


def _stroke(p: QPainter, color: str | None = None, width: float = 4.0) -> None:
    pen = QPen(QColor(color or FG), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _fill(p: QPainter, color: str | None = None) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(color or FG)))


def _line(p: QPainter, *pts: tuple[float, float]) -> None:
    p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))


def _poly(p: QPainter, *pts: tuple[float, float]) -> None:
    p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))


def _path(p: QPainter, start: tuple[float, float], *steps: tuple) -> None:
    path = QPainterPath(QPointF(*start))
    for step in steps:
        {2: path.lineTo, 4: path.quadTo, 6: path.cubicTo}[len(step)](*step)
    p.drawPath(path)


def _undo(p: QPainter) -> None:
    _stroke(p, ACCENT)
    _path(p, (24, 26), (38, 26), (54, 26, 54, 48, 38, 48), (22, 48))
    _fill(p, ACCENT)
    _poly(p, (10, 26), (26, 14), (26, 38))


def _redo(p: QPainter) -> None:
    p.translate(SIZE, 0)
    p.scale(-1, 1)
    _undo(p)


def _save(p: QPainter) -> None:
    _stroke(p)
    p.drawRoundedRect(QRectF(12, 10, 40, 44), 3, 3)
    p.drawRect(QRectF(22, 10, 20, 14))
    _stroke(p, ACCENT)
    p.drawRect(QRectF(21, 34, 22, 20))


def _build(p: QPainter) -> None:
    _stroke(p, ACCENT)
    _line(p, (32, 8), (32, 36))
    _line(p, (20, 24), (32, 36), (44, 24))
    _stroke(p)
    _line(p, (12, 42), (12, 54), (52, 54), (52, 42))


def _select(p: QPainter) -> None:
    _fill(p)
    _poly(p, (18, 8), (18, 48), (29, 38), (37, 56), (44, 53), (36, 35), (50, 35))


def _deselect(p: QPainter) -> None:
    pen = QPen(QColor(FG), 3)
    pen.setStyle(Qt.PenStyle.DashLine)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(10, 10, 38, 38))
    _stroke(p, DANGER)
    _line(p, (34, 34), (58, 58))
    _line(p, (58, 34), (34, 58))


def _focus(p: QPainter) -> None:
    _stroke(p)
    p.drawEllipse(QPointF(32, 32), 16, 16)
    for a, b in (((32, 6), (32, 18)), ((32, 46), (32, 58)), ((6, 32), (18, 32)), ((46, 32), (58, 32))):
        _line(p, a, b)
    _fill(p, ACCENT)
    p.drawEllipse(QPointF(32, 32), 4, 4)


def _move(p: QPainter) -> None:
    _stroke(p)
    _line(p, (32, 12), (32, 52))
    _line(p, (12, 32), (52, 32))
    _fill(p, ACCENT)
    for pts in (
        ((32, 4), (25, 14), (39, 14)),
        ((32, 60), (25, 50), (39, 50)),
        ((4, 32), (14, 25), (14, 39)),
        ((60, 32), (50, 25), (50, 39)),
    ):
        _poly(p, *pts)


def _turn(p: QPainter) -> None:
    _stroke(p)
    p.drawArc(QRectF(12, 12, 40, 40), 60 * 16, 270 * 16)
    _fill(p, ACCENT)
    _poly(p, (40, 38), (58, 38), (50, 52))


def _tilt(p: QPainter) -> None:
    _stroke(p, FG, 2.5)
    _line(p, (6, 54), (58, 54))
    p.save()
    p.translate(32, 34)
    p.rotate(-24)
    _stroke(p, ACCENT)
    p.drawRect(QRectF(-14, -10, 28, 20))
    p.restore()


def _scale(p: QPainter) -> None:
    _stroke(p)
    p.drawRect(QRectF(8, 30, 24, 24))
    _stroke(p, ACCENT)
    _line(p, (30, 34), (50, 14))
    _fill(p, ACCENT)
    _poly(p, (56, 8), (42, 10), (54, 22))


def _delete(p: QPainter) -> None:
    _stroke(p, DANGER, 6)
    _line(p, (14, 14), (50, 50))
    _line(p, (50, 14), (14, 50))


def _duplicate(p: QPainter) -> None:
    _stroke(p)
    p.drawRect(QRectF(8, 8, 30, 30))
    _stroke(p, ACCENT)
    p.setBrush(QColor("#303030" if QColor(FG).lightness() > 128 else "#efefef"))  # the panel behind the front sheet
    p.drawRect(QRectF(26, 26, 30, 30))


def _place(p: QPainter) -> None:
    _stroke(p)
    p.drawRect(QRectF(8, 20, 34, 34))
    _stroke(p, ACCENT, 5)
    _line(p, (48, 8), (48, 28))
    _line(p, (38, 18), (58, 18))


def _models(p: QPainter) -> None:
    _stroke(p)
    p.drawRect(QRectF(10, 14, 12, 38))
    p.drawRect(QRectF(26, 8, 12, 44))
    _stroke(p, ACCENT)
    _line(p, (44, 52), (54, 14))


def _eye(p: QPainter) -> None:
    _stroke(p)
    _path(p, (6, 32), (32, 6, 58, 32))
    _path(p, (6, 32), (32, 58, 58, 32))
    _fill(p, ACCENT)
    p.drawEllipse(QPointF(32, 32), 7, 7)


def _hide(p: QPainter) -> None:
    _eye(p)
    _stroke(p, DANGER)
    _line(p, (12, 54), (52, 10))


def _tree(p: QPainter) -> None:
    _fill(p, "#7a5a36")
    p.drawRect(QRectF(29, 44, 6, 14))
    _fill(p, GREEN)
    _poly(p, (32, 6), (14, 30), (50, 30))
    _poly(p, (32, 18), (10, 46), (54, 46))


def _plant(p: QPainter) -> None:
    _tree(p)
    _stroke(p, ACCENT, 5)
    _line(p, (52, 4), (52, 22))
    _line(p, (43, 13), (61, 13))


def _clear(p: QPainter) -> None:
    _tree(p)
    _stroke(p, DANGER, 5)
    _line(p, (44, 6), (60, 22))
    _line(p, (60, 6), (44, 22))


def _hill(p: QPainter) -> None:
    _stroke(p)
    _line(p, (6, 54), (22, 30), (32, 42), (44, 22), (58, 54))


def _raise(p: QPainter) -> None:
    _hill(p)
    _stroke(p, ACCENT, 4)
    _line(p, (14, 26), (14, 6))
    _fill(p, ACCENT)
    _poly(p, (14, 2), (7, 12), (21, 12))


def _lower(p: QPainter) -> None:
    _hill(p)
    _stroke(p, ACCENT, 4)
    _line(p, (14, 6), (14, 26))
    _fill(p, ACCENT)
    _poly(p, (14, 30), (7, 20), (21, 20))


def _flatten(p: QPainter) -> None:
    _stroke(p)
    _line(p, (6, 56), (20, 38), (44, 38), (58, 56))
    _stroke(p, ACCENT, 3)
    _line(p, (4, 24), (60, 24))


def _smooth(p: QPainter) -> None:
    _stroke(p, ACCENT)
    _path(p, (6, 40), (18, 10, 26, 10, 32, 34), (38, 58, 48, 58, 58, 28))


def _cells(p: QPainter) -> None:
    _stroke(p, FG, 3)
    for k in (8, 24, 40, 56):
        _line(p, (8, k), (56, k))
        _line(p, (k, 8), (k, 56))


def _cell_on(p: QPainter) -> None:
    _cells(p)
    _fill(p, ACCENT)
    _poly(p, (24, 24), (40, 24), (40, 40), (24, 40))


def _ford(p: QPainter) -> None:
    _stroke(p, WATER, 4)
    for y in (18, 46):
        _path(p, (6, y), (18, y - 6, 30, y + 6, 42, y), (50, y - 5, 58, y))
    _stroke(p, FG, 3)
    for x in (14, 30, 46):
        _fill(p, FG)
        _poly(p, (x - 4, 28), (x + 4, 28), (x + 4, 36), (x - 4, 36))


def _ford_on(p: QPainter) -> None:
    _ford(p)
    _stroke(p, ACCENT, 4)
    _line(p, (10, 58), (54, 58))


def _ford_off(p: QPainter) -> None:
    _ford(p)
    _stroke(p, DANGER, 4)
    _line(p, (12, 24), (52, 40))
    _line(p, (52, 24), (12, 40))


def _zone(p: QPainter) -> None:
    _cells(p)
    for (x, y), colour in zip(((12, 12), (36, 12), (12, 36), (36, 36)), (WATER, ACCENT, FG, DANGER), strict=True):
        _fill(p, colour)
        _poly(p, (x, y), (x + 16, y), (x + 16, y + 16), (x, y + 16))


def _surf_on(p: QPainter) -> None:
    _stroke(p, WATER, 4)
    for y in (22, 36, 50):
        _path(p, (6, y), (18, y - 8, 30, y + 8, 42, y), (50, y - 6, 56, y - 4))
    _stroke(p, FG, 3)
    _line(p, (6, 58), (58, 58))


def _surf_off(p: QPainter) -> None:
    _stroke(p, WATER, 4)
    for y in (24, 40):
        _line(p, (6, y), (58, y))
    _stroke(p, FG, 3)
    _line(p, (6, 58), (58, 58))


def _cell_off(p: QPainter) -> None:
    _cells(p)
    _stroke(p, DANGER, 4)
    _line(p, (24, 24), (40, 40))
    _line(p, (40, 24), (24, 40))


def _spray(p: QPainter) -> None:
    _stroke(p, FG, 3)
    _line(p, (10, 54), (10, 40), (22, 40), (22, 54))
    _fill(p, GREEN)
    for x, y in ((30, 14), (42, 10), (50, 22), (38, 28), (52, 38), (28, 40), (44, 48)):
        p.drawEllipse(QPointF(x, y), 4.5, 4.5)
    _stroke(p, ACCENT, 3)
    _line(p, (14, 34), (24, 22))


def _noise(p: QPainter) -> None:
    _stroke(p)
    _line(p, (6, 40), (14, 28), (20, 44), (28, 24), (36, 46), (44, 30), (50, 42), (58, 34))
    _stroke(p, ACCENT, 3)
    _line(p, (6, 52), (58, 52))


def _terrace(p: QPainter) -> None:
    _stroke(p)
    _line(p, (6, 54), (6, 44), (20, 44), (20, 32), (34, 32), (34, 20), (48, 20), (48, 10), (58, 10))
    _stroke(p, ACCENT, 3)
    _line(p, (6, 56), (58, 56))


def _ramp(p: QPainter) -> None:
    _stroke(p)
    _line(p, (6, 54), (58, 54), (58, 12), (6, 54))
    _stroke(p, ACCENT, 4)
    _line(p, (10, 48), (52, 18))


def _despike(p: QPainter) -> None:
    _stroke(p)
    _line(p, (6, 46), (24, 46), (28, 8), (32, 46), (58, 46))
    _stroke(p, ACCENT, 3)
    _line(p, (10, 30), (50, 30))
    _line(p, (24, 18), (34, 18))


def _paint(p: QPainter) -> None:
    _stroke(p, FG, 6)
    _line(p, (50, 8), (32, 30))
    _fill(p, ACCENT)
    p.drawEllipse(QPointF(24, 42), 13, 13)


def _materials(p: QPainter) -> None:
    for (x, y), c in zip(((8, 8), (34, 8), (8, 34), (34, 34)), ("#8a8a80", ACCENT, "#6a8a4a", "#5a6a7a"), strict=True):
        _fill(p, c)
        p.drawRoundedRect(QRectF(x, y, 22, 22), 3, 3)


def _water(p: QPainter) -> None:
    _stroke(p, WATER)
    for y in (22, 40):
        _path(p, (6, y), (16, y - 12, 24, y - 12, 32, y), (40, y + 12, 48, y + 12, 58, y))


def _point(p: QPainter) -> None:
    _stroke(p, WATER)
    _line(p, (6, 52), (22, 38), (46, 28), (58, 10))
    _fill(p, "#ffffff")
    p.drawEllipse(QPointF(22, 38), 7, 7)


def _point_delete(p: QPainter) -> None:
    _point(p)
    _stroke(p, DANGER, 5)
    _line(p, (40, 46), (58, 62))
    _line(p, (58, 46), (40, 62))


def _point_insert(p: QPainter) -> None:
    _point(p)
    _stroke(p, ACCENT, 5)
    _line(p, (50, 40), (50, 60))
    _line(p, (40, 50), (60, 50))


def _road(p: QPainter) -> None:
    _stroke(p)
    _poly(p, (22, 6), (42, 6), (58, 58), (6, 58))
    pen = QPen(QColor(ACCENT), 3)
    pen.setStyle(Qt.PenStyle.DashLine)
    p.setPen(pen)
    _line(p, (32, 8), (32, 56))


def _slots(p: QPainter) -> None:
    _stroke(p, FG, 2.5)
    for k in (8, 28, 48):
        _line(p, (k, 8), (k, 48))
        _line(p, (8, k), (48, k))
    _stroke(p, ACCENT, 6)
    _line(p, (36, 46), (46, 58), (62, 36))


def _falls(p: QPainter) -> None:
    _fill(p, "#7a7a72")
    _poly(p, (4, 6), (30, 6), (30, 58), (4, 58))
    _stroke(p, WATER, 4)
    for x, y0, y1 in ((38, 8, 50), (48, 14, 54), (58, 6, 46)):
        _line(p, (x, y0), (x, y1))
    _stroke(p, ACCENT, 3)
    _line(p, (34, 56), (62, 56))


def _rules(p: QPainter) -> None:
    _stroke(p, FG, 3)
    for y in (14, 32, 50):
        _line(p, (28, y), (58, y))
    _stroke(p, GREEN, 4)
    _line(p, (6, 14), (12, 20), (20, 8))
    _line(p, (6, 32), (12, 38), (20, 26))
    _stroke(p, DANGER, 4)
    _line(p, (7, 45), (19, 57))
    _line(p, (19, 45), (7, 57))


def _block(p: QPainter) -> None:
    _stroke(p, FG, 2.5)
    for k in (8, 28, 48):
        _line(p, (k, 8), (k, 48))
        _line(p, (8, k), (48, k))
    _stroke(p, DANGER, 6)
    _line(p, (34, 34), (58, 58))
    _line(p, (58, 34), (34, 58))


def _free(p: QPainter) -> None:
    _stroke(p, FG, 2.5)
    for k in (8, 28, 48):
        _line(p, (k, 8), (k, 48))
        _line(p, (8, k), (48, k))
    _stroke(p, GREEN, 6)
    _line(p, (34, 48), (44, 58), (60, 36))


def _app_islands(p: QPainter) -> None:
    _fill(p, GREEN)
    _poly(p, (8, 42), (20, 22), (30, 30), (42, 14), (56, 42))
    _stroke(p, WATER)
    _path(p, (4, 50), (14, 44, 22, 44, 32, 50), (42, 56, 50, 56, 60, 50))


def _app_assets(p: QPainter) -> None:
    _stroke(p)
    _line(p, (12, 10), (12, 54))
    for y in (18, 34, 50):
        _line(p, (12, y), (24, y))
    _stroke(p, ACCENT)
    for y in (18, 34, 50):
        p.drawRoundedRect(QRectF(28, y - 6, 26, 12), 2, 2)


def _app_ui(p: QPainter) -> None:
    _stroke(p)
    p.drawRoundedRect(QRectF(6, 10, 52, 44), 4, 4)
    _line(p, (6, 20), (58, 20))
    _stroke(p, ACCENT)
    p.drawRoundedRect(QRectF(14, 28, 18, 18), 2, 2)
    _line(p, (38, 30), (50, 30))
    _line(p, (38, 40), (50, 40))


def _app_phoenix(p: QPainter) -> None:
    _stroke(p)
    p.drawRoundedRect(QRectF(6, 10, 52, 44), 4, 4)
    _stroke(p, ACCENT)
    p.drawEllipse(QRectF(12, 18, 10, 10))
    p.drawEllipse(QRectF(42, 36, 10, 10))
    _path(p, (22, 23), (32, 23, 32, 41, 42, 41))


def _app_viewer(p: QPainter) -> None:
    _stroke(p)
    _poly(p, (32, 8), (54, 20), (54, 44), (32, 56), (10, 44), (10, 20))
    _line(p, (10, 20), (32, 32), (54, 20))
    _line(p, (32, 32), (32, 56))
    _stroke(p, ACCENT)
    p.drawEllipse(QRectF(40, 40, 16, 16))


def _app_converter(p: QPainter) -> None:
    _stroke(p)
    _path(p, (10, 26), (18, 12, 40, 10, 50, 22))
    _poly(p, (50, 12), (52, 24), (40, 24))
    _stroke(p, ACCENT)
    _path(p, (54, 38), (46, 52, 24, 54, 14, 42))
    _poly(p, (14, 52), (12, 40), (24, 40))


def _app_objects(p: QPainter) -> None:
    _stroke(p)
    _poly(p, (24, 14), (40, 22), (40, 40), (24, 48), (8, 40), (8, 22))
    _line(p, (8, 22), (24, 30), (40, 22))
    _line(p, (24, 30), (24, 48))
    _stroke(p, ACCENT)
    _line(p, (46, 12), (58, 12))
    _line(p, (46, 28), (58, 28))
    _line(p, (46, 44), (58, 44))


def _app_mods(p: QPainter) -> None:
    _stroke(p)
    p.drawRoundedRect(QRectF(8, 10, 48, 12), 3, 3)
    p.drawRoundedRect(QRectF(8, 26, 48, 12), 3, 3)
    p.drawRoundedRect(QRectF(8, 42, 48, 12), 3, 3)
    _stroke(p, ACCENT)
    _line(p, (14, 16), (20, 16))
    _line(p, (14, 32), (20, 32))
    _line(p, (14, 48), (20, 48))


def _app_sound(p: QPainter) -> None:
    _stroke(p)
    _poly(p, (8, 24), (20, 24), (34, 12), (34, 52), (20, 40), (8, 40))
    _stroke(p, ACCENT)
    _path(p, (42, 24), (48, 32), (42, 40))
    _path(p, (48, 16), (58, 32), (48, 48))


def _app_moddiff(p: QPainter) -> None:
    _stroke(p)
    p.drawRoundedRect(QRectF(6, 10, 22, 44), 3, 3)
    p.drawRoundedRect(QRectF(36, 10, 22, 44), 3, 3)
    _stroke(p, ACCENT)
    _line(p, (12, 22), (22, 22))
    _line(p, (42, 22), (52, 22))
    _line(p, (12, 34), (22, 34))
    _line(p, (42, 34), (52, 34))
    _line(p, (42, 44), (52, 44))


def _app_texts(p: QPainter) -> None:
    _stroke(p)
    p.drawRoundedRect(QRectF(6, 10, 22, 44), 3, 3)
    p.drawRoundedRect(QRectF(36, 10, 22, 44), 3, 3)
    _stroke(p, ACCENT)
    _line(p, (11, 20), (23, 20))
    _line(p, (11, 30), (19, 30))
    _line(p, (41, 20), (53, 20))
    _line(p, (41, 30), (49, 30))
    _path(p, (28, 40), (32, 44), (36, 40))


def _app_maps(p: QPainter) -> None:
    _stroke(p)
    p.drawRect(QRectF(8, 8, 48, 48))
    p.drawRect(QRectF(14, 14, 36, 36))
    _stroke(p, ACCENT)
    p.drawRect(QRectF(18, 20, 10, 10))
    p.drawRect(QRectF(34, 32, 12, 12))
    p.drawEllipse(QRectF(34, 18, 6, 6))


def _app_saves(p: QPainter) -> None:
    _stroke(p)
    p.drawRoundedRect(QRectF(10, 8, 36, 46), 3, 3)
    _line(p, (16, 18), (40, 18))
    _line(p, (16, 26), (34, 26))
    _stroke(p, ACCENT)
    p.drawEllipse(QRectF(32, 32, 22, 22))
    _path(p, (43, 37), (43, 43), (48, 46))


def _app_particles(p: QPainter) -> None:
    _stroke(p)
    _path(p, (14, 54), (14, 40, 26, 34), (24, 22, 34, 14))
    _fill(p, ACCENT)
    for x, y, r in ((34, 14, 5), (46, 22, 4), (40, 34, 3.5), (52, 38, 3), (28, 42, 2.5), (48, 50, 2.5)):
        p.drawEllipse(QRectF(x - r, y - r, 2 * r, 2 * r))


def _project(p: QPainter) -> None:
    _stroke(p)
    _poly(p, (8, 18), (24, 18), (30, 24), (56, 24), (56, 52), (8, 52))
    _stroke(p, ACCENT)
    _line(p, (20, 38), (44, 38))
    _line(p, (32, 30), (32, 46))


ICONS = {
    "app_islands": _app_islands,
    "app_assets": _app_assets,
    "app_ui": _app_ui,
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
    """The icon of that name (an empty one for an unknown name); needs a ``QGuiApplication``."""
    draw = ICONS.get(key)
    if draw is None:
        return QIcon()
    pix = QPixmap(SIZE, SIZE)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw(p)
    p.end()
    return QIcon(pix)
