"""Anno 1800 islands for the island editor: an 1800 ``.a7m`` read into what the 117 editor works on.

1800's islands are 117's island format one engine generation earlier (the same managers, object
schema, bit grids and material packing, in any of the three FileDB layouts); the differences - the
material maps in ``rd3d.data``, the default material set, 2 texels per tile - are handled in
``CIsland1800``, an ``islandview.CIslandView`` whose material, weight and render methods then work
unchanged (it opens a 117 island the same way). ``CIslandFile1800`` writes an edited island back in
its own layout (island-editor "Other games").
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import numpy as np

from ....formats import a7mread, filedb
from ....formats.rda import CRdaArchive, write_v22
from ....islands import islandedits
from ....islands.islandgrid import HEIGHT_UNITS, read_bits
from ....islands.islandview import CIslandView
from . import edit117 as a7m_edit117

DEFAULT_MATERIAL_SET = "data/config/engine/material_sets/terra_01.xml"  # used when rd3d names none
# MapGenerator IslandSizes: the first class whose MaxWidth/MaxHeight hold the ActiveMapRect
SIZE_CLASSES = (("Small", 192), ("Medium", 272), ("Large", 384))
HEIGHT_SCALE = 64 * HEIGHT_UNITS  # rd3d CoarseHeightMap steps per world unit


class CGameFiles:
    """``read`` over an install's archives for ``islandview.CFiles``: accepts an ``overlay.CSourceTree``
    (``read_bytes``) or anything with ``read``."""

    def __init__(self, tree) -> None:
        self.m_tree = tree

    def read(self, path: str) -> bytes:
        reader = getattr(self.m_tree, "read_bytes", None) or self.m_tree.read
        return reader(path)

    def iter_paths(self, prefix: str, suffix: str | None = None) -> list[str]:
        return list(self.m_tree.iter_paths(prefix, suffix=suffix) if suffix else self.m_tree.iter_paths(prefix))

    def exists(self, path: str) -> bool:
        """Whether the install has the file, from its index (the file is not read)."""
        return self.m_tree.exists(path)


def archive_members(a7m: bytes) -> tuple[CRdaArchive, dict[str, bytes]]:
    """An island archive held in memory and every member of it by path."""
    archive = CRdaArchive.from_bytes(a7m)
    return archive, {entry.path: archive.read(entry.path) for entry in archive}


class CIsland1800(CIslandView):
    """An 1800 island; ``files`` is an ``islandview.CFiles`` (or anything with ``read``)."""

    def __init__(self, files, a7m_path: str) -> None:
        self.files = files
        self.path = a7m_path.replace("\\", "/").lower()
        self.name = Path(self.path).stem
        self.folder = self.path.rsplit("/", 1)[0] + "/"
        self.gamedata_dir = f"{self.folder}_gamedata/{self.name}/"
        self.raw_a7m = files.read(self.path)
        self.archive = CRdaArchive.from_bytes(self.raw_a7m)
        gamedata, rd3d = a7mread.members(self.archive, "gamedata.data", "rd3d.data")
        self.file_versions = {"gamedata.data": filedb.version(gamedata), "rd3d.data": filedb.version(rd3d)}
        self.gamedata, self.rd3d = filedb.read(gamedata), filedb.read(rd3d)
        self.a7minfo = filedb.read(files.read(self.folder + self.name + ".a7minfo"))
        self.game_session_manager = self.gamedata.path("GameSessionManager")
        terrain = self.rd3d.path("Terrain")
        width, steps = a7mread.coarse_steps(self.rd3d)
        self.height_map = np.frombuffer(steps, np.int16).reshape(width, width).astype(np.float32) / a7mread.HEIGHT_STEPS
        self.size = (width - 1) // 2
        self.texels = terrain.find("MaterialMapWidth").u32() // self.size
        self.land = np.asarray(read_bits(self.game_session_manager.path("WorldManager/Water")), dtype=bool)
        material_set_tag = terrain.find("MaterialSetFileName")
        self.material_set = (
            material_set_tag.data.decode("utf-16-le").replace("\\", "/")
            if material_set_tag is not None
            else DEFAULT_MATERIAL_SET
        )
        self._tex_cache: dict[str, np.ndarray] = {}
        self._lod_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def material_maps(self) -> tuple[np.ndarray, np.ndarray]:
        """As 117's ``matid`` / ``matalpha``: one u32 of six 5-bit ids per id block of texels, one u32
        of six 5-bit alphas per texel; 1800 keeps them in rd3d, 117 in its DDS files (its rd3d
        copies are smaller than the map and not what it draws)."""
        t = self.rd3d.path("Terrain")
        n = t.find("MaterialMapWidth").u32()
        m = n // t.find("MaterialIDBlockSize").u32()
        ids, alphas = t.find("MaterialIDs2"), t.find("MaterialAlphas2")
        if ids is None or alphas is None or len(ids.data) != 4 * m * m or len(alphas.data) != 4 * n * n:
            return super().material_maps()
        return np.frombuffer(ids.data, "<u4").reshape(m, m), np.frombuffer(alphas.data, "<u4").reshape(n, n)

    def heights(self) -> np.ndarray:
        """The render heights in world units, ``2 * size + 1`` samples across (rows = z)."""
        return self.height_map / HEIGHT_UNITS

    def game_heights(self) -> np.ndarray:
        """The game's own heights (gamedata ``TerrainManager/HeightMap``) in world units."""
        height_map = self.game_session_manager.path("TerrainManager/HeightMap")
        width = height_map.find("Width").u32()
        return np.frombuffer(height_map.find("HeightMap").data, "<i2").reshape(-1, width).astype(np.float32) / (
            HEIGHT_SCALE / 2
        )

    def active_rect(self) -> tuple[int, int, int, int]:
        tag = self.a7minfo.find("ActiveMapRect")
        return struct.unpack("<4i", tag.data) if tag is not None else (0, 0, self.size, self.size)

    def size_class(self) -> str:
        """The map generator's size class (1800 derives it from the island, 117 tags the asset)."""
        min_x, min_z, max_x, max_z = self.active_rect()
        return next(
            (name for name, limit in SIZE_CLASSES if max_x - min_x <= limit and max_z - min_z <= limit), "Large"
        )

    def objects(self) -> list[dict]:
        """Every placed object: group, guid, position (world), rotation (quaternion x, y, z, w), scale."""
        return [
            _object(game_object, group)
            for group, game_object in a7mread.object_tags(self.game_session_manager)
            if game_object.find("Position") is not None
        ]


