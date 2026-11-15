"""The UI editor window's frame: ``build`` lays out its widgets and docks, ``wire`` connects their signals to the
window's slots, ``menus`` fills the menu bar. Split off ``uiwindow`` so the window class holds what it does, not how it
is put together; each takes the ``UiWindow`` and sets the widgets it names on it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QLineEdit,
    QListWidget,
    QStackedWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..suite import widgets
from . import dumpview, tweens
from .dumpview import DumpPanel
from .graphview import BindingGraph
from .panels import PalettePanel, PropertyPanel, SamplePanel, StatesPanel, ViewModelPanel
from .preview import Preview
from .timeline import SequenceEditor

APP = "ui"  # the key of its remembered state in the settings
GAMES = {"anno117": "Anno 117", "anno1800": "Anno 1800", "anno1404": "Anno 1404", "anno1701": "Anno 1701"}
OLD_GAMES = {
    "anno2070": "Anno 2070",
    "anno1404": "Anno 1404",
    "anno1701": "Anno 1701",
}  # the games of the old interface
ROLE = Qt.ItemDataRole.UserRole


def build(window) -> None:
    """The window's widgets: the preview in the centre, the panels as docks around it; then ``wire``."""
    window.preview = Preview()
    window.centre = QStackedWidget()  # the preview, or (a game's old interface) its page
    window.centre.addWidget(window.preview)
    window.setCentralWidget(window.centre)
    files = QWidget()
    window.file_filter = QLineEdit(placeholderText=tr("find a file: a scene, a name, a GUID"))
    window.file_list = QTreeWidget()
    window.file_list.setHeaderLabels([tr("File"), tr("Kind")])
    lay = QVBoxLayout(files)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(window.file_filter)
    lay.addWidget(window.file_list)
    window.element_tree = QTreeWidget()
    window.element_tree.setHeaderLabels([tr("Element"), tr("Kind")])
    window.props, window.palette_panel = PropertyPanel(), PalettePanel()
    window.vm, window.sample_panel, window.states = ViewModelPanel(), SamplePanel(), StatesPanel()
    window.graph = BindingGraph()
    window.checks = QListWidget()
    window.dump_panel = DumpPanel()
    window.sequence = SequenceEditor()
    window.tween_list: list[tweens.Tween] = []
    window.docks: dict[str, QDockWidget] = {}
    left, right, bottom = (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea,
                           Qt.DockWidgetArea.BottomDockWidgetArea)  # fmt: skip
    for name, widget, area in ((N_("Files"), files, left), (N_("Elements"), window.element_tree, left),
                               (N_("Palette"), window.palette_panel, left), (N_("Properties"), window.props, right),
                               (N_("View models"), window.vm, right), (N_("Sample values"), window.sample_panel, right),
                               (N_("Bindings"), window.graph, bottom), (N_("States and events"), window.states, bottom),
                               (N_("Sequence"), window.sequence, None), (N_("Checks"), window.checks, None),
                               (N_("Game dump"), window.dump_panel, None)):  # fmt: skip
        dock = QDockWidget(tr(name), window, objectName=f"ui{name.replace(' ', '')}")
        dock.setWidget(widget)
        if area is not None:  # the others join a tab group below
            window.addDockWidget(area, dock)
        window.docks[name] = dock
    window.tabifyDockWidget(window.docks["Elements"], window.docks["Palette"])
    window.docks["Elements"].raise_()
    window.tabifyDockWidget(window.docks["Properties"], window.docks["View models"])
    window.tabifyDockWidget(window.docks["View models"], window.docks["Sample values"])
    window.docks["Properties"].raise_()
    window.splitDockWidget(window.docks["Bindings"], window.docks["States and events"], Qt.Orientation.Horizontal)
    window.tabifyDockWidget(
        window.docks["Bindings"], window.docks["Sequence"]
    )  # one tab each: a third leaves a stray bar
    window.tabifyDockWidget(window.docks["States and events"], window.docks["Checks"])
    window.tabifyDockWidget(window.docks["Checks"], window.docks["Game dump"])
    window.docks["States and events"].raise_()
    window.docks["Bindings"].raise_()
    window.resizeDocks([window.docks["Files"], window.docks["Properties"]], [360, 420], Qt.Orientation.Horizontal)
    window.resizeDocks([window.docks["Bindings"]], [260], Qt.Orientation.Vertical)
    window.resizeDocks(
        [window.docks["Bindings"], window.docks["States and events"]], [1100, 500], Qt.Orientation.Horizontal
    )
    wire(window)


