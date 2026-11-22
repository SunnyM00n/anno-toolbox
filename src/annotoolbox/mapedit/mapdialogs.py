"""The map editor's dialogs (Qt): an island from the game's pool or any island file of the game, a random slot's size
and type, the four sides of a growth, the report of a growth, a new map's name and GUID, mirrored copies, the three
difficulties, and a fixed island's mine slots and per-area fertilities.
docs/map-editor.md "The window", "Growing a map on any side", "Mirrored copies", "Three difficulties from one map"
and "Mine slots and fertilities".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..infra.i18n import english, tr
from ..suite import widgets
from .grow import STEP
from .placing import MIRRORS
from .template import MINE_SLOT_ACTIVATION, MINE_SLOT_MAPPING, SIZES, TYPES
from .variants import LEVELS

ROLE = Qt.ItemDataRole.UserRole
SHOWN = 2000  # island files listed at most (the filter narrows them)


class CIslandDialog(QDialog):
    """Pick an island: the pool's ``RandomIsland`` entries, or with "every island file" any of the game's."""

    def __init__(self, pool_islands: list, files: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("maps.title.add_island"))
        self.m_pool_islands, self.m_files = pool_islands, files
        self.m_filter = QLineEdit(placeholderText=tr("maps.filter_name_path"))
        self.m_every = QCheckBox(tr("maps.every_island_file_game_not"))
        self.m_list = QListWidget()
        lay = QVBoxLayout(self)
        for widget in (self.m_filter, self.m_every, self.m_list):
            lay.addWidget(widget)
        lay.addWidget(widgets.ok_cancel(self))
        self.m_filter.textChanged.connect(self.fill)
        self.m_every.toggled.connect(self.fill)
        self.m_list.itemDoubleClicked.connect(lambda _i: self.accept())
        self.resize(640, 520)
        self.fill()

    def fill(self) -> None:
        self.m_list.clear()
        words = self.m_filter.text().lower().split()
        if self.m_every.isChecked():
            rows = [(file, file) for file in self.m_files]
        else:
            titles = {english(title): title for title in TYPES.values()}  # a type the game names by our English word
            rows = [
                (f"{file.name}  ({tr(titles.get(file.type, file.type))}, {file.region})", file.path)
                for file in self.m_pool_islands
            ]
        for text, path in [row for row in rows if all(word in row[0].lower() for word in words)][:SHOWN]:
            item = QListWidgetItem(text)
            item.setData(ROLE, path)
            self.m_list.addItem(item)

    def chosen(self) -> str:
        item = self.m_list.currentItem()
        return item.data(ROLE) if item is not None else ""


class CSlotDialog(QDialog):
    def __init__(self, sizes: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("maps.title.add_random_slot"))
        self.m_size, self.m_type = QComboBox(), QComboBox()
        self.m_size.addItems(sizes or [tr(size_name) for size_name in SIZES[:4]])
        for key, name in TYPES.items():
            self.m_type.addItem(tr(name), key)
        form = QFormLayout(self)
        form.addRow(tr("viewer.size"), self.m_size)
        form.addRow(tr("sound.type"), self.m_type)
        form.addRow(widgets.ok_cancel(self))

    def values(self) -> tuple[int, int]:
        return self.m_size.currentIndex(), self.m_type.currentData()


class CGrowDialog(QDialog):
    """The four sides of a growth, in tiles (multiples of the level header's chunk)."""

    def __init__(self, size_tiles: tuple[int, int], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("maps.title.grow_map"))
        self.m_sides = []
        form = QFormLayout(self)
        form.addRow(QLabel(tr("maps.map_x_tiles_growing_west", w=size_tiles[0], h=size_tiles[1])))
        for name in (tr("maps.west"), tr("maps.south"), tr("maps.east"), tr("maps.north")):
            box = QSpinBox()
            box.setRange(0, 8192)
            box.setSingleStep(STEP)
            self.m_sides.append(box)
            form.addRow(name, box)
        self.m_widen = QCheckBox(tr("maps.grow_playable_area_map"))
        self.m_widen.setChecked(True)
        form.addRow(self.m_widen)
        form.addRow(widgets.ok_cancel(self))

    def values(self) -> tuple[tuple[int, int, int, int], bool]:
        return tuple(box.value() for box in self.m_sides), self.m_widen.isChecked()


class CReportDialog(QDialog):
    def __init__(self, lines: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("maps.title.what_growth_moved_kept"))
        text = QPlainTextEdit(readOnly=True)
        text.setPlainText("\n".join(lines))
        lay = QVBoxLayout(self)
        lay.addWidget(text)
        lay.addWidget(widgets.ok_cancel(self))
        self.resize(900, 600)


class CNewMapDialog(QDialog):
    """A new map's name (lower-case letters, digits, _) and its ``MapTemplate`` GUID."""

    def __init__(self, name: str, guid: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("maps.title.write_new_map"))
        self.m_name = QLineEdit(name)
        self.m_guid = QSpinBox()
        self.m_guid.setRange(1, 2_147_483_647)
        self.m_guid.setValue(max(guid, 1))
        form = QFormLayout(self)
        form.addRow(tr("common.name_lower"), self.m_name)
        form.addRow(tr("maps.guid_maptemplate_asset"), self.m_guid)
        form.addRow(widgets.ok_cancel(self))

    def values(self) -> tuple[str, int]:
        return self.m_name.text().strip().lower(), self.m_guid.value()


class CMirrorDialog(QDialog):
    """How the selection is mirrored (``placing.MIRRORS``) and whether as copies."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("maps.title.mirrored_copies"))
        self.m_mode = QComboBox()
        for key, title in MIRRORS.items():
            self.m_mode.addItem(tr(title), key)
        self.m_copies = QCheckBox(tr("maps.copies_else_selection_itself_mirrored"))
        self.m_copies.setChecked(True)
        form = QFormLayout(self)
        form.addRow(QLabel(tr("maps.mirrored_about_playable_areas_centre")))
        form.addRow(tr("maps.mirror"), self.m_mode)
        form.addRow(self.m_copies)
        form.addRow(widgets.ok_cancel(self))

    def values(self) -> tuple[str, bool]:
        return self.m_mode.currentData(), self.m_copies.isChecked()


class CVariantsDialog(QDialog):
    """The three difficulties: as new maps (a name and a GUID each) or over the vanilla siblings."""

    def __init__(self, name: str, guids: dict[str, int], siblings: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("maps.title.write_easy_medium_hard"))
        self.m_as_new = QCheckBox(tr("maps.three_new_maps_else_over"))
        self.m_as_new.setChecked(True)
        self.m_as_new.setEnabled(siblings)
        self.m_name = QLineEdit(name)
        self.m_guids = {}
        form = QFormLayout(self)
        form.addRow(QLabel(tr("maps.open_map_medium_one_easy")))
        form.addRow(self.m_as_new)
        form.addRow(tr("maps.name_without_easy_medium_hard"), self.m_name)
        for level in LEVELS:
            box = QSpinBox()
            box.setRange(1, 2_147_483_647)
            box.setValue(max(guids.get(level.key, 1), 1))
            self.m_guids[level.key] = box
            form.addRow(tr("maps.guid_level_map", level=tr(level.title)), box)
        form.addRow(widgets.ok_cancel(self))

    def values(self) -> tuple[bool, str, dict[str, int]]:
        return (
            self.m_as_new.isChecked(),
            self.m_name.text().strip().lower(),
            {k: box.value() for k, box in self.m_guids.items()},
        )


GUID_MAX = 2**32 - 1  # a GUID is stored in 32 bits
AREA_RANGE = (-(2**15), 2**15 - 1)  # an area index in 16, signed


def guid_list(text: str) -> tuple[int, ...]:
    """A comma (or semicolon) list of GUIDs as numbers; ``ValueError`` for text that is none, or a number no
    GUID can be (the file could not hold it)."""
    found = tuple(int(guid) for guid in text.replace(";", ",").split(",") if guid.strip())
    if any(not 0 <= guid <= GUID_MAX for guid in found):
        raise ValueError(f"a GUID lies in 0..{GUID_MAX}")
    return found


def pick_asset(parent, title: str, named: dict[int, str]) -> int | None:
    """One of the game's assets ``named`` (GUID -> name), searched by name or GUID; None when cancelled."""

    def rows(query: str) -> list[tuple[str, object]]:
        needle = query.strip().lower()
        found = sorted(named.items(), key=lambda kv: kv[1].lower())
        return [
            (f"{name}  ·  {guid}", guid)
            for guid, name in found
            if not needle or needle in name.lower() or needle in str(guid)
        ]

    return widgets.pick_from_list(parent, title, tr("maps.n_assets_game", n=len(named)), rows)


def with_guid(text: str, guid: int) -> str:
    """A comma list of GUIDs with ``guid`` added at its end (once)."""
    have = [existing.strip() for existing in text.replace(";", ",").split(",") if existing.strip()]
    return ", ".join(have if str(guid) in have else [*have, str(guid)])


class CDetailsDialog(QDialog):
    """A fixed island's mine slots (each with its value) and, for 117, its areas' fertility set and fertilities."""

    def __init__(self, element, slots: list, mine_field: str, maps, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("maps.title.mine_slots_fertilities"))
        self.m_mine_field, self.m_maps = mine_field, maps  # ``pool.CGameMaps``: the game's slot and fertility assets
        slot_assets = maps.m_slot_assets
        values = dict(element.MineSlotActivation)
        names = {slot.id: slot.name for slot in slots}
        ids = [slot.id for slot in slots] + [k for k in values if k not in names]
        self.m_slot_ids = ids  # by row: an object id is 64 bits, more than Qt's item data holds
        self.m_mine_slot_activation = QTableWidget(len(ids), 3)
        self.m_mine_slot_activation.setHorizontalHeaderLabels(
            [tr("maps.slot_object"), tr("maps.on_the_island"), self._value_title(mine_field)]
        )
        for row, oid in enumerate(ids):
            self.m_mine_slot_activation.setItem(row, 0, QTableWidgetItem(f"{oid:#x}"))
            self.m_mine_slot_activation.setItem(row, 1, QTableWidgetItem(names.get(oid, tr("maps.not_island"))))
            self.m_mine_slot_activation.setCellWidget(row, 2, self._value_box(values.get(oid), slot_assets))
        self.m_areas = QTableWidget(0, 3)
        self.m_areas.setHorizontalHeaderLabels(
            [tr("maps.area"), tr("maps.fertility_set_guid"), tr("maps.fertility_guids")]
        )
        sets = dict(element.FertilitySetGUIDs)
        for area in sorted(set(sets) | {area_number for area_number, _g in element.FertilitiesPerAreaIndex}):
            fert = next((gs for area_number, gs in element.FertilitiesPerAreaIndex if area_number == area), ())
            self._area_row(str(area), str(sets.get(area, "")), ", ".join(str(guid) for guid in fert))
        self.m_problem = QLabel()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(self._value_title(mine_field) if mine_field else tr("maps.template_keeps_no_mine_slots")))
        lay.addWidget(self.m_mine_slot_activation)
        self.m_mine_slot_activation.setEnabled(bool(mine_field))
        if mine_field == MINE_SLOT_ACTIVATION:
            lay.addWidget(QLabel(tr("maps.areas_117")))
            lay.addWidget(self.m_areas)
            row = QHBoxLayout()
            add, drop = QPushButton(tr("maps.add_area")), QPushButton(tr("maps.remove_area"))
            add.clicked.connect(lambda: self._area_row("1", "", ""))
            drop.clicked.connect(lambda: self.m_areas.removeRow(self.m_areas.currentRow()))
            pick_set, pick_one = QPushButton(tr("maps.the_areas_set")), QPushButton(tr("maps.add_fertility"))
            pick_set.clicked.connect(lambda: self.pick_into(1, self.m_maps.m_fertility_sets))
            pick_one.clicked.connect(lambda: self.pick_into(2, self.m_maps.m_fertilities))
            for button in (add, drop, pick_set, pick_one):
                row.addWidget(button)
            lay.addLayout(row)
        lay.addWidget(self.m_problem)
        lay.addWidget(widgets.ok_cancel(self))
        self.resize(720, 560)

    def _value_box(self, value, slot_assets: dict[int, str]) -> QComboBox:
        box = QComboBox()
        box.addItem(tr("maps.not_listed"), None)
        if self.m_mine_field == MINE_SLOT_ACTIVATION:
            box.addItem(tr("maps.active"), 1)
            box.addItem(tr("maps.inactive"), 0)
        else:
            box.addItem(tr("maps.random"), 0)
            for guid, name in sorted(slot_assets.items(), key=lambda kv: kv[1].lower()):
                box.addItem(f"{name} ({guid})", guid)  # i18n: ok
        index = box.findData(value) if value is not None else 0
        box.setCurrentIndex(index if index >= 0 else 0)
        if value is not None and index < 0:  # a value the game's list does not name
            box.addItem(str(value), value)  # i18n: ok
            box.setCurrentIndex(box.count() - 1)
        return box

    def pick_into(self, column: int, named: dict[int, str]) -> None:
        """A game asset picked into the current area's cell: the set (column 1) replaced, a fertility (2) added."""
        row = self.m_areas.currentRow()
        if row < 0:
            return self.m_problem.setText(tr("maps.select_area_first"))
        title = tr("maps.areas_fertility_set") if column == 1 else tr("maps.fertility_area")
        guid = pick_asset(self, title, named)
        if guid is not None:
            cell = self.m_areas.item(row, column)
            text = str(guid) if column == 1 else with_guid(cell.text() if cell else "", guid)
            self.m_areas.setItem(row, column, QTableWidgetItem(text))

    def _area_row(self, area: str, guid: str, fertilities: str) -> None:
        row = self.m_areas.rowCount()
        self.m_areas.insertRow(row)
        for column, text in enumerate((area, guid, fertilities)):
            self.m_areas.setItem(row, column, QTableWidgetItem(text))

    @staticmethod
    def _value_title(mine_field: str) -> str:
        """117's MineSlotActivation; 1800's MineSlotMapping, its form of the same list, named as it is."""
        return (
            tr("maps.mineslotactivation")
            if mine_field != MINE_SLOT_MAPPING
            else tr("maps.mineslotmapping_1800s_mineslotactivation")
        )

    def values(self) -> dict:
        """The element's ``MineSlotActivation``, ``FertilitySetGUIDs`` and ``FertilitiesPerAreaIndex``; ``ValueError`` for text that is no number."""
        mines = []
        for row in range(self.m_mine_slot_activation.rowCount()):
            value = self.m_mine_slot_activation.cellWidget(row, 2).currentData()
            if value is not None:
                mines.append((self.m_slot_ids[row], value))
        sets, fertilities = [], []
        for row in range(self.m_areas.rowCount()):
            cells = [
                self.m_areas.item(row, column).text().strip() if self.m_areas.item(row, column) else ""
                for column in range(3)
            ]
            area = int(cells[0])
            if not AREA_RANGE[0] <= area <= AREA_RANGE[1]:
                raise ValueError(f"an area index lies in {AREA_RANGE[0]}..{AREA_RANGE[1]}")
            if cells[1]:
                (fertility_set,) = guid_list(cells[1])  # one GUID: more is a ValueError too
                sets.append((area, fertility_set))
            if cells[2]:
                fertilities.append((area, guid_list(cells[2])))
        return {
            "MineSlotActivation": tuple(mines),
            "FertilitySetGUIDs": tuple(sets),
            "FertilitiesPerAreaIndex": tuple(fertilities),
        }

    def accept(self) -> None:
        try:
            self.values()
        except ValueError:
            self.m_problem.setText(tr("maps.area_set_fertilities_numbers_guids"))
            return
        super().accept()
