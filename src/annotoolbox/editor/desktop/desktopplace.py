"""Placing a game's buildings and other objects from its asset list: the Buildings dock (``PlaceablesPanel``: the
list searched by name, GUID or template, grouped like the build menu, with the asset's icon) and the Place tool's
building mode (``PlaceTool``: the model under the cursor on its build grid, its footprint drawn green or red by the
live rule check, turned in the game's steps, placed by a click or a drag from the list).

The view holds one ``PlaceTool`` (``IslandControls.placer``); the island's doc holds the panel. Rules and sources:
island-editor "Placing from the list"; the rules themselves are ``placedrules.check_place``.
"""

from __future__ import annotations

import json
import math

from PySide6.QtCore import QEvent, QMimeData, QObject, Qt
from PySide6.QtGui import QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...assetedit.icons import IconSource
from ...infra.i18n import alternatives, tr
from ...sources import icons2070
from .. import footprints, placeables, placedrules, snapping
from ..editchange import yaw_quat
from ..editsession import game_of
from .desktopgeometry import outline

MIME = "application/x-anno-placeable"  # a row dragged from the list onto the view: its GUID
SHOWN = 600  # rows listed at once (the search narrows the rest)
ICONS = 200  # rows given their icon per fill (each is a game file read and decoded once)
ICON_SIZE = 28
FREE_TURN = math.pi / 12  # what [ and ] turn a model without a footprint by
OK_COLOUR, BAD_COLOUR = (0.35, 0.9, 0.35, 1.0), (1.0, 0.25, 0.2, 1.0)


class PlaceTool:
    """The Place tool for a pick with a footprint: snapping, the live rules, the footprint overlay, drops on the view."""

    def __init__(self, view) -> None:
        self.view = view
        self.polygons: list | None = None  # the pick's footprint (tiles round its origin)
        self.problems: list[str] = []
        self.spot: tuple[list[float], float] | None = None  # where a click would place it now: position, yaw
        self.panel: PlaceablesPanel | None = None  # the list a drop comes from
        view.setAcceptDrops(True)
        self._filter = _DropFilter(self)
        view.installEventFilter(self._filter)

    def reset(self) -> None:
        self.polygons, self.problems, self.spot = None, [], None

    def picked(self, pick: dict | None) -> None:
        """A new pick: its footprint from the pick or the island's table, the turn onto the game's steps."""
        self.reset()
        if not pick:
            return
        session = self.view.session
        table = session.island.setdefault("footprints", {}) if session is not None else {}
        self.polygons = pick.get("footprint") or table.get(pick.get("cfg") or "") or None
        if self.polygons and pick.get("cfg"):
            table[pick["cfg"]] = self.polygons  # the rules and the snap of what is placed use it from now on
        if self.polygons:
            self.view.place_yaw = snapping.snap_yaw(self.view.place_yaw, self.step())

    def step(self) -> str:
        session = self.view.session
        return placeables.turn_step(game_of(session.island)) if session is not None else "quarter"

    def turn(self, sign: int) -> None:
        """``[`` / ``]``: a building by the game's step, another model by 15 degrees."""
        v = self.view
        if self.polygons:
            v.place_yaw = (snapping.snap_yaw(v.place_yaw, self.step()) + sign * snapping.TURNS[self.step()]) % math.tau
        else:
            v.place_yaw = (v.place_yaw + sign * FREE_TURN) % math.tau
        self.ghost()

    def where(self, hit) -> tuple[list[float], float]:
        """Where a click on ``hit`` (view space) places the pick, and its turn: on the footprint's grid unless Shift
        is held, else by the snap setting."""
        v = self.view
        yaw = v.place_yaw
        free = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self.polygons and not free:
            yaw = snapping.snap_yaw(yaw, self.step())
            at = footprints.snap_place(v.from_view(hit), yaw_quat(yaw), self.polygons, True, v.session.height_at)
            return at, yaw
        return v.group.place(v.from_view(hit)), v.group.snap.yaw(yaw)

    def ghost(self) -> None:
        """The pick under the cursor, and what the rules say of that spot (the status bar)."""
        v = self.view
        pick = v.pick
        if v.session is None or not pick or v.cursor_hit is None or pick.get("tree") is not None:
            if v.renderer is not None:
                v.renderer.set_ghost(None)
            self.spot = None
            return
        at, yaw = self.where(v.cursor_hit)
        self.spot = (at, yaw)
        marker = pick.get("template") if pick.get("guid") and not pick.get("cfg") else None
        if v.renderer is not None:
            v.renderer.set_ghost(pick.get("cfg") or "", marker, at, yaw_quat(yaw))
        old = self.problems
        self.problems = placedrules.check_place(v.session, self.polygons, at, yaw_quat(yaw), pick.get("terrain", ()))
        if self.problems != old:
            v.status.emit(f"{pick.get('name', '')}: {'; '.join(self.problems)}" if self.problems else "")

    def place(self, hit) -> None:
        """Place the pick at ``hit`` (view space); what the rules said is in the status bar, the object placed all the
        same (the Checks tab keeps the warning)."""
        v = self.view
        if hit is None or not v.pick:
            return
        at, yaw = self.where(hit)
        problems = placedrules.check_place(v.session, self.polygons, at, yaw_quat(yaw), v.pick.get("terrain", ()))
        if v.session.place(v.pick, at, yaw) is not None:
            v.apply_hidden()
            name = v.pick.get("name", "")
            v.status.emit(
                tr("islands.placed_name_with_problems", name=name, problems="; ".join(problems))
                if problems
                else tr("islands.placed_name", name=name)
            )

    def overlay(self) -> list:
        """The footprint under the ghost, on the ground: green where the rules allow it, red where they do not."""
        if self.view.tool != "place" or not self.polygons or self.spot is None:
            return []
        at, yaw = self.spot
        v = self.view
        polys = footprints.world(self.polygons, at, yaw_quat(yaw))
        return outline(polys, v.to_view, v.session.height_at, BAD_COLOUR if self.problems else OK_COLOUR)