def wire(window) -> None:
    """Connect the panels' signals to the window's handlers: every edit goes through ``window._edit``."""
    window.file_filter.textChanged.connect(window._fill_files)
    window.file_list.itemDoubleClicked.connect(
        lambda it, _c: window.open_file(it.data(0, ROLE)) if it.data(0, ROLE) else None
    )
    window.element_tree.currentItemChanged.connect(lambda it, _old: window._pick(it.data(0, ROLE) if it else None))
    window.preview.selected.connect(window._picked_in_preview)
    window.preview.moved.connect(window._moved)
    window.preview.sized.connect(window._sized)
    window.props.edit_field.connect(lambda node, f, v: window._edit(lambda d: d.set_field(node, f, v)))
    window.props.edit_color.connect(lambda node, f, rgb: window._edit(lambda d: d.set_color(node, f, rgb)))
    window.props.edit_value.connect(lambda node, p, c, v: window._edit(lambda d: d.set_property_value(node, p, c, v)))
    window.props.remove_object.connect(lambda node, f, o: window._edit(lambda d: d.remove_object(node, f, o)))
    window.palette_panel.add.connect(window._add_from_palette)
    window.graph.picked.connect(window._binding_picked)
    window.graph.change.connect(lambda b, op: window._edit(lambda d: d.set_operator(b, op)))
    window.graph.remove.connect(lambda b: window._edit(lambda d: d.remove_binding(b)))
    window.vm.name_id.connect(window._name_id)
    window.vm.bind.connect(window._bind_read)
    window.vm.add_readout.connect(window._add_readout)
    window.vm.load_scripts.connect(window._load_scripts)
    window.sample_panel.changed.connect(window._sample_changed)
    window.sample_panel.reset.connect(lambda key: window._sample_changed(key, None))
    window.props.pick_reference.connect(window._pick_reference)
    window.dump_panel.changed.connect(window._redraw)  # the held dump's texts and rectangles
    window.dump_panel.cleared.connect(
        lambda: (setattr(window.preview, "overlay", []), window.preview.update(), window._redraw())
    )
    window.dump_panel.picked.connect(window._picked_in_preview)
    window.preview.dropped.connect(lambda what, which, x, y: window._add_from_palette(what, which, (x, y)))
    window.states.animation.connect(window._animation)
    window.states.design.connect(window._design)
    window.sequence.add_key.connect(lambda trk, t, v: window._edit(lambda d: d.add_key(trk, t, v)))
    window.sequence.set_key.connect(lambda trk, k, t, v, i: window._edit(lambda d: d.set_key(trk, k, t, v, i)))
    window.sequence.remove_key.connect(lambda trk, k: window._edit(lambda d: d.remove_key(trk, k)))
    window.sequence.edit_tween.connect(
        lambda node, p, c, v: window._edit(lambda d: d.set_property_value(node, p, c, v))
    )


