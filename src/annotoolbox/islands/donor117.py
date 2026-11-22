"""What a built island borrows from 117's own islands: the donor island it is built on (one per grid size,
``DONORS``; the 256x256 ``roman_island_small_01`` is the default; it supplies everything not converted yet: session
settings, weather, terrain render settings) and the vanilla objects cloned into it (the river, lake and coast line
editor objects, the decoration object). The island archives are read in memory (``a7mread.members``); the objects of a
vanilla island are parsed once per game install."""

from __future__ import annotations

import shutil
import weakref
from pathlib import Path

from ..formats import a7mread, filedb
from ..sources.anno117 import CAnno117Files

DONOR = "roman_island_small_01"
DONOR_DIR = f"data/base/provinces/roman/islands/pool/{DONOR}/"
# the grid sizes (tiles) an island comes in, each on a vanilla Roman island of that size (island-terrain
# "The grid"): everything the builder replaces is sized from the donor, so the donor is the grid
POOL = "data/base/provinces/roman/islands/pool/"
DONORS = {
    128: "data/base/provinces/roman/islands/deco/roman_island_deco_01/roman_island_deco_01",
    192: POOL + "roman_island_small_02/roman_island_small_02",
    256: DONOR_DIR + DONOR,
    320: POOL + "roman_island_medium_03/roman_island_medium_03",
    384: POOL + "roman_island_large_06/roman_island_large_06",
    448: POOL + "roman_island_extralarge_02/roman_island_extralarge_02",
    512: POOL + "roman_island_large_01/roman_island_large_01",
    768: "data/dlc01/provinces/roman/islands/pool/roman_dlc01_island_continental_01/roman_dlc01_island_continental_01",
}
GRID_SIZES = tuple(sorted(DONORS))
TEMPLATE_ISLANDS = {  # vanilla islands holding the editor objects that are cloned
    "River": "data/base/provinces/roman/islands/pool/roman_island_medium_03/roman_island_medium_03.a7m",
    "Lake": "data/base/provinces/roman/islands/pool/roman_island_medium_08/roman_island_medium_08.a7m",
    "CoastLine": "data/base/provinces/roman/islands/pool/roman_island_medium_08/roman_island_medium_08.a7m",
}
# the vanilla island whose SimpleCfg decoration object (GUID 37676, template TestData_Simple; about 1,000 on it) and
# slot objects are cloned
TEMPLATE_ISLAND = "data/base/provinces/roman/islands/pool/roman_island_medium_03/roman_island_medium_03.a7m"
SIMPLE_CFG_GUID = 37676


def grid_size(width_tiles: int, height_tiles: int) -> int:
    """The grid (``GRID_SIZES``) an island of the 2070 footprint ``width`` x ``height`` is built on: the smallest that
    holds the footprint. 2070's footprint carries the margin the sea bed needs (its land and shelf lie at least
    ``seabed.EDGE_BLEND`` tiles inside it, island-terrain "The grid"); a footprint above the largest grid is an
    error."""
    for size in GRID_SIZES:
        if max(width_tiles, height_tiles) <= size:
            return size
    raise ValueError(f"2070 island {width_tiles}x{height_tiles} does not fit the largest grid ({GRID_SIZES[-1]})")


def donor_path(size_tiles: int = 256) -> str:
    """The game path of the donor of grid ``size_tiles``, without extension (its folder holds ``_gamedata/<name>/``)."""
    return DONORS[size_tiles]


def donor_files(files: CAnno117Files, size_tiles: int = 256) -> tuple[bytes, bytes, bytes]:
    """The donor's ``gamedata.data``, ``rd3d.data`` and ``.a7minfo``."""
    base = donor_path(size_tiles)
    gamedata, rd3d = a7mread.members(files.read(base + ".a7m"), "gamedata.data", "rd3d.data")
    return gamedata, rd3d, files.read(base + ".a7minfo")


def donor_gamedata(files: CAnno117Files, size_tiles: int = 256) -> dict[str, bytes]:
    """The donor's ``_gamedata`` files by their path inside that folder (meshes, pages, material and map pictures)."""
    base = donor_path(size_tiles)
    root = f"{base.rsplit('/', 1)[0]}/_gamedata/{base.rsplit('/', 1)[1]}/"
    return {path[len(root) :]: files.read(path) for path in files.iter_paths(root)}


