"""The tabs of the preview settings panel that hold a person's own look and the platform: the player (name, portrait,
emblem, colour, banner, title), the characters per role, the platform (input, session, DLCs) and the values picked as
defaults with the game's language. The session setup and the progression are ``previewsetup`` and
``previewprogress``; the panel that holds them all is ``previewpanel``.
"""

from __future__ import annotations

import re
from dataclasses import replace

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from ..infra.i18n import N_, tr
from . import gamelanguages
from . import previewdefaults as pd
from .previewcatalogue import Option
from .previewwidgets import IconCombo, IconGrid, Section, icon_at

NUMBER = re.compile(r"(\D)0*(\d+)$")  # "Shape01" as "Shape 1"
BANNER = (
    ("LogoColor", N_("ui.preview_banner_logo_colour")),
    ("BannerShape", N_("ui.preview_banner_shape")),
    ("BannerPattern", N_("ui.preview_banner_pattern")),
    ("BannerBackgroundPattern", N_("ui.preview_banner_background")),
    ("BannerStripes", N_("ui.preview_banner_stripes")),
)
LOOK = (  # the player's look: Defaults field, label, campaign's value, what the catalogue has
    ("player_portrait", N_("ui.preview_player_portrait"), pd.PLAYER.portrait, "portraits"),
    ("player_emblem", N_("ui.preview_player_emblem"), pd.PLAYER.emblem, "emblems"),
    ("player_colour", N_("ui.preview_player_colour"), pd.PLAYER.colour, "colours"),
)


def _held(options: list[Option], guid: int) -> list[Option]:
    """``options``, or the one value the campaign holds when the game's data gave none."""
    return options or [Option(guid, str(guid))]


class PlayerSection(Section):
    def build(self) -> None:
        form = QFormLayout(self)
        self.name = QLineEdit(placeholderText=pd.PLAYER.name)
        self.name.editingFinished.connect(self._named)
        self.row(form, tr("ui.preview_player_name"), self.name, ("player_name",), tr("ui.preview_player_name_tip"))
        self.grids: dict[str, tuple[IconGrid, int]] = {}
        for field, label, campaign, source in LOOK:
            grid = IconGrid(rows=3 if source == "emblems" else 2, backdrop="#3c3c3c" if source == "emblems" else "")
            grid.fill(getattr(self.cat, source), self.panel.icons)
            grid.picked.connect(lambda guid, f=field, c=campaign: self.put(f, None if guid == c else guid))
            self.grids[field] = (grid, campaign)
            self.row(form, tr(label), grid, (field,), tr("ui.preview_player_pick_tip"))
        self.parts: dict[str, IconCombo] = {}
        for part, label in BANNER:  # the game has no picture of a banner part in the install: its number names it
            combo = IconCombo()
            options = self.cat.colours if part == "LogoColor" else self.cat.banner.get(part, [])
            named = [replace(o, name=NUMBER.sub(r"\1 \2", o.name)) if part != "LogoColor" else o
                     for o in _held(options, pd.CAMPAIGN_OPTIONS[f"banner.{part}"])]  # fmt: skip
            combo.fill(named, self.panel.icons)
            combo.picked.connect(lambda guid, p=part: self.put_option(f"banner.{p}", guid))
            self.parts[f"banner.{part}"] = combo
            self.row(form, tr(label), combo, (f"options.banner.{part}",), tr("ui.preview_banner_tip"))
        self.combos: dict[str, IconCombo] = {}
        for part, label in (("prefix", N_("ui.preview_title_prefix")), ("suffix", N_("ui.preview_title_suffix"))):
            combo = IconCombo()
            combo.fill([Option(0, tr("ui.preview_title_none")), *self.cat.titles.get(part, [])], self.panel.icons)
            combo.picked.connect(lambda guid, p=part: self.put_option(f"title.{p}", guid))
            self.combos[f"title.{part}"] = combo
            self.row(form, tr(label), combo, (f"options.title.{part}",), tr("ui.preview_title_tip"))

    def _named(self) -> None:
        text = self.name.text().strip()
        self.put("player_name", None if not text or text == pd.PLAYER.name else text)

    def refresh(self) -> None:
        d = self.d
        self.name.setText(d.player().name)  # the one shown is the name in use: the campaign's Marcia unless changed
        for key, grid in self.parts.items():
            grid.select(d.option(key))
        for field, (grid, campaign) in self.grids.items():
            grid.select(getattr(d, field) or campaign)
        for key, combo in self.combos.items():
            combo.select(d.option(key, 0))

    def update_icons(self) -> None:
        for grid, _campaign in self.grids.values():
            grid.update_icons()
        for grid in self.parts.values():
            grid.update_icons()
        for combo in self.combos.values():
            combo.update_icons()


