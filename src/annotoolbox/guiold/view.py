"""The old interface in a window page (Qt): a list of the game's dialogs, templates and backgrounds, the dialog drawn
with its backgrounds and texts, and an inspector for the widget under the cursor. Read only.

``OldGuiPage(tree)`` is the widget; ``show_dialog(path)`` opens a window of the game. The placing is ``scene``, the files
``files``; docs/ui/old-gui.md says what the preview draws and what it leaves out.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
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
    QLineEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import tr
from ..models import ddsdecode
from ..suite.background import Background
from . import files as oldfiles
from . import richtext, scene

ROLE = Qt.ItemDataRole.UserRole
STAGE = QColor(34, 36, 40)
ICON, PICTURE, BOX = "icon:", "img:", "box:"  # resource names in a text: an icon, a loose picture, a drawn box
BOX_SCALE = 2.0  # a box is drawn at twice the reference size: the page scales it to the window


def cut(image: QImage, tile: oldfiles.Tile) -> QRectF | None:
    """The rectangle of ``image`` a tile shows: its own, or (a ``.bg2`` icon) the cell its number names."""
    if tile.w <= 0 or tile.h <= 0:
        return None
    if tile.y >= 0:
        return QRectF(tile.x, tile.y, tile.w, tile.h)
    per_row = max(1, image.width() // tile.w)
    return QRectF((tile.x % per_row) * tile.w, (tile.x // per_row) * tile.h, tile.w, tile.h)


class Atlases:
    """The pictures the backgrounds cut their tiles from, decoded once (``missing`` the names no file was found for)."""

    def __init__(self, tree) -> None:
        self.tree, self._cache, self.missing = tree, {}, set()
        self._tinted: dict[tuple, QImage] = {}

    def tinted(self, path: str, factors: tuple[float, float, float] | None) -> QImage | None:
        """The picture multiplied by ``factors`` (kept per factors; its alpha as it is)."""
        image = self.get(path)
        if image is None or factors is None:
            return image
        key = (oldfiles.stem(path), factors)
        if key not in self._tinted:
            out = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            p = QPainter(out)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            p.fillRect(out.rect(), QColor(*(max(0, min(255, round(f * 255))) for f in factors)))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            p.drawImage(0, 0, image)
            p.end()
            self._tinted[key] = out
        return self._tinted[key]

    def get(self, path: str) -> QImage | None:
        stem = oldfiles.stem(path)
        if stem not in self._cache:
            self._cache[stem] = self._load(stem) if stem else None
            if self._cache[stem] is None and stem:
                self.missing.add(path)
        return self._cache[stem]

    def _load(self, stem: str) -> QImage | None:
        for candidate in (f"{stem}_0.dds", f"{stem}.dds", f"{stem}.tga", f"{stem}.png", f"{stem}.jpg"):
            if not self.tree.exists(candidate):
                continue
            data = self.tree.read(candidate)
            if candidate.endswith(".dds"):
                try:
                    rgba = ddsdecode.DdsFile(data).level(0)
                except (ddsdecode.UnsupportedDds, ValueError, IndexError):
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
    fmt = table.format()
    spacing, edge = fmt.cellSpacing(), fmt.border() + fmt.padding()
    cells, lefts, tops = {}, {}, {}
    for r in range(table.rows()):
        for c in range(table.columns()):
            cell = table.cellAt(r, c)
            rects = _cell_blocks(doc, table, cell) if cell.row() == r and cell.column() == c else []
            if not rects:
                continue
            own = cell.format().toTableCellFormat()  # a cell's own padding, else the table's
            left = own.leftPadding() if own.hasProperty(_PADS[0]) else fmt.cellPadding()
            top = own.topPadding() if own.hasProperty(_PADS[1]) else fmt.cellPadding()
            cells[(r, c)] = cell
            lefts[c] = min(lefts.get(c, 1e9), min(q.x() for q in rects) - left)
            tops[r] = min(tops.get(r, 1e9), min(q.y() for q in rects) - top)
    if 0 not in lefts or 0 not in tops:
        return QRectF(), {}
    size = doc.documentLayout().frameBoundingRect(table).size()
    box = QRectF(lefts[0] - spacing - edge, tops[0] - spacing - edge, size.width() - fmt.leftMargin() - fmt.rightMargin(),
                 size.height() - fmt.topMargin() - fmt.bottomMargin())  # fmt: skip
    after = {"right": box.right() - edge - spacing, "bottom": box.bottom() - edge - spacing}
    out = {}
    for (r, c), cell in cells.items():
        c2, r2 = c + cell.columnSpan(), r + cell.rowSpan()
        right = lefts[c2] - spacing if c2 in lefts else after["right"]
        bottom = tops[r2] - spacing if r2 in tops else after["bottom"]
        out[(r, c)] = QRectF(lefts[c], tops[r], right - lefts[c], bottom - tops[r])
    return box, out


def table_backdrops(doc: QTextDocument, frames: list[richtext.Frame]) -> list[tuple[QRectF, str]]:
    """The background pictures of a laid-out document's tables and cells (``richtext.to_html``'s ``frames``) with
    where they go: a table's over its padding, a cell's over the cell; none where Qt made other tables or cells."""
    tables = list(_tables(doc.rootFrame()))
    if len(tables) != len(frames) or not any(p or any(cells) for p, _pad, cells in frames):
        return []
    out = []
    for table, (picture, (top, right, bottom, left), pictures) in zip(tables, frames, strict=True):
        box, cells = table_rects(doc, table)
        if picture and not box.isEmpty():
            out.append((box.adjusted(-left, -top, right, bottom), picture))
        if len(cells) == len(pictures):
            out += [(cells[k], p) for k, p in zip(sorted(cells), pictures, strict=True) if p]
    return out


def in_class(font: QFont, style: richtext.Style | None) -> QFont:
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


class Stage(QWidget):
    """Draws the open dialog scaled to fit; a click picks the widget under it."""

    picked = Signal(object)

    def __init__(self, atlases: Atlases) -> None:
        super().__init__()
        self.atlases, self.root, self.selected = atlases, None, None
        self.faces: dict[str, str] = {}  # a font file of the game -> the family it was loaded as (``OldGuiPage``)
        self.states: list[tuple[str, oldfiles.Look]] = []  # a background shown on its own: its states
        self.screen = scene.REFERENCE  # the visible space the dialog was placed in (``scene.SCREENS``)
        self.files: oldfiles.OldGui | None = None  # for the icons inside texts
        self._laid: dict[int, QTextDocument] = {}  # a placed widget's rich text, laid out (``show``)
        self._boxes = 0  # the boxes of the texts drawn so far (``_box``: each a resource of its own)
        self.setMinimumSize(320, 256)

    def show(self, root: scene.Node | None) -> None:
        """Draw a placed dialog (its rich texts are laid out again with their first paint)."""
        self.root, self._laid = root, {}

    def _scale(self) -> tuple[float, float, float]:
        w, h = self.screen
        s = min(self.width() / w, self.height() / h)
        return s, (self.width() - w * s) / 2, (self.height() - h * s) / 2

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().window())
        s, ox, oy = self._scale()
        p.translate(ox, oy)
        p.scale(s, s)
        p.fillRect(QRectF(0, 0, *self.screen), STAGE)
        if self.states:
            self._draw_states(p)
        elif self.root is not None:
            self._draw(p, self.root)
        p.end()

    def _draw_states(self, p: QPainter) -> None:
        """One cell per state of a background, each filled at 360 x 200 (the state's own size when smaller)."""
        for i, (name, look) in enumerate(self.states):
            x, y = 20 + (i % 3) * 400, 20 + (i // 3) * 240
            self._fill(p, look, QRectF(x, y, 360, 200))
            p.setPen(QColor(200, 200, 200))
            p.drawText(QPointF(x, y + 216), name)

    def _fill(self, p: QPainter, look: oldfiles.Look | None, box: QRectF, tint=None) -> None:
        if look is None:
            return
        for tile, (dx, dy, dw, dh) in scene.tile_rects(look, box.width(), box.height()):
            self._tile(p, tile, QRectF(box.x() + dx, box.y() + dy, dw, dh), tint)

    def _tile(self, p: QPainter, tile: oldfiles.Tile, target: QRectF, tint=None) -> None:
        image = self.atlases.tinted(tile.atlas, tint) if tile.atlas else None
        src = cut(image, tile) if image is not None else None
        if image is not None and src is not None and target.width() > 0 and target.height() > 0:
            p.drawImage(target, image, src)

    def _draw(self, p: QPainter, node: scene.Node) -> None:
        if not node.visible:
            return
        x, y, w, h = node.rect
        box = QRectF(x, y, w, h)
        self._fill(p, node.look, box, node.tint)
        if node.icon is not None and node.icon_rect is not None:
            icon_tint = node.look.icon_tint if node.look is not None else None
            icon_tint = (
                oldfiles.hsv_rgb(*icon_tint[:3]) if icon_tint and tuple(icon_tint) != oldfiles.UNCHANGED else None
            )
            self._tile(p, node.icon, QRectF(*node.icon_rect), icon_tint)
        if node.text:
            self._text(p, node, box)
        pen = QPen(QColor(255, 160, 40) if node is self.selected else QColor(110, 150, 190, 90))  # every outline
        pen.setCosmetic(True)
        pen.setWidthF(2.0 if node is self.selected else 1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(box)
        clips = node.kind in scene.CLIPS
        if clips:  # a scroll box shows what it holds inside its client area, at the scroll position 0
            p.save()
            p.setClipRect(QRectF(*scene.client(node)), Qt.ClipOperation.IntersectClip)
        for child in node.children:
            self._draw(p, child)
        if clips:
            p.restore()

    def _text(self, p: QPainter, node: scene.Node, box: QRectF) -> None:
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
            p.setFont(font)
            p.setPen(QColor(*colour))
            p.drawText(box, int(flag | Qt.AlignmentFlag.AlignVCenter), node.text)
            return
        laid = self._laid.get(id(node))
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
            laid = self._laid[id(node)] = (doc, table_backdrops(doc, frames))
        doc, backdrops = laid
        p.save()
        p.translate(box.x(), box.y() + max(0.0, (box.height() - doc.size().height()) / 2))
        self._contents(p, doc, backdrops)
        p.restore()

    def _contents(self, p: QPainter, doc: QTextDocument, backdrops: list, clip: QRectF | None = None) -> None:
        """A laid-out document, its tables' and cells' background pictures behind it."""
        for rect, picture in backdrops if self.files is not None else ():
            self._fill(p, scene.rest_look(self.files, picture), rect)
        doc.drawContents(p, clip) if clip is not None else doc.drawContents(p)

    def _markup(
        self, doc: QTextDocument, text: str, around: tuple, path: tuple = (), frames: list | None = None
    ) -> str:
        """A text's markup as rich text for ``doc``, its icons, pictures and boxes held by the document; ``around``:
        (font, colour, width) of the text around it, ``path`` the elements it sits in, ``frames`` gets its tables."""
        line = float(around[0].pixelSize())
        sheet = self.files.sheet if self.files else richtext.StyleSheet()
        return richtext.to_html(text, sheet, lambda attrs, style: self._icon(doc, attrs, style, line),
                                lambda style, inner, at: self._box(doc, style, inner, around, at), frames, path)  # fmt: skip

    def _box(self, doc: QTextDocument, style: richtext.Style, inner: str, around: tuple, path: tuple = ()):
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
        p = QPainter(image)
        p.scale(BOX_SCALE, BOX_SCALE)
        if style.picture and self.files is not None:
            self._fill(p, scene.rest_look(self.files, style.picture), QRectF(0, 0, width, height))
        p.translate(left, top)
        self._contents(p, box, table_backdrops(box, frames), QRectF(0, 0, width - left - right, height - top - bottom))
        p.end()
        self._boxes += 1
        name = f"{BOX}{self._boxes}"
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image)
        return name, width, height

    def _icon(self, doc: QTextDocument, attrs: dict, style, line: float):
        """An ``<icon>`` of a text as a picture the document holds: its atlas cell, at the line's height for
        ``type="autosize"``, else at the class's size, else at its own; an ``<img>`` the loose picture it names."""
        if "src" in attrs:
            return self._picture(doc, attrs, style)
        tile = self.files.icon(attrs.get("guid")) if self.files is not None else None
        image = self.atlases.get(tile.atlas) if tile is not None else None
        src = cut(image, tile) if image is not None else None
        if src is None:
            return None
        name = f"{ICON}{attrs.get('guid')}"
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image.copy(src.toRect()))
        if attrs.get("type") == "autosize":
            return name, line, line
        w, h = (style.width, style.height) if style is not None else (None, None)
        if w or h:  # one of them alone keeps the picture's aspect
            return name, w or h * tile.w / max(1, tile.h), h or w * tile.h / max(1, tile.w)
        return name, float(tile.w), float(tile.h)

    def _picture(self, doc: QTextDocument, attrs: dict, style):
        """An ``<img>``'s loose picture, at its own ``width`` / ``height``, else the class's, else its size."""
        src = attrs.get("src", "")
        image = self.atlases.get(src) if src and "[" not in src else None
        if image is None:
            return None
        name = f"{PICTURE}{src}"
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image)
        w, h = oldfiles.number(attrs.get("width"), 0.0), oldfiles.number(attrs.get("height"), 0.0)
        if not (w and h) and style is not None and style.width and style.height:
            w, h = style.width, style.height
        return name, w or float(image.width()), h or float(image.height())

    def qfont(self, font: oldfiles.Font | None, style: richtext.Style | None = None, height: float = 24.0) -> QFont:
        """The game's font (its face loaded from the file the game ships), else a stand-in sized to the widget; a
        class of the style sheet over it where it sets the size, weight or slant."""
        out = QFont(self.font())
        if font is None:
            out.setPixelSize(max(10, min(24, int(height * 0.6) or 14)))
        else:
            family = self.faces.get(font.file) if font.file else None
            out.setFamily(family or font.family)
            out.setPixelSize(max(6, round(font.size)))
            out.setWeight(QFont.Weight(max(100, min(900, font.weight))))
        return in_class(out, style)

    def measure(self, text: str, font: oldfiles.Font | None, width: float, style=None) -> tuple[float, float]:
        """A text's size in its font and class, as ``_text`` draws it (``scene.Measure``), wrapped at ``width`` when
        that is not 0 (any thread)."""
        metrics = QFontMetricsF(self.qfont(font, style))
        flags = int(Qt.TextFlag.TextWordWrap) if width > 0 else 0
        r = metrics.boundingRect(QRectF(0, 0, width if width > 0 else 1e6, 1e6), flags, richtext.plain(text))
        return r.width(), r.height()

    def mousePressEvent(self, event) -> None:
        if self.root is None:
            return
        s, ox, oy = self._scale()
        pos = event.position()
        self.picked.emit(self.root.at((pos.x() - ox) / s, (pos.y() - oy) / s))


