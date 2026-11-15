"""The asset window's other tabs and lists: BalancingView (a group's assets against chosen values, editable, CSV
out and in), TextView (the asset's texts in a language, editable), Icons (the asset's icon file), Templates (the
template's properties and defaults), Usage and diff (who names the asset, what the change set changed), the check
results and the history. Each panel is filled with ``present(doc, guid)`` by ``assetwindow``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import tr
from . import balancing, compare, icons, report
from .assetdoc import AssetDocument
from .values import OWN
from .views import ViewStore
from .xmltree import leaves

LANGUAGES = ("english", "german", "french", "italian", "polish", "russian", "spanish", "chinese", "japanese", "korean")


class BalancingPanel(QWidget):
    """Many assets against chosen values; a cell edit is a value edit. The columns can be saved as a named view."""

    def __init__(self) -> None:
        super().__init__()
        self.doc, self.guids = None, []
        self.store: ViewStore | None = None  # the saved views of the game open (``views.ViewStore``)
        layout = QVBoxLayout(self)
        self.columns = QLineEdit(placeholderText=tr("value paths, comma separated: Building/Upkeep, Product/Price"))
        self.saved = QComboBox(toolTip=tr("the saved views: pick one to take its columns"))
        self.save_button = QPushButton(tr("Save view…"), toolTip=tr("keep these columns under a name"))
        self.delete_button = QPushButton(tr("Delete view"), toolTip=tr("forget the picked view"))
        bar = QHBoxLayout()
        for widget in (self.columns, self.saved, self.save_button, self.delete_button):
            bar.addWidget(widget, 1 if widget is self.columns else 0)
        self.table = QTableWidget()
        layout.addLayout(bar)
        layout.addWidget(self.table, 1)
        self.columns.returnPressed.connect(self.fill)
        self.table.itemChanged.connect(self._edited)
        self.saved.activated.connect(self._pick)
        self.save_button.clicked.connect(lambda _c=False: self.save_view())
        self.delete_button.clicked.connect(lambda _c=False: self.delete_view())
        self.set_store(None)

    def set_store(self, store: ViewStore | None) -> None:
        """The saved views to offer (None: the game's views are not available, saving is off)."""
        self.store = store
        self.save_button.setEnabled(store is not None)
        self._list_views()

    def _list_views(self) -> None:
        self.saved.blockSignals(True)
        self.saved.clear()
        self.saved.addItem(tr("(saved views)"), "")
        for name in self.store.names if self.store is not None else ():
            self.saved.addItem(self.store.views[name].label(), name)
        self.saved.blockSignals(False)
        self.delete_button.setEnabled(self.saved.count() > 1)

    def _pick(self, _index: int) -> None:
        name = self.saved.currentData()
        if name and self.store is not None and name in self.store.views:
            self.columns.setText(", ".join(self.store.views[name].columns))
            self.fill()

    def save_view(self, name: str | None = None) -> str | None:
        """Keep the columns as a view (asks for the name unless given); returns the name saved."""
        if self.store is None or not self.paths():
            return None
        if name is None:
            current = self.saved.currentData() or ""
            name, ok = QInputDialog.getText(self, tr("Save view"), tr("Name of the view:"), text=current)
            if not ok:
                return None
        template = self.doc.template(self.guids[0]) if self.doc is not None and self.guids else ""
        self.store.put(name, self.paths(), template)
        self.store.save()
        self._list_views()
        self.saved.setCurrentIndex(self.saved.findData(name.strip()))
        return name.strip()

    def delete_view(self) -> None:
        name = self.saved.currentData()
        if name and self.store is not None and self.store.remove(name):
            self.store.save()
            self._list_views()

    def present(self, doc: AssetDocument, guids: list[int]) -> None:
        self.doc, self.guids = doc, guids[:500]
        self.fill()

    def paths(self) -> list[str]:
        return [c.strip() for c in self.columns.text().split(",") if c.strip()]

    def fill(self) -> None:
        self.table.blockSignals(True)
        cols = self.paths()
        rows = balancing.grid(self.doc, self.guids, cols) if self.doc and cols else []
        self.table.clear()
        self.table.setColumnCount(len(cols) + 1)
        self.table.setRowCount(len(rows))
        self.table.setHorizontalHeaderLabels([tr("Asset"), *cols])
        for r, (guid, name, cells) in enumerate(rows):
            head = QTableWidgetItem(f"{name} ({guid})")
            head.setFlags(head.flags() & ~Qt.ItemFlag.ItemIsEditable)
            head.setData(Qt.ItemDataRole.UserRole, guid)
            self.table.setItem(r, 0, head)
            for c, cell in enumerate(cells, 1):
                item = QTableWidgetItem(cell.text)
                item.setToolTip(
                    cell.origin
                    + (
                        " · " + tr("the game's: {value}", value=cell.was or tr("(none)"))
                        if cell.was is not None
                        else ""
                    )
                )
                font = item.font()
                font.setBold(cell.origin == OWN)
                item.setFont(font)
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)

    def _edited(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            return
        guid = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        self.doc.set_value(guid, self.paths()[item.column() - 1], item.text())


class TextPanel(QWidget):
    """The asset's text values and the texts they name in a language; an edit is a text edit."""

    def __init__(self, texts, languages: tuple[str, ...] = LANGUAGES, group=lambda: []) -> None:
        """``texts(language)``: the game's texts of that language (id -> text); ``languages``: those the game has;
        ``group()``: the GUIDs of the group picked in the tree (its words are counted too)."""
        super().__init__()
        self.texts, self.group, self.doc, self.guid = texts, group, None, None
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.language = QComboBox()
        self.language.addItems(languages)
        row.addWidget(QLabel(tr("Language")))
        row.addWidget(self.language)
        row.addStretch(1)
        layout.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("Value"), tr("Text id"), tr("Text")])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.words = QLabel()
        layout.addWidget(self.words)
        self.language.currentIndexChanged.connect(lambda _i: self.fill())
        self.table.itemChanged.connect(self._edited)

    def set_languages(self, names: tuple[str, ...]) -> None:
        """The languages of the game just opened (the file names differ per game)."""
        keep = self.language.currentText()
        self.language.blockSignals(True)
        self.language.clear()
        self.language.addItems(names)
        self.language.setCurrentText(keep if keep in names else names[0])
        self.language.blockSignals(False)

    def present(self, doc: AssetDocument, guid: int | None) -> None:
        self.doc, self.guid = doc, guid
        self.fill()

    def fill(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.words.setText("")
        if self.doc is not None and self.guid is not None and self.doc.exists(self.guid):
            lang = self.language.currentText()
            game, edited = self.texts(lang), self.doc.changes.texts.get(lang, {})
            rows = report.asset_texts(self.doc, self.guid, game, edited)
            for r, row in enumerate(rows):
                self.table.insertRow(r)
                for c, value in enumerate(row):
                    item = QTableWidgetItem(value)
                    if c < 2:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(r, c, item)
            self.words.setText(self._count(rows, game, edited))
        self.table.blockSignals(False)

    def _count(self, rows, game: dict[str, str], edited: dict[str, str]) -> str:
        """The words of this asset's texts, and of the picked group's assets when it is more than this one."""
        text = tr("{n} words in this asset's texts", n=report.words_of(rows))
        group = self.group()
        if len(group) > 1:
            text += " · " + tr(
                "{n} words in the {count} assets of the group",
                n=report.group_words(self.doc, group, game, edited),
                count=len(group),
            )
        return text

    def _edited(self, item: QTableWidgetItem) -> None:
        if item.column() == 2:
            self.doc.set_text(self.language.currentText(), self.table.item(item.row(), 1).text(), item.text())


class IconPanel(QWidget):
    """The asset's icon: its file or atlas tile, the picture, and where the game keeps its icons in ``icons.xml``
    (2070, 1404, 1701) the boxes that set the atlas and the tile."""

    def __init__(self, source) -> None:
        """``source``: the ``icons.IconSource`` of the game open."""
        super().__init__()
        self.source = source
        layout = QVBoxLayout(self)
        self.path = QLabel(wordWrap=True, textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        self.picture = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.number = QSpinBox(maximum=0)  # which icon of the entry (0: the first); one past the last adds one
        self.atlas, self.tile = QSpinBox(maximum=100000), QSpinBox(maximum=100000)
        self.set_button = QPushButton(tr("Set this icon"))
        self.set_button.clicked.connect(self._set)
        self.doc: AssetDocument | None = None
        self.guid: int | None = None
        self.editor = QWidget()
        row = QHBoxLayout(self.editor)
        self.number.valueChanged.connect(lambda _n: self._show())
        for text, box in ((tr("icon"), self.number), (tr("atlas file id"), self.atlas), (tr("tile"), self.tile)):
            row.addWidget(QLabel(text))
            row.addWidget(box)
        row.addWidget(self.set_button)
        row.addStretch(1)
        layout.addWidget(self.path)
        layout.addWidget(self.editor)
        layout.addWidget(self.picture, 1)

    def _set(self) -> None:
        if self.doc is not None and self.guid is not None:
            self.doc.set_icon(self.guid, self.atlas.value(), self.tile.value(), self.number.value())

    def present(self, doc: AssetDocument, guid: int | None) -> None:
        self.doc, self.guid = doc, guid
        shown = guid is not None and doc.exists(guid)
        self.editor.setVisible(self.source.editable and shown)
        self.number.blockSignals(True)
        self.number.setMaximum(self.source.count_of(guid) if shown else 0)
        self.number.setValue(min(self.number.value(), self.number.maximum()))
        self.number.blockSignals(False)
        self._show()

    def _show(self) -> None:
        doc, guid, position = self.doc, self.guid, self.number.value()
        shown = doc is not None and guid is not None and doc.exists(guid)
        ref = self.source.ref_of(guid, position) if shown else None
        if ref is not None:
            self.atlas.setValue(ref.file_id)
            self.tile.setValue(ref.index)
        key = icons.atlas_key(guid, position) if ref is not None and position else ""
        key = key or (self.source.key_of(doc, guid) if shown else "")
        self.path.setText(self.source.describe(key) if key else tr("this asset names no icon file"))
        pix = self.source.picture(key)
        self.picture.setPixmap(pix)
        if key and pix.isNull():
            self.picture.setText(tr("(no picture of this path in the game's files)"))


class TemplatePanel(QTreeWidget):
    """The asset's template: its properties and the default values it gives."""

    def __init__(self) -> None:
        super().__init__()
        self.setHeaderLabels([tr("Template value"), tr("Default")])
        self.setColumnWidth(0, 320)

    def present(self, doc: AssetDocument, guid: int | None) -> None:
        self.clear()
        t = doc.schema.templates.get(doc.template(guid)) if guid is not None else None
        if t is None:
            self.addTopLevelItem(QTreeWidgetItem([tr("(no template)"), ""]))
            return
        top = QTreeWidgetItem([f"{t.name}  ({' / '.join(t.group)})", ""])
        self.addTopLevelItem(top)
        for prop in t.properties:
            node = QTreeWidgetItem([prop.tag, ""])
            node.addChildren([QTreeWidgetItem(["/".join(p), text]) for p, text in leaves(prop)])
            top.addChild(node)
        top.setExpanded(True)


class UsagePanel(QWidget):
    """Who names the asset (a double click shows that one), and what the change set changed in it."""

    jump = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.users = QListWidget()
        self.diff = QTableWidget(0, 3)
        self.diff.setHorizontalHeaderLabels([tr("Value"), tr("Game"), tr("Edited")])
        self.diff.horizontalHeader().setStretchLastSection(True)
        self.users_label, self.diff_label = QLabel(), QLabel()
        for w in (self.users_label, self.users, self.diff_label, self.diff):
            layout.addWidget(w)
        self.users.itemDoubleClicked.connect(lambda it: self.jump.emit(it.data(Qt.ItemDataRole.UserRole)))

    def present(self, doc: AssetDocument, guid: int | None) -> None:
        self.users.clear()
        self.diff.setRowCount(0)
        if guid is None or not doc.exists(guid):
            return
        users = compare.usage(doc, guid)
        self.users_label.setText(tr("{n} assets name {guid}", n=len(users), guid=guid))
        for u in users[:2000]:
            item = QListWidgetItem(f"{doc.name(u)} ({u}) · {doc.template(u)}")
            item.setData(Qt.ItemDataRole.UserRole, u)
            self.users.addItem(item)
        rows = compare.diff_asset(doc, guid)
        self.diff_label.setText(
            tr("{n} values differ from the game's", n=len(rows)) if guid in doc.index.by_guid else tr("new")
        )
        for d in rows:
            r = self.diff.rowCount()
            self.diff.insertRow(r)
            for c, v in enumerate((d.path, d.before, d.after)):
                self.diff.setItem(r, c, QTableWidgetItem("" if v is None else v))


class FindingsList(QListWidget):
    """The check's findings; a double click shows the asset."""

    jump = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.itemDoubleClicked.connect(lambda it: it.data(Qt.ItemDataRole.UserRole) and self.jump.emit(
            it.data(Qt.ItemDataRole.UserRole)))  # fmt: skip

    def present(self, findings) -> None:
        self.clear()
        for f in findings:
            item = QListWidgetItem(str(f))
            item.setData(Qt.ItemDataRole.UserRole, f.guid)
            self.addItem(item)
        if not findings:
            self.addItem(tr("no findings"))


class HistoryList(QListWidget):
    """The undo stack: a click goes back (or forward) to that change."""

    def __init__(self) -> None:
        super().__init__()
        self.doc = None
        self.itemClicked.connect(self._jump)

    def present(self, doc: AssetDocument) -> None:
        self.doc = doc
        stack = doc.undo
        self.clear()
        self.addItem(tr("(as opened)"))
        self.addItems(
            [lab + ("  · " + tr("saved") if k + 1 == stack.clean else "") for k, lab in enumerate(stack.labels())]
        )
        self.setCurrentRow(stack.index)

    def _jump(self, item) -> None:
        stack, want = self.doc.undo, self.row(item)
        while stack.index > want and stack.undo():
            pass
        while stack.index < want and stack.redo():
            pass
