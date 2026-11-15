"""The island editor's ribbon, after Anno 1800's internal editor: tabs, each with labelled groups of large
buttons. ``TABS`` says what is where and when it is enabled (plain data, no Qt); ``Ribbon`` draws it and
``make_actions`` makes the actions the buttons and the menus share. Plan: ``docs/island-editor.md`` ("Layout").
"""

from __future__ import annotations

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
from ...suite import features
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
    "none": N_("No island"),
    "view": N_("View only"),
    "object": N_("Object Mode"),
    "terrain": N_("Terrain Mode"),
    "path": N_("Path Mode"),
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


_RADIUS = _spin("radius", N_("Radius"), (1, 24, 1), 6, "brush", N_("The brush's radius in tiles"))


TABS = (
    (
        N_("Home"),
        (
            (N_("Edit"), (_b("undo", N_("Undo"), "undo", "undo", N_("Undo the last change (Ctrl+Z)"), True),
                      _b("redo", N_("Redo"), "redo", "redo", N_("Redo (Ctrl+Y)"), True))),
            (N_("File"), (_b("save", N_("Save"), "save", "editable", N_("Write the edit file (Ctrl+S)")),
                      _b("build", N_("Build &&\ninstall"), "build", "buildable", N_("Build the open island from its saved edits")),
                      Item("target", N_("Saved"), "", "combo", "kind117", N_("How a 117 island is saved"),
                           choices=(N_("replaces the original"), N_("as a new island"))))),
            (N_("Selection"), (_t("tool_select", N_("Select"), "select", "select", N_("Select and move objects (S)")),
                           _b("deselect", N_("De-Select"), "deselect", "island", N_("Select nothing (Esc)")),
                           _b("focus", N_("Focus"), "focus", "selection", N_("Fly the camera to the selection (F)")))),
            (N_("Camera"), (Item("goto", N_("go to: x z [distance] [turn]"), "", "text", "island",
                             N_("x and z in tiles, then how far and from which side")),)),
        ),
    ),
    (
        N_("Object"),
        (
            (N_("Place"), (_t("tool_place", N_("Place"), "place", "place", N_("Place the picked model on the ground (A; [ ] turn)")),
                       _b("models", N_("Object\nGroups"), "models", "island", N_("The model library")))),
            (N_("Transform"), (_t("gizmo_move", N_("Move"), "move", "editable", N_("Move along an axis or over the ground (W)")),
                           _t("gizmo_turn", N_("Turn"), "turn", "editable", N_("Turn about the vertical (E)")),
                           _t("gizmo_tilt", N_("Tilt"), "tilt", "editable", N_("Tilt about the model's own axes (E again)")),
                           _t("gizmo_scale", N_("Scale"), "scale", "editable", N_("Scale (R); a prop per axis")))),
            (N_("Edit"), (_b("delete", N_("Delete"), "delete", "selection", N_("Delete the selection (Del)")),
                      _b("duplicate", N_("Duplicate"), "duplicate", "selection", N_("Duplicate the selection (Ctrl+D)")))),
            (N_("Object Spray"), (_t("tool_plant", N_("Plant\nforest"), "plant", "forest", N_("Plant forest tiles (T)")),
                              _t("tool_clear", N_("Clear\nforest"), "clear", "forest", N_("Clear forest tiles (T)")),
                              _t("tool_spray", N_("Spray\nmodel"), "spray", "spray",
                                 N_("Spray the picked model over the ground with a spacing and a random turn (O)")),
                              _RADIUS,
                              _spin("spacing", N_("Spacing"), (0.5, 12, 0.5), 2.0, "spray",
                              N_("Tiles between sprayed models, at least"), decimals=1))),
            (N_("Visibility"), (_t("foliage", N_("Hide\nfoliage"), "hide", "island", N_("Trees, bushes and grass out of the view")),
                            _b("show_hidden", N_("Show\nhidden"), "eye", "island", N_("Show everything hidden again")))),
        ),
    ),
    (
        N_("Terrain"),
        (
            (N_("Height"), (_t("tool_raise", N_("Raise"), "raise", "height", N_("Height brush: raise (H)")),
                         _t("tool_lower", N_("Lower"), "lower", "height", N_("Height brush: lower (H)")),
                         _t("tool_flatten", N_("Flatten"), "flatten", "height", N_("Height brush: flatten to the height where the stroke began (H)")),
                         _t("tool_smooth", N_("Smooth"), "smooth", "height", N_("Height brush: smooth (H)")))),
            (N_("Relief"), (_t("tool_noise", N_("Noise"), "noise", "height", N_("Height brush: rough up the ground with repeatable noise (H)")),
                        _t("tool_terrace", N_("Terrace"), "terrace", "height", N_("Height brush: pull the ground onto steps of one unit (H)")),
                        _t("tool_ramp", N_("Ramp"), "ramp", "height", N_("Height brush: drag from one point to another to lay a slope between them (H)")),
                        _t("tool_despike", N_("Spike\nremoval"), "despike", "height",
                           N_("Height brush: level single corners that stand out of the ground (H)")))),
            (N_("Ground"), (_t("tool_paint", N_("Paint"), "paint", "paint", N_("Paint the ground's materials (P)")),
                        _b("materials", N_("Material\nGallery"), "materials", "island", N_("The ground materials by the game they come from")))),
            (N_("Tint"), (_t("tool_tint", N_("Tint"), "paint", "active", N_("Tint: tint the ground; the build writes it into the tint pages (G)")),
                      Item("tint", N_("Tint"), "", "combo", "active", N_("The tint the brush lays"),
                           choices=tuple(name for name, _factor in PRESETS)))),
            (N_("Surf"), (_t("tool_surf_on", N_("Waves"), "surf_on", "surf", N_("Sea map: set the coast waves to the brush's strength (U)")),
                      _t("tool_surf_off", N_("Calm"), "surf_off", "surf", N_("Sea map: no coast waves (U)")))),
            (N_("Brush"), (_RADIUS,
                       _spin("strength", N_("Strength"), (0.1, 1.0, 0.05), 0.5, "brush", N_("The brush's strength"), decimals=2))),
        ),
    ),
    (
        N_("Grid"),
        (
            (N_("Active map"), (_t("tool_cell_on", N_("Draw\ncells"), "cell_on", "active", N_("Active map: draw 8-tile cells the game draws (G)")),
                            _t("tool_cell_off", N_("Erase\ncells"), "cell_off", "active", N_("Active map: erase cells (G)")),
                            _RADIUS)),
            (N_("Ford"), (_t("tool_ford_on", N_("Lay\nford"), "ford_on", "active", N_("Ford: lay fords on river tiles, where units may cross (G)")),
                        _t("tool_ford_off", N_("Erase\nford"), "ford_off", "active", N_("Ford: erase fords (G)")))),
            (N_("Construction area"), (_t("tool_area_on", N_("Add\narea"), "cell_on", "active", N_("Construction area: add tiles (G)")),
                             _t("tool_area_off", N_("Remove\narea"), "cell_off", "active", N_("Construction area: remove tiles (G)")),
                             _t("tool_areakind", N_("Paint\narea id"), "zone", "active", N_("Construction area: paint the chosen area id (G)")),
                             Item("areakind", N_("Area id"), "", "combo", "active", N_("The id of the island's other area the brush paints"),
                                  choices=tuple(name for _id, name in AREA_KINDS)))),
            (N_("Herd area"), (_t("tool_herd_on", N_("Add\nherds"), "cell_on", "active", N_("Herd area: add tiles the herds roam on (G)")),
                           _t("tool_herd_off", N_("Remove\nherds"), "cell_off", "active", N_("Herd area: remove tiles from the herds' ground (G)")))),
            (N_("Environment"), (_t("tool_zone", N_("Paint\nzone"), "zone", "active", N_("Environment: paint the chosen zone on 4-tile cells (G)")),
                             Item("zone", N_("Zone"), "", "combo", "active", N_("The environment zone the brush paints"),
                                  choices=tuple(name for _number, name in ZONES)))),
            (N_("Checks"), (_b("check_rules", N_("Check\nrules"), "rules", "island", N_("The island against the game's rules")),
                        _b("check_slots", N_("Check\nslots"), "slots", "slots",
                           N_("River slots: both banks on the build plateau and joined to the build grid")),
                        _b("check_falls", N_("Check\nfalls"), "falls", "island", N_("Which waterfalls run through rock")))),
        ),
    ),
    (
        N_("Path"),
        (
            (N_("Rivers"), (_t("tool_water", N_("Rivers &&\nlakes"), "water", "water", N_("Rivers' points and lakes' levels (L)")),
                        _b("point_delete", N_("Delete\npoint"), "point_delete", "river_point", N_("Remove the river point (Del)")),
                        _b("point_insert", N_("Insert\npoint"), "point_insert", "river_point", N_("Insert a point after it (Ctrl+D)")))),
            (N_("Path blockers"), (_t("tool_block", N_("Block\npath"), "block", "blocker", N_("Paint cells units cannot walk on (B)")),
                               _t("tool_free", N_("Free\npath"), "free", "blocker", N_("Clear blocked cells (B)")),
                               _RADIUS)),
            (N_("Roads"), (_b("road_pieces", N_("Road\npieces"), "road", "", N_("The road piece editor")),)),
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
        for name, groups in tabs:
            self.bar.addTab(tr(name))
            self.pages.addWidget(self._page(groups))
        self.bar.currentChanged.connect(self.pages.setCurrentIndex)
        self.pages.setFixedHeight(max(self.pages.widget(k).sizeHint().height() for k in range(self.pages.count())))
        layout.addWidget(self.bar)
        layout.addWidget(self.pages)

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
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the view keeps the keyboard
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
