"""Anno 1800 islands for the island editor: an 1800 ``.a7m`` read into what the 117 editor works on.

1800's islands are 117's island format one engine generation earlier (the same managers, object
schema, bit grids and material packing, in any of the three FileDB layouts); the differences - the
material maps in ``rd3d.data``, the default material set, 2 texels per tile - are handled in
``Island1800``, an ``islandview.IslandView`` whose material, weight and render methods then work
unchanged (it opens a 117 island the same way). ``IslandFile1800`` writes an edited island back in
its own layout (island-editor "Other games").
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import numpy as np

from ....formats import a7mread, filedb
from ....formats.rda import RdaArchive, write_v22
from ....islands import islandedits
from ....islands.islandgrid import HEIGHT_UNITS, read_bits
from ....islands.islandview import IslandView
from . import edit117 as a7m_edit117

DEFAULT_MATERIAL_SET = "data/config/engine/material_sets/terra_01.xml"  # used when rd3d names none
# MapGenerator IslandSizes: the first class whose MaxWidth/MaxHeight hold the ActiveMapRect
SIZE_CLASSES = (("Small", 192), ("Medium", 272), ("Large", 384))
HEIGHT_SCALE = 64 * HEIGHT_UNITS  # rd3d CoarseHeightMap steps per world unit


class GameFiles:
    """``read`` over an install's archives for ``islandview.Files``: accepts an ``overlay.SourceTree``
    (``read_bytes``) or anything with ``read``."""

    def __init__(self, tree) -> None:
        self.tree = tree

    def read(self, path: str) -> bytes:
        reader = getattr(self.tree, "read_bytes", None) or self.tree.read
        return reader(path)

    def iter_paths(self, prefix: str, suffix: str | None = None) -> list[str]:
        return list(self.tree.iter_paths(prefix, suffix=suffix) if suffix else self.tree.iter_paths(prefix))

    def exists(self, path: str) -> bool:
        """Whether the install has the file, from its index (the file is not read)."""
        return self.tree.exists(path)


def archive_members(a7m: bytes) -> tuple[RdaArchive, dict[str, bytes]]:
    """An island archive held in memory and every member of it by path."""
    arc = RdaArchive.from_bytes(a7m)
    return arc, {e.path: arc.read(e.path) for e in arc}


class Island1800(IslandView):
    """An 1800 island; ``files`` is an ``islandview.Files`` (or anything with ``read``)."""

    def __init__(self, files, a7m_path: str) -> None:
        self.files = files
        self.path = a7m_path.replace("\\", "/").lower()
        self.name = Path(self.path).stem
        self.folder = self.path.rsplit("/", 1)[0] + "/"
        self.gamedata_dir = f"{self.folder}_gamedata/{self.name}/"
        self.raw_a7m = files.read(self.path)
        self.archive = RdaArchive.from_bytes(self.raw_a7m)
        gamedata, rd3d = a7mread.members(self.archive, "gamedata.data", "rd3d.data")
        self.versions = {"gamedata.data": filedb.version(gamedata), "rd3d.data": filedb.version(rd3d)}
        self.gd, self.rd = filedb.read(gamedata), filedb.read(rd3d)
        self.info = filedb.read(files.read(self.folder + self.name + ".a7minfo"))
        self.gm = self.gd.path("GameSessionManager")
        t = self.rd.path("Terrain")
        w, steps = a7mread.coarse_steps(self.rd)
        self.hm = np.frombuffer(steps, np.int16).reshape(w, w).astype(np.float32) / a7mread.HEIGHT_STEPS
        self.size = (w - 1) // 2
        self.texels = t.find("MaterialMapWidth").u32() // self.size
        self.land = np.asarray(read_bits(self.gm.path("WorldManager/Water")), dtype=bool)
        ms = t.find("MaterialSetFileName")
        self.material_set = ms.data.decode("utf-16-le").replace("\\", "/") if ms is not None else DEFAULT_MATERIAL_SET
        self._tex_cache: dict[str, np.ndarray] = {}
        self._lod_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def material_maps(self) -> tuple[np.ndarray, np.ndarray]:
        """As 117's ``matid`` / ``matalpha``: one u32 of six 5-bit ids per id block of texels, one u32
        of six 5-bit alphas per texel; 1800 keeps them in rd3d, 117 in its DDS files (its rd3d
        copies are smaller than the map and not what it draws)."""
        t = self.rd.path("Terrain")
        n = t.find("MaterialMapWidth").u32()
        m = n // t.find("MaterialIDBlockSize").u32()
        ids, alphas = t.find("MaterialIDs2"), t.find("MaterialAlphas2")
        if ids is None or alphas is None or len(ids.data) != 4 * m * m or len(alphas.data) != 4 * n * n:
            return super().material_maps()
        return np.frombuffer(ids.data, "<u4").reshape(m, m), np.frombuffer(alphas.data, "<u4").reshape(n, n)

    def heights(self) -> np.ndarray:
        """The render heights in world units, ``2 * size + 1`` samples across (rows = z)."""
        return self.hm / HEIGHT_UNITS

    def game_heights(self) -> np.ndarray:
        """The game's own heights (gamedata ``TerrainManager/HeightMap``) in world units."""
        hm = self.gm.path("TerrainManager/HeightMap")
        w = hm.find("Width").u32()
        return np.frombuffer(hm.find("HeightMap").data, "<i2").reshape(-1, w).astype(np.float32) / (HEIGHT_SCALE / 2)

    def active_rect(self) -> tuple[int, int, int, int]:
        tag = self.info.find("ActiveMapRect")
        return struct.unpack("<4i", tag.data) if tag is not None else (0, 0, self.size, self.size)

    def size_class(self) -> str:
        """The map generator's size class (1800 derives it from the island, 117 tags the asset)."""
        x0, z0, x1, z1 = self.active_rect()
        return next((n for n, m in SIZE_CLASSES if x1 - x0 <= m and z1 - z0 <= m), "Large")

    def objects(self) -> list[dict]:
        """Every placed object: group, guid, position (world), rotation (quaternion x, y, z, w), scale."""
        return [_object(o, group) for group, o in a7mread.object_tags(self.gm) if o.find("Position") is not None]