def _object(game_object: filedb.STag, group: str) -> dict:
    """An object as the editor's dict: ``formats.a7mread.placed`` with lists for the vectors, and the edit file's name."""
    item = a7mread.placed(game_object, group)
    scale = item.scale
    return {
        "group": group,
        "guid": item.guid,
        "variation": item.variation,
        "position": list(item.position),
        "rotation": list(item.rotation),
        "scale": list(scale) if isinstance(scale, tuple) else scale,
        "cfg": item.config,
        "name": islandedits.object_name(game_object),
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
        "color": [float(tags.get(f"cDiffuseColor.{channel}", 1.0)) for channel in "rgb"],
        "shader": 8,
        "flow": 0,
        "flipbook": None,
        "position": [0, 0, 0],
        "rotation": [0, 0, 0, 1],
        "scale": [1, 1, 1],
        "values": tags,
    }
    return {"parts": [part], "files": []}


class CIslandFile1800(a7m_edit117.CIslandFile):
    """``formats.a7m.edit117.CIslandFile`` for an 1800 island: written back in the layouts it was read in."""

    def __init__(self, a7m: bytes, a7minfo: bytes) -> None:
        gamedata, rd3d = a7mread.members(a7m, "gamedata.data", "rd3d.data")
        self.file_versions = {"gamedata.data": filedb.version(gamedata), "rd3d.data": filedb.version(rd3d)}
        super().__init__(a7m, a7minfo)

    def write(self) -> bytes:
        for area, document, ids in self._areas_data():
            data = area.find("Data")
            data.data = filedb.write(document, *ids, version=filedb.version(data.data))
            byte_count = area.find("ByteCount")
            if byte_count is not None:
                byte_count.data = struct.pack("<I", len(data.data))
        versions = self.file_versions
        return write_v22(
            {
                "gamedata.data": filedb.write(
                    self.gamedata, *self.name_ids_gamedata, version=versions["gamedata.data"]
                ),
                "rd3d.data": filedb.write(self.rd3d, *self.name_ids_rd3d, version=versions["rd3d.data"]),
            }
        )
