"""The session tab of the preview settings panel: the campaign's difficulty preset, the province the session starts
in, the seed, and every ``Setup...`` option of the game's difficulty setup with its values (the catalogue's
``setup``), each kept in the store as ``options.setup.<name>``.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QSpinBox, QVBoxLayout

from ..infra.i18n import tr
from . import previewdefaults as pd
from .previewcatalogue import SOption
from .previewwidgets import CIconCombo, CSection, humanise

PROVINCES = {pd.LATIUM: "Latium", pd.ALBION: "Albion", pd.AEGYPTUS: "Aegyptus"}  # the names when the data has none
PRESET, SEED, MAP, EMPEROR = (
    "setup.DifficultyPreset",
    "setup.GameSeed",
    "setup.SetupMapTemplateType",
    "setup.SetupStartEmperor",
)
FIRST = (MAP, EMPEROR)  # the options with no data set of their own: the map type is a GUID, the emperor a participant


class CSetupSection(CSection):
    def build(self) -> None:
        layout = QVBoxLayout(self)
        top = QFormLayout()
        self.m_combos: dict[str, CIconCombo] = {}
        self.m_spins: dict[str, QSpinBox] = {}
        self.m_preset = self._combo(PRESET, self.cat.presets or [SOption(41206, "Campaign")])
        self.row(top, tr("ui.preview_preset"), self.m_preset, (f"options.{PRESET}",), tr("ui.preview_preset_tip"))
        self.m_region = CIconCombo()
        provinces = [
            SOption(option.guid, option.name or PROVINCES.get(option.guid, str(option.guid)))
            for option in self.cat.regions
        ]
        self.m_region.fill(provinces or [SOption(guid, province_name) for guid, province_name in PROVINCES.items()])
        self.m_region.picked.connect(lambda guid: self.put("region", None if guid == pd.LATIUM else guid))
        self.row(top, tr("ui.preview_region"), self.m_region, ("region",), tr("ui.preview_region_tip"))
        self.row(top, tr("ui.preview_seed"), self._spin(SEED), (f"options.{SEED}",), tr("ui.preview_seed_tip"))
        layout.addLayout(top)
        bar = QHBoxLayout()
        self.m_campaign = QPushButton(tr("ui.preview_campaign_setup"))
        self.m_campaign.setToolTip(tr("ui.preview_campaign_setup_tip"))
        self.m_campaign.clicked.connect(lambda _c=False: self.use_campaign())
        self.m_keyed.append(self.m_campaign)
        self.find = QLineEdit(placeholderText=tr("ui.preview_setup_find"))
        self.find.setToolTip(tr("ui.preview_setup_find_tip"))
        self.find.textChanged.connect(self._filter)
        bar.addWidget(self.m_campaign)
        bar.addWidget(self.find, 1)
        layout.addLayout(bar)
        self.m_form = QFormLayout()
        layout.addLayout(self.m_form)
        layout.addStretch(1)
        self.m_rows: list[tuple[int, str]] = []
        for name in self._names():
            self._option_row(name)

    def _names(self) -> list[str]:
        names = list(self.cat.setup)
        names += [name for name, _value in pd.SETUP if name not in names]
        return names + [key.removeprefix("setup.") for key in FIRST if key.removeprefix("setup.") not in names]

    def _combo(self, key: str, options: list[SOption]) -> CIconCombo:
        combo = CIconCombo()
        combo.fill(options)
        combo.picked.connect(lambda guid, k=key: self.put_option(k, guid))
        self.m_combos[key] = combo
        return combo

    def _spin(self, key: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 2**31 - 1)
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(lambda value, option=key: self.put_option(option, value))
        self.m_spins[key] = spin
        return spin

    def _option_row(self, name: str) -> None:
        key = f"setup.{name}"
        values = self.cat.setup.get(name)
        if not values and key == EMPEROR:
            values = [
                SOption(character.participant, character.name) for character in self.cat.characters.get("emperor", [])
            ]
        control = self._combo(key, values) if values else self._spin(key)
        text = humanise(name)
        self.row(self.m_form, text, control, (f"options.{key}",), tr("ui.preview_option_tip", name=name))
        self.m_rows.append((self.m_form.rowCount() - 1, text.lower()))

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        for index, label in self.m_rows:
            self.m_form.setRowVisible(index, all(word in label for word in words))

    def use_campaign(self) -> None:
        """Every setup option back to the campaign's, the province too."""
        mine = {f"options.{k}": None for k in self.d.options if k.startswith("setup.")}
        self.m_panel.m_store.set_many({**mine, "region": None})

    def refresh(self) -> None:
        for key, combo in self.m_combos.items():
            combo.select(self.d.option(key))
        for key, spin in self.m_spins.items():
            spin.setValue(int(self.d.option(key, 0) or 0))
        self.m_region.select(self.d.region)
