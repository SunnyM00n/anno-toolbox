"""The UI editor's docks (Qt): the properties of the selected element, the Widget Library palette, the view models,
the sample values, and the states (the timeline of the file's animations, its events and actions).

Each panel shows what the window gives it and reports a wish as a signal; the window turns wishes into ``uidoc``
edits. ``parse`` reads a typed value from the text a user wrote. docs/ui-editor.md has the user's guide.
"""

from __future__ import annotations

import math
import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..formats import phoenixedit
from ..infra.i18n import N_, tr
from . import bindings, fields, library, pickers, viewmodels

TYPE_CODES = {0: fields.BOOL, 1: fields.I32, 2: fields.U32, 3: fields.FLOAT, 4: fields.STRING, 5: fields.VEC2,
              6: fields.VEC3, 13: fields.STRING, 17: fields.U32, 18: fields.U32}  # fmt: skip
ROLE = Qt.ItemDataRole.UserRole
NEW_WIDGETS = (
    ("image", N_("ui.a_picture")),
    ("label", N_("ui.a_text")),
    ("container", N_("ui.widget_file_placed_here")),
    ("video", N_("ui.a_video")),
)
LIBRARY_GROUPS = {  # a library kind -> its tree heading
    "Component": N_("ui.components_n"),
    "Widget type": N_("ui.widget_types_n"),
    "Effect": N_("ui.effects_n"),
    "Enum": N_("ui.enums_n"),
    "Game object": N_("ui.game_objects_n"),
}


def show(value) -> str:
    """A value as the panels write it (and ``parse`` reads it back)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (tuple, list)):
        return ", ".join(show(item) for item in value)
    return str(value)


def whole(text: str) -> int:
    """A whole number from text ("12", "12.0", "1e3"); ``ValueError`` for anything else, "inf" included."""
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(f"not a number: {text}")
    return int(number)


def parse(text: str, code: int, names: dict[int, str] | None = None):
    """A typed value from text: numbers, ``x, y, z``, true/false, a GUID (empty: none), an enum value's name."""
    text = text.strip()
    if code == fields.BOOL:
        return text.lower() in ("true", "1", "yes", "on")
    if code in (fields.ENUM, fields.U32, fields.U8, fields.U16, fields.I32, fields.I64, fields.U64, fields.KEY):
        if names:
            found = next(
                (value_code for value_code, enum_name in names.items() if enum_name.lower() == text.lower()), None
            )
            if found is not None:
                return found
        text = text.split()[0] if text else "0"
        return int(text, 16) if text.lower().startswith("0x") else whole(text)
    if code == fields.FLOAT:
        return float(text or 0)
    if code in (fields.VEC2, fields.VEC3, fields.VEC4):
        component_count = {fields.VEC2: 2, fields.VEC3: 3, fields.VEC4: 4}[code]
        parts = [float(part) for part in re.split(r"[,\s]+", text) if part]
        return tuple((parts + [0.0] * component_count)[:component_count])
    if code == fields.U32_LIST:
        return [int(part, 16) for part in re.split(r"[,\s]+", text) if part]
    if code in fields.REFS or code == fields.ID:
        return text or None
    return text


def _item(parent, texts: list[str], meta=None, is_editable: bool = False, is_dim: bool = False) -> QTreeWidgetItem:
    item = QTreeWidgetItem(parent, texts)
    if meta is not None:
        item.setData(1, ROLE, meta)
    if is_editable:
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
    if is_dim:
        for column in range(len(texts)):
            item.setForeground(column, QBrush(QColor(140, 140, 140)))
    return item


