"""The 3D view's selection of several objects, its snapping, copy and paste, and the edit layers, as the view and
the Edit menu use them.

``CGroupTools`` is held by the view (``CIslandView3D.group``): a Shift+click adds to or takes from the selection, a
gizmo move carries the rest with the picked one, Del and Ctrl+D act on all, a placed or moved object snaps
(``snapping.SSnap``). ``edit_menu`` adds Copy, Paste, Select the same model, Snap, Brush falloff and the layer items
to the window's Edit menu. The documents: island-editor-tools "Several objects, snapping, layers".
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QInputDialog, QLabel

from ...infra.i18n import N_, tr
from ...suite.widgets import access_keys
from .. import brushes, footprints, snapping
from ..editlayers import CEditLayers
from ..editsession import CEditSession, SEntry
from ..selection import CSelection, copy, paste

SNAP_LABELS = {
    "off": N_("islands.off"),
    "tile": N_("islands.tile_centres"),
    "corner": N_("islands.tile_corners"),
    "half": N_("islands.half_tiles"),
    "quarter": N_("islands.quarter_tiles"),
    "diagonal": N_("islands.diagonal_lattice_turned_buildings"),
    "building": N_("islands.building_grid_footprint"),
    "cell": N_("islands.8_tile_cells"),
}
TURN_LABELS = {
    "off": N_("islands.free"),
    "eighth": N_("islands.eighths_45_deg"),
    "15": N_("islands.15_deg"),
    "quarter": N_("islands.quarters_90_deg"),
}


def curve_labels() -> dict[str, str]:
    """``brushes.CURVES`` as the menu says them (the status line says them in lower case)."""
    return {
        "smooth": tr("islands.falloff.smooth"),
        "linear": tr("islands.linear"),
        "sharp": tr("islands.sharp"),
        "sphere": tr("islands.spherical"),
        "constant": tr("islands.constant"),
    }


def start_falloff(settings) -> str:
    """The brush falloff of a new island: the user's setting, the default for a name that is not a curve."""
    curve = getattr(settings, "brush_falloff", brushes.DEFAULT)
    return curve if curve in brushes.CURVES else brushes.DEFAULT


def start_snap(settings) -> snapping.SSnap:
    """The snap of a new island from the user's settings (a value that is not a mode or a turn reads as off)."""
    mode, turn = getattr(settings, "snap_places", "off"), getattr(settings, "snap_turns", "off")
    return snapping.SSnap(
        mode=mode if mode in snapping.MODES else "off",
        turn=turn if turn in snapping.TURNS else "off",
        ground=bool(getattr(settings, "snap_onto_ground", False)),
    )


def new_island_choices() -> tuple:
    """The settings rows of a new island's start values: (settings key, label id, {value: shown text})."""
    on_off = {False: tr("islands.off"), True: tr("common.on")}
    return (
        ("snap_places", "islands.menu.snap_places", {k: tr(v) for k, v in SNAP_LABELS.items()}),
        ("snap_turns", "islands.menu.snap_turns", {k: tr(v) for k, v in TURN_LABELS.items()}),
        ("snap_onto_ground", "islands.snap_onto_ground", on_off),
        ("brush_falloff", "islands.menu.brush_falloff", curve_labels()),
    )


