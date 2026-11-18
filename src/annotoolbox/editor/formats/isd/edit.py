"""Anno 2070 editing of the island editor: an Anno 2070 island edited for Anno 2070 itself.

The island is read from its ``.isd`` (``isd2070``) and shown with its models read from the game's files
(``modelviewer.RdmModels``);
the edits are written back into the ``.isd``, which ``formats.isd.build`` puts into ``maindata/patch9.rda`` - the one
archive 2070 mounts last and without a checksum (engine-notes "Anno 2070 archives"). What it edits (objects, heights
and the logic grid, ground paint, forest, rivers, lakes, coast lines, path blockers) and what keeps the vanilla
island's: island-editor "Islands and modes". Rivers and the rest of the water: ``formats.isd.water``.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
import struct
from pathlib import Path

import numpy as np

from ....formats import isd2070
from ....formats.isd2070 import FIXED, RENDER_TRANSFORM, Node
from ....infra.i18n import tr
from ....islands import islandedits, objectedits
from ....islands.forestpick import forest_kinds, tile_pick
from ....islands.islandview import Files
from ....models import fileresolve
from ...assetmodels import Reason
from ...islandassets import finish_png, whole_file
from ...islandeditor import ALBEDO_MAX, GROUND_MAX, GROUND_TEXELS, IslandEditor
from ...modelparts import library_group
from ...modelviewer import RdmModels
from . import props as isd_props
from . import water as isd_water

EDIT_PREFIX = "2070_"  # edit files of 2070 islands: island-edits/2070_<island>.json
KEY_PREFIX = "2070:"  # an object's name in the edit file: its m_ID, which the .isd keeps
PLAYER_NONE = 15  # m_PlayerID of every object a vanilla island places
V2_UNITS = 1024  # the logic grid heights (m_HeightMap_v2; 1404: m_HeightMap): int16, a tile's mean corner height x 1024
# a terrain chunk out at sea has no heightmap (VertexResolution -1); one an edit reaches is switched
# on as most vanilla chunks are, and marked in UsedChunks (which lists exactly those with heights)
CHUNK_ON = {"VertexResolution": "4", "Flags": "131072"}
# where 2070 keeps each placeable template's objects, and what an added one carries
NATURE = {"Tree", "NatureMountainObject", "SimpleStoneObject", "Vegetation"}
SIMPLE = {"SimpleObject"}
# PropertyMesh/m_RenderParameters of props and gameplay objects: a header, then the object's own
# position, orientation (x, y, z, w), scale and a tint, the header and tint as most vanilla ones
RENDER_HEAD = bytes(16) + bytes.fromhex("0008000000000100000064000000")
RENDER_TINT = bytes.fromhex("9998983d9998983eeeecec3e0000803f")


ISLAND_JSON = 6  # the cached island data's layout: a change makes the editor read the island again
LAYERS_2070 = 24  # MAX_SPLATS_PER_CHUNK: the most layers 2070 paints in one chunk
FOREST_LAND = 0.3  # no tree is planted below this height (the forest brush skips it too, editsession)


def edit_name(source: str) -> str:
    return f"{EDIT_PREFIX}{Path(source).stem.lower()}.json"


def object_key(obj: Node) -> str:
    return f"{KEY_PREFIX}{obj.int('m_ID')}"


def _groups(root: Node):
    """(group name, its Objects node) for every object group of the island."""
    groups = root.path("m_GOPManager/Objects")
    for g in groups.children if groups is not None else []:
        objs = g.find("Objects")
        if objs is not None:
            yield g.name, objs


def _position(obj: Node) -> tuple[float, float, float]:
    x, z, y = struct.unpack("<qqq", isd2070.position_node(obj).data)
    return x / FIXED, y / FIXED, z / FIXED


def _quat(obj: Node) -> tuple[float, float, float, float]:
    nat = obj.find("Nature")
    orient = nat.find("m_Orientation") if nat is not None else None
    if orient is not None and orient.data:
        return struct.unpack("<4f", orient.data)
    rp = _render(obj)
    if rp is not None:  # a prop's tilt is only in its render parameters
        return RENDER_TRANSFORM.unpack_from(rp.data, len(RENDER_HEAD))[3:7]
    yaw = math.radians(obj.int("m_Direction") / 4096)
    return 0.0, -math.sin(yaw / 2), 0.0, math.cos(yaw / 2)


def _render(obj: Node) -> Node | None:
    rp = obj.path("PropertyMesh/m_RenderParameters")
    return rp if rp is not None and rp.data and len(rp.data) >= len(RENDER_HEAD) + RENDER_TRANSFORM.size else None


def _scale(obj: Node) -> float:
    """A tree's or rock's own scale; a prop's sits in its render parameters."""
    s = obj.path("Nature/m_Scale")
    if s is not None and s.text:
        return float(s.text)
    rp = _render(obj)
    return RENDER_TRANSFORM.unpack_from(rp.data, len(RENDER_HEAD))[-1] if rp is not None else 1.0


