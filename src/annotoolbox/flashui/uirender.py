"""The preview of the Flash UI editor: a movie's first frame drawn with Qt's painter (the ``desktop`` extra).

``render`` draws the main timeline or one sprite into a ``QImage``: the placements of the first frame by depth,
each with its matrix, colour transform and blend mode, masks by clip depth; shapes as filled outlines (solid,
gradient, or the game's picture through ``images``), text fields with the font family their font reference names.
What the game adds at run time (the code builds most lists and values) and what the preview leaves out (filters,
the fonts' own glyphs) are listed in docs/formats/anno2205-ui-format.md, "The preview"; a picture it cannot find is
drawn as a crossed placeholder (docs/ui/2205/flash-ui-editor.md, "What the preview cannot draw").
"""

from __future__ import annotations

import re
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
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
from ..formats.feuread import SMatrix, SMovie, STag

TWIPS = 20
MAX_DEPTH = 24  # nested sprites drawn at most this deep (a movie can nest itself through its code)
ALIGN = {0: Qt.AlignLeft, 1: Qt.AlignRight, 2: Qt.AlignHCenter, 3: Qt.AlignJustify}

ImageLoader = Callable[[feuread.SExternalImage], QImage | None]
SAME = feuread.SColorTransform()
SWATCH, LIVE = "swatch", "live"
TINT = QColor(122, 146, 168)  # 2205's panel blue-grey: the stand-in colour of a swatch the code colours at run time
REASONS = {  # why a picture is drawn as a stand-in (the status line and the preview's tooltip)
    SWATCH: "white base pictures that the game's code colours at run time (tinted blue-grey as a placeholder)",
    LIVE: "pictures the game renders live from its 3D scene (drawn hatched, crossed)",
}
# Flash's blend modes (PlaceObject3) that Qt composes per drawn shape; the rest draw as normal
BLEND = {
    3: QPainter.CompositionMode_Multiply, 4: QPainter.CompositionMode_Screen, 5: QPainter.CompositionMode_Lighten,
    6: QPainter.CompositionMode_Darken, 7: QPainter.CompositionMode_Difference, 8: QPainter.CompositionMode_Plus,
    13: QPainter.CompositionMode_Overlay, 14: QPainter.CompositionMode_HardLight,
}  # fmt: skip


def _qt(matrix: SMatrix) -> QTransform:
    return QTransform(matrix.a, matrix.b, matrix.c, matrix.d, matrix.tx, matrix.ty)


def _color(rgba) -> QColor:
    red, green, blue, alpha = rgba
    return QColor(red, green, blue, alpha)