class CPropertyPanel(QTreeWidget):
    """The selected object's fields, its values of its template's and components' properties, its effects and the
    built-in properties a binding can reach; a value is edited in place."""

    edit_field = Signal(object, str, object)  # node, field, value
    edit_color = Signal(object, str, tuple)  # node, field, rgb
    edit_value = Signal(object, object, int, object)  # instance, id path, type code, value
    remove_object = Signal(object, str, object)  # node, list field, object
    pick_reference = Signal(object, str, tuple)  # node, field, the kinds of file it takes

    def __init__(self) -> None:
        super().__init__()
        self.setColumnCount(3)
        self.setHeaderLabels([tr("ui.property"), tr("common.value"), tr("ui.type")])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.itemChanged.connect(self._changed)
        self._m_filling = False

    def show_node(self, node: dict | None, kind: str, namer: library.CNamer | None) -> None:
        self._m_filling = True
        self.clear()
        if node is not None:
            self._fields(node)
            if namer is not None:
                if kind == "container":
                    self._template_values(node, namer)
                self._components(node, namer)
                if kind == "template":
                    self._own_properties(node)
            self._effects(node)
            self._builtins(kind)
        self._m_filling = False

    def _fields(self, node: dict) -> None:
        top = _item(self, [tr("ui.fields"), "", node.get("schema_type", "")])
        top.setExpanded(True)
        for field_row in fields.fields_of(node):
            name = fields.label(field_row)
            if field_row.code == fields.EMBEDDED and field_row.objs and field_row.objs[0]["schema_type"] == "33d396c1":
                palette, entry, rgb = fields.color_of(node, field_row.schema["name"] or field_row.schema["hash"])
                text = show(rgb) + (tr("ui.palette_entry", entry=entry) if palette else "")
                _item(top, [name, text, tr("common.colour_lower")], ("color", node, name), is_editable=True)
                continue
            if field_row.objs or field_row.code in (fields.OBJECTS, fields.OBJECT, fields.EMBEDDED):
                _item(
                    top, [name, tr("ui.n_objects", n=len(field_row.objs)), field_row.kind], is_dim=not field_row.present
                )
                continue
            names = fields.enum_names(name)
            text = show(field_row.value) + (
                f"  {names[field_row.value]}" if isinstance(field_row.value, int) and field_row.value in names else ""
            )
            _item(
                top,
                [name, text if field_row.present else "", field_row.kind],
                ("field", node, field_row),
                field_row.editable,
                is_dim=not field_row.present,
            )

    def _template_values(self, node: dict, namer: library.CNamer) -> None:
        template = fields.get(node, "Template")
        properties = namer.template(template)
        if not properties:
            return
        given = {tuple(fields.get(property_value, "PropertyIdPath", []) or []): fields.get(property_value, "Value")
                 for property_value in phoenixedit.objects(node, "PropertyValues")}  # fmt: skip
        top = _item(self, [tr("ui.widget_files_properties"), "", template or ""])
        top.setExpanded(True)
        for uid, (name, ptype) in sorted(properties.items(), key=lambda entry: entry[1][0].lower()):
            code = TYPE_CODES.get(ptype)
            value = given.get((uid,))
            text = show(value[1]) if isinstance(value, tuple) else tr("ui.its_default")
            kind = fields.ENUMS["PropertyType"].get(ptype, str(ptype))
            _item(top, [name, text, kind], ("value", node, [uid], code), code is not None, is_dim=value is None)

    def _components(self, node: dict, namer: library.CNamer) -> None:
        components = phoenixedit.objects(node, "Components")
        if not components:
            return
        given = {tuple(fields.get(property_value, "PropertyIdPath", []) or []): fields.get(property_value, "Value")
                 for property_value in phoenixedit.objects(node, "PropertyValues")}  # fmt: skip
        top = _item(self, [tr("ui.components"), "", str(len(components))])
        top.setExpanded(True)
        for component in components:
            template = fields.get(component, "ComponentTemplate") or ""
            uid = int(fields.get(component, "UniqueId", 0) or 0)
            item = namer.m_library.m_items.get(template)
            head = _item(top, [phoenixedit.object_name(component) or tr("ui.component_in_brackets"), item.name if item else template[:8], f"{uid:08x}"],
                         ("component", node, component))  # fmt: skip
            if item is not None:
                head.setToolTip(0, item.description)
            for prop_uid, (name, ptype) in namer.template(template).items():
                code = TYPE_CODES.get(ptype)
                value = given.get((uid, prop_uid))
                text = show(value[1]) if isinstance(value, tuple) else tr("ui.its_default")
                _item(head, [name, text, fields.ENUMS["PropertyType"].get(ptype, str(ptype))],
                      ("value", node, [uid, prop_uid], code), code is not None, is_dim=value is None)  # fmt: skip

    def _own_properties(self, root: dict) -> None:
        properties = bindings.custom_properties(root)
        top = _item(self, [tr("ui.custom_properties"), "", str(len(properties))])
        for custom_property in properties:
            property_details = bindings.property_info(custom_property)
            access = fields.ENUMS["PropertyAccessibility"].get(
                property_details["access"], str(property_details["access"])
            )
            default = show(property_details["default"][1]) if isinstance(property_details["default"], tuple) else ""
            _item(
                top,
                [
                    property_details["name"],
                    default,
                    f"{fields.ENUMS['PropertyType'].get(property_details['type'], property_details['type'])} {access}",
                ],
            )

    def _effects(self, node: dict) -> None:
        effects = phoenixedit.objects(node, "Effects")
        if effects:
            top = _item(self, [tr("common.effects"), "", str(len(effects))])
            for effect in effects:
                _item(top, [phoenixedit.object_name(effect) or tr("ui.effect_in_brackets"), fields.get(effect, "EffectTemplate") or ""],
                      ("effect", node, effect))  # fmt: skip

    def show_origins(self, rows: list[tuple[str, str, str]]) -> None:
        """Where the values the selected element is given came from (``gamedataview.origin_rows``), on top."""
        if not rows:
            return
        self._m_filling = True
        top = QTreeWidgetItem([tr("ui.where_values_come_from"), "", str(len(rows))])
        self.insertTopLevelItem(0, top)
        for what, value, origin in rows:
            QTreeWidgetItem(top, [what, value, origin])
        top.setExpanded(True)
        self._m_filling = False

    def _builtins(self, kind: str) -> None:
        top = _item(self, [tr("ui.built_properties_binding_ends"), "", ""])
        for builtin in fields.builtins_of(kind):
            _item(
                top,
                [builtin.name, f"{builtin.uid:08x}", f"{builtin.owner} {builtin.kind} {builtin.vtype}"],
                is_dim=True,
            )

    def _changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._m_filling or column != 1:
            return
        meta = item.data(1, ROLE)
        if not meta:
            return
        text = item.text(1)
        try:
            if meta[0] == "field":
                _, node, field_row = meta
                names = fields.enum_names(fields.label(field_row))
                if names:  # an enum's row reads "value  Name" (``_fields``): the name is no part of an edit
                    text = text.split("  ")[0]
                self.edit_field.emit(
                    node, field_row.schema["name"] or field_row.schema["hash"], parse(text, field_row.code, names)
                )
            elif meta[0] == "color":
                _, node, name = meta
                self.edit_color.emit(node, name, tuple(whole(part) for part in re.split(r"[,\s]+", text) if part)[:3])
            elif meta[0] == "value":
                _, node, path, code = meta
                self.edit_value.emit(node, path, code, parse(text, code))
        except (ValueError, IndexError):
            item.setForeground(1, QBrush(QColor(220, 60, 60)))

    def contextMenuEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        meta = item.data(1, ROLE) if item is not None else None
        if meta and meta[0] == "field" and meta[2].code in fields.REFS:
            name = meta[2].schema["name"] or meta[2].schema["hash"]
            kinds = pickers.kind_of_field(name)
            if kinds:
                menu = QMenu(self)
                menu.addAction(
                    tr("ui.choose_the_name", name=name), lambda: self.pick_reference.emit(meta[1], name, kinds)
                )
                menu.exec(event.globalPos())
            return
        if not meta or meta[0] not in ("component", "effect"):
            return
        menu = QMenu(self)
        field = "Components" if meta[0] == "component" else "Effects"
        label = tr("ui.remove_component") if meta[0] == "component" else tr("ui.remove_effect")
        menu.addAction(label, lambda: self.remove_object.emit(meta[1], field, meta[2]))
        menu.exec(event.globalPos())


