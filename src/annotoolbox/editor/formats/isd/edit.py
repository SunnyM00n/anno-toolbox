"""Anno 2070 editing of the island editor: an Anno 2070 island edited for Anno 2070 itself.

The island is read from its ``.isd`` (``isd2070``) and shown with its models read from the game's files
(``modelviewer.CRdmModels``);
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
from ....formats.isd2070 import FIXED, RENDER_TRANSFORM, SNode
from ....infra.i18n import tr
from ....islands import islandedits, objectedits
from ....islands.forestpick import forest_kinds, tile_pick
from ....islands.islandview import CFiles
from ....models import fileresolve
from ....sources import streetcells2070, streetgrid2070
from ...assetmodels import CReason
from ...islandassets import finish_png, whole_file
from ...islandeditor import ALBEDO_MAX, GROUND_MAX, GROUND_TEXELS, CIslandEditor
from ...modelparts import library_group
from ...modelviewer import CRdmModels
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


ISLAND_JSON = 7  # the cached island data's layout: a change makes the editor read the island again
LAYERS_2070 = 24  # MAX_SPLATS_PER_CHUNK: the most layers 2070 paints in one chunk
FOREST_LAND = 0.3  # no tree is planted below this height (the forest brush skips it too, editsession)


def edit_name(source: str) -> str:
    return f"{EDIT_PREFIX}{Path(source).stem.lower()}.json"


def object_key(object_node: SNode) -> str:
    return f"{KEY_PREFIX}{object_node.int('m_ID')}"


def _groups(root: SNode):
    """(group name, its Objects node) for every object group of the island."""
    groups = root.path("m_GOPManager/Objects")
    for group in groups.children if groups is not None else []:
        objs = group.find("Objects")
        if objs is not None:
            yield group.name, objs


def _position(object_node: SNode) -> tuple[float, float, float]:
    x, z, y = struct.unpack("<qqq", isd2070.position_node(object_node).data)
    return x / FIXED, y / FIXED, z / FIXED


def _quat(object_node: SNode) -> tuple[float, float, float, float]:
    nat = object_node.find("Nature")
    orient = nat.find("m_Orientation") if nat is not None else None
    if orient is not None and orient.data:
        return struct.unpack("<4f", orient.data)
    render_node = _render(object_node)
    if render_node is not None:  # a prop's tilt is only in its render parameters
        return RENDER_TRANSFORM.unpack_from(render_node.data, len(RENDER_HEAD))[3:7]
    yaw = math.radians(object_node.int("m_Direction") / 4096)
    return 0.0, -math.sin(yaw / 2), 0.0, math.cos(yaw / 2)


def _render(object_node: SNode) -> SNode | None:
    render_node = object_node.path("PropertyMesh/m_RenderParameters")
    return (
        render_node
        if render_node is not None
        and render_node.data
        and len(render_node.data) >= len(RENDER_HEAD) + RENDER_TRANSFORM.size
        else None
    )


def _scale(object_node: SNode) -> float:
    """A tree's or rock's own scale; a prop's sits in its render parameters."""
    scale_node = object_node.path("Nature/m_Scale")
    if scale_node is not None and scale_node.text:
        return float(scale_node.text)
    render_node = _render(object_node)
    return RENDER_TRANSFORM.unpack_from(render_node.data, len(RENDER_HEAD))[-1] if render_node is not None else 1.0


def _set(parent: SNode, name: str, text: str | None = None, data: bytes | None = None) -> SNode:
    node = parent.find(name)
    if node is None:
        node = SNode(name)
        parent.children.append(node)
    node.text, node.data = text, data
    return node


def _legacy_turn(object_node: SNode) -> bool:
    """Older 1404 islands turn an object by an ``m_Orientation`` number of their own (unit not known): not edited."""
    return object_node.find("m_Direction") is None and object_node.find("m_Orientation") is not None


def _set_object(object_node: SNode, change: dict) -> None:
    if "position" in change:
        x, y, z = map(float, change["position"])
        isd2070.position_node(object_node).data = struct.pack(
            "<qqq", round(x * FIXED), round(z * FIXED), round(y * FIXED)
        )
    quaternion = tuple(map(float, change["rotation"])) if "rotation" in change else _quat(object_node)
    if "rotation" in change and not _legacy_turn(object_node):
        _set(object_node, "m_Direction", str(round(math.degrees(islandedits._yaw(quaternion)) * 4096)))
        nat = object_node.find("Nature")
        if nat is not None:
            _set(nat, "m_Orientation", data=struct.pack("<4f", *quaternion))
    scale = _scale(object_node)
    if "scale" in change:
        scale_value = change["scale"]
        scale = float(scale_value[0] if isinstance(scale_value, list) else scale_value)
    nat = object_node.find("Nature")
    if nat is not None:
        _set(nat, "m_Scale", f"{scale:.6f}")
    render_node = _render(object_node)
    if render_node is not None:  # the game draws props from here: it moves with the object
        data = bytearray(render_node.data)
        RENDER_TRANSFORM.pack_into(data, len(RENDER_HEAD), *_position(object_node), *quaternion, scale)
        render_node.data = bytes(data)


