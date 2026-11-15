"""The map editor's right panel (Qt): the selected element's fields and the map's size and playable areas, each
applied as one undoable edit of the open map, and the checks' findings.

``MapPanel(apply_element, apply_map, details)`` calls back into the window; ``show_element`` and ``show_map`` fill it.
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
from .mapdialogs import pick_guid, with_guid
from .template import KIND_FIXED, KIND_SLOT, KIND_START, SIZES, SNAP, TYPES
from .worlds import BITS_1701

KIND_NAMES = {KIND_FIXED: N_("fixed island"), KIND_SLOT: N_("random slot"), KIND_START: N_("start position")}
BIT_TITLES = (N_("culture bits (1701)"), N_("fertility bits (1701)"), N_("resource bits (1701)"))
LIMIT = 16384  # the largest coordinate the boxes take (TAMPER's largest map is 8192)


def _spin(step: int = SNAP, low: int = -LIMIT) -> QSpinBox:
    box = QSpinBox()
    box.setRange(low, LIMIT)
    box.setSingleStep(step)
    return box


class MapPanel(QWidget):
    def __init__(
        self, apply_element: Callable[[dict], None], apply_map: Callable[[dict], None], details: Callable[[], None]
    ) -> None:
        super().__init__()
        self._apply_element, self._apply_map = apply_element, apply_map
        self.random = False  # the map is an older game's random-map template (its slots have edges and climates)
        self.game = ""
        self.kind = QLabel(tr("nothing selected"))
        self.x, self.z = _spin(), _spin()
        self.size = QComboBox()
        self.size.addItems([tr(s) for s in SIZES])
        self.clime = _spin(1, 0)
        self.clime.setMaximum(255)
        self.details = QPushButton(tr("Mine slots and fertilities..."))
        self.details.clicked.connect(details)
        self.type = QComboBox()
        for key, name in TYPES.items():
            self.type.addItem(tr(name), key)
        self.rotation90 = QComboBox()
        self.rotation90.addItems(["0°", "90°", "180°", "270°"])  # i18n: ok
        self.map_file_path = QLineEdit()
        self.island_label = QLineEdit()
        self.fertility_guids = QLineEdit(placeholderText=tr("GUIDs, separated by commas"))
        self.pick_fertility = QToolButton(text="...", toolTip=tr("add one of the game's fertilities"))
        self.pick_fertility.clicked.connect(self._pick_fertility)
        self.fertility_names: dict[int, str] = {}  # the open game's ``Fertility`` assets
        self.bits = {name: _spin(1, 0) for name in BITS_1701}  # 1701's placeholder bits
        for box in self.bits.values():
            box.setMaximum(2**31 - 1)
        self.randomize_fertilities = QCheckBox(tr("RandomizeFertilities"))
        self.rotation90.setToolTip(tr("quarter turns of the island, counter-clockwise seen from above"))
        self.map_file_path.setToolTip(tr("the island file this place holds"))
        self.island_label.setToolTip(tr("the name the island is known by in the map"))
        self.fertility_guids.setToolTip(tr("the fertilities the island has, as GUIDs"))
        self.randomize_fertilities.setToolTip(tr("the game deals the fertilities when a game starts"))
        self.map_w, self.map_h = _spin(64, 64), _spin(64, 64)
        self.area = [_spin(4, 0) for _ in range(4)]
        self.initial = [_spin(4, 0) for _ in range(4)]
        self.findings = QListWidget()
        self._build()
        self.show_element(None)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        sel = QGroupBox(tr("Selection"))
        form = QFormLayout(sel)
        form.addRow(self.kind)
        form.addRow(tr("x (east)"), self.x)
        form.addRow(tr("z (north)"), self.z)
        form.addRow(tr("size"), self.size)
        form.addRow(tr("climate bits"), self.clime)
        for name, title in zip(BITS_1701, BIT_TITLES, strict=True):
            form.addRow(tr(title), self.bits[name])
        form.addRow(tr("type"), self.type)
        form.addRow(tr("Rotation90"), self.rotation90)
        form.addRow(tr("MapFilePath"), self.map_file_path)
        form.addRow(tr("IslandLabel"), self.island_label)
        fertility_row = QHBoxLayout()
        fertility_row.addWidget(self.fertility_guids)
        fertility_row.addWidget(self.pick_fertility)
        form.addRow(tr("FertilityGuids"), fertility_row)
        form.addRow(self.randomize_fertilities)
        form.addRow(self.details)
        apply = QPushButton(tr("Apply to the selection"))
        apply.clicked.connect(self._element_values)
        form.addRow(apply)
        lay.addWidget(sel)
        box = QGroupBox(tr("Map"))
        mform = QFormLayout(box)
        size_row = QHBoxLayout()
        size_row.addWidget(self.map_w)
        size_row.addWidget(self.map_h)
        mform.addRow(tr("size (x, z)"), size_row)
        for title, boxes in ((tr("playable area"), self.area), (tr("initial playable area"), self.initial)):
            row = QHBoxLayout()
            for b in boxes:
                row.addWidget(b)
            mform.addRow(title, row)
        apply_map = QPushButton(tr("Apply to the map"))
        apply_map.clicked.connect(self._map_values)
        mform.addRow(apply_map)
        lay.addWidget(box)
        lay.addWidget(QLabel(tr("Checks (double-click selects)")))
        lay.addWidget(self.findings, 1)

    def show_element(self, e) -> None:
        """Fill the fields from the picked element (None: none picked) and enable those its kind and game have."""
        widgets = (
            self.x,
            self.z,
            self.size,
            self.type,
            self.rotation90,
            self.map_file_path,
            self.island_label,
            self.fertility_guids,
        )
        for w in (
            *widgets,
            self.randomize_fertilities,
            self.clime,
            self.details,
            self.pick_fertility,
            *self.bits.values(),
        ):
            w.setEnabled(e is not None)
        if e is None:
            self.kind.setText(tr("nothing selected"))
            return
        self.kind.setText(tr(KIND_NAMES[e.kind]))
        self.x.setValue(e.x)
        self.z.setValue(e.z)
        self.size.setCurrentIndex(min(e.size, self.size.count() - 1))
        self.clime.setValue(e.clime)
        for name, box in self.bits.items():
            box.setValue(getattr(e, name))
            box.setEnabled(e.kind == KIND_SLOT and self.random and self.game == "anno1701")
        self.type.setCurrentIndex(max(self.type.findData(e.type), 0))
        self.rotation90.setCurrentIndex(e.rotation90 % 4)
        self.map_file_path.setText(e.map_file_path)
        self.island_label.setText(e.island_label)
        self.fertility_guids.setText(", ".join(str(g) for g in e.fertility_guids))
        self.randomize_fertilities.setChecked(e.randomize_fertilities)
        self.size.setEnabled(e.kind == KIND_SLOT)
        self.clime.setEnabled(e.kind == KIND_SLOT and self.random)
        self.type.setEnabled(e.kind != KIND_START)
        fixed = (
            self.rotation90,
            self.map_file_path,
            self.island_label,
            self.fertility_guids,
            self.randomize_fertilities,
            self.details,
        )
        for w in (*fixed, self.pick_fertility):
            w.setEnabled(e.kind == KIND_FIXED)

    def _pick_fertility(self) -> None:
        guid = pick_guid(self, tr("A fertility of the island"), self.fertility_names)
        if guid is not None:
            self.fertility_guids.setText(with_guid(self.fertility_guids.text(), guid))

    def show_map(self, doc) -> None:
        if doc is None:
            return
        self.random = not doc.is_template and doc.record.random
        self.game, self.fertility_names = doc.game, doc.maps.fertilities
        names = doc.size_names()
        if [self.size.itemText(i) for i in range(self.size.count())] != names:
            self.size.clear()
            self.size.addItems(names)
        self.map_w.setValue(doc.size[0])
        self.map_h.setValue(doc.size[1])
        for box, v in zip(self.area, doc.playable, strict=True):
            box.setValue(v)
        initial = doc.initial
        for box, v in zip(self.initial, initial or (0, 0, 0, 0), strict=True):
            box.setValue(v)
            box.setEnabled(initial is not None)

    def _element_values(self) -> None:
        try:
            fert = tuple(int(g) for g in self.fertility_guids.text().replace(";", ",").split(",") if g.strip())
        except ValueError:
            self.kind.setText(tr("the fertilities must be GUIDs (numbers)"))
            return
        self._apply_element(
            {
                "x": self.x.value(),
                "z": self.z.value(),
                "size": self.size.currentIndex(),
                "clime": self.clime.value(),
                "type": self.type.currentData(),
                "rotation90": self.rotation90.currentIndex(),
                "map_file_path": self.map_file_path.text().strip().replace("\\", "/").lower(),
                "island_label": self.island_label.text().strip(),
                "fertility_guids": fert,
                "randomize_fertilities": self.randomize_fertilities.isChecked(),
                **{name: box.value() for name, box in self.bits.items()},
            }
        )

    def _map_values(self) -> None:
        self._apply_map(
            {
                "size": (self.map_w.value(), self.map_h.value()),
                "playable": tuple(b.value() for b in self.area),
                "initial": tuple(b.value() for b in self.initial) if self.initial[0].isEnabled() else None,
            }
        )
