"""Anno 1701 islands (``.isd`` terrain and objects, ``.isc`` header with coasts and rivers): the old
engine family's 1701 dialect of the tag tree that ``isd2070.parse`` reads.

The layout is in ``docs/formats/anno1701-world-format.md``. Units follow ``isd2070.Island``: x and z
in tiles, heights in world units (``TILE`` per tile); widths along coasts and rivers in tiles. ``Island1701.write``
gives the bytes back; the editor's changes are ``editor/formats/isc/edit``, ``archive_name`` and ``Tree1701`` the
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
from ..formats.isd2070 import IslandObject, Node
from ..sources.overlay import SourceTree, normalize

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
class Line1701:
    """A coast or river polyline of the ``.isc``: points (x, z in tiles, y in world units) with a
    width per point (tiles)."""

    id: int
    guid: int  # 0 for a coast, the river object (235, 236) for a river
    variation: int
    points: list[tuple[float, float, float]] = field(default_factory=list)
    widths: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class Texture1701:
    """A ground texture of the island palette (``.prj`` ``[TEXTURE]``)."""

    guid: int
    name: str
    filename: str  # data\graphics\textures\...\ground\*.tga
    color: int  # minimap colour, 0x00RRGGBB
    base: int | None  # the texture this one is a variant of (TEX_MAP_TO_GUID)


class Island1701:
    """A 1701 island: the ``.isd`` and, when given, its ``.isc`` header."""

    def __init__(self, isd: bytes, isc: bytes | None = None) -> None:
        self.root = isd2070.parse(isd)
        self.header = isd2070.parse(isc) if isc is not None else None
        terrain = self.root.path("Terrain")
        if terrain is None or terrain.path("HeightMap") is None or terrain.path("TileMap") is None:
            raise ValueError("not a 1701 island: no Terrain/HeightMap and TileMap")
        heights = terrain.path("HeightMap").data or b""
        side = math.isqrt(len(heights) // VERTEX.size)
        # the grid covers UsedSize, not Dim (Dim is the next power of two in 125 shipped islands)
        self.width = terrain.int("Settings/UsedSizeX", side - 1)
        self.height = terrain.int("Settings/UsedSizeZ", side - 1)
        if (self.width + 1) * (self.height + 1) * VERTEX.size != len(heights):
            raise ValueError(f"HeightMap holds {len(heights)} bytes, not ({self.width}+1) x ({self.height}+1) vertices")
        self.chunk = terrain.int("Settings/ChunkSize", 16)
        self.water_level = self.root.float("m_GOPManager/m_GRIDManager/m_WaterLevel")
        self.clime = self.header.int("Clime") if self.header is not None else 0  # 0 north, 1 south
        self.vertices = list(VERTEX.iter_unpack(heights))
        self.tile_records = list(TILE_RECORD.iter_unpack(terrain.path("TileMap").data or b""))
        if len(self.tile_records) != self.width * self.height:
            raise ValueError(f"TileMap holds {len(self.tile_records)} tiles, not {self.width} x {self.height}")

    def heightmap(self) -> list[list[float]]:
        """(height+1) rows of (width+1) heights in world units (the sea floor is -100)."""
        w = self.width + 1
        return [[v[1] for v in self.vertices[j * w : (j + 1) * w]] for j in range(self.height + 1)]

    def colours(self) -> list[list[int]]:
        """(height+1) rows of (width+1) vertex colours, 0x00RRGGBB (white but where shaded)."""
        w = self.width + 1
        return [[v[-1] & 0xFFFFFF for v in self.vertices[j * w : (j + 1) * w]] for j in range(self.height + 1)]

    def tiles(self) -> list[list[int]]:
        """``height`` rows of ``width`` ground texture ids: one texture per tile, no blending."""
        w = self.width
        return [[t[2] for t in self.tile_records[j * w : (j + 1) * w]] for j in range(self.height)]

    def texture_ids(self) -> list[int]:
        """The ground textures the island uses, most tiles first."""
        return [t for t, _ in Counter(t[2] for t in self.tile_records).most_common()]

    def texture_layers(self) -> dict[int, list[list[int]]]:
        """Texture id -> (height+1) x (width+1) alpha (0-255) in the form of ``isd2070.Island``: at
        each vertex, the share of the tiles around it that carry the texture. Most tiles first."""
        w, h = self.width, self.height
        tiles = self.tiles()
        layers = {t: [[0] * (w + 1) for _ in range(h + 1)] for t in self.texture_ids()}
        for j in range(h + 1):
            for i in range(w + 1):
                around = [tiles[z][x] for z in (j - 1, j) for x in (i - 1, i) if 0 <= z < h and 0 <= x < w]
                for t, n in Counter(around).items():
                    layers[t][j][i] = round(255 * n / len(around))
        return layers

    def texture_chunks(self):
        """Per chunk (``ChunkSize`` tiles): (chunk x, chunk z, tiles per chunk, [(texture id, alpha
        grid)]) as ``isd2070.Island.texture_chunks`` yields them, from ``texture_layers``."""
        layers = self.texture_layers()
        per = self.chunk
        for cz in range(-(-self.height // per)):
            for cx in range(-(-self.width // per)):
                out = []
                for t, grid in layers.items():
                    part = [
                        [
                            grid[j][i] if j <= self.height and i <= self.width else 0
                            for i in range(cx * per, cx * per + per + 1)
                        ]
                        for j in range(cz * per, cz * per + per + 1)
                    ]
                    if any(any(row) for row in part):
                        out.append((t, part))
                if out:
                    yield cx, cz, per, out

    def objects(self) -> list[IslandObject]:
        """The game objects (``m_HIDManager``, kind "HID") and the props (``m_PIDManager``, "PID").
        ``direction`` is in degrees x 4096 as 2070's; ``y`` lies on the terrain only for objects of
        ``m_TerrainState`` 0 (anno1701-world-format "Island data")."""
        return [self._hid(node) if kind == "HID" else self._pid(node) for kind, node in self.object_nodes()]

    def object_nodes(self) -> list[tuple[str, Node]]:
        """(kind, node) of every object, in the order ``objects`` lists them (what an edit's key counts)."""
        hid = [("HID", n) for n in self._managed("m_HIDManager") if n.find("m_Position") is not None]
        return hid + [("PID", n) for n in self._managed("m_PIDManager") if n.find("m_Position.x") is not None]

    @staticmethod
    def _hid(node: Node) -> IslandObject:
        x, y, z = hid_position(node)
        turn = math.degrees(node.int("m_Direction") / FIXED)  # radians x 4096
        guid, var = node.int("m_GUID"), _variation(node)
        return IslandObject("HID", guid, x, y * TILE, z, round(turn * FIXED), var, None, None)

    @staticmethod
    def _pid(node: Node) -> IslandObject:
        x, y, z = (node.float(f"m_Position.{k}") for k in "xyz")  # world units
        size = {node.float(f"m_Size.{k}", 1.0) for k in "xyz"}
        turn = math.degrees(node.float("m_Direction"))  # radians
        scale = size.pop() if len(size) == 1 else None
        return IslandObject(
            "PID", node.int("m_GUID"), x / TILE, y, z / TILE, round(turn * FIXED), _variation(node), scale, None
        )

    def _managed(self, manager: str) -> list[Node]:
        """The objects of a manager: its children with a ``m_GUID`` (named by their handle)."""
        node = self.root.path(f"m_GOPManager/{manager}")
        return [c for c in node.children if c.find("m_GUID") is not None] if node is not None else []

    def coasts(self) -> list[Line1701]:
        return self._lines("Coastlines")

    def rivers(self) -> list[Line1701]:
        return self._lines("Rivers")

    def _lines(self, name: str) -> list[Line1701]:
        lines = []
        node = self.header.find(name) if self.header else None
        for item in list_items(node) if node is not None else []:
            line = Line1701(item.int("m_ID"), item.int("m_GUID"), item.int("m_Variation"))
            poly = item.find("m_Polygon")
            for pt in list_items(poly) if poly is not None else []:
                pos = pt.find("position")
                if pos is None:
                    continue
                x, y, z = _vec3(pos, "<3f", ("x", "y", "z"), float)
                line.points.append((x / TILE, y, z / TILE))
                line.widths.append(pt.float("width") / TILE)
            lines.append(line)
        return lines

    # Writing
    def write(self) -> bytes:
        """The ``.isd`` as bytes: the file it was read from, byte for byte, unless edits were applied."""
        return isd2070.serialize(self.root)

    def write_header(self) -> bytes | None:
        """The ``.isc`` as bytes (byte for byte unless edits were applied), None for an island read without one."""
        return isd2070.serialize(self.header) if self.header is not None else None


def hid_position(node: Node) -> tuple[float, float, float]:
    """A game object's ``m_Position`` in tiles (x, height, z; the logic position is fixed point, ``FIXED`` to a tile)."""
    x, y, z = _vec3(node.find("m_Position"), "<3i", ("m_x", "m_y", "m_z"), int)
    return x / FIXED, y / FIXED, z / FIXED


def _variation(node: Node) -> int | None:
    return node.int("m_Variation") if node.find("m_Variation") is not None else None


def list_items(node: Node) -> list[Node]:
    """A list's elements in either generation: repeated ``i``, or ``arraysize`` + ``i0000``..."""
    if node.find("arraysize") is not None:
        return [c for c in node.children if re.fullmatch(r"i\d+", c.name)]
    return node.findall("i")


def _vec3(node: Node, fmt: str, names: tuple[str, str, str], kind) -> tuple:
    """A small vector: a binary ``data`` child (12 bytes) or three named children."""
    data = node.find("data")
    if data is not None and data.data is not None and len(data.data) == 12:
        return struct.unpack(fmt, data.data)
    return tuple(kind(node.value(n, 0)) for n in names)


def palette(prj: str) -> dict[int, Texture1701]:
    """The ground textures of a ``.prj`` island palette (``data/project/anno3.prj``), by TEX_GUID."""
    out = {}
    for block in re.findall(r"\[TEXTURE\]\s*\{(.*?)\}", prj, re.S):
        keys = dict(re.findall(r"^\s*(TEX_\w+)[ \t]*(.*?)\s*$", block, re.M))
        guid = int(keys.get("TEX_GUID", "-1") or -1)
        if guid < 0:
            continue
        bgr = int(keys.get("TEX_MAP_COLOR", "0") or 0)
        mapped = keys.get("TEX_MAP_TO_GUID") == "1" and keys.get("TEX_MAPPING_GUID", "65535") != "65535"
        out[guid] = Texture1701(
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
    for n in range(1000):
        for name in (f"data{n}.rda", f"data{n}_{language}.rda"):
            if (root / name).is_file():
                out.append(root / name)
                break
        else:
            return out
    return out


class Tree1701(SourceTree):
    """``SourceTree`` with 1701's rule: of two archives holding a path, the entry with the newer
    timestamp wins (a tie keeps the earlier archive), whatever their order. Build it with
    ``source_tree`` or ``Tree1701.from_archives(archives(install))``."""

    def _build_index(self) -> None:
        stamps: dict[str, int] = {}
        for i, archive in enumerate(self.archives):
            for entry in archive.rda:
                key = normalize(entry.path)
                if key not in stamps or stamps[key] < entry.mtime:
                    stamps[key] = entry.mtime
                    self._index[key] = (archive.rank, i, entry.path)


def source_tree(
    install: str | Path, language: str = "eng", cache_dir: str | Path | None = None, replace=None
) -> Tree1701:
    """The files of a 1701 install as the game sees them, the History Edition's ``ahe/a3he0.rda`` (its menus and
    texts, no path of the others) included. ``replace``: archive file -> the file to read instead, or None to leave
    it out (an archive of the island editor's: ``editor/oldenginebuild``)."""
    files = archives(install, language)
    files += [f for f in [Path(install) / "ahe" / "a3he0.rda"] if f.is_file()]
    return Tree1701.from_archives([r for f in files if (r := replace(f) if replace else f) is not None], cache_dir)


def islands(tree: SourceTree, prefix: str = ISLAND_PREFIX) -> list[str]:
    """The island files (``.isd``) under ``prefix``, as game paths."""
    return list(tree.iter_paths(prefix, suffix=".isd"))


def load(tree: SourceTree, path: str) -> Island1701:
    """An island by its ``.isd`` path, with the ``.isc`` beside it when there is one."""
    header = path[: -len(".isd")] + ".isc"
    return Island1701(tree.read_bytes(path), tree.read_bytes(header) if tree.exists(header) else None)
