"""The UI editor's docks (Qt): the properties of the selected element, the Widget Library palette, the view models,
the sample values, and the states (the timeline of the file's animations, its events and actions).

Each panel shows what the window gives it and reports a wish as a signal; the window turns wishes into ``uidoc``
edits. ``parse`` reads a typed value from the text a user wrote. docs/ui-editor.md has the user's guide.
"""

from __future__ import annotations

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
    ("image", N_("a picture")),
    ("label", N_("a text")),
    ("container", N_("a widget file placed here")),
    ("video", N_("a video")),
)
LIBRARY_GROUPS = {  # a library kind -> its tree heading
    "Component": N_("Components ({n})"),
    "Widget type": N_("Widget types ({n})"),
    "Effect": N_("Effects ({n})"),
    "Enum": N_("Enums ({n})"),
    "Game object": N_("Game objects ({n})"),
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
        return ", ".join(show(v) for v in value)
    return str(value)


def parse(text: str, code: int, names: dict[int, str] | None = None):
    """A typed value from text: numbers, ``x, y, z``, true/false, a GUID (empty: none), an enum value's name."""
    text = text.strip()
    if code == fields.BOOL:
        return text.lower() in ("true", "1", "yes", "on")
    if code in (fields.ENUM, fields.U32, fields.U8, fields.U16, fields.I32, fields.I64, fields.U64, fields.KEY):
        if names:
            found = next((k for k, v in names.items() if v.lower() == text.lower()), None)
            if found is not None:
                return found
        text = text.split()[0] if text else "0"
        return int(text, 16) if text.lower().startswith("0x") else int(float(text))
    if code == fields.FLOAT:
        return float(text or 0)
    if code in (fields.VEC2, fields.VEC3, fields.VEC4):
        n = {fields.VEC2: 2, fields.VEC3: 3, fields.VEC4: 4}[code]
        parts = [float(p) for p in re.split(r"[,\s]+", text) if p]
        return tuple((parts + [0.0] * n)[:n])
    if code == fields.U32_LIST:
        return [int(p, 16) for p in re.split(r"[,\s]+", text) if p]
    if code in fields.REFS or code == fields.ID:
        return text or None
    return text


def _item(parent, texts: list[str], meta=None, editable: bool = False, dim: bool = False) -> QTreeWidgetItem:
    it = QTreeWidgetItem(parent, texts)
    if meta is not None:
        it.setData(1, ROLE, meta)
    if editable:
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
    if dim:
        for c in range(len(texts)):
            it.setForeground(c, QBrush(QColor(140, 140, 140)))
    return it


class PropertyPanel(QTreeWidget):
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
        self.setHeaderLabels([tr("Property"), tr("Value"), tr("Type")])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.itemChanged.connect(self._changed)
        self._filling = False

    def show_node(self, node: dict | None, kind: str, namer: library.Namer | None) -> None:
        self._filling = True
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
        self._filling = False

    def _fields(self, node: dict) -> None:
        top = _item(self, [tr("Fields"), "", node.get("schema_type", "")])
        top.setExpanded(True)
        for f in fields.fields_of(node):
            name = fields.label(f)
            if f.code == fields.EMBEDDED and f.objs and f.objs[0]["schema_type"] == "33d396c1":
                pal, entry, rgb = fields.color_of(node, f.schema["name"] or f.schema["hash"])
                text = show(rgb) + (tr("  (palette entry {entry:08x})", entry=entry) if pal else "")
                _item(top, [name, text, tr("colour")], ("color", node, name), editable=True)
                continue
            if f.objs or f.code in (fields.OBJECTS, fields.OBJECT, fields.EMBEDDED):
                _item(top, [name, tr("{n} objects", n=len(f.objs)), f.kind], dim=not f.present)
                continue
            names = fields.enum_names(name)
            text = show(f.value) + (f"  {names[f.value]}" if isinstance(f.value, int) and f.value in names else "")
            _item(top, [name, text if f.present else "", f.kind], ("field", node, f), f.editable, dim=not f.present)

    def _template_values(self, node: dict, namer: library.Namer) -> None:
        tpl = fields.get(node, "Template")
        props = namer.template(tpl)
        if not props:
            return
        given = {tuple(fields.get(pv, "PropertyIdPath", []) or []): fields.get(pv, "Value")
                 for pv in phoenixedit.objects(node, "PropertyValues")}  # fmt: skip
        top = _item(self, [tr("Its widget file's properties"), "", tpl or ""])
        top.setExpanded(True)
        for uid, (name, ptype) in sorted(props.items(), key=lambda kv: kv[1][0].lower()):
            code = TYPE_CODES.get(ptype)
            value = given.get((uid,))
            text = show(value[1]) if isinstance(value, tuple) else tr("(its default)")
            kind = fields.ENUMS["PropertyType"].get(ptype, str(ptype))
            _item(top, [name, text, kind], ("value", node, [uid], code), code is not None, dim=value is None)

    def _components(self, node: dict, namer: library.Namer) -> None:
        comps = phoenixedit.objects(node, "Components")
        if not comps:
            return
        given = {tuple(fields.get(pv, "PropertyIdPath", []) or []): fields.get(pv, "Value")
                 for pv in phoenixedit.objects(node, "PropertyValues")}  # fmt: skip
        top = _item(self, [tr("Components"), "", str(len(comps))])
        top.setExpanded(True)
        for comp in comps:
            tpl = fields.get(comp, "ComponentTemplate") or ""
            uid = int(fields.get(comp, "UniqueId", 0) or 0)
            item = namer.library.items.get(tpl)
            head = _item(top, [phoenixedit.object_name(comp) or tr("(component)"), item.name if item else tpl[:8], f"{uid:08x}"],
                         ("component", node, comp))  # fmt: skip
            if item is not None:
                head.setToolTip(0, item.description)
            for prop_uid, (name, ptype) in namer.template(tpl).items():
                code = TYPE_CODES.get(ptype)
                value = given.get((uid, prop_uid))
                text = show(value[1]) if isinstance(value, tuple) else tr("(its default)")
                _item(head, [name, text, fields.ENUMS["PropertyType"].get(ptype, str(ptype))],
                      ("value", node, [uid, prop_uid], code), code is not None, dim=value is None)  # fmt: skip

    def _own_properties(self, root: dict) -> None:
        props = bindings.custom_properties(root)
        top = _item(self, [tr("Custom properties"), "", str(len(props))])
        for p in props:
            info = bindings.property_info(p)
            access = fields.ENUMS["PropertyAccessibility"].get(info["access"], str(info["access"]))
            default = show(info["default"][1]) if isinstance(info["default"], tuple) else ""
            _item(
                top, [info["name"], default, f"{fields.ENUMS['PropertyType'].get(info['type'], info['type'])} {access}"]
            )

    def _effects(self, node: dict) -> None:
        effects = phoenixedit.objects(node, "Effects")
        if effects:
            top = _item(self, [tr("Effects"), "", str(len(effects))])
            for e in effects:
                _item(top, [phoenixedit.object_name(e) or tr("(effect)"), fields.get(e, "EffectTemplate") or ""],
                      ("effect", node, e))  # fmt: skip

    def _builtins(self, kind: str) -> None:
        top = _item(self, [tr("Built-in properties (binding ends)"), "", ""])
        for b in fields.builtins_of(kind):
            _item(top, [b.name, f"{b.uid:08x}", f"{b.owner} {b.kind} {b.vtype}"], dim=True)

    def _changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 1:
            return
        meta = item.data(1, ROLE)
        if not meta:
            return
        text = item.text(1).split("  ")[0]
        try:
            if meta[0] == "field":
                _, node, f = meta
                self.edit_field.emit(
                    node, f.schema["name"] or f.schema["hash"], parse(text, f.code, fields.enum_names(fields.label(f)))
                )
            elif meta[0] == "color":
                _, node, name = meta
                self.edit_color.emit(node, name, tuple(int(float(v)) for v in re.split(r"[,\s]+", text) if v)[:3])
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
                    tr("Choose the {name}...", name=name), lambda: self.pick_reference.emit(meta[1], name, kinds)
                )
                menu.exec(event.globalPos())
            return
        if not meta or meta[0] not in ("component", "effect"):
            return
        menu = QMenu(self)
        field = "Components" if meta[0] == "component" else "Effects"
        label = tr("Remove this component") if meta[0] == "component" else tr("Remove this effect")
        menu.addAction(label, lambda: self.remove_object.emit(meta[1], field, meta[2]))
        menu.exec(event.globalPos())


