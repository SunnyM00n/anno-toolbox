"""What the preview draws through: a small interface (a quad with a picture, nine-slice, tint, blend and clips; a
text; an outline) and its backend on Qt's painter. A GPU backend (QRhi) can take the painter's place behind the
same calls.

``draw(frame, canvas, pictures, seconds)`` draws a placed frame (``layout.Frame``) at a time, which the moving effects
(a scroll, a blink) read and the 3D scene the world effects draw from; ``PainterCanvas`` is the backend.
docs/rendering/ui-117.md is how the game draws (blend modes, masks); docs/ui-editor.md "The preview" says what differs.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Protocol

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform

from . import effects, fonts, placeholders, worldfx, worldscene
from .labels import TextStyle
from .layout import Clip, DrawItem, Frame, Matrix, apply, bounds
from .pictures import Picture, Pictures
from .worldscene import Scene

BLENDS = {2: QPainter.CompositionMode.CompositionMode_Plus, 3: QPainter.CompositionMode.CompositionMode_Multiply}
MISSING = (90, 90, 110)  # a picture the game does not have: a flat stand-in
STAND_IN = (170, 160, 140)  # a placeholder picture: a light neutral tile


class Canvas(Protocol):
    """The calls a backend answers; matrices map a widget's own units into the reference space."""

    def quad(self, item: DrawItem, picture: Picture | None) -> None: ...

    def text(self, item: DrawItem) -> None: ...

    def outline(self, matrix: Matrix, size: tuple[float, float], color: tuple[int, int, int], width: float) -> None: ...

    def layered(
        self, item: DrawItem, masks: list[tuple[Clip, Picture | None]], draw: Callable[[Canvas], None]
    ) -> None: ...


def qtransform(m: Matrix) -> QTransform:
    a, b, c, d, tx, ty = m
    return QTransform(a, b, c, d, tx, ty)


def measure(text: str, style: TextStyle, width: float) -> tuple[float, float]:
    """The size a text takes in its font: one line, or wrapped at ``width`` (``fonts.lay_out``)."""
    lines = fonts.lay_out(text or " ", style, width)
    return (width if style.wrap and width > 0 else lines.width), lines.height


