"""The island editor's ribbon, after Anno 1800's internal editor: tabs, each with labelled groups of large
buttons. ``TABS`` says what is where and when it is enabled (plain data, no Qt); ``CRibbon`` draws it and
``make_actions`` makes the actions the buttons and the menus share. Plan: ``docs/island-editor.md`` ("Layout").
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...infra.i18n import N_, tr
from ...suite import features, widgets
from ...suite.icons import icon
from ..cellbrush import AREA_KINDS
from ..islandgrids import ZONES
from ..tintbrush import PRESETS
from .desktopview import BRUSHES, TOOLS

# What a ribbon item needs before it is enabled (``available``): the island's state, or a tool its mode offers.
NEEDS = (
    "",  # always
    "island",  # an island is open
    "editable",  # ... and not view only
    "undo",
    "redo",
    "buildable",  # its edits can be built into a mod
    "selection",  # an object is selected (and editable)
    "river_point",  # a river point is selected (water tool)
    "slots",  # the slot check applies (an island built for 117)
    "brush",  # a brush tool is offered
    "kind117",  # a vanilla 117 island (how it is saved)
    *TOOLS,
)
MODES = {
    "none": N_("islands.no_island"),
    "view": N_("islands.view_only"),
    "object": N_("islands.object_mode"),
    "terrain": N_("islands.terrain_mode"),
    "path": N_("islands.path_mode"),
}


@dataclass(frozen=True)
class SItem:
    """One ribbon entry: a button, a toggle, a number field, a combo box or a text field."""

    id: str
    label: str
    icon: str = ""
    kind: str = "button"  # button | toggle | spin | combo | text
    requirement: str = ""
    tip: str = ""
    is_small: bool = False  # a small button stacked with its neighbours
    min_value: float = 0
    max_value: float = 0
    step: float = 1
    value: float = 0
    decimals: int = 0
    choices: tuple[str, ...] = ()


def _button(id, label, icon, requirement="", tip="", small=False):
    return SItem(id, label, icon, "button", requirement, tip, small)


def _toggle(id, label, icon, requirement="", tip=""):
    return SItem(id, label, icon, "toggle", requirement, tip)


def _spin(id, label, span, value, requirement, tip="", decimals=0):
    """A number field over ``span`` (lowest, highest, step)."""
    return SItem(id, label, "", "spin", requirement, tip, False, *span, value, decimals)


_RADIUS = _spin("radius", N_("islands.radius"), (1, 24, 1), 6, "brush", N_("islands.brushs_radius_tiles"))


TABS = (
    (
        N_("islands.home"),
        (
            (N_("common.edit"), (_button("undo", N_("islands.undo"), "undo", "undo", N_("islands.undo_last_change_ctrl_plus"), True),
                      _button("redo", N_("islands.redo"), "redo", "redo", N_("islands.redo_ctrl_plus_y"), True))),
            (N_("common.file"), (_button("save", N_("common.save"), "save", "editable", N_("islands.write_edit_file_ctrl_plus")),
                      _button("build", N_("islands.build_install"), "build", "buildable", N_("islands.build_open_island_saved_edits")),
                      SItem("target", N_("islands.saved"), "", "combo", "kind117", N_("islands.how_117_island_saved"),
                           choices=(N_("islands.replaces_original"), N_("islands.new_island_lower"))))),
            (N_("islands.selection"), (_toggle("tool_select", N_("islands.select"), "select", "select", N_("islands.select_move_objects_s")),
                           _button("deselect", N_("islands.de_select"), "deselect", "island", N_("islands.select_nothing_esc")),
                           _button("focus", N_("islands.focus"), "focus", "selection", N_("islands.fly_camera_selection_f")))),
            (N_("islands.camera"), (SItem("goto", N_("islands.go_x_z_distance_turn"), "", "text", "island",
                             N_("islands.x_z_tiles_how_far")),)),
            (N_("islands.layers"), (_toggle("layer_objects", N_("islands.layer_objects"), "models", "island", N_("islands.tip.layer_objects")),
                         _toggle("layer_markers", N_("islands.layer_markers"), "focus", "island", N_("islands.tip.layer_markers")),
                         _toggle("layer_props", N_("islands.layer_effects"), "noise", "island", N_("islands.tip.layer_effects")),
                         _toggle("layer_grass", N_("islands.layer_grass"), "plant", "island", N_("islands.tip.layer_grass")),
                         _toggle("layer_forest", N_("islands.layer_forest"), "plant", "island", N_("islands.tip.layer_forest")),
                         _toggle("layer_water", N_("islands.layer_water"), "water", "island", N_("islands.tip.layer_water")))),
        ),
    ),
    (
        N_("common.object"),
        (
            (N_("islands.place"), (_toggle("tool_place", N_("islands.place"), "place", "place", N_("islands.place_picked_model_ground_turn")),
                       _button("models", N_("islands.object_groups"), "models", "island", N_("islands.model_library")))),
            (N_("islands.transform"), (_toggle("gizmo_move", N_("islands.move"), "move", "editable", N_("islands.move_along_axis_over_ground")),
                           _toggle("gizmo_turn", N_("islands.turn"), "turn", "editable", N_("islands.turn_about_vertical_e")),
                           _toggle("gizmo_tilt", N_("islands.tilt"), "tilt", "editable", N_("islands.tilt_about_models_own_axes")),
                           _toggle("gizmo_scale", N_("islands.scale"), "scale", "editable", N_("islands.scale_r_prop_per_axis")))),
            (N_("common.edit"), (_button("delete", N_("common.delete"), "delete", "selection", N_("islands.delete_selection_del")),
                      _button("duplicate", N_("islands.duplicate"), "duplicate", "selection", N_("islands.duplicate_selection_ctrl_plus_d")))),
            (N_("islands.object_spray"), (_toggle("tool_plant", N_("islands.plant_forest"), "plant", "forest", N_("islands.plant_forest_tiles_t")),
                              _toggle("tool_clear", N_("islands.clear_forest"), "clear", "forest", N_("islands.clear_forest_tiles_t")),
                              _toggle("tool_spray", N_("islands.spray_model"), "spray", "spray",
                                 N_("islands.spray_picked_model_over_ground")),
                              _RADIUS,
                              _spin("spacing", N_("islands.spacing"), (0.5, 12, 0.5), 2.0, "spray",
                              N_("islands.tiles_between_sprayed_models_least"), decimals=1))),
            (N_("islands.visibility"), (_toggle("foliage", N_("islands.hide_foliage"), "hide", "island", N_("islands.trees_bushes_grass_out_view")),
                            _button("show_hidden", N_("islands.show_hidden"), "eye", "island", N_("islands.show_everything_hidden_again")))),
        ),
    ),
    (
        N_("islands.terrain"),
        (
            (N_("islands.height"), (_toggle("tool_raise", N_("islands.raise"), "raise", "height", N_("islands.height_brush_raise_h")),
                         _toggle("tool_lower", N_("islands.lower"), "lower", "height", N_("islands.height_brush_lower_h")),
                         _toggle("tool_flatten", N_("islands.flatten"), "flatten", "height", N_("islands.height_brush_flatten_where_stroke")),
                         _toggle("tool_smooth", N_("islands.smooth"), "smooth", "height", N_("islands.height_brush_smooth_h")))),
            (N_("islands.relief"), (_toggle("tool_noise", N_("islands.noise"), "noise", "height", N_("islands.height_brush_rough_up_ground")),
                        _toggle("tool_terrace", N_("islands.terrace"), "terrace", "height", N_("islands.height_brush_pull_ground_onto")),
                        _toggle("tool_ramp", N_("islands.ramp"), "ramp", "height", N_("islands.height_brush_drag_one_point")),
                        _toggle("tool_despike", N_("islands.spike_removal"), "despike", "height",
                           N_("islands.height_brush_level_single_corners")))),
            (N_("islands.ground"), (_toggle("tool_paint", N_("islands.paint"), "paint", "paint", N_("islands.paint_grounds_materials_p")),
                        _button("materials", N_("islands.material_gallery"), "materials", "island", N_("islands.ground_materials_game_they_come")))),
            (N_("islands.tint"), (_toggle("tool_tint", N_("islands.tint"), "paint", "active", N_("islands.tint_ground_build_writes_pages")),
                      SItem("tint", N_("islands.tint"), "", "combo", "active", N_("islands.tint_brush_lays"),
                           choices=tuple(name for name, _factor in PRESETS)))),
            (N_("islands.surf"), (_toggle("tool_surf_on", N_("islands.waves"), "surf_on", "surf", N_("islands.sea_map_set_coast_waves")),
                      _toggle("tool_surf_off", N_("islands.calm"), "surf_off", "surf", N_("islands.sea_map_no_coast_waves")))),
            (N_("islands.brush"), (_RADIUS,
                       _spin("strength", N_("islands.strength"), (0.1, 1.0, 0.05), 0.5, "brush", N_("islands.brushs_strength"), decimals=2))),
        ),
    ),
    (
        N_("islands.grid"),
        (
            (N_("islands.active_map"), (_toggle("tool_cell_on", N_("islands.draw_cells"), "cell_on", "active", N_("islands.active_map_draw_8_tile")),
                            _toggle("tool_cell_off", N_("islands.erase_cells"), "cell_off", "active", N_("islands.active_map_erase_cells_g")),
                            _RADIUS)),
            (N_("islands.ford"), (_toggle("tool_ford_on", N_("islands.lay_ford"), "ford_on", "active", N_("islands.ford_lay_fords_river_tiles")),
                        _toggle("tool_ford_off", N_("islands.erase_ford"), "ford_off", "active", N_("islands.ford_erase_fords_g")))),
            (N_("islands.construction_area"), (_toggle("tool_area_on", N_("islands.add_area"), "cell_on", "active",
                                                       N_("islands.construction_area_add_tiles_g")),
                             _toggle("tool_area_off", N_("islands.remove_area"), "cell_off", "active", N_("islands.construction_area_remove_tiles_g")),
                             _toggle("tool_areakind", N_("islands.paint_area_id"), "zone", "active", N_("islands.construction_area_paint_chosen_id")),
                             SItem("areakind", N_("islands.area_id"), "", "combo", "active", N_("islands.id_islands_other_area_brush"),
                                  choices=tuple(name for _id, name in AREA_KINDS)))),
            (N_("islands.herd_area"), (_toggle("tool_herd_on", N_("islands.add_herds"), "cell_on", "active", N_("islands.herd_area_add_tiles_herds")),
                           _toggle("tool_herd_off", N_("islands.remove_herds"), "cell_off", "active", N_("islands.herd_area_remove_tiles_herds")))),
            (N_("islands.environment"), (_toggle("tool_zone", N_("islands.paint_zone"), "zone", "active", N_("islands.environment_paint_chosen_zone_4")),
                             SItem("zone", N_("islands.zone"), "", "combo", "active", N_("islands.environment_zone_brush_paints"),
                                  choices=tuple(name for _number, name in ZONES)))),
            (N_("common.checks"), (_button("check_rules", N_("islands.check_rules"), "rules", "island", N_("islands.island_against_games_rules")),
                        _button("check_slots", N_("islands.check_slots"), "slots", "slots",
                           N_("islands.river_slots_both_banks_build")),
                        _button("check_falls", N_("islands.check_falls"), "falls", "island", N_("islands.waterfalls_run_through_rock")))),
        ),
    ),
    (
        N_("islands.path"),
        (
            (N_("islands.rivers"), (_toggle("tool_water", N_("islands.rivers_lakes"), "water", "water", N_("islands.rivers_points_lakes_levels_l")),
                        _button("point_delete", N_("islands.delete_point"), "point_delete", "river_point", N_("islands.remove_river_point_del")),
                        _button("point_insert", N_("islands.insert_point"), "point_insert", "river_point", N_("islands.insert_point_after_ctrl_plus")))),
            (N_("islands.path_blockers"), (_toggle("tool_block", N_("islands.block_path"), "block", "blocker", N_("islands.paint_cells_units_cannot_walk")),
                               _toggle("tool_free", N_("islands.free_path"), "free", "blocker", N_("islands.clear_blocked_cells_b")),
                               _RADIUS)),
            (N_("islands.roads"), (_button("road_pieces", N_("islands.road_pieces"), "road", "", N_("islands.road_piece_editor")),)),
        ),
    ),
)  # fmt: skip


# Items that belong to some games only; an application restricted to other games leaves them out (``tabs_for``).
ITEM_GAMES = {
    "road_pieces": ("anno117", "anno2070"),
    "check_slots": ("anno117",),
    "check_falls": ("anno117", "anno2070"),
    "point_delete": ("anno117", "anno1800"),
    "point_insert": ("anno117", "anno1800"),
    "target": ("anno117",),
}


WITH_TOOL = {"materials": "paint", "models": "place"}  # panels that only make sense with a tool


# Items that rest on an extension (``features``: the point and the name registered there); an edition without it
# leaves them out.
ITEM_FEATURES = {
    "road_pieces": (features.TOOLS, "road_pieces"),
    "check_slots": (features.CHECKS, "slots"),
    "check_falls": (features.CHECKS, "falls"),
}


def _has_feature(item_id: str) -> bool:
    """Whether this edition has the extension the item rests on."""
    return features.extension(*ITEM_FEATURES[item_id]) is not None


def tabs_for(games: tuple[str, ...] | None, tools: set[str]) -> tuple:
    """``TABS`` for an application restricted to ``games`` (None: all): without the items of other games and the
    tools none of its islands offers, without the items whose module this edition lacks, and without the groups
    and tabs that leave empty."""
    if games is None and all(_has_feature(item_id) for item_id in ITEM_FEATURES):
        return TABS

    def keep(item: SItem) -> bool:
        if item.id in ITEM_FEATURES and not _has_feature(item.id):
            return False
        if games is None:
            return True
        needed = set(BRUSHES) if item.requirement == "brush" else {WITH_TOOL.get(item.id, item.requirement)}
        if (item.requirement == "brush" or item.id in WITH_TOOL or item.requirement in TOOLS) and not needed & tools:
            return False
        return item.id not in ITEM_GAMES or any(game in games for game in ITEM_GAMES[item.id])

    out = []
    for tab, groups in TABS:
        kept = [(name, tuple(item for item in group if keep(item))) for name, group in groups]
        kept = [(name, group) for name, group in kept if group]
        if kept:
            out.append((tab, tuple(kept)))
    return tuple(out)


def items(tabs=TABS):
    """Every item of ``tabs``, tab by tab and group by group."""
    for _tab, groups in tabs:
        for _group, group_items in groups:
            yield from group_items


def available(requirement: str, state: dict) -> bool:
    """Whether an item with that ``need`` is enabled in the editor's ``state`` (``CIslandDoc.state``)."""
    if not requirement:
        return True
    if not state.get("island"):
        return False
    if requirement == "island":
        return True
    if requirement in TOOLS:
        return requirement in state.get("tools", ())
    return bool(state.get(requirement))