class _CPaletteTree(QTreeWidget):
    """The palette's tree: an entry dragged out carries what it adds (into the preview)."""

    def __init__(self) -> None:
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragOnly)

    def mimeData(self, items):
        meta = items[0].data(0, ROLE) if items else None
        return pickers.palette_mime(*meta) if meta else super().mimeData(items)


class CPalettePanel(QWidget):
    """The Widget Library: new instances (image, label, container, video) and the components, widget types,
    effects, enums and game objects the game describes, by folder; a double-click asks to add one."""

    add = Signal(str, str)  # what ("instance", "Component", "Effect" ...), its kind or GUID

    def __init__(self) -> None:
        super().__init__()
        self.m_filter = QLineEdit(placeholderText=tr("ui.filter_library"))
        self.m_tree = _CPaletteTree()
        self.m_tree.setHeaderHidden(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.m_filter)
        layout.addWidget(self.m_tree)
        self.m_filter.textChanged.connect(self._filter)
        self.m_tree.itemDoubleClicked.connect(self._picked)
        self.m_library: library.CLibrary | None = None

    def show_library(self, loaded_library: library.CLibrary | None) -> None:
        self.m_library = loaded_library
        self.m_tree.clear()
        widgets = QTreeWidgetItem(self.m_tree, [tr("ui.new_widgets")])
        for kind, tip in NEW_WIDGETS:
            tree_item = QTreeWidgetItem(widgets, [kind.title()])
            tree_item.setData(0, ROLE, ("instance", kind))
            tree_item.setToolTip(0, tr(tip))
        widgets.setExpanded(True)
        for kind in library.KINDS.values():
            items = loaded_library.of_kind(kind) if loaded_library else []
            top = QTreeWidgetItem(self.m_tree, [tr(LIBRARY_GROUPS[kind], n=len(items))])
            folders: dict[str, QTreeWidgetItem] = {}
            for item in items:
                folder = folders.get(item.folder) or QTreeWidgetItem(top, [item.folder or tr("ui.no_folder")])
                folders[item.folder] = folder
                tree_item = QTreeWidgetItem(folder, [item.name])
                tree_item.setData(0, ROLE, (kind, item.guid))
                props = "\n".join(
                    f"  {entry.name}: {entry.description.splitlines()[0] if entry.description else ''}"
                    for entry in item.properties
                )
                tree_item.setToolTip(0, f"{item.description}\n{props}".strip())

    def _filter(self, text: str) -> None:
        words = text.lower().split()

        def visit(item: QTreeWidgetItem) -> bool:
            own = all(word in item.text(0).lower() or word in item.toolTip(0).lower() for word in words)
            kids = [visit(item.child(i)) for i in range(item.childCount())]
            shown = own or any(kids)
            item.setHidden(not shown)
            if words and any(kids):
                item.setExpanded(True)
            return shown

        for i in range(self.m_tree.topLevelItemCount()):
            visit(self.m_tree.topLevelItem(i))

    def _picked(self, item: QTreeWidgetItem) -> None:
        meta = item.data(0, ROLE)
        if meta:
            self.add.emit(meta[0], meta[1])