class PainterCanvas:
    """The painter backend: ``painter`` already carries the zoom and pan (reference space to pixels)."""

    def __init__(self, painter: QPainter) -> None:
        self.p = painter
        self.base = painter.transform()
        self._images: dict[str, QImage] = {}
        self._tinted: dict[tuple, QImage] = {}

    def _image(self, picture: Picture) -> QImage:
        img = self._images.get(picture.key)
        if img is None:
            h, w = picture.rgba.shape[:2]
            img = QImage(picture.rgba.tobytes(), w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
            self._images[picture.key] = img
        return img

    def _tint(self, picture: Picture, rgb: tuple[int, int, int]) -> QImage:
        img = self._image(picture)
        if tuple(rgb) == (255, 255, 255):
            return img
        key = (picture.key, tuple(rgb))
        if key not in self._tinted:
            out = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            p = QPainter(out)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            p.fillRect(out.rect(), QColor(*rgb))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            p.drawImage(0, 0, img)
            p.end()
            self._tinted[key] = out
        return self._tinted[key]

    def _begin(self, item: DrawItem) -> None:
        self.p.save()
        self.p.setTransform(self.base)
        for clip in item.clips:
            if clip.soft is not None:
                continue  # a soft mask multiplies the item's alpha (layered)
            m, (w, h) = clip.matrix, clip.size
            poly = QPolygonF([QPointF(*apply(m, x, y)) for x, y in ((0, 0), (w, 0), (w, h), (0, h))])
            path = QPainterPath()
            path.addPolygon(poly)
            self.p.setClipPath(path, Qt.ClipOperation.IntersectClip)
        self.p.setCompositionMode(BLENDS.get(item.blend, QPainter.CompositionMode.CompositionMode_SourceOver))
        self.p.setOpacity(max(0.0, min(1.0, item.alpha)))

    def quad(self, item: DrawItem, picture: Picture | None) -> None:
        w, h = item.size
        if w <= 0 or h <= 0:
            return
        self._begin(item)
        a, b, c, d, tx, ty = item.matrix
        sx, sy = math.hypot(a, b) or 1e-9, math.hypot(c, d) or 1e-9
        if item.nine is not None and picture is not None:  # the border keeps its size, the middle stretches
            self.p.setTransform(qtransform((a / sx, b / sx, c / sy, d / sy, tx, ty)) * self.base)
            self._nine(self._tint(picture, item.color), item.nine, (w * sx, h * sy), (w, h))
        else:
            self.p.setTransform(qtransform(item.matrix) * self.base)
            if picture is None:
                self.p.fillRect(QRectF(0, 0, w, h), QColor(*_mix(item.color, MISSING)))
            else:
                img = self._tint(picture, item.color)
                ox, oy, us, vs = item.uv
                src = QRectF(ox * img.width(), oy * img.height(), us * img.width(), vs * img.height())
                self.p.drawImage(QRectF(0, 0, w, h), img, src)
        self.p.restore()

    def _nine(self, img: QImage, nine, shown: tuple[float, float], natural: tuple[float, float]) -> None:
        iw, ih = img.width(), img.height()
        (w, h), (nw, nh) = shown, natural
        left, top, right, bottom = (nine[0] * iw, nine[1] * ih, nine[2] * iw, nine[3] * ih)
        dl, dr = left * nw / iw, right * nw / iw  # the border in reference units, as the asset's size has it
        dt, db = top * nh / ih, bottom * nh / ih
        dl, dr = min(dl, w / 2), min(dr, w / 2)
        dt, db = min(dt, h / 2), min(db, h / 2)
        xs_src, ys_src = (0, left, iw - right, iw), (0, top, ih - bottom, ih)
        xs_dst, ys_dst = (0, dl, w - dr, w), (0, dt, h - db, h)
        for i in range(3):
            for j in range(3):
                src = QRectF(xs_src[i], ys_src[j], xs_src[i + 1] - xs_src[i], ys_src[j + 1] - ys_src[j])
                dst = QRectF(xs_dst[i], ys_dst[j], xs_dst[i + 1] - xs_dst[i], ys_dst[j + 1] - ys_dst[j])
                if src.width() > 0 and src.height() > 0 and dst.width() > 0 and dst.height() > 0:
                    self.p.drawImage(dst, img, src)

    def text(self, item: DrawItem) -> None:
        style = item.style or TextStyle()
        if not item.text:
            return
        self._begin(item)
        self.p.setTransform(qtransform(item.matrix) * self.base)
        font = fonts.qfont(style)
        w, h = item.size
        laid = fonts.lay_out(item.text, style, w if style.wrap or style.cut else 0.0, h if style.vcut else 0.0)
        metrics = QFontMetricsF(font)
        paths = []
        for i, line in enumerate(laid.lines):
            free = max(0.0, w - metrics.horizontalAdvance(line))
            x = {1: free / 2, 2: free}.get(style.align, 0.0)
            path = QPainterPath()
            path.addText(QPointF(x, i * laid.line_height + metrics.ascent()), font, line)
            paths.append(path)
        if style.clip and w > 0:
            self.p.setClipRect(QRectF(0, -h, w, laid.height + 2 * h), Qt.ClipOperation.IntersectClip)
        if style.shadow is not None:
            rgb, alpha, dist, angle = style.shadow
            r = math.radians(angle)
            self.p.save()
            self.p.setOpacity(self.p.opacity() * alpha)
            self.p.translate(dist * math.cos(r), dist * math.sin(r))
            for path in paths:
                self.p.fillPath(path, QColor(*rgb))
            self.p.restore()
        if style.outline is not None:  # drawn under the glyphs, half its width outside them
            rgb, alpha = style.outline
            pen = QPen(QColor(*rgb), 2 * style.outline_size * style.size)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            self.p.save()
            self.p.setOpacity(self.p.opacity() * alpha)
            for path in paths:
                self.p.strokePath(path, pen)
            self.p.restore()
        for path in paths:
            self.p.fillPath(path, QColor(*style.color))
        self.p.restore()

    def layered(self, item: DrawItem, masks: list[tuple[Clip, Picture | None]], draw: Callable[[Canvas], None]) -> None:
        """Draw ``item`` (by ``draw``) into a layer, multiply the layer by its soft masks, lay it down."""
        device = self.p.device()
        x0, y0, x1, y1 = bounds(item.matrix, *item.size)
        r = self.base.mapRect(QRectF(x0, y0, x1 - x0, y1 - y0)).intersected(
            QRectF(0, 0, device.width(), device.height())
        )
        if r.width() < 1 or r.height() < 1:
            return
        bx, by, w, h = int(r.x()), int(r.y()), int(math.ceil(r.width())) + 1, int(math.ceil(r.height())) + 1
        layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(0)
        lp = QPainter(layer)
        lp.setRenderHints(self.p.renderHints())
        shift = self.base * QTransform.fromTranslate(-bx, -by)
        lp.setTransform(shift)
        draw(PainterCanvas(lp))
        lp.end()
        cover = np.ones((h, w), np.float32)
        for clip, picture in masks:
            cover *= _layer_mask(clip, picture, shift, (w, h))
        arr = np.frombuffer(layer.constBits(), np.uint8).reshape(h, layer.bytesPerLine() // 4, 4)[:, :w]
        out = (arr.astype(np.float32) * cover[..., None]).astype(np.uint8)
        result = QImage(
            np.ascontiguousarray(out).tobytes(), w, h, 4 * w, QImage.Format.Format_ARGB32_Premultiplied
        ).copy()
        self.p.save()
        self.p.setTransform(QTransform())
        self.p.drawImage(bx, by, result)
        self.p.restore()

    def outline(self, matrix: Matrix, size: tuple[float, float], color: tuple[int, int, int], width: float) -> None:
        w, h = size
        self.p.save()
        self.p.setTransform(self.base)
        pen = QPen(QColor(*color))
        pen.setCosmetic(True)
        pen.setWidthF(width)
        self.p.setPen(pen)
        poly = QPolygonF([QPointF(*apply(matrix, x, y)) for x, y in ((0, 0), (w, 0), (w, h), (0, h), (0, 0))])
        self.p.drawPolyline(poly)
        self.p.restore()


def _alpha(img: QImage) -> np.ndarray:
    """The alpha of a premultiplied ARGB32 picture as floats (rows of its own stride)."""
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
    return arr[:, : img.width(), 3].astype(np.float32) / 255.0


def _layer_mask(clip: Clip, picture: Picture | None, transform: QTransform, size: tuple[int, int]) -> np.ndarray:
    """A soft mask's coverage over a layer: its picture's alpha, smooth-stepped, inside its rectangle."""
    w, h = size
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setTransform(qtransform(clip.matrix) * transform)
    cw, ch = clip.size
    if picture is not None:
        p.drawImage(QRectF(0, 0, cw, ch), _qimage(picture))
    else:
        p.fillRect(QRectF(0, 0, cw, ch), QColor(255, 255, 255))
    p.end()
    cover = effects.soft_step(_alpha(img), clip.soft or {})
    if (clip.soft or {}).get("ExclusiveMask"):  # outside its rectangle it keeps what is drawn
        rect = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        rect.fill(0)
        q = QPainter(rect)
        q.setTransform(qtransform(clip.matrix) * transform)
        q.fillRect(QRectF(0, 0, cw, ch), QColor(255, 255, 255))
        q.end()
        cover = np.where(_alpha(rect) > 0, cover, 1.0)
    return cover


def _qimage(picture: Picture) -> QImage:
    h, w = picture.rgba.shape[:2]
    return QImage(np.ascontiguousarray(picture.rgba).tobytes(), w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()


_EFFECTED: dict[tuple, Picture] = {}
EFFECTED_LIMIT = 400  # pictures kept at most


def with_effects(picture: Picture | None, fx: dict, seconds: float = 0.0) -> Picture | None:
    """A picture with an item's effects applied (cached by picture and values; a moving effect, a scroll or a blink,
    also by the step of ``seconds`` it is made for: a blink and a scroll read the time, nothing else does)."""
    if picture is None or not fx:
        return picture
    step = round(seconds / effects.STEP_SECONDS) if effects.moving(fx) else 0
    key = (picture.key, repr(sorted(fx.items())), step)
    if key not in _EFFECTED:
        if len(_EFFECTED) > EFFECTED_LIMIT:  # a long play of a scroll would otherwise keep every step it drew
            _EFFECTED.clear()
        _EFFECTED[key] = Picture(
            f"{picture.key}|{key[1]}|{step}", effects.apply(picture.rgba, fx, step * effects.STEP_SECONDS)
        )
    return _EFFECTED[key]


def moves(frame: Frame) -> bool:
    """Whether a picture of ``frame`` has an effect that changes with time (the preview then runs its clock)."""
    return any(item.kind == "image" and effects.moving(item.effects) for item in frame.items)


def _mix(a, b):
    return tuple(x * y // 255 for x, y in zip(a, b, strict=True))


def _stand_in(canvas: Canvas, item: DrawItem, name: str) -> None:
    """A picture the game names at run time, as a neutral tile with the slot's name (a placeholder)."""
    canvas.quad(DrawItem("box", item.matrix, item.size, STAND_IN, item.alpha, 0, clips=item.clips), None)
    style = TextStyle(size=max(6.0, min(14.0, item.size[1] / 3)), color=(30, 30, 30), align=1, wrap=True)
    canvas.text(DrawItem("text", item.matrix, item.size, alpha=item.alpha, text=name, style=style, clips=item.clips))


def draw(
    frame: Frame, canvas: Canvas, pictures: Pictures | None, seconds: float = 0.0, world: Scene | None = None
) -> None:
    """Every draw item of ``frame`` in order; masks clip and are not drawn themselves, soft ones through a layer.
    ``world`` is the 3D scene behind the frame: the effects that read it (``effects.WORLD``) draw from it."""
    for item in frame.items:
        if item.mask:
            continue
        soft = [c for c in item.clips if c.soft is not None]
        if soft:
            masks = [(c, pictures.get(c.image, c.image_id, c.size) if pictures is not None else None) for c in soft]
            canvas.layered(item, masks, lambda cv, it=item: _draw_item(cv, it, pictures, seconds, world, frame.size))
        else:
            _draw_item(canvas, item, pictures, seconds, world, frame.size)


def _draw_item(canvas: Canvas, item: DrawItem, pictures: Pictures | None, seconds: float = 0.0,
               world: Scene | None = None, size: tuple[float, float] = (0.0, 0.0)) -> None:  # fmt: skip
    if item.kind == "image" and (item.image_id or "").startswith(placeholders.PICTURE):
        _stand_in(canvas, item, item.image_id[len(placeholders.PICTURE) :])
    elif item.kind == "image":
        pic = pictures.get(item.image, item.image_id, item.size) if pictures is not None else None
        if world is not None and any(k in item.effects for k in effects.WORLD):
            item, pic = worldfx.shade(item, pic, world, size)
        canvas.quad(item, with_effects(pic, item.effects, seconds))
    elif item.kind == "text":
        canvas.text(item)
    elif item.kind == "video":
        canvas.quad(DrawItem("box", item.matrix, item.size, (40, 40, 60), item.alpha, 0, clips=item.clips), None)


def render(
    frame: Frame,
    pictures: Pictures | None,
    zoom: float = 1.0,
    background=(30, 30, 34),
    seconds: float = 0.0,
    world: Scene | None = None,
) -> QImage:
    """The frame as a picture at ``zoom`` (an offscreen preview: screenshots, tests); over ``world``, the scene
    behind the UI, when one is given."""
    w, h = max(1, round(frame.size[0] * zoom)), max(1, round(frame.size[1] * zoom))
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(*background))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(zoom, zoom)
    if world is not None:
        p.drawImage(QRectF(0, 0, frame.size[0], frame.size[1]), worldscene.to_qimage(world))
    draw(frame, PainterCanvas(p), pictures, seconds, world)
    p.end()
    return img
