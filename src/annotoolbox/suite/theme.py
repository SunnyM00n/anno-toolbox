"""The island editor's look: after Anno 1800's internal editor, with an orange accent - a dark palette and a light
one, the window following the system's (live) unless the setting says otherwise - and the status bar's colour per
editing mode. Plan: ``docs/island-editor.md`` ("Layout").
"""

from __future__ import annotations

from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette

from . import qtlang

SCHEMES = {
    "dark": {
        "bg": "#262626",
        "panel": "#303030",
        "field": "#1e1e1e",
        "raised": "#3b3b3b",
        "edge": "#181818",
        "text": "#d6d6d6",
        "dim": "#8d8d8d",
        "accent": "#f28c28",
        "accent_dim": "#6f4318",
        "checked_text": "#ffffff",
        "danger": "#e0523c",
        "icon": "#d6d6d6",
    },
    "light": {
        "bg": "#e4e4e4",
        "panel": "#efefef",
        "field": "#ffffff",
        "raised": "#d4d4d4",
        "edge": "#b0b0b0",
        "text": "#1f1f1f",
        "dim": "#666666",
        "accent": "#d46a0a",
        "accent_dim": "#f5cfa6",
        "checked_text": "#1f1f1f",
        "danger": "#c23a24",
        "icon": "#3a3a3a",
    },
}
# The status bar's colour says which mode the editor is in (1800: purple in object mode, red-orange in terrain mode).
MODE_COLORS = {
    "dark": {"none": "#383838", "view": "#4a4a4a", "object": "#6a2c9e", "terrain": "#b8431a", "path": "#1f6a8a"},
    "light": {"none": "#8a8a8a", "view": "#6e6e6e", "object": "#8a4cc0", "terrain": "#d9622f", "path": "#3b8db0"},
}
MODE_TEXT = {"dark": "#ececec", "light": "#ffffff"}
# The road piece editor's canvas (roadcanvas.PieceCanvas): background, grid by line weight, outlines, picks
# (a translucent one is #AARRGGBB, as Qt reads it).
CANVAS = {
    "dark": {
        "bg": "#1e2328",
        "grid_whole": "#4a535d",
        "grid_half": "#353d45",
        "grid": "#2c333a",
        "edge": "#5affffff",
        "pick": "#4fa3d9",
        "hot": "#ffd166",
        "node": "#e07a5f",
        "outline": "#b4ffc450",
        "outline_fill": "#1effc450",
        "note": "#8a949e",
    },
    "light": {
        "bg": "#eef0f2",
        "grid_whole": "#b4bcc4",
        "grid_half": "#cfd5da",
        "grid": "#e0e4e7",
        "edge": "#5a000000",
        "pick": "#1f78b4",
        "hot": "#c98a00",
        "node": "#c4553a",
        "outline": "#c8d68a14",
        "outline_fill": "#28d68a14",
        "note": "#5f6a74",
    },
}
SETTINGS = ("system", "light", "dark")

_g_scheme = "dark"

