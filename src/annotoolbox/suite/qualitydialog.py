"""The rendering quality's menu entries and its Custom window, as 117's graphics options offer them: the game's own
settings (the player's ``engine.ini``), the quality presets, and Custom with a level per area (``render.quality``).
The island editor's Rendering menu and the viewer's View > Rendering menu share them.

``add_actions(menu, choose)`` makes the entries, ``check_actions`` marks the one in use, ``ask_custom(parent, game,
current)`` shows the window and returns what ``Settings.quality`` keeps. docs/rendering/quality.md, "The plan".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QComboBox, QDialog, QFormLayout, QLabel, QVBoxLayout

from ..infra.i18n import N_, number, tr
from ..render import quality
from . import widgets

PLAYER = "player"  # the entry that follows the player's own settings of the game
RENDER_SCALES = (1.0, 0.85, 0.75, 0.6, 0.5)  # [ours]: the game has no render scale besides its upscalers


def add_actions(menu, choose: Callable[[str], None]) -> dict[str, QAction]:
    """The entries of a Rendering quality menu: the game's own settings, the presets and Custom (which opens the
    window); ``choose`` gets the entry's key (``PLAYER``, a key of ``quality.PRESETS`` or ``quality.CUSTOM``)."""
    group = QActionGroup(menu, exclusive=True)
    actions: dict[str, QAction] = {}
    entries = (
        (PLAYER, N_("common.quality_as_in_game")),
        *quality.PRESETS.items(),
        (quality.CUSTOM, N_("common.quality_custom_window")),
    )
    for key, label in entries:
        action = menu.addAction(tr(label))
        action.setCheckable(True)
        action.triggered.connect(lambda _on=False, key=key: choose(key))
        group.addAction(action)
        actions[key] = action
        if key == PLAYER:
            action.setToolTip(tr("common.tip.quality_as_in_game"))
            menu.addSeparator()
    return actions


def check_actions(actions: dict[str, QAction], current: quality.SQuality | None) -> None:
    """Mark the entry ``current`` comes from (none for a game without a quality); the player's entry says what the
    game is set to."""
    for key, action in actions.items():
        action.setEnabled(current is not None)
        if key == PLAYER:
            action.setChecked(current is not None and current.is_player_setting)
        else:
            action.setChecked(current is not None and not current.is_player_setting and current.preset == key)
    if PLAYER in actions:
        text = tr("common.quality_as_in_game")
        if current is not None and current.is_player_setting:
            text = tr("common.quality_as_in_game_named", name=preset_text(current.preset))
        actions[PLAYER].setText(text)


def preset_text(name: str) -> str:
    """A preset's name as the game's options show it (Custom included)."""
    return tr(quality.PRESETS.get(name, N_("common.quality_custom")))


class CQualityDialog(QDialog):
    """The Custom window: the quality preset and a level per area as 117's graphics options list them, and the
    render scale of ours; ``stored()`` is the record for ``Settings.quality``."""

    def __init__(self, game: str, current: quality.SQuality, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("common.quality_graphics_settings"))
        self.m_game = game
        self.m_shadows = current.shadows  # a settings record's switch, kept as it is
        self.m_preset = QComboBox()
        for key in (*quality.PRESETS, quality.CUSTOM):
            self.m_preset.addItem(preset_text(key), key)
        self.m_preset.setToolTip(tr("common.tip.quality_preset"))
        form = QFormLayout()
        form.addRow(tr("common.quality_graphics_quality"), self.m_preset)
        self.m_areas: dict[str, QComboBox] = {}
        for area, top in quality.AREAS.items():
            combo = QComboBox()
            for level in range(top + 1):
                combo.addItem(tr(quality.LEVEL_LABELS[level]), level)
            combo.setCurrentIndex(current.area(area))
            if area not in quality.DRAWN:
                combo.setToolTip(tr("common.tip.quality_not_drawn"))
            form.addRow(tr(quality.AREA_LABELS[area]), combo)
            self.m_areas[area] = combo
        self.m_scale = QComboBox()
        # a scale off the list (a Toolbox 0.3 Custom record made from Medium kept 0.8) is offered too: OK keeps it
        for scale in sorted({*RENDER_SCALES, current.render_scale}, reverse=True):
            self.m_scale.addItem(tr("common.quality_percent", value=number(scale * 100, 0)), scale)
        self.m_scale.setCurrentIndex(max(0, self.m_scale.findData(current.render_scale)))
        self.m_scale.setToolTip(tr("common.tip.quality_render_scale"))
        form.addRow(tr("common.quality_render_scale"), self.m_scale)
        self._select_preset(current.preset if current.preset in quality.PRESETS else quality.CUSTOM)
        # connected after the start values, so they do not count as the person's choice
        self.m_preset.currentIndexChanged.connect(self._preset_chosen)
        for combo in self.m_areas.values():
            combo.currentIndexChanged.connect(self._area_chosen)
        hint = QLabel(tr("common.quality_hint_game_settings"))
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(widgets.ok_cancel(self))

    def _select_preset(self, key: str) -> None:
        self.m_preset.blockSignals(True)
        self.m_preset.setCurrentIndex(max(0, self.m_preset.findData(key)))
        self.m_preset.blockSignals(False)

    def _preset_chosen(self) -> None:
        """A preset sets every area to its levels, as the game's list does."""
        key = self.m_preset.currentData()
        if key not in quality.PRESETS:
            return
        for area, level in quality.preset_levels(key).items():
            combo = self.m_areas[area]
            combo.blockSignals(True)
            combo.setCurrentIndex(level)
            combo.blockSignals(False)

    def _area_chosen(self) -> None:
        """An area set by hand makes the quality Custom, unless the levels are a preset's again."""
        levels = self.levels()
        match = [key for key in quality.PRESETS if quality.preset_levels(key) == levels]
        self._select_preset(match[0] if match else quality.CUSTOM)

    def levels(self) -> dict[str, int]:
        return {area: int(combo.currentData()) for area, combo in self.m_areas.items()}

    def stored(self):
        """What ``Settings.quality`` keeps for the game: a preset's name, or a Custom record."""
        chosen = quality.derive(self.m_game, self.m_preset.currentData(), self.levels())
        scale = float(self.m_scale.currentData())
        return quality.store(replace(chosen, render_scale=scale, shadows=self.m_shadows))


def choose(parent, game: str, key: str, current: quality.SQuality | None) -> tuple[bool, object]:
    """What the menu's entry ``key`` keeps for ``game`` in ``Settings.quality``: (whether it was set, the value:
    None for the player's own settings, a preset's name, or the Custom window's record; cancelled: not set)."""
    if key == PLAYER:
        return True, None
    if key == quality.CUSTOM:
        got = ask_custom(parent, game, current if current is not None else quality.resolve(game, None))
        return got is not None, got
    return True, key


def replace_game(stored_by_game: dict, game: str, stored) -> dict:
    """``Settings.quality`` with ``stored`` for ``game`` (None: the game's entry taken out, the player's settings)."""
    out = {key: value for key, value in (stored_by_game or {}).items() if key != game}
    if stored is not None:
        out[game] = stored
    return out


def ask_custom(parent, game: str, current: quality.SQuality):
    """The Custom window for ``game`` from ``current``; what ``Settings.quality`` keeps, or None when cancelled."""
    dialog = CQualityDialog(game, current, parent)
    return dialog.stored() if dialog.exec() == QDialog.DialogCode.Accepted else None
