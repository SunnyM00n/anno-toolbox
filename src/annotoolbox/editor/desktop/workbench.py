"""The island editor as a PC application (Qt, PySide6): one workbench window over ``editorapp.CApp``, laid out
after Anno 1800's internal editor - a ribbon of tabs (``desktopribbon``), the 3D view (``desktopview``) with a
row of view tabs above it, an Islands dock on the left (per game its Native, Edited and From-other-games islands,
``desktopislands``), the Material Gallery, Object Groups, Properties and the object tree on the
right, and a status bar with the camera and the mode. "Edit" loads an island into the window (one at a time);
the window never starts again. Plan and use: ``docs/island-editor.md``.

    anno-toolbox
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSlider,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ...infra.i18n import N_, tr
from ...islands.donor117 import GRID_SIZES
from ...render import quality, rhiview
from ...suite import centralmenu, features, gamesync, icons, theme, widgets
from ...suite.background import CBackground, long_lived, release_long_lived
from ...suite.settingsdialog import CSettingsDialog, applied
from .. import archivedelivery, editorapp
from . import flycamera
from .desktopeditor import (
    LOOKS,
    LOOKS_2070,
    CIslandDoc,
    game_tools,
    look_blocker,
    open_session,
    set_shipped,
    settings_rows,
    shipped_state,  # fmt: skip
)
from .desktopforms import edit_menu as forms_menu
from .desktopgroup import edit_menu
from .desktopislands import CGameTab, tab_title
from .desktopjobs import CANCEL, STOP, WAIT, CJobRunner
from .desktopprogress import COpenProgress
from .desktoprhiview import make_island_view
from .desktopribbon import (
    MODES,
    CRibbon,
    available,
    items,
    make_actions,
    mode_of,
    one_line,
    tabs_for,
    tool_action,
)

TITLE = "Anno Island Editor"
PAGES = {
    "Viewport": N_("islands.viewport"),
    "Checks": N_("common.checks"),
    "History": N_("common.history"),
    "Log": N_("common.log"),
}  # the pages' keys in tab order -> their titles
# The docks' keys (also their object names, so a remembered layout keeps working) -> their titles.
DOCK_TITLES = {
    "Islands": N_("common.islands"),
    "Material Gallery": N_("islands.dock.material_gallery"),
    "Object Groups": N_("islands.dock.object_groups"),
    "Buildings": N_("islands.dock.buildings"),
    "Properties": N_("common.properties"),
    "On the island": N_("islands.dock.on_the_island"),
    "Minimap": N_("islands.dock.minimap"),
}
LAYOUT_VERSION = 2  # the state version: raise it when docks change, an older remembered layout is then ignored
SMOKE_MIN_S, SMOKE_WAIT_S = 4.0, 90.0  # seconds: before ``--smoke`` looks, and how long it waits for the island to draw
HELP = N_("islands.left_drag_orbits_middle_pans")
# How long Python lets one thread run before it hands the interpreter lock on (the default is 5 ms). The island's
# reads run in worker threads in Python; with the default the window's thread waited up to a second for its turn
# between two events while three of them ran (known-issues "Island editor held the window while an island opened").
SWITCH_INTERVAL_S = 0.0005


def window_size(available_px: tuple[int, int]) -> tuple[int, int]:
    """The window's first size: 1700 x 1000 where the screen (its usable size in logical pixels, so at a 150 % or
    200 % scale too) holds it, else most of the screen."""
    return min(1700, int(available_px[0] * 0.94)), min(1000, int(available_px[1] * 0.9))


def dock_widths(width_px: int) -> tuple[int, int, int]:
    """The Islands dock, the Material Gallery and Properties as wide as a window of ``width`` lets them be, so the 3D
    view keeps the larger part; the full size at 1700, never narrower than a list or a form needs."""
    ratio = min(1.0, width_px / 1700)
    return max(300, int(380 * ratio)), max(200, int(300 * ratio)), max(200, int(320 * ratio))


def listed_name(request: tuple):
    """The island an open request names as the lists hold it: a blank island's request starts with the game key, the
    island it is made from comes after."""
    return request[2] if request[0] == "blank" else request[1]


def request_name(request: tuple) -> str:
    """What an open request (``CMainWindow._load``) names, for the status bar: the island's file or list name (a blank
    island's request ends in its options, an island file's in its mod folder and edit file)."""
    return str(listed_name(request)).replace("\\", "/").rsplit("/", 1)[-1]


def gizmo_action(gizmo) -> str:
    """The ribbon button of the view's gizmo mode."""
    if gizmo.m_mode == "rotate":
        return "gizmo_tilt" if gizmo.m_tilt else "gizmo_turn"
    return "gizmo_scale" if gizmo.m_mode == "scale" else "gizmo_move"


class CHolder(QWidget):
    """The slot of a dock or view page: an island's panel, or a note while no island is open."""

    def __init__(self, note: str) -> None:
        super().__init__()
        self.m_layout_ = QVBoxLayout(self)
        self.m_layout_.setContentsMargins(0, 0, 0, 0)
        self.m_note = QLabel(note, alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True, objectName="note")
        self.m_layout_.addWidget(self.m_note)
        self.m_panel: QWidget | None = None

    def set(self, panel: QWidget | None) -> None:
        if self.m_panel is not None:
            self.m_panel.hide()
            self.m_panel.setParent(None)  # gone from the screen now, deleted when the event loop is next idle
            self.m_panel.deleteLater()
        self.m_panel = panel
        self.m_note.setVisible(panel is None)
        if panel is not None:
            self.m_layout_.addWidget(panel)