class CGroupTools:
    def __init__(self, view) -> None:
        self.m_view = view
        self.m_snap = snapping.SSnap()
        self.m_settings = None  # the user's settings (``Settings``): the start values of a new island
        self.m_selection: CSelection | None = None
        self.m_layers: CEditLayers | None = None
        self.m_clipboard: dict | None = None  # kept across islands: a paste onto another island

    def attach(self, session: CEditSession) -> None:
        if session.m_snap is None:  # a new island starts from the user's settings; an opened one keeps its own state
            session.m_falloff = start_falloff(self.m_settings)
            session.m_snap = start_snap(self.m_settings)
        self.m_snap = session.m_snap
        self.m_selection, self.m_layers = CSelection(session), CEditLayers(session)
        self.apply_layers()

    def detach(self) -> None:
        self.m_selection = self.m_layers = None

    # the selection
    def click(self, entry: SEntry | None, shift: bool) -> None:
        """A click in Select: the entry alone, or (Shift) added to or taken from the selection."""
        if self.m_selection is None:
            return
        if entry is not None and self.m_layers.locked(entry):
            self.m_view.status.emit(tr("islands.locked_layer_name", name=self.m_layers.layer_of(entry)))
            return
        if shift and entry is not None:
            self.m_selection.toggle(entry)
            self.m_view.select(self.m_selection.primary)
        else:
            self.m_selection.set([entry])
            self.m_view.select(entry)

    def sync(self, entry: SEntry | None) -> None:
        """The view selected ``e``: the selection follows unless ``e`` is already in it."""
        if self.m_selection is None:
            return
        if entry is None:
            self.m_selection.clear()
        elif entry not in self.m_selection.entries:
            self.m_selection.set([entry])

    def others(self) -> list[SEntry]:
        if self.m_selection is None:
            return []
        return [entry for entry in self.m_selection.entries if entry is not self.m_view.m_selected and entry.shown]

    def moving(self, primary: SEntry) -> list[SEntry]:
        """What a gizmo drag of ``primary`` moves: it first, then the rest of the selection."""
        return [primary, *[entry for entry in self.others() if entry.kind != "tree"]]

    def follow(self, start: list[tuple], position) -> None:
        """A gizmo move of the first entry of ``start`` (``begin_transform``'s states) carries the others along."""
        if len(start) < 2 or position is None:
            return
        delta = np.subtract(position, start[0][1][1])
        for entry, state in start[1:]:
            self.m_view.m_session.set_transform(entry, position=np.add(state[1], delta).tolist())

    def place(self, position, entry: SEntry | None = None) -> list[float]:
        """A place snapped by the setting (onto the ground when it says so); with the building grid and a building
        that has a footprint, by its footprint."""
        polygons = self._footprint(entry)
        if self.m_snap.mode == "building" and polygons:
            return footprints.snap_place(
                position, entry.rotation, polygons, self.m_snap.ground, self.m_view.m_session.height_at
            )
        return self.m_snap.place(position, self.m_view.m_session.height_at)

    def turned(self, entry: SEntry, rotation) -> tuple[list[float], list[float] | None]:
        """The turn a gizmo gave ``entry`` rounded to the turn setting, and, with the building grid, the place its
        footprint then needs (None: it stays)."""
        turn = snapping.snap_rotation(rotation, self.m_snap.turn)
        polygons = self._footprint(entry)
        if self.m_snap.mode != "building" or not polygons:
            return turn, None
        return turn, footprints.snap_place(entry.position, turn, polygons)

    def _footprint(self, entry: SEntry | None):
        table = self.m_view.m_session.m_island.get("footprints") or {}
        return table.get(entry.cfg) if entry is not None and entry.cfg else None

    def delete(self) -> bool:
        """Del with several selected: all of them as one change. False when one or none is selected."""
        if self.m_selection is None or len(self.m_selection) < 2:
            return False
        self.m_selection.delete()
        return True

    def duplicate(self) -> bool:
        if self.m_selection is None or len(self.m_selection) < 2:
            return False
        copies = self.m_selection.duplicate()
        self.m_view.select(copies[0] if copies else None)
        self.m_selection.set(copies)
        return True

    def copy(self) -> int:
        if self.m_selection is None:
            return 0
        self.m_clipboard = copy(self.m_selection.entries)
        return len(self.m_clipboard["items"])

    def paste(self) -> int:
        """The clipboard round the cursor's spot on the ground (else the view's centre), onto the ground."""
        view = self.m_view
        if self.m_clipboard is None or view.m_session is None:
            return 0
        position = (
            view.from_view(view.m_cursor_hit)
            if view.m_cursor_hit is not None
            else view.from_view(view.m_camera.m_target)
        )
        new = paste(view.m_session, self.m_clipboard, self.place(position), on_ground=True)
        if new:
            view.apply_hidden()
            view.select(new[0])
            self.m_selection.set(new)
        return len(new)

    def same_model(self) -> int:
        if self.m_selection is None or self.m_view.m_selected is None:
            return 0
        count = self.m_selection.same_model(self.m_view.m_selected)
        self.m_view.update()
        return count

    # layers
    def apply_layers(self) -> None:
        """Hide what sits on a hidden layer (the view's own hidden set, so Show hidden brings it back)."""
        if self.m_layers is None:
            return
        if not self.m_layers.table:  # no layer: none hides anything (the walk over a continental island took a second)
            self.m_view.m_hidden.m_entries.clear()
        else:
            for entry in self.m_view.m_session.m_entries:
                if self.m_layers.hides(entry):
                    self.m_view.m_hidden.m_entries.add(entry)
                else:
                    self.m_view.m_hidden.m_entries.discard(entry)
        self.m_view.apply_hidden()