def read_areas(session: filedb.STag) -> list[list]:
    """The areas of a session document (``GameSessionManager``): ``[area tag, its document, its name ids]`` each; the
    document is written back by the caller's ``write``."""
    return [[area, doc, filedb.name_ids(area.find("Data").data)] for area, doc in a7mread.area_docs(session)]


ISLAND_AREA_FLAG = 0x2000  # an area key with it is the island's construction area (0x2001), without it the sea's


def area_keys(session: filedb.STag) -> list[int]:
    """The key of each area of ``read_areas``, in the same order: the u16 before each area entry. The donors list
    their two areas in either order."""
    keys, last = [], 0
    for child in session.path("AreaManagerData").children:
        if isinstance(child, filedb.STag):
            keys.append(last)
        elif len(child.data) >= 2:
            last = int.from_bytes(child.data[:2], "little")
    return keys


def fit_area_ids(keys: list[int], values):
    """``values`` (area ids per tile, a numpy array) with every id that has no area record (``keys``, from
    ``area_keys``) put in the island's first area: a tile naming an area the game has no record of breaks the island
    (the Cinis crash in 1800: every id in ``AreaIDs`` must have an ``AreaManagerData`` record, island-regions "The
    areas"). Returns (the values, {id: tiles moved}); ``values`` itself when nothing moves or there are no keys."""
    import numpy as np

    moved: dict[int, int] = {}
    if not keys:
        return values, moved
    fitted = values
    for known in np.unique(values):
        if known and int(known) not in keys:
            mask = fitted == known
            if fitted is values:
                fitted = values.copy()
            fitted[mask] = keys[0]
            moved[int(known)] = int(mask.sum())
    return fitted, moved


def _tags(files, path: str, groups: tuple[str, ...]):
    """The object tags of the island ``path`` in the named groups (``a7mread.object_tags``)."""
    session = filedb.read(a7mread.members(files.read(path), "gamedata.data")[0]).path("GameSessionManager")
    return a7mread.object_tags(session, groups) if session is not None else ()


def load_templates(files: CAnno117Files) -> dict[str, filedb.STag]:
    """The vanilla editor objects (river, lake, coast line) the builder clones."""
    out = {}
    for kind, path in TEMPLATE_ISLANDS.items():
        for _group, editor_object in _tags(files, path, ("EditorObject",)):
            if editor_object.find(kind) is not None and kind not in out:
                out[kind] = editor_object
    missing = set(TEMPLATE_ISLANDS) - set(out)
    if missing:
        raise RuntimeError(f"no vanilla template for {missing}")
    return out


_g_vanilla_objects: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def vanilla_objects(files, path: str) -> list[filedb.STag]:
    """The game objects of every area of a vanilla island (``path`` a game path), parsed once per game install: the
    templates a build clones come from the same few islands, island after island. The tags are shared; copy one
    before changing it."""
    game = getattr(files, "m_game", files)  # an islandview.CFiles reads through to the game
    try:
        cache = _g_vanilla_objects.setdefault(game, {})
    except TypeError:  # a stand-in that cannot be weakly referenced: no cache
        cache = {}
    if path not in cache:
        cache[path] = [game_object for _group, game_object in _tags(files, path, ("GameObject",))]
    return cache[path]


def load_template(files) -> filedb.STag:
    """A vanilla ``SimpleCfg`` game object to clone (shared: copy it before changing it)."""
    import struct

    for game_object in vanilla_objects(files, TEMPLATE_ISLAND):
        if (
            game_object.find("guid") is not None
            and struct.unpack("<I", game_object.find("guid").data[:4])[0] == SIMPLE_CFG_GUID
            and game_object.path("Mesh/Orientation") is not None
            and game_object.path("Mesh/Scale") is not None
        ):
            return game_object
    raise RuntimeError("no SimpleCfg template object in " + TEMPLATE_ISLAND)


def copy_control(files: CAnno117Files, out: Path, name: str = "a2070_control") -> str:
    """The donor island unchanged under the mod's island path (renamed): the A/B control (``--ab``)."""
    island_directory = f"data/anno2070/islands/pool/{name}/"
    (out / island_directory).mkdir(parents=True, exist_ok=True)
    for extension in (".a7m", ".a7minfo", ".a7me"):
        (out / island_directory / f"{name}{extension}").write_bytes(files.read(DONOR_DIR + DONOR + extension))
    shutil.rmtree(out / island_directory / "_gamedata" / name, ignore_errors=True)
    for rel, data in donor_gamedata(files).items():
        target = out / island_directory / "_gamedata" / name / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return f"{island_directory}{name}.a7m"
