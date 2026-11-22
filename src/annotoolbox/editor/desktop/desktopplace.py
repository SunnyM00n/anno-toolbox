"""Placing a game's buildings and other objects from its asset list: the Buildings dock (``CPlaceablesPanel``: the
list searched by name, GUID or template, grouped like the build menu, with the asset's icon) and the Place tool's
building mode (``CPlaceTool``: the model under the cursor on its build grid, its footprint drawn green or red by the
live rule check, turned in the game's steps, placed by a click or a drag from the list).

The view holds one ``CPlaceTool`` (``CIslandControls.placer``); the island's doc holds the panel. Rules and sources:
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

from ...assetedit.icons import CIconSource
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


class CPlaceTool:
    """The Place tool for a pick with a footprint: snapping, the live rules, the footprint overlay, drops on the view."""

    def __init__(self, view) -> None:
        self.m_view = view
        self.m_polygons: list | None = None  # the pick's footprint (tiles round its origin)
        self.m_problems: list[str] = []
        self.m_spot: tuple[list[float], float] | None = None  # where a click would place it now: position, yaw
        self.m_panel: CPlaceablesPanel | None = None  # the list a drop comes from
        view.setAcceptDrops(True)
        self._m_filter = _CDropFilter(self)
        view.installEventFilter(self._m_filter)

    def reset(self) -> None:
        self.m_polygons, self.m_problems, self.m_spot = None, [], None

    def picked(self, pick: dict | None) -> None:
        """A new pick: its footprint from the pick or the island's table, the turn onto the game's steps."""
        self.reset()
        if not pick:
            return
        session = self.m_view.m_session
        table = session.m_island.setdefault("footprints", {}) if session is not None else {}
        self.m_polygons = pick.get("footprint") or table.get(pick.get("cfg") or "") or None
        if self.m_polygons and pick.get("cfg"):
            table[pick["cfg"]] = self.m_polygons  # the rules and the snap of what is placed use it from now on
        if self.m_polygons:
            self.m_view.m_place_yaw = snapping.snap_yaw(self.m_view.m_place_yaw, self.step())

    def step(self) -> str:
        session = self.m_view.m_session
        return placeables.turn_step(game_of(session.m_island)) if session is not None else "quarter"

    def turn(self, sign: int) -> None:
        """``[`` / ``]``: a building by the game's step, another model by 15 degrees."""
        view = self.m_view
        if self.m_polygons:
            view.m_place_yaw = (
                snapping.snap_yaw(view.m_place_yaw, self.step()) + sign * snapping.TURNS[self.step()]
            ) % math.tau
        else:
            view.m_place_yaw = (view.m_place_yaw + sign * FREE_TURN) % math.tau
        self.ghost()

    def where(self, hit) -> tuple[list[float], float]:
        """Where a click on ``hit`` (view space) places the pick, and its turn: on the footprint's grid unless Shift
        is held, else by the snap setting."""
        view = self.m_view
        yaw = view.m_place_yaw
        free = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self.m_polygons and not free:
            yaw = snapping.snap_yaw(yaw, self.step())
            at = footprints.snap_place(
                view.from_view(hit), yaw_quat(yaw), self.m_polygons, True, view.m_session.height_at
            )
            return at, yaw
        return view.m_group.place(view.from_view(hit)), view.m_group.m_snap.yaw(yaw)

    def ghost(self) -> None:
        """The pick under the cursor, and what the rules say of that spot (the status bar)."""
        view = self.m_view
        pick = view.m_pick
        if view.m_session is None or not pick or view.m_cursor_hit is None or pick.get("tree") is not None:
            if view.m_renderer is not None:
                view.m_renderer.set_ghost(None)
            self.m_spot = None
            return
        at, yaw = self.where(view.m_cursor_hit)
        self.m_spot = (at, yaw)
        marker = pick.get("template") if pick.get("guid") and not pick.get("cfg") else None
        if view.m_renderer is not None:
            view.m_renderer.set_ghost(pick.get("cfg") or "", marker, at, yaw_quat(yaw))
        old = self.m_problems
        self.m_problems = placedrules.check_place(
            view.m_session, self.m_polygons, at, yaw_quat(yaw), pick.get("terrain", ())
        )
        if self.m_problems != old:
            view.status.emit(f"{pick.get('name', '')}: {'; '.join(self.m_problems)}" if self.m_problems else "")

    def place(self, hit) -> None:
        """Place the pick at ``hit`` (view space); what the rules said is in the status bar, the object placed all the
        same (the Checks tab keeps the warning)."""
        view = self.m_view
        if hit is None or not view.m_pick:
            return
        at, yaw = self.where(hit)
        problems = placedrules.check_place(
            view.m_session, self.m_polygons, at, yaw_quat(yaw), view.m_pick.get("terrain", ())
        )
        if view.m_session.place(view.m_pick, at, yaw) is not None:
            view.apply_hidden()
            name = view.m_pick.get("name", "")
            view.status.emit(
                tr("islands.placed_name_with_problems", name=name, problems="; ".join(problems))
                if problems
                else tr("islands.placed_name", name=name)
            )

    def overlay(self) -> list:
        """The footprint under the ghost, on the ground: green where the rules allow it, red where they do not."""
        if self.m_view.m_tool != "place" or not self.m_polygons or self.m_spot is None:
            return []
        at, yaw = self.m_spot
        view = self.m_view
        polys = footprints.world(self.m_polygons, at, yaw_quat(yaw))
        return outline(polys, view.to_view, view.m_session.height_at, BAD_COLOUR if self.m_problems else OK_COLOUR)


