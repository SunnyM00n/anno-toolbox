"""Anno 2205's maps (``.a6m``) through the new engine's island reader (``formats.a7m.reader``): a whole session
of 1024 tiles, its map info inside the archive (``mapinfo.info``) instead of an ``.a7minfo`` beside it.
What the map holds, the ground's material maps and the object lists: ``docs/formats/anno2205-map-format.md``.

``Session2205`` also reads the rivers and lakes (Bezier paths), the props and the object folders; the models the
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
from .reader import DEFAULT_MATERIAL_SET, Island1800, archive_members

OBJECT_GROUPS = ("GameObject", "EditorObject", "NaturePreset")
FIXED = 4096  # object positions and Direction: 64-bit fixed point (Direction in degrees)
ID_BLOCK = 4  # alpha texels per material-id texel, fixed in 2205's bake shader
BAND = 256  # rows of material texels composited at a time (a session map is 2048 across)


def _rd3d(raw: bytes) -> bytes:
    """rd3d.data as a tree file: the last patch's loose copy is an RDA archive around it."""
    return archive_members(raw)[1]["rd3d.data"] if raw.startswith(b"Resource File V2") else raw


class Session2205(Island1800):
    def __init__(self, files, a6m_path: str) -> None:
        self.files = files
        self.path = a6m_path.replace("\\", "/").lower()
        self.name = Path(self.path).stem
        self.folder = self.path.rsplit("/", 1)[0] + "/"
        self.gamedata_dir = f"{self.folder}_a6mdata/{self.name}/"
        self.raw_a7m = files.read(self.path)
        self.archive, members = archive_members(self.raw_a7m)
        self.member_paths = {n: f"{self.path}|{n}" for n in members}  # the archive index's names for the members
        if "rd3d.data" not in members:  # the last patch keeps it beside the map (anno2205-map-format)
            members["rd3d.data"] = files.read(self.gamedata_dir + "rd3d.data")
            self.member_paths["rd3d.data"] = f"{self.gamedata_dir}rd3d.data|rd3d.data"
        members["rd3d.data"] = _rd3d(members["rd3d.data"])
        self.versions = {n: filedb.version(members[n]) for n in ("gamedata.data", "rd3d.data")}
        self.ids = {n: filedb.name_ids(members[n]) for n in ("gamedata.data", "rd3d.data")}
        self.gd = filedb.read(members["gamedata.data"])
        self.rd = filedb.read(members["rd3d.data"])
        self.info = filedb.read(members["mapinfo.info"])
        self.gm = self.gd.path("GameSessionManager")
        t = self.rd.path("Terrain")
        self.hm = self._render_heights(t)
        self.size = (self.hm.shape[0] - 1) // 2
        self.texels = max(1, t.find("MaterialMapWidth").u32() // self.size)
        water = self.gm.path("WorldManager/Water")
        self.land = np.asarray(read_bits(water), dtype=bool) if water is not None else np.zeros((self.size,) * 2, bool)
        ms = t.find("MaterialSetFileName")
        self.material_set = self._shipped_set(
            ms.data.decode("utf-16-le").replace("\\", "/") if ms is not None else DEFAULT_MATERIAL_SET
        )
        self._tex_cache: dict = {}
        self._lod_cache: dict = {}
        self.new_files: dict[str, bytes] = {}  # files of the session an edit rewrote (normal-map pages), by path

    def _render_heights(self, terrain: filedb.Tag) -> np.ndarray:
        """``CoarseHeightMap`` (twice the game heights) over 64; a portrait scene ships none, so the game's own
        ``TerrainManager/HeightMap`` (dense int16, 128 to the unit) stands in, at the same scale."""
        if terrain.find("CoarseHeightMap") is not None:
            w, steps = a7mread.coarse_steps(self.rd)
            return np.frombuffer(steps, np.int16).reshape(w, w).astype(np.float32) / a7mread.HEIGHT_STEPS
        hm = self.gm.path("TerrainManager/HeightMap")
        w = hm.find("Width").u32()
        return np.frombuffer(hm.find("HeightMap").data, "<i2").reshape(-1, w).astype(np.float32) / 32

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
        return next((p for p in listing(name.rsplit("/", 1)[0] + "/") if squash(p) == squash(name)), name)

    @property
    def has_sea(self) -> bool:
        """Whether the session has water (``SessionSettings/HasWater``, on by default; a portrait scene has none)."""
        flag = self.gm.path("SessionSettings/HasWater")
        return flag is None or flag.data[:1] != b"\x00"

    def material_maps(self) -> tuple[np.ndarray, np.ndarray]:
        """117's packing (six 5-bit ids / alphas per u32), the ids per 4 x 4 alpha texels."""
        t = self.rd.path("Terrain")
        n = t.find("MaterialMapWidth").u32()
        m = n // ID_BLOCK
        ids, alphas = t.find("MaterialIDs2"), t.find("MaterialAlphas2")
        if ids is None or alphas is None or len(ids.data) != 4 * m * m or len(alphas.data) != 4 * n * n:
            return np.zeros((max(1, n // ID_BLOCK),) * 2, "<u4"), np.full((n, n), 31, "<u4")
        return np.frombuffer(ids.data, "<u4").reshape(m, m), np.frombuffer(alphas.data, "<u4").reshape(n, n)

    def materials(self) -> dict[int, dict]:
        """The set's baked materials; ``Type`` 1 entries place ground objects (grass, gravel) and
        are not drawn into the ground."""
        try:
            text = self.files.read(self.material_set).decode("utf-8", "ignore")
        except (OSError, KeyError):  # a set that does not ship: the ground stays the bake's grey
            return {}
        props = {
            int(m.group(1)) for m in re.finditer(r"<Material><ID>(\d+)</>(?:(?!<Material>).)*?<Type>[1-9]", text, re.S)
        }
        return {mid: m for mid, m in super().materials().items() if mid not in props}

    def weights(self) -> tuple[np.ndarray, np.ndarray]:
        """Per texel and slot: material id and visible weight, composited as the bake does: each
        material's coverage (its alphas summed over the slots) laid over the earlier ones in the
        set's order. What no material covers (the bake's grey) goes to the lowest one present."""
        ids, al = self.material_maps()
        rank = np.full(32, -1, np.int16)
        for mid, m in self.materials().items():
            rank[mid] = m["order"]
        n = al.shape[0]
        mids = np.zeros((6, n, n), np.uint8)
        wts = np.zeros((6, n, n), np.float32)
        for z0 in range(0, n, BAND):
            band_ids = np.repeat(np.repeat(ids[z0 // ID_BLOCK : (z0 + BAND) // ID_BLOCK], ID_BLOCK, 0), ID_BLOCK, 1)
            m, w = _composite(band_ids[: min(BAND, n - z0)], al[z0 : z0 + BAND], rank)
            mids[:, z0 : z0 + BAND], wts[:, z0 : z0 + BAND] = m, w
        return mids, wts

    def objects(self) -> list[dict]:
        """Every placed object: group, guid, position (world), rotation (quaternion x, y, z, w),
        scale, variation (index into its asset's model list), folder; ``cfg`` only where the object
        names its own model (``DynamicVariation``), else its asset's variation decides."""
        return [_object(o, group) for group, o in self.object_tags()]

    def object_tags(self, groups: tuple[str, ...] = OBJECT_GROUPS) -> Iterator[tuple[str, filedb.Tag]]:
        """Every placed object's tag with its group, in the file's order."""
        gom = self.gm.find("GameObjectManager")
        for group in groups:
            objs = gom.path(f"{group}/objects") if gom is not None else None
            for o in objs.children if objs is not None else []:
                if isinstance(o, filedb.Tag) and o.find("guid") is not None:
                    yield group, o

    def splines(self) -> list[tuple[str, list, list]]:
        """Rivers, lakes and coast lines: the Bezier paths of the editor objects (points in tiles)."""
        found = (bezier_spline(o) for _group, o in self.object_tags(("EditorObject", "GameObject")))
        return [s for s in found if s]

    def prop_tags(self) -> list[filedb.Tag]:
        """The ``PropGrid`` items: one per placed prop (``FileName``, ``Position``, ``Rotation``, ``Scale``, ``Color``)."""
        props = self.rd.path("PropGrid/Props")
        children = props.children if props is not None else []
        return [
            p
            for p in children
            if isinstance(p, filedb.Tag) and p.find("FileName") is not None and p.find("Position") is not None
        ]

    def props(self) -> list[dict]:
        """The props: model file (``.prp``), position, rotation (quaternion x, y, z, w) and scale per axis."""
        return [_prop(p) for p in self.prop_tags()]

    def folder_paths(self) -> dict[int, list[int]]:
        """Folder id -> the ids from the top of the editor's folder tree down to it (the tree is
        ``ObjectGroupFilterCollection``; no folder name is stored, only the ids)."""
        gom = self.gm.find("GameObjectManager")
        filters = gom.path("ObjectGroupFilterCollection/Root/Filter") if gom is not None else None
        out: dict[int, list[int]] = {}

        def walk(tag: filedb.Tag | None, above: list[int]) -> None:
            for item in tag.children if tag is not None else []:
                if isinstance(item, filedb.Tag) and item.find("FolderID") is not None:
                    here = [*above, item.find("FolderID").u32()]
                    out.setdefault(here[-1], here)
                    walk(item.find("Filter"), here)

        walk(filters, [])
        return out


def _composite(ids: np.ndarray, al: np.ndarray, rank: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mids = np.stack([(ids >> (5 * s)) & 31 for s in range(6)]).astype(np.uint8)
    alpha = np.stack([((al >> (5 * s)) & 31).astype(np.float32) / 31 for s in range(6)])
    cov = np.zeros_like(alpha)
    for s in range(6):  # a material in two slots counts once, with both alphas, in its first slot
        first = np.ones(mids.shape[1:], bool) if s == 0 else ~(mids[:s] == mids[s]).any(axis=0)
        cov[s] = np.where(first, np.minimum((alpha * (mids == mids[s])).sum(axis=0), 1), 0)
    r = rank[mids]
    cov[r < 0] = 0
    order = np.argsort(-r, axis=0, kind="stable")  # top first
    wts = np.zeros_like(cov)
    remaining = np.ones(mids.shape[1:], np.float32)
    for k in range(6):
        s = order[k]
        c = np.take_along_axis(cov, s[None], 0)[0]
        np.put_along_axis(wts, s[None], (c * remaining)[None], 0)
        remaining -= c * remaining
    present = np.where((cov > 0) & (r >= 0), r, np.int16(32767))
    bottom = present.argmin(axis=0)
    has = present.min(axis=0) < 32767
    base = np.take_along_axis(wts, bottom[None], 0)[0] + np.where(has, remaining, 0)
    np.put_along_axis(wts, bottom[None], base[None], 0)
    return mids, wts


def _fixed(tag, name: str) -> float:
    a = tag.find(name) if tag is not None else None
    return struct.unpack("<q", a.data)[0] / FIXED if a is not None else 0.0


def unique_key(key: str, seen: dict[str, int]) -> str:
    """The edit-file key of a second thing at the same model and place: ``#2``, ``#3``... (a session holds
    such pairs; the key is the viewer's and the build's way to name each)."""
    seen[key] = seen.get(key, 0) + 1
    return key if seen[key] == 1 else f"{key}#{seen[key]}"


def object_position(o: filedb.Tag) -> list[float]:
    """An object's place in tiles (x, height, z); an axis it does not store is 0."""
    pos = o.find("Position")
    return [_fixed(pos, "x"), _fixed(pos, "y"), _fixed(pos, "z")]


def object_rotation(o: filedb.Tag) -> list[float]:
    """An object's rotation as a quaternion (x, y, z, w): ``FreeOrientation``, else ``Direction``, which turns the
    other way round the vertical axis (FreeOrientation agrees: its turn is minus Direction)."""
    orient = o.path("FreeOrientation/Orientation")
    if orient is not None:
        return list(struct.unpack("<4f", orient.data))
    a = -math.radians(_fixed(o, "Direction"))
    return [0.0, math.sin(a / 2), 0.0, math.cos(a / 2)]


def object_scale(o: filedb.Tag) -> float:
    scale = o.path("FreeOrientation/Scale")
    return struct.unpack("<f", scale.data)[0] if scale is not None else 1.0


def _object(o: filedb.Tag, group: str) -> dict:
    variation, folder = o.find("Variation"), o.find("ObjectFolderID")
    cfg = o.path("DynamicVariation/ConfigFilename")
    return {
        "group": group,
        "guid": o.find("guid").u32(),
        "name": islandedits.object_name(o),
        "position": object_position(o),
        "rotation": object_rotation(o),
        "scale": object_scale(o),
        "variation": variation.u32() if variation is not None else 0,
        "folder": folder.u32() if folder is not None else 1,  # absent: the game's default folder 1
        "cfg": cfg.data.decode("utf-16-le").replace("\\", "/") if cfg is not None else "",
    }


def _prop(p: filedb.Tag) -> dict:
    rotation, scale = p.find("Rotation"), p.find("Scale")
    return {
        "cfg": p.find("FileName").data.decode("utf-16-le").replace("\\", "/"),
        "position": list(struct.unpack("<3f", p.find("Position").data)),
        "rotation": list(struct.unpack("<4f", rotation.data)) if rotation is not None else [0.0, 0.0, 0.0, 1.0],
        "scale": list(struct.unpack("<3f", scale.data)) if scale is not None else [1.0, 1.0, 1.0],
    }
