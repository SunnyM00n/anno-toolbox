"""The island editor as a PC application (Qt, PySide6): one workbench window over ``editorapp.App``, laid out
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
from ...suite import features, gamesync, icons, theme, widgets
from ...suite.background import Background, long_lived, release_long_lived
from .. import archivedelivery, editorapp
from . import flycamera
from .desktopeditor import LOOKS, IslandDoc, game_tools, open_session
from .desktopforms import edit_menu as forms_menu
from .desktopgroup import edit_menu
from .desktopislands import GameTab, SettingsDialog, tab_title
from .desktopjobs import CANCEL, STOP, JobRunner
from .desktoprhiview import make_island_view
from .desktopribbon import (
    MODES,
    Ribbon,
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
    "Layers": N_("islands.layers"),
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
SMOKE_MIN, SMOKE_WAIT = 4.0, 90.0  # seconds: before ``--smoke`` looks, and how long it waits for the island to draw
HELP = N_("islands.left_drag_orbits_middle_pans")


def window_size(available: tuple[int, int]) -> tuple[int, int]:
    """The window's first size: 1700 x 1000 where the screen (its usable size in logical pixels, so at a 150 % or
    200 % scale too) holds it, else most of the screen."""
    return min(1700, int(available[0] * 0.94)), min(1000, int(available[1] * 0.9))


def dock_widths(width: int) -> tuple[int, int, int]:
    """The Islands dock, the Material Gallery and Properties as wide as a window of ``width`` lets them be, so the 3D
    view keeps the larger part; the full size at 1700, never narrower than a list or a form needs."""
    k = min(1.0, width / 1700)
    return max(300, int(380 * k)), max(200, int(300 * k)), max(200, int(320 * k))


def request_name(request: tuple) -> str:
    """What an open request (``MainWindow._load``) names, for the status bar: the island's file or list name (a blank
    island's request ends in its options, an island file's in its mod folder and edit file)."""
    kind = request[0]
    name = request[2] if kind == "blank" else request[1]
    return str(name).replace("\\", "/").rsplit("/", 1)[-1]


def gizmo_action(gizmo) -> str:
    """The ribbon button of the view's gizmo mode."""
    if gizmo.mode == "rotate":
        return "gizmo_tilt" if gizmo.tilt else "gizmo_turn"
    return "gizmo_scale" if gizmo.mode == "scale" else "gizmo_move"


class Holder(QWidget):
    """The slot of a dock or view page: an island's panel, or a note while no island is open."""

    def __init__(self, note: str) -> None:
        super().__init__()
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.note = QLabel(note, alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True, objectName="note")
        self.layout_.addWidget(self.note)
        self.panel: QWidget | None = None

    def set(self, panel: QWidget | None) -> None:
        if self.panel is not None:
            self.panel.hide()
            self.panel.setParent(None)  # gone from the screen now, deleted when the event loop is next idle
            self.panel.deleteLater()
        self.panel = panel
        self.note.setVisible(panel is None)
        if panel is not None:
            self.layout_.addWidget(panel)


