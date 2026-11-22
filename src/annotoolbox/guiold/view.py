"""The old interface in a window page (Qt): a list of the game's dialogs, templates and backgrounds, the dialog drawn
with its backgrounds and texts, and an inspector for the widget under the cursor. Read only.

``COldGuiPage(tree)`` is the widget; ``COldGuiPage.open(path)`` shows a file of the game. The placing is ``scene``, the files
``files``; docs/ui/old-gui.md says what the preview draws and what it leaves out.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPen,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextFrame,
    QTextTable,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QLineEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import tr
from ..models import ddsdecode
from ..suite import gamefonts
from ..suite.background import CBackground
from . import files as oldfiles
from . import richtext, scene

ROLE = Qt.ItemDataRole.UserRole
STAGE = QColor(34, 36, 40)
ICON, PICTURE, BOX = "icon:", "img:", "box:"  # resource names in a text: an icon, a loose picture, a drawn box
BOX_SCALE = 2.0  # a box is drawn at twice the reference size: the page scales it to the window


def cut(image: QImage, tile: oldfiles.STile) -> QRectF | None:
    """The rectangle of ``image`` a tile shows: its own, or (a ``.bg2`` icon) the cell its number names."""
    if tile.w <= 0 or tile.h <= 0:
        return None
    if tile.y >= 0:
        return QRectF(tile.x, tile.y, tile.w, tile.h)
    per_row = max(1, image.width() // tile.w)
    return QRectF((tile.x % per_row) * tile.w, (tile.x // per_row) * tile.h, tile.w, tile.h)


class CAtlases:
    """The pictures the backgrounds cut their tiles from, decoded once (``missing`` the names no file was found for)."""

    def __init__(self, tree) -> None:
        self.m_tree, self._m_cache, self.m_missing = tree, {}, set()
        self._m_tinted: dict[tuple, QImage] = {}

    def tinted(self, path: str, factors: tuple[float, float, float] | None) -> QImage | None:
        """The picture multiplied by ``factors`` (kept per factors; its alpha as it is)."""
        image = self.get(path)
        if image is None or factors is None:
            return image
        key = (oldfiles.stem(path), factors)
        if key not in self._m_tinted:
            out = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            painter = QPainter(out)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            painter.fillRect(out.rect(), QColor(*(max(0, min(255, round(factor * 255))) for factor in factors)))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawImage(0, 0, image)
            painter.end()
            self._m_tinted[key] = out
        return self._m_tinted[key]

    def get(self, path: str) -> QImage | None:
        stem = oldfiles.stem(path)
        if stem not in self._m_cache:
            self._m_cache[stem] = self._load(stem) if stem else None
            if self._m_cache[stem] is None and stem:
                self.m_missing.add(path)
        return self._m_cache[stem]

    def _load(self, stem: str) -> QImage | None:
        for candidate in (f"{stem}_0.dds", f"{stem}.dds", f"{stem}.tga", f"{stem}.png", f"{stem}.jpg"):
            if not self.m_tree.exists(candidate):
                continue
            data = self.m_tree.read(candidate)
            if candidate.endswith(".dds"):
                try:
                    rgba = ddsdecode.CDdsFile(data).level(0)
                except (ddsdecode.CUnsupportedDds, ValueError, IndexError):
                    continue
                height, width = rgba.shape[:2]
                return QImage(rgba.tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
            image = QImage.fromData(data)
            if not image.isNull():
                return image
        return None


_PADS = tuple(getattr(QTextFormat.Property, f"TableCell{s}Padding") for s in ("Left", "Top", "Right", "Bottom"))


def _tables(frame: QTextFrame):
    """The tables under ``frame`` in the order Qt lays them out (each before the ones its cells hold)."""
    for child in frame.childFrames():
        if isinstance(child, QTextTable):
            yield child
        yield from _tables(child)


def _cell_blocks(doc: QTextDocument, table: QTextTable, cell) -> list[QRectF]:
    """The places of the blocks a cell holds itself (not those of a table inside it)."""
    out, block = [], doc.findBlock(cell.firstPosition())
    while block.isValid() and block.position() <= cell.lastPosition():
        if QTextCursor(block).currentFrame() is table:
            out.append(doc.documentLayout().blockBoundingRect(block))
        block = block.next()
    return out


def table_rects(doc: QTextDocument, table: QTextTable) -> tuple[QRectF, dict[tuple[int, int], QRectF]]:
    """Where Qt laid out a table (its box inside its margins) and each cell (by its row and column, the rectangle its
    own colour fills): a cell's column from its blocks less its padding, a row from its top, the table round its first
    row and column (docs/ui/old-gui.md, "Tables and cells on background pictures")."""
    table_format = table.format()
    spacing, edge = table_format.cellSpacing(), table_format.border() + table_format.padding()
    cells, lefts, tops = {}, {}, {}
    for row in range(table.rows()):
        for column in range(table.columns()):
            cell = table.cellAt(row, column)
            rects = _cell_blocks(doc, table, cell) if cell.row() == row and cell.column() == column else []
            if not rects:
                continue
            own = cell.format().toTableCellFormat()  # a cell's own padding, else the table's
            left = own.leftPadding() if own.hasProperty(_PADS[0]) else table_format.cellPadding()
            top = own.topPadding() if own.hasProperty(_PADS[1]) else table_format.cellPadding()
            cells[(row, column)] = cell
            lefts[column] = min(lefts.get(column, 1e9), min(rect.x() for rect in rects) - left)
            tops[row] = min(tops.get(row, 1e9), min(rect.y() for rect in rects) - top)
    if 0 not in lefts or 0 not in tops:
        return QRectF(), {}
    size = doc.documentLayout().frameBoundingRect(table).size()
    box = QRectF(lefts[0] - spacing - edge, tops[0] - spacing - edge, size.width() - table_format.leftMargin() - table_format.rightMargin(),
                 size.height() - table_format.topMargin() - table_format.bottomMargin())  # fmt: skip
    after = {"right": box.right() - edge - spacing, "bottom": box.bottom() - edge - spacing}
    out = {}
    for (row, column), cell in cells.items():
        end_column, end_row = column + cell.columnSpan(), row + cell.rowSpan()
        right = lefts[end_column] - spacing if end_column in lefts else after["right"]
        bottom = tops[end_row] - spacing if end_row in tops else after["bottom"]
        out[(row, column)] = QRectF(lefts[column], tops[row], right - lefts[column], bottom - tops[row])
    return box, out


def table_backdrops(doc: QTextDocument, frames: list[richtext.Frame]) -> list[tuple[QRectF, str]]:
    """The background pictures of a laid-out document's tables and cells (``richtext.to_html``'s ``frames``) with
    where they go: a table's over its padding, a cell's over the cell; none where Qt made other tables or cells."""
    tables = list(_tables(doc.rootFrame()))
    if len(tables) != len(frames) or not any(backdrop or any(cells) for backdrop, _pad, cells in frames):
        return []
    out = []
    for table, (picture, (top, right, bottom, left), pictures) in zip(tables, frames, strict=True):
        box, cells = table_rects(doc, table)
        if picture and not box.isEmpty():
            out.append((box.adjusted(-left, -top, right, bottom), picture))
        if len(cells) == len(pictures):
            out += [(cells[k], backdrop) for k, backdrop in zip(sorted(cells), pictures, strict=True) if backdrop]
    return out


def in_class(font: QFont, style: richtext.SStyle | None) -> QFont:
    """``font`` with what a class of the style sheet sets of it: the size, the weight, the slant."""
    out = QFont(font)
    if style is not None:
        if style.size:
            out.setPixelSize(max(6, round(style.size)))
        if style.bold is not None:
            out.setBold(style.bold)
        if style.italic is not None:
            out.setItalic(style.italic)
    return out


class CStage(QWidget):
    """Draws the open dialog scaled to fit; a click picks the widget under it."""

    picked = Signal(object)

    def __init__(self, atlases: CAtlases) -> None:
        super().__init__()
        self.m_atlases, self.m_root, self.m_selected = atlases, None, None
        self.m_faces: dict[str, str] = {}  # a font file of the game -> the family it was loaded as (``COldGuiPage``)
        self.m_states: list[tuple[str, oldfiles.SLook]] = []  # a background shown on its own: its states
        self.m_screen = scene.REFERENCE  # the visible space the dialog was placed in (``scene.SCREENS``)
        self.m_files: oldfiles.COldGui | None = None  # for the icons inside texts
        self._m_laid: dict[int, QTextDocument] = {}  # a placed widget's rich text, laid out (``show``)
        self._m_boxes = 0  # the boxes of the texts drawn so far (``_box``: each a resource of its own)
        self.setMinimumSize(320, 256)

    def set_root(self, root: scene.SNode | None) -> None:
        """Draw a placed dialog (its rich texts are laid out again with their first paint)."""
        self.m_root, self._m_laid = root, {}

    def _scale(self) -> tuple[float, float, float]:
        width, height = self.m_screen
        zoom = min(self.width() / width, self.height() / height)
        return zoom, (self.width() - width * zoom) / 2, (self.height() - height * zoom) / 2

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window())
        zoom, offset_x, offset_y = self._scale()
        painter.translate(offset_x, offset_y)
        painter.scale(zoom, zoom)
        painter.fillRect(QRectF(0, 0, *self.m_screen), STAGE)
        if self.m_states:
            self._draw_states(painter)
        elif self.m_root is not None:
            self._draw(painter, self.m_root)
        painter.end()

    def _draw_states(self, painter: QPainter) -> None:
        """One cell per state of a background, each filled at 360 x 200 (the state's own size when smaller)."""
        for i, (name, look) in enumerate(self.m_states):
            x, y = 20 + (i % 3) * 400, 20 + (i // 3) * 240
            self._fill(painter, look, QRectF(x, y, 360, 200))
            painter.setPen(QColor(200, 200, 200))
            painter.drawText(QPointF(x, y + 216), name)

    def _fill(self, painter: QPainter, look: oldfiles.SLook | None, box: QRectF, tint=None) -> None:
        if look is None:
            return
        for tile, (target_x, target_y, target_width, target_height) in scene.tile_rects(
            look, box.width(), box.height()
        ):
            self._tile(painter, tile, QRectF(box.x() + target_x, box.y() + target_y, target_width, target_height), tint)

    def _tile(self, painter: QPainter, tile: oldfiles.STile, target: QRectF, tint=None) -> None:
        image = self.m_atlases.tinted(tile.atlas, tint) if tile.atlas else None
        source_rect = cut(image, tile) if image is not None else None
        if image is not None and source_rect is not None and target.width() > 0 and target.height() > 0:
            painter.drawImage(target, image, source_rect)

    def _draw(self, painter: QPainter, node: scene.SNode) -> None:
        if not node.visible:
            return
        x, y, width, height = node.rect
        box = QRectF(x, y, width, height)
        self._fill(painter, node.look, box, node.tint)
        if node.icon is not None and node.icon_rect is not None:
            icon_tint = node.look.icon_tint if node.look is not None else None
            icon_tint = (
                oldfiles.hsv_rgb(*icon_tint[:3]) if icon_tint and tuple(icon_tint) != oldfiles.UNCHANGED else None
            )
            self._tile(painter, node.icon, QRectF(*node.icon_rect), icon_tint)
        if node.text:
            self._text(painter, node, box)
        pen = QPen(QColor(255, 160, 40) if node is self.m_selected else QColor(110, 150, 190, 90))  # every outline
        pen.setCosmetic(True)
        pen.setWidthF(2.0 if node is self.m_selected else 1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(box)
        clips = node.kind in scene.CLIPS
        if clips:  # a scroll box shows what it holds inside its client area, at the scroll position 0
            painter.save()
            painter.setClipRect(QRectF(*scene.client(node)), Qt.ClipOperation.IntersectClip)
        for child in node.children:
            self._draw(painter, child)
        if clips:
            painter.restore()

    def _text(self, painter: QPainter, node: scene.SNode, box: QRectF) -> None:
        """A widget's text in its font, class and colour, aligned as ``ALIGNMENT_HORIZONTAL`` says; markup as rich
        text with its tables, styled spans and icons."""
        font = self.qfont(node.font, node.style, box.height())
        style_colour = node.style.color if node.style is not None else None
        colour = style_colour or node.text_color or (235, 235, 220)
        align = int(oldfiles.number(scene.text_setting(node.props).get("ALIGNMENT_HORIZONTAL"), 0))
        flag = [Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignHCenter, Qt.AlignmentFlag.AlignRight][
            min(max(align, 0), 2)
        ]
        if "<" not in node.text:
            painter.setFont(font)
            painter.setPen(QColor(*colour))
            painter.drawText(box, int(flag | Qt.AlignmentFlag.AlignVCenter), node.text)
            return
        laid = self._m_laid.get(id(node))
        if laid is None:  # laid out once per placed widget: a paint only draws it
            doc, frames = QTextDocument(), []
            doc.setDefaultFont(font)
            doc.setDocumentMargin(0)
            doc.setTextWidth(max(1.0, box.width()))
            body = self._markup(doc, node.text, (font, colour, box.width()), node.around, frames)
            align_word = ("left", "center", "right")[[Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignHCenter,
                                                       Qt.AlignmentFlag.AlignRight].index(flag)]  # fmt: skip
            rgb = "#%02x%02x%02x" % tuple(colour)
            doc.setHtml(f'<div align="{align_word}" style="color:{rgb}">{body}</div>')  # i18n: ok (the game's text)
            laid = self._m_laid[id(node)] = (doc, table_backdrops(doc, frames))
        doc, backdrops = laid
        painter.save()
        painter.translate(box.x(), box.y() + max(0.0, (box.height() - doc.size().height()) / 2))
        self._contents(painter, doc, backdrops)
        painter.restore()

    def _contents(self, painter: QPainter, doc: QTextDocument, backdrops: list, clip: QRectF | None = None) -> None:
        """A laid-out document, its tables' and cells' background pictures behind it."""
        for rect, picture in backdrops if self.m_files is not None else ():
            self._fill(painter, scene.rest_look(self.m_files, picture), rect)
        doc.drawContents(painter, clip) if clip is not None else doc.drawContents(painter)

    def _markup(
        self, doc: QTextDocument, text: str, around: tuple, path: tuple = (), frames: list | None = None
    ) -> str:
        """A text's markup as rich text for ``doc``, its icons, pictures and boxes held by the document; ``around``:
        (font, colour, width) of the text around it, ``path`` the elements it sits in, ``frames`` gets its tables."""
        line = float(around[0].pixelSize())
        sheet = self.m_files.sheet if self.m_files else richtext.CStyleSheet()
        return richtext.to_html(text, sheet, lambda attrs, style: self._icon(doc, attrs, style, line),
                                lambda style, inner, at: self._box(doc, style, inner, around, at), frames, path)  # fmt: skip

    def _box(self, doc: QTextDocument, style: richtext.SStyle, inner: str, around: tuple, path: tuple = ()):
        """An inline block or a box on a background picture as a picture ``doc`` holds: its markup laid out at its
        width (its own, a share of the width ``around`` it, a block's the width around less its margins, else what it
        holds), on its background, padded; ``path``: the elements it sits in, itself last."""
        font, colour, outer = around
        top, right, bottom, left = style.padding
        known = style.width or (style.share * outer if style.share else None)
        if known is None and style.block:
            known = outer - style.margin[1] - style.margin[3]
        box, frames, colour = QTextDocument(), [], style.color or colour  # what it holds takes its colour
        box.setDocumentMargin(0)
        box.setDefaultFont(in_class(font, style))
        css = ";".join(x for x in ("color:#%02x%02x%02x" % tuple(colour), f"text-align:{style.align}" if style.align
                                   else "") if x)  # fmt: skip
        markup = self._markup(box, inner, (box.defaultFont(), colour, (known or outer) - left - right), path, frames)
        box.setHtml(f'<div style="{css}">{markup}</div>')  # i18n: ok (the game's text)
        width = known or min(outer, box.idealWidth() + left + right)
        box.setTextWidth(max(1.0, width - left - right))
        content = box.size().height() + top + bottom if inner else 1.0  # an <hr> holds nothing: a line
        height = style.height or content
        image = QImage(max(1, round(width * BOX_SCALE)), max(1, round(height * BOX_SCALE)),
                       QImage.Format.Format_ARGB32_Premultiplied)  # fmt: skip
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.scale(BOX_SCALE, BOX_SCALE)
        if style.picture and self.m_files is not None:
            self._fill(painter, scene.rest_look(self.m_files, style.picture), QRectF(0, 0, width, height))
        painter.translate(left, top)
        self._contents(
            painter, box, table_backdrops(box, frames), QRectF(0, 0, width - left - right, height - top - bottom)
        )
        painter.end()
        self._m_boxes += 1
        name = f"{BOX}{self._m_boxes}"
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image)
        return name, width, height

    def _icon(self, doc: QTextDocument, attrs: dict, style, line: float):
        """An ``<icon>`` of a text as a picture the document holds: its atlas cell, at the line's height for
        ``type="autosize"``, else at the class's size, else at its own; an ``<img>`` the loose picture it names."""
        if "src" in attrs:
            return self._picture(doc, attrs, style)
        tile = self.m_files.icon(attrs.get("guid")) if self.m_files is not None else None
        image = self.m_atlases.get(tile.atlas) if tile is not None else None
        source_rect = cut(image, tile) if image is not None else None
        if source_rect is None:
            return None
        name = f"{ICON}{attrs.get('guid')}"
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image.copy(source_rect.toRect()))
        if attrs.get("type") == "autosize":
            return name, line, line
        width, height = (style.width, style.height) if style is not None else (None, None)
        if width or height:  # one of them alone keeps the picture's aspect
            return name, width or height * tile.w / max(1, tile.h), height or width * tile.h / max(1, tile.w)
        return name, float(tile.w), float(tile.h)

    def _picture(self, doc: QTextDocument, attrs: dict, style):
        """An ``<img>``'s loose picture, at its own ``width`` / ``height``, else the class's, else its size."""
        source = attrs.get("src", "")
        image = self.m_atlases.get(source) if source and "[" not in source else None
        if image is None:
            return None
        name = f"{PICTURE}{source}"
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image)
        width, height = oldfiles.number(attrs.get("width"), 0.0), oldfiles.number(attrs.get("height"), 0.0)
        if not (width and height) and style is not None and style.width and style.height:
            width, height = style.width, style.height
        return name, width or float(image.width()), height or float(image.height())

    def qfont(
        self, font: oldfiles.SFont | None, style: richtext.SStyle | None = None, height_px: float = 24.0
    ) -> QFont:
        """The game's font (its face loaded from the file the game ships), else a stand-in sized to the widget; a
        class of the style sheet over it where it sets the size, weight or slant."""
        out = QFont(self.font())
        if font is None:
            out.setPixelSize(max(10, min(24, int(height_px * 0.6) or 14)))
        else:
            family = self.m_faces.get(font.file) if font.file else None
            out.setFamily(family or font.family)
            out.setPixelSize(max(6, round(font.size)))
            out.setWeight(QFont.Weight(max(100, min(900, font.weight))))
        return in_class(out, style)

    def measure(self, text: str, font: oldfiles.SFont | None, width_px: float, style=None) -> tuple[float, float]:
        """A text's size in its font and class, as ``_text`` draws it (``scene.Measure``), wrapped at ``width`` when
        that is not 0 (any thread)."""
        metrics = QFontMetricsF(self.qfont(font, style))
        flags = int(Qt.TextFlag.TextWordWrap) if width_px > 0 else 0
        bounds = metrics.boundingRect(QRectF(0, 0, width_px if width_px > 0 else 1e6, 1e6), flags, richtext.plain(text))
        return bounds.width(), bounds.height()

    def mousePressEvent(self, event) -> None:
        if self.m_root is None:
            return
        zoom, offset_x, offset_y = self._scale()
        position = event.position()
        self.picked.emit(self.m_root.at((position.x() - offset_x) / zoom, (position.y() - offset_y) / zoom))


