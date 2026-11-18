"""The island editor's ribbon, after Anno 1800's internal editor: tabs, each with labelled groups of large
buttons. ``TABS`` says what is where and when it is enabled (plain data, no Qt); ``Ribbon`` draws it and
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
class Item:
    id: str
    label: str
    icon: str = ""
    kind: str = "button"  # button | toggle | spin | combo | text
    need: str = ""
    tip: str = ""
    small: bool = False  # a small button stacked with its neighbours
    lo: float = 0
    hi: float = 0
    step: float = 1
    value: float = 0
    decimals: int = 0
    choices: tuple[str, ...] = ()


def _b(id, label, icon, need="", tip="", small=False):
    return Item(id, label, icon, "button", need, tip, small)


def _t(id, label, icon, need="", tip=""):
    return Item(id, label, icon, "toggle", need, tip)


def _spin(id, label, span, value, need, tip="", decimals=0):
    """A number field over ``span`` (lowest, highest, step)."""
    return Item(id, label, "", "spin", need, tip, False, *span, value, decimals)


_RADIUS = _spin("radius", N_("islands.radius"), (1, 24, 1), 6, "brush", N_("islands.brushs_radius_tiles"))


TABS = (
    (
        N_("islands.home"),
        (
            (N_("common.edit"), (_b("undo", N_("islands.undo"), "undo", "undo", N_("islands.undo_last_change_ctrl_plus"), True),
                      _b("redo", N_("islands.redo"), "redo", "redo", N_("islands.redo_ctrl_plus_y"), True))),
            (N_("common.file"), (_b("save", N_("common.save"), "save", "editable", N_("islands.write_edit_file_ctrl_plus")),
                      _b("build", N_("islands.build_install"), "build", "buildable", N_("islands.build_open_island_saved_edits")),
                      Item("target", N_("islands.saved"), "", "combo", "kind117", N_("islands.how_117_island_saved"),
                           choices=(N_("islands.replaces_original"), N_("islands.new_island_lower"))))),
            (N_("islands.selection"), (_t("tool_select", N_("islands.select"), "select", "select", N_("islands.select_move_objects_s")),
                           _b("deselect", N_("islands.de_select"), "deselect", "island", N_("islands.select_nothing_esc")),
                           _b("focus", N_("islands.focus"), "focus", "selection", N_("islands.fly_camera_selection_f")))),
            (N_("islands.camera"), (Item("goto", N_("islands.go_x_z_distance_turn"), "", "text", "island",
                             N_("islands.x_z_tiles_how_far")),)),
        ),
    ),
    (
        N_("common.object"),
        (
            (N_("islands.place"), (_t("tool_place", N_("islands.place"), "place", "place", N_("islands.place_picked_model_ground_turn")),
                       _b("models", N_("islands.object_groups"), "models", "island", N_("islands.model_library")))),
            (N_("islands.transform"), (_t("gizmo_move", N_("islands.move"), "move", "editable", N_("islands.move_along_axis_over_ground")),
                           _t("gizmo_turn", N_("islands.turn"), "turn", "editable", N_("islands.turn_about_vertical_e")),
                           _t("gizmo_tilt", N_("islands.tilt"), "tilt", "editable", N_("islands.tilt_about_models_own_axes")),
                           _t("gizmo_scale", N_("islands.scale"), "scale", "editable", N_("islands.scale_r_prop_per_axis")))),
            (N_("common.edit"), (_b("delete", N_("common.delete"), "delete", "selection", N_("islands.delete_selection_del")),
                      _b("duplicate", N_("islands.duplicate"), "duplicate", "selection", N_("islands.duplicate_selection_ctrl_plus_d")))),
            (N_("islands.object_spray"), (_t("tool_plant", N_("islands.plant_forest"), "plant", "forest", N_("islands.plant_forest_tiles_t")),
                              _t("tool_clear", N_("islands.clear_forest"), "clear", "forest", N_("islands.clear_forest_tiles_t")),
                              _t("tool_spray", N_("islands.spray_model"), "spray", "spray",
                                 N_("islands.spray_picked_model_over_ground")),
                              _RADIUS,
                              _spin("spacing", N_("islands.spacing"), (0.5, 12, 0.5), 2.0, "spray",
                              N_("islands.tiles_between_sprayed_models_least"), decimals=1))),
            (N_("islands.visibility"), (_t("foliage", N_("islands.hide_foliage"), "hide", "island", N_("islands.trees_bushes_grass_out_view")),
                            _b("show_hidden", N_("islands.show_hidden"), "eye", "island", N_("islands.show_everything_hidden_again")))),
        ),
    ),
    (
        N_("islands.terrain"),
        (
            (N_("islands.height"), (_t("tool_raise", N_("islands.raise"), "raise", "height", N_("islands.height_brush_raise_h")),
                         _t("tool_lower", N_("islands.lower"), "lower", "height", N_("islands.height_brush_lower_h")),
                         _t("tool_flatten", N_("islands.flatten"), "flatten", "height", N_("islands.height_brush_flatten_where_stroke")),
                         _t("tool_smooth", N_("islands.smooth"), "smooth", "height", N_("islands.height_brush_smooth_h")))),
            (N_("islands.relief"), (_t("tool_noise", N_("islands.noise"), "noise", "height", N_("islands.height_brush_rough_up_ground")),
                        _t("tool_terrace", N_("islands.terrace"), "terrace", "height", N_("islands.height_brush_pull_ground_onto")),
                        _t("tool_ramp", N_("islands.ramp"), "ramp", "height", N_("islands.height_brush_drag_one_point")),
                        _t("tool_despike", N_("islands.spike_removal"), "despike", "height",
                           N_("islands.height_brush_level_single_corners")))),
            (N_("islands.ground"), (_t("tool_paint", N_("islands.paint"), "paint", "paint", N_("islands.paint_grounds_materials_p")),
                        _b("materials", N_("islands.material_gallery"), "materials", "island", N_("islands.ground_materials_game_they_come")))),
            (N_("islands.tint"), (_t("tool_tint", N_("islands.tint"), "paint", "active", N_("islands.tint_ground_build_writes_pages")),
                      Item("tint", N_("islands.tint"), "", "combo", "active", N_("islands.tint_brush_lays"),
                           choices=tuple(name for name, _factor in PRESETS)))),
            (N_("islands.surf"), (_t("tool_surf_on", N_("islands.waves"), "surf_on", "surf", N_("islands.sea_map_set_coast_waves")),
                      _t("tool_surf_off", N_("islands.calm"), "surf_off", "surf", N_("islands.sea_map_no_coast_waves")))),
            (N_("islands.brush"), (_RADIUS,
                       _spin("strength", N_("islands.strength"), (0.1, 1.0, 0.05), 0.5, "brush", N_("islands.brushs_strength"), decimals=2))),
        ),
    ),
    (
        N_("islands.grid"),
        (
            (N_("islands.active_map"), (_t("tool_cell_on", N_("islands.draw_cells"), "cell_on", "active", N_("islands.active_map_draw_8_tile")),
                            _t("tool_cell_off", N_("islands.erase_cells"), "cell_off", "active", N_("islands.active_map_erase_cells_g")),
                            _RADIUS)),
            (N_("islands.ford"), (_t("tool_ford_on", N_("islands.lay_ford"), "ford_on", "active", N_("islands.ford_lay_fords_river_tiles")),
                        _t("tool_ford_off", N_("islands.erase_ford"), "ford_off", "active", N_("islands.ford_erase_fords_g")))),
            (N_("islands.construction_area"), (_t("tool_area_on", N_("islands.add_area"), "cell_on", "active", N_("islands.construction_area_add_tiles_g")),
                             _t("tool_area_off", N_("islands.remove_area"), "cell_off", "active", N_("islands.construction_area_remove_tiles_g")),
                             _t("tool_areakind", N_("islands.paint_area_id"), "zone", "active", N_("islands.construction_area_paint_chosen_id")),
                             Item("areakind", N_("islands.area_id"), "", "combo", "active", N_("islands.id_islands_other_area_brush"),
                                  choices=tuple(name for _id, name in AREA_KINDS)))),
            (N_("islands.herd_area"), (_t("tool_herd_on", N_("islands.add_herds"), "cell_on", "active", N_("islands.herd_area_add_tiles_herds")),
                           _t("tool_herd_off", N_("islands.remove_herds"), "cell_off", "active", N_("islands.herd_area_remove_tiles_herds")))),
            (N_("islands.environment"), (_t("tool_zone", N_("islands.paint_zone"), "zone", "active", N_("islands.environment_paint_chosen_zone_4")),
                             Item("zone", N_("islands.zone"), "", "combo", "active", N_("islands.environment_zone_brush_paints"),
                                  choices=tuple(name for _number, name in ZONES)))),
            (N_("common.checks"), (_b("check_rules", N_("islands.check_rules"), "rules", "island", N_("islands.island_against_games_rules")),
                        _b("check_slots", N_("islands.check_slots"), "slots", "slots",
                           N_("islands.river_slots_both_banks_build")),
                        _b("check_falls", N_("islands.check_falls"), "falls", "island", N_("islands.waterfalls_run_through_rock")))),
        ),
    ),
    (
        N_("islands.path"),
        (
            (N_("islands.rivers"), (_t("tool_water", N_("islands.rivers_lakes"), "water", "water", N_("islands.rivers_points_lakes_levels_l")),
                        _b("point_delete", N_("islands.delete_point"), "point_delete", "river_point", N_("islands.remove_river_point_del")),
                        _b("point_insert", N_("islands.insert_point"), "point_insert", "river_point", N_("islands.insert_point_after_ctrl_plus")))),
            (N_("islands.path_blockers"), (_t("tool_block", N_("islands.block_path"), "block", "blocker", N_("islands.paint_cells_units_cannot_walk")),
                               _t("tool_free", N_("islands.free_path"), "free", "blocker", N_("islands.clear_blocked_cells_b")),
                               _RADIUS)),
            (N_("islands.roads"), (_b("road_pieces", N_("islands.road_pieces"), "road", "", N_("islands.road_piece_editor")),)),
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
    return features.extension(*ITEM_FEATURES[item_id]) is not None


def tabs_for(games: tuple[str, ...] | None, tools: set[str]) -> tuple:
    """``TABS`` for an application restricted to ``games`` (None: all): without the items of other games and the
    tools none of its islands offers, without the items whose module this edition lacks, and without the groups
    and tabs that leave empty."""
    if games is None and all(_has_feature(i) for i in ITEM_FEATURES):
        return TABS

    def keep(it: Item) -> bool:
        if it.id in ITEM_FEATURES and not _has_feature(it.id):
            return False
        if games is None:
            return True
        needed = set(BRUSHES) if it.need == "brush" else {WITH_TOOL.get(it.id, it.need)}
        if (it.need == "brush" or it.id in WITH_TOOL or it.need in TOOLS) and not needed & tools:
            return False
        return it.id not in ITEM_GAMES or any(g in games for g in ITEM_GAMES[it.id])

    out = []
    for tab, groups in TABS:
        kept = [(name, tuple(i for i in group if keep(i))) for name, group in groups]
        kept = [(name, group) for name, group in kept if group]
        if kept:
            out.append((tab, tuple(kept)))
    return tuple(out)


def items(tabs=TABS):
    for _tab, groups in tabs:
        for _group, group_items in groups:
            yield from group_items


def available(need: str, state: dict) -> bool:
    """Whether an item with that ``need`` is enabled in the editor's ``state`` (``IslandDoc.state``)."""
    if not need:
        return True
    if not state.get("island"):
        return False
    if need == "island":
        return True
    if need in TOOLS:
        return need in state.get("tools", ())
    return bool(state.get(need))


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


