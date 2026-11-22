"""Anno 1701 islands (``.isd`` terrain and objects, ``.isc`` header with coasts and rivers): the old
engine family's 1701 dialect of the tag tree that ``isd2070.parse`` reads.

The layout is in ``docs/formats/anno1701-world-format.md``. Units follow ``isd2070.CIsland``: x and z
in tiles, heights in world units (``TILE`` per tile); widths along coasts and rivers in tiles. ``CIsland1701.write``
gives the bytes back; the editor's changes are ``editor/formats/isc/edit``, ``archive_name`` and ``CTree1701`` the
archive rule an edited island goes into (``editor/oldenginebuild``).
"""

from __future__ import annotations

import math
import re
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import isd2070
from ..formats.isd2070 import SIslandObject, SNode
from ..sources.overlay import CSourceTree, normalize

TILE = 32.0  # world units per tile
FIXED = 4096.0  # logic positions (int32): units per tile
ISLAND_PREFIX = "data/levels/islands/"  # the shipped islands; data/levels/testlevel holds developer ones

# fmt: off
VERTEX = struct.Struct("<3f3fHHI")
# Terrain/HeightMap, one per vertex, rows = z (anno1701-world-format "Island data"; offset table, check-comments: ok)
#  0 float   x: 0, or the vertex's x in world units (94 of 407 shipped islands)
#  4 float   height, world units
#  8 float   z: as x
# 12 float3  normal (not normalised in most files)
# 24 uint16  0xffff on every vertex
# 26 uint16  unknown: 1 in 293 islands, other small values in the rest
# 28 uint32  colour 0x00RRGGBB

TILE_RECORD = struct.Struct("<fIHH3f3f")
# Terrain/TileMap, one per tile (anno1701-world-format "Island data"; offset table, check-comments: ok)
#  0 float   unknown: 20.0, or noise in the older files (114 islands)
#  4 uint32  unknown: 0 in 345 islands
#  8 uint16  ground texture: a TEX_GUID of the palette (.prj)
# 10 uint16  unknown: 0, or noise in the older files
# 12 float3  normal of the tile's first triangle
# 24 float3  normal of the second
# fmt: on


@dataclass
class SLine1701:
    """A coast or river polyline of the ``.isc``: points (x, z in tiles, y in world units) with a
    width per point (tiles)."""

    id: int
    guid: int  # 0 for a coast, the river object (235, 236) for a river
    variation: int
    points: list[tuple[float, float, float]] = field(default_factory=list)
    widths: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class STexture1701:
    """A ground texture of the island palette (``.prj`` ``[TEXTURE]``)."""

    guid: int
    name: str
    filename: str  # data\graphics\textures\...\ground\*.tga
    color: int  # minimap colour, 0x00RRGGBB
    base: int | None  # the texture this one is a variant of (TEX_MAP_TO_GUID)