class CViewModelPanel(QWidget):
    """What the file can bind to: the scene's data class (a guess from its name), the field ids the file reads with
    the names given to them, every UI data class, and a script list's readouts."""

    name_id = Signal(object, str)  # a field id (32 bits unsigned, beyond a Qt int) named after a class's field
    bind = Signal(object)  # a source path (data object mode, owner id path) to bind to the selected element
    add_readout = Signal(object)  # a readout to add as a custom property
    load_scripts = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.m_filter = QLineEdit(placeholderText=tr("ui.find_class_field"))
        self.m_tree = QTreeWidget()
        self.m_tree.setColumnCount(2)
        self.m_tree.setHeaderLabels([tr("common.name"), tr("ui.type")])
        self.m_tree.itemExpanded.connect(self._expand)
        load = QPushButton(tr("ui.load_script_list"))
        load.clicked.connect(self.load_scripts.emit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.m_filter)
        layout.addWidget(self.m_tree)
        layout.addWidget(load)
        self.m_filter.textChanged.connect(lambda _text: self._fill())
        self.m_models: viewmodels.CViewModels | None = None
        self.m_scripts: viewmodels.CScriptList | None = None
        self.m_guess: viewmodels.SClassDef | None = None
        self.m_reads: list[tuple[str, list[int]]] = []  # (words, id path) of the game object paths the file reads
        self.m_names: dict[int, str] = {}

    def show_models(self, models, guess, reads, names, scripts=None) -> None:
        self.m_models, self.m_guess, self.m_reads, self.m_names = models, guess, reads, names
        self.m_scripts = scripts if scripts is not None else self.m_scripts
        self._fill()

    def _class_item(self, parent, class_def: viewmodels.SClassDef, label: str = "") -> QTreeWidgetItem:
        tree_item = QTreeWidgetItem(parent, [label or class_def.short, class_def.name])
        tree_item.setData(0, ROLE, ("class", class_def.name))
        if class_def.fields:
            QTreeWidgetItem(tree_item, ["..."])
        return tree_item

    def _fill(self) -> None:
        self.m_tree.clear()
        if self.m_guess is not None:
            top = self._class_item(self.m_tree, self.m_guess, tr("ui.scenes_data_name", name=self.m_guess.short))
            top.setFont(0, _bold())
        reads = QTreeWidgetItem(self.m_tree, [tr("ui.field_ids_file_reads", n=len(self.m_reads)), ""])
        for words, path in self.m_reads:
            tree_item = QTreeWidgetItem(reads, [words, " ".join(f"{i:08x}" for i in path)])
            tree_item.setData(0, ROLE, ("read", path))
        reads.setExpanded(True)
        if self.m_models is not None:
            text = self.m_filter.text().strip()
            found = self.m_models.search(text) if text else []
            total = len(self.m_models.m_classes)
            head = (
                tr("ui.ui_data_classes_found", n=total, found=len(found)) if text else tr("ui.ui_data_classes", n=total)
            )
            top = QTreeWidgetItem(self.m_tree, [head, ""])
            for class_def in found[:300]:
                self._class_item(top, class_def)
            top.setExpanded(bool(text))
        if self.m_scripts is not None:
            top = QTreeWidgetItem(self.m_tree, [tr("ui.script_readouts_n", n=len(self.m_scripts.readouts())), ""])
            for readout in self.m_scripts.readouts():
                tree_item = QTreeWidgetItem(top, [readout.call, ", ".join(readout.returns)])
                tree_item.setToolTip(0, readout.doc)
                tree_item.setData(0, ROLE, ("readout", readout))

    def _expand(self, item: QTreeWidgetItem) -> None:
        meta = item.data(0, ROLE)
        if not meta or meta[0] != "class" or item.childCount() != 1 or item.child(0).text(0) != "...":
            return
        item.takeChild(0)
        class_def = self.m_models.m_classes.get(meta[1]) if self.m_models else None
        for member in class_def.fields if class_def else []:
            child_class = self.m_models.m_classes.get(member.child_class or "")
            tree_item = (
                self._class_item(item, child_class, member.name)
                if child_class and member.kind != viewmodels.VALUE
                else QTreeWidgetItem(item, [member.name, ""])
            )
            tree_item.setText(
                1,
                tr("ui.type_kind_settable", type=member.type, kind=member.kind)
                if member.settable
                else tr("ui.type_kind", type=member.type, kind=member.kind),
            )
            tree_item.setData(1, ROLE, ("field", f"{class_def.short}.{member.name}"))

    def contextMenuEvent(self, event) -> None:
        item = self.m_tree.itemAt(self.m_tree.viewport().mapFrom(self, event.pos()))
        if item is None:
            return
        meta, field = item.data(0, ROLE), item.data(1, ROLE)
        menu = QMenu(self)
        if meta and meta[0] == "read":
            menu.addAction(tr("ui.bind_selected_elements_property"), lambda: self.bind.emit(meta[1]))
        if field and field[0] == "field":
            unnamed = [
                (path[-1], read_words) for read_words, path in self.m_reads if path and path[-1] not in self.m_names
            ]
            sub = menu.addMenu(tr("ui.menu.name_id_field", field=field[1]))
            for uid, words in unnamed[:40]:
                sub.addAction(
                    f"{uid:08x} ({words})",
                    lambda unique_id=uid, field_name=field[1]: self.name_id.emit(unique_id, field_name),
                )
            sub.setEnabled(bool(unnamed))
        if meta and meta[0] == "readout":
            menu.addAction(tr("ui.add_custom_property_file"), lambda: self.add_readout.emit(meta[1]))
        if not menu.isEmpty():
            menu.exec(event.globalPos())