class COldGuiPage(QWidget):
    """The old interface of one game: files on the left, the drawing in the middle, the inspector on the right."""

    def __init__(self, tree) -> None:
        super().__init__()
        self.m_files, self.m_atlases = oldfiles.COldGui(tree), CAtlases(tree)
        self.m_root: scene.SNode | None = None
        self.m_background = CBackground(self)
        self.m_sync_open = False  # read a file on this thread (tests)
        self._m_font_data: dict[str, bytes | None] = {}  # font files read off the window's thread, to be loaded on it
        self._m_wanted: tuple | None = None  # (file, state, screen) asked for last: an older one is not shown
        self.m_state = QComboBox()
        self.m_state.addItems(scene.STATES)
        self.m_state.setToolTip(tr("ui.tip.state_widgets_drawn_where_background"))
        self.m_screen = QComboBox()
        self.m_screen.addItems(list(scene.SCREENS))
        self.m_screen.setToolTip(tr("ui.tip.screens_aspect_dialog_placed_visible"))
        self.m_find = QLineEdit(placeholderText=tr("ui.find_dialog_template_background"))
        self.m_list = QTreeWidget()
        self.m_list.setHeaderLabels([tr("common.file"), tr("ui.kind")])
        head = self.m_list.header()  # the file name takes the width: at the default 100 px every name read "mai..."
        head.setStretchLastSection(False)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.m_stage = CStage(self.m_atlases)
        self.m_stage.m_files = self.m_files
        self.m_inspector = QTreeWidget()
        self.m_inspector.setHeaderLabels([tr("ui.property"), tr("common.value")])
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.m_find)
        lay.addWidget(self.m_state)
        lay.addWidget(self.m_screen)
        lay.addWidget(self.m_list)
        split = QSplitter(self)
        for panel in (left, self.m_stage, self.m_inspector):
            split.addWidget(panel)
        split.setSizes([300, 760, 380])
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(split)
        self.m_find.textChanged.connect(self.fill)
        self.m_list.currentItemChanged.connect(lambda item, _old: self.open(item.data(0, ROLE)) if item else None)
        self.m_stage.picked.connect(self.inspect)
        for choice in (self.m_state, self.m_screen):
            choice.currentTextChanged.connect(lambda _s: self.open(self._m_wanted[0] if self._m_wanted else None))
        self.fill()

    def fill(self) -> None:
        self.m_list.clear()
        words = self.m_find.text().lower().split()
        groups = {"dialog": tr("ui.dialogs"), "template": tr("assets.templates"), "background": tr("ui.backgrounds")}
        tops = {k: QTreeWidgetItem(self.m_list, [text, ""]) for k, text in groups.items()}
        for path, entry in sorted(self.m_files.m_entries.items()):
            if "__custom" in path or (words and not all(word in path for word in words)):
                continue
            item = QTreeWidgetItem(tops[entry.kind], [path.rsplit("/", 1)[-1], entry.kind])
            item.setToolTip(0, path)
            item.setData(0, ROLE, path)
        for kind, top in tops.items():
            top.setText(0, f"{groups[kind]} ({top.childCount()})")
        tops["dialog"].setExpanded(True)

    def open(self, path: str | None) -> None:
        """Show a file: a dialog drawn, a background's states, a template's properties. The file, the templates and the
        pictures are read off the window's thread (the first dialog reads every template of the game)."""
        entry = self.m_files.m_entries.get(path or "")
        if entry is None:
            return
        screen = scene.SCREENS.get(self.m_screen.currentText(), scene.REFERENCE)
        wanted = self._m_wanted = (entry.path, self.m_state.currentText(), screen)  # read here: the window thread's
        self.m_background.run(
            lambda: self._read(entry, *wanted[1:]), lambda result: self._opened(entry, result, wanted), self.m_sync_open
        )

    def _read(self, entry: oldfiles.SEntry, state: str, screen=scene.REFERENCE):
        """What showing ``entry`` in ``state`` on ``screen`` needs, with every picture its backgrounds, icons and the
        icons inside its texts cut from decoded."""
        if entry.kind == "dialog":
            shown = scene.build(self.m_files, self.m_files.dialog(entry.path), state, screen, self.m_stage.measure)
            looks = [scene_node.look for scene_node in shown.walk() if scene_node.look is not None]
            for scene_node in shown.walk():
                if (
                    scene_node.font is not None
                    and scene_node.font.file
                    and scene_node.font.file not in self.m_stage.m_faces
                ):
                    self._m_font_data.setdefault(scene_node.font.file, self.m_files.font_file(scene_node.font))
                tiles = [scene_node.icon] + [self.m_files.icon(guid) for guid in richtext.icon_guids(scene_node.text)]
                for picture in [
                    icon_tile.atlas for icon_tile in tiles if icon_tile is not None
                ] + richtext.picture_paths(scene_node.text):
                    self.m_atlases.get(picture)
                pictures = richtext.box_pictures(scene_node.text, self.m_files.sheet, scene_node.around)
                boxes = (scene.rest_look(self.m_files, background) for background in pictures)
                looks += [look for look in boxes if look is not None]  # the backgrounds of its boxes
        elif entry.kind == "background":
            shown = list(self.m_files.background(entry.path).items())
            looks = [look for _, look in shown]
        else:
            return self._template_rows(entry.path)
        for tile in {icon_tile for look in looks for icon_tile in look.tiles if icon_tile.atlas}:
            self.m_atlases.get(tile.atlas)
        return shown

    def _opened(self, entry: oldfiles.SEntry, result, wanted: tuple) -> None:
        if wanted != self._m_wanted:  # another file or state was asked for meanwhile (and may have been read sooner)
            return
        self.m_stage.m_states, self.m_stage.m_selected = [], None
        self.m_stage.set_root(None)
        if isinstance(result, Exception):
            self.m_root = None
            self._fill_inspector([(tr("ui.error"), f"{type(result).__name__}: {result}")])
        elif entry.kind == "dialog":
            self.m_root = result
            self.m_stage.set_root(result)
            self.m_stage.m_screen = wanted[2]
            if self._load_faces(result):  # the texts were measured in a stand-in face: once more in the game's
                self.open(entry.path)
            self.inspect(self.m_root)
        elif entry.kind == "background":
            self.m_stage.m_states = result
            self.m_inspector.clear()
        else:
            self.m_root = None
            self._fill_inspector(result)
        self.m_stage.update()

    def _load_faces(self, root: scene.SNode) -> bool:
        """The font files the dialog's fonts name (read by ``_read``), loaded into the page once (Qt's font database:
        the window's thread). Whether a face was new."""
        new, notes = False, {}
        for font in {scene_node.font for scene_node in root.walk() if scene_node.font is not None}:
            if not font.file:  # a Windows family the game names and ships no file for (1404, 1701, 2070's console)
                notes.setdefault(font.name, tr("common.font_system_family", name=font.family or font.name))
            elif font.file not in self.m_stage.m_faces:
                data = self._m_font_data.pop(font.file, None)
                family = gamefonts.load(data) if data else None
                self.m_stage.m_faces[font.file] = family or ""
                new = new or bool(family)
            if font.file and not self.m_stage.m_faces.get(font.file):
                notes.setdefault(font.name, tr("common.font_not_in_install", font=font.file))
        self.m_stage.setToolTip("\n".join(notes.values()))  # why a font is a system one
        return new

    def inspect(self, node: scene.SNode | None) -> None:
        self.m_stage.m_selected = node
        self.m_stage.update()
        if node is not None:
            self._fill_inspector(self._widget_rows(node))

    def _widget_rows(self, node: scene.SNode) -> list[tuple[str, object]]:
        widget = node.widget
        x, y, width, height = node.rect
        head = [
            (tr("ui.kind"), widget.kind),
            (tr("common.name"), widget.path_id),
            (tr("ui.unique_id"), widget.uid if widget.uid is not None else ""),
            (tr("assets.templates"), ", ".join(widget.templates)),
            (tr("ui.rectangle"), f"{x:g}, {y:g}, {width:g} x {height:g}"),
            (tr("assets.text"), node.text),
            (tr("ui.background"), node.background),
            (tr("ui.visible"), tr("mods.yes") if node.visible else tr("mods.no")),
        ]
        events = [(tr("ui.event_type", type=event.type), event.slot) for event in widget.events]
        return [*head, *events, *node.props.items()]

    def _template_rows(self, path: str) -> list[tuple[str, object]]:
        widget = self.m_files.dialog(path)
        return [
            (tr("ui.kind"), widget.kind),
            (tr("common.name"), widget.path_id),
            (tr("assets.templates"), ", ".join(widget.templates)),
            *widget.props.items(),
        ]

    def _fill_inspector(self, rows: list[tuple[str, object]]) -> None:
        self.m_inspector.clear()

        def add(parent, key: str, value: object) -> None:
            if isinstance(value, dict):
                item = QTreeWidgetItem(parent, [key, ""])
                for k, text in value.items():
                    add(item, k, text)
            else:
                QTreeWidgetItem(parent, [key, str(value)])

        for key, value in rows:
            add(self.m_inspector, str(key), value)
        self.m_inspector.expandToDepth(0)
