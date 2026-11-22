"""Anno 2205's maps (``.a6m``) through the new engine's island reader (``formats.a7m.reader``): a whole session
of 1024 tiles, its map info inside the archive (``mapinfo.info``) instead of an ``.a7minfo`` beside it.
What the map holds, the ground's material maps and the object lists: ``docs/formats/anno2205-map-format.md``.

``CSession2205`` also reads the rivers and lakes (Bezier paths), the props and the object folders; the models the
objects name come from ``assetmodels`` (``ASSETS_2205``). Writing a session back: ``editor/formats/a7m/edit2205.py``.
"""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from ....formats import a7mread, filedb
from ....islands import islandedits
from ....islands.islandgrid import read_bits
from ....islands.islandview import bezier_spline
from .reader import DEFAULT_MATERIAL_SET, CIsland1800, archive_members

OBJECT_GROUPS = ("GameObject", "EditorObject", "NaturePreset")
FIXED = 4096  # object positions and Direction: 64-bit fixed point (Direction in degrees)
ID_BLOCK = 4  # alpha texels per material-id texel, fixed in 2205's bake shader
BAND = 256  # rows of material texels composited at a time (a session map is 2048 across)


def _rd3d(raw: bytes) -> bytes:
    """rd3d.data as a tree file: the last patch's loose copy is an RDA archive around it."""
    return archive_members(raw)[1]["rd3d.data"] if raw.startswith(b"Resource File V2") else raw