def edit_menu(main, menu) -> None:
    """The group, snap, falloff and layer items of the window's Edit menu (``main.m_doc.m_view.m_group``)."""

    def group():
        return main.m_doc.m_view.m_group if main.m_doc is not None and main.m_doc.m_view.m_session is not None else None

    def run(job, saying):
        group_tools = group()
        if group_tools is not None:
            main.say(saying(job(group_tools)))

    menu.addSeparator()
    for label, keys, job, saying in (
        (tr("islands.copy"), QKeySequence.StandardKey.Copy, CGroupTools.copy, lambda n: tr("islands.n_copied", n=n)),
        (tr("islands.paste"), QKeySequence.StandardKey.Paste, CGroupTools.paste, lambda n: tr("islands.n_pasted", n=n)),
        (
            tr("islands.select_same_model"),
            QKeySequence("Ctrl+Shift+A"),
            CGroupTools.same_model,
            lambda n: tr("islands.n_selected", n=n),
        ),
    ):
        act = menu.addAction(label, lambda job=job, saying=saying: run(job, saying))
        act.setShortcut(keys)

    def snap_value(field: str, default):
        group_tools = group()
        return getattr(group_tools.m_snap, field) if group_tools is not None else default

    _choice_menu(
        menu.addMenu(tr("islands.menu.snap_places")),
        {k: tr(v) for k, v in SNAP_LABELS.items()},
        lambda k: _set_snap(group(), "mode", k),
        lambda: snap_value("mode", "off"),
    )
    _choice_menu(
        menu.addMenu(tr("islands.menu.snap_turns")),
        {k: tr(v) for k, v in TURN_LABELS.items()},
        lambda k: _set_snap(group(), "turn", k),
        lambda: snap_value("turn", "off"),
    )
    ground = menu.addAction(tr("islands.snap_onto_ground"))
    ground.setCheckable(True)
    ground.toggled.connect(lambda on: _set_snap(group(), "ground", on))
    menu.aboutToShow.connect(lambda: _show_checked(ground, snap_value("ground", False)))
    _choice_menu(
        menu.addMenu(tr("islands.menu.brush_falloff")),
        curve_labels(),
        lambda c: _set_falloff(main, c),
        lambda: main.m_doc.m_session.m_falloff if main.m_doc is not None else brushes.DEFAULT,
    )
    menu.addSeparator()
    menu.addAction(tr("islands.selection_to_layer"), lambda: _to_layer(main, group()))
    menu.addAction(tr("islands.layers_dots"), lambda: _layers_dialog(main, group()))


def _choice_menu(menu, labels: dict, pick, current) -> None:
    """A menu of exclusive choices; ``current()`` says which is the open island's, read each time the menu opens."""
    acts = QActionGroup(menu)
    for key, label in labels.items():
        act = QAction(label, menu, checkable=True)
        act.triggered.connect(lambda _=False, key=key: pick(key))
        acts.addAction(act)
        menu.addAction(act)

    def show_current() -> None:
        for key, act in zip(labels, acts.actions(), strict=True):
            act.setChecked(key == current())

    menu.aboutToShow.connect(show_current)


def _show_checked(action: QAction, is_on: bool) -> None:
    """Show an island's state on a check item without running the item's own handler."""
    blocked = action.blockSignals(True)
    action.setChecked(is_on)
    action.blockSignals(blocked)


def _set_snap(group_tools: CGroupTools | None, field: str, value) -> None:
    if group_tools is not None:
        setattr(group_tools.m_snap, field, value)


def _set_falloff(main, curve: str) -> None:
    if main.m_doc is not None:
        main.m_doc.m_session.m_falloff = curve
        main.say(tr("islands.brush_falloff_curve", curve=curve_labels().get(curve, curve).lower()))


def _to_layer(main, group_tools: CGroupTools | None) -> None:
    if group_tools is None or not group_tools.m_selection.entries:
        main.say(tr("islands.select_something_first"))
        return
    names = group_tools.m_layers.names()
    first = [tr("islands.layer_1")]  # the name a new layer gets, kept in the edit file as typed
    name, ok = QInputDialog.getItem(
        main, tr("islands.layer"), tr("islands.layer_new_name_makes_one"), names or first, 0, True
    )
    if ok and name:
        group_tools.m_layers.assign(group_tools.m_selection.entries, name)
        group_tools.apply_layers()
        main.say(tr("islands.n_layer_name", n=len(group_tools.m_selection.entries), name=name))


def _layers_dialog(main, group_tools: CGroupTools | None) -> None:
    """Every layer with its Hidden and Locked boxes; a change applies at once and undoes like any other."""
    if group_tools is None:
        return
    dlg = QDialog(main)
    dlg.setWindowTitle(tr("islands.layers"))
    grid = QGridLayout(dlg)
    grid.addWidget(QLabel(tr("islands.layer_heading")), 0, 0)
    grid.addWidget(QLabel(tr("islands.hidden")), 0, 1)
    grid.addWidget(QLabel(tr("islands.locked")), 0, 2)
    for row, name in enumerate(group_tools.m_layers.names(), 1):
        grid.addWidget(QLabel(f"{name} ({len(group_tools.m_layers.members(name))})"), row, 0)
        for column, flag in ((1, "hidden"), (2, "locked")):
            box = QCheckBox()
            box.setChecked(bool(group_tools.m_layers.table[name][flag]))
            box.toggled.connect(
                lambda on, name=name, flag=flag: (
                    group_tools.m_layers.set_flag(name, flag, on),
                    group_tools.apply_layers(),
                )
            )
            grid.addWidget(box, row, column)
    if not group_tools.m_layers.names():
        grid.addWidget(QLabel(tr("islands.no_layers_yet_edit_gt")), 1, 0, 1, 3)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText(access_keys([tr("common.close")])[0])
    buttons.rejected.connect(dlg.reject)
    grid.addWidget(buttons, grid.rowCount(), 0, 1, 3)
    dlg.exec()
