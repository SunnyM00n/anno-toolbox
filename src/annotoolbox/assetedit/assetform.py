"""The Properties tab: an asset's values as a form generated from the game's schema, one collapsible block per
property, its fields made when the block is first opened. Each field shows where its value comes from (own, a base
asset, the template, the defaults) and offers "reset" where the asset sets it.

``CAssetForm.show_asset(doc, guid)`` fills it; edits go to the ``CAssetDocument``. Field kinds follow
``schema.SValueSpec.kind``: text and numbers in a line, a flag as a box, a choice from its data set, flags as a
set of boxes, an asset reference with a jump, a list or an array as a table, an inline asset as XML.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..suite import links
from . import picker, visibility
from .assetdoc import CAssetDocument
from .schema import SPropertySpec, SValueSpec
from .values import OWN, CLayers
from .xmltree import parse, to_text

ORIGINS = {
    "own": N_("assets.own"),
    "base": N_("assets.base_source"),
    "template": N_("assets.template"),
    "default": N_("assets.default_lower"),
    "none": "",
}
OPEN_FIRST = 3  # blocks open when an asset is shown
PLAIN = ""  # the one column of a list or an array whose items the schema gives no values: the item's own text


def origin_text(origin: str, source: int | None) -> str:
    text = ORIGINS[origin]
    return tr(text, source=source) if text else ""


class _CButtons(QWidget):
    """Two or more buttons side by side beside a field."""

    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)


class CBlock(QWidget):
    """One property: a header button and, once opened, its fields."""

    def __init__(self, form: CAssetForm, name: str) -> None:
        super().__init__()
        self.m_form, self.m_name, self.m_built = form, name, False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        property_spec = form.m_doc.m_schema.properties.get(name)
        self.m_head = QToolButton(text=name, checkable=True, objectName="blockHead")
        self.m_head.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.m_head.setArrowType(Qt.ArrowType.RightArrow)
        self.m_head.setToolTip(property_spec.description if property_spec and property_spec.description else name)
        self.m_head.toggled.connect(self.set_open)
        self.m_body = QWidget()
        self.m_body_layout = QFormLayout(self.m_body)
        self.m_body.hide()
        layout.addWidget(self.m_head)
        layout.addWidget(self.m_body)

    def set_open(self, is_open: bool) -> None:
        self.m_head.setChecked(is_open)
        self.m_head.setArrowType(Qt.ArrowType.DownArrow if is_open else Qt.ArrowType.RightArrow)
        if is_open and not self.m_built:
            self.m_built = True
            self.m_form.fill_block(self)
        self.m_body.setVisible(is_open)
        (self.m_form.m_opened.add if is_open else self.m_form.m_opened.discard)(self.m_name)


class CAssetForm(QScrollArea):
    """The form of the asset shown; ``jump`` asks the window to show another asset."""

    jump = Signal(object)  # a GUID (may exceed a Qt int)

    def __init__(self) -> None:
        super().__init__(widgetResizable=True, objectName="assetForm")
        self.m_doc: CAssetDocument | None = None
        self.m_guid: int | None = None
        self.m_layers: CLayers | None = None
        self.m_expert = False
        self.m_secondary = False  # also the values the template calls secondary
        self.m_conditional = False  # also the values whose visibility conditions fail
        self.m_original = False  # beside an edited value, the game's own
        self.m_hidden: set[str] = set()  # the open asset's template's secondary values
        self.m_original_layers: CLayers | None = None
        self.m_opened: set[str] = set()
        self._show(QLabel(tr("assets.pick_asset_tree"), alignment=Qt.AlignmentFlag.AlignCenter))

    def _show(self, widget: QWidget) -> None:
        old = self.takeWidget()
        if old is not None:
            old.deleteLater()
        self.setWidget(widget)

    def show_asset(self, doc: CAssetDocument, guid: int | None) -> None:
        """Show ``guid`` (None: nothing); the same asset again keeps its open blocks."""
        self._commit_pending()
        if guid != self.m_guid:
            self.m_opened = set()
        self.m_doc, self.m_guid = doc, guid
        if guid is None or not doc.exists(guid):
            self._show(QLabel(tr("assets.pick_asset_tree"), alignment=Qt.AlignmentFlag.AlignCenter))
            return
        self.m_layers = doc.layers(guid)
        self.m_original_layers = doc.layers(guid, original=True) if self.m_original else None
        template = doc.m_schema.templates.get(doc.template(guid))
        self.m_hidden = set(template.hidden) if template is not None else set()
        box = QWidget()
        layout = QVBoxLayout(box)
        base = doc.base_of(guid)
        head = QLabel(  # i18n: ok (markup around names)
            f"<b>{escape(doc.name(guid))}</b> · {guid} · {escape(doc.template(guid))}"
            + (" · " + tr("assets.base_guid", guid=base) if base else "")
        )
        layout.addWidget(head)
        self.m_blocks: dict[str, CBlock] = {}
        first_time = not self.m_opened
        for k, name in enumerate(self.m_layers.property_names()):
            block = CBlock(self, name)
            self.m_blocks[name] = block
            layout.addWidget(block)
            if name in self.m_opened or (first_time and k < OPEN_FIRST):
                block.set_open(True)
        layout.addStretch(1)
        self._show(box)

    def _commit_pending(self) -> None:
        """A field still being typed in takes its text now, while the form is still its asset's: left to the rebuild,
        the text would go to the asset shown next."""
        focus, shown = QApplication.focusWidget(), self.widget()
        if focus is not None and shown is not None and shown.isAncestorOf(focus):
            focus.clearFocus()

    def refresh(self) -> None:
        if self.m_doc is not None:
            keep = self.verticalScrollBar().value()
            self.show_asset(self.m_doc, self.m_guid)
            self.verticalScrollBar().setValue(keep)

    # the fields
    def fill_block(self, block: CBlock) -> None:
        property_spec = self.m_doc.m_schema.properties.get(block.m_name)
        if property_spec is None:
            self._raw_rows(block.m_body_layout, (block.m_name,))
            return
        self._rows(block.m_body_layout, property_spec, (block.m_name,))

    def _rows(self, form: QFormLayout, node: SPropertySpec | SValueSpec, path: tuple[str, ...]) -> None:
        specs = node.values if isinstance(node, SPropertySpec) else node.items
        left_out = {"secondary": 0, "condition": 0}
        for spec in specs:
            if spec.expert and not self.m_expert:
                continue
            here = path + (spec.name,)
            reason = self._left_out(here)
            if reason:
                left_out[reason] += 1
                continue
            if spec.kind == "struct":
                sub = QFormLayout()
                self._rows(sub, spec, here)
                box = QWidget()
                box.setLayout(sub)
                form.addRow(self._label(spec, here), box)
            else:
                form.addRow(self._label(spec, here), self.field(spec, here))
        if any(left_out.values()):
            form.addRow(QLabel(f"<i>{self._left_out_text(left_out)}</i>"))  # i18n: ok (markup around a message)
        known = {row_spec.name for row_spec in specs}
        found = self.m_layers.lookup(path).element
        for extra in [child for child in (found if found is not None else []) if child.tag not in known]:
            tag = QLabel(f"<i>{extra.tag}</i>")  # i18n: ok (markup around a name)
            form.addRow(tag, self._xml_field(path + (extra.tag,)))

    def _left_out(self, path: tuple[str, ...]) -> str:
        """Why a top-level value is not in the form: "secondary" (its template's) or "condition" (1800's visibility
        conditions fail), else empty; the View menu's switches bring both back."""
        if len(path) != 2:
            return ""
        if path[1] in self.m_hidden and not self.m_secondary:
            return "secondary"
        if not self.m_conditional and not visibility.is_shown(self.m_doc.m_schema, self.m_layers, *path):
            return "condition"
        return ""

    @staticmethod
    def _left_out_text(left_out: dict[str, int]) -> str:
        parts = []
        if left_out["secondary"]:
            parts.append(tr("assets.n_secondary_template_values", n=left_out["secondary"]))
        if left_out["condition"]:
            parts.append(tr("assets.values_whose_conditions_not_hold", n=left_out["condition"]))
        return tr("assets.not_shown_view_menu", parts=", ".join(parts))

    def _raw_rows(self, form: QFormLayout, path: tuple[str, ...]) -> None:
        form.addRow(QLabel(tr("assets.not_schema")), self._xml_field(path))

    def _label(self, spec: SValueSpec, path: tuple[str, ...]) -> QWidget:
        found = self.m_layers.lookup(path)
        label = QLabel(f"<b>{spec.name}</b>" if found.origin == OWN else spec.name)
        tip = [
            spec.description or "",
            f"{spec.type}" + (" " + tr("assets.of_data_set", data_set=spec.data_set) if spec.data_set else ""),
        ]
        if len(path) == 2 and path[1] in self.m_hidden:
            tip.append(tr("assets.secondary_value_assets_template"))
        if len(path) == 2 and not visibility.is_shown(self.m_doc.m_schema, self.m_layers, *path):
            tip.append(tr("assets.visibility_conditions_not_hold_asset"))
        if spec.borrowed:
            tip.append(tr("assets.description_anno_1800s"))
        if spec.written:
            tip.append(tr("assets.description_written_editor_not_taken"))
        label.setToolTip("\n".join(part for part in tip if part))
        label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        label.customContextMenuRequested.connect(lambda _at, path=path, label=label: self._menu(path, label))
        return label

    def _menu(self, path: tuple[str, ...], label: QWidget) -> None:
        menu = QMenu(self)
        action = menu.addAction(tr("assets.reset_value"), lambda: self.m_doc.reset_value(self.m_guid, "/".join(path)))
        action.setEnabled(self.m_layers.lookup(path).origin == OWN)
        menu.exec(label.mapToGlobal(label.rect().bottomLeft()))

    def field(self, spec: SValueSpec, path: tuple[str, ...]) -> QWidget:
        found = self.m_layers.lookup(path)
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        kind = spec.kind
        if kind in ("list", "array"):
            editor = self._table(spec, path, found.element)
        elif kind == "nested":
            editor = self._xml_field(path)
        elif kind == "bool":
            editor = QCheckBox(checked=found.text in ("1", "True", "true"))
            editor.toggled.connect(lambda is_on, path=path: self._set(path, "1" if is_on else "0"))
        elif kind == "choice":
            editor = QComboBox(editable=True)
            editor.addItems(self._names(spec))
            editor.setCurrentText(found.text)
            editor.activated.connect(lambda _i, editor=editor, path=path: self._set(path, editor.currentText()))
        else:
            editor = QLineEdit(found.text)
            editor.setReadOnly(spec.read_only)
            editor.editingFinished.connect(lambda editor=editor, path=path: self._set(path, editor.text()))
            if kind == "flags":
                line.addWidget(editor, 1)
                editor = self._flags_button(spec, editor, path)
            elif kind == "asset":
                line.addWidget(editor, 1)
                editor = self._asset_buttons(editor, spec, path)
            elif spec.type == "Color":
                line.addWidget(editor, 1)
                editor = self._colour_button(editor, path)
            elif spec.type == "FileName" and self.m_doc.m_files is not None:
                line.addWidget(editor, 1)
                editor = self._file_button(editor, spec, path)
        editor.setEnabled(not spec.read_only)
        line.addWidget(editor, 0 if isinstance(editor, (QToolButton, _CButtons)) else 1)
        badge = QLabel(origin_text(found.origin, found.source), objectName="origin")
        badge.setMinimumWidth(70)
        line.addWidget(badge)
        was = self._was(path, kind)
        if was is not None:
            line.addWidget(QLabel(was, objectName="original", toolTip=tr("assets.tip.games_own_value")))
        reset = QToolButton(text="↺", toolTip=tr("assets.tip.reset_value_what_asset_inherits"))
        reset.setEnabled(found.origin == OWN)
        reset.clicked.connect(lambda _c=False, path=path: self.m_doc.reset_value(self.m_guid, "/".join(path)))
        line.addWidget(reset)
        return row

    def _was(self, path: tuple[str, ...], kind: str) -> str | None:
        """What the game's own data has where this asset's value differs from it (View > Original data), else None."""
        if self.m_original_layers is None:
            return None
        before, now = self.m_original_layers.lookup(path), self.m_layers.lookup(path)
        if kind in ("list", "array", "nested"):
            same = (to_text(before.element) if before.element is not None else "") == (
                to_text(now.element) if now.element is not None else ""
            )
            return None if same else tr("assets.game_differs")
        return (
            None
            if before.text == now.text
            else tr("assets.game_value", value=before.text or tr("common.none_in_brackets"))
        )

    def _names(self, spec: SValueSpec) -> list[str]:
        data_set = self.m_doc.m_schema.data_sets.get(spec.data_set)
        return data_set.names if data_set else []

    def _set(self, path: tuple[str, ...], text: str) -> None:
        if self.m_layers.lookup(path).text != text:
            self.m_doc.set_value(self.m_guid, "/".join(path), text)

    def _flags_button(self, spec: SValueSpec, edit: QLineEdit, path: tuple[str, ...]) -> QWidget:
        button = QToolButton(text="…", toolTip=tr("assets.tip.pick_flags"))
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        selected = set(filter(None, edit.text().split(";")))
        for name in self._names(spec):
            flag_action = menu.addAction(name)
            flag_action.setCheckable(True)
            flag_action.setChecked(name in selected)
        menu.triggered.connect(
            lambda _a: self._set(path, ";".join(action.text() for action in menu.actions() if action.isChecked()))
        )
        button.setMenu(menu)
        return button

    def _jump_button(self, edit: QLineEdit) -> QWidget:
        button = QToolButton(text="→", toolTip=tr("assets.tip.show_asset"))
        button.clicked.connect(lambda: edit.text().strip().isdigit() and self.jump.emit(int(edit.text())))
        if edit.text().strip().isdigit() and self.m_doc.exists(int(edit.text())):
            edit.setToolTip(self.m_doc.name(int(edit.text())))
        return button

    def _asset_buttons(self, edit: QLineEdit, spec: SValueSpec, path: tuple[str, ...]) -> QWidget:
        """The picker (a search over the assets the value may name) and the jump to the one named."""
        pick = QToolButton(text="…", toolTip=tr("assets.tip.pick_asset_value_name"))

        def choose() -> None:
            guid = picker.pick_asset(self, self.m_doc, spec)
            if guid is not None:
                edit.setText(str(guid))
                self._set(path, str(guid))

        pick.clicked.connect(lambda _c=False: choose())
        box = _CButtons()
        box.layout().addWidget(pick)
        box.layout().addWidget(self._jump_button(edit))
        return box

    def _colour_button(self, edit: QLineEdit, path: tuple[str, ...]) -> QWidget:
        button = QToolButton(text="…", toolTip=tr("assets.tip.pick_colour_alpha_included"))
        swatch = picker.from_argb(edit.text())
        button.setStyleSheet(f"QToolButton {{ border-left: 12px solid {swatch.name()}; }}")

        def choose() -> None:
            text = picker.pick_colour(self, edit.text())
            if text is not None:
                edit.setText(text)
                self._set(path, text)

        button.clicked.connect(lambda _c=False: choose())
        return button

    def _file_button(self, edit: QLineEdit, spec: SValueSpec, path: tuple[str, ...]) -> QWidget:
        button = QToolButton(text="…", toolTip=tr("assets.tip.pick_one_games_files"))

        def choose() -> None:
            found = picker.pick_file(self, self.m_doc.m_files.paths, spec)
            if found is not None:
                edit.setText(found)
                self._set(path, found)

        button.clicked.connect(lambda _c=False: choose())
        if not links.available(links.VIEW_FILE):
            return button
        view = QToolButton(text=tr("assets.view"), toolTip=tr("assets.tip.show_file_viewer"))
        view.clicked.connect(
            lambda _c=False: edit.text().strip() and links.view_file(self.m_doc.m_changes.m_game, edit.text())
        )
        box = _CButtons()
        export = QToolButton(text=tr("common.export"), toolTip=tr("assets.tip.export_file_stored_converted_viewer"))
        export.clicked.connect(
            lambda _c=False: (
                edit.text().strip() and links.export_files(self.m_doc.m_changes.m_game, [edit.text().strip()])
            )
        )
        box.layout().addWidget(button)
        box.layout().addWidget(view)
        box.layout().addWidget(export)
        return box

    def _xml_field(self, path: tuple[str, ...]) -> QWidget:
        """An inline asset or a value the schema does not know, as its XML; editing it replaces the block."""
        found = self.m_layers.lookup(path)
        edit = QPlainTextEdit(to_text(found.element) if found.element is not None else f"<{path[-1]} />")
        edit.setFixedHeight(90)
        apply = QPushButton(tr("common.apply"))

        def done() -> None:
            try:
                element = parse(edit.toPlainText())
            except ET.ParseError as error:
                apply.setToolTip(tr("assets.tip.not_xml_exc", exc=error))
                return
            self.m_doc.set_block(self.m_guid, "/".join(path), element)

        apply.clicked.connect(done)
        box = QWidget()
        line = QHBoxLayout(box)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(edit, 1)
        line.addWidget(apply)
        return box

    def _table(self, spec: SValueSpec, path: tuple[str, ...], element: ET.Element | None) -> QWidget:
        """A list (one row per ``Item``) or an array (one row per data set item) as a table; any edit, an added or a
        removed row replaces the whole block (a list cannot be patched shorter)."""
        column_names = [item.name for item in spec.items] or [PLAIN]
        if spec.kind == "array":
            rows = self._names(spec) or [child.tag for child in (element if element is not None else [])]
            items = [element.find(row) if element is not None else None for row in rows]
        else:
            items = list(element.findall("Item")) if element is not None else []
            rows = [str(k + 1) for k in range(len(items))]
        table = _filled_table(rows, column_names, items)
        table.itemChanged.connect(
            lambda _i: self.m_doc.set_block(
                self.m_guid, "/".join(path), _rebuilt_block(spec, path, element, table, rows, column_names)
            )
        )
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(table)
        if spec.kind == "list":
            buttons = QHBoxLayout()
            add, remove = QPushButton(tr("assets.add_item")), QPushButton(tr("assets.remove_item"))
            add.clicked.connect(lambda: self._add_item(path, element, spec))
            remove.clicked.connect(lambda: self._remove_item(path, element, table.currentRow()))
            buttons.addWidget(add)
            buttons.addWidget(remove)
            buttons.addStretch(1)
            layout.addLayout(buttons)
        return box

    def _add_item(self, path: tuple[str, ...], element: ET.Element | None, spec: SValueSpec) -> None:
        out = copy.deepcopy(element) if element is not None else ET.Element(path[-1])
        item = ET.SubElement(out, "Item")
        defaults = self.m_doc.m_schema.container_defaults.get(path[0])
        sample = defaults.find("/".join(path[1:])) if defaults is not None and len(path) > 1 else None
        for child in sample if sample is not None else []:  # a new item's defaults (DefaultContainerValues)
            item.append(copy.deepcopy(child))
        self.m_doc.set_block(self.m_guid, "/".join(path), out, label=tr("assets.name_item_added", name=path[-1]))

    def _remove_item(self, path: tuple[str, ...], element: ET.Element | None, row: int) -> None:
        items = element.findall("Item") if element is not None else []
        if not 0 <= row < len(items):
            return
        out = copy.deepcopy(element)
        out.remove(out.findall("Item")[row])
        self.m_doc.set_block(self.m_guid, "/".join(path), out, label=tr("assets.name_item_removed", name=path[-1]))