def _set(parent: Node, name: str, text: str | None = None, data: bytes | None = None) -> Node:
    node = parent.find(name)
    if node is None:
        node = Node(name)
        parent.children.append(node)
    node.text, node.data = text, data
    return node


def _legacy_turn(obj: Node) -> bool:
    """Older 1404 islands turn an object by an ``m_Orientation`` number of their own (unit not known): not edited."""
    return obj.find("m_Direction") is None and obj.find("m_Orientation") is not None


def _set_object(obj: Node, change: dict) -> None:
    if "position" in change:
        x, y, z = map(float, change["position"])
        isd2070.position_node(obj).data = struct.pack("<qqq", round(x * FIXED), round(z * FIXED), round(y * FIXED))
    q = tuple(map(float, change["rotation"])) if "rotation" in change else _quat(obj)
    if "rotation" in change and not _legacy_turn(obj):
        _set(obj, "m_Direction", str(round(math.degrees(islandedits._yaw(q)) * 4096)))
        nat = obj.find("Nature")
        if nat is not None:
            _set(nat, "m_Orientation", data=struct.pack("<4f", *q))
    scale = _scale(obj)
    if "scale" in change:
        s = change["scale"]
        scale = float(s[0] if isinstance(s, list) else s)
    nat = obj.find("Nature")
    if nat is not None:
        _set(nat, "m_Scale", f"{scale:.6f}")
    rp = _render(obj)
    if rp is not None:  # the game draws props from here: it moves with the object
        data = bytearray(rp.data)
        RENDER_TRANSFORM.pack_into(data, len(RENDER_HEAD), *_position(obj), *q, scale)
        rp.data = bytes(data)


def _render_block() -> Node:
    """A prop's render parameters (header, transform, tint), the transform set by ``_set_object``."""
    render = RENDER_HEAD + bytes(RENDER_TRANSFORM.size) + RENDER_TINT
    return Node("PropertyMesh", children=[Node("m_RenderParameters", data=render)])


