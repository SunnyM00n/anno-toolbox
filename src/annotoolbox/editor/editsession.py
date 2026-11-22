"""The island editor's edit document without a window: the island as the editor opened it
(``IslandEditor.island()``), the edit file its tools build (``islandedits`` format, what the island build applies),
every tool's operation and an undo/redo stack. The PC application (``desktopeditor``, ``desktopview``) draws it
and calls these; the tests drive it on synthetic islands. Positions are Anno's (left-handed); only the view
mirrors x. What the panels list of it: ``editlists``; the slot check is an optional part (``features``).
"""

from __future__ import annotations

import base64
import copy
import math
import re
from dataclasses import dataclass, field

import numpy as np

from ..infra.i18n import tr
from ..islands import islandedits, sessionsettings
from ..suite.undo import CUndoStack, SCommand
from . import brushes
from .cellbrush import CActiveMapEdits, CAreaEdits, CEnvironmentEdits, CFordEdits, CHerdEdits, CSurfEdits
from .editchange import SChange, copy_json, num, yaw_quat
from .modelparts import library_group
from .sessionpath import CPathEdits, moved_outline
from .sessionterrain import FOREST_LAND, CTerrainEdits
from .tintbrush import CTintEdits

GRASS_LIMIT = 200  # a prop model placed more often than this is scatter (the grass layer)
FOLIAGE = re.compile(
    r"/(trees?|dead_?trees?|leaftree|bush(es)?|grass|vegetation|plants?|foliage|flowers?|shrubs?)(/|_)", re.I
)
LAYERS = ("objects", "markers", "props", "grass", "forest")
CARRIED = ("source", "asset", "variation", "plain")  # placement keys a copy (Ctrl+D, paste) keeps of its original


def group_key(item: dict) -> str:
    return item.get("cfg") or f"guid:{item.get('guid')}"


def game_of(island: dict, *, kind: str = "") -> str:
    """The game ("anno117", "anno2070", ...) whose rules and assets an island follows: its own ``game``, else the
    document's ``kind`` when that names a game, else 2070 for a 2070-format island, else 117."""
    if island.get("game"):
        return str(island["game"])
    if kind.startswith("anno"):
        return kind
    return "anno2070" if island.get("mode") == "2070" else "anno117"


def label_of(item: dict) -> str:
    return item.get("label") or item.get("name") or (item.get("cfg") or "").split("/")[-1].removesuffix(".cfg")


def type_of(item: dict) -> str:
    """A model's category, from its folder as the library groups it; an island that keeps its objects in folders
    of its own (a 2205 session) names the folder."""
    if item.get("folder_path"):
        return item["folder_path"]
    if item.get("marker"):
        return tr("islands.editor_only_marker", marker=item["marker"])
    return library_group(item.get("cfg") or "")


def is_foliage(item: dict) -> bool:
    return item.get("template") in ("Tree", "Vegetation") or bool(FOLIAGE.search(item.get("cfg") or ""))


@dataclass(eq=False)
class SEntry:
    """One thing on the island the tools act on: a placed object, a prop or a forest tree."""

    kind: str  # object | prop | tree
    key: str
    base: dict  # the island's item (cfg, marker, label, guid, template, position, rotation, scale)
    added: dict | None = None  # its record in added_objects / added_props (the same dict)
    position: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    scale: object = 1.0
    color: list | None = None  # a prop's tint (r, g, b, a); None for what has none
    visible: bool = True  # not deleted (a tree: its tile planted)
    alive: bool = True  # an added entry taken out again
    hidden: bool = False  # hidden in the view only
    layer: str = "objects"
    batch: tuple | None = None
    slot: int = -1
    tile: str | None = None

    @property
    def cfg(self) -> str:
        return self.base.get("cfg") or ""

    @property
    def marker(self) -> str | None:
        return self.base.get("marker")

    @property
    def shown(self) -> bool:
        return self.alive and self.visible and not self.hidden