def tool_action(tool: str, brush: dict) -> str | None:
    """The tool button that stands for the view's tool (the height brush by its mode, the forest by plant or clear)."""
    if tool == "height":
        return f"tool_{brush['mode']}"
    if tool == "forest":
        return "tool_plant" if brush["plant"] else "tool_clear"
    if tool == "blocker":
        return "tool_block" if brush["plant"] else "tool_free"
    if tool == "active":
        if brush.get("grid") in ("environment", "areakind", "tint"):
            return {"environment": "tool_zone", "areakind": "tool_areakind", "tint": "tool_tint"}[brush["grid"]]
        kind = brush.get("grid") if brush.get("grid") in ("ford", "area", "herd") else "cell"
        return f"tool_{kind}_{'on' if brush['plant'] else 'off'}"
    if tool == "surf":
        return "tool_surf_on" if brush["plant"] else "tool_surf_off"
    return f"tool_{tool}"


def mode_of(tool: str | None, is_read_only: bool = False) -> str:
    """The editing mode a tool belongs to (a key of ``MODES`` and ``suite.theme.MODE_COLORS``)."""
    if tool is None:
        return "none"
    if is_read_only:
        return "view"
    return {
        "height": "terrain",
        "paint": "terrain",
        "water": "path",
        "blocker": "path",
        "active": "path",
        "surf": "terrain",
    }.get(tool, "object")


