"""The island editor's side panels that list and edit what the island holds: the ground materials as swatches
(``CMaterialGallery``), the libraries of models and assets (``CLibraryPanel``), the objects (``CObjectsPanel``) and the
selected object's properties (``CPropertiesPanel``). ``desktopeditor.CIslandDoc`` builds them for an open island."""

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
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...infra.i18n import N_, tr
from ...suite import links
from ..editlists import library_items, object_groups
from ..editsession import game_of, group_key, is_foliage, label_of
from .desktopcolor import CColorButton
from .desktopgizmo import euler_of, quat_of
from .desktopslotstab import CSlotsPanel
from .desktopview import fly_to

if TYPE_CHECKING:
    from .desktopeditor import CIslandDoc

LIBRARY_SHOWN = 400  # rows the library lists at once: the search narrows the rest
SOURCES = (  # the library's sources: the key in ``IslandEditor.library()`` and the label
    ("2070", "2070"),
    ("117", "117"),
    ("editor", N_("islands.117_editor_only")),
    ("anno1800", "Anno 1800"),
    ("anno2205", "Anno 2205"),
)
VANILLA_GAMES = {"anno117": "Anno 117", "anno1800": "Anno 1800"}  # the game key of a palette entry -> its group
GAMES_BY_RELEASE = ("Anno 117", "Anno 1800", "Anno 2205", "Anno 2070", "Anno 1404", "Anno 1701")


def game_rank(name: str) -> int:
    """Newest game first (a game's name may carry more, as "Anno 1404 (with Venice)")."""
    return next((k for k, game in enumerate(GAMES_BY_RELEASE) if name.startswith(game)), len(GAMES_BY_RELEASE))


def material_game(material: dict, island: dict) -> str:
    """The game a ground material comes from: another game's island's own, 2070's (Anno 2070 editing, or a built
    island's ``data/anno2070/`` materials), else 117's."""
    if island.get("game_name"):
        return island["game_name"]
    if island.get("mode") == "2070" or (material.get("diffuse") or "").lower().startswith("data/anno2070/"):
        return "Anno 2070"
    return "Anno 117"


# The tools a kind of island does not offer. A vanilla 117 or 1800 island gets its ground edits written with its
# render data (groundedit); 1800 has no forest (its trees are props); another game's: objects, heights and paint.
# Rivers, lakes, coast lines and path blockers are edited in the 2070-format islands (2070, 1404; sessionpath).


