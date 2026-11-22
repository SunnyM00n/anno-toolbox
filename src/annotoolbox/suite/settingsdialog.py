"""The settings dialog of the Toolbox (Settings > Games and folders): the game folders, the workspace, the theme and the
language. ``CSettingsDialog(settings, parent, games)`` asks for the folders of ``games`` (None: all); ``values()`` is
what was set. ``choices``: further rows of (settings key, label id, {value: text}), one drop-down each (the island
editor's start values for a new island). The suite's menu bar and the island editor's own File menu open it.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..infra.i18n import N_, languages, tr
from ..othergames import installs
from .settings import THEMES, CSettings, found_text

OPTIONAL_INSTALL = N_("islands.game_install_optional")
REQUIRED_INSTALL = N_("islands.game_install")  # the optional one, for an application that needs the game
FOLDERS = (  # (settings key, label text; the {game} place takes the name of ``installs.GAMES[key]``)
    ("anno117", N_("islands.anno_117_install")),
    ("anno2070", N_("islands.anno_2070_install")),
    *((key, OPTIONAL_INSTALL) for key in installs.GAMES if key not in ("anno117", "anno2070")),
    ("mods", N_("islands.anno_117_mods_folder")),
    ("mods1800", N_("islands.anno_1800_mods_folder_build")),
    ("patch2205", N_("islands.anno_2205_folder_patch_build")),
    ("workspace", N_("islands.workspace")),
)
THEME_LABELS = {
    "system": N_("islands.system_follows_windows"),
    "light": N_("islands.light"),
    "dark": N_("islands.dark"),
}


def folders_for(games: tuple[str, ...] | None) -> tuple:
    """The folders the settings ask for: all, or (an application restricted to ``games``) theirs and the workspace."""
    if games is None:
        return FOLDERS
    return tuple(
        (key, REQUIRED_INSTALL if label == OPTIONAL_INSTALL else label)
        for key, label in FOLDERS
        if key in games or key in ("workspace", "mods1800")
    )


class CSettingsDialog(QDialog):
    """The game folders, the workspace and the theme, each folder with a picker."""

    def __init__(
        self, settings: CSettings, parent=None, games: tuple[str, ...] | None = None, choices: tuple = ()
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("islands.title.games_folders"))
        self.m_edits: dict[str, QLineEdit] = {}
        form = QFormLayout()
        for key, label in folders_for(games):
            edit = QLineEdit(getattr(settings, key))
            pick = QPushButton(tr("islands.browse"))
            pick.clicked.connect(lambda _=False, edit_field=edit: self._pick(edit_field))
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            row.addWidget(pick)
            edit.setToolTip(found_text(edit.text()))  # off Windows: the launcher's library or prefix it came from
            form.addRow(tr(label, game=installs.GAMES[key].name if key in installs.GAMES else ""), row)
            self.m_edits[key] = edit
            if text := found_text(edit.text()):
                form.addRow("", QLabel(text))
        self.m_theme = QComboBox()
        for key in THEMES:
            self.m_theme.addItem(tr(THEME_LABELS[key]), key)
        self.m_theme.setCurrentIndex(max(0, self.m_theme.findData(settings.theme)))
        form.addRow(tr("islands.theme"), self.m_theme)
        self.m_language = QComboBox()
        for code, name in languages().items():
            self.m_language.addItem(name, code)  # i18n: ok (each language's own name)
        self.m_language.setCurrentIndex(max(0, self.m_language.findData(settings.language)))
        self.m_language.setToolTip(tr("islands.tip.language_apps_windows_change_applies"))
        form.addRow(tr("common.language"), self.m_language)
        self.m_choices: dict[str, QComboBox] = {}
        if choices:
            form.addRow(QLabel(tr("islands.settings.new_island_start")))
        for key, label, options in choices or ():
            combo = QComboBox()
            for value, text in options.items():
                combo.addItem(text, value)
            combo.setCurrentIndex(max(0, combo.findData(getattr(settings, key))))
            form.addRow(tr(label), combo)
            self.m_choices[key] = combo
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("common.save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("common.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(760, 0)

    def _pick(self, edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("islands.choose_folder"), edit.text())
        if folder:
            edit.setText(folder)

    def values(self) -> dict[str, str]:
        return {
            **{k: e.text().strip() for k, e in self.m_edits.items()},
            "theme": self.m_theme.currentData(),
            "language": self.m_language.currentData(),
            **{key: combo.currentData() for key, combo in self.m_choices.items()},
        }