class CSession2205(CIsland1800):
    def __init__(self, files, a6m_path: str) -> None:
        self.files = files
        self.path = a6m_path.replace("\\", "/").lower()
        self.name = Path(self.path).stem
        self.folder = self.path.rsplit("/", 1)[0] + "/"
        self.gamedata_dir = f"{self.folder}_a6mdata/{self.name}/"
        self.raw_a7m = files.read(self.path)
        self.archive, members = archive_members(self.raw_a7m)
        self.member_paths = {
            member_name: f"{self.path}|{member_name}" for member_name in members
        }  # the archive index's names for the members
        if "rd3d.data" not in members:  # the last patch keeps it beside the map (anno2205-map-format)
            members["rd3d.data"] = files.read(self.gamedata_dir + "rd3d.data")
            self.member_paths["rd3d.data"] = f"{self.gamedata_dir}rd3d.data|rd3d.data"
        members["rd3d.data"] = _rd3d(members["rd3d.data"])
        self.file_versions = {
            member_name: filedb.version(members[member_name]) for member_name in ("gamedata.data", "rd3d.data")
        }
        self.file_name_ids = {
            member_name: filedb.name_ids(members[member_name]) for member_name in ("gamedata.data", "rd3d.data")
        }
        self.gamedata = filedb.read(members["gamedata.data"])
        self.rd3d = filedb.read(members["rd3d.data"])
        self.a7minfo = filedb.read(members["mapinfo.info"])
        self.game_session_manager = self.gamedata.path("GameSessionManager")
        terrain = self.rd3d.path("Terrain")
        self.height_map = self._render_heights(terrain)
        self.size = (self.height_map.shape[0] - 1) // 2
        self.texels = max(1, terrain.find("MaterialMapWidth").u32() // self.size)
        water = self.game_session_manager.path("WorldManager/Water")
        self.land = np.asarray(read_bits(water), dtype=bool) if water is not None else np.zeros((self.size,) * 2, bool)
        material_set_tag = terrain.find("MaterialSetFileName")
        self.material_set = self._shipped_set(
            material_set_tag.data.decode("utf-16-le").replace("\\", "/")
            if material_set_tag is not None
            else DEFAULT_MATERIAL_SET
        )
        self._tex_cache: dict = {}
        self._lod_cache: dict = {}
        self.new_files: dict[str, bytes] = {}  # files of the session an edit rewrote (normal-map pages), by path

    def _render_heights(self, terrain: filedb.STag) -> np.ndarray:
        """``CoarseHeightMap`` (twice the game heights) over 64; a portrait scene ships none, so the game's own
        ``TerrainManager/HeightMap`` (dense int16, 128 to the unit) stands in, at the same scale."""
        if terrain.find("CoarseHeightMap") is not None:
            width, steps = a7mread.coarse_steps(self.rd3d)
            return np.frombuffer(steps, np.int16).reshape(width, width).astype(np.float32) / a7mread.HEIGHT_STEPS
        height_map = self.game_session_manager.path("TerrainManager/HeightMap")
        width = height_map.find("Width").u32()
        return np.frombuffer(height_map.find("HeightMap").data, "<i2").reshape(-1, width).astype(np.float32) / 32

    def _shipped_set(self, name: str) -> str:
        """The material set a map names, or the shipped one whose name differs only by an ``fpp_`` prefix and the
        underscores (the portrait scene names ``fpp_moon01``; the game ships ``moon_01``); else ``name``."""
        try:
            self.files.read(name)
            return name
        except (OSError, KeyError):
            pass

        def squash(path: str) -> str:
            return re.sub(r"[^a-z0-9]", "", Path(path).stem.lower().removeprefix("fpp_"))

        listing = getattr(self.files, "list", None) or self.files.iter_paths
        return next(
            (candidate for candidate in listing(name.rsplit("/", 1)[0] + "/") if squash(candidate) == squash(name)),
            name,
        )

    @property
    def has_sea(self) -> bool:
        """Whether the session has water (``SessionSettings/HasWater``, on by default; a portrait scene has none)."""
        flag = self.game_session_manager.path("SessionSettings/HasWater")
        return flag is None or flag.data[:1] != b"\x00"

    def material_maps(self) -> tuple[np.ndarray, np.ndarray]:
        """117's packing (six 5-bit ids / alphas per u32), the ids per 4 x 4 alpha texels."""
        terrain = self.rd3d.path("Terrain")
        alpha_width = terrain.find("MaterialMapWidth").u32()
        id_width = alpha_width // ID_BLOCK
        ids, alphas = terrain.find("MaterialIDs2"), terrain.find("MaterialAlphas2")
        if (
            ids is None
            or alphas is None
            or len(ids.data) != 4 * id_width * id_width
            or len(alphas.data) != 4 * alpha_width * alpha_width
        ):
            return np.zeros((max(1, alpha_width // ID_BLOCK),) * 2, "<u4"), np.full(
                (alpha_width, alpha_width), 31, "<u4"
            )
        return np.frombuffer(ids.data, "<u4").reshape(id_width, id_width), np.frombuffer(alphas.data, "<u4").reshape(
            alpha_width, alpha_width
        )

    def materials(self) -> dict[int, dict]:
        """The set's baked materials; ``Type`` 1 entries place ground objects (grass, gravel) and
        are not drawn into the ground."""
        try:
            text = self.files.read(self.material_set).decode("utf-8", "ignore")
        except (OSError, KeyError):  # a set that does not ship: the ground stays the bake's grey
            return {}
        props = {
            int(match.group(1))
            for match in re.finditer(r"<Material><ID>(\d+)</>(?:(?!<Material>).)*?<Type>[1-9]", text, re.S)
        }
        return {mid: material for mid, material in super().materials().items() if mid not in props}

    def weights(self) -> tuple[np.ndarray, np.ndarray]:
        """Per texel and slot: material id and visible weight, composited as the bake does: each
        material's coverage (its alphas summed over the slots) laid over the earlier ones in the
        set's order. What no material covers (the bake's grey) goes to the lowest one present."""
        ids, alphas = self.material_maps()
        rank = np.full(32, -1, np.int16)
        for mid, material in self.materials().items():
            rank[mid] = material["order"]
        width = alphas.shape[0]
        mids = np.zeros((6, width, width), np.uint8)
        slot_weights = np.zeros((6, width, width), np.float32)
        for band_start in range(0, width, BAND):
            band_ids = np.repeat(
                np.repeat(ids[band_start // ID_BLOCK : (band_start + BAND) // ID_BLOCK], ID_BLOCK, 0), ID_BLOCK, 1
            )
            band_mids, band_weights = _composite(
                band_ids[: min(BAND, width - band_start)], alphas[band_start : band_start + BAND], rank
            )
            mids[:, band_start : band_start + BAND] = band_mids
            slot_weights[:, band_start : band_start + BAND] = band_weights
        return mids, slot_weights

    def objects(self) -> list[dict]:
        """Every placed object: group, guid, position (world), rotation (quaternion x, y, z, w),
        scale, variation (index into its asset's model list), folder; ``cfg`` only where the object
        names its own model (``DynamicVariation``), else its asset's variation decides."""
        return [_object(game_object, group) for group, game_object in self.object_tags()]

    def object_tags(self, groups: tuple[str, ...] = OBJECT_GROUPS) -> Iterator[tuple[str, filedb.STag]]:
        """Every placed object's tag with its group, in the file's order."""
        game_object_manager = self.game_session_manager.find("GameObjectManager")
        for group in groups:
            objs = game_object_manager.path(f"{group}/objects") if game_object_manager is not None else None
            for game_object in objs.children if objs is not None else []:
                if isinstance(game_object, filedb.STag) and game_object.find("guid") is not None:
                    yield group, game_object

    def splines(self) -> list[tuple[str, list, list]]:
        """Rivers, lakes and coast lines: the Bezier paths of the editor objects (points in tiles)."""
        found = (bezier_spline(game_object) for _group, game_object in self.object_tags(("EditorObject", "GameObject")))
        return [spline for spline in found if spline]

    def prop_tags(self) -> list[filedb.STag]:
        """The ``PropGrid`` items: one per placed prop (``FileName``, ``Position``, ``Rotation``, ``Scale``, ``Color``)."""
        props = self.rd3d.path("PropGrid/Props")
        children = props.children if props is not None else []
        return [
            prop
            for prop in children
            if isinstance(prop, filedb.STag) and prop.find("FileName") is not None and prop.find("Position") is not None
        ]

    def read_props(self) -> list[dict]:
        """The props: model file (``.prp``), position, rotation (quaternion x, y, z, w) and scale per axis."""
        return [_prop(prop) for prop in self.prop_tags()]

    def folder_paths(self) -> dict[int, list[int]]:
        """Folder id -> the ids from the top of the editor's folder tree down to it (the tree is
        ``ObjectGroupFilterCollection``; no folder name is stored, only the ids)."""
        game_object_manager = self.game_session_manager.find("GameObjectManager")
        filters = (
            game_object_manager.path("ObjectGroupFilterCollection/Root/Filter")
            if game_object_manager is not None
            else None
        )
        out: dict[int, list[int]] = {}

        def walk(tag: filedb.STag | None, above: list[int]) -> None:
            for item in tag.children if tag is not None else []:
                if isinstance(item, filedb.STag) and item.find("FolderID") is not None:
                    here = [*above, item.find("FolderID").u32()]
                    out.setdefault(here[-1], here)
                    walk(item.find("Filter"), here)

        walk(filters, [])
        return out


def _composite(ids: np.ndarray, alphas: np.ndarray, rank: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mids = np.stack([(ids >> (5 * slot)) & 31 for slot in range(6)]).astype(np.uint8)
    alpha = np.stack([((alphas >> (5 * slot)) & 31).astype(np.float32) / 31 for slot in range(6)])
    covers = np.zeros_like(alpha)
    for slot in range(6):  # a material in two slots counts once, with both alphas, in its first slot
        first = np.ones(mids.shape[1:], bool) if slot == 0 else ~(mids[:slot] == mids[slot]).any(axis=0)
        covers[slot] = np.where(first, np.minimum((alpha * (mids == mids[slot])).sum(axis=0), 1), 0)
    ranks = rank[mids]
    covers[ranks < 0] = 0
    order = np.argsort(-ranks, axis=0, kind="stable")  # top first
    weights = np.zeros_like(covers)
    remaining = np.ones(mids.shape[1:], np.float32)
    for k in range(6):
        slot = order[k]
        coverage = np.take_along_axis(covers, slot[None], 0)[0]
        np.put_along_axis(weights, slot[None], (coverage * remaining)[None], 0)
        remaining -= coverage * remaining
    present = np.where((covers > 0) & (ranks >= 0), ranks, np.int16(32767))
    bottom = present.argmin(axis=0)
    has_present = present.min(axis=0) < 32767
    base = np.take_along_axis(weights, bottom[None], 0)[0] + np.where(has_present, remaining, 0)
    np.put_along_axis(weights, bottom[None], base[None], 0)
    return mids, weights


def _fixed(tag, name: str) -> float:
    attribute = tag.find(name) if tag is not None else None
    return struct.unpack("<q", attribute.data)[0] / FIXED if attribute is not None else 0.0


def unique_key(key: str, seen: dict[str, int]) -> str:
    """The edit-file key of a second thing at the same model and place: ``#2``, ``#3``... (a session holds
    such pairs; the key is the viewer's and the build's way to name each)."""
    seen[key] = seen.get(key, 0) + 1
    return key if seen[key] == 1 else f"{key}#{seen[key]}"


def object_position(game_object: filedb.STag) -> list[float]:
    """An object's place in tiles (x, height, z); an axis it does not store is 0."""
    position_tag = game_object.find("Position")
    return [_fixed(position_tag, "x"), _fixed(position_tag, "y"), _fixed(position_tag, "z")]


def object_rotation(game_object: filedb.STag) -> list[float]:
    """An object's rotation as a quaternion (x, y, z, w): ``FreeOrientation``, else ``Direction``, which turns the
    other way round the vertical axis (FreeOrientation agrees: its turn is minus Direction)."""
    orient = game_object.path("FreeOrientation/Orientation")
    if orient is not None:
        return list(struct.unpack("<4f", orient.data))
    angle_rad = -math.radians(_fixed(game_object, "Direction"))
    return [0.0, math.sin(angle_rad / 2), 0.0, math.cos(angle_rad / 2)]


def object_scale(game_object: filedb.STag) -> float:
    scale = game_object.path("FreeOrientation/Scale")
    return struct.unpack("<f", scale.data)[0] if scale is not None else 1.0


def _object(game_object: filedb.STag, group: str) -> dict:
    variation, folder = game_object.find("Variation"), game_object.find("ObjectFolderID")
    cfg = game_object.path("DynamicVariation/ConfigFilename")
    return {
        "group": group,
        "guid": game_object.find("guid").u32(),
        "name": islandedits.object_name(game_object),
        "position": object_position(game_object),
        "rotation": object_rotation(game_object),
        "scale": object_scale(game_object),
        "variation": variation.u32() if variation is not None else 0,
        "folder": folder.u32() if folder is not None else 1,  # absent: the game's default folder 1
        "cfg": cfg.data.decode("utf-16-le").replace("\\", "/") if cfg is not None else "",
    }


def _prop(prop: filedb.STag) -> dict:
    rotation, scale = prop.find("Rotation"), prop.find("Scale")
    return {
        "cfg": prop.find("FileName").data.decode("utf-16-le").replace("\\", "/"),
        "position": list(struct.unpack("<3f", prop.find("Position").data)),
        "rotation": list(struct.unpack("<4f", rotation.data)) if rotation is not None else [0.0, 0.0, 0.0, 1.0],
        "scale": list(struct.unpack("<3f", scale.data)) if scale is not None else [1.0, 1.0, 1.0],
    }