def _bold() -> QFont:
    font = QFont()
    font.setBold(True)
    return font


class CSamplePanel(QTableWidget):
    """The values the preview read where the game would give one; a value written here is shown at once."""

    changed = Signal(str, object)
    reset = Signal(str)  # a key's value dropped: the preview makes up a placeholder again

    def __init__(self) -> None:
        super().__init__(0, 2)
        self.setHorizontalHeaderLabels([tr("ui.read_from"), tr("ui.sample_value")])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.itemChanged.connect(self._changed)
        self._m_filling = False

    def show_samples(self, samples: viewmodels.CSamples, describe) -> None:
        self._m_filling = True
        keys = sorted(set(samples.m_seen) | set(samples.m_values))
        self.setRowCount(len(keys))
        for row, key in enumerate(keys):
            name = QTableWidgetItem(describe(key))
            name.setToolTip(key)
            name.setData(ROLE, key)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.setItem(row, 0, name)
            value = QTableWidgetItem(show(samples.m_values.get(key)))
            if key in samples.m_generated:  # a made-up value: shown apart from one set by hand
                value.setForeground(QBrush(QColor(140, 140, 140)))
                font = value.font()
                font.setItalic(True)
                value.setFont(font)
                value.setToolTip(tr("ui.tip.placeholder_preview_made_up_write"))
            self.setItem(row, 1, value)
        self._m_filling = False

    def contextMenuEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        if item is None:
            return
        key = self.item(item.row(), 0).data(ROLE)
        menu = QMenu(self)
        menu.addAction(tr("ui.reset_to_placeholder"), lambda: self.reset.emit(key))
        menu.exec(event.globalPos())

    def _changed(self, item: QTableWidgetItem) -> None:
        if self._m_filling or item.column() != 1:
            return
        key = self.item(item.row(), 0).data(ROLE)
        text = item.text().strip()
        value: object = text
        for kind in (int, float):
            try:
                value = kind(text)
                break
            except ValueError:
                continue
        if text.lower() in ("true", "false"):
            value = text.lower() == "true"
        self.changed.emit(key, value if text else None)


