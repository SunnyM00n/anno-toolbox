"""The preview of the Flash UI editor: a movie's first frame drawn with Qt's painter (the ``desktop`` extra).

``render`` draws the main timeline or one sprite into a ``QImage``: the placements of the first frame by depth,
each with its matrix and alpha; shapes as filled outlines (solid, gradient, or the game's picture through
``images``), text fields with the font family their font reference names. What the game adds at run time (the
code builds most lists and values) and what the preview leaves out (filters, blend modes, masks, the fonts' own
glyphs) are listed in docs/formats/anno2205-ui-format.md, "The preview".
"""

from __future__ import annotations

import re
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QTransform,
)

from ..formats import feuread, feushape
from ..formats.feuread import Matrix, Movie, Tag

TWIPS = 20
MAX_DEPTH = 24  # nested sprites drawn at most this deep (a movie can nest itself through its code)
ALIGN = {0: Qt.AlignLeft, 1: Qt.AlignRight, 2: Qt.AlignHCenter, 3: Qt.AlignJustify}

ImageLoader = Callable[[feuread.ExternalImage], QImage | None]


def _qt(m: Matrix) -> QTransform:
    return QTransform(m.a, m.b, m.c, m.d, m.tx, m.ty)


def _color(rgba) -> QColor:
    r, g, b, a = rgba
    return QColor(r, g, b, a)


