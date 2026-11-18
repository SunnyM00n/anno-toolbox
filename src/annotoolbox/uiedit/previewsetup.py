"""The session tab of the preview settings panel: the campaign's difficulty preset, the province the session starts
in, the seed, and every ``Setup...`` option of the game's difficulty setup with its values (the catalogue's
``setup``), each kept in the store as ``options.setup.<name>``.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QSpinBox, QVBoxLayout

from ..infra.i18n import tr
from . import previewdefaults as pd
from .previewcatalogue import Option
from .previewwidgets import IconCombo, Section, humanise

PROVINCES = {pd.LATIUM: "Latium", pd.ALBION: "Albion", pd.AEGYPTUS: "Aegyptus"}  # the names when the data has none
PRESET, SEED, MAP, EMPEROR = (
    "setup.DifficultyPreset",
    "setup.GameSeed",
    "setup.SetupMapTemplateType",
    "setup.SetupStartEmperor",
)
FIRST = (MAP, EMPEROR)  # the options with no data set of their own: the map type is a GUID, the emperor a participant


class SetupSection(Section):
    def build(self) -> None:
        lay = QVBoxLayout(self)
        top = QFormLayout()
        self.combos: dict[str, IconCombo] = {}
        self.spins: dict[str, QSpinBox] = {}
        self.preset = self._combo(PRESET, self.cat.presets or [Option(41206, "Campaign")])
        self.row(top, tr("ui.preview_preset"), self.preset, (f"options.{PRESET}",), tr("ui.preview_preset_tip"))
        self.region = IconCombo()
        provinces = [Option(o.guid, o.name or PROVINCES.get(o.guid, str(o.guid))) for o in self.cat.regions]
        self.region.fill(provinces or [Option(g, n) for g, n in PROVINCES.items()])
        self.region.picked.connect(lambda guid: self.put("region", None if guid == pd.LATIUM else guid))
        self.row(top, tr("ui.preview_region"), self.region, ("region",), tr("ui.preview_region_tip"))
        self.row(top, tr("ui.preview_seed"), self._spin(SEED), (f"options.{SEED}",), tr("ui.preview_seed_tip"))
        lay.addLayout(top)
        bar = QHBoxLayout()
        self.campaign = QPushButton(tr("ui.preview_campaign_setup"))
        self.campaign.setToolTip(tr("ui.preview_campaign_setup_tip"))
        self.campaign.clicked.connect(lambda _c=False: self.use_campaign())
        self.keyed.append(self.campaign)
        self.find = QLineEdit(placeholderText=tr("ui.preview_setup_find"))
        self.find.setToolTip(tr("ui.preview_setup_find_tip"))
        self.find.textChanged.connect(self._filter)
        bar.addWidget(self.campaign)
        bar.addWidget(self.find, 1)
        lay.addLayout(bar)
        self.form = QFormLayout()
        lay.addLayout(self.form)
        lay.addStretch(1)
        self.rows: list[tuple[int, str]] = []
        for name in self._names():
            self._option_row(name)

    def _names(self) -> list[str]:
        names = list(self.cat.setup)
        names += [n for n, _v in pd.SETUP if n not in names]
        return names + [m.removeprefix("setup.") for m in FIRST if m.removeprefix("setup.") not in names]

    def _combo(self, key: str, options: list[Option]) -> IconCombo:
        combo = IconCombo()
        combo.fill(options)
        combo.picked.connect(lambda guid, k=key: self.put_option(k, guid))
        self.combos[key] = combo
        return combo

    def _spin(self, key: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 2**31 - 1)
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(lambda v, k=key: self.put_option(k, v))
        self.spins[key] = spin
        return spin

    def _option_row(self, name: str) -> None:
        key = f"setup.{name}"
        values = self.cat.setup.get(name)
        if not values and key == EMPEROR:
            values = [Option(c.participant, c.name) for c in self.cat.characters.get("emperor", [])]
        control = self._combo(key, values) if values else self._spin(key)
        text = humanise(name)
        self.row(self.form, text, control, (f"options.{key}",), tr("ui.preview_option_tip", name=name))
        self.rows.append((self.form.rowCount() - 1, text.lower()))

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        for index, label in self.rows:
            self.form.setRowVisible(index, all(w in label for w in words))

    def use_campaign(self) -> None:
        """Every setup option back to the campaign's, the province too."""
        mine = {f"options.{k}": None for k in self.d.options if k.startswith("setup.")}
        self.panel.store.set_many({**mine, "region": None})

    def refresh(self) -> None:
        for key, combo in self.combos.items():
            combo.select(self.d.option(key))
        for key, spin in self.spins.items():
            spin.setValue(int(self.d.option(key, 0) or 0))
        self.region.select(self.d.region)