class _PaletteTree(QTreeWidget):
    """The palette's tree: an entry dragged out carries what it adds (into the preview)."""

    def __init__(self) -> None:
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragOnly)

    def mimeData(self, items):
        meta = items[0].data(0, ROLE) if items else None
        return pickers.palette_mime(*meta) if meta else super().mimeData(items)


class PalettePanel(QWidget):
    """The Widget Library: new instances (image, label, container, video) and the components, widget types,
    effects, enums and game objects the game describes, by folder; a double-click asks to add one."""

    add = Signal(str, str)  # what ("instance", "Component", "Effect" ...), its kind or GUID

    def __init__(self) -> None:
        super().__init__()
        self.filter = QLineEdit(placeholderText=tr("filter the library"))
        self.tree = _PaletteTree()
        self.tree.setHeaderHidden(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.filter)
        lay.addWidget(self.tree)
        self.filter.textChanged.connect(self._filter)
        self.tree.itemDoubleClicked.connect(self._picked)
        self.library: library.Library | None = None

    def show_library(self, lib: library.Library | None) -> None:
        self.library = lib
        self.tree.clear()
        widgets = QTreeWidgetItem(self.tree, [tr("New widgets")])
        for kind, tip in NEW_WIDGETS:
            it = QTreeWidgetItem(widgets, [kind.title()])
            it.setData(0, ROLE, ("instance", kind))
            it.setToolTip(0, tr(tip))
        widgets.setExpanded(True)
        for kind in library.KINDS.values():
            items = lib.of_kind(kind) if lib else []
            top = QTreeWidgetItem(self.tree, [tr(LIBRARY_GROUPS[kind], n=len(items))])
            folders: dict[str, QTreeWidgetItem] = {}
            for item in items:
                folder = folders.get(item.folder) or QTreeWidgetItem(top, [item.folder or tr("(no folder)")])
                folders[item.folder] = folder
                it = QTreeWidgetItem(folder, [item.name])
                it.setData(0, ROLE, (kind, item.guid))
                props = "\n".join(
                    f"  {p.name}: {p.description.splitlines()[0] if p.description else ''}" for p in item.properties
                )
                it.setToolTip(0, f"{item.description}\n{props}".strip())

    def _filter(self, text: str) -> None:
        words = text.lower().split()

        def visit(item: QTreeWidgetItem) -> bool:
            own = all(w in item.text(0).lower() or w in item.toolTip(0).lower() for w in words)
            kids = [visit(item.child(i)) for i in range(item.childCount())]
            shown = own or any(kids)
            item.setHidden(not shown)
            if words and any(kids):
                item.setExpanded(True)
            return shown

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def _picked(self, item: QTreeWidgetItem) -> None:
        meta = item.data(0, ROLE)
        if meta:
            self.add.emit(meta[0], meta[1])


