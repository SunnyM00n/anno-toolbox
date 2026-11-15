"""The island editor's side panels that list and edit what the island holds: the ground materials as swatches
(``MaterialGallery``), the libraries of models and assets (``LibraryPanel``), the objects (``ObjectsPanel``) and the
selected object's properties (``PropertiesPanel``). ``desktopeditor.IslandDoc`` builds them for an open island."""

from __future__ import annotations

from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...infra.i18n import N_, tr
from ...suite import links
from ..editlists import library_items, object_groups
from ..editsession import game_of, group_key, is_foliage, label_of
from .desktopcolor import ColorButton
from .desktopgizmo import euler_of, quat_of

if TYPE_CHECKING:
    from .desktopeditor import IslandDoc

LIBRARY_SHOWN = 400  # rows the library lists at once: the search narrows the rest
SOURCES = (  # the library's sources: the key in ``IslandEditor.library()`` and the label
    ("2070", "2070"),
    ("117", "117"),
    ("editor", N_("117 editor-only")),
    ("anno1800", "Anno 1800"),
    ("anno2205", "Anno 2205"),
)
VANILLA_GAMES = {"anno117": "Anno 117", "anno1800": "Anno 1800"}  # the game key of a palette entry -> its group
GAMES_BY_RELEASE = ("Anno 117", "Anno 1800", "Anno 2205", "Anno 2070", "Anno 1404", "Anno 1701")


def game_rank(name: str) -> int:
    """Newest game first (a game's name may carry more, as "Anno 1404 (with Venice)")."""
    return next((k for k, g in enumerate(GAMES_BY_RELEASE) if name.startswith(g)), len(GAMES_BY_RELEASE))


def material_game(m: dict, island: dict) -> str:
    """The game a ground material comes from: another game's island's own, 2070's (Anno 2070 editing, or a built
    island's ``data/anno2070/`` materials), else 117's."""
    if island.get("game_name"):
        return island["game_name"]
    if island.get("mode") == "2070" or (m.get("diffuse") or "").lower().startswith("data/anno2070/"):
        return "Anno 2070"
    return "Anno 117"


# The tools a kind of island does not offer. A vanilla 117 or 1800 island gets its ground edits written with its
# render data (groundedit); 1800 has no forest (its trees are props); another game's: objects, heights and paint.
# Rivers, lakes, coast lines and path blockers are edited in the 2070-format islands (2070, 1404; sessionpath).