class MainWindow(JobRunner, QMainWindow):
    def __init__(self, app: editorapp.App | None = None, games: tuple[str, ...] | None = None) -> None:
        """``games``: the keys of the only games the window deals with, None for all."""
        super().__init__()
        self.app = app or editorapp.App(games=games)
        names = " / ".join(k.removeprefix("anno") for k in self.app.games or ())
        self.title = TITLE if self.app.games is None else tr("islands.anno_island_editor_preview", names=names)
        self.tool_windows: list = []  # the tool windows opened (``open_tool``: kept alive while shown)
        self.installed: dict[
            str, bool
        ] = {}  # per game that loads archives: one of ours is installed (a button restores)
        self.doc: IslandDoc | None = None
        self.loading: tuple | None = None  # the request being read off the window's thread
        self.closing = False  # the window waits for its workers before it closes (``finish_work``)
        self.background = Background(self)
        self.setWindowTitle(self.title)
        tabs = tabs_for(self.app.games, game_tools(self.app.games))
        self.needs = {it.id: it.need for it in items(tabs)}
        self.icon_names = {it.id: it.icon for it in items(tabs)}
        # The one 3D view lives from the start: a second one added to the shown window made Qt recreate it.
        self.view = make_island_view(rhiview.chosen(self.app.settings.renderer), self)
        self.view.status.connect(self.say)
        self.actions = make_actions(self, tabs)
        self.apply_theme()
        hints = QGuiApplication.styleHints()
        if hints is not None:  # the system's light or dark, live (the setting "system")
            hints.colorSchemeChanged.connect(self.system_scheme_changed)
        self._bind_actions()
        self.ribbon = Ribbon(self.actions, tabs)
        self.ribbon.fieldChanged.connect(self._field)
        self._toolbars()
        self._pages()
        self.resize(*window_size(self.screen().availableGeometry().size().toTuple()))
        self._docks()
        self._menus()
        bar = self.menuBar()
        self.ribbon.set_access_keys(widgets.access_letters(a.text() for a in bar.actions()), widgets.menu_groups(bar))
        self._status()
        self._default_layout = self.saveState(LAYOUT_VERSION)
        self.persist_layout = True  # False: this window neither reads nor writes the remembered layout
        self.restore_layout()
        self._log_next, self._job_state, self._open_after = 0, None, None
        self.timer = QTimer(self, interval=700, timeout=self.poll)
        self.timer.start()
        self.reload()
        self.sync()

    def system_scheme_changed(self, _scheme) -> None:
        """Windows switched between light and dark: follow it, unless the setting pins a theme."""
        if self.app.settings.theme == "system":
            self.apply_theme()

    def apply_theme(self) -> None:
        """The palette, style sheet and icons of the theme the setting means (System: Windows' light or dark)."""
        theme.apply(self, self.app.settings.theme)
        self.view.set_scheme(theme.scheme())  # the 3D view's own colours, live
        tabs = getattr(self, "tabs", None)  # (not there yet while the window is built)
        for k in range(tabs.count() if tabs else 0):  # the island lists' group headers carry the theme's colours
            for lst in getattr(tabs.widget(k), "lists", ()):
                lst.fill()
        for window in self.tool_windows:
            window.set_scheme(theme.scheme())
        icons.set_foreground(theme.colors()["icon"])
        for id, action in self.actions.items():
            action.setIcon(icons.icon(self.icon_names[id]))

    # layout
    def restore_layout(self) -> None:
        """The window size, place and docks of the last run (settings ``layout``); a state this window cannot
        take (docks that are gone) leaves the default."""
        layout = self.app.settings.layout
        if not self.persist_layout or not layout:
            return
        if layout.get("geometry"):
            self.restoreGeometry(QByteArray.fromBase64(layout["geometry"].encode("ascii")))
        if layout.get("state"):
            self.restoreState(QByteArray.fromBase64(layout["state"].encode("ascii")), LAYOUT_VERSION)

    def remember_layout(self) -> None:
        """The docks, and the window's place unless it is a page of another window (the suite's shell)."""
        if self.persist_layout:
            geometry = self.app.settings.layout.get("geometry", "")
            if self.isWindow():
                geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
            state = bytes(self.saveState(LAYOUT_VERSION).toBase64()).decode("ascii")
            self.app.save_layout({"geometry": geometry, "state": state} if geometry else {"state": state})

    def reset_layout(self) -> None:
        """Every dock back where it started, and the remembered layout forgotten."""
        self.restoreState(self._default_layout, LAYOUT_VERSION)
        if self.persist_layout:
            self.app.save_layout({})

    def _toolbars(self) -> None:
        bar = QToolBar(tr("islands.ribbon"), self, movable=False, floatable=False)
        bar.addWidget(self.ribbon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.addToolBarBreak()
        self.view_tabs = QTabBar(drawBase=False, expanding=False)
        for title in PAGES.values():
            self.view_tabs.addTab(tr(title))
        row = QToolBar(tr("islands.views"), self, movable=False, floatable=False)
        row.addWidget(self.view_tabs)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, row)

    def _pages(self) -> None:
        self.pages = QStackedWidget()
        self.holders = {
            "Layers": Holder(tr("islands.views_layers_appear_here_once")),
            "Checks": Holder(tr("islands.checks_results_appear_here_grid")),
            "History": Holder(tr("islands.islands_changes_appear_here_once")),
        }
        self.log = QPlainTextEdit(readOnly=True)
        self.pages.addWidget(self.view)
        for name in list(PAGES)[1:]:
            self.pages.addWidget(self.holders.get(name) or self.log)
        self.setCentralWidget(self.pages)
        self.view_tabs.currentChanged.connect(self.pages.setCurrentIndex)

    def _docks(self) -> None:
        self.tabs = QTabWidget()  # one tab per game, then the one that builds for another game
        self.tabs.currentChanged.connect(self._game_tab_shown)
        self.dock_holders = {
            "Material Gallery": Holder(tr("islands.open_island_see_ground_materials")),
            "Object Groups": Holder(tr("islands.open_island_see_models_place")),
            "Buildings": Holder(tr("islands.open_island_see_buildings_objects")),
            "Properties": Holder(tr("islands.open_island")),
            "On the island": Holder(tr("islands.open_island")),
            "Minimap": Holder(tr("islands.open_island_see_minimap")),
        }
        self.docks: dict[str, QDockWidget] = {}
        for name, widget in {"Islands": self.tabs, **self.dock_holders}.items():
            dock = QDockWidget(tr(DOCK_TITLES[name]), self)
            dock.setObjectName(name)
            dock.setWidget(widget)
            self.docks[name] = dock
        area = Qt.DockWidgetArea
        d = self.docks
        self.addDockWidget(area.LeftDockWidgetArea, d["Islands"])
        self.addDockWidget(area.RightDockWidgetArea, d["Material Gallery"])
        self.addDockWidget(area.RightDockWidgetArea, d["Properties"])
        self.splitDockWidget(d["Material Gallery"], d["Properties"], Qt.Orientation.Horizontal)
        self.addDockWidget(area.RightDockWidgetArea, d["On the island"])
        self.splitDockWidget(d["Properties"], d["On the island"], Qt.Orientation.Vertical)
        self.tabifyDockWidget(d["On the island"], d["Minimap"])
        self.tabifyDockWidget(d["Material Gallery"], d["Object Groups"])  # after the splits: Qt loses a tabbed one
        self.tabifyDockWidget(d["Object Groups"], d["Buildings"])
        d["Material Gallery"].raise_()
        islands, gallery, props = dock_widths(self.width())
        self.resizeDocks([d["Islands"]], [islands], Qt.Orientation.Horizontal)
        self.resizeDocks([d["Material Gallery"], d["Properties"]], [gallery, props], Qt.Orientation.Horizontal)
        self.resizeDocks([d["Properties"], d["On the island"]], [300, 520], Qt.Orientation.Vertical)
        if "paint" not in game_tools(self.app.games):  # nothing to paint with: the gallery stays off
            d["Material Gallery"].hide()
            d["Object Groups"].raise_()

    def _menus(self) -> None:
        bar = self.menuBar()
        file = bar.addMenu(tr("common.menu.file"))
        for id in ("save", "build"):
            self._menu_item(file, id)
        file.addSeparator()
        file.addAction(tr("islands.open_island_mod"), self.open_from_mod)
        file.addAction(tr("islands.games_folders"), self.edit_settings)
        file.addAction(tr("islands.reload_islands"), lambda: self.reload(wait=False))
        self.close_island_action = file.addAction(tr("islands.close_island"), self.close_island)
        file.addSeparator()
        file.addAction(tr("islands.quit"), self.close)
        edit = bar.addMenu(tr("common.menu.edit"))
        for id in ("undo", "redo"):
            self._menu_item(edit, id)
        edit.addSeparator()
        for id in ("delete", "duplicate", "deselect"):
            self._menu_item(edit, id)
        edit_menu(self, edit)
        forms_menu(self, edit)
        rendering = bar.addMenu(tr("common.menu.rendering"))
        self.look_actions: dict[str, QAction] = {}
        looks = LOOKS if self.app.games is None else ()  # the rules and looks are 117's and 2070's
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
            act = rendering.addAction(tr(label))
            act.setCheckable(True)
            act.setToolTip(tr(tip))
            act.triggered.connect(lambda on, key=key: self.doc and self.doc.set_look(key, on))
            self.look_actions[key] = act
            if key == "particles_frozen":
                rendering.addSeparator()
        self._quality_menu(rendering)
        shown = [id for id in ("road_pieces", "check_rules", "check_slots", "check_falls") if id in self.actions]
        if shown:
            tools = bar.addMenu(tr("common.menu.tools"))
            for id in shown:
                self._menu_item(tools, id)
        layout = bar.addMenu(tr("islands.menu.layout"))
        for dock in self.docks.values():
            layout.addAction(dock.toggleViewAction())
        layout.addSeparator()
        layout.addAction(tr("islands.reset_layout"), self.reset_layout)
        bar.addMenu(tr("islands.help")).addAction(
            tr("islands.controls"), lambda: QMessageBox.information(self, self.title, tr(HELP))
        )

    def _menu_item(self, menu, id: str) -> None:
        """A ribbon button in a menu: the action itself, or (for a two-line label) a one-line twin that follows it."""
        action = self.actions[id]
        if "\n" not in action.text():
            menu.addAction(action)
            return
        twin = menu.addAction(action.icon(), one_line(action.text()), lambda: action.trigger())
        twin.setEnabled(action.isEnabled())
        action.changed.connect(lambda: twin.setEnabled(action.isEnabled()))

    def _status(self) -> None:
        bar = self.statusBar()
        self.badge = QLabel()
        self.fov = QSlider(Qt.Orientation.Horizontal, minimum=20, maximum=90, value=45, fixedWidth=110)
        self.fov_label = QLabel("45°")
        self.speed = QDoubleSpinBox(minimum=0.25, maximum=4.0, singleStep=0.25, value=1.0, decimals=2)
        self.mode_label = QLabel(objectName="modeLabel")
        self.camera_mode = QComboBox()
        self.camera_mode.addItems([tr(label) for label in flycamera.LABELS.values()])
        self.camera_mode.setToolTip(tr("islands.tip.orbit_about_pivot_fly_freely"))
        self.camera_mode.currentIndexChanged.connect(lambda k: self.view.set_camera_mode(flycamera.MODES[k]))
        self.fov.valueChanged.connect(self._fov)
        self.speed.valueChanged.connect(self.view.set_speed)
        for widget in (
            self.badge,
            QLabel(" " + tr("islands.camera")),
            self.camera_mode,
            QLabel(" " + tr("islands.fov")),
            self.fov,
            self.fov_label,
            QLabel(" " + tr("islands.speed")),
            self.speed,
            self.mode_label,
        ):
            bar.addPermanentWidget(widget)

    def _fov(self, degrees: int) -> None:
        self.fov_label.setText(f"{degrees}°")
        self.view.set_fov(degrees)

    # the ribbon's buttons
    def _bind_actions(self) -> None:
        def on_doc(name: str):
            return lambda _=False: self.doc is not None and getattr(self.doc, name)()

        v = self.view
        handlers = {
            "undo": on_doc("undo"),
            "redo": on_doc("redo"),
            "save": on_doc("save"),
            "build": on_doc("build"),
            "deselect": on_doc("deselect"),
            "focus": on_doc("focus"),
            "delete": lambda _=False: v.delete_selection(),
            "duplicate": lambda _=False: v.duplicate_selection(),
            "point_delete": lambda _=False: v.delete_selection(),
            "point_insert": lambda _=False: v.duplicate_selection(),
            "models": lambda _=False: self.raise_dock("Object Groups"),
            "materials": lambda _=False: self.raise_dock("Material Gallery"),
            "show_hidden": lambda _=False: self.doc is not None and self.doc.objects.show_hidden(),
            "foliage": lambda on=False: self.doc is not None and self.doc.objects.set_foliage(on),
            "check_rules": lambda _=False: self.doc is not None and self.doc.run_check("rules"),
            "check_slots": lambda _=False: self.doc is not None and self.doc.run_check("slots"),
            "check_falls": lambda _=False: self.doc is not None and self.doc.run_check("falls"),
            "road_pieces": lambda _=False: self.open_tool("road_pieces"),
        }
        handlers = {id: handler for id, handler in handlers.items() if id in self.actions}
        for id in self.actions:
            if id.startswith("tool_"):
                handlers[id] = lambda _=False, id=id: self.doc is not None and self.doc.set_tool(id)
            elif id.startswith("gizmo_"):
                handlers[id] = lambda _=False, id=id: self.doc is not None and self.doc.set_gizmo(id)
        missing = [id for id in self.actions if id not in handlers]
        if missing:
            raise KeyError(f"ribbon buttons without a handler: {missing}")
        for id, action in self.actions.items():
            action.triggered.connect(handlers[id])
        shortcuts = {
            "undo": [QKeySequence.StandardKey.Undo],
            "redo": [QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Y")],
            "save": [QKeySequence.StandardKey.Save],
        }
        for id, keys in shortcuts.items():
            self.actions[id].setShortcuts(keys)

    def _quality_menu(self, rendering) -> None:
        """Rendering > Rendering quality: the presets of ``render.quality`` for the open island's game (117 and
        1800; the older games draw at their best), kept per game in the settings."""
        menu = rendering.addMenu(tr("islands.menu.rendering_quality"))
        menu.setToolTip(tr("islands.tip.how_much_view_draws_117"))
        self.quality_actions: dict[str, QAction] = {}
        for key, label in (*quality.PRESETS.items(), (quality.CUSTOM, N_("islands.custom"))):
            act = menu.addAction(tr(label))
            act.setCheckable(True)
            widgets.enable_unless(act, tr("islands.tip.quality_custom") if key == quality.CUSTOM else "")
            act.triggered.connect(lambda _on, key=key: self.set_quality(key))
            self.quality_actions[key] = act

    def set_quality(self, name: str) -> None:
        """The open island's game drawn at preset ``name`` from now on, kept in the settings."""
        doc = self.doc
        if doc is None or doc.game_key() not in quality.GAMES:
            return
        kept = dict(getattr(self.app.settings, "quality", {}))
        kept[doc.game_key()] = name
        self.app.set_settings({"quality": kept})
        doc.apply_quality()
        self.sync()

    def _field(self, id: str, value) -> None:
        if self.doc is None:
            return
        if id in ("radius", "strength", "spacing", "zone", "areakind", "tint"):
            self.doc.set_brush(id, value)
            self.ribbon.set_field(id, value)  # the same brush on another tab
        elif id == "target":
            self.doc.set_target(value)
        elif id == "goto":
            self.doc.goto(value)

    def raise_dock(self, name: str) -> None:
        dock = self.docks[name]
        dock.show()
        dock.raise_()

    def show_page(self, name: str) -> None:
        self.view_tabs.setCurrentIndex(list(PAGES).index(name))
        if name == "Viewport":
            self.view.setFocus()

    def say(self, text: str) -> None:
        self.statusBar().showMessage(text, 15000 if text else 1)

    def sync(self) -> None:
        """The ribbon, the menus and the status bar follow the island's state."""
        doc, v = self.doc, self.view
        state = doc.state() if doc else {}
        for id, action in self.actions.items():
            action.setEnabled(available(self.needs[id], state))
        want = tool_action(v.tool, v.brush) if doc else None
        for id, action in self.actions.items():
            if id.startswith("tool_"):
                action.setChecked(id == want)
            elif id.startswith("gizmo_"):
                action.setChecked(doc is not None and id == gizmo_action(v.gizmo))
        self.actions["foliage"].setChecked(bool(doc) and v.hidden.foliage)
        looks = doc.look_state() if doc else {}
        for key, action in self.look_actions.items():
            enabled, on = looks.get(key, (False, False))
            action.setEnabled(enabled)
            action.setChecked(on)
        q = doc.view.opts.quality if doc else None
        for key, action in getattr(self, "quality_actions", {}).items():
            action.setEnabled(q is not None and key != quality.CUSTOM)
            action.setChecked(q is not None and q.preset == key)
        self.ribbon.apply_state(state)
        self.close_island_action.setEnabled(doc is not None)
        mode = mode_of(v.tool if doc else None, bool(doc) and doc.session.readonly)
        bar = self.statusBar()
        if bar.property("mode") != mode:
            bar.setProperty("mode", mode)
            bar.style().unpolish(bar)
            bar.style().polish(bar)
        self.mode_label.setText(tr(MODES[mode]))
        self.badge.setText(doc.badge() if doc else "")
        self.setWindowTitle(f"{doc.window_title()} · {self.title}" if doc else self.title)

    # islands
    _reloads = 0  # counts the reloads asked for: only the last one's lists are shown

    def reload(self, wait: bool = True) -> None:
        """Fill the Islands panel from the games' lists again. ``wait`` False (the menu's Reload islands): every
        game's lists are read off the window's thread and the panel is refilled when they are."""
        self._reloads += 1
        mine = self._reloads
        if not wait:
            self.statusBar().showMessage(tr("islands.reading_island_lists"))
        self.background.run(self._read_lists, lambda got: self._lists_read(got, mine, wait), wait=wait)

    def _read_lists(self) -> tuple[list[str], dict, dict[str, bool], object]:
        """What the panel shows, read from the games (any thread): the problems, each game's lists, which games
        hold an archive of the editor's, and the routes the extensions' tabs need."""
        problems = self.app.problems()
        if problems:
            return problems, {}, {}, None
        catalogue = self.app.catalogue()
        installed = {k: self.app.installed(k) for k in archivedelivery.ISLAND_DELIVERIES}
        return problems, catalogue, installed, self.app.routes() if self.app.games is None else None

    def _lists_read(self, got, mine: int, wait: bool) -> None:
        if mine != self._reloads:
            return  # asked for again meanwhile
        if isinstance(got, Exception):
            if wait:
                raise got
            self.statusBar().showMessage(tr("islands.island_lists_not_read", error=got))
            return
        current = self.tabs.tabText(self.tabs.currentIndex()) if self.tabs.count() else ""
        self.tabs.blockSignals(True)  # refilling the tabs is no pick of a game (``_game_tab_shown``)
        try:
            self._fill_game_tabs(current, got)
        finally:
            self.tabs.blockSignals(False)

    def _game_tab_shown(self, index: int) -> None:
        """A game's tab of the Islands panel was turned to: the suite's other apps follow that game. The
        panel lists every game, so the tab stays turned whatever they answer (suite.md "One game")."""
        key = getattr(self.tabs.widget(index), "key", "")
        if key:
            gamesync.may_switch(self, key)

    def show_game(self, key: str) -> bool:
        """Turn the Islands panel to the game ``key``; False when it lists no such game."""
        for k in range(self.tabs.count()):
            if getattr(self.tabs.widget(k), "key", "") == key:
                self.tabs.setCurrentIndex(k)
                return True
        return False

    def shown_game(self) -> str:
        return getattr(self.tabs.currentWidget(), "key", "")

    def _fill_game_tabs(self, current: str, lists: tuple) -> None:
        problems, catalogue, self.installed, routes = lists
        self.tabs.clear()
        if problems:
            self.statusBar().showMessage("; ".join(problems))
            self.tabs.addTab(QLabel(tr("islands.set_game_folders_file_gt")), tr("islands.setup"))
            return
        for key, entry in catalogue.items():
            self.tabs.addTab(GameTab(self, key, entry), tab_title(entry["name"]))
            self.tabs.setTabToolTip(self.tabs.count() - 1, entry["name"])
        if routes:  # the tabs an extension adds need an island build
            for tab in features.extensions(features.ISLAND_TABS).values():
                self.tabs.addTab(tab.make(self, catalogue, routes), tr(tab.title))
        names = [self.tabs.tabText(i) for i in range(self.tabs.count())]
        if current in names:
            self.tabs.setCurrentIndex(names.index(current))
        total = sum(len(e["native"]) for e in catalogue.values())
        self.statusBar().showMessage(tr("islands.total_islands_games", total=total, games=len(catalogue)))

    def open(self, kind: str, name: str, wait: bool = True) -> None:
        """Load an island of the lists into this window (the open one is closed first, asking to save its
        edits); the island that is already open is only shown. ``wait`` False: read off the window's thread, the
        window stays responsive and the island appears when it is read (the lists' Edit)."""
        self._load((kind, name), lambda: self.app.open(kind, name), wait)

    def open_blank(self, key: str, name: str, shaped: bool = False) -> None:
        """Start a blank island of 1800, 1404 or 1701 from one of the game's (``App.open_blank``, ``shaped``: its
        ground reshaped as a new island's is) and load it."""
        self._load(("blank", key, name, shaped), lambda: self.app.open_blank(key, name, shaped))

    def open_blank117(self, grid: int | None = None) -> None:
        """A new 117 island from scratch at a grid size (asked for when not given; ``App.open_blank117``)."""
        if grid is None:
            sizes = [str(g) for g in GRID_SIZES]
            text, ok = QInputDialog.getItem(self, TITLE, tr("islands.grid_size_new_island_tiles"), sizes, 2, False)
            if not ok:
                return
            grid = int(text)
        self._load(("blank117", grid), lambda: self.app.open_blank117(grid))

    def open_blank2070(self, size: int | None = None) -> None:
        """A new 2070 island from scratch at a size (asked for when not given; ``App.open_blank2070``)."""
        if size is None:
            text, ok = QInputDialog.getItem(
                self, TITLE, tr("islands.size_new_island_tiles"), [str(n) for n in editorapp.NEW_SIZES], 0, False
            )
            if not ok:
                return
            size = int(text)
        self._load(("blank2070", size), lambda: self.app.open_blank2070(size))

    def open_from_mod(self) -> None:
        """Pick a mod folder, then one of the islands it holds, loose or packed in its archives, and load it."""
        start = self.app.settings.mods or ""
        folder = QFileDialog.getExistingDirectory(self, tr("islands.mod_folder"), start)
        if not folder:
            return
        try:
            islands = self.app.mod_islands(Path(folder))
        except Exception as exc:  # the reason, not a crash
            QMessageBox.warning(self, TITLE, f"{type(exc).__name__}: {exc}")
            return
        if not islands:
            QMessageBox.information(self, TITLE, tr("islands.no_island_a7m_loose_archive", folder=folder))
            return
        island, ok = QInputDialog.getItem(
            self, TITLE, tr("islands.islands_of_name", name=Path(folder).name), islands, 0, False
        )
        if ok:
            self.open_file(island, Path(folder))

    def open_file(self, island: str, mod: Path | None = None, edits: Path | None = None) -> None:
        """Load an island file of a mod folder or the game (the ``desktop ISLAND`` command line)."""
        self._load(("file", island, mod, edits), lambda: self.app.open_path(island, mod, edits))

    def _load(self, request: tuple, opener, wait: bool = True) -> None:
        """Open what ``opener`` opens: the island's files read and its edit session made (off the window's thread
        unless ``wait``), then its panels; one open at a time."""
        if self.doc is not None and self.doc.opened == request:
            self.show_page("Viewport")
            return
        if self.loading is not None or not self.close_island():
            return
        self.loading = request
        self.statusBar().showMessage(tr("islands.opening_name", name=request_name(request)))

        def read():
            with long_lived():
                info = opener()
                return info, open_session(self.app.editor, info)

        self.background.run(read, lambda got: self._loaded(request, got), wait=wait)

    def _loaded(self, request: tuple, got) -> None:
        """The island read (``got``: (info, prepared) or the exception): its document attached, or the reason."""
        self.loading = None
        if self.closing:  # read for a window that is closing: let go of unseen
            return
        try:
            if isinstance(got, Exception):
                raise got
            info, prepared = got
            doc = IslandDoc(self, info["name"].rsplit("/", 1)[-1], info, opened=request, prepared=prepared)
        except Exception as exc:  # the reason, not a crash
            reason = f"{type(exc).__name__}: {exc}"
            QMessageBox.warning(self, TITLE, reason)
            for k in range(self.tabs.count()):  # the island stays in its list, with the reason on its row
                for lst in getattr(self.tabs.widget(k), "lists", ()):
                    if request[0] != "file" and request[1] in lst.names:
                        lst.mark(request[1], reason.splitlines()[0][:160])
            self.sync()
            return
        self._attach(doc)

    def _attach(self, doc: IslandDoc) -> None:
        """Put the island's panels into the docks and view pages, and the brush and save settings on the ribbon."""
        self.doc = doc
        panels = {
            "Material Gallery": doc.gallery,
            "Object Groups": doc.library,
            "Buildings": doc.buildings,
            "Properties": doc.props,
            "On the island": doc.objects,
            "Minimap": doc.minimap,
            "Layers": doc.layers,
            "Checks": doc.checks,
            "History": doc.history,
        }
        for name, panel in panels.items():
            (self.dock_holders.get(name) or self.holders[name]).set(panel)
        brush = self.view.brush
        self.ribbon.set_field("radius", brush["radius"])
        self.ribbon.set_field("strength", brush["strength"])
        self.ribbon.set_field("target", 1 if doc.session.edits.get("target") == "new" else 0)
        self.show_page("Viewport")
        self.sync()

    def close_island(self) -> bool:
        """Close the open island; False when the user keeps it (unsaved edits, Cancel)."""
        doc = self.doc
        if doc is None:
            return True
        if not doc.confirm_close():
            return False
        self.doc = None
        doc.close()
        release_long_lived()
        for holder in (*self.dock_holders.values(), *self.holders.values()):
            holder.set(None)
        self.sync()
        return True

    def busy_with(self) -> str | None:
        """What still runs off the window's thread: the job's name, else the island being read; None when idle."""
        job = self.app.job
        if job is not None and not job.wait(0):
            return job.name
        return request_name(self.loading) if self.loading is not None else None

    def closeEvent(self, event) -> None:
        if self.closing:  # asked again while it waits: the first close goes on
            event.ignore()
            return
        answer = self.ask_running_job()
        if answer == CANCEL or not self.close_island():
            event.ignore()
            return
        if answer == STOP and self.app.job is not None:
            self.app.job.stop()  # it ends at its next step; the wait below is for that
        self.finish_work()
        self.remember_layout()
        super().closeEvent(event)

    # settings
    def edit_settings(self) -> None:
        dialog = SettingsDialog(self.app.settings, self, self.app.games)
        if dialog.exec():
            self.app.set_settings(dialog.values())
            self.apply_theme()
            self.reload()


def smoke_report(window: MainWindow, started: float) -> dict | None:
    """What a packaged program writes for ``--smoke``, once an open island has drawn (None: not yet)."""
    doc, renderer = window.doc, window.view.renderer
    if doc is not None and (renderer is None or renderer.busy()) and time.monotonic() - started < SMOKE_WAIT:
        return None
    return {
        "title": window.windowTitle(),
        "ribbon": window.ribbon.tab_names(),
        "games": [window.tabs.tabText(k) for k in range(window.tabs.count())],
        "island": doc.title if doc else None,
        "entries": len(doc.session.entries) if doc else 0,
        "models": len(renderer.batches) if renderer is not None else 0,
        "messages": list(renderer.messages) if renderer is not None else [],
        "log": window.log.toPlainText().splitlines()[-5:],
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
    qt = QApplication.instance() or QApplication([sys.argv[0]])
    qt.setApplicationName(TITLE)
    qt.setStyle("Fusion")  # the native style ignores parts of the dark palette
    window = MainWindow(games=games)
    window.show()
    if island and games:
        QTimer.singleShot(0, lambda: window.open(games[0], island))
    elif island:
        QTimer.singleShot(0, lambda: window.open_file(island, mod, edits))
    if smoke is not None:
        started = time.monotonic()
        timer = QTimer(window, interval=500)

        def check() -> None:
            report = smoke_report(window, started) if time.monotonic() - started > SMOKE_MIN else None
            if report is not None:
                smoke.write_text(json.dumps(report, indent=1), encoding="utf-8", newline="\n")
                timer.stop()
                window.close()
                qt.quit()

        timer.timeout.connect(check)
        timer.start()
    return qt.exec()


if __name__ == "__main__":
    sys.exit(run())
