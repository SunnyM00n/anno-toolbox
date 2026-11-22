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
from .previewcatalogue import SOption
from .previewwidgets import CIconCombo, CIconGrid, CSection, icon_at

NUMBER = re.compile(r"(\D)0*(\d+)$")  # "Shape01" as "Shape 1"
BANNER = (
    ("LogoColor", N_("ui.preview_banner_logo_colour")),
    ("BannerShape", N_("ui.preview_banner_shape")),
    ("BannerPattern", N_("ui.preview_banner_pattern")),
    ("BannerBackgroundPattern", N_("ui.preview_banner_background")),
    ("BannerStripes", N_("ui.preview_banner_stripes")),
)
LOOK = (  # the player's look: Defaults field, label, campaign's value, what the catalogue has
    ("participant_portrait", N_("ui.preview_participant_portrait"), pd.PARTICIPANT.portrait, "portraits"),
    ("participant_emblem", N_("ui.preview_participant_emblem"), pd.PARTICIPANT.emblem, "emblems"),
    ("participant_color", N_("ui.preview_participant_colour"), pd.PARTICIPANT.colour, "colours"),
)


def _held(options: list[SOption], guid: int) -> list[SOption]:
    """``options``, or the one value the campaign holds when the game's data gave none."""
    return options or [SOption(guid, str(guid))]


class CParticipantSection(CSection):
    def build(self) -> None:
        form = QFormLayout(self)
        self.m_name = QLineEdit(placeholderText=pd.PARTICIPANT.name)
        self.m_name.editingFinished.connect(self._named)
        self.row(
            form,
            tr("ui.preview_participant_name"),
            self.m_name,
            ("participant_name",),
            tr("ui.preview_participant_name_tip"),
        )
        self.m_grids: dict[str, tuple[CIconGrid, int]] = {}
        for field, label, campaign, source in LOOK:
            grid = CIconGrid(rows=3 if source == "emblems" else 2, backdrop="#3c3c3c" if source == "emblems" else "")
            grid.fill(getattr(self.cat, source), self.m_panel.m_icons)
            grid.picked.connect(
                lambda guid, field_name=field, campaign_value=campaign: self.put(
                    field_name, None if guid == campaign_value else guid
                )
            )
            self.m_grids[field] = (grid, campaign)
            self.row(form, tr(label), grid, (field,), tr("ui.preview_participant_pick_tip"))
        self.m_parts: dict[str, CIconCombo] = {}
        for part, label in BANNER:  # the game has no picture of a banner part in the install: its number names it
            combo = CIconCombo()
            options = self.cat.colours if part == "LogoColor" else self.cat.banner.get(part, [])
            named = [replace(option, name=NUMBER.sub(r"\1 \2", option.name)) if part != "LogoColor" else option
                     for option in _held(options, pd.CAMPAIGN_OPTIONS[f"banner.{part}"])]  # fmt: skip
            combo.fill(named, self.m_panel.m_icons)
            combo.picked.connect(lambda guid, part_name=part: self.put_option(f"banner.{part_name}", guid))
            self.m_parts[f"banner.{part}"] = combo
            self.row(form, tr(label), combo, (f"options.banner.{part}",), tr("ui.preview_banner_tip"))
        self.m_combos: dict[str, CIconCombo] = {}
        for part, label in (("prefix", N_("ui.preview_title_prefix")), ("suffix", N_("ui.preview_title_suffix"))):
            combo = CIconCombo()
            combo.fill([SOption(0, tr("ui.preview_title_none")), *self.cat.titles.get(part, [])], self.m_panel.m_icons)
            combo.picked.connect(lambda guid, part_name=part: self.put_option(f"title.{part_name}", guid))
            self.m_combos[f"title.{part}"] = combo
            self.row(form, tr(label), combo, (f"options.title.{part}",), tr("ui.preview_title_tip"))

    def _named(self) -> None:
        text = self.m_name.text().strip()
        self.put("participant_name", None if not text or text == pd.PARTICIPANT.name else text)

    def refresh(self) -> None:
        defaults = self.d
        self.m_name.setText(
            defaults.participant().name
        )  # the one shown is the name in use: the campaign's Marcia unless changed
        for key, grid in self.m_parts.items():
            grid.select(defaults.option(key))
        for field, (grid, campaign) in self.m_grids.items():
            grid.select(getattr(defaults, field) or campaign)
        for key, combo in self.m_combos.items():
            combo.select(defaults.option(key, 0))

    def update_icons(self) -> None:
        for grid, _campaign in self.m_grids.values():
            grid.update_icons()
        for grid in self.m_parts.values():
            grid.update_icons()
        for combo in self.m_combos.values():
            combo.update_icons()