class ViewModelPanel(QWidget):
    """What the file can bind to: the scene's data class (a guess from its name), the field ids the file reads with
    the names given to them, every UI data class, and a script list's readouts."""

    name_id = Signal(int, str)  # a field id named after a class's field
    bind = Signal(object)  # a source path (data object mode, owner id path) to bind to the selected element
    add_readout = Signal(object)  # a readout to add as a custom property
    load_scripts = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.filter = QLineEdit(placeholderText=tr("find a class or field"))
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels([tr("Name"), tr("Type")])
        self.tree.itemExpanded.connect(self._expand)
        load = QPushButton(tr("Load a script list..."))
        load.clicked.connect(self.load_scripts.emit)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.filter)
        lay.addWidget(self.tree)
        lay.addWidget(load)
        self.filter.textChanged.connect(lambda _t: self._fill())
        self.models: viewmodels.ViewModels | None = None
        self.scripts: viewmodels.ScriptList | None = None
        self.guess: viewmodels.ClassDef | None = None
        self.reads: list[tuple[str, list[int]]] = []  # (words, id path) of the game object paths the file reads
        self.names: dict[int, str] = {}

    def show_models(self, models, guess, reads, names, scripts=None) -> None:
        self.models, self.guess, self.reads, self.names = models, guess, reads, names
        self.scripts = scripts if scripts is not None else self.scripts
        self._fill()

    def _class_item(self, parent, c: viewmodels.ClassDef, label: str = "") -> QTreeWidgetItem:
        it = QTreeWidgetItem(parent, [label or c.short, c.name])
        it.setData(0, ROLE, ("class", c.name))
        if c.fields:
            QTreeWidgetItem(it, ["..."])
        return it

    def _fill(self) -> None:
        self.tree.clear()
        if self.guess is not None:
            top = self._class_item(self.tree, self.guess, tr("This scene's data: {name}", name=self.guess.short))
            top.setFont(0, _bold())
        reads = QTreeWidgetItem(self.tree, [tr("Field ids this file reads ({n})", n=len(self.reads)), ""])
        for words, path in self.reads:
            it = QTreeWidgetItem(reads, [words, " ".join(f"{i:08x}" for i in path)])
            it.setData(0, ROLE, ("read", path))
        reads.setExpanded(True)
        if self.models is not None:
            text = self.filter.text().strip()
            found = self.models.search(text) if text else []
            total = len(self.models.classes)
            head = (
                tr("UI data classes ({n}): {found} found", n=total, found=len(found))
                if text
                else tr("UI data classes ({n})", n=total)
            )
            top = QTreeWidgetItem(self.tree, [head, ""])
            for c in found[:300]:
                self._class_item(top, c)
            top.setExpanded(bool(text))
        if self.scripts is not None:
            top = QTreeWidgetItem(self.tree, [tr("Script readouts ({n})", n=len(self.scripts.readouts())), ""])
            for r in self.scripts.readouts():
                it = QTreeWidgetItem(top, [r.call, ", ".join(r.returns)])
                it.setToolTip(0, r.doc)
                it.setData(0, ROLE, ("readout", r))

    def _expand(self, item: QTreeWidgetItem) -> None:
        meta = item.data(0, ROLE)
        if not meta or meta[0] != "class" or item.childCount() != 1 or item.child(0).text(0) != "...":
            return
        item.takeChild(0)
        c = self.models.classes.get(meta[1]) if self.models else None
        for f in c.fields if c else []:
            child_class = self.models.classes.get(f.child_class or "")
            it = (
                self._class_item(item, child_class, f.name)
                if child_class and f.kind != viewmodels.VALUE
                else QTreeWidgetItem(item, [f.name, ""])
            )
            it.setText(
                1,
                tr("{type} ({kind}, settable)", type=f.type, kind=f.kind)
                if f.settable
                else tr("{type} ({kind})", type=f.type, kind=f.kind),
            )
            it.setData(1, ROLE, ("field", f"{c.short}.{f.name}"))

    def contextMenuEvent(self, event) -> None:
        item = self.tree.itemAt(self.tree.viewport().mapFrom(self, event.pos()))
        if item is None:
            return
        meta, field = item.data(0, ROLE), item.data(1, ROLE)
        menu = QMenu(self)
        if meta and meta[0] == "read":
            menu.addAction(tr("Bind it to the selected element's property..."), lambda: self.bind.emit(meta[1]))
        if field and field[0] == "field":
            unnamed = [(p[-1], w) for w, p in self.reads if p and p[-1] not in self.names]
            sub = menu.addMenu(tr("Name an id {field}", field=field[1]))
            for uid, words in unnamed[:40]:
                sub.addAction(f"{uid:08x} ({words})", lambda u=uid, n=field[1]: self.name_id.emit(u, n))
            sub.setEnabled(bool(unnamed))
        if meta and meta[0] == "readout":
            menu.addAction(tr("Add as a custom property of this file"), lambda: self.add_readout.emit(meta[1]))
        if not menu.isEmpty():
            menu.exec(event.globalPos())


