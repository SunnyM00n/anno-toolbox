"""The settings window of the Toolbox (Settings > Settings): every global setting on one page of a tab bar - General
(language, theme, update check), Games and folders, Rendering (the 3D views' device, hardware acceleration, the game's
own shaders) and, where an app adds rows (``extspecs.SSettingsRows``), that app's page (the island editor's start
values and Performance switches). ``CSettingsDialog(settings, parent, games, rows)`` asks for the folders of ``games``
(None: all); ``values()`` is what was set, ``applied(values)`` lets the running program follow it. The suite's
Settings menu and the island editor's File menu open it.
"""

from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, languages, tr
from ..othergames import installs
from ..render import nativelook, rhiview
from .extspecs import SSettingsRows
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


def applied(values: dict) -> None:
    """Let the running program follow saved ``values``: the game's own shaders are read by each view as it is made
    (the device and the hardware setting are read at the start)."""
    if "native_shaders" in values:
        from ..render import nativegpu  # the native GPU module: Windows only

        nativegpu.want(bool(values["native_shaders"]))


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


def _add_hinted(form: QFormLayout, label: str, field: QWidget, hint: str = "") -> None:
    """A row of ``form`` with a short hint line under it (a choice that needs explaining)."""
    form.addRow(label, field)
    if hint:
        form.addRow("", _hint(hint))


class CSettingsDialog(QDialog):
    """The settings window (module docstring); one tab per kind of setting."""

    def __init__(
        self,
        settings: CSettings,
        parent=None,
        games: tuple[str, ...] | None = None,
        rows: tuple[SSettingsRows, ...] = (),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("central.menu.settings"))
        self.m_settings = settings
        self.m_edits: dict[str, QLineEdit] = {}
        self.m_choices: dict[str, QComboBox] = {}
        self.m_switches: dict[str, dict[str, QCheckBox]] = {}
        self.m_tabs = QTabWidget()
        self.m_tabs.addTab(self._general_page(), tr("settings.page.general"))
        self.m_tabs.addTab(self._folders_page(games), tr("settings.page.games"))
        self.m_tabs.addTab(self._rendering_page(), tr("settings.page.rendering"))
        if any(row.choices or row.switches for row in rows):
            self.m_tabs.addTab(self._island_editor_page(rows), tr("settings.page.island_editor"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("common.save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("common.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.m_tabs)
        layout.addWidget(buttons)
        self.resize(760, 0)

    def _general_page(self) -> QWidget:
        page, form = QWidget(), QFormLayout()
        self.m_theme = QComboBox()
        for key in THEMES:
            self.m_theme.addItem(tr(THEME_LABELS[key]), key)
        self.m_theme.setCurrentIndex(max(0, self.m_theme.findData(self.m_settings.theme)))
        form.addRow(tr("islands.theme"), self.m_theme)
        self.m_language = QComboBox()
        for code, name in languages().items():
            self.m_language.addItem(name, code)  # i18n: ok (each language's own name)
        self.m_language.setCurrentIndex(max(0, self.m_language.findData(self.m_settings.language)))
        _add_hinted(
            form, tr("common.language"), self.m_language, tr("islands.tip.language_apps_windows_change_applies")
        )
        self.m_check_updates = QCheckBox(tr("common.check_updates_start"), checked=self.m_settings.check_updates)
        _add_hinted(form, "", self.m_check_updates, tr("settings.check_updates_hint"))
        page.setLayout(form)
        return page

    def _folders_page(self, games: tuple[str, ...] | None) -> QWidget:
        page, form = QWidget(), QFormLayout()
        for key, label in folders_for(games):
            edit = QLineEdit(getattr(self.m_settings, key))
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
        page.setLayout(form)
        return page

    def _rendering_page(self) -> QWidget:
        page, form = QWidget(), QFormLayout()
        self.m_renderer = QComboBox()
        self.m_renderer.addItem(tr("settings.renderer_automatic"), "")
        offered = rhiview.offered()
        for name in offered:
            self.m_renderer.addItem(rhiview.LABELS[name], name)
        stored = self.m_settings.renderer
        if stored and stored not in offered:  # kept as it is: a settings file from another machine
            self.m_renderer.addItem(tr("settings.renderer_missing", name=rhiview.LABELS.get(stored, stored)), stored)
        self.m_renderer.setCurrentIndex(max(0, self.m_renderer.findData(stored)))
        _add_hinted(form, tr("settings.renderer"), self.m_renderer, tr("settings.renderer_hint"))
        self.m_hardware = QCheckBox(tr("settings.hardware"), checked=self.m_settings.hardware)
        _add_hinted(form, "", self.m_hardware, tr("settings.hardware_hint"))
        self.m_native_shaders = QCheckBox(tr("common.games_own_shaders"), checked=self.m_settings.native_shaders)
        if not nativelook.direct3d_here():
            self.m_native_shaders.setEnabled(False)
            self.m_native_shaders.setToolTip(tr("common.game_look_windows_only"))
        _add_hinted(form, "", self.m_native_shaders, tr("settings.native_shaders_hint"))
        page.setLayout(form)
        return page

    def _island_editor_page(self, rows: tuple[SSettingsRows, ...]) -> QWidget:
        page, form = QWidget(), QFormLayout()
        choices = [choice for row in rows for choice in row.choices]
        if choices:
            form.addRow(QLabel(tr("islands.settings.new_island_start")))
        for key, label, options in choices:
            combo = QComboBox()
            for value, text in options.items():
                combo.addItem(text, value)
            combo.setCurrentIndex(max(0, combo.findData(getattr(self.m_settings, key))))
            form.addRow(tr(label), combo)
            self.m_choices[key] = combo
        for key, switches in (switch for row in rows for switch in row.switches):
            form.addRow(QLabel(tr("islands.performance")))
            form.addRow("", _hint(tr("settings.performance_hint")))
            stored = getattr(self.m_settings, key)
            boxes = self.m_switches[key] = {}
            for name, (label, tip, *default) in switches.items():  # a third entry: the switch's state when not set
                box = QCheckBox(
                    tr(label).replace("\n", " "), checked=bool(stored.get(name, default[0] if default else True))
                )
                box.setToolTip(tr(tip))
                form.addRow("", box)
                boxes[name] = box
        page.setLayout(form)
        return page

    def _pick(self, edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("islands.choose_folder"), edit.text())
        if folder:
            edit.setText(folder)

    def values(self) -> dict:
        """The settings as the window shows them, by settings key (a dict-valued setting keeps its other entries)."""
        return {
            **{k: e.text().strip() for k, e in self.m_edits.items()},
            "theme": self.m_theme.currentData(),
            "language": self.m_language.currentData(),
            "check_updates": self.m_check_updates.isChecked(),
            "renderer": self.m_renderer.currentData(),
            "hardware": self.m_hardware.isChecked(),
            "native_shaders": self.m_native_shaders.isChecked(),
            **{key: combo.currentData() for key, combo in self.m_choices.items()},
            **{
                key: {**getattr(self.m_settings, key), **{name: box.isChecked() for name, box in boxes.items()}}
                for key, boxes in self.m_switches.items()
            },
        }