def mode_of(tool: str | None, readonly: bool = False) -> str:
    """The editing mode a tool belongs to (a key of ``MODES`` and ``suite.theme.MODE_COLORS``)."""
    if tool is None:
        return "none"
    if readonly:
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
    for it in items(tabs):
        if it.kind not in ("button", "toggle") or it.id in out:
            continue
        a = QAction(icon(it.icon), tr(it.label), parent)  # a two-line label: the large button breaks it
        a.setToolTip(tr(it.tip) if it.tip else one_line(tr(it.label)))
        a.setCheckable(it.kind == "toggle")
        out[it.id] = a
        for prefix, group in groups.items():
            if it.id.startswith(prefix):
                group.addAction(a)
    return out


class Ribbon(QWidget):
    """The tab bar and the pages of groups; ``fieldChanged(id, value)`` for the spin boxes, combos and text."""

    fieldChanged = Signal(str, object)

    def __init__(self, actions: dict[str, QAction], tabs=TABS, parent=None) -> None:
        super().__init__(parent)
        self.actions = actions
        self.fields: dict[str, list[QWidget]] = {}
        self._needs: list[tuple[QWidget, str]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.bar = QTabBar(drawBase=False, expanding=False)
        self.pages = QStackedWidget()
        self._names = [name for name, _groups in tabs]  # the data names: ``show_tab`` and the smoke report use them
        self._tabs = tabs
        for name, groups in tabs:
            self.bar.addTab(tr(name))
            self.pages.addWidget(self._page(groups))
        self.bar.currentChanged.connect(self.pages.setCurrentIndex)
        self.pages.setFixedHeight(max(self.pages.widget(k).sizeHint().height() for k in range(self.pages.count())))
        layout.addWidget(self.bar)
        layout.addWidget(self.pages)

    def set_access_keys(self, avoid: str = "", shown_together: Iterable[list[QAction]] = ()) -> None:
        """An access key for each tab and each button, none of the letters ``avoid`` (the menu bar's). A button's key
        differs from every other action's of its page and of each list in ``shown_together`` (a menu's entries: the
        buttons' actions are the menus' too)."""
        titles = widgets.access_keys([tr(name) for name in self._names], avoid)
        for k, title in enumerate(titles):
            self.bar.setTabText(k, title)
        away = avoid + widgets.access_letters(titles)
        pages = []
        for _name, groups in self._tabs:
            ids = dict.fromkeys(it.id for _g, its in groups for it in its if it.kind in ("button", "toggle"))
            pages.append([self.actions[i] for i in ids if i in self.actions])
        widgets.assign_access_keys([a for page in pages for a in page], [*pages, *shown_together], away)

    def tab_names(self) -> list[str]:
        return list(self._names)

    def show_tab(self, name: str) -> None:
        if name in self.tab_names():
            self.bar.setCurrentIndex(self.tab_names().index(name))

    def apply_state(self, state: dict) -> None:
        for widget, need in self._needs:
            widget.setEnabled(available(need, state))

    def set_field(self, id: str, value) -> None:
        """Set every widget of that field (a brush's radius sits on two tabs) without sending a change."""
        for w in self.fields.get(id, ()):
            w.blockSignals(True)
            if isinstance(w, QComboBox):
                w.setCurrentIndex(int(value))
            elif isinstance(w, QLineEdit):
                w.setText(str(value))
            else:
                w.setValue(value)
            w.blockSignals(False)

    # building
    def _page(self, groups) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(0)
        for name, group_items in groups:
            row.addWidget(self._group(name, group_items))
            sep = QFrame(objectName="groupSeparator")
            sep.setFixedWidth(1)
            row.addWidget(sep)
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
        for it in group_items:
            if it.small or it.kind in ("spin", "combo", "text"):
                if column is None:
                    column = QVBoxLayout()
                    column.setSpacing(2)
                    column.setAlignment(Qt.AlignmentFlag.AlignTop)
                    row.addLayout(column)
                column.addWidget(self._small(it) if it.small else self._field(it))
            else:
                column = None
                row.addWidget(self._big(it), 0, Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)
        outer.addLayout(row, 1)
        outer.addWidget(QLabel(tr(name), objectName="groupLabel", alignment=Qt.AlignmentFlag.AlignHCenter))
        return box

    def _button(self, it: Item) -> QToolButton:
        b = QToolButton()
        b.setDefaultAction(self.actions[it.id])
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # reached by Tab; a click leaves the keyboard with the view
        return b

    def _big(self, it: Item) -> QToolButton:
        b = self._button(it)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        b.setIconSize(QSize(32, 32))
        b.setMinimumWidth(56)
        return b

    def _small(self, it: Item) -> QToolButton:
        b = self._button(it)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setIconSize(QSize(16, 16))
        b.setMinimumWidth(64)
        return b

    def _field(self, it: Item) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        if it.kind == "spin":
            h.addWidget(QLabel(tr(it.label)))
            w = QDoubleSpinBox(decimals=it.decimals) if it.decimals else QSpinBox()
            w.setRange(it.lo, it.hi)
            w.setSingleStep(it.step)
            w.setValue(it.value)
            w.setKeyboardTracking(False)
            w.setFixedWidth(72)
            w.valueChanged.connect(lambda v, id=it.id: self.fieldChanged.emit(id, v))
        elif it.kind == "combo":
            h.addWidget(QLabel(tr(it.label)))
            w = QComboBox()
            w.addItems([tr(c) for c in it.choices])
            w.currentIndexChanged.connect(lambda k, id=it.id: self.fieldChanged.emit(id, k))
        else:
            w = QLineEdit(placeholderText=tr(it.label))
            w.setMinimumWidth(190)
            w.returnPressed.connect(lambda w=w, id=it.id: self.fieldChanged.emit(id, w.text()))
        w.setToolTip(tr(it.tip) if it.tip else "")
        h.addWidget(w)
        self.fields.setdefault(it.id, []).append(w)
        self._needs.append((row, it.need))
        return row