class CMainWindow(CJobRunner, QMainWindow):
    def __init__(
        self, app: editorapp.CApp | None = None, games: tuple[str, ...] | None = None, lists_wait: bool = True
    ) -> None:
        """``games``: the keys of the only games the window deals with, None for all. ``lists_wait`` False (the
        programs): the Islands panel fills when the games' lists are read, off the window's thread, instead of the
        window waiting for them (known-issues "Island editor held the window")."""
        super().__init__()
        sys.setswitchinterval(min(sys.getswitchinterval(), SWITCH_INTERVAL_S))
        self.m_app = app or editorapp.CApp(games=games)
        names = " / ".join(key.removeprefix("anno") for key in self.m_app.m_games or ())
        self.m_title = TITLE if self.m_app.m_games is None else tr("islands.anno_island_editor_preview", names=names)
        self.m_tool_windows: list = []  # the tool windows opened (``open_tool``: kept alive while shown)
        self.m_installed: dict[
            str, bool
        ] = {}  # per game that loads archives: one of ours is installed (a button restores)
        self.m_doc: CIslandDoc | None = None
        self.m_loading: tuple | None = None  # the request being read off the window's thread
        self.m_job_answer = WAIT  # what the person said about a running job when closing (``can_close``)
        self.m_closing = False  # the window waits for its workers before it closes (``finish_work``)
        self.m_background = CBackground(self)
        self.setWindowTitle(self.m_title)
        tabs = tabs_for(self.m_app.m_games, game_tools(self.m_app.m_games))
        self.m_needs = {item.id: item.requirement for item in items(tabs)}
        self.m_icon_names = {item.id: item.icon for item in items(tabs)}
        # The one 3D view lives from the start: a second one added to the shown window made Qt recreate it.
        self.m_view = make_island_view(rhiview.chosen(self.m_app.m_settings.renderer), self)
        self.m_view.status.connect(self.say)
        self.m_view.m_group.m_settings = self.m_app.m_settings  # the start values of a new island
        self.m_actions = make_actions(self, tabs)
        self.apply_theme()
        hints = QGuiApplication.styleHints()
        if hints is not None:  # the system's light or dark, live (the setting "system")
            hints.colorSchemeChanged.connect(self.system_scheme_changed)
        self._bind_actions()
        self.m_ribbon = CRibbon(self.m_actions, tabs)
        self.m_ribbon.fieldChanged.connect(self._field)
        self._toolbars()
        self._pages()
        self.resize(*window_size(self.screen().availableGeometry().size().toTuple()))
        self._docks()
        self._menus()
        bar = self.menuBar()
        self.m_ribbon.set_access_keys(
            widgets.access_letters(action.text() for action in bar.actions()), widgets.menu_groups(bar)
        )
        self._status()
        self.m_progress = COpenProgress(self)
        self._m_default_layout = self.saveState(LAYOUT_VERSION)
        self.m_persist_layout = True  # False: this window neither reads nor writes the remembered layout
        self.restore_layout()
        self._m_log_next, self._m_job_state, self._m_open_after = 0, None, None
        self.m_timer = QTimer(self, interval=700, timeout=self.poll)
        self.m_timer.start()
        self._m_game_after = ""  # a game tab asked for before the lists were read (``show_game``)
        self.reload(lists_wait)
        self.sync()

    def system_scheme_changed(self, _scheme) -> None:
        """Windows switched between light and dark: follow it, unless the setting pins a theme."""
        if self.m_app.m_settings.theme == "system":
            self.apply_theme()

    def apply_theme(self) -> None:
        """The palette, style sheet and icons of the theme the setting means (System: Windows' light or dark)."""
        theme.apply(self, self.m_app.m_settings.theme)
        self.m_view.set_scheme(theme.scheme())  # the 3D view's own colours, live
        tabs = getattr(self, "m_tabs", None)  # (not there yet while the window is built)
        for k in range(tabs.count() if tabs else 0):  # the island lists' group headers carry the theme's colours
            for island_list in getattr(tabs.widget(k), "m_lists", ()):
                island_list.fill()
        for window in self.m_tool_windows:
            window.set_scheme(theme.scheme())
        icons.set_foreground(theme.colors()["icon"])
        for id, action in self.m_actions.items():
            action.setIcon(icons.icon(self.m_icon_names[id]))

    # layout
    def restore_layout(self) -> None:
        """The window size, place and docks of the last run (settings ``layout``); a state this window cannot
        take (docks that are gone) leaves the default."""
        layout = self.m_app.m_settings.layout
        if not self.m_persist_layout or not layout:
            return
        if layout.get("geometry"):
            self.restoreGeometry(QByteArray.fromBase64(layout["geometry"].encode("ascii")))
        if layout.get("state"):
            self.restoreState(QByteArray.fromBase64(layout["state"].encode("ascii")), LAYOUT_VERSION)

    def remember_layout(self) -> None:
        """The docks, and the window's place unless it is a page of another window (the suite's shell)."""
        if self.m_persist_layout:
            geometry = self.m_app.m_settings.layout.get("geometry", "")
            if self.isWindow():
                geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
            state = bytes(self.saveState(LAYOUT_VERSION).toBase64()).decode("ascii")
            self.m_app.save_layout({"geometry": geometry, "state": state} if geometry else {"state": state})

    def reset_layout(self) -> None:
        """Every dock back where it started, and the remembered layout forgotten."""
        self.restoreState(self._m_default_layout, LAYOUT_VERSION)
        if self.m_persist_layout:
            self.m_app.save_layout({})

    def _toolbars(self) -> None:
        bar = QToolBar(tr("islands.ribbon"), self, movable=False, floatable=False)
        bar.addWidget(self.m_ribbon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.addToolBarBreak()
        self.m_view_tabs = QTabBar(drawBase=False, expanding=False)
        for title in PAGES.values():
            self.m_view_tabs.addTab(tr(title))
        row = QToolBar(tr("islands.views"), self, movable=False, floatable=False)
        row.addWidget(self.m_view_tabs)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, row)

    def _pages(self) -> None:
        self.m_pages = QStackedWidget()
        self.m_holders = {
            "Checks": CHolder(tr("islands.checks_results_appear_here_grid")),
            "History": CHolder(tr("islands.islands_changes_appear_here_once")),
        }
        self.m_log = QPlainTextEdit(readOnly=True)
        self.m_pages.addWidget(self.m_view)
        for name in list(PAGES)[1:]:
            self.m_pages.addWidget(self.m_holders.get(name) or self.m_log)
        self.setCentralWidget(self.m_pages)
        self.m_view_tabs.currentChanged.connect(self.m_pages.setCurrentIndex)

    def _docks(self) -> None:
        self.m_tabs = QTabWidget()  # one tab per game, then the one that builds for another game
        self.m_tabs.currentChanged.connect(self._game_tab_shown)
        self.m_dock_holders = {
            "Material Gallery": CHolder(tr("islands.open_island_see_ground_materials")),
            "Object Groups": CHolder(tr("islands.open_island_see_models_place")),
            "Buildings": CHolder(tr("islands.open_island_see_buildings_objects")),
            "Properties": CHolder(tr("islands.open_island")),
            "On the island": CHolder(tr("islands.open_island")),
            "Minimap": CHolder(tr("islands.open_island_see_minimap")),
        }
        self.m_docks: dict[str, QDockWidget] = {}
        for name, widget in {"Islands": self.m_tabs, **self.m_dock_holders}.items():
            dock = QDockWidget(tr(DOCK_TITLES[name]), self)
            dock.setObjectName(name)
            dock.setWidget(widget)
            self.m_docks[name] = dock
        area = Qt.DockWidgetArea
        docks = self.m_docks
        self.addDockWidget(area.LeftDockWidgetArea, docks["Islands"])
        self.addDockWidget(area.RightDockWidgetArea, docks["Material Gallery"])
        self.addDockWidget(area.RightDockWidgetArea, docks["Properties"])
        self.splitDockWidget(docks["Material Gallery"], docks["Properties"], Qt.Orientation.Horizontal)
        self.addDockWidget(area.RightDockWidgetArea, docks["On the island"])
        self.splitDockWidget(docks["Properties"], docks["On the island"], Qt.Orientation.Vertical)
        self.tabifyDockWidget(docks["On the island"], docks["Minimap"])
        self.tabifyDockWidget(
            docks["Material Gallery"], docks["Object Groups"]
        )  # after the splits: Qt loses a tabbed one
        self.tabifyDockWidget(docks["Object Groups"], docks["Buildings"])
        docks["Material Gallery"].raise_()
        islands, gallery, props = dock_widths(self.width())
        self.resizeDocks([docks["Islands"]], [islands], Qt.Orientation.Horizontal)
        self.resizeDocks([docks["Material Gallery"], docks["Properties"]], [gallery, props], Qt.Orientation.Horizontal)
        self.resizeDocks([docks["Properties"], docks["On the island"]], [300, 520], Qt.Orientation.Vertical)
        if "paint" not in game_tools(self.m_app.m_games):  # nothing to paint with: the gallery stays off
            docks["Material Gallery"].hide()
            docks["Object Groups"].raise_()

    def _menus(self) -> None:
        bar = self.menuBar()
        file = bar.addMenu(tr("common.menu.file"))
        for id in ("save", "build"):
            self._menu_item(file, id)
        centralmenu.mark(self.m_actions["save"], centralmenu.SAVE)
        file.addSeparator()
        file.addAction(tr("islands.open_island_mod"), self.open_from_mod)
        file.addAction(tr("islands.open_island_project"), self.open_from_project)
        centralmenu.mark(file.addAction(tr("common.settings_dots"), self.edit_settings), centralmenu.SETTINGS)
        file.addAction(tr("islands.reload_islands"), lambda: self.reload(is_waiting=False))
        self.m_close_island_action = file.addAction(tr("islands.close_island"), self.close_island)
        file.addSeparator()
        centralmenu.mark(file.addAction(tr("islands.quit"), self.close), centralmenu.QUIT)
        edit = bar.addMenu(tr("common.menu.edit"))
        for id in ("undo", "redo"):
            self._menu_item(edit, id)
        edit.addSeparator()
        for id in ("delete", "duplicate", "deselect"):
            self._menu_item(edit, id)
        edit_menu(self, edit)
        forms_menu(self, edit)
        rendering = bar.addMenu(tr("common.menu.rendering"))
        self.m_look_actions: dict[str, QAction] = {}
        self.m_look_tips: dict[str, str] = {}
        looks = LOOKS if self.m_app.m_games is None else ()  # the rules and looks are 117's and 2070's
        basics = (
            ("shadows", N_("islands.shadows"), N_("islands.sun_shadows_following_camera")),
            (
                "particles",
                N_("islands.particles"),
                N_("islands.particle_effects_models_steam_smoke"),
            ),
            ("particles_frozen", N_("islands.freeze_particles"), N_("islands.hold_particle_effects_they")),
        )
        for key, label, tip in (*basics, *looks):
            action = rendering.addAction(tr(label))
            action.setCheckable(True)
            action.setToolTip(tr(tip))
            action.triggered.connect(lambda is_checked, key=key: self.m_doc and self.m_doc.set_look(key, is_checked))
            self.m_look_actions[key] = action
            self.m_look_tips[key] = action.toolTip()
            if key == "particles_frozen":
                rendering.addSeparator()
        self.m_shipped_action = rendering.addAction(tr("islands.shipped_shaders"))
        self.m_shipped_action.setCheckable(True)
        self.m_shipped_action.triggered.connect(lambda is_checked: set_shipped(self, is_checked))
        self._quality_menu(rendering)
        shown = [id for id in ("road_pieces", "check_rules", "check_slots", "check_falls") if id in self.m_actions]
        if shown:
            tools = bar.addMenu(tr("common.menu.tools"))
            for id in shown:
                self._menu_item(tools, id)
        layout = centralmenu.mark(bar.addMenu(tr("islands.menu.layout")), centralmenu.WINDOW)
        for dock in self.m_docks.values():
            layout.addAction(dock.toggleViewAction())
        layout.addSeparator()
        layout.addAction(tr("islands.reset_layout"), self.reset_layout)
        bar.addMenu(tr("islands.help")).addAction(
            tr("islands.controls"), lambda: QMessageBox.information(self, self.m_title, tr(HELP))
        )

    def _menu_item(self, menu, id: str) -> None:
        """A ribbon button in a menu: the action itself, or (for a two-line label) a one-line twin that follows it."""
        action = self.m_actions[id]
        if "\n" not in action.text():
            menu.addAction(action)
            return
        twin = menu.addAction(action.icon(), one_line(action.text()), lambda: action.trigger())
        twin.setEnabled(action.isEnabled())
        action.changed.connect(lambda: twin.setEnabled(action.isEnabled()))

    def _status(self) -> None:
        bar = self.statusBar()
        self.m_badge = QLabel()
        self.m_fov = QSlider(Qt.Orientation.Horizontal, minimum=20, maximum=90, value=45, fixedWidth=110)
        self.m_fov_label = QLabel("45°")
        self.m_speed = QDoubleSpinBox(minimum=0.25, maximum=4.0, singleStep=0.25, value=1.0, decimals=2)
        self.m_mode_label = QLabel(objectName="modeLabel")
        self.m_camera_mode = QComboBox()
        self.m_camera_mode.addItems([tr(label) for label in flycamera.LABELS.values()])
        self.m_camera_mode.setToolTip(tr("islands.tip.orbit_about_pivot_fly_freely"))
        self.m_camera_mode.currentIndexChanged.connect(lambda k: self.m_view.set_camera_mode(flycamera.MODES[k]))
        self.m_fov.valueChanged.connect(self._fov)
        self.m_speed.valueChanged.connect(self.m_view.set_speed)
        for widget in (
            self.m_badge,
            QLabel(" " + tr("islands.camera")),
            self.m_camera_mode,
            QLabel(" " + tr("islands.fov")),
            self.m_fov,
            self.m_fov_label,
            QLabel(" " + tr("islands.speed")),
            self.m_speed,
            self.m_mode_label,
        ):
            bar.addPermanentWidget(widget)

    def _fov(self, degrees: int) -> None:
        self.m_fov_label.setText(f"{degrees}°")
        self.m_view.set_fov(degrees)

    # the ribbon's buttons
    def _bind_actions(self) -> None:
        def on_doc(name: str):
            return lambda _=False: self.m_doc is not None and getattr(self.m_doc, name)()

        view = self.m_view
        handlers = {
            "undo": on_doc("undo"),
            "redo": on_doc("redo"),
            "save": on_doc("save"),
            "build": on_doc("build"),
            "deselect": on_doc("deselect"),
            "focus": on_doc("focus"),
            "delete": lambda _=False: view.delete_selection(),
            "duplicate": lambda _=False: view.duplicate_selection(),
            "point_delete": lambda _=False: view.delete_selection(),
            "point_insert": lambda _=False: view.duplicate_selection(),
            "models": lambda _=False: self.raise_dock("Object Groups"),
            "materials": lambda _=False: self.raise_dock("Material Gallery"),
            "show_hidden": lambda _=False: self.m_doc is not None and self.m_doc.m_objects.show_hidden(),
            "foliage": lambda is_checked=False: self.m_doc is not None and self.m_doc.m_objects.set_foliage(is_checked),
            "check_rules": lambda _=False: self.m_doc is not None and self.m_doc.run_check("rules"),
            "check_slots": lambda _=False: self.m_doc is not None and self.m_doc.run_check("slots"),
            "check_falls": lambda _=False: self.m_doc is not None and self.m_doc.run_check("falls"),
            "road_pieces": lambda _=False: self.open_tool("road_pieces"),
        }
        handlers = {id: handler for id, handler in handlers.items() if id in self.m_actions}
        for id in self.m_actions:
            if id.startswith("tool_"):
                handlers[id] = lambda _=False, id=id: self.m_doc is not None and self.m_doc.set_tool(id)
            elif id.startswith("gizmo_"):
                handlers[id] = lambda _=False, id=id: self.m_doc is not None and self.m_doc.set_gizmo(id)
            elif id.startswith("layer_"):
                handlers[id] = lambda is_checked=False, id=id: (
                    self.m_doc is not None and self.m_doc.set_layer(id[6:], is_checked)
                )
            elif id.startswith("perf_"):
                handlers[id] = lambda is_checked=False, id=id: (
                    self.m_doc is not None and self.m_doc.set_performance(id[5:], is_checked)
                )
        missing = [id for id in self.m_actions if id not in handlers]
        if missing:
            raise KeyError(f"ribbon buttons without a handler: {missing}")
        for id, action in self.m_actions.items():
            action.triggered.connect(handlers[id])
        shortcuts = {
            "undo": [QKeySequence.StandardKey.Undo],
            "redo": [QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Y")],
            "save": [QKeySequence.StandardKey.Save],
        }
        for id, keys in shortcuts.items():
            self.m_actions[id].setShortcuts(keys)

    def _quality_menu(self, rendering) -> None:
        """Rendering > Rendering quality: the presets of ``render.quality`` for the open island's game (117 and
        1800; the older games draw at their best), kept per game in the settings."""
        menu = rendering.addMenu(tr("islands.menu.rendering_quality"))
        menu.setToolTip(tr("islands.tip.how_much_view_draws_117"))
        self.m_quality_actions: dict[str, QAction] = {}
        for key, label in (*quality.PRESETS.items(), (quality.CUSTOM, N_("islands.custom"))):
            action = menu.addAction(tr(label))
            action.setCheckable(True)
            widgets.enable_unless(action, tr("islands.tip.quality_custom") if key == quality.CUSTOM else "")
            action.triggered.connect(lambda _on, key=key: self.set_quality(key))
            self.m_quality_actions[key] = action

    def set_quality(self, name: str) -> None:
        """The open island's game drawn at preset ``name`` from now on, kept in the settings."""
        doc = self.m_doc
        if doc is None or doc.game_key() not in quality.GAMES:
            return
        kept = dict(getattr(self.m_app.m_settings, "quality", {}))
        kept[doc.game_key()] = name
        self.m_app.set_settings({"quality": kept})
        doc.apply_quality()
        self.sync()

    def _field(self, id: str, value) -> None:
        if self.m_doc is None:
            return
        if id in ("radius", "strength", "spacing", "zone", "areakind", "tint"):
            self.m_doc.set_brush(id, value)
            self.m_ribbon.set_field(id, value)  # the same brush on another tab
        elif id == "target":
            self.m_doc.set_target(value)
        elif id == "goto":
            self.m_doc.goto(value)

    def raise_dock(self, name: str) -> None:
        dock = self.m_docks[name]
        dock.show()
        dock.raise_()

    def show_page(self, name: str) -> None:
        self.m_view_tabs.setCurrentIndex(list(PAGES).index(name))
        if name == "Viewport":
            self.m_view.setFocus()

    def say(self, text: str) -> None:
        self.statusBar().showMessage(text, 15000 if text else 1)

    def sync(self) -> None:
        """The ribbon, the menus and the status bar follow the island's state."""
        doc, view = self.m_doc, self.m_view
        state = doc.state() if doc else {}
        for id, action in self.m_actions.items():
            action.setEnabled(available(self.m_needs[id], state))
        want = tool_action(view.m_tool, view.m_brush) if doc else None
        for id, action in self.m_actions.items():
            if id.startswith("tool_"):
                action.setChecked(id == want)
            elif id.startswith("gizmo_"):
                action.setChecked(doc is not None and id == gizmo_action(view.m_gizmo))
            elif id.startswith("layer_"):
                action.setChecked(not doc or view.m_opts.layers.get(id[6:], True))
            elif id.startswith("perf_"):
                action.setChecked(not doc or getattr(view.m_opts, id[5:], True))
        self.m_actions["foliage"].setChecked(bool(doc) and view.m_hidden.m_foliage)
        looks = doc.look_state() if doc else {}
        offers_2070 = bool(doc) and doc.offers_2070_looks()
        for key, action in self.m_look_actions.items():
            is_enabled, is_on = looks.get(key, (False, False))
            action.setVisible(key not in LOOKS_2070 or offers_2070)
            action.setEnabled(is_enabled)
            action.setToolTip((doc.look_reason(key) if doc else look_blocker(key)) or self.m_look_tips[key])
            action.setChecked(is_on)
        is_enabled, is_on, why = shipped_state(getattr(view, "m_backend", "gl"))
        self.m_shipped_action.setEnabled(is_enabled)
        self.m_shipped_action.setChecked(is_on)
        self.m_shipped_action.setToolTip(why or tr("islands.tip.shipped_shaders"))
        view_quality = doc.m_view.m_opts.quality if doc else None
        for key, action in getattr(self, "m_quality_actions", {}).items():
            action.setEnabled(view_quality is not None and key != quality.CUSTOM)
            action.setChecked(view_quality is not None and view_quality.preset == key)
        self.m_ribbon.apply_state(state)
        self.m_close_island_action.setEnabled(doc is not None)
        mode = mode_of(view.m_tool if doc else None, bool(doc) and doc.m_session.m_readonly)
        bar = self.statusBar()
        if bar.property("mode") != mode:
            bar.setProperty("mode", mode)
            bar.style().unpolish(bar)
            bar.style().polish(bar)
        self.m_mode_label.setText(tr(MODES[mode]))
        self.m_badge.setText(doc.badge() if doc else "")
        self.setWindowTitle(f"{doc.window_title()} · {self.m_title}" if doc else self.m_title)

    # islands
    _m_reloads = 0  # counts the reloads asked for: only the last one's lists are shown

    def reload(self, is_waiting: bool = True) -> None:
        """Fill the Islands panel from the games' lists again. ``is_waiting`` False (the menu's Reload islands): every
        game's lists are read off the window's thread and the panel is refilled when they are."""
        self._m_reloads += 1
        mine = self._m_reloads
        if not is_waiting:
            self.statusBar().showMessage(tr("islands.reading_island_lists"))
        self.m_background.run(
            self._read_lists, lambda got: self._lists_read(got, mine, is_waiting), is_waiting=is_waiting
        )

    def _read_lists(self) -> tuple[list[str], dict, dict[str, bool], object]:
        """What the panel shows, read from the games (any thread): the problems, each game's lists, which games
        hold an archive of the editor's, and the routes the extensions' tabs need."""
        problems = self.m_app.problems()
        if problems:
            return problems, {}, {}, None
        catalogue = self.m_app.catalogue()
        installed = {key: self.m_app.installed(key) for key in archivedelivery.ISLAND_DELIVERIES}
        return problems, catalogue, installed, self.m_app.routes() if self.m_app.m_games is None else None

    def _lists_read(self, got, mine: int, is_waiting: bool) -> None:
        if mine != self._m_reloads:
            return  # asked for again meanwhile
        if isinstance(got, Exception):
            if is_waiting:
                raise got
            self.statusBar().showMessage(tr("islands.island_lists_not_read", error=got))
            return
        current = self.m_tabs.tabText(self.m_tabs.currentIndex()) if self.m_tabs.count() else ""
        self.m_tabs.blockSignals(True)  # refilling the tabs is no pick of a game (``_game_tab_shown``)
        try:
            self._fill_game_tabs(current, got)
        finally:
            self.m_tabs.blockSignals(False)
        if self._m_game_after:
            key, self._m_game_after = self._m_game_after, ""
            self.show_game(key)

    def _game_tab_shown(self, index: int) -> None:
        """A game's tab of the Islands panel was turned to: the suite's other apps follow that game. The
        panel lists every game, so the tab stays turned whatever they answer (suite.md "One game")."""
        key = getattr(self.m_tabs.widget(index), "m_key", "")
        if key:
            gamesync.may_switch(self, key)

    def show_game(self, key: str) -> bool:
        """Turn the Islands panel to the game ``key``; False when it lists no such game (yet: while the lists are
        read it is turned to when they are in)."""
        for k in range(self.m_tabs.count()):
            if getattr(self.m_tabs.widget(k), "m_key", "") == key:
                self.m_tabs.setCurrentIndex(k)
                return True
        self._m_game_after = key
        return False

    def shown_game(self) -> str:
        return getattr(self.m_tabs.currentWidget(), "m_key", "")

    def _fill_game_tabs(self, current: str, lists: tuple) -> None:
        problems, catalogue, self.m_installed, routes = lists
        for k in range(self.m_tabs.count()):
            self.m_tabs.widget(k).deleteLater()  # ``clear`` only takes the pages out: each reload kept its lists
        self.m_tabs.clear()
        if problems:
            self.statusBar().showMessage("; ".join(problems))
            self.m_tabs.addTab(QLabel(tr("islands.set_game_folders_file_gt")), tr("islands.setup"))
            return
        for key, entry in catalogue.items():
            self.m_tabs.addTab(CGameTab(self, key, entry), tab_title(entry["name"]))
            self.m_tabs.setTabToolTip(self.m_tabs.count() - 1, entry["name"])
        if routes:  # the tabs an extension adds need an island build
            for tab in features.extensions(features.ISLAND_TABS).values():
                self.m_tabs.addTab(tab.make(self, catalogue, routes), tr(tab.title))
        names = [self.m_tabs.tabText(i) for i in range(self.m_tabs.count())]
        if current in names:
            self.m_tabs.setCurrentIndex(names.index(current))
        total = sum(len(entry["native"]) for entry in catalogue.values())
        self.statusBar().showMessage(tr("islands.total_islands_games", total=total, games=len(catalogue)))

    def open(self, kind: str, name: str, is_waiting: bool = True) -> None:
        """Load an island of the lists into this window (the open one is closed first, asking to save its
        edits); the island that is already open is only shown. ``is_waiting`` False: read off the window's thread, the
        window stays responsive and the island appears when it is read (the lists' Edit)."""
        self._load((kind, name), lambda: self.m_app.open(kind, name), is_waiting)

    def open_blank(self, key: str, name: str, is_shaped: bool = False) -> None:
        """Start a blank island of 1800, 1404 or 1701 from one of the game's (``CApp.open_blank``, ``is_shaped``: its
        ground reshaped as a new island's is) and load it."""
        self._load(("blank", key, name, is_shaped), lambda: self.m_app.open_blank(key, name, is_shaped))

    def open_blank117(self, grid_tiles: int | None = None) -> None:
        """A new 117 island from scratch at a grid size (asked for when not given; ``CApp.open_blank117``)."""
        if grid_tiles is None:
            sizes = [str(grid_size) for grid_size in GRID_SIZES]
            text, is_accepted = QInputDialog.getItem(
                self, TITLE, tr("islands.grid_size_new_island_tiles"), sizes, 2, False
            )
            if not is_accepted:
                return
            grid_tiles = int(text)
        self._load(("blank117", grid_tiles), lambda: self.m_app.open_blank117(grid_tiles))

    def open_blank2070(self, size_tiles: int | None = None) -> None:
        """A new 2070 island from scratch at a size (asked for when not given; ``CApp.open_blank2070``)."""
        if size_tiles is None:
            text, is_accepted = QInputDialog.getItem(
                self,
                TITLE,
                tr("islands.size_new_island_tiles"),
                [str(new_size) for new_size in editorapp.NEW_SIZES],
                0,
                False,
            )
            if not is_accepted:
                return
            size_tiles = int(text)
        self._load(("blank2070", size_tiles), lambda: self.m_app.open_blank2070(size_tiles))

    def open_from_mod(self) -> None:
        """Pick a mod folder, then one of the islands it holds, loose or packed in its archives, and load it."""
        start = self.m_app.m_settings.mods or ""
        folder = QFileDialog.getExistingDirectory(self, tr("islands.mod_folder"), start)
        if not folder:
            return
        try:
            islands = self.m_app.mod_islands(Path(folder))
        except Exception as error:  # the reason, not a crash
            QMessageBox.warning(self, TITLE, f"{type(error).__name__}: {error}")
            return
        if not islands:
            QMessageBox.information(self, TITLE, tr("islands.no_island_a7m_loose_archive", folder=folder))
            return
        island, is_accepted = QInputDialog.getItem(
            self, TITLE, tr("islands.islands_of_name", name=Path(folder).name), islands, 0, False
        )
        if is_accepted:
            self.open_file(island, Path(folder))

    def open_from_project(self) -> None:
        """Pick one of the islands of the mod the open project imported and load it: the project's file is the base."""
        islands = self.m_app.project_islands()
        if not islands:
            QMessageBox.information(self, TITLE, tr("islands.project_holds_no_islands"))
            return
        island, is_accepted = QInputDialog.getItem(self, TITLE, tr("islands.islands_of_project"), islands, 0, False)
        if is_accepted:
            self.open_project_island(island)

    def open_project_island(self, island: str, is_waiting: bool = True) -> None:
        self._load(("project", island), lambda: self.m_app.open_project_island(island), is_waiting)

    def open_file(
        self, island: str, mod: Path | None = None, edits: Path | None = None, is_waiting: bool = True
    ) -> None:
        """Load an island file of a mod folder or the game (the ``desktop ISLAND`` command line); ``is_waiting`` as in
        ``open``."""
        self._load(("file", island, mod, edits), lambda: self.m_app.open_path(island, mod, edits), is_waiting)

    def _load(self, request: tuple, opener, is_waiting: bool = True) -> None:
        """Open what ``opener`` opens: the island's files read and its edit session made (off the window's thread
        unless ``is_waiting``), then its panels; one open at a time."""
        if self.m_doc is not None and self.m_doc.m_opened == request:
            self.show_page("Viewport")
            return
        if self.m_loading is not None or not self.close_island():
            return
        self.m_loading = request
        self.m_progress.begin(request_name(request))

        def read():
            with long_lived():
                island_info = opener()
                self.m_progress.reading(request_name(request))
                return island_info, open_session(self.m_app.m_editor, island_info)

        self.m_background.run(read, lambda got: self._loaded(request, got), is_waiting=is_waiting)

    def _loaded(self, request: tuple, got) -> None:
        """The island read (``got``: (info, prepared) or the exception): its document attached, or the reason."""
        self.m_loading = None
        if self.m_closing:  # read for a window that is closing: let go of unseen
            return
        try:
            if isinstance(got, Exception):
                raise got
            island_info, prepared = got
            self.m_progress.building(request_name(request))
            doc = CIslandDoc(
                self, island_info["name"].rsplit("/", 1)[-1], island_info, opened=request, prepared=prepared
            )
        except Exception as error:  # the reason, not a crash
            self.m_progress.end()
            reason = f"{type(error).__name__}: {error}"
            QMessageBox.warning(self, TITLE, reason)
            for k in range(self.m_tabs.count()):  # the island stays in its list, with the reason on its row
                for island_list in getattr(self.m_tabs.widget(k), "m_lists", ()):
                    if request[0] not in ("file", "project") and listed_name(request) in island_list.m_names:
                        island_list.mark(listed_name(request), reason.splitlines()[0][:160])
            self.sync()
            return
        self._attach(doc)
        self.m_progress.follow(doc)

    def _attach(self, doc: CIslandDoc) -> None:
        """Put the island's panels into the docks and view pages, and the brush and save settings on the ribbon."""
        self.m_doc = doc
        panels = {
            "Material Gallery": doc.m_gallery,
            "Object Groups": doc.m_library,
            "Buildings": doc.m_buildings,
            "Properties": doc.m_props,
            "On the island": doc.m_objects,
            "Minimap": doc.m_minimap,
            "Checks": doc.m_checks,
            "History": doc.m_history,
        }
        for name, panel in panels.items():
            (self.m_dock_holders.get(name) or self.m_holders[name]).set(panel)
        brush = self.m_view.m_brush
        self.m_ribbon.set_field("radius", brush["radius"])
        self.m_ribbon.set_field("strength", brush["strength"])
        self.m_ribbon.set_field("target", 1 if doc.m_session.m_edits.get("target") == "new" else 0)
        self.show_page("Viewport")
        self.sync()

    def close_island(self) -> bool:
        """Close the open island; False when the user keeps it (unsaved edits, Cancel)."""
        if self.m_doc is not None and not self.m_doc.confirm_close():
            return False
        self.release_island()
        return True

    def release_island(self) -> None:
        """Let go of the open island, its edits already kept or dropped by the person."""
        doc = self.m_doc
        if doc is None:
            return
        self.m_doc = None
        self.m_progress.end()
        doc.close()
        release_long_lived()
        for holder in (*self.m_dock_holders.values(), *self.m_holders.values()):
            holder.set(None)
        self.sync()

    def busy_with(self) -> str | None:
        """What still runs off the window's thread: the job's name, else the island being read; None when idle."""
        job = self.m_app.m_job
        if job is not None and not job.wait(0):
            return job.m_name
        return request_name(self.m_loading) if self.m_loading is not None else None

    def can_close(self) -> bool:
        """The first step of closing, here and in the Toolbox: what to do with a running job and with unsaved edits
        are asked, nothing is released; the answer about the job is kept for ``closeEvent``."""
        answer = self.ask_running_job()
        if answer == CANCEL or (self.m_doc is not None and not self.m_doc.confirm_close()):
            return False
        self.m_job_answer = answer
        return True

    def closeEvent(self, event) -> None:
        if self.m_closing:  # asked again while it waits: the first close goes on
            event.ignore()
            return
        if not widgets.may_close(self):
            event.ignore()
            return
        answer, self.m_job_answer = self.m_job_answer, WAIT
        self.release_island()
        if answer == STOP and self.m_app.m_job is not None:
            self.m_app.m_job.stop()  # it ends at its next step; the wait below is for that
        self.finish_work()
        self.remember_layout()
        super().closeEvent(event)

    # settings
    def edit_settings(self) -> None:
        dialog = CSettingsDialog(self.m_app.m_settings, self, self.m_app.m_games, rows=(settings_rows(),))
        if dialog.exec():
            self.m_app.set_settings(dialog.values())
            applied(dialog.values())
            self.settings_changed()

    def settings_changed(self) -> None:
        """The settings were saved (here, or in the suite's Settings menu): the theme and the island lists again, and
        the open island follows its Performance switches; the menus and the ribbon show the new state."""
        self.apply_theme()
        self.reload(is_waiting=False)
        if self.m_doc is not None:
            self.m_doc.apply_performance()
        self.sync()


def smoke_report(window: CMainWindow, started: float) -> dict | None:
    """What a packaged program writes for ``--smoke``, once an open island has drawn (None: not yet)."""
    doc, renderer = window.m_doc, window.m_view.m_renderer
    if doc is not None and (renderer is None or renderer.busy()) and time.monotonic() - started < SMOKE_WAIT_S:
        return None
    return {
        "title": window.windowTitle(),
        "ribbon": window.m_ribbon.tab_names(),
        "games": [window.m_tabs.tabText(k) for k in range(window.m_tabs.count())],
        "island": doc.m_title if doc else None,
        "entries": len(doc.m_session.m_entries) if doc else 0,
        "models": len(renderer.m_batches) if renderer is not None else 0,
        "messages": list(renderer.m_messages) if renderer is not None else [],
        "log": window.m_log.toPlainText().splitlines()[-5:],
    }


def run(
    island: str | None = None,
    mod: Path | None = None,
    edits: Path | None = None,
    games: tuple[str, ...] | None = None,
    smoke: Path | None = None,
) -> int:
    """The application; ``island`` (a game path, in ``mod`` or the game, with its edit file ``edits``) is opened
    as soon as the window shows. ``games``: the only games it deals with (``--games``), None for all. ``smoke``:
    write what came up (``smoke_report``) to that JSON file once the island has drawn, and quit."""
    from ...render.glformat import prepare

    prepare()
    application = QApplication.instance() or QApplication([sys.argv[0]])
    application.setApplicationName(TITLE)
    application.setStyle("Fusion")  # the native style ignores parts of the dark palette
    window = CMainWindow(games=games, lists_wait=False)
    window.show()
    if island and games:
        QTimer.singleShot(0, lambda: window.open(games[0], island, is_waiting=False))
    elif island:
        QTimer.singleShot(0, lambda: window.open_file(island, mod, edits, is_waiting=False))
    if smoke is not None:
        started = time.monotonic()
        timer = QTimer(window, interval=500)

        def check() -> None:
            report = smoke_report(window, started) if time.monotonic() - started > SMOKE_MIN_S else None
            if report is not None:
                smoke.write_text(json.dumps(report, indent=1), encoding="utf-8", newline="\n")
                timer.stop()
                window.close()
                application.quit()

        timer.timeout.connect(check)
        timer.start()
    return application.exec()


if __name__ == "__main__":
    sys.exit(run())