class Renderer:
    """Draws characters of one movie; ``images`` gives the picture of an image tag (None: a grey stand-in)."""

    def __init__(self, movie: Movie, images: ImageLoader | None = None) -> None:
        self.movie, self.images = movie, images or (lambda _img: None)
        self.defs = feuread.definitions(movie)
        self.fonts = {f.font_id: f for t in movie.tags if t.code == feuread.FONT for f in [feuread.read_font(t)]}
        self._shapes: dict[int, feushape.Shape] = {}
        self._pictures: dict[int, QImage | None] = {}
        self.missing: set[str] = set()  # image paths that had no picture

    def frame_tags(self, owner: int) -> list[Tag]:
        tags = self.movie.tags if owner == 0 else (self.defs[owner].children or [])
        return tags

    def display_list(self, tags: list[Tag]) -> list[tuple[int, feuread.Placement]]:
        """The first frame's placements by depth (later changes at a depth folded into the first)."""
        depths: dict[int, feuread.Placement] = {}
        for tag in tags:
            if tag.code == feuread.SHOW_FRAME:
                break
            if tag.code == feuread.REMOVE_OBJECT2:
                depths.pop(int.from_bytes(tag.body[:2], "little"), None)
            elif tag.code in (feuread.PLACE_OBJECT2, feuread.PLACE_OBJECT3):
                p = feuread.read_placement(tag)
                old = depths.get(p.depth)
                if p.char_id is None and old is not None:  # a move: the character stays, the matrix changes
                    p = feuread.Placement(p.code, p.flags, p.depth, old.char_id, old.class_name,
                                          p.matrix or old.matrix, old.name, p.head, p.matrix_raw, p.tail)  # fmt: skip
                depths[p.depth] = p
        return sorted(depths.items())

    def draw_timeline(self, painter: QPainter, owner: int, level: int = 0) -> None:
        for _, p in self.display_list(self.frame_tags(owner)):
            if p.char_id is None or p.flags & feuread.PLACE_HAS_CLIP_DEPTH:
                continue  # a mask: not drawn itself
            painter.save()
            if p.matrix is not None:
                painter.setTransform(_qt(p.matrix) * painter.transform())
            cx = _cxform(p)
            if cx is not None:
                painter.setOpacity(painter.opacity() * max(0.0, min(1.0, cx.mult[3])))
            self.draw_character(painter, p.char_id, level)
            painter.restore()

    def draw_character(self, painter: QPainter, cid: int, level: int = 0) -> None:
        tag = self.defs.get(cid)
        if tag is None:
            return
        if tag.code in feuread.SHAPES:
            self.draw_shape(painter, tag)
        elif tag.code == feuread.DEFINE_EDIT_TEXT:
            self.draw_text(painter, feuread.read_edit_text(tag))
        elif tag.code == feuread.DEFINE_SPRITE and level < MAX_DEPTH:
            self.draw_timeline(painter, cid, level + 1)
        elif tag.code in (feuread.IMAGE, feuread.ATLAS_IMAGE):  # placed as a bitmap (PlaceObject3)
            img = feuread.read_image(tag)
            target = QRectF(0, 0, img.width * TWIPS, img.height * TWIPS)
            picture = self._picture(cid)
            if picture is None:
                painter.fillRect(target, QBrush(QColor(128, 128, 128, 160), Qt.Dense4Pattern))
            else:
                painter.drawImage(target, picture)

    def draw_shape(self, painter: QPainter, tag: Tag) -> None:
        cid = feuread.char_id(tag)
        if cid not in self._shapes:
            self._shapes[cid] = feushape.read_shape(tag)
        shape = self._shapes[cid]
        painter.setPen(Qt.NoPen)
        for fill, loops in shape.fills:
            path = _path(loops)
            path.setFillRule(Qt.OddEvenFill)
            painter.setBrush(self._brush(fill))
            painter.drawPath(path)
        painter.setBrush(Qt.NoBrush)
        for line, segments in shape.lines:
            pen = QPen(_color(line.color), max(line.width, 1))
            pen.setCosmetic(line.width <= TWIPS)
            painter.setPen(pen)
            painter.drawPath(_path([segments], close=False))

    def _brush(self, fill: feushape.Fill) -> QBrush:
        if fill.kind in (feushape.LINEAR, feushape.RADIAL, feushape.FOCAL):
            if fill.kind == feushape.LINEAR:
                grad = QLinearGradient(-16384, 0, 16384, 0)
            else:
                grad = QRadialGradient(QPointF(0, 0), 16384, QPointF(fill.focal * 16384, 0))
            grad.setSpread((QLinearGradient.PadSpread, QLinearGradient.ReflectSpread, QLinearGradient.RepeatSpread)[min(fill.spread, 2)])  # fmt: skip
            for ratio, rgba in fill.stops:
                grad.setColorAt(ratio / 255, _color(rgba))
            brush = QBrush(grad)
            brush.setTransform(_qt(fill.matrix))
            return brush
        if fill.bitmap_id is not None:
            picture = self._picture(fill.bitmap_id)
            if picture is None:
                return QBrush(QColor(128, 128, 128, 160), Qt.Dense4Pattern)
            brush = QBrush(picture)
            brush.setTransform(_qt(fill.matrix))
            return brush
        return QBrush(_color(fill.color))

    def _picture(self, cid: int) -> QImage | None:
        if cid not in self._pictures:
            tag = self.defs.get(cid)
            img = (
                feuread.read_image(tag)
                if tag is not None and tag.code in (feuread.IMAGE, feuread.ATLAS_IMAGE)
                else None
            )
            picture = self.images(img) if img is not None else None
            if img is not None and picture is None:
                self.missing.add(img.path)
            self._pictures[cid] = picture
        return self._pictures[cid]

    def draw_text(self, painter: QPainter, t: feuread.EditText) -> None:
        if not t.text:
            return
        font_ref = self.fonts.get(t.font_id) if t.font_id is not None else None
        x0, x1, y0, y1 = t.bounds
        box = painter.transform().mapRect(QRectF(x0, y0, x1 - x0, y1 - y0))
        scale = box.height() / max(y1 - y0, 1)
        # drawn unscaled: Qt draws no text through a scaling transform on some platforms (the offscreen one)
        font = QFont(font_ref.name if font_ref else "Arial")
        font.setPixelSize(max(round((t.height or 240) * scale), 1))
        align = ALIGN.get(t.layout[0] if t.layout else 0, Qt.AlignLeft) | Qt.AlignTop
        if t.flags & feuread.EDIT_WORD_WRAP:
            align |= Qt.TextWordWrap
        text = _plain(t.text) if t.flags & feuread.EDIT_HTML else t.text
        painter.save()
        painter.resetTransform()
        painter.setFont(font)
        painter.setPen(_color(t.color or (0, 0, 0, 255)))
        painter.drawText(box, int(align), text)
        painter.restore()


