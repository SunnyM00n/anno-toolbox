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
from ..formats.feuread import Matrix, Movie, Tag

TWIPS = 20
MAX_DEPTH = 24  # nested sprites drawn at most this deep (a movie can nest itself through its code)
ALIGN = {0: Qt.AlignLeft, 1: Qt.AlignRight, 2: Qt.AlignHCenter, 3: Qt.AlignJustify}

ImageLoader = Callable[[feuread.ExternalImage], QImage | None]
SAME = feuread.ColorTransform()
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


def _qt(m: Matrix) -> QTransform:
    return QTransform(m.a, m.b, m.c, m.d, m.tx, m.ty)


def _color(rgba) -> QColor:
    r, g, b, a = rgba
    return QColor(r, g, b, a)


def _flat_white(picture: QImage, floor: int = 240, solid: bool = False) -> bool:
    """Whether every visible pixel is light (all channels at least ``floor``): a base the code tints (a colour
    swatch, a gradient or a mask). ``solid`` also wants every pixel opaque, so a white icon is no swatch."""
    w, h = picture.width(), picture.height()
    if w == 0 or h == 0:
        return False
    for x, y in ((0, 0), (w - 1, h - 1), (w // 2, h // 2), (0, h - 1), (w - 1, 0)):  # a quick no first
        c = picture.pixelColor(x, y)
        if c.alpha() > 8 and min(c.red(), c.green(), c.blue()) < floor:
            return False
    premult = picture.convertToFormat(QImage.Format_RGBA8888_Premultiplied)
    raw = premult.constBits().tobytes()[: w * h * 4]
    alpha = raw[3::4]
    channels = [raw[c::4] for c in range(3)]
    if solid and min(alpha) < 250:
        return False
    return max(alpha) > 8 and all(
        min(r, g, b) >= a * floor // 255 - 12 for a, r, g, b in zip(alpha, *channels, strict=True)
    )


def _hatched(picture: QImage, kind: str) -> QImage:
    """A stand-in for a picture the game paints at run time: a swatch tinted with 2205's panel blue-grey (its own
    shading kept), a live picture dark grey with diagonal hatch lines and a cross."""
    out = QImage(picture.size(), QImage.Format_ARGB32_Premultiplied)
    out.fill(0)
    p = QPainter(out)
    p.drawImage(0, 0, picture)
    if kind == SWATCH:
        p.setCompositionMode(
            QPainter.CompositionMode_Multiply
        )  # a white base takes the tint, a gradient keeps its shade
        p.fillRect(out.rect(), TINT)
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn)  # and the picture's own shape and opacity
        p.drawImage(0, 0, picture)
        p.end()
        return out
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)  # keep the picture's own shape and opacity
    p.fillRect(out.rect(), QColor(60, 60, 70))
    p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
    p.setPen(QPen(QColor(235, 200, 90, 200), 1))
    step = max(4, min(picture.width(), picture.height()) // 4)
    for k in range(-picture.height(), picture.width(), step):
        p.drawLine(k, picture.height(), k + picture.height(), 0)
    if kind == LIVE:
        p.drawLine(0, 0, picture.width(), picture.height())
        p.drawLine(picture.width(), 0, 0, picture.height())
    p.end()
    return out


def _edge_extended(picture: QImage, outer: QRect) -> QImage:
    """The picture on a canvas of ``outer`` (pixel space, containing it), the margins filled with its edge pixels."""
    w, h = picture.width(), picture.height()
    out = QImage(outer.size(), QImage.Format_ARGB32_Premultiplied)
    out.fill(0)
    p = QPainter(out)
    ox, oy = -outer.left(), -outer.top()  # where the picture's origin sits on the canvas
    xs = ((0, 0, ox), (w - 1, ox + w, out.width() - ox - w))  # source column, first target column, width
    ys = ((0, 0, oy), (h - 1, oy + h, out.height() - oy - h))
    for sx, tx, tw in xs:
        for sy, ty, th in ys:  # the four corners
            if tw > 0 and th > 0:
                p.drawImage(QRect(tx, ty, tw, th), picture, QRect(sx, sy, 1, 1))
        if tw > 0:
            p.drawImage(QRect(tx, oy, tw, h), picture, QRect(sx, 0, 1, h))
    for sy, ty, th in ys:
        if th > 0:
            p.drawImage(QRect(ox, ty, w, th), picture, QRect(0, sy, w, 1))
    p.drawImage(ox, oy, picture)
    p.end()
    return out


def _clamp(v: float) -> int:
    return max(0, min(255, int(v)))


def compose(outer: feuread.ColorTransform, inner: feuread.ColorTransform) -> feuread.ColorTransform:
    """The transform of ``inner`` (a placement) seen through ``outer`` (the sprites above it); the alpha
    multiplier is left to the painter's opacity, so it is 1 here."""
    mult = tuple(o * i for o, i in zip(outer.mult[:3], inner.mult[:3], strict=True))
    add = tuple(round(o * a + b) for o, a, b in zip(outer.mult[:3], inner.add[:3], outer.add[:3], strict=True))
    return feuread.ColorTransform((*mult, 1.0), (*add, outer.add[3] + inner.add[3]))


def tint(cx: feuread.ColorTransform, rgba) -> tuple[int, int, int, int]:
    """A colour through a colour transform (alpha multiplier excluded: see ``compose``)."""
    r, g, b, a = rgba
    return (
        _clamp(r * cx.mult[0] + cx.add[0]), _clamp(g * cx.mult[1] + cx.add[1]), _clamp(b * cx.mult[2] + cx.add[2]),
        _clamp(a + cx.add[3]),
    )  # fmt: skip


def tinted(picture: QImage, cx: feuread.ColorTransform) -> QImage:
    """A picture through a colour transform, per channel with lookup tables (straight alpha, as Flash does)."""
    if cx == SAME:
        return picture
    src = picture.convertToFormat(QImage.Format_RGBA8888)
    raw = bytearray(src.constBits().tobytes()[: src.width() * src.height() * 4]) if src.width() else bytearray()
    for c in range(4):
        table = bytes(_clamp(v * (cx.mult[c] if c < 3 else 1.0) + cx.add[c]) for v in range(256))
        raw[c::4] = bytes(raw[c::4]).translate(table)
    out = QImage(bytes(raw), src.width(), src.height(), src.width() * 4, QImage.Format_RGBA8888)
    return out.copy()


class Renderer:
    """Draws characters of one movie; ``images`` gives the picture of an image tag (None: a grey stand-in)."""

    def __init__(self, movie: Movie, images: ImageLoader | None = None, fonts=None) -> None:
        self.movie, self.images = movie, images or (lambda _img: None)
        self.font_choice = fonts  # ``Fonts2205.choose``: the game's face for a font reference (None: the family's name)
        self.texts_in_game_font = self.texts_in_stand_in = 0  # the texts drawn so far, by what they drew with
        self.defs = feuread.definitions(movie)
        self.fonts = {f.font_id: f for t in movie.tags if t.code == feuread.FONT for f in [feuread.read_font(t)]}
        self._shapes: dict[int, feushape.Shape] = {}
        self._pictures: dict[int, QImage | None] = {}
        self._tinted: dict[tuple, QImage] = {}
        self._names: dict[int, str] = {}  # image character id -> the path the movie names
        self.cx = SAME  # the colour transform of the placements above what is drawn now
        self.missing: set[str] = set()  # image paths that had no picture
        self.unsupported: set[str] = set()  # what the preview cannot draw, as reasons for the status line

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
                                          p.matrix or old.matrix, old.name, p.head, p.matrix_raw, p.tail,
                                          p.clip_depth, p.blend)  # fmt: skip
                depths[p.depth] = p
        return sorted(depths.items())

    def draw_timeline(self, painter: QPainter, owner: int, level: int = 0) -> None:
        masks: list[tuple[int, QPainterPath]] = []  # (last depth masked, the mask in device space)
        for depth, p in self.display_list(self.frame_tags(owner)):
            masks = [m for m in masks if m[0] >= depth]
            if p.char_id is None:
                continue
            if p.flags & feuread.PLACE_HAS_CLIP_DEPTH:  # a mask: not drawn itself, it clips the depths above it
                masks.append((p.clip_depth, self._mask_path(p, painter.transform(), level)))
                continue
            painter.save()
            for _, path in masks:
                painter.setClipPath(_device(painter, path), Qt.IntersectClip)
            if p.matrix is not None:
                painter.setTransform(_qt(p.matrix) * painter.transform())
            before = self.cx
            cx = _cxform(p)
            if cx is not None:
                painter.setOpacity(painter.opacity() * max(0.0, min(1.0, cx.mult[3])))
                self.cx = compose(self.cx, cx)
            self._blend(painter, p)
            self.draw_character(painter, p.char_id, level)
            self.cx = before
            painter.restore()

    def _blend(self, painter: QPainter, p: feuread.Placement) -> None:
        if p.blend in BLEND:
            painter.setCompositionMode(BLEND[p.blend])
        elif p.blend > 2:
            self.unsupported.add(f"blend mode {p.blend}")
        if p.flags & feuread.PLACE3_HAS_FILTERS:
            self.unsupported.add("filters (the game draws them on the GPU)")

    def _mask_path(self, p: feuread.Placement, base: QTransform, level: int) -> QPainterPath:
        """The area a mask placement covers, in device space (``base`` is the timeline's transform)."""
        matrix = _qt(p.matrix) * base if p.matrix is not None else base
        return matrix.map(self._outline(p.char_id, level))

    def _outline(self, cid: int, level: int) -> QPainterPath:
        """The outline of every shape of a character (nested sprites at their placements), in its own space."""
        tag = self.defs.get(cid)
        path = QPainterPath()
        if tag is None or level >= MAX_DEPTH:
            return path
        if tag.code in feuread.SHAPES:
            if cid not in self._shapes:
                self._shapes[cid] = feushape.read_shape(tag)
            for _, loops in self._shapes[cid].fills:
                part = _path(loops)
                part.setFillRule(Qt.OddEvenFill)
                path.addPath(part)
        elif tag.code == feuread.DEFINE_SPRITE:
            for _, q in self.display_list(self.frame_tags(cid)):
                if q.char_id is not None and not q.flags & feuread.PLACE_HAS_CLIP_DEPTH:
                    inner = self._outline(q.char_id, level + 1)
                    path.addPath((_qt(q.matrix) if q.matrix is not None else QTransform()).map(inner))
        elif tag.code in (feuread.IMAGE, feuread.ATLAS_IMAGE):
            img = feuread.read_image(tag)
            path.addRect(QRectF(0, 0, img.width * TWIPS, img.height * TWIPS))
        return path

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
        key = (cid, self.cx)
        if key not in self._tinted:
            out = tinted(picture, self.cx)
            name = self._names.get(cid, "").lower().replace("/", "\\")
            if name.rsplit("\\", 1)[-1].startswith("rtt_"):
                kind = LIVE
            else:  # the colour scheme's light bases (``color_*``, grey 229) are what the code colours
                scheme = "\\colorscheme\\" in name or name.rsplit("\\", 1)[-1].startswith("color_")
                kind = SWATCH if _flat_white(out, 200 if scheme else 240, solid=not scheme) else None
            if kind is not None:
                self.unsupported.add(REASONS[kind])
                out = _hatched(out, kind)
            self._tinted[key] = out
        return self._tinted[key]

    def draw_shape(self, painter: QPainter, tag: Tag) -> None:
        cid = feuread.char_id(tag)
        if cid not in self._shapes:
            self._shapes[cid] = feushape.read_shape(tag)
        shape = self._shapes[cid]
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
            pen = QPen(_color(tint(self.cx, line.color)), max(line.width, 1))
            pen.setCosmetic(line.width <= TWIPS)
            painter.setPen(pen)
            painter.drawPath(_path([segments], close=False))

    def _draw_clipped_bitmap(self, painter: QPainter, path: QPainterPath, fill: feushape.Fill) -> None:
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
        inverse, ok = matrix.inverted()
        want = inverse.mapRect(path.boundingRect()).toAlignedRect() if ok else image.rect()
        if image.rect().contains(want) or want.width() * want.height() > 4096 * 4096:
            painter.drawImage(0, 0, image)
        else:  # Flash fills what lies beyond a clipped bitmap with its edge pixels
            outer = want.united(image.rect())
            painter.drawImage(outer.topLeft(), _edge_extended(image, outer))
        painter.restore()

    def _brush(self, fill: feushape.Fill) -> QBrush:
        if fill.kind in (feushape.LINEAR, feushape.RADIAL, feushape.FOCAL):
            if fill.kind == feushape.LINEAR:
                grad = QLinearGradient(-16384, 0, 16384, 0)
            else:
                grad = QRadialGradient(QPointF(0, 0), 16384, QPointF(fill.focal * 16384, 0))
            grad.setSpread((QLinearGradient.PadSpread, QLinearGradient.ReflectSpread, QLinearGradient.RepeatSpread)[min(fill.spread, 2)])  # fmt: skip
            for ratio, rgba in fill.stops:
                grad.setColorAt(ratio / 255, _color(tint(self.cx, rgba)))
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
        return QBrush(_color(tint(self.cx, fill.color)))

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
            if img is not None:
                self._names[cid] = img.path
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
        font = self.text_font(font_ref, max(round((t.height or 240) * scale), 1))
        align = ALIGN.get(t.layout[0] if t.layout else 0, Qt.AlignLeft) | Qt.AlignTop
        if t.flags & feuread.EDIT_WORD_WRAP:
            align |= Qt.TextWordWrap
        text = _plain(t.text) if t.flags & feuread.EDIT_HTML else t.text
        painter.save()
        painter.resetTransform()
        painter.setFont(font)
        painter.setPen(_color(tint(self.cx, t.color or (0, 0, 0, 255))))
        painter.drawText(box, int(align), text)
        painter.restore()

    def text_font(self, font_ref: feuread.FontRef | None, pixels: int) -> QFont:
        """The font of a text: the game's face where ``fonts`` finds one, else the family the reference names."""
        if self.font_choice is None:
            font = QFont(font_ref.name if font_ref else "Arial")
            font.setPixelSize(pixels)
            return font
        choice = self.font_choice(font_ref)
        self.texts_in_game_font, self.texts_in_stand_in = (
            self.texts_in_game_font + choice.real,
            self.texts_in_stand_in + (not choice.real),
        )
        font = QFont(choice.family)
        font.setPixelSize(pixels)
        font.setWeight(QFont.Weight(choice.weight))
        return font


def _device(painter: QPainter, path: QPainterPath) -> QPainterPath:
    """A device-space path as ``setClipPath`` wants it (it reads the path through the painter's transform)."""
    return painter.transform().inverted()[0].map(path)


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
           background: QColor | None = None, fonts=None) -> tuple[QImage, Renderer]:  # fmt: skip
    """The first frame of the main timeline (``owner`` 0, the stage's size) or of a sprite (its own bounds)."""
    r = Renderer(movie, images, fonts)
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
        else:  # (x, y, image) or (QPoint, image): the picture at its own size
            at = (rect.x(), rect.y()) if isinstance(rect, QPoint) else (rect, args[0])
            image = args[-1] if isinstance(rect, QPoint) else args[1]
            self._add(QRectF(at[0], at[1], image.width(), image.height()))

    def fillRect(self, rect, *args) -> None:  # Qt's name
        if isinstance(rect, QRectF):
            self._add(rect)
