"""The UI editor window's frame: ``build`` lays out its widgets and docks, ``wire`` connects their signals to the
window's slots, ``menus`` fills the menu bar, ``fill_files`` the list of the game's UI files. Split off ``uiwindow`` so
the window class holds what it does, not how it is put together; the first three take the ``UiWindow`` and set the
widgets they name on it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QLineEdit,
    QListWidget,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..suite import features, widgets
from . import gamedataview, livevalues, playmode, previewpanel, tweens, uifiles
from .graphview import BindingGraph
from .panels import PalettePanel, PropertyPanel, SamplePanel, StatesPanel, ViewModelPanel
from .preview import Preview
from .timeline import SequenceEditor

APP = "ui"  # the key of its remembered state in the settings
GAMES = {"anno117": "Anno 117", "anno1800": "Anno 1800", "anno1404": "Anno 1404", "anno1701": "Anno 1701"}
ROLE = Qt.ItemDataRole.UserRole


def fill_files(file_list: QTreeWidget, files: uifiles.UiFiles, text: str, saved: set[str]) -> None:
    """The (cleared) file list filled: scenes, widget files and - only when ``text`` filters - the other files, each
    group with its count; a file the project holds a copy of (``saved``, by path) is marked."""
    words = text.lower().split()
    groups = {"SC": QTreeWidgetItem(file_list, [tr("ui.scenes"), ""]), "WI": QTreeWidgetItem(file_list, [tr("ui.widget_files"), ""]),
              "": QTreeWidgetItem(file_list, [tr("ui.other_files_properties_only"), ""])}  # fmt: skip
    for entry in sorted(files.entries.values(), key=lambda e: (files.title(e.key).lower(), e.key)):
        title = files.title(entry.key)
        if words and not all(w in f"{title} {entry.key}".lower() for w in words):
            continue
        group = groups.get(entry.kind, groups[""])
        if group is groups[""] and not words:
            continue
        mark = " *" if entry.path in saved else ""
        kind = uifiles.KIND_NAMES.get(entry.kind)
        it = QTreeWidgetItem(group, [title + mark, tr(kind) if kind else entry.kind])
        it.setToolTip(0, entry.path)
        it.setData(0, ROLE, entry.key)
    for g in groups.values():
        g.setText(0, f"{g.text(0)} ({g.childCount()})")
    groups["SC"].setExpanded(True)


def build(window) -> None:
    """The window's widgets: the preview in the centre, the panels as docks around it; then ``wire``."""
    window.preview = Preview()
    window.centre = QStackedWidget()  # the preview, or (a game's old interface) its page
    window.centre.addWidget(window.preview)
    window.setCentralWidget(window.centre)
    files = QWidget()
    window.file_filter = QLineEdit(placeholderText=tr("ui.find_file_scene_name_guid"))
    widgets.find_key(window, window.file_filter)
    window.file_list = QTreeWidget()
    window.file_list.setHeaderLabels([tr("common.file"), tr("ui.kind")])
    lay = QVBoxLayout(files)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(window.file_filter)
    lay.addWidget(window.file_list)
    window.element_tree = QTreeWidget()
    window.element_tree.setHeaderLabels([tr("ui.element"), tr("ui.kind")])
    window.props, window.palette_panel = PropertyPanel(), PalettePanel()
    window.vm, window.sample_panel, window.states = ViewModelPanel(), SamplePanel(), StatesPanel()
    window.graph = BindingGraph()
    window.checks = QListWidget()
    window.compare = features.extension(features.UI_COMPARE, "game")  # None in an edition without it
    window.compare_panel = window.compare.make_panel() if window.compare else None
    window.sequence = SequenceEditor()
    previewpanel.install(window)  # what the preview assumes of the player's game
    window.tween_list: list[tweens.Tween] = []
    window.docks: dict[str, QDockWidget] = {}
    left, right, bottom = (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea,
                           Qt.DockWidgetArea.BottomDockWidgetArea)  # fmt: skip
    for key, name, widget, area in (("Files", N_("common.files"), files, left), ("Elements", N_("ui.elements"), window.element_tree, left),
                               ("Palette", N_("ui.palette"), window.palette_panel, left), ("Properties", N_("common.properties"), window.props, right),
                               ("View models", N_("ui.view_models"), window.vm, right), ("Sample values", N_("ui.sample_values"), window.sample_panel, right),
                               ("Preview settings", previewpanel.TITLE, window.preview_panel, right),
                               ("Bindings", N_("ui.bindings"), window.graph, bottom), ("States and events", N_("ui.states_events"), window.states, bottom),
                               ("Sequence", N_("common.sequence"), window.sequence, None), ("Checks", N_("common.checks"), window.checks, None),
                               *((("Comparison", window.compare.title, window.compare_panel, None),) if window.compare else ())):  # fmt: skip
        dock = QDockWidget(tr(name), window, objectName=f"ui{key.replace(' ', '')}")
        dock.setWidget(widget)
        if area is not None:  # the others join a tab group below
            window.addDockWidget(area, dock)
        window.docks[key] = dock
    window.tabifyDockWidget(window.docks["Elements"], window.docks["Palette"])
    window.docks["Elements"].raise_()
    window.tabifyDockWidget(window.docks["Properties"], window.docks["View models"])
    window.tabifyDockWidget(window.docks["View models"], window.docks["Sample values"])
    window.tabifyDockWidget(window.docks["Sample values"], window.docks["Preview settings"])
    window.docks["Properties"].raise_()
    window.splitDockWidget(window.docks["Bindings"], window.docks["States and events"], Qt.Orientation.Horizontal)
    window.tabifyDockWidget(
        window.docks["Bindings"], window.docks["Sequence"]
    )  # one tab each: a third leaves a stray bar
    window.tabifyDockWidget(window.docks["States and events"], window.docks["Checks"])
    if "Comparison" in window.docks:
        window.tabifyDockWidget(window.docks["Checks"], window.docks["Comparison"])
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
    if window.compare_panel is not None:
        window.compare_panel.changed.connect(window._redraw)  # its texts and rectangles
        window.compare_panel.cleared.connect(
            lambda: (setattr(window.preview, "overlay", []), window.preview.update(), window._redraw())
        )
        window.compare_panel.picked.connect(window._picked_in_preview)
    window.preview.dropped.connect(lambda what, which, x, y: window._add_from_palette(what, which, (x, y)))
    window.states.animation.connect(window._animation)
    window.states.stage = window.preview
    window.states.design.connect(window._design)
    window.sequence.add_key.connect(lambda trk, t, v: window._edit(lambda d: d.add_key(trk, t, v)))
    window.sequence.set_key.connect(lambda trk, k, t, v, i: window._edit(lambda d: d.set_key(trk, k, t, v, i)))
    window.sequence.remove_key.connect(lambda trk, k: window._edit(lambda d: d.remove_key(trk, k)))
    window.sequence.edit_tween.connect(
        lambda node, p, c, v: window._edit(lambda d: d.set_property_value(node, p, c, v))
    )


