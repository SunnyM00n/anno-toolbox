"""What the preview draws through: a small interface (a quad with a picture, nine-slice, tint, blend and clips; a
text; an outline) and its backend on Qt's painter. A GPU backend (QRhi) can take the painter's place behind the
same calls.

``draw(frame, canvas, pictures, seconds)`` draws a placed frame (``layout.Frame``) at a time, which the moving effects
(a scroll, a blink) read and the 3D scene the world effects draw from; ``CPainterCanvas`` is the backend.
docs/rendering/ui-117.md is how the game draws (blend modes, masks); docs/ui-editor.md "The preview" says what differs.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Protocol

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform

from . import effects, fonts, labels, placeholders, worldfx, worldscene
from .labels import STextStyle
from .layout import Matrix, SClip, SDrawItem, SFrame, apply, bounds
from .pictures import CPictures, SPicture
from .worldscene import CScene

BLENDS = {2: QPainter.CompositionMode.CompositionMode_Plus, 3: QPainter.CompositionMode.CompositionMode_Multiply}
MISSING = (90, 90, 110)  # a picture the game does not have: a flat stand-in
STAND_IN = (170, 160, 140)  # a placeholder picture: a light neutral tile


class ICanvas(Protocol):
    """The calls a backend answers; matrices map a widget's own units into the reference space."""

    def quad(self, item: SDrawItem, picture: SPicture | None) -> None: ...

    def text(self, item: SDrawItem) -> None: ...

    def outline(
        self, matrix: Matrix, size_px: tuple[float, float], color: tuple[int, int, int], width_px: float
    ) -> None: ...

    def layered(
        self, item: SDrawItem, masks: list[tuple[SClip, SPicture | None]], draw: Callable[[ICanvas], None]
    ) -> None: ...


def qtransform(matrix: Matrix) -> QTransform:
    a, b, c, d, tx, ty = matrix
    return QTransform(a, b, c, d, tx, ty)


def measure(text: str, style: STextStyle, width_px: float) -> tuple[float, float]:
    """The size a text takes in its font: one line, or wrapped at ``width_px`` (``fonts.lay_out``)."""
    lines = fonts.lay_out(text or " ", style, width_px)
    return lines.width_px, lines.height_px  # a wrapped text is as wide as its widest line (the game's DisplayedSize)