class CharactersSection(Section):
    ROLES = (("emperor", N_("ui.preview_role_emperor"), True), ("rival", N_("ui.preview_role_rivals"), False),
             ("pirate", N_("ui.preview_role_pirates"), False))  # fmt: skip

    def build(self) -> None:
        lay = QVBoxLayout(self)
        self.lists: dict[str, QListWidget] = {}
        for role, title, exclusive in self.ROLES:
            box = QGroupBox(tr(title))
            inner = QVBoxLayout(box)
            head = QHBoxLayout()
            hint = QLabel(tr(N_("ui.preview_role_one_tip") if exclusive else N_("ui.preview_role_many_tip")))
            hint.setWordWrap(True)
            head.addWidget(hint, 1)
            head.addWidget(self.panel.reset_button((f"characters:{role}",)))
            inner.addLayout(head)
            lst = QListWidget()
            lst.setIconSize(QSize(40, 40))
            lst.setFixedHeight(150)
            lst.setToolTip(tr("ui.preview_role_list_tip"))
            for person in self.cat.characters.get(role) or list(pd.CAMPAIGN[role]):
                item = QListWidgetItem(person.name)
                item.setData(Qt.ItemDataRole.UserRole, person.participant)
                item.setData(Qt.ItemDataRole.UserRole + 1, person.portrait_icon)
                item.setToolTip(f"{person.name} ({person.region})")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                lst.addItem(item)
            lst.itemChanged.connect(lambda item, r=role, e=exclusive: self._toggled(r, e, item))
            self.lists[role] = lst
            inner.addWidget(lst)
            lay.addWidget(box)
        lay.addStretch(1)

    def _toggled(self, role: str, exclusive: bool, item: QListWidgetItem) -> None:
        if self.panel.busy:
            return
        lst = self.lists[role]
        self.panel.busy += 1
        try:
            if exclusive and item.checkState() == Qt.CheckState.Checked:
                for i in range(lst.count()):
                    if lst.item(i) is not item:
                        lst.item(i).setCheckState(Qt.CheckState.Unchecked)
        finally:
            self.panel.busy -= 1
        chosen = [lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lst.count())
                  if lst.item(i).checkState() == Qt.CheckState.Checked]  # fmt: skip
        cast = {r: list(g) for r, g in self.d.characters.items()}
        if chosen and set(chosen) != {c.participant for c in pd.CAMPAIGN[role]}:
            cast[role] = chosen
        else:
            cast.pop(role, None)
        self.put("characters", cast or None)

    def refresh(self) -> None:
        for role, lst in self.lists.items():
            chosen = self.d.characters.get(role) or [c.participant for c in pd.CAMPAIGN[role]]
            for i in range(lst.count()):
                guid = lst.item(i).data(Qt.ItemDataRole.UserRole)
                lst.item(i).setCheckState(Qt.CheckState.Checked if guid in chosen else Qt.CheckState.Unchecked)
        self.update_icons()

    def update_icons(self) -> None:
        icons = self.panel.icons
        for lst in self.lists.values():
            for i in range(lst.count()):
                path = lst.item(i).data(Qt.ItemDataRole.UserRole + 1)
                lst.item(i).setIcon(icon_at(icons, path))