class CCharactersSection(CSection):
    ROLES = (("emperor", N_("ui.preview_role_emperor"), True), ("rival", N_("ui.preview_role_rivals"), False),
             ("pirate", N_("ui.preview_role_pirates"), False))  # fmt: skip

    def build(self) -> None:
        layout = QVBoxLayout(self)
        self.m_lists: dict[str, QListWidget] = {}
        for role, title, exclusive in self.ROLES:
            box = QGroupBox(tr(title))
            inner = QVBoxLayout(box)
            head = QHBoxLayout()
            hint = QLabel(tr(N_("ui.preview_role_one_tip") if exclusive else N_("ui.preview_role_many_tip")))
            hint.setWordWrap(True)
            head.addWidget(hint, 1)
            head.addWidget(self.m_panel.reset_button((f"characters:{role}",)))
            inner.addLayout(head)
            list_widget = QListWidget()
            list_widget.setIconSize(QSize(40, 40))
            list_widget.setFixedHeight(150)
            list_widget.setToolTip(tr("ui.preview_role_list_tip"))
            for person in self.cat.characters.get(role) or list(pd.CAMPAIGN[role]):
                item = QListWidgetItem(person.name)
                item.setData(Qt.ItemDataRole.UserRole, person.participant)
                item.setData(Qt.ItemDataRole.UserRole + 1, person.portrait_icon)
                item.setToolTip(f"{person.name} ({person.region})")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                list_widget.addItem(item)
            list_widget.itemChanged.connect(
                lambda item, role_key=role, is_exclusive=exclusive: self._toggled(role_key, is_exclusive, item)
            )
            self.m_lists[role] = list_widget
            inner.addWidget(list_widget)
            layout.addWidget(box)
        layout.addStretch(1)

    def _toggled(self, role: str, exclusive: bool, item: QListWidgetItem) -> None:
        if self.m_panel.m_busy:
            return
        list_widget = self.m_lists[role]
        self.m_panel.m_busy += 1
        try:
            if exclusive and item.checkState() == Qt.CheckState.Checked:
                for i in range(list_widget.count()):
                    if list_widget.item(i) is not item:
                        list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
        finally:
            self.m_panel.m_busy -= 1
        chosen = [list_widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(list_widget.count())
                  if list_widget.item(i).checkState() == Qt.CheckState.Checked]  # fmt: skip
        cast = {role_key: list(guids) for role_key, guids in self.d.characters.items()}
        if chosen and set(chosen) != {person.participant for person in pd.CAMPAIGN[role]}:
            cast[role] = chosen
        else:
            cast.pop(role, None)
        self.put("characters", cast or None)

    def refresh(self) -> None:
        for role, list_widget in self.m_lists.items():
            chosen = self.d.characters.get(role) or [person.participant for person in pd.CAMPAIGN[role]]
            for i in range(list_widget.count()):
                guid = list_widget.item(i).data(Qt.ItemDataRole.UserRole)
                list_widget.item(i).setCheckState(Qt.CheckState.Checked if guid in chosen else Qt.CheckState.Unchecked)
        self.update_icons()

    def update_icons(self) -> None:
        icons = self.m_panel.m_icons
        for list_widget in self.m_lists.values():
            for i in range(list_widget.count()):
                path = list_widget.item(i).data(Qt.ItemDataRole.UserRole + 1)
                list_widget.item(i).setIcon(icon_at(icons, path))