class CIsland1701:
    """A 1701 island: the ``.isd`` and, when given, its ``.isc`` header."""

    def __init__(self, isd: bytes, isc: bytes | None = None) -> None:
        self.m_root = isd2070.parse(isd)
        self.m_header = isd2070.parse(isc) if isc is not None else None
        terrain = self.m_root.path("Terrain")
        if terrain is None or terrain.path("HeightMap") is None or terrain.path("TileMap") is None:
            raise ValueError("not a 1701 island: no Terrain/HeightMap and TileMap")
        heights = terrain.path("HeightMap").data or b""
        side = math.isqrt(len(heights) // VERTEX.size)
        # the grid covers UsedSize, not Dim (Dim is the next power of two in 125 shipped islands)
        self.m_width = terrain.int("Settings/UsedSizeX", side - 1)
        self.m_height = terrain.int("Settings/UsedSizeZ", side - 1)
        if (self.m_width + 1) * (self.m_height + 1) * VERTEX.size != len(heights):
            raise ValueError(
                f"HeightMap holds {len(heights)} bytes, not ({self.m_width}+1) x ({self.m_height}+1) vertices"
            )
        self.m_chunk = terrain.int("Settings/ChunkSize", 16)
        self.m_water_level = self.m_root.float("m_GOPManager/m_GRIDManager/m_WaterLevel")
        self.m_clime = self.m_header.int("Clime") if self.m_header is not None else 0  # 0 north, 1 south
        self.m_vertices = list(VERTEX.iter_unpack(heights))
        self.m_tile_records = list(TILE_RECORD.iter_unpack(terrain.path("TileMap").data or b""))
        if len(self.m_tile_records) != self.m_width * self.m_height:
            raise ValueError(f"TileMap holds {len(self.m_tile_records)} tiles, not {self.m_width} x {self.m_height}")

    def heightmap(self) -> list[list[float]]:
        """(height+1) rows of (width+1) heights in world units (the sea floor is -100)."""
        columns = self.m_width + 1
        return [
            [vertex[1] for vertex in self.m_vertices[j * columns : (j + 1) * columns]] for j in range(self.m_height + 1)
        ]

    def colours(self) -> list[list[int]]:
        """(height+1) rows of (width+1) vertex colours, 0x00RRGGBB (white but where shaded)."""
        columns = self.m_width + 1
        return [
            [vertex[-1] & 0xFFFFFF for vertex in self.m_vertices[j * columns : (j + 1) * columns]]
            for j in range(self.m_height + 1)
        ]

    def tiles(self) -> list[list[int]]:
        """``height`` rows of ``width`` ground texture ids: one texture per tile, no blending."""
        width_tiles = self.m_width
        return [
            [tile_record[2] for tile_record in self.m_tile_records[j * width_tiles : (j + 1) * width_tiles]]
            for j in range(self.m_height)
        ]

    def texture_ids(self) -> list[int]:
        """The ground textures the island uses, most tiles first."""
        return [texture for texture, _ in Counter(texture[2] for texture in self.m_tile_records).most_common()]

    def texture_layers(self) -> dict[int, list[list[int]]]:
        """Texture id -> (height+1) x (width+1) alpha (0-255) in the form of ``isd2070.CIsland``: at
        each vertex, the share of the tiles around it that carry the texture. Most tiles first."""
        width_tiles, height_tiles = self.m_width, self.m_height
        tiles = self.tiles()
        layers = {texture: [[0] * (width_tiles + 1) for _ in range(height_tiles + 1)] for texture in self.texture_ids()}
        for j in range(height_tiles + 1):
            for i in range(width_tiles + 1):
                around = [
                    tiles[z][x]
                    for z in (j - 1, j)
                    for x in (i - 1, i)
                    if 0 <= z < height_tiles and 0 <= x < width_tiles
                ]
                for texture, count in Counter(around).items():
                    layers[texture][j][i] = round(255 * count / len(around))
        return layers

    def texture_chunks(self):
        """Per chunk (``ChunkSize`` tiles): (chunk x, chunk z, tiles per chunk, [(texture id, alpha
        grid)]) as ``isd2070.CIsland.texture_chunks`` yields them, from ``texture_layers``."""
        layers = self.texture_layers()
        per = self.m_chunk
        for cz in range(-(-self.m_height // per)):
            for cx in range(-(-self.m_width // per)):
                out = []
                for texture, grid in layers.items():
                    part = [
                        [
                            grid[j][i] if j <= self.m_height and i <= self.m_width else 0
                            for i in range(cx * per, cx * per + per + 1)
                        ]
                        for j in range(cz * per, cz * per + per + 1)
                    ]
                    if any(any(row) for row in part):
                        out.append((texture, part))
                if out:
                    yield cx, cz, per, out

    def objects(self) -> list[SIslandObject]:
        """The game objects (``m_HIDManager``, kind "HID") and the props (``m_PIDManager``, "PID").
        ``direction`` is in degrees x 4096 as 2070's; ``y`` lies on the terrain only for objects of
        ``m_TerrainState`` 0 (anno1701-world-format "Island data")."""
        return [self._hid(node) if kind == "HID" else self._pid(node) for kind, node in self.object_nodes()]

    def object_nodes(self) -> list[tuple[str, SNode]]:
        """(kind, node) of every object, in the order ``objects`` lists them (what an edit's key counts)."""
        hid = [("HID", node) for node in self._managed("m_HIDManager") if node.find("m_Position") is not None]
        return hid + [("PID", node) for node in self._managed("m_PIDManager") if node.find("m_Position.x") is not None]

    @staticmethod
    def _hid(node: SNode) -> SIslandObject:
        x, y, z = hid_position(node)
        turn = math.degrees(node.int("m_Direction") / FIXED)  # radians x 4096
        guid, var = node.int("m_GUID"), _variation(node)
        return SIslandObject("HID", guid, x, y * TILE, z, round(turn * FIXED), var, None, None)

    @staticmethod
    def _pid(node: SNode) -> SIslandObject:
        x, y, z = (node.float(f"m_Position.{k}") for k in "xyz")  # world units
        size = {node.float(f"m_Size.{k}", 1.0) for k in "xyz"}
        turn = math.degrees(node.float("m_Direction"))  # radians
        scale = size.pop() if len(size) == 1 else None
        return SIslandObject(
            "PID", node.int("m_GUID"), x / TILE, y, z / TILE, round(turn * FIXED), _variation(node), scale, None
        )

    def _managed(self, manager: str) -> list[SNode]:
        """The objects of a manager: its children with a ``m_GUID`` (named by their handle)."""
        node = self.m_root.path(f"m_GOPManager/{manager}")
        return [child for child in node.children if child.find("m_GUID") is not None] if node is not None else []

    def coasts(self) -> list[SLine1701]:
        return self._lines("Coastlines")

    def rivers(self) -> list[SLine1701]:
        return self._lines("Rivers")

    def _lines(self, name: str) -> list[SLine1701]:
        lines = []
        node = self.m_header.find(name) if self.m_header else None
        for item in list_items(node) if node is not None else []:
            line = SLine1701(item.int("m_ID"), item.int("m_GUID"), item.int("m_Variation"))
            poly = item.find("m_Polygon")
            for pt in list_items(poly) if poly is not None else []:
                position = pt.find("position")
                if position is None:
                    continue
                x, y, z = _vec3(position, "<3f", ("x", "y", "z"), float)
                line.points.append((x / TILE, y, z / TILE))
                line.widths.append(pt.float("width") / TILE)
            lines.append(line)
        return lines

    # Writing
    def write(self) -> bytes:
        """The ``.isd`` as bytes: the file it was read from, byte for byte, unless edits were applied."""
        return isd2070.serialize(self.m_root)

    def write_header(self) -> bytes | None:
        """The ``.isc`` as bytes (byte for byte unless edits were applied), None for an island read without one."""
        return isd2070.serialize(self.m_header) if self.m_header is not None else None


def hid_position(node: SNode) -> tuple[float, float, float]:
    """A game object's ``m_Position`` in tiles (x, height, z; the logic position is fixed point, ``FIXED`` to a tile)."""
    x, y, z = _vec3(node.find("m_Position"), "<3i", ("m_x", "m_y", "m_z"), int)
    return x / FIXED, y / FIXED, z / FIXED


def _variation(node: SNode) -> int | None:
    return node.int("m_Variation") if node.find("m_Variation") is not None else None


def list_items(node: SNode) -> list[SNode]:
    """A list's elements in either generation: repeated ``i``, or ``arraysize`` + ``i0000``..."""
    if node.find("arraysize") is not None:
        return [child for child in node.children if re.fullmatch(r"i\d+", child.name)]
    return node.findall("i")


def _vec3(node: SNode, fmt: str, names: tuple[str, str, str], kind) -> tuple:
    """A small vector: a binary ``data`` child (12 bytes) or three named children."""
    data = node.find("data")
    if data is not None and data.data is not None and len(data.data) == 12:
        return struct.unpack(fmt, data.data)
    return tuple(kind(node.value(name, 0)) for name in names)


def palette(prj: str) -> dict[int, STexture1701]:
    """The ground textures of a ``.prj`` island palette (``data/project/anno3.prj``), by TEX_GUID."""
    out = {}
    for block in re.findall(r"\[TEXTURE\]\s*\{(.*?)\}", prj, re.S):
        keys = dict(re.findall(r"^\s*(TEX_\w+)[ \t]*(.*?)\s*$", block, re.M))
        guid = int(keys.get("TEX_GUID", "-1") or -1)
        if guid < 0:
            continue
        bgr = int(keys.get("TEX_MAP_COLOR", "0") or 0)
        mapped = keys.get("TEX_MAP_TO_GUID") == "1" and keys.get("TEX_MAPPING_GUID", "65535") != "65535"
        out[guid] = STexture1701(
            guid,
            keys.get("TEX_NAME", "").strip('"'),
            keys.get("TEX_FILENAME", "").strip('"'),
            (bgr & 0xFF) << 16 | (bgr & 0xFF00) | (bgr >> 16 & 0xFF),
            int(keys["TEX_MAPPING_GUID"]) if mapped else None,
        )
    return out


# The install's archives (anno1701-world-format "Archives")


def archives(install: str | Path, language: str = "eng") -> list[Path]:
    """The archives 1701 mounts, in its order: ``data<n>.rda``, else ``data<n>_<language>.rda``, for
    n = 0, 1, ... up to the first number with neither."""
    root, out = Path(install), []
    for number in range(1000):
        for name in (f"data{number}.rda", f"data{number}_{language}.rda"):
            if (root / name).is_file():
                out.append(root / name)
                break
        else:
            return out
    return out


class CTree1701(CSourceTree):
    """``CSourceTree`` with 1701's rule: of two archives holding a path, the entry with the newer
    timestamp wins (a tie keeps the earlier archive), whatever their order. Build it with
    ``source_tree`` or ``CTree1701.from_archives(archives(install))``."""

    def _build_index(self) -> None:
        stamps: dict[str, int] = {}
        for i, archive in enumerate(self.m_archives):
            for entry in archive.rda:
                key = normalize(entry.path)
                if key not in stamps or stamps[key] < entry.mtime:
                    stamps[key] = entry.mtime
                    self._m_index[key] = (archive.rank, i, entry.path)


def source_tree(
    install: str | Path, language: str = "eng", cache_dir: str | Path | None = None, replace=None
) -> CTree1701:
    """The files of a 1701 install as the game sees them, the History Edition's ``ahe/a3he0.rda`` (its menus and
    texts, no path of the others) included. ``replace``: archive file -> the file to read instead, or None to leave
    it out (an archive of the island editor's: ``editor/oldenginebuild``)."""
    files = archives(install, language)
    files += [file for file in [Path(install) / "ahe" / "a3he0.rda"] if file.is_file()]
    return CTree1701.from_archives(
        [replaced for file in files if (replaced := replace(file) if replace else file) is not None], cache_dir
    )


def islands(tree: CSourceTree, prefix: str = ISLAND_PREFIX) -> list[str]:
    """The island files (``.isd``) under ``prefix``, as game paths."""
    return list(tree.iter_paths(prefix, suffix=".isd"))


def load(tree: CSourceTree, path: str) -> CIsland1701:
    """An island by its ``.isd`` path, with the ``.isc`` beside it when there is one."""
    header = path[: -len(".isd")] + ".isc"
    return CIsland1701(tree.read_bytes(path), tree.read_bytes(header) if tree.exists(header) else None)
