"""The map editor's right panel (Qt): the selected element's fields and the map's size and playable areas, each
applied as one undoable edit of the open map, and the checks' findings.

``CMapPanel(apply_element, apply_map, details)`` calls back into the window; ``show_element`` and ``show_map`` fill it.
docs/map-editor.md "The window".
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from .mapdialogs import guid_list, pick_asset, with_guid
from .template import KIND_FIXED, KIND_SLOT, KIND_START, SIZES, SNAP, TYPES
from .worlds import BITS_1701

KIND_NAMES = {
    KIND_FIXED: N_("maps.fixed_island"),
    KIND_SLOT: N_("maps.random_slot"),
    KIND_START: N_("maps.start_position"),
}
BIT_TITLES = (N_("maps.culture_bits_1701"), N_("maps.fertility_bits_1701"), N_("maps.resource_bits_1701"))
LIMIT = 16384  # the largest coordinate the boxes take (TAMPER's largest map is 8192)


def _spin(step: int = SNAP, low: int = -LIMIT) -> QSpinBox:
    box = QSpinBox()
    box.setRange(low, LIMIT)
    box.setSingleStep(step)
    return box


class CMapPanel(QWidget):
    def __init__(
        self, apply_element: Callable[[dict], None], apply_map: Callable[[dict], None], details: Callable[[], None]
    ) -> None:
        super().__init__()
        self._m_apply_element, self._m_apply_map = apply_element, apply_map
        self.m_random = False  # the map is an older game's random-map template (its slots have edges and climates)
        self.m_game = ""
        self.m_kind = QLabel(tr("islands.nothing_selected"))
        self.m_x, self.m_z = _spin(), _spin()
        self.m_size = QComboBox()
        self.m_size.addItems([tr(size_name) for size_name in SIZES])
        self.m_clime = _spin(1, 0)
        self.m_clime.setMaximum(255)
        self.m_details = QPushButton(tr("maps.mine_slots_fertilities"))
        self.m_details.clicked.connect(details)
        self.m_type = QComboBox()
        for key, name in TYPES.items():
            self.m_type.addItem(tr(name), key)
        self.m_rotation90 = QComboBox()
        self.m_rotation90.addItems(["0°", "90°", "180°", "270°"])  # i18n: ok
        self.m_map_file_path = QLineEdit()
        self.m_island_label = QLineEdit()
        self.m_fertility_guids = QLineEdit(placeholderText=tr("maps.guids_separated_commas"))
        self.m_pick_fertility = QToolButton(text="…", toolTip=tr("maps.tip.add_one_games_fertilities"))
        self.m_pick_fertility.clicked.connect(self._pick_fertility)
        self.m_fertility_names: dict[int, str] = {}  # the open game's ``Fertility`` assets
        self.m_bits = {name: _spin(1, 0) for name in BITS_1701}  # 1701's placeholder bits
        for box in self.m_bits.values():
            box.setMaximum(2**31 - 1)
        self.m_randomize_fertilities = QCheckBox(tr("maps.randomizefertilities"))
        self.m_rotation90.setToolTip(tr("maps.tip.quarter_turns_island_counter_clockwise"))
        self.m_map_file_path.setToolTip(tr("maps.tip.island_file_place_holds"))
        self.m_island_label.setToolTip(tr("maps.tip.name_island_known_map"))
        self.m_fertility_guids.setToolTip(tr("maps.tip.fertilities_island_guids"))
        self.m_randomize_fertilities.setToolTip(tr("maps.tip.game_deals_fertilities_when_starts"))
        self.m_map_w, self.m_map_h = _spin(64, 64), _spin(64, 64)
        self.m_area = [_spin(4, 0) for _ in range(4)]
        self.m_initial = [_spin(4, 0) for _ in range(4)]
        self.m_findings = QListWidget()
        self._build()
        self.show_element(None)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        sel = QGroupBox(tr("islands.selection"))
        form = QFormLayout(sel)
        form.addRow(self.m_kind)
        form.addRow(tr("maps.x_east"), self.m_x)
        form.addRow(tr("maps.z_north"), self.m_z)
        form.addRow(tr("viewer.size"), self.m_size)
        form.addRow(tr("maps.climate_bits"), self.m_clime)
        for name, title in zip(BITS_1701, BIT_TITLES, strict=True):
            form.addRow(tr(title), self.m_bits[name])
        form.addRow(tr("sound.type"), self.m_type)
        form.addRow(tr("maps.rotation90"), self.m_rotation90)
        form.addRow(tr("maps.mapfilepath"), self.m_map_file_path)
        form.addRow(tr("maps.islandlabel"), self.m_island_label)
        fertility_row = QHBoxLayout()
        fertility_row.addWidget(self.m_fertility_guids)
        fertility_row.addWidget(self.m_pick_fertility)
        form.addRow(tr("maps.fertilityguids"), fertility_row)
        form.addRow(self.m_randomize_fertilities)
        form.addRow(self.m_details)
        apply = QPushButton(tr("maps.apply_to_selection"))
        apply.clicked.connect(self._element_values)
        form.addRow(apply)
        lay.addWidget(sel)
        box = QGroupBox(tr("maps.map"))
        mform = QFormLayout(box)
        size_row = QHBoxLayout()
        size_row.addWidget(self.m_map_w)
        size_row.addWidget(self.m_map_h)
        mform.addRow(tr("maps.size_x_z"), size_row)
        for title, boxes in (
            (tr("maps.playable_area"), self.m_area),
            (tr("maps.initial_playable_area"), self.m_initial),
        ):
            row = QHBoxLayout()
            for spin_box in boxes:
                row.addWidget(spin_box)
            mform.addRow(title, row)
        apply_map = QPushButton(tr("maps.apply_to_map"))
        apply_map.clicked.connect(self._map_values)
        mform.addRow(apply_map)
        lay.addWidget(box)
        lay.addWidget(QLabel(tr("maps.checks_double_click_selects")))
        lay.addWidget(self.m_findings, 1)

    def show_element(self, element, size: int | None = None) -> None:
        """Fill the fields from the picked element (None: none picked) and enable those its kind and game have;
        ``size``: the size class to show (``CMapDoc.size_index``; a fixed island stores none)."""
        widgets = (
            self.m_x,
            self.m_z,
            self.m_size,
            self.m_type,
            self.m_rotation90,
            self.m_map_file_path,
            self.m_island_label,
            self.m_fertility_guids,
        )
        for widget in (
            *widgets,
            self.m_randomize_fertilities,
            self.m_clime,
            self.m_details,
            self.m_pick_fertility,
            *self.m_bits.values(),
        ):
            widget.setEnabled(element is not None)
        if element is None:
            self.m_kind.setText(tr("islands.nothing_selected"))
            return
        self.m_kind.setText(tr(KIND_NAMES[element.kind]))
        self.m_x.setValue(element.x)
        self.m_z.setValue(element.z)
        self.m_size.setCurrentIndex(min(element.size if size is None else size, self.m_size.count() - 1))
        self.m_clime.setValue(element.clime)
        for name, box in self.m_bits.items():
            box.setValue(getattr(element, name))
            box.setEnabled(element.kind == KIND_SLOT and self.m_random and self.m_game == "anno1701")
        self._show_type(element.type)
        self.m_rotation90.setCurrentIndex(element.Rotation90 % 4)
        self.m_map_file_path.setText(element.MapFilePath)
        self.m_island_label.setText(element.IslandLabel)
        self.m_fertility_guids.setText(", ".join(str(guid) for guid in element.FertilityGuids))
        self.m_randomize_fertilities.setChecked(element.RandomizeFertilities)
        self.m_size.setEnabled(element.kind == KIND_SLOT)
        self.m_clime.setEnabled(element.kind == KIND_SLOT and self.m_random)
        self.m_type.setEnabled(element.kind != KIND_START)
        fixed = (
            self.m_rotation90,
            self.m_map_file_path,
            self.m_island_label,
            self.m_fertility_guids,
            self.m_randomize_fertilities,
            self.m_details,
        )
        for widget in (*fixed, self.m_pick_fertility):
            widget.setEnabled(element.kind == KIND_FIXED)

    def _show_type(self, value: int) -> None:
        """The type box on ``value``; a type this app has no name for gets an entry of its number, so Apply keeps it."""
        while self.m_type.count() > len(TYPES):
            self.m_type.removeItem(self.m_type.count() - 1)
        if self.m_type.findData(value) < 0:
            self.m_type.addItem(str(value), value)  # i18n: ok (a number)
        self.m_type.setCurrentIndex(self.m_type.findData(value))

    def _pick_fertility(self) -> None:
        guid = pick_asset(self, tr("maps.fertility_island"), self.m_fertility_names)
        if guid is not None:
            self.m_fertility_guids.setText(with_guid(self.m_fertility_guids.text(), guid))

    def show_map(self, doc) -> None:
        if doc is None:
            return
        self.m_random = not doc.is_template and doc.m_record.random
        self.m_game, self.m_fertility_names = doc.m_game, doc.m_maps.m_fertilities
        names = doc.size_names()
        if [self.m_size.itemText(i) for i in range(self.m_size.count())] != names:
            self.m_size.clear()
            self.m_size.addItems(names)
        self.m_map_w.setValue(doc.size[0])
        self.m_map_h.setValue(doc.size[1])
        for box, value in zip(self.m_area, doc.playable, strict=True):
            box.setValue(value)
        initial = doc.initial
        for box, value in zip(self.m_initial, initial or (0, 0, 0, 0), strict=True):
            box.setValue(value)
            box.setEnabled(initial is not None)

    def _element_values(self) -> None:
        try:
            fert = guid_list(self.m_fertility_guids.text())
        except ValueError:
            self.m_kind.setText(tr("maps.fertilities_must_guids_numbers"))
            return
        self._m_apply_element(
            {
                "x": self.m_x.value(),
                "z": self.m_z.value(),
                "size": self.m_size.currentIndex(),
                "clime": self.m_clime.value(),
                "type": self.m_type.currentData(),
                "Rotation90": self.m_rotation90.currentIndex(),
                "MapFilePath": self.m_map_file_path.text().strip().replace("\\", "/").lower(),
                "IslandLabel": self.m_island_label.text().strip(),
                "FertilityGuids": fert,
                "RandomizeFertilities": self.m_randomize_fertilities.isChecked(),
                **{name: box.value() for name, box in self.m_bits.items()},
            }
        )

    def _map_values(self) -> None:
        self._m_apply_map(
            {
                "size": (self.m_map_w.value(), self.m_map_h.value()),
                "playable": tuple(box.value() for box in self.m_area),
                "initial": tuple(box.value() for box in self.m_initial) if self.m_initial[0].isEnabled() else None,
            }
        )