class CPlatformSection(CSection):
    def build(self) -> None:
        layout = QVBoxLayout(self)
        self.m_inputs, self.m_sessions = QButtonGroup(self), QButtonGroup(self)
        for title, group, names, field, words in (
            (N_("ui.preview_input"), self.m_inputs, pd.INPUTS, "input",
             (N_("ui.preview_input_mouse"), N_("ui.preview_input_xbox"), N_("ui.preview_input_playstation"))),
            (N_("ui.preview_session"), self.m_sessions, ("single", "multi"), "session",
             (N_("ui.preview_session_single"), N_("ui.preview_session_multi"))),
        ):  # fmt: skip
            box = QGroupBox(tr(title))
            inner = QHBoxLayout(box)
            for name, word in zip(names, words, strict=True):
                radio = QRadioButton(tr(word))
                radio.setToolTip(tr(N_("ui.preview_input_tip") if field == "input" else N_("ui.preview_session_tip")))
                radio.toggled.connect(lambda checked, field_name=field, choice=name: checked and self.put(field_name, None if choice == getattr(
                    pd.CAMPAIGN_DEFAULTS, field_name) else choice))  # fmt: skip
                group.addButton(radio)
                group.setId(radio, names.index(name))
                inner.addWidget(radio)
                self.m_keyed.append(radio)
            inner.addStretch(1)
            inner.addWidget(self.m_panel.reset_button((field,)))
            layout.addWidget(box)
        box = QGroupBox(tr("ui.preview_dlcs"))
        inner = QVBoxLayout(box)
        head = QHBoxLayout()
        note = QLabel(tr("ui.preview_dlcs_tip"))
        note.setWordWrap(True)
        head.addWidget(note, 1)
        head.addWidget(self.m_panel.reset_button(("dlcs",)))
        inner.addLayout(head)
        self.m_dlcs = QListWidget()
        self.m_dlcs.setToolTip(tr("ui.preview_dlcs_list_tip"))
        for option in self.cat.dlcs:
            item = QListWidgetItem(option.name)
            item.setData(Qt.ItemDataRole.UserRole, option.guid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            self.m_dlcs.addItem(item)
        self.m_dlcs.itemChanged.connect(self._dlc_toggled)
        inner.addWidget(self.m_dlcs)
        layout.addWidget(box, 1)

    def _dlc_toggled(self, _item) -> None:
        if self.m_panel.m_busy:
            return
        owned = [self.m_dlcs.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.m_dlcs.count())
                 if self.m_dlcs.item(i).checkState() == Qt.CheckState.Checked]  # fmt: skip
        self.put("dlcs", None if len(owned) == self.m_dlcs.count() else owned)

    def refresh(self) -> None:
        defaults = self.d
        for group, names, value in (
            (self.m_inputs, pd.INPUTS, defaults.input),
            (self.m_sessions, ("single", "multi"), defaults.session),
        ):
            button = group.button(names.index(value)) if value in names else None
            if button is not None:
                button.setChecked(True)
        for i in range(self.m_dlcs.count()):
            guid = self.m_dlcs.item(i).data(Qt.ItemDataRole.UserRole)
            is_owned = defaults.dlcs is None or guid in defaults.dlcs
            self.m_dlcs.item(i).setCheckState(Qt.CheckState.Checked if is_owned else Qt.CheckState.Unchecked)


class CValuesSection(CSection):
    def build(self) -> None:
        layout = QVBoxLayout(self)
        box = QGroupBox(tr("ui.preview_values"))
        form = QFormLayout(box)
        self.m_season = QLineEdit(placeholderText=tr("ui.preview_season_default"))
        self.m_season.editingFinished.connect(lambda: self.put("season", self.m_season.text().strip() or None))
        self.row(form, tr("ui.preview_season"), self.m_season, ("season",), tr("ui.preview_season_tip"))
        self.m_island = QComboBox(editable=True)
        self.m_island.addItems(["large", "medium", "small"])  # i18n: ok (the preview's island words, data)
        self.m_island.lineEdit().editingFinished.connect(
            lambda: self.put("island", self.m_island.currentText().strip() or None)
        )
        self.m_island.activated.connect(lambda _i: self.put("island", self.m_island.currentText().strip() or None))
        self.row(form, tr("ui.preview_island"), self.m_island, ("island",), tr("ui.preview_island_tip"))
        self.m_numbers: dict[str, QSpinBox] = {}
        for field, label, tip, top in (("residence", N_("ui.preview_residence"), N_("ui.preview_object_tip"), 2**31 - 1),
                                       ("production", N_("ui.preview_production"), N_("ui.preview_object_tip"), 2**31 - 1),
                                       ("money", N_("ui.preview_money"), N_("ui.preview_money_tip"), 2**31 - 1),
                                       ("goods", N_("ui.preview_goods"), N_("ui.preview_goods_tip"), 1_000_000)):  # fmt: skip
            spin = QSpinBox()
            spin.setRange(0, top)
            spin.setGroupSeparatorShown(True)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(lambda number, field_name=field: self.put(field_name, number))
            self.m_numbers[field] = spin
            self.row(form, tr(label), spin, (field,), tr(tip))
        layout.addWidget(box)
        box = QGroupBox(tr("ui.preview_language_group"))
        form = QFormLayout(box)
        self.m_language = QComboBox()
        self.m_language.addItem(tr("ui.preview_language_editor"), "")
        for name in self._languages():
            self.m_language.addItem(gamelanguages.title(name), name)
        self.m_language.activated.connect(lambda i: self.put("language", self.m_language.itemData(i) or None))
        self.row(form, tr("ui.preview_language"), self.m_language, ("language",), tr("ui.preview_language_tip"))
        layout.addWidget(box)
        layout.addStretch(1)

    @staticmethod
    def _languages() -> tuple[str, ...]:
        """The twelve text languages 117 ships; a language the install lacks shows English (``gamelanguages.chosen``)."""
        return gamelanguages.KNOWN

    def refresh(self) -> None:
        defaults = self.d
        self.m_season.setText(defaults.season)
        self.m_island.setCurrentText(defaults.island)
        for field, spin in self.m_numbers.items():
            spin.setValue(int(getattr(defaults, field)))
        self.m_language.setCurrentIndex(max(0, self.m_language.findData(defaults.language)))