def _flat_white(picture: QImage, floor: int = 240, solid: bool = False) -> bool:
    """Whether every visible pixel is light (all channels at least ``floor``): a base the code tints (a colour
    swatch, a gradient or a mask). ``solid`` also wants every pixel opaque, so a white icon is no swatch."""
    width_px, height_px = picture.width(), picture.height()
    if width_px == 0 or height_px == 0:
        return False
    for x, y in (
        (0, 0),
        (width_px - 1, height_px - 1),
        (width_px // 2, height_px // 2),
        (0, height_px - 1),
        (width_px - 1, 0),
    ):  # a quick no first
        pixel = picture.pixelColor(x, y)
        if pixel.alpha() > 8 and min(pixel.red(), pixel.green(), pixel.blue()) < floor:
            return False
    premult = picture.convertToFormat(QImage.Format_RGBA8888_Premultiplied)
    pixels = np.frombuffer(premult.constBits(), np.uint8)[: width_px * height_px * 4].reshape(-1, 4).astype(np.int32)
    alpha = pixels[:, 3]
    if solid and alpha.min() < 250:
        return False
    return bool(alpha.max() > 8 and (pixels[:, :3].min(axis=1) >= alpha * floor // 255 - 12).all())


def _hatched(picture: QImage, kind: str) -> QImage:
    """A stand-in for a picture the game paints at run time: a swatch tinted with 2205's panel blue-grey (its own
    shading kept), a live picture dark grey with diagonal hatch lines and a cross."""
    out = QImage(picture.size(), QImage.Format_ARGB32_Premultiplied)
    out.fill(0)
    painter = QPainter(out)
    painter.drawImage(0, 0, picture)
    if kind == SWATCH:
        painter.setCompositionMode(
            QPainter.CompositionMode_Multiply
        )  # a white base takes the tint, a gradient keeps its shade
        painter.fillRect(out.rect(), TINT)
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)  # and the picture's own shape and opacity
        painter.drawImage(0, 0, picture)
        painter.end()
        return out
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)  # keep the picture's own shape and opacity
    painter.fillRect(out.rect(), QColor(60, 60, 70))
    painter.setCompositionMode(QPainter.CompositionMode_SourceAtop)
    painter.setPen(QPen(QColor(235, 200, 90, 200), 1))
    step = max(4, min(picture.width(), picture.height()) // 4)
    for k in range(-picture.height(), picture.width(), step):
        painter.drawLine(k, picture.height(), k + picture.height(), 0)
    if kind == LIVE:
        painter.drawLine(0, 0, picture.width(), picture.height())
        painter.drawLine(picture.width(), 0, 0, picture.height())
    painter.end()
    return out


def _edge_extended(picture: QImage, outer: QRect) -> QImage:
    """The picture on a canvas of ``outer`` (pixel space, containing it), the margins filled with its edge pixels."""
    width_px, height_px = picture.width(), picture.height()
    out = QImage(outer.size(), QImage.Format_ARGB32_Premultiplied)
    out.fill(0)
    painter = QPainter(out)
    origin_x, origin_y = -outer.left(), -outer.top()  # where the picture's origin sits on the canvas
    x_spans = (
        (0, 0, origin_x),
        (width_px - 1, origin_x + width_px, out.width() - origin_x - width_px),
    )  # source column, first target column, width
    y_spans = ((0, 0, origin_y), (height_px - 1, origin_y + height_px, out.height() - origin_y - height_px))
    for source_x, target_x, target_width in x_spans:
        for source_y, target_y, target_height in y_spans:  # the four corners
            if target_width > 0 and target_height > 0:
                painter.drawImage(
                    QRect(target_x, target_y, target_width, target_height), picture, QRect(source_x, source_y, 1, 1)
                )
        if target_width > 0:
            painter.drawImage(
                QRect(target_x, origin_y, target_width, height_px), picture, QRect(source_x, 0, 1, height_px)
            )
    for source_y, target_y, target_height in y_spans:
        if target_height > 0:
            painter.drawImage(
                QRect(origin_x, target_y, width_px, target_height), picture, QRect(0, source_y, width_px, 1)
            )
    painter.drawImage(origin_x, origin_y, picture)
    painter.end()
    return out


def _clamp(value: float) -> int:
    return max(0, min(255, int(value)))


def compose(outer: feuread.SColorTransform, inner: feuread.SColorTransform) -> feuread.SColorTransform:
    """The transform of ``inner`` (a placement) seen through ``outer`` (the sprites above it); the alpha
    multiplier is left to the painter's opacity, so it is 1 here."""
    mult = tuple(
        outer_mult * inner_mult
        for outer_mult, inner_mult in zip(outer.multipliers[:3], inner.multipliers[:3], strict=True)
    )
    add = tuple(
        round(outer_mult * inner_add + outer_add)
        for outer_mult, inner_add, outer_add in zip(
            outer.multipliers[:3], inner.offsets[:3], outer.offsets[:3], strict=True
        )
    )
    return feuread.SColorTransform((*mult, 1.0), (*add, outer.offsets[3] + inner.offsets[3]))


def tint(color_transform: feuread.SColorTransform, rgba) -> tuple[int, int, int, int]:
    """A colour through a colour transform (alpha multiplier excluded: see ``compose``)."""
    red, green, blue, alpha = rgba
    return (
        _clamp(red * color_transform.multipliers[0] + color_transform.offsets[0]),
        _clamp(green * color_transform.multipliers[1] + color_transform.offsets[1]),
        _clamp(blue * color_transform.multipliers[2] + color_transform.offsets[2]),
        _clamp(alpha + color_transform.offsets[3]),
    )


def tinted(picture: QImage, color_transform: feuread.SColorTransform) -> QImage:
    """A picture through a colour transform, per channel with lookup tables (straight alpha, as Flash does)."""
    if color_transform == SAME:
        return picture
    source = picture.convertToFormat(QImage.Format_RGBA8888)
    raw = (
        bytearray(source.constBits().tobytes()[: source.width() * source.height() * 4])
        if source.width()
        else bytearray()
    )
    for channel in range(4):
        table = bytes(
            _clamp(
                value * (color_transform.multipliers[channel] if channel < 3 else 1.0)
                + color_transform.offsets[channel]
            )
            for value in range(256)
        )
        raw[channel::4] = bytes(raw[channel::4]).translate(table)
    out = QImage(bytes(raw), source.width(), source.height(), source.width() * 4, QImage.Format_RGBA8888)
    return out.copy()


class CRenderer:
    """Draws characters of one movie; ``images`` gives the picture of an image tag (None: a grey stand-in)."""

    def __init__(self, movie: SMovie, images: ImageLoader | None = None, fonts=None) -> None:
        self.m_movie, self.m_images = movie, images or (lambda _img: None)
        self.m_font_choice = (
            fonts  # ``Fonts2205.choose``: the game's face for a font reference (None: the family's name)
        )
        self.m_texts_in_game_font = self.m_texts_in_stand_in = 0  # the texts drawn so far, by what they drew with
        self.m_defs = feuread.definitions(movie)
        self.m_fonts = {
            font.font_id: font for tag in movie.tags if tag.code == feuread.FONT for font in [feuread.read_font(tag)]
        }
        self._m_shapes: dict[int, feushape.SShape] = {}
        self._m_pictures: dict[int, QImage | None] = {}
        self._m_tinted: dict[tuple, QImage] = {}
        self._m_names: dict[int, str] = {}  # image character id -> the path the movie names
        self.m_cx = SAME  # the colour transform of the placements above what is drawn now
        self.m_missing: set[str] = set()  # image paths that had no picture
        self.m_unsupported: set[str] = set()  # what the preview cannot draw, as reasons for the status line

    def frame_tags(self, owner: int) -> list[STag]:
        tags = self.m_movie.tags if owner == 0 else (self.m_defs[owner].children or [])
        return tags

    def display_list(self, tags: list[STag]) -> list[tuple[int, feuread.SPlacement]]:
        """The first frame's placements by depth (later changes at a depth folded into the first)."""
        depths: dict[int, feuread.SPlacement] = {}
        for tag in tags:
            if tag.code == feuread.SHOW_FRAME:
                break
            if tag.code == feuread.REMOVE_OBJECT2:
                depths.pop(int.from_bytes(tag.body[:2], "little"), None)
            elif tag.code in (feuread.PLACE_OBJECT2, feuread.PLACE_OBJECT3):
                placement = feuread.read_placement(tag)
                old = depths.get(placement.depth)
                if placement.char_id is None and old is not None:  # a move: the character stays, the matrix changes
                    placement = feuread.SPlacement(placement.code, placement.flags, placement.depth, old.char_id, old.class_name,
                                          placement.matrix or old.matrix, old.name, placement.head, placement.matrix_raw, placement.tail,
                                          placement.clip_depth, placement.blend)  # fmt: skip
                depths[placement.depth] = placement
        return sorted(depths.items())

    def draw_timeline(self, painter: QPainter, owner: int, level: int = 0) -> None:
        masks: list[tuple[int, QPainterPath]] = []  # (last depth masked, the mask in device space)
        for depth, placement in self.display_list(self.frame_tags(owner)):
            masks = [mask for mask in masks if mask[0] >= depth]
            if placement.char_id is None:
                continue
            if placement.flags & feuread.PLACE_HAS_CLIP_DEPTH:  # a mask: not drawn itself, it clips the depths above it
                masks.append((placement.clip_depth, self._mask_path(placement, painter.transform(), level)))
                continue
            painter.save()
            for _, path in masks:
                painter.setClipPath(_device(painter, path), Qt.IntersectClip)
            if placement.matrix is not None:
                painter.setTransform(_qt(placement.matrix) * painter.transform())
            before = self.m_cx
            color_transform = _cxform(placement)
            if color_transform is not None:
                painter.setOpacity(painter.opacity() * max(0.0, min(1.0, color_transform.multipliers[3])))
                self.m_cx = compose(self.m_cx, color_transform)
            self._blend(painter, placement)
            self.draw_character(painter, placement.char_id, level)
            self.m_cx = before
            painter.restore()

    def _blend(self, painter: QPainter, placement: feuread.SPlacement) -> None:
        if placement.blend in BLEND:
            painter.setCompositionMode(BLEND[placement.blend])
        elif placement.blend > 2:
            self.m_unsupported.add(f"blend mode {placement.blend}")
        if placement.flags & feuread.PLACE3_HAS_FILTERS:
            self.m_unsupported.add("filters (the game draws them on the GPU)")

    def _mask_path(self, placement: feuread.SPlacement, base: QTransform, level: int) -> QPainterPath:
        """The area a mask placement covers, in device space (``base`` is the timeline's transform)."""
        matrix = _qt(placement.matrix) * base if placement.matrix is not None else base
        return matrix.map(self._outline(placement.char_id, level))

    def _outline(self, cid: int, level: int) -> QPainterPath:
        """The outline of every shape of a character (nested sprites at their placements), in its own space."""
        tag = self.m_defs.get(cid)
        path = QPainterPath()
        if tag is None or level >= MAX_DEPTH:
            return path
        if tag.code in feuread.SHAPES:
            if cid not in self._m_shapes:
                self._m_shapes[cid] = feushape.read_shape(tag)
            for _, loops in self._m_shapes[cid].fills:
                part = _path(loops)
                part.setFillRule(Qt.OddEvenFill)
                path.addPath(part)
        elif tag.code == feuread.DEFINE_SPRITE:
            for _, placement in self.display_list(self.frame_tags(cid)):
                if placement.char_id is not None and not placement.flags & feuread.PLACE_HAS_CLIP_DEPTH:
                    inner = self._outline(placement.char_id, level + 1)
                    path.addPath((_qt(placement.matrix) if placement.matrix is not None else QTransform()).map(inner))
        elif tag.code in (feuread.IMAGE, feuread.ATLAS_IMAGE):
            image = feuread.read_image(tag)
            path.addRect(QRectF(0, 0, image.width * TWIPS, image.height * TWIPS))
        return path

    def draw_character(self, painter: QPainter, cid: int, level: int = 0) -> None:
        tag = self.m_defs.get(cid)
        if tag is None:
            return
        if tag.code in feuread.SHAPES:
            self.draw_shape(painter, tag)
        elif tag.code == feuread.DEFINE_EDIT_TEXT:
            self.draw_text(painter, feuread.read_edit_text(tag))
        elif tag.code == feuread.DEFINE_SPRITE and level < MAX_DEPTH:
            self.draw_timeline(painter, cid, level + 1)
        elif tag.code in (feuread.IMAGE, feuread.ATLAS_IMAGE):  # placed as a bitmap (PlaceObject3)
            image = feuread.read_image(tag)
            target = QRectF(0, 0, image.width * TWIPS, image.height * TWIPS)
            picture = self._picture(cid)
            if picture is None:
                self._placeholder(painter, target)
            else:
                painter.drawImage(target, self._through_cx(cid, picture))

    def _placeholder(self, painter: QPainter, target: QRectF) -> None:
        """A picture the game has no file for: a hatched box with a red cross, not a blank (the name is in the
        status line through ``missing``)."""
        painter.save()
        painter.fillRect(target, QBrush(QColor(128, 128, 128, 160), Qt.Dense4Pattern))
        painter.setPen(QPen(QColor(220, 60, 60, 200), 0))
        painter.drawRect(target)
        painter.drawLine(target.topLeft(), target.bottomRight())
        painter.drawLine(target.topRight(), target.bottomLeft())
        painter.restore()

    def _through_cx(self, cid: int, picture: QImage) -> QImage:
        """The picture as drawn: through the colour transform, and marked when the game paints it at run time."""
        key = (cid, self.m_cx)
        if key not in self._m_tinted:
            out = tinted(picture, self.m_cx)
            name = self._m_names.get(cid, "").lower().replace("/", "\\")
            if name.rsplit("\\", 1)[-1].startswith("rtt_"):
                kind = LIVE
            else:  # the colour scheme's light bases (``color_*``, grey 229) are what the code colours
                scheme = "\\colorscheme\\" in name or name.rsplit("\\", 1)[-1].startswith("color_")
                kind = SWATCH if _flat_white(out, 200 if scheme else 240, solid=not scheme) else None
            if kind is not None:
                self.m_unsupported.add(REASONS[kind])
                out = _hatched(out, kind)
            self._m_tinted[key] = out
        return self._m_tinted[key]

    def draw_shape(self, painter: QPainter, tag: STag) -> None:
        cid = feuread.char_id(tag)
        if cid not in self._m_shapes:
            self._m_shapes[cid] = feushape.read_shape(tag)
        shape = self._m_shapes[cid]
        painter.setPen(Qt.NoPen)
        for fill, loops in shape.fills:
            path = _path(loops)
            path.setFillRule(Qt.OddEvenFill)
            if fill.bitmap_id is not None and fill.kind in (feushape.BITMAP_CLIP, feushape.BITMAP_CLIP_HARD):
                self._draw_clipped_bitmap(painter, path, fill)
                continue
            painter.setBrush(self._brush(fill))
            painter.drawPath(path)
        painter.setBrush(Qt.NoBrush)
        for line, segments in shape.lines:
            pen = QPen(_color(tint(self.m_cx, line.color)), max(line.width, 1))
            pen.setCosmetic(line.width <= TWIPS)
            painter.setPen(pen)
            painter.drawPath(_path([segments], close=False))

    def _draw_clipped_bitmap(self, painter: QPainter, path: QPainterPath, fill: feushape.SFill) -> None:
        """A clipped bitmap fill shows the picture once, not tiled as a Qt texture brush would."""
        picture = self._picture(fill.bitmap_id)
        if picture is None:
            painter.setBrush(self._brush(fill))
            painter.drawPath(path)
            return
        painter.save()
        painter.setClipPath(path, Qt.IntersectClip)
        matrix = _qt(fill.matrix)
        painter.setTransform(matrix * painter.transform())
        image = self._through_cx(fill.bitmap_id, picture)
        inverse, is_invertible = matrix.inverted()
        want = inverse.mapRect(path.boundingRect()).toAlignedRect() if is_invertible else image.rect()
        if image.rect().contains(want) or want.width() * want.height() > 4096 * 4096:
            painter.drawImage(0, 0, image)
        else:  # Flash fills what lies beyond a clipped bitmap with its edge pixels
            outer = want.united(image.rect())
            painter.drawImage(outer.topLeft(), _edge_extended(image, outer))
        painter.restore()

    def _brush(self, fill: feushape.SFill) -> QBrush:
        if fill.kind in (feushape.LINEAR, feushape.RADIAL, feushape.FOCAL):
            if fill.kind == feushape.LINEAR:
                grad = QLinearGradient(-16384, 0, 16384, 0)
            else:
                grad = QRadialGradient(QPointF(0, 0), 16384, QPointF(fill.focal * 16384, 0))
            grad.setSpread((QLinearGradient.PadSpread, QLinearGradient.ReflectSpread, QLinearGradient.RepeatSpread)[min(fill.spread, 2)])  # fmt: skip
            for ratio, rgba in fill.stops:
                grad.setColorAt(ratio / 255, _color(tint(self.m_cx, rgba)))
            brush = QBrush(grad)
            brush.setTransform(_qt(fill.matrix))
            return brush
        if fill.bitmap_id is not None:
            picture = self._picture(fill.bitmap_id)
            if picture is None:
                return QBrush(QColor(128, 128, 128, 160), Qt.Dense4Pattern)
            brush = QBrush(self._through_cx(fill.bitmap_id, picture))
            brush.setTransform(_qt(fill.matrix))
            return brush
        return QBrush(_color(tint(self.m_cx, fill.color)))

    def _picture(self, cid: int) -> QImage | None:
        if cid not in self._m_pictures:
            tag = self.m_defs.get(cid)
            image = (
                feuread.read_image(tag)
                if tag is not None and tag.code in (feuread.IMAGE, feuread.ATLAS_IMAGE)
                else None
            )
            picture = self.m_images(image) if image is not None else None
            if image is not None and picture is None:
                self.m_missing.add(image.path)
            if image is not None:
                self._m_names[cid] = image.path
            self._m_pictures[cid] = picture
        return self._m_pictures[cid]

    def draw_text(self, painter: QPainter, edit_text: feuread.SEditText) -> None:
        if not edit_text.text:
            return
        font_ref = self.m_fonts.get(edit_text.font_id) if edit_text.font_id is not None else None
        min_x, max_x, min_y, max_y = edit_text.bounds
        box = painter.transform().mapRect(QRectF(min_x, min_y, max_x - min_x, max_y - min_y))
        scale = box.height() / max(max_y - min_y, 1)
        # drawn unscaled: Qt draws no text through a scaling transform on some platforms (the offscreen one)
        font = self.text_font(font_ref, max(round((edit_text.height or 240) * scale), 1))
        align = ALIGN.get(edit_text.layout[0] if edit_text.layout else 0, Qt.AlignLeft) | Qt.AlignTop
        if edit_text.flags & feuread.EDIT_WORD_WRAP:
            align |= Qt.TextWordWrap
        text = _plain(edit_text.text) if edit_text.flags & feuread.EDIT_HTML else edit_text.text
        painter.save()
        painter.resetTransform()
        painter.setFont(font)
        painter.setPen(_color(tint(self.m_cx, edit_text.color or (0, 0, 0, 255))))
        painter.drawText(box, int(align), text)
        painter.restore()

    def text_font(self, font_ref: feuread.SFontRef | None, pixels: int) -> QFont:
        """The font of a text: the game's face where ``fonts`` finds one, else the family the reference names."""
        if self.m_font_choice is None:
            font = QFont(font_ref.name if font_ref else "Arial")
            font.setPixelSize(pixels)
            return font
        choice = self.m_font_choice(font_ref)
        self.m_texts_in_game_font, self.m_texts_in_stand_in = (
            self.m_texts_in_game_font + choice.real,
            self.m_texts_in_stand_in + (not choice.real),
        )
        font = QFont(choice.family)
        font.setPixelSize(pixels)
        font.setWeight(QFont.Weight(choice.weight))
        return font


def _device(painter: QPainter, path: QPainterPath) -> QPainterPath:
    """A device-space path as ``setClipPath`` wants it (it reads the path through the painter's transform)."""
    return painter.transform().inverted()[0].map(path)


def _cxform(placement: feuread.SPlacement) -> feuread.SColorTransform | None:
    if not placement.flags & feuread.PLACE_HAS_CXFORM:
        return None
    return feuread.read_cxform(feuread.CBits(placement.tail, 0), has_alpha=True)


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


def render(movie: SMovie, owner: int = 0, scale: float = 1.0, images: ImageLoader | None = None,
           background: QColor | None = None, fonts=None) -> tuple[QImage, CRenderer]:  # fmt: skip
    """The first frame of the main timeline (``owner`` 0, the stage's size) or of a sprite (its own bounds)."""
    renderer = CRenderer(movie, images, fonts)
    min_x, max_x, min_y, max_y = movie.stage
    if owner:
        min_x, max_x, min_y, max_y = _bounds(renderer, owner)
    width, height = max(int((max_x - min_x) / TWIPS * scale), 1), max(int((max_y - min_y) / TWIPS * scale), 1)
    image = QImage(min(width, 8192), min(height, 8192), QImage.Format_ARGB32_Premultiplied)
    image.fill(background or QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setTransform(QTransform(scale / TWIPS, 0, 0, scale / TWIPS, -min_x * scale / TWIPS, -min_y * scale / TWIPS))
    if owner and renderer.m_defs.get(owner) is not None and renderer.m_defs[owner].code != feuread.DEFINE_SPRITE:
        renderer.draw_character(painter, owner)
    else:
        renderer.draw_timeline(painter, owner)
    painter.end()
    return image, renderer


def _bounds(renderer: CRenderer, cid: int) -> tuple[int, int, int, int]:
    """A character's box, from a painter that only measures (an empty sprite gets the stage)."""
    probe = QImage(1, 1, QImage.Format_ARGB32_Premultiplied)
    painter = _CMeasure(probe)
    renderer.draw_character(painter, cid)
    painter.end()
    box = painter.m_box
    if box is None or box.isEmpty():
        return renderer.m_movie.stage
    return int(box.left()), int(box.right()), int(box.top()), int(box.bottom())


class _CMeasure(QPainter):
    """A painter that records the box of what it draws, through its transform."""

    def __init__(self, device: QImage) -> None:
        super().__init__(device)
        self.m_box: QRectF | None = None

    def _add(self, rect: QRectF) -> None:
        mapped = self.transform().mapRect(rect)
        self.m_box = mapped if self.m_box is None else self.m_box.united(mapped)

    def drawPath(self, path: QPainterPath) -> None:  # Qt's name
        self._add(path.boundingRect())

    def drawText(self, rect, *args) -> None:  # Qt's name
        if isinstance(rect, QRectF):
            self._add(rect)

    def drawImage(self, rect, *args) -> None:  # Qt's name
        if isinstance(rect, QRectF):
            self._add(rect)
        else:  # (x, y, image) or (QPoint, image): the picture at its own size
            position = (rect.x(), rect.y()) if isinstance(rect, QPoint) else (rect, args[0])
            image = args[-1] if isinstance(rect, QPoint) else args[1]
            self._add(QRectF(position[0], position[1], image.width(), image.height()))

    def fillRect(self, rect, *args) -> None:  # Qt's name
        if isinstance(rect, QRectF):
            self._add(rect)