class _DropFilter(QObject):
    """A row of the list dragged onto the view: the model follows the cursor, the drop places it."""

    def __init__(self, tool: PlaceTool) -> None:
        super().__init__()
        self.tool = tool

    def eventFilter(self, obj, event) -> bool:  # Qt's name
        kind = event.type()
        if kind not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return False
        tool, view = self.tool, self.tool.view
        if not event.mimeData().hasFormat(MIME) or view.session is None or tool.panel is None:
            return False
        guid = json.loads(bytes(event.mimeData().data(MIME)).decode("utf-8"))["guid"]
        if kind == QEvent.Type.DragEnter:
            try:
                pick = tool.panel.pick_now(guid)
            except (OSError, KeyError, ValueError) as exc:  # an exception must not leave an event filter
                view.status.emit(tr("islands.cannot_place_asset", guid=guid, error=exc))
                return False
            if pick is None:
                return False
            view.set_pick(pick)
        hit = view.ground_at(event.position().toPoint())
        view.cursor_hit = hit
        if kind == QEvent.Type.Drop:
            tool.place(hit)
        else:
            tool.ghost()
        view.update()
        event.acceptProposedAction()
        return True


class _Tree(QTreeWidget):
    """The list, whose rows drag onto the view."""

    def startDrag(self, _actions) -> None:  # Qt's name
        item = self.currentItem()
        row = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if row is None:
            return
        mime = QMimeData()
        mime.setData(MIME, json.dumps({"guid": row.guid}).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        icon = item.icon(0)
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(ICON_SIZE, ICON_SIZE))
        drag.exec(Qt.DropAction.CopyAction)