class _CDropFilter(QObject):
    """A row of the list dragged onto the view: the model follows the cursor, the drop places it."""

    def __init__(self, tool: CPlaceTool) -> None:
        super().__init__()
        self.m_tool = tool

    def eventFilter(self, obj, event) -> bool:  # Qt's name
        kind = event.type()
        if kind not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return False
        tool, view = self.m_tool, self.m_tool.m_view
        if not event.mimeData().hasFormat(MIME) or view.m_session is None or tool.m_panel is None:
            return False
        guid = json.loads(bytes(event.mimeData().data(MIME)).decode("utf-8"))["guid"]
        if kind == QEvent.Type.DragEnter:
            try:
                pick = tool.m_panel.pick_now(guid)
            except (OSError, KeyError, ValueError) as error:  # an exception must not leave an event filter
                view.status.emit(tr("islands.cannot_place_asset", guid=guid, error=error))
                return False
            if pick is None:
                return False
            view.set_pick(pick)
        hit = view.ground_at(event.position().toPoint())
        view.m_cursor_hit = hit
        if kind == QEvent.Type.Drop:
            tool.place(hit)
        else:
            tool.ghost()
        view.update()
        event.acceptProposedAction()
        return True


class _CTree(QTreeWidget):
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


class CPlaceablesPanel(QWidget):
    """The Buildings dock: the game's placeable assets, read in the background, searched and grouped; a click picks one
    for the Place tool, a drag places it where it is dropped."""

    def __init__(self, doc) -> None:
        super().__init__()
        self.m_doc = doc
        self.m_rows: list[placeables.SPlaceable] = []
        self.m_by_guid: dict[int, placeables.SPlaceable] = {}
        self._m_picks: dict[int, dict] = {}
        self._m_icons = None
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.m_search = QLineEdit(placeholderText=tr("islands.search_name_guid_template"))
        self.m_buildings = QCheckBox(tr("islands.buildings_only"), checked=True)
        top.addWidget(self.m_search, 1)
        top.addWidget(self.m_buildings)
        layout.addLayout(top)
        self.m_tree = _CTree()
        self.m_tree.setHeaderLabels(
            [tr("common.asset"), "GUID", tr("common.template")]
        )  # i18n: ok (GUID is the game's term)
        self.m_tree.setColumnWidth(0, 230)
        self.m_tree.setIconSize(QPixmap(ICON_SIZE, ICON_SIZE).size())
        self.m_tree.setDragEnabled(True)
        layout.addWidget(self.m_tree, 1)
        self.m_preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.m_preview.setWordWrap(True)
        layout.addWidget(self.m_preview)
        self.m_count = QLabel(tr("islands.reading_games_assets"))
        layout.addWidget(self.m_count)
        self.m_search.textChanged.connect(self.fill)
        self.m_buildings.toggled.connect(self.fill)
        self.m_tree.itemClicked.connect(self._clicked)
        doc.m_view.m_placer.m_panel = self
        if "place" in doc.m_modes["tools"] and not doc.m_session.m_readonly:
            doc.m_background.run(lambda: doc.m_editor.placeables(), self._loaded)  # an editor without a list says so
        else:
            self.m_count.setText(tr("islands.island_takes_no_new_objects"))

    def _loaded(self, result) -> None:
        if not self.m_doc.m_alive:
            return
        if isinstance(result, Exception):
            self.m_count.setText(tr("islands.no_asset_list", error=f"{type(result).__name__}: {result}"))
            return
        self.m_rows, self.m_by_guid = list(result), {row.guid: row for row in result}
        terrain = self.m_doc.m_session.m_island.setdefault("terrain", {})  # the rules' ground per asset (placedrules)
        terrain.update({str(row.guid): list(row.terrain) for row in self.m_rows if row.terrain})
        self.m_doc.m_session.m_island["buildings"] = sorted(str(row.guid) for row in self.m_rows if row.building)
        self._m_icons = CIconSource(self.m_doc.m_editor.read_game)
        self.fill()
        self.m_doc.m_background.run(self._read_icon_data, self._icon_data)  # atlas games: icons.xml, off the thread

    def _read_icon_data(self):
        """The game's icon atlases and GUID -> tile table (the games that keep icons in ``icons.xml``)."""
        read = self.m_doc.m_editor.read_game

        def exists(path: str) -> bool:
            try:
                read(path)
            except (KeyError, FileNotFoundError, OSError):
                return False
            return True

        return icons2070.load_icon_data(read, exists)

    def _icon_data(self, result) -> None:
        if not self.m_doc.m_alive or isinstance(result, Exception) or self._m_icons is None or not result[0]:
            return
        self._m_icons.use_atlases(*result)
        self.fill()

    def _key(self, row: placeables.SPlaceable) -> str:
        """Where the row's icon is: the file its asset names, else its tile of the game's icon atlas; "" for none."""
        return self._m_icons.key_for(row.guid, row.icon) if self._m_icons is not None else ""

    def fill(self) -> None:
        self.m_tree.clear()
        found = placeables.search(self.m_rows, self.m_search.text(), self.m_buildings.isChecked())
        groups = placeables.grouped(found)
        shown, budget, query = 0, ICONS, bool(self.m_search.text().strip())
        for name, rows in groups.items():
            top = QTreeWidgetItem([f"{name}  ({len(rows)})"])
            self.m_tree.addTopLevelItem(top)
            for row in rows[: max(0, SHOWN - shown)]:
                item = QTreeWidgetItem([row.name, str(row.guid), row.template])
                item.setData(0, Qt.ItemDataRole.UserRole, row)
                item.setToolTip(
                    0,
                    tr(
                        "islands.tip.name_cfg_n_variations_stands",
                        name=row.name,
                        cfg=row.cfg,
                        n=row.variations,
                        ground=_ground(row),
                    ),
                )
                if budget and self._key(row) and (query or len(groups) == 1):
                    self._set_icon(item, row)
                    budget -= 1
                elif not self._key(row):
                    item.setToolTip(0, f"{item.toolTip(0)}\n{tr('islands.no_icon_in_games_files')}")
                top.addChild(item)
                shown += 1
            top.setExpanded(query and len(groups) <= 12)
        self.m_count.setText(
            tr("islands.found_total_groups", found=len(found), total=len(self.m_rows), groups=len(groups))
        )

    def _set_icon(self, item: QTreeWidgetItem, row: placeables.SPlaceable) -> None:
        image = self._m_icons.image(self._key(row))
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

    def _show(self, row: placeables.SPlaceable) -> None:
        image = self._m_icons.image(self._key(row)) if self._key(row) else None
        if image is not None:
            self.m_preview.setPixmap(
                QPixmap.fromImage(image.scaledToHeight(64, Qt.TransformationMode.SmoothTransformation))
            )
        else:
            self.m_preview.setText(tr("islands.model_shows_under_cursor", name=row.name))

    def guid_of(self, cfg: str) -> int | None:
        """The asset whose first model is ``cfg`` (the viewer's model handed over), None when no listed asset has it."""
        key = cfg.replace("\\", "/").lower()
        return next((row.guid for row in self.m_rows if row.cfg.lower() == key), None)

    def choose(self, guid: int) -> str:
        """Pick the asset ``guid`` for the Place tool (its model made drawable first); "" or why not."""
        row = self.m_by_guid.get(int(guid))
        if row is None:
            return (
                tr("islands.asset_list_still_read")
                if not self.m_rows
                else tr("islands.asset_no_model_place", guid=guid)
            )
        doc = self.m_doc
        doc.say(tr("islands.preparing_name", name=row.name))

        def done(result):
            if not doc.m_alive:
                return
            if isinstance(result, Exception):
                doc.say(tr("islands.cannot_place_name_error", name=row.name, error=result))
                return
            doc.m_view.set_pick(result)
            doc.say(tr("islands.place_click_island_turn_shift", name=row.name))

        doc.m_background.run(lambda: self.pick_now(row.guid), done)
        return ""

    def pick_now(self, guid: int) -> dict | None:
        """The Place tool's pick for ``guid``, made drawable and with its footprint (kept once made)."""
        if guid not in self._m_picks:
            row = self.m_by_guid.get(int(guid))
            if row is None:
                return None
            editor = self.m_doc.m_editor
            out = placeables.pick(row, editor.drawable(row.cfg))
            out["footprint"] = editor.footprint_of(out["cfg"]) or editor.footprint_of(row.cfg) or None
            self._m_picks[guid] = out
        return dict(self._m_picks[guid])


def _ground(row: placeables.SPlaceable) -> str:
    return alternatives(row.terrain) if row.terrain else tr("islands.any_ground")