class PlatformSection(Section):
    def build(self) -> None:
        lay = QVBoxLayout(self)
        self.inputs, self.sessions = QButtonGroup(self), QButtonGroup(self)
        for title, group, names, field, words in (
            (N_("ui.preview_input"), self.inputs, pd.INPUTS, "input",
             (N_("ui.preview_input_mouse"), N_("ui.preview_input_xbox"), N_("ui.preview_input_playstation"))),
            (N_("ui.preview_session"), self.sessions, ("single", "multi"), "session",
             (N_("ui.preview_session_single"), N_("ui.preview_session_multi"))),
        ):  # fmt: skip
            box = QGroupBox(tr(title))
            inner = QHBoxLayout(box)
            for name, word in zip(names, words, strict=True):
                radio = QRadioButton(tr(word))
                radio.setToolTip(tr(N_("ui.preview_input_tip") if field == "input" else N_("ui.preview_session_tip")))
                radio.toggled.connect(lambda on, f=field, n=name: on and self.put(f, None if n == getattr(
                    pd.CAMPAIGN_DEFAULTS, f) else n))  # fmt: skip
                group.addButton(radio)
                group.setId(radio, names.index(name))
                inner.addWidget(radio)
                self.keyed.append(radio)
            inner.addStretch(1)
            inner.addWidget(self.panel.reset_button((field,)))
            lay.addWidget(box)
        box = QGroupBox(tr("ui.preview_dlcs"))
        inner = QVBoxLayout(box)
        head = QHBoxLayout()
        note = QLabel(tr("ui.preview_dlcs_tip"))
        note.setWordWrap(True)
        head.addWidget(note, 1)
        head.addWidget(self.panel.reset_button(("dlcs",)))
        inner.addLayout(head)
        self.dlcs = QListWidget()
        self.dlcs.setToolTip(tr("ui.preview_dlcs_list_tip"))
        for option in self.cat.dlcs:
            item = QListWidgetItem(option.name)
            item.setData(Qt.ItemDataRole.UserRole, option.guid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            self.dlcs.addItem(item)
        self.dlcs.itemChanged.connect(self._dlc_toggled)
        inner.addWidget(self.dlcs)
        lay.addWidget(box, 1)

    def _dlc_toggled(self, _item) -> None:
        if self.panel.busy:
            return
        owned = [self.dlcs.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.dlcs.count())
                 if self.dlcs.item(i).checkState() == Qt.CheckState.Checked]  # fmt: skip
        self.put("dlcs", None if len(owned) == self.dlcs.count() else owned)

    def refresh(self) -> None:
        d = self.d
        for group, names, value in ((self.inputs, pd.INPUTS, d.input), (self.sessions, ("single", "multi"), d.session)):
            button = group.button(names.index(value)) if value in names else None
            if button is not None:
                button.setChecked(True)
        for i in range(self.dlcs.count()):
            guid = self.dlcs.item(i).data(Qt.ItemDataRole.UserRole)
            on = d.dlcs is None or guid in d.dlcs
            self.dlcs.item(i).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)


class ValuesSection(Section):
    def build(self) -> None:
        lay = QVBoxLayout(self)
        box = QGroupBox(tr("ui.preview_values"))
        form = QFormLayout(box)
        self.season = QLineEdit(placeholderText=tr("ui.preview_season_default"))
        self.season.editingFinished.connect(lambda: self.put("season", self.season.text().strip() or None))
        self.row(form, tr("ui.preview_season"), self.season, ("season",), tr("ui.preview_season_tip"))
        self.island = QComboBox(editable=True)
        self.island.addItems(["large", "medium", "small"])  # i18n: ok (the preview's island words, data)
        self.island.lineEdit().editingFinished.connect(
            lambda: self.put("island", self.island.currentText().strip() or None)
        )
        self.island.activated.connect(lambda _i: self.put("island", self.island.currentText().strip() or None))
        self.row(form, tr("ui.preview_island"), self.island, ("island",), tr("ui.preview_island_tip"))
        self.numbers: dict[str, QSpinBox] = {}
        for field, label, tip, top in (("residence", N_("ui.preview_residence"), N_("ui.preview_object_tip"), 2**31 - 1),
                                       ("production", N_("ui.preview_production"), N_("ui.preview_object_tip"), 2**31 - 1),
                                       ("money", N_("ui.preview_money"), N_("ui.preview_money_tip"), 2**31 - 1),
                                       ("goods", N_("ui.preview_goods"), N_("ui.preview_goods_tip"), 1_000_000)):  # fmt: skip
            spin = QSpinBox()
            spin.setRange(0, top)
            spin.setGroupSeparatorShown(True)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(lambda v, f=field: self.put(f, v))
            self.numbers[field] = spin
            self.row(form, tr(label), spin, (field,), tr(tip))
        lay.addWidget(box)
        box = QGroupBox(tr("ui.preview_language_group"))
        form = QFormLayout(box)
        self.language = QComboBox()
        self.language.addItem(tr("ui.preview_language_editor"), "")
        for name in self._languages():
            self.language.addItem(gamelanguages.title(name), name)
        self.language.activated.connect(lambda i: self.put("language", self.language.itemData(i) or None))
        self.row(form, tr("ui.preview_language"), self.language, ("language",), tr("ui.preview_language_tip"))
        lay.addWidget(box)
        lay.addStretch(1)

    @staticmethod
    def _languages() -> tuple[str, ...]:
        """The twelve text languages 117 ships; a language the install lacks shows English (``gamelanguages.chosen``)."""
        return gamelanguages.KNOWN

    def refresh(self) -> None:
        d = self.d
        self.season.setText(d.season)
        self.island.setCurrentText(d.island)
        for field, spin in self.numbers.items():
            spin.setValue(int(getattr(d, field)))
        self.language.setCurrentIndex(max(0, self.language.findData(d.language)))