class PlaceablesPanel(QWidget):
    """The Buildings dock: the game's placeable assets, read in the background, searched and grouped; a click picks one
    for the Place tool, a drag places it where it is dropped."""

    def __init__(self, doc) -> None:
        super().__init__()
        self.doc = doc
        self.rows: list[placeables.Placeable] = []
        self.by_guid: dict[int, placeables.Placeable] = {}
        self._picks: dict[int, dict] = {}
        self._icons = None
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit(placeholderText=tr("islands.search_name_guid_template"))
        self.buildings = QCheckBox(tr("islands.buildings_only"), checked=True)
        top.addWidget(self.search, 1)
        top.addWidget(self.buildings)
        layout.addLayout(top)
        self.tree = _Tree()
        self.tree.setHeaderLabels(
            [tr("common.asset"), "GUID", tr("common.template")]
        )  # i18n: ok (GUID is the game's term)
        self.tree.setColumnWidth(0, 230)
        self.tree.setIconSize(QPixmap(ICON_SIZE, ICON_SIZE).size())
        self.tree.setDragEnabled(True)
        layout.addWidget(self.tree, 1)
        self.preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        self.count = QLabel(tr("islands.reading_games_assets"))
        layout.addWidget(self.count)
        self.search.textChanged.connect(self.fill)
        self.buildings.toggled.connect(self.fill)
        self.tree.itemClicked.connect(self._clicked)
        doc.view.placer.panel = self
        if "place" in doc.modes["tools"] and not doc.session.readonly:
            doc.background.run(lambda: doc.editor.placeables(), self._loaded)  # an editor without a list says so
        else:
            self.count.setText(tr("islands.island_takes_no_new_objects"))

    def _loaded(self, result) -> None:
        if not self.doc.alive:
            return
        if isinstance(result, Exception):
            self.count.setText(tr("islands.no_asset_list", error=f"{type(result).__name__}: {result}"))
            return
        self.rows, self.by_guid = list(result), {r.guid: r for r in result}
        terrain = self.doc.session.island.setdefault("terrain", {})  # the rules' ground per asset (placedrules)
        terrain.update({str(r.guid): list(r.terrain) for r in self.rows if r.terrain})
        self.doc.session.island["buildings"] = sorted(str(r.guid) for r in self.rows if r.building)
        self._icons = IconSource(self.doc.editor.read_game)
        self.fill()
        self.doc.background.run(self._read_icon_data, self._icon_data)  # atlas games: icons.xml, off the thread

    def _read_icon_data(self):
        """The game's icon atlases and GUID -> tile table (the games that keep icons in ``icons.xml``)."""
        read = self.doc.editor.read_game

        def exists(path: str) -> bool:
            try:
                read(path)
            except (KeyError, FileNotFoundError, OSError):
                return False
            return True

        return icons2070.load_icon_data(read, exists)

    def _icon_data(self, result) -> None:
        if not self.doc.alive or isinstance(result, Exception) or self._icons is None or not result[0]:
            return
        self._icons.use_atlases(*result)
        self.fill()

    def _key(self, row: placeables.Placeable) -> str:
        """Where the row's icon is: the file its asset names, else its tile of the game's icon atlas; "" for none."""
        return self._icons.key_for(row.guid, row.icon) if self._icons is not None else ""

    def fill(self) -> None:
        self.tree.clear()
        found = placeables.search(self.rows, self.search.text(), self.buildings.isChecked())
        groups = placeables.grouped(found)
        shown, budget, query = 0, ICONS, bool(self.search.text().strip())
        for name, rows in groups.items():
            top = QTreeWidgetItem([f"{name}  ({len(rows)})"])
            self.tree.addTopLevelItem(top)
            for r in rows[: max(0, SHOWN - shown)]:
                item = QTreeWidgetItem([r.name, str(r.guid), r.template])
                item.setData(0, Qt.ItemDataRole.UserRole, r)
                item.setToolTip(
                    0,
                    tr(
                        "islands.tip.name_cfg_n_variations_stands",
                        name=r.name,
                        cfg=r.cfg,
                        n=r.variations,
                        ground=_ground(r),
                    ),
                )
                if budget and self._key(r) and (query or len(groups) == 1):
                    self._set_icon(item, r)
                    budget -= 1
                elif not self._key(r):
                    item.setToolTip(0, f"{item.toolTip(0)}\n{tr('islands.no_icon_in_games_files')}")
                top.addChild(item)
                shown += 1
            top.setExpanded(query and len(groups) <= 12)
        self.count.setText(tr("islands.found_total_groups", found=len(found), total=len(self.rows), groups=len(groups)))

    def _set_icon(self, item: QTreeWidgetItem, row: placeables.Placeable) -> None:
        image = self._icons.image(self._key(row))
        if image is None:
            return
        item.setIcon(0, QIcon(QPixmap.fromImage(image.scaled(ICON_SIZE, ICON_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                                                             Qt.TransformationMode.SmoothTransformation))))  # fmt: skip

    def _clicked(self, item) -> None:
        row = item.data(0, Qt.ItemDataRole.UserRole)
        if row is None:
            item.setExpanded(not item.isExpanded())
            return
        if self._key(row) and item.icon(0).isNull():
            self._set_icon(item, row)
        self._show(row)
        self.choose(row.guid)

    def _show(self, row: placeables.Placeable) -> None:
        image = self._icons.image(self._key(row)) if self._key(row) else None
        if image is not None:
            self.preview.setPixmap(
                QPixmap.fromImage(image.scaledToHeight(64, Qt.TransformationMode.SmoothTransformation))
            )
        else:
            self.preview.setText(tr("islands.model_shows_under_cursor", name=row.name))

    def guid_of(self, cfg: str) -> int | None:
        """The asset whose first model is ``cfg`` (the viewer's model handed over), None when no listed asset has it."""
        key = cfg.replace("\\", "/").lower()
        return next((r.guid for r in self.rows if r.cfg.lower() == key), None)

    def choose(self, guid: int) -> str:
        """Pick the asset ``guid`` for the Place tool (its model made drawable first); "" or why not."""
        row = self.by_guid.get(int(guid))
        if row is None:
            return (
                tr("islands.asset_list_still_read") if not self.rows else tr("islands.asset_no_model_place", guid=guid)
            )
        doc = self.doc
        doc.say(tr("islands.preparing_name", name=row.name))

        def done(result):
            if not doc.alive:
                return
            if isinstance(result, Exception):
                doc.say(tr("islands.cannot_place_name_error", name=row.name, error=result))
                return
            doc.view.set_pick(result)
            doc.say(tr("islands.place_click_island_turn_shift", name=row.name))

        doc.background.run(lambda: self.pick_now(row.guid), done)
        return ""

    def pick_now(self, guid: int) -> dict | None:
        """The Place tool's pick for ``guid``, made drawable and with its footprint (kept once made)."""
        if guid not in self._picks:
            row = self.by_guid.get(int(guid))
            if row is None:
                return None
            editor = self.doc.editor
            out = placeables.pick(row, editor.drawable(row.cfg))
            out["footprint"] = editor.footprint_of(out["cfg"]) or editor.footprint_of(row.cfg) or None
            self._picks[guid] = out
        return dict(self._picks[guid])


def _ground(row: placeables.Placeable) -> str:
    return alternatives(row.terrain) if row.terrain else tr("islands.any_ground")