def one_line(label: str) -> str:
    """A two-line button label on one line (a hyphen that ended the first line joins the words)."""
    return label.replace("-\n", "").replace("\n", " ")


def make_actions(parent: QWidget, tabs=TABS) -> dict[str, QAction]:
    """One action per button of the ribbon; the tool buttons and the gizmo buttons are each an exclusive group."""
    out: dict[str, QAction] = {}
    groups = {
        "tool_": QActionGroup(parent),
        "gizmo_": QActionGroup(parent),
    }
    groups["tool_"].setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
    for item in items(tabs):
        if item.kind not in ("button", "toggle") or item.id in out:
            continue
        action = QAction(icon(item.icon), tr(item.label), parent)  # a two-line label: the large button breaks it
        action.setToolTip(tr(item.tip) if item.tip else one_line(tr(item.label)))
        action.setCheckable(item.kind == "toggle")
        out[item.id] = action
        for prefix, group in groups.items():
            if item.id.startswith(prefix):
                group.addAction(action)
    return out


class CRibbon(QWidget):
    """The tab bar and the pages of groups; ``fieldChanged(id, value)`` for the spin boxes, combos and text."""

    fieldChanged = Signal(str, object)

    def __init__(self, actions: dict[str, QAction], tabs=TABS, parent=None) -> None:
        super().__init__(parent)
        self.m_actions = actions
        self.m_fields: dict[str, list[QWidget]] = {}
        self._m_needs: list[tuple[QWidget, str]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.m_bar = QTabBar(drawBase=False, expanding=False)
        self.m_pages = QStackedWidget()
        self._m_names = [name for name, _groups in tabs]  # the data names: ``show_tab`` and the smoke report use them
        self._m_tabs = tabs
        for name, groups in tabs:
            self.m_bar.addTab(tr(name))
            self.m_pages.addWidget(self._page(groups))
        self.m_bar.currentChanged.connect(self.m_pages.setCurrentIndex)
        self.m_pages.setFixedHeight(
            max(self.m_pages.widget(k).sizeHint().height() for k in range(self.m_pages.count()))
        )
        layout.addWidget(self.m_bar)
        layout.addWidget(self.m_pages)

    def set_access_keys(self, avoid: str = "", shown_together: Iterable[list[QAction]] = ()) -> None:
        """An access key for each tab and each button, none of the letters ``avoid`` (the menu bar's). A button's key
        differs from every other action's of its page and of each list in ``shown_together`` (a menu's entries: the
        buttons' actions are the menus' too)."""
        titles = widgets.access_keys([tr(name) for name in self._m_names], avoid)
        for k, title in enumerate(titles):
            self.m_bar.setTabText(k, title)
        away = avoid + widgets.access_letters(titles)
        pages = []
        for _name, groups in self._m_tabs:
            ids = dict.fromkeys(
                item.id for _group, group_items in groups for item in group_items if item.kind in ("button", "toggle")
            )
            pages.append([self.m_actions[i] for i in ids if i in self.m_actions])
        widgets.assign_access_keys([action for page in pages for action in page], [*pages, *shown_together], away)

    def tab_names(self) -> list[str]:
        """The tab names in the order of the tab bar."""
        return list(self._m_names)

    def show_tab(self, name: str) -> None:
        """Bring the tab of that name to the front, if the ribbon has it."""
        if name in self.tab_names():
            self.m_bar.setCurrentIndex(self.tab_names().index(name))

    def apply_state(self, state: dict) -> None:
        """Enable each item that needs something according to the editor's ``state``."""
        for widget, requirement in self._m_needs:
            widget.setEnabled(available(requirement, state))

    def set_field(self, id: str, value) -> None:
        """Set every widget of that field (a brush's radius sits on two tabs) without sending a change."""
        for widget in self.m_fields.get(id, ()):
            widget.blockSignals(True)
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(int(value))
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))
            else:
                widget.setValue(value)
            widget.blockSignals(False)

    # building
    def _page(self, groups) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(0)
        for name, group_items in groups:
            row.addWidget(self._group(name, group_items))
            separator = QFrame(objectName="groupSeparator")
            separator.setFixedWidth(1)
            row.addWidget(separator)
        row.addStretch(1)
        return page

    def _group(self, name: str, group_items) -> QWidget:
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(6, 2, 6, 1)
        outer.setSpacing(1)
        row = QHBoxLayout()
        row.setSpacing(2)
        column = None
        for item in group_items:
            if item.is_small or item.kind in ("spin", "combo", "text"):
                if column is None:
                    column = QVBoxLayout()
                    column.setSpacing(2)
                    column.setAlignment(Qt.AlignmentFlag.AlignTop)
                    row.addLayout(column)
                column.addWidget(self._small(item) if item.is_small else self._field(item))
            else:
                column = None
                row.addWidget(self._big(item), 0, Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)
        outer.addLayout(row, 1)
        outer.addWidget(QLabel(tr(name), objectName="groupLabel", alignment=Qt.AlignmentFlag.AlignHCenter))
        return box

    def _button(self, item: SItem) -> QToolButton:
        button = QToolButton()
        button.setDefaultAction(self.m_actions[item.id])
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # reached by Tab; a click leaves the keyboard with the view
        return button

    def _big(self, item: SItem) -> QToolButton:
        button = self._button(item)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIconSize(QSize(32, 32))
        button.setMinimumWidth(56)
        return button

    def _small(self, item: SItem) -> QToolButton:
        button = self._button(item)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(16, 16))
        button.setMinimumWidth(64)
        return button

    def _field(self, item: SItem) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        if item.kind == "spin":
            layout.addWidget(QLabel(tr(item.label)))
            widget = QDoubleSpinBox(decimals=item.decimals) if item.decimals else QSpinBox()
            widget.setRange(item.min_value, item.max_value)
            widget.setSingleStep(item.step)
            widget.setValue(item.value)
            widget.setKeyboardTracking(False)
            widget.setFixedWidth(72)
            widget.valueChanged.connect(lambda value, id=item.id: self.fieldChanged.emit(id, value))
        elif item.kind == "combo":
            layout.addWidget(QLabel(tr(item.label)))
            widget = QComboBox()
            widget.addItems([tr(choice) for choice in item.choices])
            widget.currentIndexChanged.connect(lambda k, id=item.id: self.fieldChanged.emit(id, k))
        else:
            widget = QLineEdit(placeholderText=tr(item.label))
            widget.setMinimumWidth(190)
            widget.returnPressed.connect(lambda widget=widget, id=item.id: self.fieldChanged.emit(id, widget.text()))
        widget.setToolTip(tr(item.tip) if item.tip else "")
        layout.addWidget(widget)
        self.m_fields.setdefault(item.id, []).append(widget)
        self._m_needs.append((row, item.requirement))
        return row
