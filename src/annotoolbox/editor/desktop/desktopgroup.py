"""The 3D view's selection of several objects, its snapping, copy and paste, and the edit layers, as the view and
the Edit menu use them.

``GroupTools`` is held by the view (``IslandView3D.group``): a Shift+click adds to or takes from the selection, a
gizmo move carries the rest with the picked one, Del and Ctrl+D act on all, a placed or moved object snaps
(``snapping.Snap``). ``edit_menu`` adds Copy, Paste, Select the same model, Snap, Brush falloff and the layer items
to the window's Edit menu. The documents: island-editor-tools "Several objects, snapping, layers".
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QInputDialog, QLabel

from ...infra.i18n import N_, tr
from ...suite.widgets import access_keys
from .. import brushes, footprints, snapping
from ..editlayers import EditLayers
from ..editsession import EditSession, Entry
from ..selection import Selection, copy, paste

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


class GroupTools:
    def __init__(self, view) -> None:
        self.view = view
        self.snap = snapping.Snap()
        self.selection: Selection | None = None
        self.layers: EditLayers | None = None
        self.clipboard: dict | None = None  # kept across islands: a paste onto another island

    def attach(self, session: EditSession) -> None:
        self.selection, self.layers = Selection(session), EditLayers(session)
        self.apply_layers()

    def detach(self) -> None:
        self.selection = self.layers = None

    # the selection
    def click(self, e: Entry | None, shift: bool) -> None:
        """A click in Select: the entry alone, or (Shift) added to or taken from the selection."""
        if self.selection is None:
            return
        if e is not None and self.layers.locked(e):
            self.view.status.emit(tr("islands.locked_layer_name", name=self.layers.layer_of(e)))
            return
        if shift and e is not None:
            self.selection.toggle(e)
            self.view.select(self.selection.primary)
        else:
            self.selection.set([e])
            self.view.select(e)

    def sync(self, e: Entry | None) -> None:
        """The view selected ``e``: the selection follows unless ``e`` is already in it."""
        if self.selection is None:
            return
        if e is None:
            self.selection.clear()
        elif e not in self.selection.entries:
            self.selection.set([e])

    def others(self) -> list[Entry]:
        if self.selection is None:
            return []
        return [e for e in self.selection.entries if e is not self.view.selected and e.shown]

    def moving(self, primary: Entry) -> list[Entry]:
        """What a gizmo drag of ``primary`` moves: it first, then the rest of the selection."""
        return [primary, *[e for e in self.others() if e.kind != "tree"]]

    def follow(self, start: list[tuple], position) -> None:
        """A gizmo move of the first entry of ``start`` (``begin_transform``'s states) carries the others along."""
        if len(start) < 2 or position is None:
            return
        delta = np.subtract(position, start[0][1][1])
        for e, state in start[1:]:
            self.view.session.set_transform(e, position=np.add(state[1], delta).tolist())

    def place(self, position, entry: Entry | None = None) -> list[float]:
        """A place snapped by the setting (onto the ground when it says so); with the building grid and a building
        that has a footprint, by its footprint."""
        polygons = self._footprint(entry)
        if self.snap.mode == "building" and polygons:
            return footprints.snap_place(
                position, entry.rotation, polygons, self.snap.ground, self.view.session.height_at
            )
        return self.snap.place(position, self.view.session.height_at)

    def turned(self, entry: Entry, rotation) -> tuple[list[float], list[float] | None]:
        """The turn a gizmo gave ``entry`` rounded to the turn setting, and, with the building grid, the place its
        footprint then needs (None: it stays)."""
        q = snapping.snap_rotation(rotation, self.snap.turn)
        polygons = self._footprint(entry)
        if self.snap.mode != "building" or not polygons:
            return q, None
        return q, footprints.snap_place(entry.position, q, polygons)

    def _footprint(self, entry: Entry | None):
        table = self.view.session.island.get("footprints") or {}
        return table.get(entry.cfg) if entry is not None and entry.cfg else None

    def delete(self) -> bool:
        """Del with several selected: all of them as one change. False when one or none is selected."""
        if self.selection is None or len(self.selection) < 2:
            return False
        self.selection.delete()
        return True

    def duplicate(self) -> bool:
        if self.selection is None or len(self.selection) < 2:
            return False
        copies = self.selection.duplicate()
        self.view.select(copies[0] if copies else None)
        self.selection.set(copies)
        return True

    def copy(self) -> int:
        if self.selection is None:
            return 0
        self.clipboard = copy(self.selection.entries)
        return len(self.clipboard["items"])

    def paste(self) -> int:
        """The clipboard round the cursor's spot on the ground (else the view's centre), onto the ground."""
        v = self.view
        if self.clipboard is None or v.session is None:
            return 0
        at = v.from_view(v.cursor_hit) if v.cursor_hit is not None else v.from_view(v.camera.target)
        new = paste(v.session, self.clipboard, self.place(at), on_ground=True)
        if new:
            v.apply_hidden()
            v.select(new[0])
            self.selection.set(new)
        return len(new)

    def same_model(self) -> int:
        if self.selection is None or self.view.selected is None:
            return 0
        n = self.selection.same_model(self.view.selected)
        self.view.update()
        return n

    # layers
    def apply_layers(self) -> None:
        """Hide what sits on a hidden layer (the view's own hidden set, so Show hidden brings it back)."""
        if self.layers is None:
            return
        if not self.layers.table:  # no layer: none hides anything (the walk over a continental island took a second)
            self.view.hidden.entries.clear()
        else:
            for e in self.view.session.entries:
                if self.layers.hides(e):
                    self.view.hidden.entries.add(e)
                else:
                    self.view.hidden.entries.discard(e)
        self.view.apply_hidden()


def edit_menu(main, menu) -> None:
    """The group, snap, falloff and layer items of the window's Edit menu (``main.doc.view.group``)."""

    def group():
        return main.doc.view.group if main.doc is not None and main.doc.view.session is not None else None

    def run(job, saying):
        g = group()
        if g is not None:
            main.say(saying(job(g)))

    menu.addSeparator()
    for label, keys, job, saying in (
        (tr("islands.copy"), QKeySequence.StandardKey.Copy, GroupTools.copy, lambda n: tr("islands.n_copied", n=n)),
        (tr("islands.paste"), QKeySequence.StandardKey.Paste, GroupTools.paste, lambda n: tr("islands.n_pasted", n=n)),
        (
            tr("islands.select_same_model"),
            QKeySequence("Ctrl+Shift+A"),
            GroupTools.same_model,
            lambda n: tr("islands.n_selected", n=n),
        ),
    ):
        act = menu.addAction(label, lambda job=job, saying=saying: run(job, saying))
        act.setShortcut(keys)
    _choice_menu(
        menu.addMenu(tr("islands.menu.snap_places")),
        {k: tr(v) for k, v in SNAP_LABELS.items()},
        lambda k: _set_snap(group(), "mode", k),
        "off",
    )
    _choice_menu(
        menu.addMenu(tr("islands.menu.snap_turns")),
        {k: tr(v) for k, v in TURN_LABELS.items()},
        lambda k: _set_snap(group(), "turn", k),
        "off",
    )
    ground = menu.addAction(tr("islands.snap_onto_ground"))
    ground.setCheckable(True)
    ground.toggled.connect(lambda on: _set_snap(group(), "ground", on))
    _choice_menu(
        menu.addMenu(tr("islands.menu.brush_falloff")), curve_labels(), lambda c: _set_falloff(main, c), brushes.DEFAULT
    )
    menu.addSeparator()
    menu.addAction(tr("islands.selection_to_layer"), lambda: _to_layer(main, group()))
    menu.addAction(tr("islands.layers_dots"), lambda: _layers_dialog(main, group()))


def _choice_menu(menu, labels: dict, pick, current: str) -> None:
    acts = QActionGroup(menu)
    for key, label in labels.items():
        act = QAction(label, menu, checkable=True, checked=key == current)
        act.triggered.connect(lambda _=False, key=key: pick(key))
        acts.addAction(act)
        menu.addAction(act)


def _set_snap(g: GroupTools | None, field: str, value) -> None:
    if g is not None:
        setattr(g.snap, field, value)


def _set_falloff(main, curve: str) -> None:
    if main.doc is not None:
        main.doc.session.falloff = curve
        main.say(tr("islands.brush_falloff_curve", curve=curve_labels().get(curve, curve).lower()))


def _to_layer(main, g: GroupTools | None) -> None:
    if g is None or not g.selection.entries:
        main.say(tr("islands.select_something_first"))
        return
    names = g.layers.names()
    first = [tr("islands.layer_1")]  # the name a new layer gets, kept in the edit file as typed
    name, ok = QInputDialog.getItem(
        main, tr("islands.layer"), tr("islands.layer_new_name_makes_one"), names or first, 0, True
    )
    if ok and name:
        g.layers.assign(g.selection.entries, name)
        g.apply_layers()
        main.say(tr("islands.n_layer_name", n=len(g.selection.entries), name=name))


def _layers_dialog(main, g: GroupTools | None) -> None:
    """Every layer with its Hidden and Locked boxes; a change applies at once and undoes like any other."""
    if g is None:
        return
    dlg = QDialog(main)
    dlg.setWindowTitle(tr("islands.layers"))
    grid = QGridLayout(dlg)
    grid.addWidget(QLabel(tr("islands.layer_heading")), 0, 0)
    grid.addWidget(QLabel(tr("islands.hidden")), 0, 1)
    grid.addWidget(QLabel(tr("islands.locked")), 0, 2)
    for row, name in enumerate(g.layers.names(), 1):
        grid.addWidget(QLabel(f"{name} ({len(g.layers.members(name))})"), row, 0)
        for col, flag in ((1, "hidden"), (2, "locked")):
            box = QCheckBox()
            box.setChecked(bool(g.layers.table[name][flag]))
            box.toggled.connect(lambda on, name=name, flag=flag: (g.layers.set_flag(name, flag, on), g.apply_layers()))
            grid.addWidget(box, row, col)
    if not g.layers.names():
        grid.addWidget(QLabel(tr("islands.no_layers_yet_edit_gt")), 1, 0, 1, 3)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText(access_keys([tr("common.close")])[0])
    buttons.rejected.connect(dlg.reject)
    grid.addWidget(buttons, grid.rowCount(), 0, 1, 3)
    dlg.exec()