def _render_block() -> SNode:
    """A prop's render parameters (header, transform, tint), the transform set by ``_set_object``."""
    render = RENDER_HEAD + bytes(RENDER_TRANSFORM.size) + RENDER_TINT
    return SNode("PropertyMesh", children=[SNode("m_RenderParameters", data=render)])


class CIsland2070:
    """A 2070 island opened for writing: the parsed ``.isd`` and what edits change in it."""

    def __init__(self, data: bytes) -> None:
        self.m_island = isd2070.CIsland(data)
        self.m_root = self.m_island.m_root
        self.m_size_tiles = self.m_island.m_width
        self._m_next_id: int | None = None  # the first free m_ID, found when the first object is added

    def write(self) -> bytes:
        return isd2070.serialize(self.m_root)

    def objects(self):
        """(group name, Objects node, object) for every placed object."""
        for name, objs in _groups(self.m_root):
            for object_node in list(objs.children):
                if object_node.name == "Object" and isd2070.position_node(object_node) is not None:
                    yield name, objs, object_node

    def apply(self, edits: dict, types: dict) -> dict:
        """Apply an edit file; ``types``: 2070's assets (``objects2070.read_types``)."""
        report = {"objects changed": 0, "objects deleted": 0, "objects added": 0, "heights": 0, "paint": 0}
        report["unmatched"] = []
        if edits.get("clear"):
            report["cleared"] = self.clear()

        def remove(gone: list[tuple[SNode, SNode]]) -> None:
            for objs, object_node in gone:
                objs.children.remove(object_node)
            if gone:
                self._drop_groups({object_node.int("m_ID") for _, object_node in gone})

        ops = objectedits.SObjectOps(lambda handle, change: _set_object(handle[1], change), remove)
        objectedits.apply_changes(
            ((object_key(object_node), (objs, object_node)) for _, objs, object_node in self.objects()),
            edits.get("objects", {}),
            ops,
            report,
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
        water, stale = isd_water.apply(self.m_root, edits)
        report |= water
        report["unmatched"] += stale
        report["properties"] = isd_props.apply(self.m_root, edits.get("properties", {}))
        return report

    def clear(self) -> dict:
        """A blank island (docs/island-editor.md, "New islands"): every object, river and lake taken off; the terrain,
        the coast lines and the logic grids stay. Returns what was removed."""
        gone = [(objs, object_node) for _, objs, object_node in self.objects()]
        for objs, object_node in gone:
            objs.children.remove(object_node)
        self._drop_groups({object_node.int("m_ID") for _, object_node in gone})
        out = {"objects": len(gone)}
        for name in ("Rivers", "Lakes"):
            node = self.m_root.find(name)
            if node is not None and node.children:
                out[name.lower()] = len(node.children)
                node.children = []
        return out

    def apply_forest(self, forest: dict, types: dict) -> int:
        """Planted forest tiles (``"x,z"`` -> 1) as tree objects: one per tile without a tree, at its
        middle on the ground, of the kind and turn the editor drew it with (``tile_pick``). Clearing
        is deleting the tree objects (``objects``)."""
        kinds = forest_kinds(self.m_island, types)
        if not kinds:
            return 0
        trees = {
            (int(object_node.x), int(object_node.z))
            for object_node in self.m_island.objects()
            if (asset_type := types.get(object_node.guid)) is not None and asset_type.template == "Tree"
        }
        heightmap = None
        planted = 0
        for k, value in sorted(forest.items()):
            x, z = map(int, k.split(","))
            if not value or (x, z) in trees or not (0 <= x < self.m_size_tiles and 0 <= z < self.m_size_tiles):
                continue
            if heightmap is None:
                heightmap = np.asarray(self.m_island.heightmap(), np.float32)
            height = float(heightmap[z : z + 2, x : x + 2].mean())
            if height < FOREST_LAND:
                continue
            pick = tile_pick(x, z)
            guid, var = kinds[pick % len(kinds)]
            yaw = math.radians(pick % 360)
            turn = [0.0, -math.sin(yaw / 2), 0.0, math.cos(yaw / 2)]
            add = {
                "asset": guid,
                "variation": var,
                "position": [x + 0.5, height, z + 0.5],
                "rotation": turn,
                "scale": 1.0,
            }
            if self.add_object(add, types):
                trees.add((x, z))
                planted += 1
        return planted

    def _drop_groups(self, ids: set[int]) -> None:
        """Object groups (pairs of IDs) that name a deleted object go with it."""
        groups = self.m_root.path("m_GOPManager/ObjectGroups")
        if groups is None:
            return
        groups.children = [
            group
            for group in groups.children
            if not (group.data and set(struct.unpack(f"<{len(group.data) // 4}I", group.data)) & ids)
        ]

    def add_object(self, add: dict, types: dict) -> bool:
        """A new object: ``asset`` (or ``guid``) and ``variation``, or a library model's 2070 ``source`` alone;
        ``position``, ``rotation``, ``scale``. A landscape asset is made new; anything else (a building) is a copy of an
        object of its asset on the island, else of its template, else a plain handle object of its own."""
        guid, var = add.get("asset", add.get("guid")), add.get("variation", 0)
        if guid is None and add.get("source"):  # a model several assets share: the placeable one
            guid, var = next(
                ((asset_type.guid, k) for asset_type in types.values() if asset_type.template in NATURE | SIMPLE
                 for k, variation in enumerate(asset_type.variations) if variation == add["source"]),
                (None, 0),
            )  # fmt: skip
        asset_type = types.get(int(guid)) if guid is not None else None
        if asset_type is None:
            return False
        group = "Nature" if asset_type.template in NATURE else "Simple" if asset_type.template in SIMPLE else None
        objs = dict(_groups(self.m_root)).get(group) if group else None
        if group and objs is None:
            return False
        object_node = (
            self._new_landscape(asset_type.guid, var, group) if group else self._new_copy(asset_type, var, types)
        )
        if object_node is None:
            return False
        objs, object_node = object_node if isinstance(object_node, tuple) else (objs, object_node)
        place = {"position": add["position"], "rotation": add.get("rotation", [0, 0, 0, 1])}
        _set_object(object_node, {**place, "scale": add.get("scale", 1)})
        objs.children.append(object_node)
        return True

    def _new_id(self) -> int:
        if self._m_next_id is None:
            self._m_next_id = max((object_node.int("m_ID") for _, _, object_node in self.objects()), default=0) + 1
        self._m_next_id += 1
        return self._m_next_id - 1

    def _new_landscape(self, guid: int, var: int, group: str) -> SNode:
        object_node = SNode("Object", children=[SNode("m_ID", str(self._new_id())), SNode("m_GUID", str(guid))])
        if var:
            object_node.children.append(SNode("m_Variation", str(var)))
        object_node.children += [
            SNode("m_Position", data=bytes(24)),
            SNode("m_PlayerID", str(PLAYER_NONE)),
            SNode("m_Direction", "0"),
        ]
        if group == "Nature":
            object_node.children.append(SNode("Nature", children=[SNode("m_Scale", "1.000000")]))
        else:
            object_node.children.append(_render_block())
        return object_node

    def _new_copy(self, asset, var: int, types: dict) -> tuple[SNode, SNode]:
        """(the objects list, a copy of the island's first object of ``asset``, else of its template, else a plain
        handle object) with an ID of its own and the variation set."""
        same = [
            (objs, object_node) for _, objs, object_node in self.objects() if object_node.int("m_GUID") == asset.guid
        ]
        kind = [(objs, object_node) for _, objs, object_node in self.objects()
                if (asset_type := types.get(object_node.int("m_GUID"))) is not None and asset_type.template == asset.template]  # fmt: skip
        if same or kind:
            objs, sample = (same or kind)[0]
            object_node = copy.deepcopy(sample)
        else:
            objs = self._handle_objects()
            object_node = SNode("Object", children=[SNode("m_ID", "0"), SNode("m_GUID", "0"), SNode("m_Position", data=bytes(24)),
                                           SNode("m_PlayerID", str(PLAYER_NONE)), SNode("m_Direction", "0"),
                                           _render_block()])  # fmt: skip
        object_node.find("m_ID").text = str(self._new_id())
        object_node.find("m_GUID").text = str(asset.guid)
        old = object_node.find("m_Variation")
        if var:
            if old is None:
                object_node.children.insert(2, old := SNode("m_Variation"))
            old.text = str(var)
        elif old is not None:
            object_node.children.remove(old)
        return objs, object_node

    def _handle_objects(self) -> SNode:
        """The ``Handle`` group's object list (the buildings'), made when the island has none."""
        objs = dict(_groups(self.m_root)).get("Handle")
        if objs is None:
            groups = self.m_root.path("m_GOPManager/Objects")
            groups.children.append(SNode("Handle", children=[SNode("Objects")]))
            objs = dict(_groups(self.m_root))["Handle"]
        return objs

    # the ground
    def _chunks(self):
        """(chunk x, chunk z, tiles per chunk, element) for every terrain chunk."""
        terrain = self.m_root.path("Terrain")
        chunk_map = terrain.path("ChunkMap")
        chunk_width = chunk_map.int("Width")
        per = terrain.int("TileCountX") // chunk_width
        for k, element_node in enumerate(chunk_map.findall("Element")):
            yield k % chunk_width, k // chunk_width, per, element_node

    @staticmethod
    def _sample_index(chunk_x: int, chunk_z: int, per: int, sample_count: int):
        """The island corners (z, x index arrays) nearest to a chunk's w x w samples."""
        indices = np.floor(np.arange(sample_count) * per / (sample_count - 1) + 0.5).astype(int)
        return chunk_z * per + indices, chunk_x * per + indices

    @staticmethod
    def _at_samples(grid, chunk_x: int, chunk_z: int, per: int, sample_count: int):
        """A corner grid at a chunk's w x w samples, between corners interpolated (fine chunks)."""
        from ....islands.islandterrain import bilinear

        offsets = np.arange(sample_count) * per / (sample_count - 1)
        return bilinear(grid, chunk_x * per + offsets, chunk_z * per + offsets)

    def apply_heights(self, heights: dict) -> int:
        """Raise or lower tile corners (``"z,x"`` -> world units): in every chunk's heightmap that
        holds the corner, and in the logic grid of the tiles around it. A chunk without heights
        (open sea) is switched on first, at the heights the editor showed it at."""
        corner_count = self.m_size_tiles + 1
        delta = islandedits.height_grid(heights, corner_count, corner_count).astype(np.float32)
        if not delta.any():
            return 0
        shown = None
        for chunk_x, chunk_z, per, element_node in self._chunks():
            data = element_node.path("HeightMap/Data")
            if data is None or data.data is None:
                sample_z, sample_x = self._sample_index(chunk_x, chunk_z, per, per + 1)
                if not delta[np.ix_(sample_z, sample_x)].any():
                    continue
                if shown is None:
                    shown = np.asarray(self.m_island.heightmap(), np.float32)
                data = self._switch_on(element_node, chunk_x, chunk_z, shown[np.ix_(sample_z, sample_x)])
            sample_count = element_node.path("HeightMap").int("Width")
            delta_samples = self._at_samples(delta, chunk_x, chunk_z, per, sample_count)
            if delta_samples.any():
                vals = np.frombuffer(data.data, "<f4").reshape(sample_count, sample_count) + delta_samples
                data.data = vals.astype("<f4").tobytes()
        manager = self.m_root.path("m_GOPManager/m_GRIDManager")
        grids = (manager.find("m_HeightMap_v2"), manager.find("m_HeightMap")) if manager is not None else ()
        first_grid = next((grid_node for grid_node in grids if grid_node is not None and grid_node.data), None)
        if first_grid is not None and len(first_grid.data) == self.m_size_tiles * self.m_size_tiles * 2:
            grid = np.frombuffer(first_grid.data, "<i2").reshape(self.m_size_tiles, self.m_size_tiles).astype(np.int32)
            mean = (delta[:-1, :-1] + delta[1:, :-1] + delta[:-1, 1:] + delta[1:, 1:]) / 4
            grid = np.clip(grid + np.round(mean * V2_UNITS).astype(np.int32), -32768, 32767)
            first_grid.data = grid.astype("<i2").tobytes()
        return int(np.count_nonzero(delta))

    def _switch_on(self, element_node: SNode, chunk_x: int, chunk_z: int, heights) -> SNode:
        """Give an open-sea chunk heights and the sea bed's layer; returns its heightmap data."""
        sample_count = len(heights)
        for name, value in CHUNK_ON.items():
            _set(element_node, name, value)
        height_map_node = SNode(
            "HeightMap",
            children=[SNode("Width", str(sample_count)), SNode("Data", data=heights.astype("<f4").tobytes())],
        )
        element_node.children.insert(1, height_map_node)  # after VertexResolution, as in a vanilla chunk
        layer = SNode(
            "AlphaMap",
            children=[
                SNode("Width", str(sample_count)),
                SNode("Data", data=bytes([255]) * sample_count * sample_count),
            ],
        )
        element_node.children.append(SNode("TexIndexData", children=[SNode("TextureIndex", "0"), layer]))
        used = self.m_root.path("UsedChunks")
        if used is not None:
            grid = bytearray(used.path("m_BitGrid").data)
            stride = used.int("m_IntXSize") * 4  # bytes per row
            grid[chunk_z * stride + chunk_x // 8] |= 1 << (chunk_x % 8)
            used.path("m_BitGrid").data = bytes(grid)
        return height_map_node.find("Data")

    def apply_paint(self, strokes: list[dict]) -> int:
        """Lay painted strokes (``material``: a ground texture GUID) into the chunks' texture
        layers: the painted layer gains what the stroke covers and the layers above it lose it.
        A chunk the stroke reaches gets the layer on top if it had none."""
        from ....islands.islandground import stroke_cover, stroke_material

        done = 0
        for stroke in strokes:
            guid = stroke_material(stroke)
            # the brush samples texel centres; corners sit half a tile earlier
            shifted = {**stroke, "points": [[point[0] + 0.5, point[1] + 0.5] for point in stroke.get("points", [])]}
            cover = stroke_cover(shifted, self.m_size_tiles + 1, 1)
            if guid < 0 or not cover.any():
                continue
            for chunk_x, chunk_z, per, element_node in self._chunks():
                layers = element_node.findall("TexIndexData")
                if not layers:
                    continue
                sample_count = layers[0].path("AlphaMap").int("Width")
                sample_z, sample_x = self._sample_index(chunk_x, chunk_z, per, sample_count)
                coverage = cover[np.ix_(sample_z, sample_x)]
                if not coverage.any():
                    continue
                mine = next((layer for layer in layers if layer.int("TextureIndex") == guid), None)
                if mine is None:
                    mine = SNode(
                        "TexIndexData",
                        children=[
                            SNode("TextureIndex", str(guid)),
                            SNode(
                                "AlphaMap",
                                children=[
                                    SNode("Width", str(sample_count)),
                                    SNode("Data", data=bytes(sample_count * sample_count)),
                                ],
                            ),
                        ],
                    )
                    element_node.children.insert(element_node.children.index(layers[-1]) + 1, mine)
                    layers.append(mine)
                above = False
                for layer in layers:
                    data = layer.path("AlphaMap/Data")
                    alpha = np.frombuffer(data.data, np.uint8).reshape(sample_count, sample_count) / 255.0
                    if layer is mine:
                        alpha = alpha + (1 - alpha) * coverage
                        above = True
                    elif above:
                        alpha = alpha * (1 - coverage)
                    else:
                        continue
                    data.data = np.clip(np.round(alpha * 255), 0, 255).astype(np.uint8).tobytes()
            done += 1
        return done


class CIsland2070Editor(CRdmModels, CIslandEditor):
    """The editor's island for Anno 2070 editing: the ``.isd`` itself, its objects drawn with the models their
    assets name, read from the game's files. The same shape as a 117 island."""

    def __init__(self, tree2070, source: str, cache: Path, edits: Path | None = None) -> None:
        from ....islands.objects2070 import read_types
        from ....islands.terrain2070 import splat_ids

        self.m_source = source
        data = tree2070.read_bytes(source)
        self.m_isl = isd2070.CIsland(data)
        self.m_isl.m_order = splat_ids(tree2070)
        self.m_size_tiles = self.m_isl.m_width
        self.m_files = CFiles(_CNoGame())
        self.m_types = read_types(tree2070)
        self._setup(cache, hashlib.sha1(data).hexdigest()[:16], edits, {"source": source}, tree2070)
        self._m_editor = ([], {})  # no 117 editor-only assets on a 2070 island
        self._m_ground: dict | None = None  # 2070's ground textures by GUID
        self._m_rgb: dict = {}  # their pixels, for the baked ground image

    def _view_cfg(self, source: str) -> str:
        """The config the view draws for a 2070 model: the model itself, "" for a marker (``model_cfg``)."""
        return self.model_cfg(source)

    def objects(self) -> list[dict]:
        out = []
        for group, _, object_node in self._objects():
            asset_type = self.m_types.get(object_node.int("m_GUID"))
            var = object_node.find("m_Variation")
            k = int(var.text) if var is not None and var.text else 0
            variations = asset_type.variations if asset_type else []
            source = variations[k if 0 <= k < len(variations) else 0] if variations else None
            cfg = self._view_cfg(source) if source and self.m_tree2070.exists(source) else ""
            out.append(
                {
                    "key": object_key(object_node),
                    "guid": object_node.int("m_GUID"),
                    "cfg": cfg,
                    "marker": None if cfg else (asset_type.template if asset_type else group),
                    "label": asset_type.name if asset_type else f"guid {object_node.int('m_GUID')}",
                    "template": asset_type.template if asset_type else group,
                    "clone": True,  # placed again as a copy of an object of its asset (``CIsland2070.add_object``)
                    "variation": k,
                    "position": list(_position(object_node)),
                    "rotation": list(_quat(object_node)),
                    "scale": _scale(object_node),
                    **({} if cfg else {"unresolved": self._why_no_model(asset_type, source)}),
                }
            )
        return out

    def _why_no_model(self, asset, source: str | None) -> str:
        """Why an object is a marker: no such asset, an asset without a model, a model file that is not there, or what
        ``model_cfg`` found wrong with the config."""
        if asset is None:
            return CReason.NO_ASSET
        if not source:
            return CReason.NO_MODEL
        if not self.m_tree2070.exists(source):
            return f"{CReason.UNREADABLE}: " + tr("islands.source_not_game", source=source)
        return self._model_state()[0].get(source) or CReason.EMPTY

    def _objects(self):
        for name, objs in _groups(self.m_isl.m_root):
            for object_node in objs.children:
                if object_node.name == "Object" and isd2070.position_node(object_node) is not None:
                    yield name, objs, object_node

    def splines(self) -> list[dict]:
        """2070's rivers (float points) and lakes (outlines at their water height), for the view's water."""
        net = isd_water.network(self.m_isl.m_root)
        rivers = [{"kind": "River", **river} for river in net["rivers"]]
        lakes = [
            {"kind": "Lake", "points": [[x, k["level"], z] for x, z in k["points"]], "widths": []} for k in net["lakes"]
        ]
        return rivers + lakes

    def streets(self) -> dict:
        """The island's stored streets for the view: the tiles [x, z, street GUID], the bridge cells [x, z] and the
        sheet of each street type (``sources.streetcells2070``); empty for an island that stores none."""
        grid = streetgrid2070.read(self.m_isl)
        if grid is None or not (grid.tiles or grid.bridges):
            return {}
        return {
            "tiles": [[x, z, guid] for (x, z), guid in sorted(grid.tiles.items())],
            "bridges": [list(tile) for tile in sorted(grid.bridges)],
            "sheets": {str(guid): key for guid, key in streetcells2070.sheets(self.m_tree2070).items()},
        }

    def island(self) -> dict:
        heightmap = np.asarray(self.m_isl.heightmap(), "<f4")
        objects = self.objects()
        return {
            "mode": "2070",
            "width": int(heightmap.shape[0]),
            "samples_per_tile": 1,
            "heights": base64.b64encode(heightmap.tobytes()).decode("ascii"),
            "splines": self.splines(),
            "water": isd_water.network(self.m_isl.m_root),
            "blockers": isd_water.blockers_json(self.m_isl.m_root),
            "objects": objects,
            "footprints": self.footprints(objects),
            "props": [],
            "streets": self.streets(),
            # trees are objects here; the forest brush plants these kinds (forest_kinds)
            "forest": {
                "tiles": [],
                "models": [
                    cfg
                    for guid, forest_kind in forest_kinds(self.m_isl, self.m_types)
                    if (cfg := self._view_cfg(self.m_types[guid].variations[forest_kind]))
                ],
            },
        }

    def _island_cache(self) -> Path:
        return self._cached(f"island2070|{ISLAND_JSON}|{self._m_fingerprint}", ".json")

    def _island_doc(self) -> dict:
        return {**self.island(), "model_notes": self.model_notes()}

    def island_json(self) -> bytes:
        data = super().island_json()
        if not getattr(self, "_m_notes_read", False):
            self._say_notes(json.loads(data))
        return data

    def island_data(self) -> dict:
        data = super().island_data()
        self._say_notes(data)
        return data

    def _say_notes(self, data: dict) -> None:
        """The notes travel inside the island's data: a cached island still says them, once."""
        if not getattr(self, "_m_notes_read", False):
            self._m_notes_read = True
            self.m_notes.extend(data.get("model_notes", []))

    def ground_textures(self) -> dict:
        from ....islands.terrain2070 import read_ground_textures

        if self._m_ground is None:
            self._m_ground = {texture.guid: texture for texture in read_ground_textures(self.m_tree2070)}
        return self._m_ground

    def materials(self) -> list[dict]:
        """2070's ground textures (painted by GUID), the island's own first."""
        from ....islands.terrain2070 import texture_scale

        repetition = texture_scale(self.m_tree2070)
        used = {texture_index for _, _, _, layers in self.m_isl.texture_chunks() for texture_index, _ in layers}
        textures = sorted(
            self.ground_textures().values(), key=lambda texture: (texture.guid not in used, texture.name.lower())
        )
        return [
            {
                "id": texture.guid,
                "name": texture.name,
                "diffuse": texture.diffuse,
                "normal": texture.normal,
                "repetition": repetition,
            }
            for texture in textures
        ]

    def vanilla_materials(self) -> list[dict]:
        return []

    def ground_layers(self, texels: int | None = None) -> tuple[list[int], np.ndarray]:
        """Each ground texture's visible weight: every chunk paints its layers over one another in
        its own order, so a layer keeps its alpha times what the layers above it leave."""
        from ....islands.islandterrain import bilinear

        texels = texels or max(1, min(GROUND_TEXELS, GROUND_MAX // max(1, self.m_size_tiles)))
        texel_count = self.m_size_tiles * texels
        seen = dict.fromkeys(i for _, _, _, chunk_layers in self.m_isl.texture_chunks() for i, _ in chunk_layers)
        ids = [i for i in seen if i in self.ground_textures()]  # not the grass and bush placement layers
        index = {i: k for k, i in enumerate(ids)}
        weights = np.zeros((len(ids), texel_count, texel_count), np.float32)
        tile = (np.arange(texel_count) + 0.5) / texels
        for chunk_x, chunk_z, per, layers in self.m_isl.texture_chunks():
            texel_x, texel_z = chunk_x * per * texels, chunk_z * per * texels
            x_slice, z_slice = slice(texel_x, texel_x + per * texels), slice(texel_z, texel_z + per * texels)
            sample_count = len(layers[0][1])
            sample_x = (tile[x_slice] - chunk_x * per) * (sample_count - 1) / per
            sample_z = (tile[z_slice] - chunk_z * per) * (sample_count - 1) / per
            left = np.ones((len(tile[z_slice]), len(tile[x_slice])), np.float32)
            for texture_index, grid in reversed(layers):  # the top layer takes its share first
                if texture_index not in index:
                    continue
                coverage = bilinear(np.asarray(grid, np.float32) / 255, sample_x, sample_z) * left
                weights[index[texture_index], z_slice, x_slice] += coverage
                left -= coverage
        return ids, weights

    def ground2070(self) -> tuple[dict, bytes]:
        """The ground as 2070 paints it (rendering/rendering-2070.md 2.2): per chunk its layers in its
        own paint order, as indices into ``ground()``'s materials (255: none), and their alpha maps,
        17 samples a chunk side over the tiles' corners. Returns (info, alphas: [layer rank]
        [chunk row * 17 + sample][chunk column * 17 + sample], uint8)."""
        index = {material["id"]: k for k, material in enumerate(self.ground()["materials"])}
        chunks = list(self.m_isl.texture_chunks())
        side = max((max(chunk_x, chunk_z) for chunk_x, chunk_z, _, _ in chunks), default=0) + 1
        per = chunks[0][2] if chunks else 16
        slots = np.full((side, side, LAYERS_2070), 255, np.uint8)
        alpha = np.zeros((LAYERS_2070, side * 17, side * 17), np.uint8)
        for chunk_x, chunk_z, _, layers in chunks:
            rank = 0
            for texture_index, grid in layers:
                if texture_index not in index or rank >= LAYERS_2070:
                    continue  # the grass and bush placement layers paint no ground
                grid_array = np.asarray(grid, np.uint8)
                grid_array = grid_array[
                    :: (len(grid_array) - 1) // 16, :: (len(grid_array) - 1) // 16
                ]  # a finer chunk's every other sample
                slots[chunk_z, chunk_x, rank] = index[texture_index]
                alpha[rank, chunk_z * 17 : chunk_z * 17 + 17, chunk_x * 17 : chunk_x * 17 + 17] = grid_array
                rank += 1
        summary = {"side": side, "per": per, "layers": LAYERS_2070, "slots": slots.ravel().tolist()}
        return summary, alpha.tobytes()

    def _texture_rgb(self, guid: int, size_px: int = 128):
        """A ground texture's pixels for the baked image; a missing or unreadable one is its mean colour."""
        if guid not in self._m_rgb:
            from PIL import Image

            texture = self.ground_textures().get(guid)
            try:
                image = Image.open(io.BytesIO(self.m_tree2070.read_bytes(texture.diffuse))).convert("RGB")
                self._m_rgb[guid] = np.asarray(image.resize((size_px, size_px)), np.float32) / 255
            except (AttributeError, OSError, KeyError, ValueError) as exc:
                self.m_notes.append(
                    tr(
                        "islands.ground_texture_drawn_mean_colour",
                        guid=guid,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                rgb = np.array(texture.mean_color if texture else (128, 128, 128), np.float32) / 255
                self._m_rgb[guid] = np.broadcast_to(rgb, (size_px, size_px, 3))
        return self._m_rgb[guid]

    def albedo(self, texels: int) -> np.ndarray:
        """The ground from above, the textures tiled and weighted by ``ground_layers``."""
        from ....islands.terrain2070 import texture_scale

        rep = texture_scale(self.m_tree2070)
        ids, weights = self.ground_layers(texels)
        layer_count = weights.shape[1]
        tile = (np.arange(layer_count) + 0.5) / texels
        out = np.zeros((layer_count, layer_count, 3), np.float32)
        for k, texture_index in enumerate(ids):
            texture_pixels = self._texture_rgb(texture_index)
            texture_u = ((tile / rep) % 1 * texture_pixels.shape[1]).astype(int)
            texture_v = ((tile / rep) % 1 * texture_pixels.shape[0]).astype(int)
            out += texture_pixels[np.ix_(texture_v, texture_u)] * weights[k][..., None]
        return out

    def albedo_png(self) -> bytes:
        from PIL import Image

        texels = max(1, min(8, ALBEDO_MAX // max(1, self.m_size_tiles)))
        out = self._cached(f"albedo2070|{self._m_fingerprint}|{texels}", ".png")
        with self._lock(out):
            if not out.is_file():
                with whole_file(out) as scratch:
                    Image.fromarray(np.clip(self.albedo(texels) * 255, 0, 255).astype(np.uint8)).save(scratch, "PNG")
        return out.read_bytes()

    def read_game(self, path: str) -> bytes:
        return self.m_tree2070.read_bytes(path)

    def placeable_sources(self) -> tuple[list[bytes], str]:
        """The asset files the asset editor reads for this game (``gamefiles.LAYOUTS``), in its order."""
        import fnmatch

        from ....assetedit.gamefiles import LAYOUTS

        layout = LAYOUTS[self.m_game.key if getattr(self, "m_game", None) else "anno2070"]
        paths = sorted(path for path in self.m_tree2070.iter_paths("", suffix=".xml") if "config/" in path)
        files = [
            self.m_tree2070.read_bytes(path)
            for pattern in layout.assets
            for path in paths
            if fnmatch.fnmatch(path, pattern)
        ]
        templates = self.m_tree2070.read_text(layout.templates) if self.m_tree2070.exists(layout.templates) else ""
        return files, templates

    def drawable(self, cfg: str) -> str:
        """The config the view draws for a game model (``model_cfg``); raises when it cannot be drawn."""
        return self.convert2070(cfg.lower())

    def library(self) -> dict:
        """2070's landscape assets an island can carry (``NATURE`` and ``SIMPLE`` templates)."""
        a2070 = []
        for asset_type in sorted(self.m_types.values(), key=lambda asset_type: asset_type.name.lower()):
            if asset_type.template in NATURE or asset_type.template in SIMPLE:
                for k, variation in enumerate(asset_type.variations):
                    if self.m_tree2070.exists(variation):
                        name = asset_type.name if len(asset_type.variations) == 1 else f"{asset_type.name} #{k + 1}"
                        a2070.append(
                            {
                                "name": name,
                                "template": asset_type.template,
                                "source": variation,
                                "asset": asset_type.guid,
                                "variation": k,
                                "group": library_group(variation),
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

    def texture_png(self, psd: str, is_normal: bool = False, is_opaque: bool = False, use_alpha: bool = False) -> bytes:
        if psd.lower().startswith("data/anno2070/"):  # a made-drawable model's
            return super().texture_png(psd, is_normal, is_opaque, use_alpha)
        from PIL import Image

        out = self._cached(f"2070|{psd}|{is_normal}|{is_opaque}|{use_alpha}", ".png")
        with self._lock(out):
            if not out.is_file():
                try:  # decoded in process, every format the old games ship (fileresolve.decode)
                    with whole_file(out) as scratch:
                        Image.fromarray(fileresolve.decode(self.m_tree2070.read_bytes(psd)), "RGBA").save(
                            scratch, "PNG"
                        )
                        finish_png(scratch, is_normal, is_opaque, use_alpha)
                except (OSError, KeyError, ValueError) as exc:  # listed in the Checks tab, drawn as missing
                    self.texture_failure(psd, exc)
                    raise
        return out.read_bytes()


class _CNoGame:
    """The view's files come from its own folder only."""

    def read(self, path: str) -> bytes:
        raise FileNotFoundError(path)

    def iter_paths(self, prefix: str = ""):
        return iter(())