class CPainterCanvas:
    """The painter backend: ``painter`` already carries the zoom and pan (reference space to pixels)."""

    def __init__(self, painter: QPainter) -> None:
        self.m_p = painter
        self.m_base = painter.transform()
        self._m_images: dict[str, QImage] = {}
        self._m_tinted: dict[tuple, QImage] = {}

    def _image(self, picture: SPicture) -> QImage:
        image = self._m_images.get(picture.key)
        if image is None:
            height_px, width_px = picture.rgba.shape[:2]
            image = QImage(
                picture.rgba.tobytes(), width_px, height_px, 4 * width_px, QImage.Format.Format_RGBA8888
            ).copy()
            self._m_images[picture.key] = image
        return image

    def _tint(self, picture: SPicture, rgb: tuple[int, int, int]) -> QImage:
        image = self._image(picture)
        if tuple(rgb) == (255, 255, 255):
            return image
        key = (picture.key, tuple(rgb))
        if key not in self._m_tinted:
            out = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            painter = QPainter(out)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            painter.fillRect(out.rect(), QColor(*rgb))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawImage(0, 0, image)
            painter.end()
            self._m_tinted[key] = out
        return self._m_tinted[key]

    def _begin(self, item: SDrawItem) -> None:
        self.m_p.save()
        self.m_p.setTransform(self.m_base)
        for clip in item.clips:
            if clip.soft is not None:
                continue  # a soft mask multiplies the item's alpha (layered)
            matrix, (width, height) = clip.matrix, clip.size
            poly = QPolygonF(
                [QPointF(*apply(matrix, x, y)) for x, y in ((0, 0), (width, 0), (width, height), (0, height))]
            )
            path = QPainterPath()
            path.addPolygon(poly)
            self.m_p.setClipPath(path, Qt.ClipOperation.IntersectClip)
        self.m_p.setCompositionMode(BLENDS.get(item.blend, QPainter.CompositionMode.CompositionMode_SourceOver))
        self.m_p.setOpacity(max(0.0, min(1.0, item.alpha)))

    def quad(self, item: SDrawItem, picture: SPicture | None) -> None:
        width, height = item.size
        if width <= 0 or height <= 0:
            return
        self._begin(item)
        a, b, c, d, tx, ty = item.matrix
        scale_x, scale_y = math.hypot(a, b) or 1e-9, math.hypot(c, d) or 1e-9
        if item.nine is not None and picture is not None:  # the border keeps its size, the middle stretches
            self.m_p.setTransform(
                qtransform((a / scale_x, b / scale_x, c / scale_y, d / scale_y, tx, ty)) * self.m_base
            )
            self._nine(self._tint(picture, item.color), item.nine, (width * scale_x, height * scale_y), (width, height))
        else:
            self.m_p.setTransform(qtransform(item.matrix) * self.m_base)
            if picture is None:
                self.m_p.fillRect(QRectF(0, 0, width, height), QColor(*_mix(item.color, MISSING)))
            else:
                image = self._tint(picture, item.color)
                uv_x, uv_y, uv_width, uv_height = item.uv_offset_scale
                source_rect = QRectF(
                    uv_x * image.width(), uv_y * image.height(), uv_width * image.width(), uv_height * image.height()
                )
                self.m_p.drawImage(QRectF(0, 0, width, height), image, source_rect)
        self.m_p.restore()

    def _nine(self, image: QImage, nine, shown: tuple[float, float], natural: tuple[float, float]) -> None:
        image_width, image_height = image.width(), image.height()
        (width, height), (natural_width, natural_height) = shown, natural
        left, top, right, bottom = (
            nine[0] * image_width,
            nine[1] * image_height,
            nine[2] * image_width,
            nine[3] * image_height,
        )
        border_left, border_right = (
            left * natural_width / image_width,
            right * natural_width / image_width,
        )  # the border in reference units, as the asset's size has it
        border_top, border_bottom = top * natural_height / image_height, bottom * natural_height / image_height
        border_left, border_right = min(border_left, width / 2), min(border_right, width / 2)
        border_top, border_bottom = min(border_top, height / 2), min(border_bottom, height / 2)
        xs_src, ys_src = (0, left, image_width - right, image_width), (0, top, image_height - bottom, image_height)
        xs_dst, ys_dst = (0, border_left, width - border_right, width), (0, border_top, height - border_bottom, height)
        for i in range(3):
            for j in range(3):
                source_rect = QRectF(xs_src[i], ys_src[j], xs_src[i + 1] - xs_src[i], ys_src[j + 1] - ys_src[j])
                target_rect = QRectF(xs_dst[i], ys_dst[j], xs_dst[i + 1] - xs_dst[i], ys_dst[j + 1] - ys_dst[j])
                if (
                    source_rect.width() > 0
                    and source_rect.height() > 0
                    and target_rect.width() > 0
                    and target_rect.height() > 0
                ):
                    self.m_p.drawImage(target_rect, image, source_rect)

    def text(self, item: SDrawItem) -> None:
        style = item.style or STextStyle()
        if not item.text:
            return
        self._begin(item)
        self.m_p.setTransform(qtransform(item.matrix) * self.m_base)
        font = fonts.qfont(style)
        width, height = (
            plane_side or item_side for plane_side, item_side in zip(style.plane, item.size, strict=True)
        )  # the text aligns in the plane
        laid = fonts.lay_out(item.text, style, width if style.wrap or style.cut else 0.0, height if style.vcut else 0.0)
        metrics = QFontMetricsF(font)
        top = labels.text_top(style.valign, height, laid.height_px, metrics.ascent(), laid.first)
        paths = []
        for i, line in enumerate(laid.lines):
            free = width - metrics.horizontalAdvance(line)  # below 0 a wider text reaches past the plane (game)
            x = {1: free / 2, 2: free}.get(style.align, 0.0)
            path = QPainterPath()
            path.addText(QPointF(x, top + i * laid.line_height + metrics.ascent()), font, line)
            paths.append(path)
        if style.clip and width > 0:
            self.m_p.setClipRect(QRectF(0, -height, width, laid.height_px + 2 * height), Qt.ClipOperation.IntersectClip)
        if style.shadow is not None:
            rgb, alpha, distance, angle_deg = style.shadow
            radians = math.radians(angle_deg)
            self.m_p.save()
            self.m_p.setOpacity(self.m_p.opacity() * alpha)
            self.m_p.translate(distance * math.cos(radians), distance * math.sin(radians))
            for path in paths:
                self.m_p.fillPath(path, QColor(*rgb))
            self.m_p.restore()
        if style.outline is not None:  # drawn under the glyphs, half its width outside them
            rgb, alpha = style.outline
            pen = QPen(QColor(*rgb), 2 * style.outline_size * style.size)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            self.m_p.save()
            self.m_p.setOpacity(self.m_p.opacity() * alpha)
            for path in paths:
                self.m_p.strokePath(path, pen)
            self.m_p.restore()
        for path in paths:
            self.m_p.fillPath(path, QColor(*style.color))
        self.m_p.restore()

    def layered(
        self, item: SDrawItem, masks: list[tuple[SClip, SPicture | None]], draw: Callable[[ICanvas], None]
    ) -> None:
        """Draw ``item`` (by ``draw``) into a layer, multiply the layer by its soft masks, lay it down."""
        device = self.m_p.device()
        min_x, min_y, max_x, max_y = bounds(item.matrix, *item.size)
        rect = self.m_base.mapRect(QRectF(min_x, min_y, max_x - min_x, max_y - min_y)).intersected(
            QRectF(0, 0, device.width(), device.height())
        )
        if rect.width() < 1 or rect.height() < 1:
            return
        box_x, box_y, width, height = (
            int(rect.x()),
            int(rect.y()),
            int(math.ceil(rect.width())) + 1,
            int(math.ceil(rect.height())) + 1,
        )
        layer = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(0)
        layer_painter = QPainter(layer)
        layer_painter.setRenderHints(self.m_p.renderHints())
        shift = self.m_base * QTransform.fromTranslate(-box_x, -box_y)
        layer_painter.setTransform(shift)
        draw(CPainterCanvas(layer_painter))
        layer_painter.end()
        cover = np.ones((height, width), np.float32)
        for clip, picture in masks:
            cover *= _layer_mask(clip, picture, shift, (width, height))
        pixels = np.frombuffer(layer.constBits(), np.uint8).reshape(height, layer.bytesPerLine() // 4, 4)[:, :width]
        out = (pixels.astype(np.float32) * cover[..., None]).astype(np.uint8)
        result = QImage(
            np.ascontiguousarray(out).tobytes(), width, height, 4 * width, QImage.Format.Format_ARGB32_Premultiplied
        ).copy()
        self.m_p.save()
        self.m_p.setTransform(QTransform())
        self.m_p.drawImage(box_x, box_y, result)
        self.m_p.restore()

    def outline(self, matrix: Matrix, size: tuple[float, float], color: tuple[int, int, int], width: float) -> None:
        size_width, size_height = size
        self.m_p.save()
        self.m_p.setTransform(self.m_base)
        pen = QPen(QColor(*color))
        pen.setCosmetic(True)
        pen.setWidthF(width)
        self.m_p.setPen(pen)
        poly = QPolygonF(
            [
                QPointF(*apply(matrix, x, y))
                for x, y in ((0, 0), (size_width, 0), (size_width, size_height), (0, size_height), (0, 0))
            ]
        )
        self.m_p.drawPolyline(poly)
        self.m_p.restore()


def _alpha(image: QImage) -> np.ndarray:
    """The alpha of a premultiplied ARGB32 picture as floats (rows of its own stride)."""
    pixels = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine() // 4, 4)
    return pixels[:, : image.width(), 3].astype(np.float32) / 255.0


def _layer_mask(clip: SClip, picture: SPicture | None, transform: QTransform, size_px: tuple[int, int]) -> np.ndarray:
    """A soft mask's coverage over a layer: its picture's alpha, smooth-stepped, inside its rectangle."""
    width, height = size_px
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setTransform(qtransform(clip.matrix) * transform)
    clip_width, clip_height = clip.size
    if picture is not None:
        painter.drawImage(QRectF(0, 0, clip_width, clip_height), _qimage(picture))
    else:
        painter.fillRect(QRectF(0, 0, clip_width, clip_height), QColor(255, 255, 255))
    painter.end()
    cover = effects.soft_step(_alpha(image), clip.soft or {})
    if (clip.soft or {}).get("ExclusiveMask"):  # outside its rectangle it keeps what is drawn
        rect = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        rect.fill(0)
        rect_painter = QPainter(rect)
        rect_painter.setTransform(qtransform(clip.matrix) * transform)
        rect_painter.fillRect(QRectF(0, 0, clip_width, clip_height), QColor(255, 255, 255))
        rect_painter.end()
        cover = np.where(_alpha(rect) > 0, cover, 1.0)
    return cover


def _qimage(picture: SPicture) -> QImage:
    height_px, width_px = picture.rgba.shape[:2]
    return QImage(
        np.ascontiguousarray(picture.rgba).tobytes(), width_px, height_px, 4 * width_px, QImage.Format.Format_RGBA8888
    ).copy()


_g_effected: dict[tuple, tuple[SPicture, SPicture]] = {}  # key -> (the picture it was made from, the result)
EFFECTED_LIMIT = 400  # pictures kept at most


def with_effects(picture: SPicture | None, fx: dict, seconds: float = 0.0) -> SPicture | None:
    """A picture with an item's effects applied (cached by picture and values; a moving effect, a scroll or a blink,
    also by the step of ``seconds`` it is made for: a blink and a scroll read the time, nothing else does)."""
    if picture is None or not fx:
        return picture
    step = round(seconds / effects.STEP_SECONDS) if effects.moving(fx) else 0
    key = (picture.key, repr(sorted(fx.items())), step)
    kept = _g_effected.get(key)
    if kept is None or kept[0] is not picture:  # another picture of the same name (another game) is made anew
        if len(_g_effected) > EFFECTED_LIMIT:  # a long play of a scroll would otherwise keep every step it drew
            _g_effected.clear()
        made = SPicture(f"{picture.key}|{key[1]}|{step}", effects.apply(picture.rgba, fx, step * effects.STEP_SECONDS))
        kept = _g_effected[key] = (picture, made)
    return kept[1]


def moves(frame: SFrame) -> bool:
    """Whether a picture of ``frame`` has an effect that changes with time (the preview then runs its clock)."""
    return any(item.kind == "image" and effects.moving(item.effects) for item in frame.items)


def _mix(first, second):
    return tuple(x * y // 255 for x, y in zip(first, second, strict=True))


def _stand_in(canvas: ICanvas, item: SDrawItem, name: str) -> None:
    """A picture the game names at run time, as a neutral tile with the slot's name, where the game has no icon file
    to show (``CPictures.sample``; the Checks list says so)."""
    canvas.quad(SDrawItem("box", item.matrix, item.size, STAND_IN, item.alpha, 0, clips=item.clips), None)
    style = STextStyle(size=max(6.0, min(14.0, item.size[1] / 3)), color=(30, 30, 30), align=1, wrap=True)
    canvas.text(SDrawItem("text", item.matrix, item.size, alpha=item.alpha, text=name, style=style, clips=item.clips))


def draw(
    frame: SFrame, canvas: ICanvas, pictures: CPictures | None, seconds: float = 0.0, world: CScene | None = None
) -> None:
    """Every draw item of ``frame`` in order; masks clip and are not drawn themselves, soft ones through a layer.
    ``world`` is the 3D scene behind the frame: the effects that read it (``effects.WORLD``) draw from it."""
    for item in frame.items:
        if item.mask:
            continue
        soft = [clip for clip in item.clips if clip.soft is not None]
        if soft:
            masks = [
                (clip, pictures.get(clip.image, clip.image_id, clip.size) if pictures is not None else None)
                for clip in soft
            ]
            canvas.layered(
                item,
                masks,
                lambda target_canvas, it=item: _draw_item(target_canvas, it, pictures, seconds, world, frame.size),
            )
        else:
            _draw_item(canvas, item, pictures, seconds, world, frame.size)


def _draw_item(canvas: ICanvas, item: SDrawItem, pictures: CPictures | None, seconds: float = 0.0,
               world: CScene | None = None, size_px: tuple[float, float] = (0.0, 0.0)) -> None:  # fmt: skip
    if item.kind == "image" and (item.image_id or "").startswith(placeholders.PICTURE):
        slot = item.image_id[len(placeholders.PICTURE) :]
        picture = pictures.sample(slot, item.size) if pictures is not None else None
        if picture is not None:  # a real icon of the game stands in for the picture the game names at run time
            canvas.quad(item, with_effects(picture, item.effects, seconds))
        else:
            _stand_in(canvas, item, slot)
    elif item.kind == "image":
        picture = pictures.get(item.image, item.image_id, item.size) if pictures is not None else None
        if world is not None and any(k in item.effects for k in effects.WORLD):
            item, picture = worldfx.shade(item, picture, world, size_px)
        canvas.quad(item, with_effects(picture, item.effects, seconds))
    elif item.kind == "text":
        canvas.text(item)
    elif item.kind == "video":
        canvas.quad(SDrawItem("box", item.matrix, item.size, (40, 40, 60), item.alpha, 0, clips=item.clips), None)


def render(
    frame: SFrame,
    pictures: CPictures | None,
    zoom: float = 1.0,
    background=(30, 30, 34),
    seconds: float = 0.0,
    world: CScene | None = None,
) -> QImage:
    """The frame as a picture at ``zoom`` (an offscreen preview: screenshots, tests); over ``world``, the scene
    behind the UI, when one is given."""
    width, height = max(1, round(frame.size[0] * zoom)), max(1, round(frame.size[1] * zoom))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(*background))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(zoom, zoom)
    if world is not None:
        painter.drawImage(QRectF(0, 0, frame.size[0], frame.size[1]), worldscene.to_qimage(world))
    draw(frame, CPainterCanvas(painter), pictures, seconds, world)
    painter.end()
    return image