class CStatesPanel(QWidget):
    """The file's animations on a timeline (pick one, scrub or play it), the design view switch, and the events and
    actions the file declares or reacts to."""

    animation = Signal(object, float)  # animation GUID or None, seconds
    design = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.m_combo = QComboBox()
        self.m_slider = QSlider(Qt.Orientation.Horizontal)
        self.m_slider.setRange(0, 1000)
        self.m_play = QPushButton(tr("common.play"))
        self.m_time = QLabel("0.00 s")  # i18n: ok (a number and a unit)
        self.m_design_box = QCheckBox(tr("ui.design_view_show_what_animation"))
        self.m_design_box.setChecked(True)
        self.m_events = QTreeWidget()
        self.m_events.setHeaderLabels([tr("ui.event_action"), tr("ui.what_drives")])
        row = QHBoxLayout()
        for widget in (self.m_combo, self.m_play, self.m_time):
            row.addWidget(widget)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addWidget(self.m_slider)
        layout.addWidget(self.m_design_box)
        layout.addWidget(self.m_events)
        self.m_timer = QTimer(self, interval=33)
        self.m_timer.timeout.connect(self._tick)
        self.m_play.clicked.connect(self._toggle)
        self.m_slider.valueChanged.connect(lambda _value: self._emit())
        self.m_combo.currentIndexChanged.connect(lambda _index: self._emit())
        self.m_design_box.toggled.connect(self.design.emit)
        self.m_anims: list = []
        self.m_stage: QWidget | None = None  # the picture the animation plays in: it stands still while that is hidden

    def show_states(self, anims, graph: bindings.SGraph | None, describe) -> None:
        self.m_anims = anims
        self.m_combo.blockSignals(True)
        self.m_combo.clear()
        self.m_combo.addItem(tr("ui.no_animation"))
        for animation in anims:
            self.m_combo.addItem(
                tr(
                    "ui.name_seconds_s_n_tracks",
                    name=animation.name,
                    seconds=animation.seconds,
                    n=len(animation.tracks),
                )
            )
        self.m_combo.blockSignals(False)
        self.m_events.clear()
        if graph is None:
            return
        for custom_property in graph.properties:
            property_details = bindings.property_info(custom_property)
            if property_details["type"] in (9, 10):
                label = (
                    tr("ui.name_event", name=property_details["name"])
                    if property_details["type"] == 9
                    else tr("ui.name_action", name=property_details["name"])
                )
                QTreeWidgetItem(self.m_events, [label, tr("ui.declared_file")])
        for binding in graph.bindings:
            if any(
                source.kind == "path"
                and source.ids
                and fields.BUILTIN_BY_ID.get(source.ids[-1], None)
                and fields.BUILTIN_BY_ID[source.ids[-1]].kind == "event"
                for source in binding.sources
            ) or binding.operator in ("c32c7054", "130e634e", "599c5545", "a3de9f56"):
                QTreeWidgetItem(
                    self.m_events,
                    [
                        ", ".join(describe(source) for source in binding.sources),
                        f"{binding.op_name} -> {', '.join(describe(source) for source in binding.targets)}",
                    ],
                )

    def current(self):
        i = self.m_combo.currentIndex() - 1
        return self.m_anims[i] if 0 <= i < len(self.m_anims) else None

    def _emit(self) -> None:
        anim = self.current()
        seconds = (anim.seconds if anim else 0.0) * self.m_slider.value() / 1000
        self.m_time.setText(f"{seconds:.2f} s")  # i18n: ok (a number and a unit)
        self.animation.emit(anim.guid if anim else None, seconds)

    def _toggle(self) -> None:
        if self.m_timer.isActive():
            self.m_timer.stop()
            self.m_play.setText(tr("common.play"))
        elif self.current() is not None:
            self.m_timer.start()
            self.m_play.setText(tr("ui.pause"))

    def _tick(self) -> None:
        anim = self.current()
        if anim is None or anim.seconds <= 0:
            self._toggle()
            return
        if self.m_stage is not None and not self.m_stage.isVisible():
            return  # another app or page is shown: a step would lay out a picture nobody sees
        step = int(1000 * 0.033 / anim.seconds)
        self.m_slider.setValue((self.m_slider.value() + max(1, step)) % 1001)