def menus(window) -> None:
    """The menu bar (File, Edit, View, Window), each entry a method of the window."""
    file = window.menuBar().addMenu(tr("&File"))
    games = file.addMenu(tr("Open &game"))
    for key, name in GAMES.items():
        games.addAction(name, lambda k=key: window.open_game(k))
    old = file.addMenu(tr("Open the &old interface of"))
    for key, name in OLD_GAMES.items():
        old.addAction(name, lambda k=key: window.open_old(k))
    file.addAction(tr("Open a &folder of loose files..."), window._open_folder)
    file.addAction(tr("&New widget file..."), window.new_widget_file, QKeySequence("Ctrl+N"))
    file.addSeparator()
    file.addAction(tr("&Save into the project"), window.save, QKeySequence.StandardKey.Save)
    file.addAction(tr("&Revert the file to the game's"), window.revert)
    file.addAction(tr("&Check the project's references"), window.check_references)
    file.addAction(tr("Save the &preview as a picture..."), window._save_picture, QKeySequence("Ctrl+Shift+S"))
    file.addAction(tr("Compare with a game &dump..."), lambda: dumpview.compare_dump(window))
    file.addAction(tr("Take the live values from the running game"), lambda: dumpview.live_dump(window))
    file.addAction(tr("Save the held dump's &label table (CSV)..."), lambda: dumpview.save_label_table(window))
    edit, window.undo_action, window.redo_action = widgets.edit_menu(window, window.undo, window.redo)
    edit.addSeparator()
    edit.addAction(tr("&Duplicate the element"), window.duplicate, QKeySequence("Ctrl+D"))
    edit.addAction(tr("&Remove the element"), window.remove, QKeySequence.StandardKey.Delete)
    edit.addAction(tr("New &binding..."), window.new_binding, QKeySequence("Ctrl+B"))
    edit.addAction(tr("New custom &property..."), window.new_property)
    view = window.menuBar().addMenu(tr("&View"))
    view.addAction(
        tr("&Fit the preview"),
        lambda: (setattr(window.preview, "fit", True), window.preview.fit_view()),
        QKeySequence("F"),
    )
    window.all_bindings = view.addAction(tr("Show &all bindings (not only the selected element's)"), window._show_graph)
    window.all_bindings.setCheckable(True)
    window.fill_action = view.addAction(tr("Fill unbound data with &placeholders"), window._fill_toggled)
    window.fill_action.setCheckable(True)
    window.fill_action.setChecked(bool(window.settings.apps.get(APP, {}).get("placeholders", True)))
    window.live_action = view.addAction(
        tr("Show the held game dump's &texts in place of sample values"), window._redraw
    )
    window.live_action.setCheckable(True)
    window.live_action.setChecked(bool(window.settings.apps.get(APP, {}).get("live_texts", True)))
    window.live_action.toggled.connect(
        lambda on: window.settings.apps.setdefault(APP, {}).__setitem__("live_texts", on)
    )
    window.hidden_action = view.addAction(
        tr("Show layers hidden in the &editor (designer helpers)"), window._hidden_toggled
    )
    window.hidden_action.setCheckable(True)
    window.play_action = view.addAction(tr("Play the &effects over time (a scroll, a blink)"), window._play_toggled)
    window.play_action.setCheckable(True)
    window.play_action.setChecked(bool(window.settings.apps.get(APP, {}).get("play_effects", True)))
    window.preview.set_play_effects(window.play_action.isChecked())
    view.addSeparator()
    world = window.world_menu
    window.world_action = view.addAction(
        tr("Show the &3D scene behind the UI (background blur, world darken, island shading)"), world.toggled
    )
    window.world_action.setCheckable(True)
    window.world_action.setChecked(bool(window.settings.apps.get(APP, {}).get("world_scene", False)))
    view.addAction(tr("Pick a picture for the scene behind the UI..."), world.pick)
    view.addAction(tr("Draw the scene behind the UI from a model of the game..."), world.pick_model)
    view.addAction(tr("Draw the scene behind the UI from an island of the game..."), world.pick_island)
    view.addAction(tr("Use the stand-in scene behind the UI"), world.stand_in)
    world.apply()
    windows = window.menuBar().addMenu(tr("&Window"))
    for dock in window.docks.values():
        windows.addAction(dock.toggleViewAction())