def _filled_table(rows: list[str], column_names: list[str], items: list[ET.Element | None]) -> QTableWidget:
    """One table row per item, one column per field; a nested block shows as XML and cannot be edited in a cell."""
    table = QTableWidget(len(rows), len(column_names))
    table.setHorizontalHeaderLabels([column_name or tr("common.value_lower") for column_name in column_names])
    table.setVerticalHeaderLabels(rows)
    for row_index, item in enumerate(items):
        for column_index, column_name in enumerate(column_names):
            cell = item if column_name == PLAIN else item.find(column_name) if item is not None else None
            nested = cell is not None and len(cell) > 0
            text = to_text(cell) if nested else (cell.text or "").strip() if cell is not None else ""
            widget = QTableWidgetItem(text)
            if nested:
                widget.setFlags(widget.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_index, column_index, widget)
    table.setFixedHeight(min(260, 34 + 26 * max(1, len(rows))))
    return table


def _rebuilt_block(
    spec: SValueSpec, path: tuple[str, ...], element: ET.Element | None, table: QTableWidget, rows: list[str],
    column_names: list[str],
) -> ET.Element:  # fmt: skip
    """The block the table now says: a copy of ``element`` with the edited cells written into it. What a cell does
    not show (a nested block, a value the schema does not know) stays as the copy has it."""
    out = copy.deepcopy(element) if element is not None else ET.Element(path[-1])
    out.tag = path[-1]
    is_list = spec.kind == "list"
    items = out.findall("Item") if is_list else []
    for row_index in range(table.rowCount()):
        node = (items[row_index] if row_index < len(items) else None) if is_list else out.find(rows[row_index])
        if node is None:
            node = ET.SubElement(out, "Item" if is_list else rows[row_index])
        for column_index, column_name in enumerate(column_names):
            cell = table.item(row_index, column_index)
            if cell is not None and not cell.flags() & Qt.ItemFlag.ItemIsEditable:
                continue  # kept from the copy
            _write_cell(node, column_name, cell.text() if cell is not None else "", drop_empty=is_list)
    return out


def _write_cell(node: ET.Element, column_name: str, text: str, drop_empty: bool) -> None:
    """One cell's text into its row's element: the child ``col`` (made when there is text), or the element's own
    text for ``PLAIN``; ``drop_empty``: an emptied child is taken out (a list item lists only what it sets)."""
    if column_name == PLAIN:
        node.text = text
        return
    child = node.find(column_name)
    if child is None and text:
        child = ET.SubElement(node, column_name)
    if child is None:
        return
    if drop_empty and not text:
        node.remove(child)
    else:
        child.text = text