def _bold() -> QFont:
    f = QFont()
    f.setBold(True)
    return f


class SamplePanel(QTableWidget):
    """The values the preview read where the game would give one; a value written here is shown at once."""

    changed = Signal(str, object)
    reset = Signal(str)  # a key's value dropped: the preview makes up a placeholder again

    def __init__(self) -> None:
        super().__init__(0, 2)
        self.setHorizontalHeaderLabels([tr("Read from"), tr("Sample value")])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.itemChanged.connect(self._changed)
        self._filling = False

    def show_samples(self, samples: viewmodels.Samples, describe) -> None:
        self._filling = True
        keys = sorted(set(samples.seen) | set(samples.values))
        self.setRowCount(len(keys))
        for row, key in enumerate(keys):
            name = QTableWidgetItem(describe(key))
            name.setToolTip(key)
            name.setData(ROLE, key)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.setItem(row, 0, name)
            value = QTableWidgetItem(show(samples.values.get(key)))
            if key in samples.generated:  # a made-up value: shown apart from one set by hand
                value.setForeground(QBrush(QColor(140, 140, 140)))
                font = value.font()
                font.setItalic(True)
                value.setFont(font)
                value.setToolTip(tr("a placeholder the preview made up; write a value to keep your own"))
            self.setItem(row, 1, value)
        self._filling = False

    def contextMenuEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        if item is None:
            return
        key = self.item(item.row(), 0).data(ROLE)
        menu = QMenu(self)
        menu.addAction(tr("Reset to a placeholder"), lambda: self.reset.emit(key))
        menu.exec(event.globalPos())

    def _changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 1:
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


