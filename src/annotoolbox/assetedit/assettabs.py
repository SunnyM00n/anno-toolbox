"""The asset window's other tabs and lists: SBalancingView (a group's assets against chosen values, editable, CSV
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
from .assetdoc import CAssetDocument
from .values import OWN
from .views import CViewStore
from .xmltree import leaves

LANGUAGES = ("english", "german", "french", "italian", "polish", "russian", "spanish", "chinese", "japanese", "korean")


class CBalancingPanel(QWidget):
    """Many assets against chosen values; a cell edit is a value edit. The columns can be saved as a named view."""

    def __init__(self) -> None:
        super().__init__()
        self.m_doc, self.m_guids = None, []
        self.m_shown: list[str] = []  # the value paths of the table's columns as it was last filled
        self.m_store: CViewStore | None = None  # the saved views of the game open (``views.CViewStore``)
        layout = QVBoxLayout(self)
        self.m_columns = QLineEdit(placeholderText=tr("assets.value_paths_comma_separated_building"))
        self.m_saved = QComboBox(toolTip=tr("assets.tip.saved_views_pick_one_take"))
        self.m_save_button = QPushButton(tr("assets.save_view_dots"), toolTip=tr("assets.tip.keep_columns_under_name"))
        self.m_delete_button = QPushButton(tr("assets.delete_view"), toolTip=tr("assets.tip.forget_picked_view"))
        bar = QHBoxLayout()
        for widget in (self.m_columns, self.m_saved, self.m_save_button, self.m_delete_button):
            bar.addWidget(widget, 1 if widget is self.m_columns else 0)
        self.m_table = QTableWidget()
        layout.addLayout(bar)
        layout.addWidget(self.m_table, 1)
        self.m_columns.returnPressed.connect(self.fill)
        self.m_table.itemChanged.connect(self._edited)
        self.m_saved.activated.connect(self._pick)
        self.m_save_button.clicked.connect(lambda _c=False: self.save_view())
        self.m_delete_button.clicked.connect(lambda _c=False: self.delete_view())
        self.set_store(None)

    def set_store(self, store: CViewStore | None) -> None:
        """The saved views to offer (None: the game's views are not available, saving is off)."""
        self.m_store = store
        self.m_save_button.setEnabled(store is not None)
        self._list_views()

    def _list_views(self) -> None:
        self.m_saved.blockSignals(True)
        self.m_saved.clear()
        self.m_saved.addItem(tr("assets.saved_views"), "")
        for name in self.m_store.names if self.m_store is not None else ():
            self.m_saved.addItem(self.m_store.m_views[name].label(), name)
        self.m_saved.blockSignals(False)
        self.m_delete_button.setEnabled(self.m_saved.count() > 1)

    def _pick(self, _index: int) -> None:
        name = self.m_saved.currentData()
        if name and self.m_store is not None and name in self.m_store.m_views:
            self.m_columns.setText(", ".join(self.m_store.m_views[name].columns))
            self.fill()

    def save_view(self, name: str | None = None) -> str | None:
        """Keep the columns as a view (asks for the name unless given); returns the name saved."""
        if self.m_store is None or not self.paths():
            return None
        if name is None:
            current = self.m_saved.currentData() or ""
            name, is_accepted = QInputDialog.getText(self, tr("assets.save_view"), tr("assets.name_view"), text=current)
            if not is_accepted:
                return None
        template = self.m_doc.template(self.m_guids[0]) if self.m_doc is not None and self.m_guids else ""
        self.m_store.put(name, self.paths(), template)
        self.m_store.save()
        self._list_views()
        self.m_saved.setCurrentIndex(self.m_saved.findData(name.strip()))
        return name.strip()

    def delete_view(self) -> None:
        name = self.m_saved.currentData()
        if name and self.m_store is not None and self.m_store.remove(name):
            self.m_store.save()
            self._list_views()

    def present(self, doc: CAssetDocument, guids: list[int]) -> None:
        self.m_doc, self.m_guids = doc, guids[:500]
        self.fill()

    def paths(self) -> list[str]:
        return [column.strip() for column in self.m_columns.text().split(",") if column.strip()]

    def fill(self) -> None:
        self.m_table.blockSignals(True)
        column_names = self.m_shown = self.paths()
        rows = balancing.grid(self.m_doc, self.m_guids, column_names) if self.m_doc and column_names else []
        self.m_table.clear()
        self.m_table.setColumnCount(len(column_names) + 1)
        self.m_table.setRowCount(len(rows))
        self.m_table.setHorizontalHeaderLabels([tr("common.asset"), *column_names])
        for row_index, (guid, name, cells) in enumerate(rows):
            head = QTableWidgetItem(f"{name} ({guid})")
            head.setFlags(head.flags() & ~Qt.ItemFlag.ItemIsEditable)
            head.setData(Qt.ItemDataRole.UserRole, guid)
            self.m_table.setItem(row_index, 0, head)
            for column_index, cell in enumerate(cells, 1):
                item = QTableWidgetItem(cell.text)
                item.setToolTip(
                    cell.origin
                    + (
                        " · " + tr("assets.the_games_value", value=cell.was or tr("common.none_in_brackets"))
                        if cell.was is not None
                        else ""
                    )
                )
                font = item.font()
                font.setBold(cell.origin == OWN)
                item.setFont(font)
                self.m_table.setItem(row_index, column_index, item)
        self.m_table.blockSignals(False)

    def _edited(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            return
        guid = self.m_table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        self.m_doc.set_value(guid, self.m_shown[item.column() - 1], item.text())  # not the line being typed in


class CTextPanel(QWidget):
    """The asset's text values and the texts they name in a language; an edit is a text edit."""

    def __init__(self, texts, languages: tuple[str, ...] = LANGUAGES, group=lambda: []) -> None:
        """``texts(language)``: the game's texts of that language (id -> text); ``languages``: those the game has;
        ``group()``: the GUIDs of the group picked in the tree (its words are counted too)."""
        super().__init__()
        self.m_texts, self.m_group, self.m_doc, self.m_guid = texts, group, None, None
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.m_language = QComboBox()
        self.m_language.addItems(languages)
        row.addWidget(QLabel(tr("common.language")))
        row.addWidget(self.m_language)
        row.addStretch(1)
        layout.addLayout(row)
        self.m_table = QTableWidget(0, 3)
        self.m_table.setHorizontalHeaderLabels([tr("common.value"), tr("assets.text_id"), tr("assets.text")])
        self.m_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.m_table, 1)
        self.m_words = QLabel()
        layout.addWidget(self.m_words)
        self.m_language.currentIndexChanged.connect(lambda _i: self.fill())
        self.m_table.itemChanged.connect(self._edited)

    def set_languages(self, names: tuple[str, ...]) -> None:
        """The languages of the game just opened (the file names differ per game)."""
        keep = self.m_language.currentText()
        self.m_language.blockSignals(True)
        self.m_language.clear()
        self.m_language.addItems(names)
        self.m_language.setCurrentText(keep if keep in names else names[0])
        self.m_language.blockSignals(False)

    def present(self, doc: CAssetDocument, guid: int | None) -> None:
        self.m_doc, self.m_guid = doc, guid
        self.fill()

    def fill(self) -> None:
        self.m_table.blockSignals(True)
        self.m_table.setRowCount(0)
        self.m_words.setText("")
        if self.m_doc is not None and self.m_guid is not None and self.m_doc.exists(self.m_guid):
            lang = self.m_language.currentText()
            game, edited = self.m_texts(lang), self.m_doc.m_changes.m_texts.get(lang, {})
            rows = report.asset_texts(self.m_doc, self.m_guid, game, edited)
            for row_index, row in enumerate(rows):
                self.m_table.insertRow(row_index)
                for column_index, value in enumerate(row):
                    item = QTableWidgetItem(value)
                    if column_index < 2:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.m_table.setItem(row_index, column_index, item)
            self.m_words.setText(self._count(rows, game, edited))
        self.m_table.blockSignals(False)

    def _count(self, rows, game: dict[str, str], edited: dict[str, str]) -> str:
        """The words of this asset's texts, and of the picked group's assets when it is more than this one."""
        text = tr("assets.words_assets_texts", n=report.words_of(rows))
        group = self.m_group()
        if len(group) > 1:
            text += " · " + tr(
                "assets.words_assets_group",
                n=report.group_words(self.m_doc, group, game, edited),
                count=len(group),
            )
        return text

    def _edited(self, item: QTableWidgetItem) -> None:
        if item.column() == 2:
            self.m_doc.set_text(self.m_language.currentText(), self.m_table.item(item.row(), 1).text(), item.text())


class CIconPanel(QWidget):
    """The asset's icon: its file or atlas tile, the picture, and where the game keeps its icons in ``icons.xml``
    (2070, 1404, 1701) the boxes that set the atlas and the tile."""

    def __init__(self, source) -> None:
        """``source``: the ``icons.CIconSource`` of the game open."""
        super().__init__()
        self.m_source = source
        layout = QVBoxLayout(self)
        self.m_path = QLabel(wordWrap=True, textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        self.m_picture = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.m_number = QSpinBox(maximum=0)  # which icon of the entry (0: the first); one past the last adds one
        self.m_atlas, self.m_tile = QSpinBox(maximum=100000), QSpinBox(maximum=100000)
        self.m_set_button = QPushButton(tr("assets.set_icon"))
        self.m_set_button.clicked.connect(self._set)
        self.m_doc: CAssetDocument | None = None
        self.m_guid: int | None = None
        self.m_editor = QWidget()
        row = QHBoxLayout(self.m_editor)
        self.m_number.valueChanged.connect(lambda _n: self._show())
        for text, box in (
            (tr("assets.icon"), self.m_number),
            (tr("assets.atlas_file_id"), self.m_atlas),
            (tr("assets.tile"), self.m_tile),
        ):
            row.addWidget(QLabel(text))
            row.addWidget(box)
        row.addWidget(self.m_set_button)
        row.addStretch(1)
        layout.addWidget(self.m_path)
        layout.addWidget(self.m_editor)
        layout.addWidget(self.m_picture, 1)

    def _set(self) -> None:
        if self.m_doc is not None and self.m_guid is not None:
            self.m_doc.set_icon(self.m_guid, self.m_atlas.value(), self.m_tile.value(), self.m_number.value())

    def present(self, doc: CAssetDocument, guid: int | None) -> None:
        self.m_doc, self.m_guid = doc, guid
        shown = guid is not None and doc.exists(guid)
        self.m_editor.setVisible(self.m_source.editable and shown)
        self.m_number.blockSignals(True)
        self.m_number.setMaximum(self.m_source.count_of(guid) if shown else 0)
        self.m_number.setValue(min(self.m_number.value(), self.m_number.maximum()))
        self.m_number.blockSignals(False)
        self._show()

    def _show(self) -> None:
        doc, guid, position = self.m_doc, self.m_guid, self.m_number.value()
        shown = doc is not None and guid is not None and doc.exists(guid)
        ref = self.m_source.ref_of(guid, position) if shown else None
        if ref is not None:
            self.m_atlas.setValue(ref.file_id)
            self.m_tile.setValue(ref.index)
        key = icons.atlas_key(guid, position) if ref is not None and position else ""
        key = key or (self.m_source.key_of(doc, guid) if shown else "")
        self.m_path.setText(self.m_source.describe(key) if key else tr("assets.asset_names_no_icon_file"))
        pix = self.m_source.picture(key)
        self.m_picture.setPixmap(pix)
        if key and pix.isNull():
            self.m_picture.setText(tr("assets.no_picture_path_games_files"))


class CTemplatePanel(QTreeWidget):
    """The asset's template: its properties and the default values it gives."""

    def __init__(self) -> None:
        super().__init__()
        self.setHeaderLabels([tr("assets.template_value"), tr("assets.default")])
        self.setColumnWidth(0, 320)

    def present(self, doc: CAssetDocument, guid: int | None) -> None:
        self.clear()
        template_spec = doc.m_schema.templates.get(doc.template(guid)) if guid is not None else None
        if template_spec is None:
            self.addTopLevelItem(QTreeWidgetItem([tr("assets.no_template"), ""]))
            return
        top = QTreeWidgetItem([f"{template_spec.name}  ({' / '.join(template_spec.group)})", ""])
        self.addTopLevelItem(top)
        for property_spec in template_spec.properties:
            node = QTreeWidgetItem([property_spec.tag, ""])
            node.addChildren([QTreeWidgetItem(["/".join(path), text]) for path, text in leaves(property_spec)])
            top.addChild(node)
        top.setExpanded(True)


class CUsagePanel(QWidget):
    """Who names the asset (a double click shows that one), and what the change set changed in it."""

    jump = Signal(object)  # a GUID (may exceed a Qt int)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.m_users = QListWidget()
        self.m_diff = QTableWidget(0, 3)
        self.m_diff.setHorizontalHeaderLabels([tr("common.value"), tr("common.game"), tr("assets.edited")])
        self.m_diff.horizontalHeader().setStretchLastSection(True)
        self.m_users_label, self.m_diff_label = QLabel(), QLabel()
        for widget in (self.m_users_label, self.m_users, self.m_diff_label, self.m_diff):
            layout.addWidget(widget)
        self.m_users.itemDoubleClicked.connect(lambda item: self.jump.emit(item.data(Qt.ItemDataRole.UserRole)))

    def present(self, doc: CAssetDocument, guid: int | None) -> None:
        self.m_users.clear()
        self.m_diff.setRowCount(0)
        if guid is None or not doc.exists(guid):
            self.m_users_label.clear()
            self.m_diff_label.clear()
            return
        users = compare.usage(doc, guid)
        self.m_users_label.setText(tr("assets.n_assets_name_guid", n=len(users), guid=guid))
        for user in users[:2000]:
            item = QListWidgetItem(f"{doc.name(user)} ({user}) · {doc.template(user)}")
            item.setData(Qt.ItemDataRole.UserRole, user)
            self.m_users.addItem(item)
        rows = compare.diff_asset(doc, guid)
        self.m_diff_label.setText(
            tr("assets.values_differ_games", n=len(rows)) if guid in doc.m_index.m_by_guid else tr("assets.new")
        )
        for difference in rows:
            row = self.m_diff.rowCount()
            self.m_diff.insertRow(row)
            for column_index, text in enumerate((difference.path, difference.before, difference.after)):
                self.m_diff.setItem(row, column_index, QTableWidgetItem("" if text is None else text))


class CFindingsList(QListWidget):
    """The check's findings; a double click shows the asset."""

    jump = Signal(object)  # a GUID (may exceed a Qt int)

    def __init__(self) -> None:
        super().__init__()
        self.itemDoubleClicked.connect(lambda item: item.data(Qt.ItemDataRole.UserRole) and self.jump.emit(
            item.data(Qt.ItemDataRole.UserRole)))  # fmt: skip

    def present(self, findings) -> None:
        self.clear()
        for finding in findings:
            item = QListWidgetItem(str(finding))
            item.setData(Qt.ItemDataRole.UserRole, finding.guid)
            self.addItem(item)
        if not findings:
            self.addItem(tr("assets.no_findings"))


class CHistoryList(QListWidget):
    """The undo stack: a click goes back (or forward) to that change."""

    def __init__(self) -> None:
        super().__init__()
        self.m_doc = None
        self.itemClicked.connect(self._jump)

    def present(self, doc: CAssetDocument) -> None:
        self.m_doc = doc
        stack = doc.m_undo
        self.clear()
        self.addItem(tr("common.as_opened"))
        self.addItems(
            [
                lab + ("  · " + tr("common.saved") if k + 1 == stack.m_clean else "")
                for k, lab in enumerate(stack.labels())
            ]
        )
        self.setCurrentRow(stack.m_index)

    def _jump(self, item) -> None:
        stack, want = self.m_doc.m_undo, self.row(item)
        while stack.m_index > want and stack.undo():
            pass
        while stack.m_index < want and stack.redo():
            pass