STYLE = Template("""
QMainWindow::separator { background: $edge; width: 3px; height: 3px; }
QMenuBar { background: $bg; color: $text; }
QMenuBar::item:selected, QMenu::item:selected { background: $accent_dim; }
QMenu { background: $panel; color: $text; border: 1px solid $edge; }
QMenu::item:disabled { color: $dim; }
QToolBar { border: 0; spacing: 0; padding: 0; }
QDockWidget::title { background: $raised; padding: 3px 6px; color: $dim; }
QDockWidget { color: $dim; }
QTabBar::tab { background: transparent; color: $dim; padding: 3px 10px; border: 0; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected { color: $accent; border-bottom: 2px solid $accent; }
QTabWidget::pane { border: 1px solid $edge; top: -1px; }
QToolButton { color: $text; background: transparent; border: 1px solid transparent; border-radius: 2px; padding: 2px 4px; }
QToolButton:hover { background: $raised; border-color: $edge; }
QToolButton:checked { color: $checked_text; background: $accent_dim; border-color: $accent; }
QToolButton:disabled { color: $dim; }
QPushButton { background: $raised; color: $text; border: 1px solid $edge; border-radius: 2px; padding: 3px 10px; }
QPushButton:hover { border-color: $accent; }
QPushButton:checked { background: $accent_dim; border-color: $accent; }
QPushButton:disabled { color: $dim; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QListWidget, QTreeWidget, QTreeView, QTableWidget {
    background: $field; color: $text; border: 1px solid $edge; }
QComboBox QAbstractItemView { background: $panel; color: $text; selection-background-color: $accent_dim; selection-color: $text; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: $accent; }
QListWidget::item:selected, QTreeWidget::item:selected, QTreeView::item:selected, QTableWidget::item:selected { background: $accent_dim; color: $text; }
QListWidget#gallery::item:selected { background: transparent; border: 2px solid $accent; }
QHeaderView::section { background: $raised; border: 0; padding: 2px 4px; color: $dim; }
QScrollBar:vertical { width: 10px; background: $bg; }
QScrollBar:horizontal { height: 10px; background: $bg; }
QScrollBar::handle { background: $raised; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: $dim; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QSlider::groove:horizontal { height: 4px; background: $field; }
QSlider::handle:horizontal { background: $accent; width: 10px; margin: -4px 0; border-radius: 5px; }
QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid $dim; background: $field; }
QCheckBox::indicator:checked { background: $accent; }
QScrollArea#galleryScroll, QScrollArea#galleryScroll > QWidget > QWidget,
QScrollArea#assetForm, QScrollArea#assetForm > QWidget > QWidget { background: $bg; border: 0; }
QToolTip { background: $panel; color: $text; border: 1px solid $accent; }
QLabel { color: $text; }
QLabel#groupLabel { color: $dim; font-size: 8pt; }
QLabel#note { color: $dim; }
QFrame#groupSeparator { background: $edge; }
QLabel#hud { background: rgba(0, 0, 0, 120); color: #ececec; padding: 1px 6px; border-radius: 2px; }
QLabel#modeLabel { padding: 0 10px; font-weight: bold; }
QStatusBar { background: $edge; }
QStatusBar::item { border: 0; }
QStatusBar[mode] QLabel { color: $mode_text; }
""")


def scheme() -> str:
    """The scheme in use, ``dark`` or ``light``."""
    return _g_scheme


def colors() -> dict[str, str]:
    return SCHEMES[_g_scheme]


def canvas() -> dict[str, str]:
    """The road piece editor's canvas colours in the scheme in use."""
    return CANVAS[_g_scheme]


def resolve(setting: str) -> str:
    """The scheme a setting (``system``, ``light`` or ``dark``) means now; the system's is asked of Qt."""
    if setting in ("light", "dark"):
        return setting
    hints = QGuiApplication.styleHints()
    return "light" if hints is not None and hints.colorScheme() == Qt.ColorScheme.Light else "dark"


def stylesheet() -> str:
    """The style sheet, with a status bar rule per mode (``statusBar().setProperty("mode", ...)``)."""
    modes = "".join(
        f'QStatusBar[mode="{k}"] {{ background: {colour}; }}\n' for k, colour in MODE_COLORS[_g_scheme].items()
    )
    return STYLE.substitute(SCHEMES[_g_scheme], mode_text=MODE_TEXT[_g_scheme]) + modes


def palette() -> QPalette:
    """The palette every widget takes its colours from; the style sheet only adds the accents."""
    colours = {k: QColor(hex_colour) for k, hex_colour in colors().items()}
    qt_palette = QPalette()
    for role, colour in (
        (QPalette.ColorRole.Window, colours["bg"]),
        (QPalette.ColorRole.WindowText, colours["text"]),
        (QPalette.ColorRole.Base, colours["field"]),
        (QPalette.ColorRole.AlternateBase, colours["panel"]),
        (QPalette.ColorRole.Text, colours["text"]),
        (QPalette.ColorRole.Button, colours["raised"]),
        (QPalette.ColorRole.ButtonText, colours["text"]),
        (QPalette.ColorRole.Highlight, colours["accent_dim"]),
        (QPalette.ColorRole.HighlightedText, colours["text"]),
        (QPalette.ColorRole.ToolTipBase, colours["panel"]),
        (QPalette.ColorRole.ToolTipText, colours["text"]),
        (QPalette.ColorRole.PlaceholderText, colours["dim"]),
        (QPalette.ColorRole.Link, colours["accent"]),
    ):
        qt_palette.setColor(role, colour)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        qt_palette.setColor(QPalette.ColorGroup.Disabled, role, colours["dim"])
    return qt_palette


def apply(widget, setting: str = "system") -> str:
    """Give a top-level window (and everything it parents) the theme the setting means; returns its scheme."""
    global _g_scheme
    _g_scheme = resolve(setting)
    qtlang.install()  # Qt's own texts follow the interface language
    widget.setPalette(palette())
    widget.setStyleSheet(stylesheet())
    return _g_scheme