class StatesPanel(QWidget):
    """The file's animations on a timeline (pick one, scrub or play it), the design view switch, and the events and
    actions the file declares or reacts to."""

    animation = Signal(object, float)  # animation GUID or None, seconds
    design = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.combo = QComboBox()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.play = QPushButton(tr("Play"))
        self.time = QLabel("0.00 s")  # i18n: ok (a number and a unit)
        self.design_box = QCheckBox(tr("Design view: show what an animation fades in"))
        self.design_box.setChecked(True)
        self.events = QTreeWidget()
        self.events.setHeaderLabels([tr("Event or action"), tr("What it drives")])
        row = QHBoxLayout()
        for w in (self.combo, self.play, self.time):
            row.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(row)
        lay.addWidget(self.slider)
        lay.addWidget(self.design_box)
        lay.addWidget(self.events)
        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self._tick)
        self.play.clicked.connect(self._toggle)
        self.slider.valueChanged.connect(lambda _v: self._emit())
        self.combo.currentIndexChanged.connect(lambda _i: self._emit())
        self.design_box.toggled.connect(self.design.emit)
        self.anims: list = []

    def show_states(self, anims, graph: bindings.Graph | None, describe) -> None:
        self.anims = anims
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(tr("(no animation)"))
        for a in anims:
            self.combo.addItem(
                tr("{name} ({seconds:.2f} s, {n} tracks)", name=a.name, seconds=a.seconds, n=len(a.tracks))
            )
        self.combo.blockSignals(False)
        self.events.clear()
        if graph is None:
            return
        for p in graph.properties:
            info = bindings.property_info(p)
            if info["type"] in (9, 10):
                label = (
                    tr("{name} (event)", name=info["name"])
                    if info["type"] == 9
                    else tr("{name} (action)", name=info["name"])
                )
                QTreeWidgetItem(self.events, [label, tr("declared by this file")])
        for b in graph.bindings:
            if any(e.kind == "path" and e.ids and fields.BUILTIN_BY_ID.get(e.ids[-1], None) and fields.BUILTIN_BY_ID[e.ids[-1]].kind == "event"
                   for e in b.sources) or b.operator in ("c32c7054", "130e634e", "599c5545", "a3de9f56"):  # fmt: skip
                QTreeWidgetItem(self.events, [", ".join(describe(e) for e in b.sources),
                                              f"{b.op_name} -> {', '.join(describe(e) for e in b.targets)}"])  # fmt: skip

    def current(self):
        i = self.combo.currentIndex() - 1
        return self.anims[i] if 0 <= i < len(self.anims) else None

    def _emit(self) -> None:
        anim = self.current()
        t = (anim.seconds if anim else 0.0) * self.slider.value() / 1000
        self.time.setText(f"{t:.2f} s")  # i18n: ok (a number and a unit)
        self.animation.emit(anim.guid if anim else None, t)

    def _toggle(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
            self.play.setText(tr("Play"))
        elif self.current() is not None:
            self.timer.start()
            self.play.setText(tr("Pause"))

    def _tick(self) -> None:
        anim = self.current()
        if anim is None or anim.seconds <= 0:
            self._toggle()
            return
        step = int(1000 * 0.033 / anim.seconds)
        self.slider.setValue((self.slider.value() + max(1, step)) % 1001)
