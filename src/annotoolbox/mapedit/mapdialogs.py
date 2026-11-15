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

from ..infra.i18n import tr
from ..suite import widgets
from .grow import STEP
from .placing import MIRRORS
from .template import MINE_SLOT_ACTIVATION, MINE_SLOT_MAPPING, SIZES, TYPES
from .variants import LEVELS

ROLE = Qt.ItemDataRole.UserRole
SHOWN = 2000  # island files listed at most (the filter narrows them)


class IslandDialog(QDialog):
    """Pick an island: the pool's ``RandomIsland`` entries, or with "every island file" any of the game's."""

    def __init__(self, pool_islands: list, files: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Add an island"))
        self.pool_islands, self.files = pool_islands, files
        self.filter = QLineEdit(placeholderText=tr("filter by name or path"))
        self.every = QCheckBox(tr("every island file of the game, not only the pool"))
        self.list = QListWidget()
        lay = QVBoxLayout(self)
        for w in (self.filter, self.every, self.list):
            lay.addWidget(w)
        lay.addWidget(widgets.ok_cancel(self))
        self.filter.textChanged.connect(self.fill)
        self.every.toggled.connect(self.fill)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        self.resize(640, 520)
        self.fill()

    def fill(self) -> None:
        self.list.clear()
        words = self.filter.text().lower().split()
        if self.every.isChecked():
            rows = [(p, p) for p in self.files]
        else:
            rows = [(f"{p.name}  ({tr(p.type)}, {p.region})", p.path) for p in self.pool_islands]
        for text, path in [r for r in rows if all(w in r[0].lower() for w in words)][:SHOWN]:
            item = QListWidgetItem(text)
            item.setData(ROLE, path)
            self.list.addItem(item)

    def chosen(self) -> str:
        item = self.list.currentItem()
        return item.data(ROLE) if item is not None else ""


class SlotDialog(QDialog):
    def __init__(self, sizes: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Add a random slot"))
        self.size, self.type = QComboBox(), QComboBox()
        self.size.addItems(sizes or [tr(s) for s in SIZES[:4]])
        for key, name in TYPES.items():
            self.type.addItem(tr(name), key)
        form = QFormLayout(self)
        form.addRow(tr("size"), self.size)
        form.addRow(tr("type"), self.type)
        form.addRow(widgets.ok_cancel(self))

    def values(self) -> tuple[int, int]:
        return self.size.currentIndex(), self.type.currentData()


class GrowDialog(QDialog):
    """The four sides of a growth, in tiles (multiples of the level header's chunk)."""

    def __init__(self, size: tuple[int, int], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Grow the map"))
        self.sides = []
        form = QFormLayout(self)
        form.addRow(
            QLabel(tr("The map is {w} x {h} tiles. Growing west or south moves every position.", w=size[0], h=size[1]))
        )
        for name in (tr("west"), tr("south"), tr("east"), tr("north")):
            box = QSpinBox()
            box.setRange(0, 8192)
            box.setSingleStep(STEP)
            self.sides.append(box)
            form.addRow(name, box)
        self.widen = QCheckBox(tr("grow the playable area with the map"))
        self.widen.setChecked(True)
        form.addRow(self.widen)
        form.addRow(widgets.ok_cancel(self))

    def values(self) -> tuple[tuple[int, int, int, int], bool]:
        return tuple(b.value() for b in self.sides), self.widen.isChecked()


class ReportDialog(QDialog):
    def __init__(self, lines: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("What the growth moved and kept"))
        text = QPlainTextEdit(readOnly=True)
        text.setPlainText("\n".join(lines))
        lay = QVBoxLayout(self)
        lay.addWidget(text)
        lay.addWidget(widgets.ok_cancel(self))
        self.resize(900, 600)


class NewMapDialog(QDialog):
    """A new map's name (lower-case letters, digits, _) and its ``MapTemplate`` GUID."""

    def __init__(self, name: str, guid: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Write as a new map"))
        self.name = QLineEdit(name)
        self.guid = QSpinBox()
        self.guid.setRange(1, 2_147_483_647)
        self.guid.setValue(max(guid, 1))
        form = QFormLayout(self)
        form.addRow(tr("name"), self.name)
        form.addRow(tr("GUID of its MapTemplate asset"), self.guid)
        form.addRow(widgets.ok_cancel(self))

    def values(self) -> tuple[str, int]:
        return self.name.text().strip().lower(), self.guid.value()


class MirrorDialog(QDialog):
    """How the selection is mirrored (``placing.MIRRORS``) and whether as copies."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Mirrored copies"))
        self.mode = QComboBox()
        for key, title in MIRRORS.items():
            self.mode.addItem(tr(title), key)
        self.copies = QCheckBox(tr("as copies (else the selection itself is mirrored)"))
        self.copies.setChecked(True)
        form = QFormLayout(self)
        form.addRow(QLabel(tr("Mirrored about the playable area's centre. A fixed island turns twice instead.")))
        form.addRow(tr("mirror"), self.mode)
        form.addRow(self.copies)
        form.addRow(widgets.ok_cancel(self))

    def values(self) -> tuple[str, bool]:
        return self.mode.currentData(), self.copies.isChecked()


class VariantsDialog(QDialog):
    """The three difficulties: as new maps (a name and a GUID each) or over the vanilla siblings."""

    def __init__(self, name: str, guids: dict[str, int], siblings: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Write easy, medium and hard"))
        self.as_new = QCheckBox(tr("as three new maps (else over the vanilla easy and hard maps)"))
        self.as_new.setChecked(True)
        self.as_new.setEnabled(siblings)
        self.name = QLineEdit(name)
        self.guids = {}
        form = QFormLayout(self)
        form.addRow(QLabel(tr("The open map is the medium one; easy and hard are derived from it.")))
        form.addRow(self.as_new)
        form.addRow(tr("name (without _easy, _medium, _hard)"), self.name)
        for level in LEVELS:
            box = QSpinBox()
            box.setRange(1, 2_147_483_647)
            box.setValue(max(guids.get(level.key, 1), 1))
            self.guids[level.key] = box
            form.addRow(tr("GUID of the {level} map", level=tr(level.title)), box)
        form.addRow(widgets.ok_cancel(self))

    def values(self) -> tuple[bool, str, dict[str, int]]:
        return self.as_new.isChecked(), self.name.text().strip().lower(), {k: b.value() for k, b in self.guids.items()}


def _numbers(text: str) -> tuple[int, ...]:
    return tuple(int(g) for g in text.replace(";", ",").split(",") if g.strip())


def pick_guid(parent, title: str, named: dict[int, str]) -> int | None:
    """One of the game's assets ``named`` (GUID -> name), searched by name or GUID; None when cancelled."""

    def rows(query: str) -> list[tuple[str, object]]:
        q = query.strip().lower()
        found = sorted(named.items(), key=lambda kv: kv[1].lower())
        return [(f"{name}  ·  {guid}", guid) for guid, name in found if not q or q in name.lower() or q in str(guid)]

    return widgets.pick_from_list(parent, title, tr("{n} assets of the game", n=len(named)), rows)


def with_guid(text: str, guid: int) -> str:
    """A comma list of GUIDs with ``guid`` added at its end (once)."""
    have = [g.strip() for g in text.replace(";", ",").split(",") if g.strip()]
    return ", ".join(have if str(guid) in have else [*have, str(guid)])


class DetailsDialog(QDialog):
    """A fixed island's mine slots (each with its value) and, for 117, its areas' fertility set and fertilities."""

    def __init__(self, element, slots: list, mine_field: str, maps, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Mine slots and fertilities"))
        self.mine_field, self.maps = mine_field, maps  # ``pool.GameMaps``: the game's slot and fertility assets
        slot_assets = maps.slot_assets
        values = dict(element.mine_slot_activation)
        names = {s.id: s.name for s in slots}
        ids = [s.id for s in slots] + [k for k in values if k not in names]
        self.slot_ids = ids  # by row: an object id is 64 bits, more than Qt's item data holds
        self.mine_slot_activation = QTableWidget(len(ids), 3)
        self.mine_slot_activation.setHorizontalHeaderLabels(
            [tr("slot object"), tr("on the island"), self._value_title(mine_field)]
        )
        for row, oid in enumerate(ids):
            self.mine_slot_activation.setItem(row, 0, QTableWidgetItem(f"{oid:#x}"))
            self.mine_slot_activation.setItem(row, 1, QTableWidgetItem(names.get(oid, tr("(not on the island)"))))
            self.mine_slot_activation.setCellWidget(row, 2, self._value_box(values.get(oid), slot_assets))
        self.areas = QTableWidget(0, 3)
        self.areas.setHorizontalHeaderLabels([tr("area"), tr("fertility set GUID"), tr("fertility GUIDs")])
        sets = dict(element.fertility_set_guids)
        for area in sorted(set(sets) | {a for a, _g in element.fertilities_per_area_index}):
            fert = next((gs for a, gs in element.fertilities_per_area_index if a == area), ())
            self._area_row(str(area), str(sets.get(area, "")), ", ".join(str(g) for g in fert))
        self.problem = QLabel()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(self._value_title(mine_field) if mine_field else tr("The template keeps no mine slots")))
        lay.addWidget(self.mine_slot_activation)
        self.mine_slot_activation.setEnabled(bool(mine_field))
        if mine_field == MINE_SLOT_ACTIVATION:
            lay.addWidget(QLabel(tr("Areas (117)")))
            lay.addWidget(self.areas)
            row = QHBoxLayout()
            add, drop = QPushButton(tr("Add an area")), QPushButton(tr("Remove the area"))
            add.clicked.connect(lambda: self._area_row("1", "", ""))
            drop.clicked.connect(lambda: self.areas.removeRow(self.areas.currentRow()))
            pick_set, pick_one = QPushButton(tr("The area's set...")), QPushButton(tr("Add a fertility..."))
            pick_set.clicked.connect(lambda: self.pick_into(1, self.maps.fertility_sets))
            pick_one.clicked.connect(lambda: self.pick_into(2, self.maps.fertilities))
            for button in (add, drop, pick_set, pick_one):
                row.addWidget(button)
            lay.addLayout(row)
        lay.addWidget(self.problem)
        lay.addWidget(widgets.ok_cancel(self))
        self.resize(720, 560)

    def _value_box(self, value, slot_assets: dict[int, str]) -> QComboBox:
        box = QComboBox()
        box.addItem(tr("(not listed)"), None)
        if self.mine_field == MINE_SLOT_ACTIVATION:
            box.addItem(tr("active"), 1)
            box.addItem(tr("inactive"), 0)
        else:
            box.addItem(tr("random"), 0)
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
        row = self.areas.currentRow()
        if row < 0:
            return self.problem.setText(tr("select an area first"))
        title = tr("The area's fertility set") if column == 1 else tr("A fertility of the area")
        guid = pick_guid(self, title, named)
        if guid is not None:
            cell = self.areas.item(row, column)
            text = str(guid) if column == 1 else with_guid(cell.text() if cell else "", guid)
            self.areas.setItem(row, column, QTableWidgetItem(text))

    def _area_row(self, area: str, guid: str, fertilities: str) -> None:
        row = self.areas.rowCount()
        self.areas.insertRow(row)
        for col, text in enumerate((area, guid, fertilities)):
            self.areas.setItem(row, col, QTableWidgetItem(text))

    @staticmethod
    def _value_title(mine_field: str) -> str:
        """117's MineSlotActivation; 1800's MineSlotMapping, its form of the same list, named as it is."""
        return (
            tr("MineSlotActivation")
            if mine_field != MINE_SLOT_MAPPING
            else tr("MineSlotMapping (1800's MineSlotActivation)")
        )

    def values(self) -> dict:
        """The element's ``mine_slot_activation``, ``fertility_set_guids`` and ``fertilities_per_area_index``; ``ValueError`` for text that is no number."""
        mines = []
        for row in range(self.mine_slot_activation.rowCount()):
            value = self.mine_slot_activation.cellWidget(row, 2).currentData()
            if value is not None:
                mines.append((self.slot_ids[row], value))
        sets, fertilities = [], []
        for row in range(self.areas.rowCount()):
            cells = [self.areas.item(row, c).text().strip() if self.areas.item(row, c) else "" for c in range(3)]
            area = int(cells[0])
            if cells[1]:
                sets.append((area, int(cells[1])))
            if cells[2]:
                fertilities.append((area, _numbers(cells[2])))
        return {
            "mine_slot_activation": tuple(mines),
            "fertility_set_guids": tuple(sets),
            "fertilities_per_area_index": tuple(fertilities),
        }

    def accept(self) -> None:
        try:
            self.values()
        except ValueError:
            self.problem.setText(tr("an area, a set and the fertilities are numbers (GUIDs, separated by commas)"))
            return
        super().accept()