class OldGuiPage(QWidget):
    """The old interface of one game: files on the left, the drawing in the middle, the inspector on the right."""

    def __init__(self, tree) -> None:
        super().__init__()
        self.files, self.atlases = oldfiles.OldGui(tree), Atlases(tree)
        self.root: scene.Node | None = None
        self.background = Background(self)
        self.sync_open = False  # read a file on this thread (tests)
        self._font_data: dict[str, bytes | None] = {}  # font files read off the window's thread, to be loaded on it
        self._wanted: tuple | None = None  # (file, state, screen) asked for last: an older one is not shown
        self.state = QComboBox()
        self.state.addItems(scene.STATES)
        self.state.setToolTip(tr("The state the widgets are drawn in, where their background has it"))
        self.screen = QComboBox()
        self.screen.addItems(list(scene.SCREENS))
        self.screen.setToolTip(tr("The screen's aspect: the dialog placed in its visible space (1024 high)"))
        self.find = QLineEdit(placeholderText=tr("find a dialog, template or background"))
        self.list = QTreeWidget()
        self.list.setHeaderLabels([tr("File"), tr("Kind")])
        self.stage = Stage(self.atlases)
        self.stage.files = self.files
        self.inspector = QTreeWidget()
        self.inspector.setHeaderLabels([tr("Property"), tr("Value")])
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.find)
        lay.addWidget(self.state)
        lay.addWidget(self.screen)
        lay.addWidget(self.list)
        split = QSplitter(self)
        for w in (left, self.stage, self.inspector):
            split.addWidget(w)
        split.setSizes([300, 760, 380])
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(split)
        self.find.textChanged.connect(self.fill)
        self.list.currentItemChanged.connect(lambda item, _old: self.open(item.data(0, ROLE)) if item else None)
        self.stage.picked.connect(self.inspect)
        for choice in (self.state, self.screen):
            choice.currentTextChanged.connect(lambda _s: self.open(self._wanted[0] if self._wanted else None))
        self.fill()

    def fill(self) -> None:
        self.list.clear()
        words = self.find.text().lower().split()
        groups = {"dialog": tr("Dialogs"), "template": tr("Templates"), "background": tr("Backgrounds")}
        tops = {k: QTreeWidgetItem(self.list, [v, ""]) for k, v in groups.items()}
        for path, entry in sorted(self.files.entries.items()):
            if "__custom" in path or (words and not all(w in path for w in words)):
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
        entry = self.files.entries.get(path or "")
        if entry is None:
            return
        screen = scene.SCREENS.get(self.screen.currentText(), scene.REFERENCE)
        wanted = self._wanted = (entry.path, self.state.currentText(), screen)  # read here: the window thread's
        self.background.run(
            lambda: self._read(entry, *wanted[1:]), lambda result: self._opened(entry, result, wanted), self.sync_open
        )

    def _read(self, entry: oldfiles.Entry, state: str, screen=scene.REFERENCE):
        """What showing ``entry`` in ``state`` on ``screen`` needs, with every picture its backgrounds, icons and the
        icons inside its texts cut from decoded."""
        if entry.kind == "dialog":
            shown = scene.build(self.files, self.files.dialog(entry.path), state, screen, self.stage.measure)
            looks = [n.look for n in shown.walk() if n.look is not None]
            for n in shown.walk():
                if n.font is not None and n.font.file and n.font.file not in self.stage.faces:
                    self._font_data.setdefault(n.font.file, self.files.font_file(n.font))
                tiles = [n.icon] + [self.files.icon(g) for g in richtext.icon_guids(n.text)]
                for picture in [t.atlas for t in tiles if t is not None] + richtext.picture_paths(n.text):
                    self.atlases.get(picture)
                pictures = richtext.box_pictures(n.text, self.files.sheet, n.around)
                boxes = (scene.rest_look(self.files, b) for b in pictures)
                looks += [look for look in boxes if look is not None]  # the backgrounds of its boxes
        elif entry.kind == "background":
            shown = list(self.files.background(entry.path).items())
            looks = [look for _, look in shown]
        else:
            return self._template_rows(entry.path)
        for tile in {t for look in looks for t in look.tiles if t.atlas}:
            self.atlases.get(tile.atlas)
        return shown

    def _opened(self, entry: oldfiles.Entry, result, wanted: tuple) -> None:
        if wanted != self._wanted:  # another file or state was asked for meanwhile (and may have been read sooner)
            return
        self.stage.states, self.stage.selected = [], None
        self.stage.show(None)
        if isinstance(result, Exception):
            self.root = None
            self._fill_inspector([(tr("Error"), f"{type(result).__name__}: {result}")])
        elif entry.kind == "dialog":
            self.root = result
            self.stage.show(result)
            self.stage.screen = wanted[2]
            if self._load_faces(result):  # the texts were measured in a stand-in face: once more in the game's
                self.open(entry.path)
            self.inspect(self.root)
        elif entry.kind == "background":
            self.stage.states = result
            self.inspector.clear()
        else:
            self.root = None
            self._fill_inspector(result)
        self.stage.update()

    def _load_faces(self, root: scene.Node) -> bool:
        """The font files the dialog's fonts name (read by ``_read``), loaded into the page once (Qt's font database:
        the window's thread). Whether a face was new."""
        new = False
        for font in {n.font for n in root.walk() if n.font is not None and n.font.file}:
            if font.file in self.stage.faces:
                continue
            data = self._font_data.pop(font.file, None)
            ident = QFontDatabase.addApplicationFontFromData(data) if data else -1
            families = QFontDatabase.applicationFontFamilies(ident) if ident >= 0 else []
            self.stage.faces[font.file] = families[0] if families else ""
            new = new or bool(families)
        return new

    def inspect(self, node: scene.Node | None) -> None:
        self.stage.selected = node
        self.stage.update()
        if node is not None:
            self._fill_inspector(self._widget_rows(node))

    def _widget_rows(self, node: scene.Node) -> list[tuple[str, object]]:
        w = node.widget
        x, y, width, height = node.rect
        head = [
            (tr("Kind"), w.kind),
            (tr("Name"), w.path_id),
            (tr("Unique id"), w.uid if w.uid is not None else ""),
            (tr("Templates"), ", ".join(w.templates)),
            (tr("Rectangle"), f"{x:g}, {y:g}, {width:g} x {height:g}"),
            (tr("Text"), node.text),
            (tr("Background"), node.background),
            (tr("Visible"), tr("yes") if node.visible else tr("no")),
        ]
        events = [(tr("Event {type}", type=e.type), e.slot) for e in w.events]
        return [*head, *events, *node.props.items()]

    def _template_rows(self, path: str) -> list[tuple[str, object]]:
        w = self.files.dialog(path)
        return [
            (tr("Kind"), w.kind),
            (tr("Name"), w.path_id),
            (tr("Templates"), ", ".join(w.templates)),
            *w.props.items(),
        ]

    def _fill_inspector(self, rows: list[tuple[str, object]]) -> None:
        self.inspector.clear()

        def add(parent, key: str, value: object) -> None:
            if isinstance(value, dict):
                item = QTreeWidgetItem(parent, [key, ""])
                for k, v in value.items():
                    add(item, k, v)
            else:
                QTreeWidgetItem(parent, [key, str(value)])

        for key, value in rows:
            add(self.inspector, str(key), value)
        self.inspector.expandToDepth(0)