def _object(o: filedb.Tag, group: str) -> dict:
    """An object as the editor's dict: ``formats.a7mread.placed`` with lists for the vectors, and the edit file's name."""
    item = a7mread.placed(o, group)
    scale = item.scale
    return {
        "group": group,
        "guid": item.guid,
        "variation": item.variation,
        "position": list(item.position),
        "rotation": list(item.rotation),
        "scale": list(scale) if isinstance(scale, tuple) else scale,
        "cfg": item.config,
        "name": islandedits.object_name(o),
    }


def prop_parts(text: str) -> dict:
    """A ``.prp`` (1800's prop file) as ``modelparts.model_parts`` gives a model config: the mesh, the maps its
    material names (plants and terrain props say ``cProp...``, the rest ``cModel...``) and its fields as ``values``
    (its ``Type`` picks the game's prop shader: rendering/island-1800-2205-view.md 15)."""
    tags = dict(re.findall(r"<([\w.]+)>([^<]*)</>", text))
    mesh = tags.get("MeshFileName", "").replace("\\", "/")
    if not mesh:
        return {"parts": [], "files": []}

    def path(*names: str) -> str:
        return next((tags[n].replace("\\", "/") for n in names if tags.get(n)), "")

    normal = path("cPropNormalTex", "cModelNormalTex")
    part = {
        "mesh": mesh,
        "diffuse": path("cPropDiffuseTex", "cModelDiffTex"),
        "normal": normal if "default_" not in normal else "",
        "color": [float(tags.get(f"cDiffuseColor.{c}", 1.0)) for c in "rgb"],
        "shader": 8,
        "flow": 0,
        "flipbook": None,
        "position": [0, 0, 0],
        "rotation": [0, 0, 0, 1],
        "scale": [1, 1, 1],
        "values": tags,
    }
    return {"parts": [part], "files": []}


class IslandFile1800(a7m_edit117.IslandFile):
    """``formats.a7m.edit117.IslandFile`` for an 1800 island: written back in the layouts it was read in."""

    def __init__(self, a7m: bytes, a7minfo: bytes) -> None:
        gamedata, rd3d = a7mread.members(a7m, "gamedata.data", "rd3d.data")
        self.versions = {"gamedata.data": filedb.version(gamedata), "rd3d.data": filedb.version(rd3d)}
        super().__init__(a7m, a7minfo)

    def write(self) -> bytes:
        for area, doc, ids in self._areas_data():
            data = area.find("Data")
            data.data = filedb.write(doc, *ids, version=filedb.version(data.data))
            bc = area.find("ByteCount")
            if bc is not None:
                bc.data = struct.pack("<I", len(data.data))
        v = self.versions
        return write_v22(
            {
                "gamedata.data": filedb.write(self.gd, *self.gd_ids, version=v["gamedata.data"]),
                "rd3d.data": filedb.write(self.rd, *self.rd_ids, version=v["rd3d.data"]),
            }
        )
