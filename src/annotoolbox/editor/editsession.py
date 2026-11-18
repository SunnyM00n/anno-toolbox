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
from ..suite.undo import Command, UndoStack
from . import brushes
from .cellbrush import ActiveMapEdits, AreaEdits, EnvironmentEdits, FordEdits, HerdEdits, SurfEdits
from .editchange import Change, copy_json, num, yaw_quat
from .modelparts import library_group
from .sessionpath import PathEdits, moved_outline
from .sessionterrain import FOREST_LAND, TerrainEdits
from .tintbrush import TintEdits

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
class Entry:
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


class EditSession(TerrainEdits):
    """An opened island and its edits. ``island``: what ``IslandEditor.island()`` returns; ``edits``: the
    edit file as ``islandedits.load`` reads it."""

    def __init__(self, island: dict, edits: dict | None = None, readonly: bool = False) -> None:
        self.island = island
        self.edits = {**islandedits.empty(), **copy_json(edits or {})}
        self.readonly = readonly or bool(island.get("readonly"))
        self.anno2070_editing = island.get("mode") == "2070"
        w, spt = island["width"], island["samples_per_tile"]
        self.width, self.spt = w, spt
        self.size = (w - 1) // spt
        self.step = 1.0 / spt
        self.base_heights = np.frombuffer(base64.b64decode(island["heights"]), "<f4").reshape(w, w).astype(np.float32)
        self.heights = self.base_heights.copy()
        self.D = np.zeros((self.size + 1, self.size + 1), np.float32)  # corner differences (z, x)
        # a corner off the island is left out, as the build does
        self.D[:] = islandedits.height_grid(self.edits.get("heights", {}), self.size + 1, self.size + 1)
        self._redraw(0, 0, self.size, self.size)
        self.water_net = island.get("water")
        self.undo = UndoStack()
        self.listeners: list = []
        self.entries: list[Entry] = []
        self.batches: dict[tuple, list[Entry]] = {}
        self._brush = None
        for o in island.get("objects", []):
            self._add_entry("object", o)
        for p in island.get("props", []):
            self._add_entry("prop", p)
        for kind, rows in (("object", self.edits["added_objects"]), ("prop", self.edits["added_props"])):
            for a in rows:
                self._add_entry(kind, self._added_item(a), a)
        forest = island.get("forest") or {"tiles": [], "models": []}
        self.forest_models: list[str] = list(forest.get("models", []))
        self.forest_base = {f"{x},{z}" for x, z in forest.get("tiles", [])}
        self.trees: dict[str, Entry] = {}
        for k in sorted(self.forest_tiles()):
            self._set_tree(k, True)
        self.ground_ids: list[int] = []
        self.weights: np.ndarray | None = None  # uint8 [layer, z, x], the paint preview
        self._wbox: tuple | None = None  # the weight texels a stroke's stamps touched since the last redraw
        self.paint_materials: dict[int, dict] = {}
        self.own_set: list[tuple[int, str]] | None = None  # the island's material set in its list order
        self.path = PathEdits(self)  # coast lines and path blocker cells (2070-format islands)
        self.active = ActiveMapEdits(self)  # the active map's cells (117 and 1800 islands)
        self.ford = FordEdits(self)  # the ford grid's cells (the same islands)
        self.area = AreaEdits(self)  # the construction area's tiles (the same islands)
        self.herd = HerdEdits(self)  # the tiles the herds roam
        self.tint = TintEdits(self)  # the tint strokes (laid into the tint pages by the build)
        self.environment = EnvironmentEdits(self)  # the environment zones (the same islands)
        self.surf = SurfEdits(self)  # the sea map's coast waves (117 and 1800 islands)
        self.falloff = brushes.DEFAULT  # the height brush's curve (``brushes.CURVES``)

    # entries
    @staticmethod
    def _added_item(a: dict) -> dict:
        item = {**a, "key": f"added:{id(a)}", "label": a.get("name")}
        if a.get("guid") and (a.get("cfg") or a.get("clone")):  # a copy of an object of the island (``objectclones``)
            item["clone"] = True
            item["marker"] = None if a.get("cfg") else "NoModel"
        elif a.get("guid"):
            item["marker"] = a.get("template")
        return item

    def _layer(self, kind: str, item: dict) -> str:
        if item.get("marker"):
            return "markers"
        return {"object": "objects", "tree": "forest"}.get(kind, "props")

    def _add_entry(self, kind: str, item: dict, added: dict | None = None) -> Entry:
        e = Entry(kind, item["key"], item, added, layer=self._layer(kind, item))
        change = None if added is not None or kind == "tree" else self._edits_for(kind).get(item["key"])
        t = {**item, **change} if change else item
        e.position = [float(v) for v in t["position"]]
        e.rotation = [float(v) for v in t.get("rotation", (0, 0, 0, 1))]
        s = t.get("scale", 1.0)
        e.scale = [float(v) for v in s] if isinstance(s, list) else float(s if s is not None else 1.0)
        col = t.get("color")
        e.color = [float(v) for v in col] if col else None
        e.visible = not (change and change.get("deleted"))
        self.entries.append(e)
        self._join_batch(e)
        return e

    def _join_batch(self, e: Entry) -> None:
        key = (e.layer, e.marker if e.marker else e.cfg)
        rows = self.batches.setdefault(key, [])
        e.batch, e.slot = key, len(rows)
        rows.append(e)

    def finish_loading(self) -> None:
        """Props placed more often than ``GRASS_LIMIT`` go to the grass layer (no shadows, hidden from afar)."""
        for key in [k for k in self.batches if k[0] == "props" and len(self.batches[k]) > GRASS_LIMIT]:
            rows = self.batches.pop(key)
            for i, e in enumerate(rows):
                e.layer, e.batch, e.slot = "grass", ("grass", key[1]), i
            self.batches[("grass", key[1])] = rows

    def _edits_for(self, kind: str) -> dict:
        return self.edits["objects"] if kind == "object" else self.edits["props"]

    def record_of(self, e: Entry):
        return copy_json(e.added if e.added is not None else self._edits_for(e.kind).get(e.key))

    def _state(self, e: Entry) -> tuple:
        return (
            self.record_of(e),
            list(e.position),
            list(e.rotation),
            copy.copy(e.scale),
            e.visible,
            copy_json(e.color),
        )

    def _restore(self, e: Entry, state: tuple) -> None:
        rec, e.position, e.rotation, e.scale, e.visible, e.color = (
            copy_json(state[0]),
            list(state[1]),
            list(state[2]),
            copy.copy(state[3]),
            state[4],
            copy_json(state[5]),
        )
        if e.added is not None:
            e.added.clear()
            e.added.update(rec or {})
        elif rec is None:
            self._edits_for(e.kind).pop(e.key, None)
        else:
            self._edits_for(e.kind)[e.key] = rec

    @staticmethod
    def _scale_record(e: Entry):
        """An entry's scale as the edit file keeps it: a prop's per axis; an object's one number, unless the island
        stores it per axis (an 1800 object of the Prop group), which stays whole."""
        if isinstance(e.scale, list):
            return list(e.scale)
        return [e.scale] * 3 if e.kind == "prop" else e.scale

    def _record(self, e: Entry) -> None:
        t = {"position": list(e.position), "rotation": list(e.rotation), "scale": self._scale_record(e)}
        if e.color is not None:
            t["color"] = list(e.color)
        if e.added is not None:
            e.added.update(t)
        else:
            self._edits_for(e.kind)[e.key] = t

    def _notify(self, change: Change) -> None:
        for f in list(self.listeners):
            f(change)

    def _push(self, label: str, undo, redo) -> None:
        self.undo.push(Command(label, undo, redo))

    # object tools
    def begin_transform(self, entries: list[Entry]) -> list[tuple]:
        """The state a drag starts from (``end_transform`` makes it one change)."""
        return [(e, self._state(e)) for e in entries]

    def set_transform(self, e: Entry, position=None, rotation=None, scale=None) -> None:
        """Move, turn or scale an entry now (a drag's step: no undo of its own)."""
        if e.kind == "tree" or self.readonly:
            return  # a forest tree stays on its tile
        if position is not None:
            e.position = [float(v) for v in position]
        if rotation is not None:
            q = np.asarray(rotation, np.float64)
            e.rotation = (q / (np.linalg.norm(q) or 1.0)).tolist()
        if scale is not None:
            e.scale = [float(v) for v in scale] if isinstance(scale, (list, tuple, np.ndarray)) else float(scale)
        self._record(e)
        self._notify(Change(entries={e}))

    def end_transform(self, start: list[tuple], label: str = "") -> bool:
        after = [(e, self._state(e)) for e, _ in start]
        if all(a[1][1:4] == s[1:4] for a, (_, s) in zip(after, start, strict=True)):
            for e, state in start:  # nothing moved: the drag's steps leave no record in the edit file
                self._restore(e, state)
            return False

        def apply(states):
            for e, st in states:
                self._restore(e, st)
            self._notify(Change(entries={e for e, _ in states}))

        self._push(label or tr("islands.move_lower"), lambda: apply(start), lambda: apply(after))
        return True

    def transform(self, e: Entry, label: str = "", **kw) -> None:
        """Move, turn or scale as one change (the property panel)."""
        start = self.begin_transform([e])
        self.set_transform(e, **kw)
        self.end_transform(start, label)

    def set_color(self, e: Entry, color) -> None:
        """Tint a prop (r, g, b, a, each 0 to 1 and over) as one change."""
        if e.kind != "prop" or self.readonly or not e.alive:
            return
        start = self._state(e)
        e.color = [round(float(v), 4) for v in color]
        self._record(e)
        after = self._state(e)
        self._push(tr("common.colour_lower"), lambda: self._apply_state(e, start), lambda: self._apply_state(e, after))
        self._notify(Change(entries={e}))

    def delete(self, e: Entry) -> None:
        """Take an entry off the island (a tree, a placed object, one of the island's own), one undo step."""
        if self.readonly or not e.alive:
            return
        if e.kind == "tree":
            k, before = e.tile, self.edits["forest"].get(e.tile)
            self._plant(k, False)

            def back():
                self._forest_value(k, before)
                self._set_tree(k, True)
                self._notify(Change(entries={self.trees[k]}, forest=True))

            self._push(
                tr("islands.delete_tree"), back, lambda: (self._plant(k, False), self._notify(Change(forest=True)))
            )
            self._notify(Change(entries={e}, forest=True))
            return
        if e.added is not None:
            rows = self.edits["added_objects" if e.kind == "object" else "added_props"]
            at = next(i for i, r in enumerate(rows) if r is e.added)

            def take():
                rows.remove(e.added)
                e.alive = False
                self._notify(Change(entries={e}, structure=True))

            def put():
                rows.insert(min(at, len(rows)), e.added)
                e.alive = True
                self._notify(Change(entries={e}, structure=True))

            take()
            self._push(tr("islands.delete"), put, take)
            return
        start = self._state(e)
        self._edits_for(e.kind)[e.key] = {"deleted": True}
        e.visible = False
        after = self._state(e)
        self._push(tr("islands.delete"), lambda: self._apply_state(e, start), lambda: self._apply_state(e, after))
        self._notify(Change(entries={e}))

    def _apply_state(self, e: Entry, st: tuple) -> None:
        self._restore(e, st)
        self._notify(Change(entries={e}))

    def add_record(self, kind: str, record: dict, item: dict | None = None, label: str = "") -> Entry:
        rows = self.edits["added_objects" if kind == "object" else "added_props"]
        rows.append(record)
        e = self._add_entry(kind, item or self._added_item(record), record)

        def take():
            rows.remove(record)
            e.alive = False
            self._notify(Change(entries={e}, structure=True))

        def put():
            rows.append(record)
            e.alive = True
            self._notify(Change(entries={e}, structure=True))

        self._push(label or tr("islands.place_lower"), take, put)
        self._notify(Change(entries={e}, structure=True))
        return e

    def duplicate(self, e: Entry) -> Entry | None:
        """A copy two tiles off, as an added entry (Ctrl+D)."""
        if self.readonly or e.kind == "tree" or not e.alive:
            return None
        if e.base.get("clone"):
            keep = {"guid": e.base.get("guid"), "template": e.base.get("template"), "name": label_of(e.base)}
            keep |= {"cfg": e.cfg} if e.cfg else {}
            keep |= {"clone": True, "variation": e.base.get("variation", 0)}
        elif e.marker:
            keep = {"guid": e.base.get("guid"), "template": e.marker, "name": label_of(e.base)}
            if e.cfg:
                keep["cfg"] = e.cfg
        else:
            keep = {"cfg": e.cfg}
        keep |= {k: e.base[k] for k in CARRIED if e.base.get(k) is not None and k not in keep}
        rec = {**keep, "position": [e.position[0] + 2, e.position[1], e.position[2] + 2], "rotation": list(e.rotation)}
        rec["scale"] = self._scale_record(e)
        if e.color is not None:
            rec["color"] = list(e.color)
        item = {**rec, "marker": e.marker, "label": e.base.get("label"), "key": f"added:{id(rec)}"}
        if e.base.get("clone"):
            item["clone"] = True
        if e.base.get("template"):
            item["template"] = e.base["template"]
        return self.add_record(e.kind, rec, item, tr("islands.duplicate_lower"))

    def place(self, pick: dict, position, yaw: float = 0.0) -> Entry | None:
        """Place a library pick at ``position`` (Anno), turned by ``yaw``: a model (``cfg``, with its 2070
        ``source`` / ``asset`` / ``variation``), an editor-only asset (``guid``) or a forest tree (``tree``)."""
        if self.readonly:
            return None
        if pick.get("tree") is not None:
            x, z = math.floor(position[0]), math.floor(position[2])
            k = f"{x},{z}"
            if (k in self.trees and self.trees[k].visible) or self.height_at(x + 0.5, z + 0.5) < FOREST_LAND:
                return None  # a tile with a tree, or water: the build plants no tree there either
            before = self.edits["forest"].get(k)
            self._plant(k, True)

            def back():
                self._forest_value(k, before)
                self._set_tree(k, False)
                self._notify(Change(entries={self.trees[k]}, forest=True))

            self._push(
                tr("islands.plant_tree"), back, lambda: (self._plant(k, True), self._notify(Change(forest=True)))
            )
            self._notify(Change(entries={self.trees[k]}, forest=True, structure=True))
            return self.trees[k]
        q = yaw_quat(yaw)
        pos = [float(v) for v in position]
        if pick.get("guid"):
            rec = {
                "guid": pick["guid"],
                "template": pick["template"],
                "name": pick["name"],
                "position": pos,
                "rotation": q,
            }
            item = {**rec, "marker": pick["template"], "label": pick["name"], "key": f"added:{id(rec)}"}
            if pick.get("cfg"):  # a model that is placed by copying an object of the island (1800)
                rec["cfg"], rec["scale"] = pick["cfg"], 1
                item.update(cfg=pick["cfg"], marker=None, clone=True, scale=1)
            if pick.get("clone"):  # a copy of the island's own object: its variation goes with it
                rec |= {"clone": True, "variation": pick.get("variation", 0), "scale": 1}
                rec |= {"plain": True} if pick.get("plain") else {}  # from the asset list: a plain object may stand in
                item.update(
                    clone=True, variation=rec["variation"], scale=1, **({"plain": True} if "plain" in rec else {})
                )
                item["marker"] = None if pick.get("cfg") else "NoModel"
            return self.add_record("object", rec, item)
        if not pick.get("cfg"):
            return None
        rec = {"cfg": pick["cfg"], "position": pos, "rotation": q, "scale": 1}
        if pick.get("source"):
            rec["source"] = pick["source"]  # island_test.py converts it again on a clean build
        if pick.get("asset") is not None:
            rec["asset"], rec["variation"] = pick["asset"], pick.get("variation", 0)
        item = {**rec, "key": f"added:{id(rec)}", "template": pick.get("template")}
        return self.add_record("object", rec, item)

    # rivers and lakes
    def river(self, k: int) -> dict:
        return self.edits["rivers"].get(str(k)) or self.water_net["rivers"][k]

    def lake_level(self, k: int) -> float:
        v = self.edits["lakes"].get(str(k))
        return float(v) if v is not None else float(self.water_net["lakes"][k]["level"])

    def lake_outline(self, k: int) -> list:
        """A lake's outline points (x, z in tiles): the edit's, else the island's own."""
        edited = self.edits["lake_outlines"].get(str(k))
        return edited["points"] if edited else self.water_net["lakes"][k]["points"]

    def move_lake_outline_point(self, k: int, i: int, position) -> None:
        """Move a lake outline's point over the ground (x, z of ``position``); the build redraws the lake's outline
        at its level (``groundedit.fill_lake``)."""
        self.edits["lake_outlines"][str(k)] = moved_outline(self.lake_outline(k), i, position)
        self._notify(Change(water=True))

    def water_snapshot(self) -> dict:
        snap = {"rivers": copy_json(self.edits["rivers"]), "lakes": copy_json(self.edits["lakes"])}
        snap["lake_outlines"] = copy_json(self.edits["lake_outlines"])
        return snap | {k: copy_json(self.edits[k]) for k in ("coast", "blocker_shapes") if k in self.edits}

    def _water_put(self, snap: dict) -> None:
        self.edits["rivers"], self.edits["lakes"] = copy_json(snap["rivers"]), copy_json(snap["lakes"])
        self.edits["lake_outlines"] = copy_json(snap["lake_outlines"])
        for key in ("coast", "blocker_shapes"):
            if key in snap:
                self.edits[key] = copy_json(snap[key])
        self._notify(Change(water=True))

    def slot_rows(self) -> list[dict]:
        """The island's slots by asset: ``guid``, ``name``, ``listed`` (what the island's file lists), ``now`` (the
        slot objects standing after the edits, which is what the build lists) and the asset's generator values
        ``count`` and ``all`` (None where it has none)."""
        listed = {int(g): n for g, n in self.island.get("slot_list", {}).items()}
        known = self.island.get("slot_generator", {})
        rows = {
            g: {
                "guid": g,
                "name": known.get(str(g), {}).get("name", ""),
                "listed": n,
                "now": 0,
                "count": known.get(str(g), {}).get("count"),
                "all": known.get(str(g), {}).get("all"),
            }
            for g, n in listed.items()
        }
        for e in self.entries:
            row = rows.get(e.base.get("guid"))
            if row is not None and e.kind == "object":
                row["name"] = row["name"] or label_of(e.base)
                row["now"] += int(e.alive and e.visible)
        return list(rows.values())

    def cells(self, grid: str | None):
        """The grid the cell tool edits: ``"ford"``, ``"environment"``, ``"area"`` (``"areakind"``: with another
        area's id), ``"herd"``, anything else the active map."""
        return {
            "ford": self.ford,
            "environment": self.environment,
            "area": self.area,
            "areakind": self.area,  # the same grid, painted with another area's id
            "herd": self.herd,
            "tint": self.tint,
        }.get(grid, self.active)

    def blockers_changed(self) -> None:
        self._notify(Change())

    def end_water(self, before: dict, label: str) -> None:
        after = self.water_snapshot()
        if after != before:
            self._push(label, lambda: self._water_put(before), lambda: self._water_put(after))

    def edit_river(self, k: int, change) -> None:
        r = self.river(k)
        c = {"points": [list(p) for p in r["points"]], "widths": list(r["widths"])}
        change(c)
        self.edits["rivers"][str(k)] = c
        self._notify(Change(water=True))

    def move_river_point(self, k: int, i: int, position) -> None:
        self.edit_river(k, lambda r: r["points"].__setitem__(i, [float(v) for v in position]))

    def set_river_width(self, k: int, i: int, width: float) -> None:
        self.edit_river(k, lambda r: r["widths"].__setitem__(i, num(width)))

    def set_lake_level(self, k: int, level: float) -> None:
        self.edits["lakes"][str(k)] = num(round(level * 100) / 100)
        self._notify(Change(water=True))

    def delete_river_point(self, k: int, i: int) -> bool:
        if len(self.river(k)["points"]) <= 2:
            return False  # two stay
        before = self.water_snapshot()

        def drop(r):
            del r["points"][i]
            del r["widths"][i]

        self.edit_river(k, drop)
        self.end_water(before, tr("islands.river_point"))
        return True

    def insert_river_point(self, k: int, i: int) -> int:
        """A point halfway to the next one (Ctrl+D); returns its index."""
        before = self.water_snapshot()

        def ins(r):
            a, b = r["points"][i], r["points"][min(i + 1, len(r["points"]) - 1)]
            r["points"].insert(i + 1, [(u + v) / 2 for u, v in zip(a, b, strict=True)])
            r["widths"].insert(i + 1, r["widths"][i])

        self.edit_river(k, ins)
        self.end_water(before, tr("islands.river_point"))
        return i + 1

    # Anno 117 editing, saving
    def session_value(self, key: str):
        """A session setting (``sessionsettings.FIELDS``): the edit's, else the island's own, None when it has none."""
        return self.edits.get("session", {}).get(key, self.island.get("session", {}).get(key))

    def set_session(self, key: str, value) -> None:
        """Change a session setting, one undo step; a value equal to the island's own takes the edit away. A key the
        game has no setting for is none, and a read-only island takes no edit."""
        if self.readonly or key not in sessionsettings.FIELDS:
            return
        value = copy_json(value)  # a tuple is the list the file holds, and the caller's list stays the caller's
        before = copy_json(self.edits.get("session", {}))

        def put(settings: dict) -> None:
            if settings:
                self.edits["session"] = copy_json(settings)
            else:
                self.edits.pop("session", None)

        after = {k: v for k, v in before.items() if k != key}
        if value != self.island.get("session", {}).get(key):
            after[key] = value
        put(after)
        self._push(tr("islands.session_settings"), lambda: put(before), lambda: put(after))

    def set_target(self, target: str) -> None:
        before = self.edits.get("target")

        def put(v):
            if v is None:
                self.edits.pop("target", None)
            else:
                self.edits["target"] = v

        put(target)
        self._push(tr("islands.save_as"), lambda: put(before), lambda: put(target))

    def save(self, editor) -> None:
        """Write the edit file (``IslandEditor.save_edits``)."""
        editor.save_edits(self.edits)
        self.undo.mark_clean()