def _cxform(p: feuread.Placement) -> feuread.ColorTransform | None:
    if not p.flags & feuread.PLACE_HAS_CXFORM:
        return None
    return feuread.read_cxform(feuread.Bits(p.tail, 0), alpha=True)


def _plain(html: str) -> str:
    """The text of an HTML text field without its tags (the preview draws one style per field)."""
    return re.sub(r"<[^>]+>", "", html.replace("<br>", "\n").replace("</p>", "\n")).replace("&amp;", "&").strip()


def _path(loops, close: bool = True) -> QPainterPath:
    path = QPainterPath()
    for loop in loops:
        if not loop:
            continue
        path.moveTo(*loop[0][1])
        last = loop[0][1]
        for seg in loop:
            if seg[1] != last:
                path.moveTo(*seg[1])
            if seg[0] == "L":
                path.lineTo(*seg[2])
            else:
                path.quadTo(QPointF(*seg[2]), QPointF(*seg[3]))
            last = seg[-1]
        if close:
            path.closeSubpath()
    return path


def render(movie: Movie, owner: int = 0, scale: float = 1.0, images: ImageLoader | None = None,
           background: QColor | None = None) -> tuple[QImage, Renderer]:  # fmt: skip
    """The first frame of the main timeline (``owner`` 0, the stage's size) or of a sprite (its own bounds)."""
    r = Renderer(movie, images)
    x0, x1, y0, y1 = movie.stage
    if owner:
        x0, x1, y0, y1 = _bounds(r, owner)
    width, height = max(int((x1 - x0) / TWIPS * scale), 1), max(int((y1 - y0) / TWIPS * scale), 1)
    image = QImage(min(width, 8192), min(height, 8192), QImage.Format_ARGB32_Premultiplied)
    image.fill(background or QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setTransform(QTransform(scale / TWIPS, 0, 0, scale / TWIPS, -x0 * scale / TWIPS, -y0 * scale / TWIPS))
    if owner and r.defs.get(owner) is not None and r.defs[owner].code != feuread.DEFINE_SPRITE:
        r.draw_character(painter, owner)
    else:
        r.draw_timeline(painter, owner)
    painter.end()
    return image, r


def _bounds(r: Renderer, cid: int) -> tuple[int, int, int, int]:
    """A character's box, from a painter that only measures (an empty sprite gets the stage)."""
    probe = QImage(1, 1, QImage.Format_ARGB32_Premultiplied)
    painter = _Measure(probe)
    r.draw_character(painter, cid)
    painter.end()
    box = painter.box
    if box is None or box.isEmpty():
        return r.movie.stage
    return int(box.left()), int(box.right()), int(box.top()), int(box.bottom())


class _Measure(QPainter):
    """A painter that records the box of what it draws, through its transform."""

    def __init__(self, device: QImage) -> None:
        super().__init__(device)
        self.box: QRectF | None = None

    def _add(self, rect: QRectF) -> None:
        mapped = self.transform().mapRect(rect)
        self.box = mapped if self.box is None else self.box.united(mapped)

    def drawPath(self, path: QPainterPath) -> None:  # Qt's name
        self._add(path.boundingRect())

    def drawText(self, rect, *args) -> None:  # Qt's name
        if isinstance(rect, QRectF):
            self._add(rect)

    def drawImage(self, rect, *args) -> None:  # Qt's name
        if isinstance(rect, QRectF):
            self._add(rect)

    def fillRect(self, rect, *args) -> None:  # Qt's name
        if isinstance(rect, QRectF):
            self._add(rect)