class Island2070:
    """A 2070 island opened for writing: the parsed ``.isd`` and what edits change in it."""

    def __init__(self, data: bytes) -> None:
        self.island = isd2070.Island(data)
        self.root = self.island.root
        self.size = self.island.width
        self._next_id: int | None = None  # the first free m_ID, found when the first object is added

    def write(self) -> bytes:
        return isd2070.serialize(self.root)

    def objects(self):
        """(group name, Objects node, object) for every placed object."""
        for name, objs in _groups(self.root):
            for o in list(objs.children):
                if o.name == "Object" and isd2070.position_node(o) is not None:
                    yield name, objs, o

    def apply(self, edits: dict, types: dict) -> dict:
        """Apply an edit file; ``types``: 2070's assets (``objects2070.read_types``)."""
        report = {"objects changed": 0, "objects deleted": 0, "objects added": 0, "heights": 0, "paint": 0}
        report["unmatched"] = []
        if edits.get("clear"):
            report["cleared"] = self.clear()

        def remove(gone: list[tuple[Node, Node]]) -> None:
            for objs, o in gone:
                objs.children.remove(o)
            if gone:
                self._drop_groups({o.int("m_ID") for _, o in gone})

        ops = objectedits.ObjectOps(lambda handle, change: _set_object(handle[1], change), remove)
        objectedits.apply_changes(
            ((object_key(o), (objs, o)) for _, objs, o in self.objects()), edits.get("objects", {}), ops, report
        )
        objectedits.add_each(
            edits.get("added_objects", []),
            lambda add: self.add_object(add, types),
            lambda add: f"added {add.get('source') or add.get('cfg')}",
            report,
        )
        report["heights"] = self.apply_heights(edits.get("heights", {}))
        report["paint"] = self.apply_paint(edits.get("paint", []))
        report["trees planted"] = self.apply_forest(edits.get("forest", {}), types)
        water, stale = isd_water.apply(self.root, edits)
        report |= water
        report["unmatched"] += stale
        report["properties"] = isd_props.apply(self.root, edits.get("properties", {}))
        return report

    def clear(self) -> dict:
        """A blank island (docs/island-editor.md, "New islands"): every object, river and lake taken off; the terrain,
        the coast lines and the logic grids stay. Returns what was removed."""
        gone = [(objs, o) for _, objs, o in self.objects()]
        for objs, o in gone:
            objs.children.remove(o)
        self._drop_groups({o.int("m_ID") for _, o in gone})
        out = {"objects": len(gone)}
        for name in ("Rivers", "Lakes"):
            node = self.root.find(name)
            if node is not None and node.children:
                out[name.lower()] = len(node.children)
                node.children = []
        return out

    def apply_forest(self, forest: dict, types: dict) -> int:
        """Planted forest tiles (``"x,z"`` -> 1) as tree objects: one per tile without a tree, at its
        middle on the ground, of the kind and turn the editor drew it with (``tile_pick``). Clearing
        is deleting the tree objects (``objects``)."""
        kinds = forest_kinds(self.island, types)
        if not kinds:
            return 0
        trees = {
            (int(o.x), int(o.z))
            for o in self.island.objects()
            if (t := types.get(o.guid)) is not None and t.template == "Tree"
        }
        hm = None
        planted = 0
        for k, v in sorted(forest.items()):
            x, z = map(int, k.split(","))
            if not v or (x, z) in trees or not (0 <= x < self.size and 0 <= z < self.size):
                continue
            if hm is None:
                hm = np.asarray(self.island.heightmap(), np.float32)
            h = float(hm[z : z + 2, x : x + 2].mean())
            if h < FOREST_LAND:
                continue
            pick = tile_pick(x, z)
            guid, var = kinds[pick % len(kinds)]
            yaw = math.radians(pick % 360)
            turn = [0.0, -math.sin(yaw / 2), 0.0, math.cos(yaw / 2)]
            add = {"asset": guid, "variation": var, "position": [x + 0.5, h, z + 0.5], "rotation": turn, "scale": 1.0}
            if self.add_object(add, types):
                trees.add((x, z))
                planted += 1
        return planted

    def _drop_groups(self, ids: set[int]) -> None:
        """Object groups (pairs of IDs) that name a deleted object go with it."""
        groups = self.root.path("m_GOPManager/ObjectGroups")
        if groups is None:
            return
        groups.children = [
            g for g in groups.children if not (g.data and set(struct.unpack(f"<{len(g.data) // 4}I", g.data)) & ids)
        ]

    def add_object(self, add: dict, types: dict) -> bool:
        """A new object: ``asset`` (or ``guid``) and ``variation``, or a library model's 2070 ``source`` alone;
        ``position``, ``rotation``, ``scale``. A landscape asset is made new; anything else (a building) is a copy of an
        object of its asset on the island, else of its template, else a plain handle object of its own."""
        guid, var = add.get("asset", add.get("guid")), add.get("variation", 0)
        if guid is None and add.get("source"):  # a model several assets share: the placeable one
            guid, var = next(
                ((t.guid, k) for t in types.values() if t.template in NATURE | SIMPLE
                 for k, v in enumerate(t.variations) if v == add["source"]),
                (None, 0),
            )  # fmt: skip
        t = types.get(int(guid)) if guid is not None else None
        if t is None:
            return False
        group = "Nature" if t.template in NATURE else "Simple" if t.template in SIMPLE else None
        objs = dict(_groups(self.root)).get(group) if group else None
        if group and objs is None:
            return False
        obj = self._new_landscape(t.guid, var, group) if group else self._new_copy(t, var, types)
        if obj is None:
            return False
        objs, obj = obj if isinstance(obj, tuple) else (objs, obj)
        place = {"position": add["position"], "rotation": add.get("rotation", [0, 0, 0, 1])}
        _set_object(obj, {**place, "scale": add.get("scale", 1)})
        objs.children.append(obj)
        return True

    def _new_id(self) -> int:
        if self._next_id is None:
            self._next_id = max((o.int("m_ID") for _, _, o in self.objects()), default=0) + 1
        self._next_id += 1
        return self._next_id - 1

    def _new_landscape(self, guid: int, var: int, group: str) -> Node:
        obj = Node("Object", children=[Node("m_ID", str(self._new_id())), Node("m_GUID", str(guid))])
        if var:
            obj.children.append(Node("m_Variation", str(var)))
        obj.children += [
            Node("m_Position", data=bytes(24)),
            Node("m_PlayerID", str(PLAYER_NONE)),
            Node("m_Direction", "0"),
        ]
        if group == "Nature":
            obj.children.append(Node("Nature", children=[Node("m_Scale", "1.000000")]))
        else:
            obj.children.append(_render_block())
        return obj

    def _new_copy(self, asset, var: int, types: dict) -> tuple[Node, Node]:
        """(the objects list, a copy of the island's first object of ``asset``, else of its template, else a plain
        handle object) with an ID of its own and the variation set."""
        same = [(objs, o) for _, objs, o in self.objects() if o.int("m_GUID") == asset.guid]
        kind = [(objs, o) for _, objs, o in self.objects()
                if (t := types.get(o.int("m_GUID"))) is not None and t.template == asset.template]  # fmt: skip
        if same or kind:
            objs, sample = (same or kind)[0]
            obj = copy.deepcopy(sample)
        else:
            objs = self._handle_objects()
            obj = Node("Object", children=[Node("m_ID", "0"), Node("m_GUID", "0"), Node("m_Position", data=bytes(24)),
                                           Node("m_PlayerID", str(PLAYER_NONE)), Node("m_Direction", "0"),
                                           _render_block()])  # fmt: skip
        obj.find("m_ID").text = str(self._new_id())
        obj.find("m_GUID").text = str(asset.guid)
        old = obj.find("m_Variation")
        if var:
            if old is None:
                obj.children.insert(2, old := Node("m_Variation"))
            old.text = str(var)
        elif old is not None:
            obj.children.remove(old)
        return objs, obj

    def _handle_objects(self) -> Node:
        """The ``Handle`` group's object list (the buildings'), made when the island has none."""
        objs = dict(_groups(self.root)).get("Handle")
        if objs is None:
            groups = self.root.path("m_GOPManager/Objects")
            groups.children.append(Node("Handle", children=[Node("Objects")]))
            objs = dict(_groups(self.root))["Handle"]
        return objs

    # the ground
    def _chunks(self):
        """(chunk x, chunk z, tiles per chunk, element) for every terrain chunk."""
        t = self.root.path("Terrain")
        cm = t.path("ChunkMap")
        cw = cm.int("Width")
        per = t.int("TileCountX") // cw
        for k, el in enumerate(cm.findall("Element")):
            yield k % cw, k // cw, per, el

    @staticmethod
    def _sample_index(cx: int, cz: int, per: int, w: int):
        """The island corners (z, x index arrays) nearest to a chunk's w x w samples."""
        idx = np.floor(np.arange(w) * per / (w - 1) + 0.5).astype(int)
        return cz * per + idx, cx * per + idx

    @staticmethod
    def _at_samples(grid, cx: int, cz: int, per: int, w: int):
        """A corner grid at a chunk's w x w samples, between corners interpolated (fine chunks)."""
        from ....islands.islandterrain import bilinear

        pos = np.arange(w) * per / (w - 1)
        return bilinear(grid, cx * per + pos, cz * per + pos)

    def apply_heights(self, heights: dict) -> int:
        """Raise or lower tile corners (``"z,x"`` -> world units): in every chunk's heightmap that
        holds the corner, and in the logic grid of the tiles around it. A chunk without heights
        (open sea) is switched on first, at the heights the editor showed it at."""
        n = self.size + 1
        delta = islandedits.height_grid(heights, n, n).astype(np.float32)
        if not delta.any():
            return 0
        shown = None
        for cx, cz, per, el in self._chunks():
            data = el.path("HeightMap/Data")
            if data is None or data.data is None:
                zs, xs = self._sample_index(cx, cz, per, per + 1)
                if not delta[np.ix_(zs, xs)].any():
                    continue
                if shown is None:
                    shown = np.asarray(self.island.heightmap(), np.float32)
                data = self._switch_on(el, cx, cz, shown[np.ix_(zs, xs)])
            w = el.path("HeightMap").int("Width")
            d = self._at_samples(delta, cx, cz, per, w)
            if d.any():
                vals = np.frombuffer(data.data, "<f4").reshape(w, w) + d
                data.data = vals.astype("<f4").tobytes()
        manager = self.root.path("m_GOPManager/m_GRIDManager")
        grids = (manager.find("m_HeightMap_v2"), manager.find("m_HeightMap")) if manager is not None else ()
        v2 = next((n for n in grids if n is not None and n.data), None)
        if v2 is not None and len(v2.data) == self.size * self.size * 2:
            grid = np.frombuffer(v2.data, "<i2").reshape(self.size, self.size).astype(np.int32)
            mean = (delta[:-1, :-1] + delta[1:, :-1] + delta[:-1, 1:] + delta[1:, 1:]) / 4
            grid = np.clip(grid + np.round(mean * V2_UNITS).astype(np.int32), -32768, 32767)
            v2.data = grid.astype("<i2").tobytes()
        return int(np.count_nonzero(delta))

    def _switch_on(self, el: Node, cx: int, cz: int, heights) -> Node:
        """Give an open-sea chunk heights and the sea bed's layer; returns its heightmap data."""
        w = len(heights)
        for name, value in CHUNK_ON.items():
            _set(el, name, value)
        hm = Node("HeightMap", children=[Node("Width", str(w)), Node("Data", data=heights.astype("<f4").tobytes())])
        el.children.insert(1, hm)  # after VertexResolution, as in a vanilla chunk
        layer = Node("AlphaMap", children=[Node("Width", str(w)), Node("Data", data=bytes([255]) * w * w)])
        el.children.append(Node("TexIndexData", children=[Node("TextureIndex", "0"), layer]))
        used = self.root.path("UsedChunks")
        if used is not None:
            grid = bytearray(used.path("m_BitGrid").data)
            stride = used.int("m_IntXSize") * 4  # bytes per row
            grid[cz * stride + cx // 8] |= 1 << (cx % 8)
            used.path("m_BitGrid").data = bytes(grid)
        return hm.find("Data")

    def apply_paint(self, strokes: list[dict]) -> int:
        """Lay painted strokes (``material``: a ground texture GUID) into the chunks' texture
        layers: the painted layer gains what the stroke covers and the layers above it lose it.
        A chunk the stroke reaches gets the layer on top if it had none."""
        from ....islands.islandground import stroke_cover, stroke_material

        done = 0
        for st in strokes:
            guid = stroke_material(st)
            # the brush samples texel centres; corners sit half a tile earlier
            shifted = {**st, "points": [[p[0] + 0.5, p[1] + 0.5] for p in st.get("points", [])]}
            cover = stroke_cover(shifted, self.size + 1, 1)
            if guid < 0 or not cover.any():
                continue
            for cx, cz, per, el in self._chunks():
                layers = el.findall("TexIndexData")
                if not layers:
                    continue
                w = layers[0].path("AlphaMap").int("Width")
                zs, xs = self._sample_index(cx, cz, per, w)
                c = cover[np.ix_(zs, xs)]
                if not c.any():
                    continue
                mine = next((t for t in layers if t.int("TextureIndex") == guid), None)
                if mine is None:
                    mine = Node(
                        "TexIndexData",
                        children=[
                            Node("TextureIndex", str(guid)),
                            Node("AlphaMap", children=[Node("Width", str(w)), Node("Data", data=bytes(w * w))]),
                        ],
                    )
                    el.children.insert(el.children.index(layers[-1]) + 1, mine)
                    layers.append(mine)
                above = False
                for t in layers:
                    data = t.path("AlphaMap/Data")
                    a = np.frombuffer(data.data, np.uint8).reshape(w, w) / 255.0
                    if t is mine:
                        a = a + (1 - a) * c
                        above = True
                    elif above:
                        a = a * (1 - c)
                    else:
                        continue
                    data.data = np.clip(np.round(a * 255), 0, 255).astype(np.uint8).tobytes()
            done += 1
        return done


class Island2070Editor(RdmModels, IslandEditor):
    """The editor's island for Anno 2070 editing: the ``.isd`` itself, its objects drawn with the models their
    assets name, read from the game's files. The same shape as a 117 island."""

    def __init__(self, tree2070, source: str, cache: Path, edits: Path | None = None) -> None:
        from ....islands.objects2070 import read_types
        from ....islands.terrain2070 import splat_ids

        self.source = source
        data = tree2070.read_bytes(source)
        self.isl = isd2070.Island(data)
        self.isl.order = splat_ids(tree2070)
        self.size = self.isl.width
        self.files = Files(_NoGame())
        self.types = read_types(tree2070)
        self._setup(cache, hashlib.sha1(data).hexdigest()[:16], edits, {"source": source}, tree2070)
        self._editor = ([], {})  # no 117 editor-only assets on a 2070 island
        self._ground: dict | None = None  # 2070's ground textures by GUID
        self._rgb: dict = {}  # their pixels, for the baked ground image

    def _view_cfg(self, source: str) -> str:
        """The config the view draws for a 2070 model: the model itself, "" for a marker (``model_cfg``)."""
        return self.model_cfg(source)

    def objects(self) -> list[dict]:
        out = []
        for group, _, o in self._objects():
            t = self.types.get(o.int("m_GUID"))
            var = o.find("m_Variation")
            k = int(var.text) if var is not None and var.text else 0
            variations = t.variations if t else []
            source = variations[k if 0 <= k < len(variations) else 0] if variations else None
            cfg = self._view_cfg(source) if source and self.tree2070.exists(source) else ""
            out.append(
                {
                    "key": object_key(o),
                    "guid": o.int("m_GUID"),
                    "cfg": cfg,
                    "marker": None if cfg else (t.template if t else group),
                    "label": t.name if t else f"guid {o.int('m_GUID')}",
                    "template": t.template if t else group,
                    "clone": True,  # placed again as a copy of an object of its asset (``Island2070.add_object``)
                    "variation": k,
                    "position": list(_position(o)),
                    "rotation": list(_quat(o)),
                    "scale": _scale(o),
                    **({} if cfg else {"unresolved": self._why_no_model(t, source)}),
                }
            )
        return out

    def _why_no_model(self, asset, source: str | None) -> str:
        """Why an object is a marker: no such asset, an asset without a model, a model file that is not there, or what
        ``model_cfg`` found wrong with the config."""
        if asset is None:
            return Reason.NO_ASSET
        if not source:
            return Reason.NO_MODEL
        if not self.tree2070.exists(source):
            return f"{Reason.UNREADABLE}: " + tr("islands.source_not_game", source=source)
        return self._model_state()[0].get(source) or Reason.EMPTY

    def _objects(self):
        for name, objs in _groups(self.isl.root):
            for o in objs.children:
                if o.name == "Object" and isd2070.position_node(o) is not None:
                    yield name, objs, o

    def splines(self) -> list[dict]:
        """2070's rivers (float points) and lakes (outlines at their water height), for the view's water."""
        net = isd_water.network(self.isl.root)
        rivers = [{"kind": "River", **r} for r in net["rivers"]]
        lakes = [
            {"kind": "Lake", "points": [[x, k["level"], z] for x, z in k["points"]], "widths": []} for k in net["lakes"]
        ]
        return rivers + lakes

    def island(self) -> dict:
        hm = np.asarray(self.isl.heightmap(), "<f4")
        objects = self.objects()
        return {
            "mode": "2070",
            "width": int(hm.shape[0]),
            "samples_per_tile": 1,
            "heights": base64.b64encode(hm.tobytes()).decode("ascii"),
            "splines": self.splines(),
            "water": isd_water.network(self.isl.root),
            "blockers": isd_water.blockers_json(self.isl.root),
            "objects": objects,
            "footprints": self.footprints(objects),
            "props": [],
            # trees are objects here; the forest brush plants these kinds (forest_kinds)
            "forest": {
                "tiles": [],
                "models": [
                    cfg
                    for g, v in forest_kinds(self.isl, self.types)
                    if (cfg := self._view_cfg(self.types[g].variations[v]))
                ],
            },
        }

    def _island_cache(self) -> Path:
        return self._cached(f"island2070|{ISLAND_JSON}|{self._fingerprint}", ".json")

    def _island_doc(self) -> dict:
        return {**self.island(), "model_notes": self.model_notes()}

    def island_json(self) -> bytes:
        data = super().island_json()
        if not getattr(self, "_notes_read", False):
            self._say_notes(json.loads(data))
        return data

    def island_data(self) -> dict:
        data = super().island_data()
        self._say_notes(data)
        return data

    def _say_notes(self, data: dict) -> None:
        """The notes travel inside the island's data: a cached island still says them, once."""
        if not getattr(self, "_notes_read", False):
            self._notes_read = True
            self.notes.extend(data.get("model_notes", []))

    def ground_textures(self) -> dict:
        from ....islands.terrain2070 import read_ground_textures

        if self._ground is None:
            self._ground = {t.guid: t for t in read_ground_textures(self.tree2070)}
        return self._ground

    def materials(self) -> list[dict]:
        """2070's ground textures (painted by GUID), the island's own first."""
        from ....islands.terrain2070 import texture_scale

        repetition = texture_scale(self.tree2070)
        used = {idx for _, _, _, layers in self.isl.texture_chunks() for idx, _ in layers}
        textures = sorted(self.ground_textures().values(), key=lambda t: (t.guid not in used, t.name.lower()))
        return [
            {
                "id": t.guid,
                "name": t.name,
                "diffuse": t.diffuse,
                "normal": t.normal,
                "repetition": repetition,
            }
            for t in textures
        ]

    def vanilla_materials(self) -> list[dict]:
        return []

    def ground_layers(self, texels: int | None = None) -> tuple[list[int], np.ndarray]:
        """Each ground texture's visible weight: every chunk paints its layers over one another in
        its own order, so a layer keeps its alpha times what the layers above it leave."""
        from ....islands.islandterrain import bilinear

        texels = texels or max(1, min(GROUND_TEXELS, GROUND_MAX // max(1, self.size)))
        n = self.size * texels
        seen = dict.fromkeys(i for _, _, _, ls in self.isl.texture_chunks() for i, _ in ls)
        ids = [i for i in seen if i in self.ground_textures()]  # not the grass and bush placement layers
        index = {i: k for k, i in enumerate(ids)}
        weights = np.zeros((len(ids), n, n), np.float32)
        tile = (np.arange(n) + 0.5) / texels
        for cx, cz, per, layers in self.isl.texture_chunks():
            x0, z0 = cx * per * texels, cz * per * texels
            xs, zs = slice(x0, x0 + per * texels), slice(z0, z0 + per * texels)
            w = len(layers[0][1])
            gx = (tile[xs] - cx * per) * (w - 1) / per
            gz = (tile[zs] - cz * per) * (w - 1) / per
            left = np.ones((len(tile[zs]), len(tile[xs])), np.float32)
            for idx, grid in reversed(layers):  # the top layer takes its share first
                if idx not in index:
                    continue
                a = bilinear(np.asarray(grid, np.float32) / 255, gx, gz) * left
                weights[index[idx], zs, xs] += a
                left -= a
        return ids, weights

    def ground2070(self) -> tuple[dict, bytes]:
        """The ground as 2070 paints it (rendering/rendering-2070.md 2.2): per chunk its layers in its
        own paint order, as indices into ``ground()``'s materials (255: none), and their alpha maps,
        17 samples a chunk side over the tiles' corners. Returns (info, alphas: [layer rank]
        [chunk row * 17 + sample][chunk column * 17 + sample], uint8)."""
        index = {m["id"]: k for k, m in enumerate(self.ground()["materials"])}
        chunks = list(self.isl.texture_chunks())
        side = max((max(cx, cz) for cx, cz, _, _ in chunks), default=0) + 1
        per = chunks[0][2] if chunks else 16
        slots = np.full((side, side, LAYERS_2070), 255, np.uint8)
        alpha = np.zeros((LAYERS_2070, side * 17, side * 17), np.uint8)
        for cx, cz, _, layers in chunks:
            rank = 0
            for idx, grid in layers:
                if idx not in index or rank >= LAYERS_2070:
                    continue  # the grass and bush placement layers paint no ground
                g = np.asarray(grid, np.uint8)
                g = g[:: (len(g) - 1) // 16, :: (len(g) - 1) // 16]  # a finer chunk's every other sample
                slots[cz, cx, rank] = index[idx]
                alpha[rank, cz * 17 : cz * 17 + 17, cx * 17 : cx * 17 + 17] = g
                rank += 1
        info = {"side": side, "per": per, "layers": LAYERS_2070, "slots": slots.ravel().tolist()}
        return info, alpha.tobytes()

    def _texture_rgb(self, guid: int, size: int = 128):
        """A ground texture's pixels for the baked image; a missing or unreadable one is its mean colour."""
        if guid not in self._rgb:
            from PIL import Image

            t = self.ground_textures().get(guid)
            try:
                img = Image.open(io.BytesIO(self.tree2070.read_bytes(t.diffuse))).convert("RGB")
                self._rgb[guid] = np.asarray(img.resize((size, size)), np.float32) / 255
            except (AttributeError, OSError, KeyError, ValueError) as exc:
                self.notes.append(
                    tr(
                        "islands.ground_texture_drawn_mean_colour",
                        guid=guid,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                rgb = np.array(t.mean_color if t else (128, 128, 128), np.float32) / 255
                self._rgb[guid] = np.broadcast_to(rgb, (size, size, 3))
        return self._rgb[guid]

    def albedo(self, texels: int) -> np.ndarray:
        """The ground from above, the textures tiled and weighted by ``ground_layers``."""
        from ....islands.terrain2070 import texture_scale

        rep = texture_scale(self.tree2070)
        ids, weights = self.ground_layers(texels)
        n = weights.shape[1]
        tile = (np.arange(n) + 0.5) / texels
        out = np.zeros((n, n, 3), np.float32)
        for k, idx in enumerate(ids):
            tex = self._texture_rgb(idx)
            u = ((tile / rep) % 1 * tex.shape[1]).astype(int)
            v = ((tile / rep) % 1 * tex.shape[0]).astype(int)
            out += tex[np.ix_(v, u)] * weights[k][..., None]
        return out

    def albedo_png(self) -> bytes:
        from PIL import Image

        texels = max(1, min(8, ALBEDO_MAX // max(1, self.size)))
        out = self._cached(f"albedo2070|{self._fingerprint}|{texels}", ".png")
        with self._lock(out):
            if not out.is_file():
                with whole_file(out) as scratch:
                    Image.fromarray(np.clip(self.albedo(texels) * 255, 0, 255).astype(np.uint8)).save(scratch, "PNG")
        return out.read_bytes()

    def read_game(self, path: str) -> bytes:
        return self.tree2070.read_bytes(path)

    def placeable_sources(self) -> tuple[list[bytes], str]:
        """The asset files the asset editor reads for this game (``gamefiles.LAYOUTS``), in its order."""
        import fnmatch

        from ....assetedit.gamefiles import LAYOUTS

        layout = LAYOUTS[self.game.key if getattr(self, "game", None) else "anno2070"]
        paths = sorted(p for p in self.tree2070.iter_paths("", suffix=".xml") if "config/" in p)
        files = [self.tree2070.read_bytes(p) for pattern in layout.assets for p in paths if fnmatch.fnmatch(p, pattern)]
        templates = self.tree2070.read_text(layout.templates) if self.tree2070.exists(layout.templates) else ""
        return files, templates

    def drawable(self, cfg: str) -> str:
        """The config the view draws for a game model (``model_cfg``); raises when it cannot be drawn."""
        return self.convert2070(cfg.lower())

    def library(self) -> dict:
        """2070's landscape assets an island can carry (``NATURE`` and ``SIMPLE`` templates)."""
        a2070 = []
        for t in sorted(self.types.values(), key=lambda t: t.name.lower()):
            if t.template in NATURE or t.template in SIMPLE:
                for k, v in enumerate(t.variations):
                    if self.tree2070.exists(v):
                        name = t.name if len(t.variations) == 1 else f"{t.name} #{k + 1}"
                        a2070.append(
                            {
                                "name": name,
                                "template": t.template,
                                "source": v,
                                "asset": t.guid,
                                "variation": k,
                                "group": library_group(v),
                            }
                        )
        return {"117": [], "2070": a2070, "editor": []}

    def convert2070(self, source: str) -> str:
        """The config the view draws for a library model: the 2070 model itself, read from the game's files."""
        if not self.model_cfg(source):
            raise FileNotFoundError(
                tr("islands.source_cannot_drawn_why", source=source, why=self._model_state()[0].get(source))
            )
        return source

    def texture_png(self, psd: str, normal: bool = False, opaque: bool = False, alpha: bool = False) -> bytes:
        if psd.lower().startswith("data/anno2070/"):  # a made-drawable model's
            return super().texture_png(psd, normal, opaque, alpha)
        from PIL import Image

        out = self._cached(f"2070|{psd}|{normal}|{opaque}|{alpha}", ".png")
        with self._lock(out):
            if not out.is_file():
                try:  # decoded in process, every format the old games ship (fileresolve.decode)
                    with whole_file(out) as scratch:
                        Image.fromarray(fileresolve.decode(self.tree2070.read_bytes(psd)), "RGBA").save(scratch, "PNG")
                        finish_png(scratch, normal, opaque, alpha)
                except (OSError, KeyError, ValueError) as exc:  # listed in the Checks tab, drawn as missing
                    self.texture_failure(psd, exc)
                    raise
        return out.read_bytes()


class _NoGame:
    """The view's files come from its own folder only."""

    def read(self, path: str) -> bytes:
        raise FileNotFoundError(path)

    def iter_paths(self, prefix: str = ""):
        return iter(())