def _open_mirror(window) -> None:
    from . import mirror  # the game mirror's window, made when it is first asked for

    mirror.open_mirror(window)


def menus(window) -> None:
    """The menu bar (File, Edit, View, Window), each entry a method of the window."""
    file = window.menuBar().addMenu(tr("common.menu.file"))
    games = file.addMenu(tr("common.menu.open_game"))
    for key, name in GAMES.items():
        games.addAction(name, lambda k=key: window.open_game(k))
    file.addAction(tr("common.open_folder_loose_files"), window._open_folder)
    file.addAction(tr("ui.new_widget_file"), window.new_widget_file, QKeySequence("Ctrl+N"))
    file.addSeparator()
    file.addAction(tr("ui.save_project"), window.save, QKeySequence.StandardKey.Save)
    file.addAction(tr("ui.revert_file_games"), window.revert)
    file.addAction(tr("ui.check_projects_references"), window.check_references)
    file.addAction(tr("ui.save_preview_picture"), window._save_picture, QKeySequence("Ctrl+Shift+S"))
    file.addSeparator()
    livevalues.add_file_actions(window, file)
    if window.compare:
        window.compare.add_file_actions(window, file)
    edit, window.undo_action, window.redo_action = widgets.edit_menu(window, window.undo, window.redo)
    edit.addSeparator()
    edit.addAction(tr("ui.duplicate_element"), window.duplicate, QKeySequence("Ctrl+D"))
    edit.addAction(tr("ui.remove_element"), window.remove, QKeySequence.StandardKey.Delete)
    edit.addAction(tr("ui.new_binding"), window.new_binding, QKeySequence("Ctrl+B"))
    edit.addAction(tr("ui.new_custom_property"), window.new_property)
    view = window.menuBar().addMenu(tr("common.menu.view"))
    view.addAction(
        tr("ui.fit_preview"),
        lambda: (setattr(window.preview, "fit", True), window.preview.fit_view()),
        QKeySequence("F"),
    )
    window.all_bindings = view.addAction(tr("ui.show_all_bindings_not_only"), window._show_graph)
    window.all_bindings.setCheckable(True)
    fill = tr("ui.fill_unbound_data_placeholders")
    window.fill_action = widgets.kept_switch(window, view, fill, window._fill_toggled, (APP, "placeholders"))
    window.baseline_action = widgets.kept_switch(
        window, view, tr("ui.baseline_typical_values"), window._baseline_toggled, (APP, "baseline")
    )
    window.game_data_action = gamedataview.add_view_action(window, view, APP)
    window.values_action = livevalues.add_view_action(window, view, APP)
    window.settings_action = view.addAction(
        tr("ui.preview_settings_open"), lambda: window.show_dock("Preview settings")
    )
    window.live_action = window.compare.add_view_action(window, view, APP) if window.compare else None
    window.hidden_action = view.addAction(tr("ui.show_layers_hidden_editor_designer"), window._hidden_toggled)
    window.hidden_action.setCheckable(True)
    shape = view.addMenu(tr("ui.window_shape"))  # the cancel and aspect modes show only on a window of another shape
    shapes = QActionGroup(shape)
    for label, aspect in ((tr("ui.window_shape_reference"), 0.0), ("21:9", 21 / 9), ("32:9", 32 / 9)):
        act = shape.addAction(label, lambda a=aspect: window._window_shape(a))
        act.setCheckable(True)
        act.setChecked(aspect == 0.0)
        shapes.addAction(act)
    play = tr("ui.play_effects_over_time_scroll")
    window.play_action = widgets.kept_switch(window, view, play, window._play_toggled, (APP, "play_effects"))
    window.preview.set_play_effects(window.play_action.isChecked())
    view.addSeparator()
    world = window.world_menu
    behind = tr("ui.show_3d_scene_behind_ui")
    window.world_action = widgets.kept_switch(window, view, behind, world.toggled, (APP, "world_scene"), on=False)
    view.addAction(tr("ui.pick_picture_scene_behind_ui"), world.pick)
    view.addAction(tr("ui.draw_scene_behind_ui_model"), world.pick_model)
    view.addAction(tr("ui.draw_scene_behind_ui_island"), world.pick_island)
    view.addAction(tr("ui.use_stand_scene_behind_ui"), world.stand_in)
    world.apply()
    view.addSeparator()
    window.mirror_action = view.addAction(tr("ui.mirror_open"), lambda: _open_mirror(window))
    playmode.install(window, view)  # the Design / Play switch
    keyed = [window.settings_action, window.mirror_action, window.design_mode_action, window.play_mode_action,
             window.reset_play_action]  # fmt: skip
    widgets.assign_access_keys(keyed, widgets.menu_groups(view))  # a key no other entry has
    windows = window.menuBar().addMenu(tr("common.menu.window"))
    widgets.add_docks(windows, window.docks.values())