class CEditSession(CTerrainEdits):
    """An opened island and its edits. ``island``: what ``IslandEditor.island()`` returns; ``edits``: the
    edit file as ``islandedits.load`` reads it."""

    def __init__(self, island: dict, edits: dict | None = None, is_readonly: bool = False) -> None:
        self.m_island = island
        self.m_edits = {**islandedits.empty(), **copy_json(edits or {})}
        self.m_readonly = is_readonly or bool(island.get("readonly"))
        self.m_anno2070_editing = island.get("mode") == "2070"
        width, samples_per_tile = island["width"], island["samples_per_tile"]
        self.m_width_samples, self.m_samples_per_tile = width, samples_per_tile
        self.m_size_tiles = (width - 1) // samples_per_tile
        self.m_step_tiles = 1.0 / samples_per_tile
        self.m_base_heights = (
            np.frombuffer(base64.b64decode(island["heights"]), "<f4").reshape(width, width).astype(np.float32)
        )
        self.m_heights = self.m_base_heights.copy()
        self.m_corner_deltas = np.zeros(
            (self.m_size_tiles + 1, self.m_size_tiles + 1), np.float32
        )  # corner differences (z, x)
        # a corner off the island is left out, as the build does
        self.m_corner_deltas[:] = islandedits.height_grid(
            self.m_edits.get("heights", {}), self.m_size_tiles + 1, self.m_size_tiles + 1
        )
        self._redraw(0, 0, self.m_size_tiles, self.m_size_tiles)
        self.m_water_net = island.get("water")
        self.m_undo = CUndoStack()
        self.m_listeners: list = []
        self.m_entries: list[SEntry] = []
        self.m_batches: dict[tuple, list[SEntry]] = {}
        self._m_brush = None
        for object_item in island.get("objects", []):
            self._add_entry("object", object_item)
        for prop_item in island.get("props", []):
            self._add_entry("prop", prop_item)
        for kind, rows in (("object", self.m_edits["added_objects"]), ("prop", self.m_edits["added_props"])):
            for added in rows:
                self._add_entry(kind, self._added_item(added), added)
        forest = island.get("forest") or {"tiles": [], "models": []}
        self.m_forest_models: list[str] = list(forest.get("models", []))
        self.m_forest_base = {f"{x},{z}" for x, z in forest.get("tiles", [])}
        self.m_trees: dict[str, SEntry] = {}
        for tile in sorted(self.forest_tiles()):
            self._set_tree(tile, True)
        self.m_ground_ids: list[int] = []
        self.m_weights: np.ndarray | None = None  # uint8 [layer, z, x], the paint preview
        self._m_wbox: tuple | None = None  # the weight texels a stroke's stamps touched since the last redraw
        self.m_paint_materials: dict[int, dict] = {}
        self.m_own_set: list[tuple[int, str]] | None = None  # the island's material set in its list order
        self.m_path = CPathEdits(self)  # coast lines and path blocker cells (2070-format islands)
        self.m_active = CActiveMapEdits(self)  # the active map's cells (117 and 1800 islands)
        self.m_ford = CFordEdits(self)  # the ford grid's cells (the same islands)
        self.m_area = CAreaEdits(self)  # the construction area's tiles (the same islands)
        self.m_herd = CHerdEdits(self)  # the tiles the herds roam
        self.m_tint = CTintEdits(self)  # the tint strokes (laid into the tint pages by the build)
        self.m_environment = CEnvironmentEdits(self)  # the environment zones (the same islands)
        self.m_surf = CSurfEdits(self)  # the sea map's coast waves (117 and 1800 islands)
        self.m_falloff = brushes.DEFAULT  # the height brush's curve (``brushes.CURVES``)
        self.m_snap = (
            None  # the tools' ``snapping.SSnap``: the view's group gives it the user's start values on first use
        )

    # entries
    @staticmethod
    def _added_item(added: dict) -> dict:
        item = {**added, "key": f"added:{id(added)}", "label": added.get("name")}
        if added.get("guid") and (
            added.get("cfg") or added.get("clone")
        ):  # a copy of an object of the island (``objectclones``)
            item["clone"] = True
            item["marker"] = None if added.get("cfg") else "NoModel"
        elif added.get("guid"):
            item["marker"] = added.get("template")
        return item

    def _layer(self, kind: str, item: dict) -> str:
        if item.get("marker"):
            return "markers"
        return {"object": "objects", "tree": "forest"}.get(kind, "props")

    def _add_entry(self, kind: str, item: dict, added: dict | None = None) -> SEntry:
        entry = SEntry(kind, item["key"], item, added, layer=self._layer(kind, item))
        change = None if added is not None or kind == "tree" else self._edits_for(kind).get(item["key"])
        values = {**item, **change} if change else item
        entry.position = [float(value) for value in values["position"]]
        entry.rotation = [float(value) for value in values.get("rotation", (0, 0, 0, 1))]
        scale = values.get("scale", 1.0)
        entry.scale = (
            [float(value) for value in scale] if isinstance(scale, list) else float(scale if scale is not None else 1.0)
        )
        color = values.get("color")
        entry.color = [float(value) for value in color] if color else None
        entry.visible = not (change and change.get("deleted"))
        self.m_entries.append(entry)
        self._join_batch(entry)
        return entry

    def _join_batch(self, entry: SEntry) -> None:
        key = (entry.layer, entry.marker if entry.marker else entry.cfg)
        rows = self.m_batches.setdefault(key, [])
        entry.batch, entry.slot = key, len(rows)
        rows.append(entry)

    def finish_loading(self) -> None:
        """Props placed more often than ``GRASS_LIMIT`` go to the grass layer (no shadows, hidden from afar)."""
        for key in [
            batch_key
            for batch_key in self.m_batches
            if batch_key[0] == "props" and len(self.m_batches[batch_key]) > GRASS_LIMIT
        ]:
            rows = self.m_batches.pop(key)
            for i, entry in enumerate(rows):
                entry.layer, entry.batch, entry.slot = "grass", ("grass", key[1]), i
            self.m_batches[("grass", key[1])] = rows

    def _edits_for(self, kind: str) -> dict:
        return self.m_edits["objects"] if kind == "object" else self.m_edits["props"]

    def record_of(self, entry: SEntry):
        return copy_json(entry.added if entry.added is not None else self._edits_for(entry.kind).get(entry.key))

    def _state(self, entry: SEntry) -> tuple:
        return (
            self.record_of(entry),
            list(entry.position),
            list(entry.rotation),
            copy.copy(entry.scale),
            entry.visible,
            copy_json(entry.color),
        )

    def _restore(self, entry: SEntry, state: tuple) -> None:
        record, entry.position, entry.rotation, entry.scale, entry.visible, entry.color = (
            copy_json(state[0]),
            list(state[1]),
            list(state[2]),
            copy.copy(state[3]),
            state[4],
            copy_json(state[5]),
        )
        if entry.added is not None:
            entry.added.clear()
            entry.added.update(record or {})
        elif record is None:
            self._edits_for(entry.kind).pop(entry.key, None)
        else:
            self._edits_for(entry.kind)[entry.key] = record

    @staticmethod
    def _scale_record(entry: SEntry):
        """An entry's scale as the edit file keeps it: a prop's per axis; an object's one number, unless the island
        stores it per axis (an 1800 object of the Prop group), which stays whole."""
        if isinstance(entry.scale, list):
            return list(entry.scale)
        return [entry.scale] * 3 if entry.kind == "prop" else entry.scale

    def _record(self, entry: SEntry) -> None:
        values = {
            "position": list(entry.position),
            "rotation": list(entry.rotation),
            "scale": self._scale_record(entry),
        }
        if entry.color is not None:
            values["color"] = list(entry.color)
        if entry.added is not None:
            entry.added.update(values)
        else:
            self._edits_for(entry.kind)[entry.key] = values

    def _notify(self, change: SChange) -> None:
        for listener in list(self.m_listeners):
            listener(change)

    def _push(self, label: str, undo, redo) -> None:
        self.m_undo.push(SCommand(label, undo, redo))

    # object tools
    def begin_transform(self, entries: list[SEntry]) -> list[tuple]:
        """The state a drag starts from (``end_transform`` makes it one change)."""
        return [(entry, self._state(entry)) for entry in entries]

    def set_transform(self, entry: SEntry, position=None, rotation=None, scale=None) -> None:
        """Move, turn or scale an entry now (a drag's step: no undo of its own)."""
        if entry.kind == "tree" or self.m_readonly:
            return  # a forest tree stays on its tile
        if position is not None:
            entry.position = [float(value) for value in position]
        if rotation is not None:
            quaternion = np.asarray(rotation, np.float64)
            entry.rotation = (quaternion / (np.linalg.norm(quaternion) or 1.0)).tolist()
        if scale is not None:
            entry.scale = (
                [float(value) for value in scale] if isinstance(scale, (list, tuple, np.ndarray)) else float(scale)
            )
        self._record(entry)
        self._notify(SChange(entries={entry}))

    def end_transform(self, start: list[tuple], label: str = "") -> bool:
        after = [(entry, self._state(entry)) for entry, _ in start]
        if all(
            after_item[1][1:4] == start_state[1:4] for after_item, (_, start_state) in zip(after, start, strict=True)
        ):
            for entry, state in start:  # nothing moved: the drag's steps leave no record in the edit file
                self._restore(entry, state)
            return False

        def apply(states):
            for entry, restored_state in states:
                self._restore(entry, restored_state)
            self._notify(SChange(entries={entry for entry, _ in states}))

        self._push(label or tr("islands.move_lower"), lambda: apply(start), lambda: apply(after))
        return True

    def transform(self, entry: SEntry, label: str = "", **changes) -> None:
        """Move, turn or scale as one change (the property panel)."""
        start = self.begin_transform([entry])
        self.set_transform(entry, **changes)
        self.end_transform(start, label)

    def set_color(self, entry: SEntry, color) -> None:
        """Tint a prop (r, g, b, a, each 0 to 1 and over) as one change."""
        if entry.kind != "prop" or self.m_readonly or not entry.alive:
            return
        start = self._state(entry)
        entry.color = [round(float(component), 4) for component in color]
        self._record(entry)
        after = self._state(entry)
        self._push(
            tr("common.colour_lower"), lambda: self._apply_state(entry, start), lambda: self._apply_state(entry, after)
        )
        self._notify(SChange(entries={entry}))

    def delete(self, entry: SEntry) -> None:
        """Take an entry off the island (a tree, a placed object, one of the island's own), one undo step."""
        if self.m_readonly or not entry.alive:
            return
        if entry.kind == "tree":
            tile, before = entry.tile, self.m_edits["forest"].get(entry.tile)
            self._plant(tile, False)

            def back():
                self._forest_value(tile, before)
                self._set_tree(tile, True)
                self._notify(SChange(entries={self.m_trees[tile]}, forest=True))

            self._push(
                tr("islands.delete_tree"), back, lambda: (self._plant(tile, False), self._notify(SChange(forest=True)))
            )
            self._notify(SChange(entries={entry}, forest=True))
            return
        if entry.added is not None:
            rows = self.m_edits["added_objects" if entry.kind == "object" else "added_props"]
            row_index = next(i for i, row in enumerate(rows) if row is entry.added)

            def take():
                rows.remove(entry.added)
                entry.alive = False
                self._notify(SChange(entries={entry}, structure=True))

            def put():
                rows.insert(min(row_index, len(rows)), entry.added)
                entry.alive = True
                self._notify(SChange(entries={entry}, structure=True))

            take()
            self._push(tr("islands.delete"), put, take)
            return
        start = self._state(entry)
        self._edits_for(entry.kind)[entry.key] = {"deleted": True}
        entry.visible = False
        after = self._state(entry)
        self._push(
            tr("islands.delete"), lambda: self._apply_state(entry, start), lambda: self._apply_state(entry, after)
        )
        self._notify(SChange(entries={entry}))

    def _apply_state(self, entry: SEntry, state: tuple) -> None:
        self._restore(entry, state)
        self._notify(SChange(entries={entry}))

    def add_record(self, kind: str, record: dict, item: dict | None = None, label: str = "") -> SEntry:
        rows = self.m_edits["added_objects" if kind == "object" else "added_props"]
        rows.append(record)
        entry = self._add_entry(kind, item or self._added_item(record), record)

        def take():
            rows.remove(record)
            entry.alive = False
            self._notify(SChange(entries={entry}, structure=True))

        def put():
            rows.append(record)
            entry.alive = True
            self._notify(SChange(entries={entry}, structure=True))

        self._push(label or tr("islands.place_lower"), take, put)
        self._notify(SChange(entries={entry}, structure=True))
        return entry

    def duplicate(self, entry: SEntry) -> SEntry | None:
        """A copy two tiles off, as an added entry (Ctrl+D)."""
        if self.m_readonly or entry.kind == "tree" or not entry.alive:
            return None
        if entry.base.get("clone"):
            keep = {
                "guid": entry.base.get("guid"),
                "template": entry.base.get("template"),
                "name": label_of(entry.base),
            }
            keep |= {"cfg": entry.cfg} if entry.cfg else {}
            keep |= {"clone": True, "variation": entry.base.get("variation", 0)}
        elif entry.marker:
            keep = {"guid": entry.base.get("guid"), "template": entry.marker, "name": label_of(entry.base)}
            if entry.cfg:
                keep["cfg"] = entry.cfg
        else:
            keep = {"cfg": entry.cfg}
        keep |= {
            field_name: entry.base[field_name]
            for field_name in CARRIED
            if entry.base.get(field_name) is not None and field_name not in keep
        }
        record = {
            **keep,
            "position": [entry.position[0] + 2, entry.position[1], entry.position[2] + 2],
            "rotation": list(entry.rotation),
        }
        record["scale"] = self._scale_record(entry)
        if entry.color is not None:
            record["color"] = list(entry.color)
        item = {**record, "marker": entry.marker, "label": entry.base.get("label"), "key": f"added:{id(record)}"}
        if entry.base.get("clone"):
            item["clone"] = True
        if entry.base.get("template"):
            item["template"] = entry.base["template"]
        return self.add_record(entry.kind, record, item, tr("islands.duplicate_lower"))

    def place(self, pick: dict, position, yaw_rad: float = 0.0) -> SEntry | None:
        """Place a library pick at ``position`` (Anno), turned by ``yaw``: a model (``cfg``, with its 2070
        ``source`` / ``asset`` / ``variation``), an editor-only asset (``guid``) or a forest tree (``tree``)."""
        if self.m_readonly:
            return None
        if pick.get("tree") is not None:
            x, z = math.floor(position[0]), math.floor(position[2])
            tile_key = f"{x},{z}"
            if (tile_key in self.m_trees and self.m_trees[tile_key].visible) or self.height_at(
                x + 0.5, z + 0.5
            ) < FOREST_LAND:
                return None  # a tile with a tree, or water: the build plants no tree there either
            before = self.m_edits["forest"].get(tile_key)
            self._plant(tile_key, True)

            def back():
                self._forest_value(tile_key, before)
                self._set_tree(tile_key, False)
                self._notify(SChange(entries={self.m_trees[tile_key]}, forest=True))

            self._push(
                tr("islands.plant_tree"),
                back,
                lambda: (self._plant(tile_key, True), self._notify(SChange(forest=True))),
            )
            self._notify(SChange(entries={self.m_trees[tile_key]}, forest=True, structure=True))
            return self.m_trees[tile_key]
        rotation = yaw_quat(yaw_rad)
        position_values = [float(value) for value in position]
        if pick.get("guid"):
            record = {
                "guid": pick["guid"],
                "template": pick["template"],
                "name": pick["name"],
                "position": position_values,
                "rotation": rotation,
            }
            item = {**record, "marker": pick["template"], "label": pick["name"], "key": f"added:{id(record)}"}
            if pick.get("cfg"):  # a model that is placed by copying an object of the island (1800)
                record["cfg"], record["scale"] = pick["cfg"], 1
                item.update(cfg=pick["cfg"], marker=None, clone=True, scale=1)
            if pick.get("clone"):  # a copy of the island's own object: its variation goes with it
                record |= {"clone": True, "variation": pick.get("variation", 0), "scale": 1}
                record |= (
                    {"plain": True} if pick.get("plain") else {}
                )  # from the asset list: a plain object may stand in
                item.update(
                    clone=True, variation=record["variation"], scale=1, **({"plain": True} if "plain" in record else {})
                )
                item["marker"] = None if pick.get("cfg") else "NoModel"
            return self.add_record("object", record, item)
        if not pick.get("cfg"):
            return None
        record = {"cfg": pick["cfg"], "position": position_values, "rotation": rotation, "scale": 1}
        if pick.get("source"):
            record["source"] = pick["source"]  # island_test.py converts it again on a clean build
        if pick.get("asset") is not None:
            record["asset"], record["variation"] = pick["asset"], pick.get("variation", 0)
        item = {**record, "key": f"added:{id(record)}", "template": pick.get("template")}
        return self.add_record("object", record, item)

    # rivers and lakes
    def river(self, river_index: int) -> dict:
        return self.m_edits["rivers"].get(str(river_index)) or self.m_water_net["rivers"][river_index]

    def lake_level(self, lake_index: int) -> float:
        level_edit = self.m_edits["lakes"].get(str(lake_index))
        return float(level_edit) if level_edit is not None else float(self.m_water_net["lakes"][lake_index]["level"])

    def lake_outline(self, lake_index: int) -> list:
        """A lake's outline points (x, z in tiles): the edit's, else the island's own."""
        edited = self.m_edits["lake_outlines"].get(str(lake_index))
        return edited["points"] if edited else self.m_water_net["lakes"][lake_index]["points"]

    def move_lake_outline_point(self, lake_index: int, point_index: int, position) -> None:
        """Move a lake outline's point over the ground (x, z of ``position``); the build redraws the lake's outline
        at its level (``groundedit.fill_lake``)."""
        self.m_edits["lake_outlines"][str(lake_index)] = moved_outline(
            self.lake_outline(lake_index), point_index, position
        )
        self._notify(SChange(water=True))

    def water_snapshot(self) -> dict:
        snapshot = {"rivers": copy_json(self.m_edits["rivers"]), "lakes": copy_json(self.m_edits["lakes"])}
        snapshot["lake_outlines"] = copy_json(self.m_edits["lake_outlines"])
        return snapshot | {
            key: copy_json(self.m_edits[key]) for key in ("coast", "blocker_shapes") if key in self.m_edits
        }

    def _water_put(self, snapshot: dict) -> None:
        self.m_edits["rivers"], self.m_edits["lakes"] = copy_json(snapshot["rivers"]), copy_json(snapshot["lakes"])
        self.m_edits["lake_outlines"] = copy_json(snapshot["lake_outlines"])
        for key in ("coast", "blocker_shapes"):
            if key in snapshot:
                self.m_edits[key] = copy_json(snapshot[key])
        self._notify(SChange(water=True))

    def slot_rows(self) -> list[dict]:
        """The island's slots by asset: ``guid``, ``name``, ``listed`` (what the island's file lists), ``now`` (the
        slot objects standing after the edits, which is what the build lists) and the asset's generator values
        ``count`` and ``all`` (None where it has none)."""
        listed = {int(guid): slot_count for guid, slot_count in self.m_island.get("slot_list", {}).items()}
        known = self.m_island.get("slot_generator", {})
        rows = {
            guid: {
                "guid": guid,
                "name": known.get(str(guid), {}).get("name", ""),
                "listed": slot_count,
                "now": 0,
                "count": known.get(str(guid), {}).get("count"),
                "all": known.get(str(guid), {}).get("all"),
            }
            for guid, slot_count in listed.items()
        }
        for entry in self.m_entries:
            row = rows.get(entry.base.get("guid"))
            if row is not None and entry.kind == "object":
                row["name"] = row["name"] or label_of(entry.base)
                row["now"] += int(entry.alive and entry.visible)
        return list(rows.values())

    def cells(self, grid: str | None):
        """The grid the cell tool edits: ``"ford"``, ``"environment"``, ``"area"`` (``"areakind"``: with another
        area's id), ``"herd"``, anything else the active map."""
        return {
            "ford": self.m_ford,
            "environment": self.m_environment,
            "area": self.m_area,
            "areakind": self.m_area,  # the same grid, painted with another area's id
            "herd": self.m_herd,
            "tint": self.m_tint,
        }.get(grid, self.m_active)

    def blockers_changed(self) -> None:
        self._notify(SChange())

    def end_water(self, before: dict, label: str) -> None:
        after = self.water_snapshot()
        if after != before:
            self._push(label, lambda: self._water_put(before), lambda: self._water_put(after))

    def edit_river(self, river_index: int, change) -> None:
        river = self.river(river_index)
        changed = {"points": [list(point) for point in river["points"]], "widths": list(river["widths"])}
        change(changed)
        self.m_edits["rivers"][str(river_index)] = changed
        self._notify(SChange(water=True))

    def move_river_point(self, river_index: int, point_index: int, position) -> None:
        self.edit_river(
            river_index,
            lambda river: river["points"].__setitem__(point_index, [float(coordinate) for coordinate in position]),
        )

    def set_river_width(self, river_index: int, point_index: int, width: float) -> None:
        self.edit_river(river_index, lambda river: river["widths"].__setitem__(point_index, num(width)))

    def set_lake_level(self, lake_index: int, level: float) -> None:
        self.m_edits["lakes"][str(lake_index)] = num(round(level * 100) / 100)
        self._notify(SChange(water=True))

    def delete_river_point(self, river_index: int, point_index: int) -> bool:
        if len(self.river(river_index)["points"]) <= 2:
            return False  # two stay
        before = self.water_snapshot()

        def drop(river):
            del river["points"][point_index]
            del river["widths"][point_index]

        self.edit_river(river_index, drop)
        self.end_water(before, tr("islands.river_point"))
        return True

    def insert_river_point(self, river_index: int, point_index: int) -> int:
        """A point halfway to the next one (Ctrl+D); returns its index."""
        before = self.water_snapshot()

        def ins(river):
            point, next_point = (
                river["points"][point_index],
                river["points"][min(point_index + 1, len(river["points"]) - 1)],
            )
            river["points"].insert(
                point_index + 1, [(here + there) / 2 for here, there in zip(point, next_point, strict=True)]
            )
            river["widths"].insert(point_index + 1, river["widths"][point_index])

        self.edit_river(river_index, ins)
        self.end_water(before, tr("islands.river_point"))
        return point_index + 1

    # Anno 117 editing, saving
    def session_value(self, key: str):
        """A session setting (``sessionsettings.FIELDS``): the edit's, else the island's own, None when it has none."""
        return self.m_edits.get("session", {}).get(key, self.m_island.get("session", {}).get(key))

    def set_session(self, key: str, value) -> None:
        """Change a session setting, one undo step; a value equal to the island's own takes the edit away. A key the
        game has no setting for is none, and a read-only island takes no edit."""
        if self.m_readonly or key not in sessionsettings.FIELDS:
            return
        value = copy_json(value)  # a tuple is the list the file holds, and the caller's list stays the caller's
        before = copy_json(self.m_edits.get("session", {}))

        def put(settings: dict) -> None:
            if settings:
                self.m_edits["session"] = copy_json(settings)
            else:
                self.m_edits.pop("session", None)

        after = {name: setting for name, setting in before.items() if name != key}
        if value != self.m_island.get("session", {}).get(key):
            after[key] = value
        put(after)
        self._push(tr("islands.session_settings"), lambda: put(before), lambda: put(after))

    def set_target(self, target: str) -> None:
        before = self.m_edits.get("target")

        def put(value):
            if value is None:
                self.m_edits.pop("target", None)
            else:
                self.m_edits["target"] = value

        put(target)
        self._push(tr("islands.save_as"), lambda: put(before), lambda: put(target))

    def save(self, editor) -> None:
        """Write the edit file (``IslandEditor.save_edits``)."""
        editor.save_edits(self.m_edits)
        self.m_undo.mark_clean()