class MaterialGallery(QWidget):
    """The ground materials as swatches grouped by the game each comes from (newest game first), each group in
    its draw order, bottom layer first. A click picks the material and the paint brush."""

    def __init__(self, doc: IslandDoc) -> None:
        super().__init__()
        self.doc = doc
        layout = QVBoxLayout(self)
        note = QLabel(tr("By the game they come from, each bottom layer first (draw order; docs/island-editor.md)."))
        note.setWordWrap(True)
        layout.addWidget(note)
        scroll = QScrollArea(widgetResizable=True, objectName="galleryScroll")
        self.palette_box = QWidget()
        self.palette_layout = QVBoxLayout(self.palette_box)
        scroll.setWidget(self.palette_box)
        layout.addWidget(scroll, 1)
        self.material_lists: list[QListWidget] = []

    def fill(self, own: list[dict], vanilla: list[dict]) -> None:
        """The swatches: the island's own by its set's list, 117's by their place in their vanilla set."""
        while self.palette_layout.count():
            w = self.palette_layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.material_lists = []
        groups: dict[str, list] = {}
        for m in own:
            groups.setdefault(material_game(m, self.doc.session.island), []).append(("own", m))
        for m in vanilla:  # another game's materials (``othermaterials``) are listed under that game
            groups.setdefault(VANILLA_GAMES.get(m.get("game", "anno117"), "Anno 117"), []).append(("117", m))
        order = self.doc.session.paint_materials
        for game in sorted(groups, key=game_rank):
            items = groups[game]
            items.sort(
                key=lambda km: (km[0] != "own", order.get(km[1].get("id"), km[1]).get("order", km[1].get("rank", 0)))
            )
            self.palette_layout.addWidget(QLabel(f"<b>{game}</b>"))  # i18n: ok (a game's name)
            lst = QListWidget(viewMode=QListWidget.ViewMode.IconMode, objectName="gallery")
            lst.setIconSize(QSize(44, 44))
            lst.setGridSize(QSize(50, 50))
            lst.setMovement(QListWidget.Movement.Static)
            lst.setResizeMode(QListWidget.ResizeMode.Adjust)
            rows = -(-len(items) // 4)
            lst.setFixedHeight(min(6, rows) * 52 + 6)
            for kind, m in items:
                item = QListWidgetItem()
                where = (
                    tr("island set, layer {layer}", layer=order.get(m["id"], m).get("order", m["id"]))
                    if kind == "own"
                    else tr("{set}, place {rank}", set=m["set"], rank=m.get("rank", 0))
                )
                item.setToolTip(f"{m['name']} ({where})")
                item.setData(Qt.ItemDataRole.UserRole, (kind, m))
                lst.addItem(item)
            lst.itemClicked.connect(self._material)
            self.palette_layout.addWidget(lst)
            self.material_lists.append(lst)
        self.palette_layout.addStretch(1)

    def items(self):
        for lst in self.material_lists:
            for k in range(lst.count()):
                yield lst.item(k)

    def _material(self, item) -> None:
        for lst in self.material_lists:  # one pick across the groups
            if lst is not item.listWidget():
                lst.clearSelection()
        kind, m = item.data(Qt.ItemDataRole.UserRole)
        doc = self.doc
        s = doc.session
        if kind == "own":
            s.paint_materials.setdefault(m["id"], m)
            mid = m["id"]
        else:
            mid = s.register_vanilla_material(m, [x["id"] for x in doc.own_materials])
            if mid is None:
                doc.say(tr("the island's material set is full (32 materials)"))
                return
        doc.view.paint_material = mid
        doc.view.set_tool("paint")


class LibraryPanel(QWidget):
    """Object Groups: the models that can be placed, by source and group, searched."""

    def __init__(self, doc: IslandDoc) -> None:
        super().__init__()
        self.doc = doc
        self.data = {key: [] for key, _label in SOURCES}
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.source = QComboBox()
        self._sources()
        self.search = QLineEdit(placeholderText=tr("search models"))
        row.addWidget(self.source)
        row.addWidget(self.search, 1)
        layout.addLayout(row)
        self.tree = QTreeWidget(headerHidden=True)
        layout.addWidget(self.tree, 1)
        self.count = QLabel()
        layout.addWidget(self.count)
        self.source.currentIndexChanged.connect(self.fill)
        self.search.textChanged.connect(self.fill)
        self.tree.itemClicked.connect(self._pick)

    def _sources(self) -> None:
        """The combo lists the sources that have models (the first three while none has)."""
        self.source.blockSignals(True)
        self.source.clear()
        shown = [(k, label) for k, label in SOURCES if self.data.get(k)] or list(SOURCES[:3])
        for key, label in shown:
            self.source.addItem(tr(label) if key == "editor" else label, key)
        self.source.blockSignals(False)

    def set_data(self, data: dict) -> None:
        self.data = {**{key: [] for key, _label in SOURCES}, **data}
        self._sources()
        self.fill()

    def fill(self) -> None:
        self.tree.clear()
        groups = library_items(self.data, self.source.currentData(), self.search.text(), self.doc.session.forest_models)
        shown, total = 0, 0
        query = bool(self.search.text().strip())
        for group, items in groups.items():
            top = QTreeWidgetItem([f"{group}  ({len(items)})"])
            self.tree.addTopLevelItem(top)
            total += len(items)
            for e in items[: max(0, LIBRARY_SHOWN - shown)]:
                child = QTreeWidgetItem([e["name"]])
                child.setToolTip(0, e["title"])
                child.setData(0, Qt.ItemDataRole.UserRole, e)
                top.addChild(child)
                shown += 1
            top.setExpanded(query or group == tr("forest of this island"))
        self.count.setText(tr("{total} in {groups} groups", total=total, groups=len(groups)))

    def _pick(self, item) -> None:
        e = item.data(0, Qt.ItemDataRole.UserRole)
        if not e:
            return
        if e.get("guid") or e.get("cfg") or e.get("tree") is not None:
            self.doc.view.set_pick(e)
            return
        doc = self.doc
        doc.say(tr("converting {name}…", name=e["name"]))
        editor = doc.editor

        def done(result):
            if not doc.alive:
                return
            if isinstance(result, Exception):
                doc.say(tr("could not prepare {name}: {error}", name=e["name"], error=result))
                return
            e["cfg"] = result
            doc.say("")
            doc.view.set_pick(e)

        doc.background.run(lambda: editor.convert2070(e["source"]), done)


INSTANCE_ROWS = 60  # a model placed this often or less lists each object under it


class ObjectsPanel(QWidget):
    """The island's objects by category and model; a click selects and flies there, a right click hides."""

    def __init__(self, doc: IslandDoc) -> None:
        super().__init__()
        self.doc = doc
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Object"), tr("Count")])
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemClicked.connect(self._click)
        layout.addWidget(self.tree, 1)
        self._open: set[str] = set()
        self._asked = 0  # the number of the latest fill: an older one still grouping is dropped
        self._timer = QTimer(self, singleShot=True, interval=300, timeout=self.fill)

    def refresh(self) -> None:
        self._timer.start()

    def fill(self) -> None:
        """The list again: the entries grouped off the window's thread (a continental island's 130,000 took a
        second), the tree made when they are; a newer fill wins over one still grouping."""
        entries = list(self.doc.session.entries)
        self._asked += 1
        asked = self._asked
        background = getattr(self.doc, "background", None)
        if background is None:
            self._show_groups(object_groups(entries))
            return
        background.run(lambda: object_groups(entries), lambda groups: self._grouped(asked, groups))

    def _grouped(self, asked: int, groups) -> None:
        if asked == self._asked and isinstance(groups, dict) and shiboken6.isValid(self) and self.doc.alive:
            self._show_groups(groups)

    def _show_groups(self, groups: dict) -> None:
        hid = self.doc.view.hidden
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            (self._open.add if it.isExpanded() else self._open.discard)(it.data(0, Qt.ItemDataRole.UserRole))
        self.tree.clear()
        few = sum(len(m) for m in groups.values()) <= 40
        for t in sorted(groups):
            models = groups[t]
            count = sum(1 for es in models.values() for e in es if e.visible)
            off = t in hid.types
            top = QTreeWidgetItem([t, (tr("hidden") + " · " if off else "") + str(count)])
            top.setData(0, Qt.ItemDataRole.UserRole, t)
            self.tree.addTopLevelItem(top)
            for g in sorted(models, key=lambda g: label_of(models[g][0].base)):
                es = models[g]
                hidden = g in hid.groups or off or (hid.foliage and is_foliage(es[0].base))
                child = QTreeWidgetItem(
                    [label_of(es[0].base), (tr("hidden") + " · " if hidden else "") + str(sum(e.visible for e in es))]
                )
                child.setToolTip(0, g)
                child.setData(0, Qt.ItemDataRole.UserRole, g)
                top.addChild(child)
                if 1 < len(es) <= INSTANCE_ROWS:  # a few objects of one model: each can be picked
                    for e in es:
                        if e.visible:
                            row = QTreeWidgetItem(
                                [f"x {e.position[0]:.1f}  z {e.position[2]:.1f}", ""]
                            )  # i18n: ok (axis names)
                            row.setData(0, Qt.ItemDataRole.UserRole + 1, e)
                            child.addChild(row)
            top.setExpanded(few or t in self._open)
        self.tree.resizeColumnToContents(0)

    def _entries(self, g: str):
        return [e for e in self.doc.session.entries if e.alive and group_key(e.base) == g]

    def _click(self, item) -> None:
        if item.parent() is None:
            return
        picked = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if picked is not None:  # one object of a model
            view = self.doc.view
            if view.tool != "select":
                view.set_tool("select")
            view.select(picked)
            view.focus_on([picked])
            return
        es = [e for e in self._entries(item.data(0, Qt.ItemDataRole.UserRole)) if e.shown]
        if es:
            view = self.doc.view
            if view.tool != "select":
                view.set_tool("select")
            view.select(es[0])
            view.focus_on(es)

    def _menu(self, at) -> None:
        item = self.tree.itemAt(at)
        if item is None:
            return
        if item.data(0, Qt.ItemDataRole.UserRole + 1) is not None:
            item = item.parent()  # an object's row: the menu of its model
        hid, view = self.doc.view.hidden, self.doc.view
        menu = QMenu(self)
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if item.parent() is not None:
            label = item.text(0)
            if key in hid.groups:
                menu.addAction(
                    tr("Show all {name}", name=label),
                    lambda: (hid.groups.discard(key), view.apply_hidden(), self.fill()),
                )
            else:
                menu.addAction(
                    tr("Hide all {name}", name=label), lambda: (hid.groups.add(key), view.apply_hidden(), self.fill())
                )
            key = item.parent().data(0, Qt.ItemDataRole.UserRole)
        if key in hid.types:
            menu.addAction(
                tr("Show category {name}", name=key), lambda: (hid.types.discard(key), view.apply_hidden(), self.fill())
            )
        else:
            menu.addAction(
                tr("Hide category {name}", name=key), lambda: (hid.types.add(key), view.apply_hidden(), self.fill())
            )
        if hid.count():
            menu.addAction(tr("Show everything hidden ({n})", n=hid.count()), self.show_hidden)
        menu.exec(self.tree.viewport().mapToGlobal(at))

    def show_hidden(self) -> None:
        view = self.doc.view
        view.hidden.clear()
        self.doc.layers.set_layer("forest", True)
        self.doc.layers.set_layer("grass", True)
        view.apply_hidden()
        self.fill()
        self.doc.shell.sync()

    def set_foliage(self, off: bool) -> None:
        """Trees, bushes and grass out of the view (the objects and the forest and grass layers)."""
        view = self.doc.view
        view.hidden.foliage = off
        self.doc.layers.set_layer("forest", not off)
        self.doc.layers.set_layer("grass", not off)
        view.apply_hidden()
        self.fill()
        self.doc.shell.sync()


class PropertiesPanel(QWidget):
    """The selected object's place, turn and size, or the selected river point's width / lake's level; its asset
    and its model open in the suite's other apps (``suite.links``)."""

    def __init__(self, doc: IslandDoc) -> None:
        super().__init__()
        self.doc = doc
        layout = QVBoxLayout(self)
        self.title = QLabel(tr("nothing selected"))
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        row = QHBoxLayout()
        self.asset_button = QPushButton(tr("Show the asset"), toolTip=tr("the object's asset in the asset editor"))
        self.model_button = QPushButton(tr("View the model"), toolTip=tr("the object's model config in the viewer"))
        self.asset_button.clicked.connect(lambda _c=False: self._follow(links.SHOW_ASSET, "guid"))
        self.model_button.clicked.connect(lambda _c=False: self._follow(links.VIEW_FILE, "cfg"))
        self.export_button = QPushButton(
            tr("Export the model"), toolTip=tr("the object's model config and meshes, exported by the viewer")
        )
        self.export_button.clicked.connect(lambda _c=False: self._follow(links.EXPORT_FILES, "cfg"))
        for button in (self.asset_button, self.model_button, self.export_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.form = QFormLayout()
        self.fields = {}
        for key, label, lo, hi, step in (
            ("x", tr("x (tiles)"), -2000, 4000, 0.1), ("y", tr("height"), -200, 400, 0.05), ("z", tr("z (tiles)"), -2000, 4000, 0.1),
            ("yaw", tr("turn (°)"), -360, 360, 5), ("tx", tr("tilt x (°)"), -90, 90, 1), ("tz", tr("tilt z (°)"), -180, 180, 1),
            ("s", tr("scale"), 0.01, 100, 0.05), ("sx", tr("scale x"), 0.01, 100, 0.05), ("sy", tr("scale y"), 0.01, 100, 0.05),
            ("sz", tr("scale z"), 0.01, 100, 0.05), ("width", tr("river width"), 0.5, 100, 0.5), ("level", tr("lake level"), -50, 100, 0.05),
        ):  # fmt: skip
            sb = QDoubleSpinBox(minimum=lo, maximum=hi, singleStep=step, decimals=3, keyboardTracking=False)
            sb.editingFinished.connect(lambda k=key: self._edited(k))
            self.fields[key] = sb
            self.form.addRow(label, sb)
        self.tint = ColorButton()
        self.tint.picked.connect(lambda rgba: self.entry is not None and self.doc.session.set_color(self.entry, rgba))
        self.form.addRow(tr("tint"), self.tint)
        layout.addLayout(self.form)
        layout.addStretch(1)
        self.show_entry(None)

    def _visible(self, keys) -> None:
        for k, sb in self.fields.items():
            self.form.setRowVisible(sb, k in keys)
        base = self.entry.base if self.entry is not None and self.entry.alive else {}
        self.form.setRowVisible(self.tint, "x" in keys and self.entry is not None and self.entry.color is not None)
        self.asset_button.setVisible(bool(base.get("guid")) and links.available(links.SHOW_ASSET))
        self.model_button.setVisible(bool(base.get("cfg")) and links.available(links.VIEW_FILE))
        self.export_button.setVisible(bool(base.get("cfg")) and links.available(links.EXPORT_FILES))

    def _follow(self, link: str, key: str) -> None:
        value = (self.entry.base if self.entry is not None else {}).get(key)
        if link == links.EXPORT_FILES:
            value = [value] if value else None
        if value and not links.follow(
            link, game_of({}, kind=self.doc.info.get("kind") or ""), int(value) if key == "guid" else value
        ):
            if key == "guid":
                self.doc.say(tr("the asset {value} is not in that game's files", value=value))
            else:
                self.doc.say(tr("the model {value} is not in that game's files", value=value))

    def show_entry(self, e) -> None:
        self.entry = e
        if self.doc.view.water_sel is not None:
            return self.show_water(self.doc.view.water_sel)
        if e is None or not e.alive:
            self.title.setText(tr("nothing selected"))
            return self._visible(())
        added = " " + tr("(added)") if e.added is not None else ""
        info = f"<b>{label_of(e.base)}</b><br>{e.marker or e.kind}{added}"
        if e.kind == "tree":
            self.title.setText(tr("{info}<br>forest tree · tile {tile}<br>Del clears the tile", info=info, tile=e.tile))
            return self._visible(())
        if not e.visible:
            info += " · " + tr("deleted")
        self.title.setText(info)
        keys = ["x", "y", "z", "yaw", "tx", "tz"] + (["sx", "sy", "sz"] if isinstance(e.scale, list) else ["s"])
        self._visible(keys)
        yaw, tx, tz = euler_of(e.rotation)
        values = dict(zip("xyz", e.position, strict=True)) | {"yaw": yaw, "tx": tx, "tz": tz}
        if isinstance(e.scale, list):
            values |= dict(zip(("sx", "sy", "sz"), e.scale, strict=True))
        else:
            values["s"] = e.scale
        for k, v in values.items():
            sb = self.fields[k]
            if not sb.hasFocus():
                sb.blockSignals(True)
                sb.setValue(float(v))
                sb.blockSignals(False)
        for sb in self.fields.values():
            sb.setEnabled(not self.doc.session.readonly)
        self.tint.set_color(e.color)
        self.tint.setEnabled(not self.doc.session.readonly)

    def show_water(self, w) -> None:
        s = self.doc.session
        if w is None:
            self.title.setText(
                tr("click a river or coast point or a lake")
                if s.water_net
                else tr("this island has no river network (no .water.json)")
            )
            return self._visible(())
        if "lake" in w:
            self.title.setText(tr("<b>lake {name}</b>", name=w["lake"]))
            self._visible(["level"])
            self.fields["level"].setValue(s.lake_level(w["lake"]))
            return
        if "outline" in w:
            self.title.setText(
                tr(
                    "<b>lake {name} · outline point {point}/{n}</b><br>drag it over the ground",
                    name=w["outline"],
                    point=w["point"] + 1,
                    n=len(s.lake_outline(w["outline"])),
                )
            )
            return self._visible(())
        if "coast" in w:
            n = len(s.path.coast(w["coast"])["points"])
            self.title.setText(
                tr(
                    "<b>coast line {name} · point {point}/{n}</b><br>drag it over the ground",
                    name=w["coast"],
                    point=w["point"] + 1,
                    n=n,
                )
            )
            return self._visible(())
        if "shape" in w:
            shape = {"name": w["k"], "point": w["point"] + 1, "n": len(s.path.shapes(w["shape"])[w["k"]]["points"])}
            self.title.setText(
                tr("<b>camera blocker {name} · point {point}/{n}</b><br>drag it over the ground", **shape)
                if w["shape"] == "camera"
                else tr("<b>build blocker {name} · point {point}/{n}</b><br>drag it over the ground", **shape)
            )
            return self._visible(())
        r = s.river(w["river"])
        self.title.setText(
            tr(
                "<b>river {name} · point {point}/{n}</b><br>water {level}<br>Del: remove point · Ctrl+D: insert after",
                name=w["river"],
                point=w["point"] + 1,
                n=len(r["points"]),
                level=f"{r['points'][w['point']][1]:.2f}",
            )
        )
        self._visible(["width"])
        self.fields["width"].setValue(r["widths"][w["point"]])

    def _edited(self, key: str) -> None:
        s, v = self.doc.session, self.fields[key].value()
        w = self.doc.view.water_sel
        if key in ("width", "level") and w is not None:
            before = s.water_snapshot()
            if key == "level":
                s.set_lake_level(w["lake"], v)
            else:
                s.set_river_width(w["river"], w["point"], v)
            s.end_water(before, tr("lake level") if key == "level" else tr("river width"))
            return
        e = self.entry
        if e is None or not e.alive or e.kind == "tree":
            return
        f = {k: sb.value() for k, sb in self.fields.items()}
        if key in ("x", "y", "z"):
            s.transform(e, tr("move"), position=[f["x"], f["y"], f["z"]])
        elif key in ("yaw", "tx", "tz"):
            s.transform(e, tr("rotation"), rotation=quat_of(f["yaw"], f["tx"], f["tz"]))
        elif key == "s":
            s.transform(e, tr("scale"), scale=f["s"])
        else:
            s.transform(e, tr("scale"), scale=[f["sx"], f["sy"], f["sz"]])