class CMaterialGallery(QWidget):
    """The ground materials as swatches grouped by the game each comes from (newest game first), each group in
    its draw order, bottom layer first. A click picks the material and the paint brush."""

    def __init__(self, doc: CIslandDoc) -> None:
        super().__init__()
        self.m_doc = doc
        layout = QVBoxLayout(self)
        note = QLabel(tr("islands.game_they_come_bottom_layer"))
        note.setWordWrap(True)
        layout.addWidget(note)
        scroll = QScrollArea(widgetResizable=True, objectName="galleryScroll")
        self.m_palette_box = QWidget()
        self.m_palette_layout = QVBoxLayout(self.m_palette_box)
        scroll.setWidget(self.m_palette_box)
        layout.addWidget(scroll, 1)
        self.m_material_lists: list[QListWidget] = []

    def fill(self, own: list[dict], vanilla: list[dict]) -> None:
        """The swatches: the island's own by its set's list, 117's by their place in their vanilla set."""
        while self.m_palette_layout.count():
            widget = self.m_palette_layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self.m_material_lists = []
        groups: dict[str, list] = {}
        for material in own:
            groups.setdefault(material_game(material, self.m_doc.m_session.m_island), []).append(("own", material))
        home = material_game({}, self.m_doc.m_session.m_island)
        for material in vanilla:  # only the island's own game: another game's ground does not mix with it
            game = VANILLA_GAMES.get(material.get("game", "anno117"), "Anno 117")
            if game == home:
                groups.setdefault(game, []).append(("117", material))
        order = self.m_doc.m_session.m_paint_materials
        for game in sorted(groups, key=game_rank):
            items = groups[game]
            items.sort(
                key=lambda kind_material: (
                    kind_material[0] != "own",
                    order.get(kind_material[1].get("id"), kind_material[1]).get(
                        "order", kind_material[1].get("rank", 0)
                    ),
                )
            )
            self.m_palette_layout.addWidget(QLabel(f"<b>{game}</b>"))  # i18n: ok (a game's name)
            material_list = QListWidget(viewMode=QListWidget.ViewMode.IconMode, objectName="gallery")
            material_list.setIconSize(QSize(44, 44))
            material_list.setGridSize(QSize(50, 50))
            material_list.setMovement(QListWidget.Movement.Static)
            material_list.setResizeMode(QListWidget.ResizeMode.Adjust)
            rows = -(-len(items) // 4)
            material_list.setFixedHeight(min(6, rows) * 52 + 6)
            for kind, material in items:
                item = QListWidgetItem()
                where = (
                    tr(
                        "islands.island_set_layer",
                        layer=order.get(material["id"], material).get("order", material["id"]),
                    )
                    if kind == "own"
                    else tr("islands.set_place_rank", set=material["set"], rank=material.get("rank", 0))
                )
                item.setToolTip(f"{material['name']} ({where})")
                item.setData(Qt.ItemDataRole.UserRole, (kind, material))
                material_list.addItem(item)
            material_list.itemClicked.connect(self._material)
            self.m_palette_layout.addWidget(material_list)
            self.m_material_lists.append(material_list)
        self.m_palette_layout.addStretch(1)

    def items(self):
        for material_list in self.m_material_lists:
            for k in range(material_list.count()):
                yield material_list.item(k)

    def _material(self, item) -> None:
        for material_list in self.m_material_lists:  # one pick across the groups
            if material_list is not item.listWidget():
                material_list.clearSelection()
        kind, material = item.data(Qt.ItemDataRole.UserRole)
        doc = self.m_doc
        session = doc.m_session
        if kind == "own":
            session.m_paint_materials.setdefault(material["id"], material)
            material_id = material["id"]
        else:
            material_id = session.register_vanilla_material(material, [own["id"] for own in doc.m_own_materials])
            if material_id is None:
                doc.say(tr("islands.islands_material_set_full_32"))
                return
        doc.m_view.m_paint_material = material_id
        doc.m_view.set_tool("paint")


class CLibraryPanel(QWidget):
    """Object Groups: the models that can be placed, by source and group, searched."""

    def __init__(self, doc: CIslandDoc) -> None:
        super().__init__()
        self.m_doc = doc
        self.m_data = {key: [] for key, _label in SOURCES}
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.m_source = QComboBox()
        self._sources()
        self.m_search = QLineEdit(placeholderText=tr("islands.search_models"))
        row.addWidget(self.m_source)
        row.addWidget(self.m_search, 1)
        layout.addLayout(row)
        self.m_tree = QTreeWidget(headerHidden=True)
        layout.addWidget(self.m_tree, 1)
        self.m_count = QLabel()
        layout.addWidget(self.m_count)
        self.m_source.currentIndexChanged.connect(self.fill)
        self.m_search.textChanged.connect(self.fill)
        self.m_tree.itemClicked.connect(self._pick)

    def _sources(self) -> None:
        """The combo lists the sources that have models (the first three while none has)."""
        self.m_source.blockSignals(True)
        self.m_source.clear()
        shown = [(source_key, label) for source_key, label in SOURCES if self.m_data.get(source_key)] or list(
            SOURCES[:3]
        )
        for key, label in shown:
            self.m_source.addItem(tr(label) if key == "editor" else label, key)
        self.m_source.blockSignals(False)

    def set_data(self, data: dict) -> None:
        self.m_data = {**{key: [] for key, _label in SOURCES}, **data}
        self._sources()
        self.fill()

    def fill(self) -> None:
        self.m_tree.clear()
        groups = library_items(
            self.m_data, self.m_source.currentData(), self.m_search.text(), self.m_doc.m_session.m_forest_models
        )
        shown, total = 0, 0
        query = bool(self.m_search.text().strip())
        for group, items in groups.items():
            top = QTreeWidgetItem([f"{group}  ({len(items)})"])
            self.m_tree.addTopLevelItem(top)
            total += len(items)
            for entry in items[: max(0, LIBRARY_SHOWN - shown)]:
                child = QTreeWidgetItem([entry["name"]])
                child.setToolTip(0, entry["title"])
                child.setData(0, Qt.ItemDataRole.UserRole, entry)
                top.addChild(child)
                shown += 1
            top.setExpanded(query or group == tr("islands.forest_island"))
        self.m_count.setText(tr("islands.total_in_groups", total=total, groups=len(groups)))

    def _pick(self, item) -> None:
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if not entry:
            return
        if entry.get("guid") or entry.get("cfg") or entry.get("tree") is not None:
            self.m_doc.m_view.set_pick(entry)
            return
        doc = self.m_doc
        doc.say(tr("islands.converting_name", name=entry["name"]))
        editor = doc.m_editor

        def done(result):
            if not doc.m_alive:
                return
            if isinstance(result, Exception):
                doc.say(tr("islands.could_not_prepare", name=entry["name"], error=result))
                return
            entry["cfg"] = result
            doc.say("")
            doc.m_view.set_pick(entry)

        doc.m_background.run(lambda: editor.convert2070(entry["source"]), done)


INSTANCE_ROWS = 60  # a model placed this often or less lists each object under it


class CObjectsPanel(QWidget):
    """The island's objects by category and model; a click selects and flies there, a right click hides."""

    def __init__(self, doc: CIslandDoc) -> None:
        super().__init__()
        self.m_doc = doc
        layout = QVBoxLayout(self)
        self.m_tree = QTreeWidget()
        self.m_tree.setHeaderLabels([tr("common.object"), tr("islands.count")])
        self.m_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.m_tree.customContextMenuRequested.connect(self._menu)
        self.m_tree.itemClicked.connect(self._click)
        self.m_slots = CSlotsPanel(doc)
        tabs = QTabWidget()
        tabs.addTab(self.m_tree, tr("islands.objects"))
        tabs.addTab(self.m_slots, tr("islands.slots"))
        layout.addWidget(tabs, 1)
        self._m_open: set[str] = set()
        self._m_asked = 0  # the number of the latest fill: an older one still grouping is dropped
        self._m_timer = QTimer(self, singleShot=True, interval=300, timeout=self.fill)

    def refresh(self) -> None:
        self._m_timer.start()

    def fill(self) -> None:
        """The list again: the entries grouped off the window's thread (a continental island's 130,000 took a
        second), the tree made when they are; a newer fill wins over one still grouping."""
        entries = list(self.m_doc.m_session.m_entries)
        self.m_slots.fill()
        self._m_asked += 1
        asked = self._m_asked
        background = getattr(self.m_doc, "m_background", None)
        if background is None:
            self._show_groups(object_groups(entries))
            return
        background.run(lambda: object_groups(entries), lambda groups: self._grouped(asked, groups))

    def _grouped(self, asked: int, groups) -> None:
        if asked == self._m_asked and isinstance(groups, dict) and shiboken6.isValid(self) and self.m_doc.m_alive:
            self._show_groups(groups)

    def _show_groups(self, groups: dict) -> None:
        hidden_items = self.m_doc.m_view.m_hidden
        for i in range(self.m_tree.topLevelItemCount()):
            top_item = self.m_tree.topLevelItem(i)
            (self._m_open.add if top_item.isExpanded() else self._m_open.discard)(
                top_item.data(0, Qt.ItemDataRole.UserRole)
            )
        self.m_tree.clear()
        few = sum(len(group_models) for group_models in groups.values()) <= 40
        for category in sorted(groups):
            models = groups[category]
            count = sum(1 for entries in models.values() for entry in entries if entry.visible)
            is_category_hidden = category in hidden_items.m_types
            top = QTreeWidgetItem([category, (tr("common.hidden") + " · " if is_category_hidden else "") + str(count)])
            top.setData(0, Qt.ItemDataRole.UserRole, category)
            self.m_tree.addTopLevelItem(top)
            for group in sorted(models, key=lambda group: label_of(models[group][0].base)):
                entries = models[group]
                hidden = (
                    group in hidden_items.m_groups
                    or is_category_hidden
                    or (hidden_items.m_foliage and is_foliage(entries[0].base))
                )
                child = QTreeWidgetItem(
                    [
                        label_of(entries[0].base),
                        (tr("common.hidden") + " · " if hidden else "") + str(sum(entry.visible for entry in entries)),
                    ]
                )
                child.setToolTip(0, group)
                child.setData(0, Qt.ItemDataRole.UserRole, group)
                top.addChild(child)
                if 1 < len(entries) <= INSTANCE_ROWS:  # a few objects of one model: each can be picked
                    for entry in entries:
                        if entry.visible:
                            row = QTreeWidgetItem(
                                [f"x {entry.position[0]:.1f}  z {entry.position[2]:.1f}", ""]
                            )  # i18n: ok (axis names)
                            row.setData(0, Qt.ItemDataRole.UserRole + 1, entry)
                            child.addChild(row)
            top.setExpanded(few or category in self._m_open)
        self.m_tree.resizeColumnToContents(0)

    def _entries(self, group: str):
        return [entry for entry in self.m_doc.m_session.m_entries if entry.alive and group_key(entry.base) == group]

    def _click(self, item) -> None:
        if item.parent() is None:
            return
        picked = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if picked is not None:  # one object of a model
            fly_to(self.m_doc.m_view, [picked])
            return
        entries = [entry for entry in self._entries(item.data(0, Qt.ItemDataRole.UserRole)) if entry.shown]
        if entries:
            fly_to(self.m_doc.m_view, entries)

    def _menu(self, position) -> None:
        item = self.m_tree.itemAt(position)
        if item is None:
            return
        if item.data(0, Qt.ItemDataRole.UserRole + 1) is not None:
            item = item.parent()  # an object's row: the menu of its model
        hidden_items, view = self.m_doc.m_view.m_hidden, self.m_doc.m_view
        menu = QMenu(self)
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if item.parent() is not None:
            label = item.text(0)
            if key in hidden_items.m_groups:
                menu.addAction(
                    tr("islands.show_all_name", name=label),
                    lambda: (hidden_items.m_groups.discard(key), view.apply_hidden(), self.fill()),
                )
            else:
                menu.addAction(
                    tr("islands.hide_all_name", name=label),
                    lambda: (hidden_items.m_groups.add(key), view.apply_hidden(), self.fill()),
                )
            key = item.parent().data(0, Qt.ItemDataRole.UserRole)
        if key in hidden_items.m_types:
            menu.addAction(
                tr("islands.show_category_name", name=key),
                lambda: (hidden_items.m_types.discard(key), view.apply_hidden(), self.fill()),
            )
        else:
            menu.addAction(
                tr("islands.hide_category_name", name=key),
                lambda: (hidden_items.m_types.add(key), view.apply_hidden(), self.fill()),
            )
        if hidden_items.count():
            menu.addAction(tr("islands.show_everything_hidden", n=hidden_items.count()), self.show_hidden)
        menu.exec(self.m_tree.viewport().mapToGlobal(position))

    def show_hidden(self) -> None:
        view = self.m_doc.m_view
        view.m_hidden.clear()
        self.m_doc.set_layer("forest", True)
        self.m_doc.set_layer("grass", True)
        view.apply_hidden()
        self.fill()
        self.m_doc.m_shell.sync()

    def set_foliage(self, is_hidden: bool) -> None:
        """Trees, bushes and grass out of the view (the objects and the forest and grass layers)."""
        view = self.m_doc.m_view
        view.m_hidden.m_foliage = is_hidden
        self.m_doc.set_layer("forest", not is_hidden)
        self.m_doc.set_layer("grass", not is_hidden)
        view.apply_hidden()
        self.fill()
        self.m_doc.m_shell.sync()


class CPropertiesPanel(QWidget):
    """The selected object's place, turn and size, or the selected river point's width / lake's level; its asset
    and its model open in the suite's other apps (``suite.links``)."""

    def __init__(self, doc: CIslandDoc) -> None:
        super().__init__()
        self.m_doc = doc
        layout = QVBoxLayout(self)
        self.m_title = QLabel(tr("islands.nothing_selected"))
        self.m_title.setWordWrap(True)
        layout.addWidget(self.m_title)
        row = QHBoxLayout()
        self.m_asset_button = QPushButton(tr("islands.show_asset"), toolTip=tr("islands.tip.objects_asset_editor"))
        self.m_model_button = QPushButton(
            tr("islands.view_model"), toolTip=tr("islands.tip.objects_model_config_viewer")
        )
        self.m_asset_button.clicked.connect(lambda _c=False: self._follow(links.SHOW_ASSET, "guid"))
        self.m_model_button.clicked.connect(lambda _c=False: self._follow(links.VIEW_FILE, "cfg"))
        self.m_export_button = QPushButton(
            tr("islands.export_model"), toolTip=tr("islands.tip.objects_model_config_meshes_exported")
        )
        self.m_export_button.clicked.connect(lambda _c=False: self._follow(links.EXPORT_FILES, "cfg"))
        for button in (self.m_asset_button, self.m_model_button, self.m_export_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.m_form = QFormLayout()
        self.m_fields = {}
        for key, label, minimum, maximum, step in (
            ("x", tr("islands.x_tiles"), -2000, 4000, 0.1), ("y", tr("common.height"), -200, 400, 0.05), ("z", tr("islands.z_tiles"), -2000, 4000, 0.1),
            ("yaw", tr("islands.turn_deg"), -360, 360, 5), ("tx", tr("islands.tilt_x_deg"), -90, 90, 1), ("tz", tr("islands.tilt_z_deg"), -180, 180, 1),
            ("s", tr("common.scale"), 0.01, 100, 0.05), ("sx", tr("islands.scale_x"), 0.01, 100, 0.05), ("sy", tr("islands.scale_y"), 0.01, 100, 0.05),
            ("sz", tr("islands.scale_z"), 0.01, 100, 0.05), ("width", tr("islands.river_width"), 0.5, 100, 0.5),
            ("level", tr("islands.lake_level"), -50, 100, 0.05),
        ):  # fmt: skip
            spin_box = QDoubleSpinBox(
                minimum=minimum, maximum=maximum, singleStep=step, decimals=3, keyboardTracking=False
            )
            spin_box.editingFinished.connect(lambda field_key=key: self._edited(field_key))
            self.m_fields[key] = spin_box
            self.m_form.addRow(label, spin_box)
        self.m_tint = CColorButton()
        self.m_tint.picked.connect(
            lambda rgba: self.m_entry is not None and self.m_doc.m_session.set_color(self.m_entry, rgba)
        )
        self.m_form.addRow(tr("islands.tint_lower"), self.m_tint)
        layout.addLayout(self.m_form)
        layout.addStretch(1)
        self.show_entry(None)

    def _visible(self, keys) -> None:
        for field_key, spin_box in self.m_fields.items():
            self.m_form.setRowVisible(spin_box, field_key in keys)
        base = self.m_entry.base if self.m_entry is not None and self.m_entry.alive else {}
        self.m_form.setRowVisible(
            self.m_tint, "x" in keys and self.m_entry is not None and self.m_entry.color is not None
        )
        self.m_asset_button.setVisible(bool(base.get("guid")) and links.available(links.SHOW_ASSET))
        self.m_model_button.setVisible(bool(base.get("cfg")) and links.available(links.VIEW_FILE))
        self.m_export_button.setVisible(bool(base.get("cfg")) and links.available(links.EXPORT_FILES))

    def _follow(self, link: str, key: str) -> None:
        value = (self.m_entry.base if self.m_entry is not None else {}).get(key)
        if link == links.EXPORT_FILES:
            value = [value] if value else None
        if value and not links.follow(
            link, game_of({}, kind=self.m_doc.m_info.get("kind") or ""), int(value) if key == "guid" else value
        ):
            if key == "guid":
                self.m_doc.say(tr("islands.asset_not_games_files", value=value))
            else:
                self.m_doc.say(tr("islands.model_not_games_files", value=value))

    def show_entry(self, entry) -> None:
        self.m_entry = entry
        if self.m_doc.m_view.m_water_sel is not None:
            return self.show_water(self.m_doc.m_view.m_water_sel)
        if entry is None or not entry.alive:
            self.m_title.setText(tr("islands.nothing_selected"))
            return self._visible(())
        added = " " + tr("islands.added") if entry.added is not None else ""
        summary = f"<b>{label_of(entry.base)}</b><br>{entry.marker or entry.kind}{added}"
        if entry.kind == "tree":
            self.m_title.setText(tr("islands.forest_tree_tile_del_clears", info=summary, tile=entry.tile))
            return self._visible(())
        if not entry.visible:
            summary += " · " + tr("islands.deleted")
        self.m_title.setText(summary)
        values = self._values(entry)
        self._visible(list(values))
        for field_key, field_value in values.items():
            spin_box = self.m_fields[field_key]
            if not spin_box.hasFocus():
                spin_box.blockSignals(True)
                spin_box.setValue(float(field_value))
                spin_box.blockSignals(False)
        for spin_box in self.m_fields.values():
            spin_box.setEnabled(not self.m_doc.m_session.m_readonly)
        self.m_tint.set_color(entry.color)
        self.m_tint.setEnabled(not self.m_doc.m_session.m_readonly)

    @staticmethod
    def _values(entry) -> dict[str, float]:
        """An object's place, turn and size as the fields show them (a scale per axis: three fields)."""
        yaw, tx, tz = euler_of(entry.rotation)
        values = dict(zip("xyz", entry.position, strict=True)) | {"yaw": yaw, "tx": tx, "tz": tz}
        if isinstance(entry.scale, list):
            return values | dict(zip(("sx", "sy", "sz"), entry.scale, strict=True))
        return values | {"s": entry.scale}

    def _unchanged(self, key: str, now: float) -> bool:
        """Whether the field ``key`` still shows ``now``, the value it was filled with: a field only left (Tab, a
        click elsewhere) reports an edit too, and its rounded value must not be written back as one."""
        spin_box = self.m_fields[key]
        return abs(spin_box.value() - float(now)) <= 0.5 * 10 ** -spin_box.decimals()

    def show_water(self, water) -> None:
        session = self.m_doc.m_session
        if water is None:
            self.m_title.setText(
                tr("islands.click_river_coast_point_lake")
                if session.m_water_net
                else tr("islands.island_no_river_network_water")
            )
            return self._visible(())
        if "lake" in water:
            self.m_title.setText(tr("islands.lake_name", name=water["lake"]))
            self._visible(["level"])
            self.m_fields["level"].setValue(session.lake_level(water["lake"]))
            return
        if "outline" in water:
            self.m_title.setText(
                tr(
                    "islands.lake_outline_point_drag_over",
                    name=water["outline"],
                    point=water["point"] + 1,
                    n=len(session.lake_outline(water["outline"])),
                )
            )
            return self._visible(())
        if "coast" in water:
            point_count = len(session.m_path.coast(water["coast"])["points"])
            self.m_title.setText(
                tr(
                    "islands.coast_line_point_drag_over",
                    name=water["coast"],
                    point=water["point"] + 1,
                    n=point_count,
                )
            )
            return self._visible(())
        if "shape" in water:
            shape = {
                "name": water["k"],
                "point": water["point"] + 1,
                "n": len(session.m_path.shapes(water["shape"])[water["k"]]["points"]),
            }
            self.m_title.setText(
                tr("islands.camera_blocker_point_drag_over", **shape)
                if water["shape"] == "camera"
                else tr("islands.build_blocker_point_drag_over", **shape)
            )
            return self._visible(())
        if water["river"] >= len(session.m_water_net["rivers"]) or water["point"] >= len(
            session.river(water["river"])["points"]
        ):
            return self.show_water(None)  # an undo took the selected point away
        river = session.river(water["river"])
        self.m_title.setText(
            tr(
                "islands.river_point_water_del_remove",
                name=water["river"],
                point=water["point"] + 1,
                n=len(river["points"]),
                level=f"{river['points'][water['point']][1]:.2f}",
            )
        )
        self._visible(["width"])
        self.m_fields["width"].setValue(river["widths"][water["point"]])

    def _edited(self, key: str) -> None:
        """A field was edited (or only left): the selected river point, lake or object takes its value as one change;
        the object's other values stay its own, not the fields' rounded ones."""
        session, value = self.m_doc.m_session, self.m_fields[key].value()
        water = self.m_doc.m_view.m_water_sel
        if key in ("width", "level"):
            if (
                water is not None
                and key == "level"
                and "lake" in water
                and not self._unchanged(key, session.lake_level(water["lake"]))
            ):
                before = session.water_snapshot()
                session.set_lake_level(water["lake"], value)
                session.end_water(before, tr("islands.lake_level"))
            elif water is not None and key == "width" and "river" in water:
                if not self._unchanged(key, session.river(water["river"])["widths"][water["point"]]):
                    before = session.water_snapshot()
                    session.set_river_width(water["river"], water["point"], value)
                    session.end_water(before, tr("islands.river_width"))
            return
        entry = self.m_entry
        if entry is None or not entry.alive or entry.kind == "tree":
            return
        values = self._values(entry)
        if key not in values or self._unchanged(key, values[key]):
            return
        values[key] = value
        if key in ("x", "y", "z"):
            session.transform(entry, tr("islands.move_lower"), position=[values["x"], values["y"], values["z"]])
        elif key in ("yaw", "tx", "tz"):
            session.transform(
                entry, tr("islands.rotation"), rotation=quat_of(values["yaw"], values["tx"], values["tz"])
            )
        elif key == "s":
            session.transform(entry, tr("common.scale"), scale=values["s"])
        else:
            session.transform(entry, tr("common.scale"), scale=[values["sx"], values["sy"], values["sz"]])
