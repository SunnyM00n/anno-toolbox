"""An island of Anno 117 carried into Anno 1800, or back: a vanilla pool island of the target game and region with the
same grid (the donor) gives the target game's files, and the source island's shape, ground, props and the objects both
games know are written into it.

``transfer(src, dst, request, own, target)`` is the entry (``src`` / ``dst``: ``islandconvert.CGameSide``); it returns
the parts of the ported island as a ``SCarried``. What comes from which side: island-regions "Between Anno
117 and Anno 1800".
"""

from __future__ import annotations

import copy
import math
import re
import struct
import zlib
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from ..formats import a7mread, filedb
from ..infra.i18n import tr
from ..islands import regions, regiontables, slotlist, tintpages
from ..islands.donor117 import area_keys, fit_area_ids
from ..islands.gameterrain import coarse
from ..islands.islandgrid import read_sparse, write_bits, write_sparse
from ..islands.islandterrain import fill_empty_meshes, replace_dds_pixels, set_bytes
from ..islands.materialorder import set_ranks
from . import (
    groundedit,
    islandplaces,
    largerdonor,
    objectclones,
    portdrops,
    portpictures,
    portreskin,
    regionconvert,
    slotlinks,
    templatedonors,
)
from .assetmodels import CAssetModels
from .formats.a7m import edit117 as a7m_edit117
from .regionconvert import ASSET_GAMES, material_set_of

# the source's shape, written over the donor's where both have the node (``graft``)
GRAFT_GAMEDATA = (
    "TerrainManager",
    "WorldManager/Water",
    "WorldManager/RiverGrid",
    "WorldManager/FordGrid",
    "WorldManager/EnvironmentGrid",
    "RegrowManager/TreeMap",
    "SessionSettings/PlayableArea",
)
GRAFT_TERRAIN = (  # not the far pictures' BaseTextureData: another size in each game, (portpictures.far_tint)
    "Levels", "ActiveMap", "AllNormalMapLevels/Levels", "TintMapLevels/Levels", "CoarseHeightMap", "GritMap2",
)  # fmt: skip
GRAFT_RD3D = ("WaterInfos", "WaterFlow")
GRAFT_INFO = ("MapSize", "ActiveMapGrid", "ActiveMapRect", "ObjectMetaInfo/StartCoastDirection")
SHAPE_FILES = ("meshes/", "normalmaps/", "tintmaps/")  # the source's _gamedata folders the island takes
WINDOW = 32  # tiles: the ground is carried over in squares of this side (memory)
SLOT_GROUPS = ("GameObject",)
# data other steps write (the areas' objects, the ground, the props, the slot list, the session's names): not "left out"
UNTAKEN_GAMEDATA_HANDLED = frozenset(
    {"AreaIDs", "AreaManagerData", "AreaManager", "SessionSettings/GlobalAmbientName",
     "SessionSettings/VegetationPropSetName"}
)  # fmt: skip
UNTAKEN_TERRAIN_HANDLED = frozenset(
    {
        "Terrain/MaterialIDs2",
        "Terrain/MaterialAlphas2",
        "Terrain/UsedMaterialBits",
        "Terrain/MaterialSetFileName",
        "Terrain/TintMapLevels/BaseTextureData",
    }  # the ground's maps and the far tint picture are drawn from the ground
)


@dataclass
class SCarried:
    a7m: bytes
    a7minfo: bytes
    gamedata: dict[str, bytes]
    donor: str
    tables: regiontables.STables
    report: dict = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)  # files for the mod beside the island (reskinned models)


def graft(source_tag, target_tag, lost: list | None = None, where: str = "") -> int:
    """Write the source node's values over the donor's, child by child by name (the n-th of a name to the n-th):
    what only the donor has stays, what only the source has is left out (appended to ``lost`` as (path, node) when
    given). Returns the values written."""
    if isinstance(source_tag, filedb.SAttrib) and isinstance(target_tag, filedb.SAttrib):
        target_tag.data = source_tag.data
        return 1
    if not (isinstance(source_tag, filedb.STag) and isinstance(target_tag, filedb.STag)):
        return 0
    seen: dict[str, int] = {}
    pool: dict[str, list] = {}
    for child in source_tag.children:
        pool.setdefault(child.name, []).append(child)
    done = 0
    for child in target_tag.children:
        occurrence = seen.get(child.name, 0)
        seen[child.name] = occurrence + 1
        mine = pool.get(child.name, [])
        if occurrence < len(mine):
            done += graft(mine[occurrence], child, lost, f"{where}/{child.name}")
    whole_list = (
        len(target_tag.children) != len(source_tag.children)
        and source_tag.children
        and all(child.name == "#1" for child in source_tag.children)
    )
    if lost is not None and not whole_list:
        lost += [
            (f"{where}/{name}", child)
            for name, children in pool.items()
            for child in children[seen.get(name, 0) :]
            if not name.startswith("#")
        ]
    if whole_list:
        target_tag.children = copy.deepcopy(
            source_tag.children
        )  # a list of entries (levels, blocks): the source's whole
    return done


def graft_paths(
    source_tag: filedb.STag,
    target_tag: filedb.STag,
    paths: tuple[str, ...],
    ledger: portdrops.CLedger | None = None,
    root: str = "",
) -> list[str]:
    """``graft`` at each path both trees have; returns the paths written. What the source holds there that the donor
    has no place for, and a path only the source has, go into ``ledger`` (``root``: the tree's name in its lines)."""
    done, lost = [], []
    for path in paths:
        source_node, target_node = source_tag.path(path), target_tag.path(path)
        if source_node is not None and target_node is None:
            lost.append((path, source_node))
        elif source_node is not None and target_node is not None and graft(source_node, target_node, lost, path):
            done.append(path)
    for path, node in lost if ledger is not None else []:
        ledger.add("island data", f"{root}{path}", portdrops.LEFT_OUT, portdrops.data_size(node))
    return done


def grid_size(a7minfo: filedb.STag) -> int:
    return struct.unpack_from("<I", a7minfo.find("MapSize").data)[0]


def active_area(a7minfo: filedb.STag) -> int:
    tag = a7minfo.find("ActiveMapRect")
    min_x, min_z, max_x, max_z = struct.unpack("<4i", tag.data) if tag is not None else (0, 0, 0, 0)
    return (max_x - min_x) * (max_z - min_z)


def registered(side) -> set[str]:
    """The islands a ``RandomIsland`` asset of the game puts in its pool (game paths, lower case)."""
    return {path.lower() for block in side.pool() for path in re.findall(r"<FilePath>([^<|]+)", block)}


def donor_for(target_side, region, source, is_on_maps: bool = False) -> tuple[str, largerdonor.SPlace]:
    """The target region's pool island that carries ``source`` and where the source lies in it: one of the source's
    grid whose active rectangle is nearest, else the smallest larger one that draws its land (``largerdonor``). Only
    an island the pool lists counts where the region has a pool: Latium's 768-tile continental island stands on fixed
    maps only, and an island carried onto it took a small island's place (island-regions "The report of 2026-11-17").
    ``on_maps``: the island is placed on maps, not drawn from the pool: an island a map places fixedly comes first
    (island-regions "A place on a map")."""
    grid_tiles, area = grid_size(source.a7minfo), active_area(source.a7minfo)
    paths = target_side.island_paths(region)
    if is_on_maps:
        fixed = {fixed_island.island for fixed_island in islandplaces.fixed_islands(target_side)}
        paths = [path for path in paths if path.lower() in fixed] or paths
    else:
        listed = registered(target_side)
        paths = [path for path in paths if path.lower() in listed] or paths  # the Nile has no pool: its map's islands
    pool = []
    for path in paths:
        try:
            a7minfo = filedb.read(target_side.read(path[:-4] + ".a7minfo"))
        except (KeyError, OSError, ValueError):
            continue
        pool.append((grid_size(a7minfo), abs(active_area(a7minfo) - area), path))
    same = [(diff, path) for grid, diff, path in pool if grid == grid_tiles]
    if same:
        return min(same)[1], largerdonor.SPlace(grid_tiles, grid_tiles)
    if pool and not is_on_maps and not any(grid > grid_tiles for grid, _diff, _path in pool):  # 1800's 768 in Latium
        raise ValueError(tr("islands.no_pool_size_slot", region=tr(region.label), size=grid_tiles))
    return largerdonor.choose(
        pool, grid_tiles, largerdonor.source_land(source), lambda path: largerdonor.active_cells(target_side.open(path))
    )


def area_values(gamedata: filedb.STag, size_tiles: int) -> np.ndarray:
    """The island's area ids per tile: 117 keeps them sparse, 1800 dense (``val``)."""
    node = gamedata.find("AreaIDs")
    if node.find("val") is not None:
        return np.frombuffer(node.find("val").data, "<u2").reshape(size_tiles, size_tiles).copy()
    return np.asarray(read_sparse(node, "H"), np.uint16)


def fit_areas(gamedata: filedb.STag, values: np.ndarray, ledger: portdrops.CLedger | None = None) -> np.ndarray:
    """``values`` (area ids per tile) with every id the island ``gm`` has no area for (``AreaManagerData``) put in the
    island's first area, where its objects go (``carry_objects``): a tile naming an area the game has no record of
    breaks the island (Cinis' third area 0x4001 in 1800; island-regions "The areas")."""
    keys = area_keys(gamedata)
    values, moved = fit_area_ids(keys, values)
    for known, tiles in moved.items():
        if ledger is not None:
            ledger.add("area", f"tiles of area {known:#x} put in area {keys[0]:#x}", portdrops.MOVED, tiles)
    return values


def set_area_values(gamedata: filedb.STag, values: np.ndarray, ledger: portdrops.CLedger | None = None) -> None:
    """Write the area ids per tile into the island's ``AreaIDs`` in its layout, the ids its areas lack fitted first."""
    values = fit_areas(gamedata, values, ledger)
    node = gamedata.find("AreaIDs")
    if node.find("val") is not None:
        node.find("val").data = values.astype("<u2").tobytes()
    else:
        write_sparse(node, values.tolist(), "H")


def refile(name: str, raw: bytes, version: int) -> bytes:
    """A ``_gamedata`` file in the FileDB layout ``version`` (a page's ``_CTT`` body too)."""
    if name.endswith(".ctt"):
        body = zlib.decompress(raw[8:])
        if filedb.version(body) == version:
            return raw
        new = filedb.write(filedb.read(body), *filedb.name_ids(body), version=version)
        return raw[:4] + struct.pack("<I", len(new)) + zlib.compress(new, 1)
    if name.endswith(".tmc") and filedb.version(raw) != version:
        return filedb.write(filedb.read(raw), *filedb.name_ids(raw), version=version)
    return raw


def _resample(weights: np.ndarray, texels: int) -> np.ndarray:
    have = weights.shape[1]
    if have > texels:
        factor = have // texels
        return weights.reshape(weights.shape[0], texels, factor, texels, factor).mean(axis=(2, 4))
    if have < texels:
        factor = texels // have
        return np.repeat(np.repeat(weights, factor, axis=1), factor, axis=2)
    return weights


@dataclass(frozen=True)
class SGround:
    """The source's ground as the game lays it, and how its materials and the target's are ordered and mapped."""

    ids: np.ndarray
    alphas: np.ndarray
    size_tiles: int
    table: dict[int, int]
    source_order: dict[int, int]
    target_order: dict[int, int]
    place: largerdonor.SPlace | None = None  # the source's place in a larger donor (None: the donor's grid is its own)
    fill: int = 0  # the source material the donor's sea bed round the source is laid in


def source_weights(ground: SGround, tile_x: int, tile_z: int, window_tiles: int) -> tuple[list[int], np.ndarray]:
    """The layered weights of the source's ``win`` tiles at (``sx``, ``sz``); outside the source (a larger donor's sea)
    its sea bed material alone."""
    source_texels_per_tile = ground.alphas.shape[0] // ground.size_tiles
    if not (0 <= tile_x < ground.size_tiles and 0 <= tile_z < ground.size_tiles):
        return [ground.fill], np.ones(
            (1, window_tiles * source_texels_per_tile, window_tiles * source_texels_per_tile), np.float32
        )
    source_block = ground.alphas.shape[0] // ground.ids.shape[0]
    alpha_slice = np.s_[
        tile_z * source_texels_per_tile : (tile_z + window_tiles) * source_texels_per_tile,
        tile_x * source_texels_per_tile : (tile_x + window_tiles) * source_texels_per_tile,
    ]
    id_slice = np.s_[
        tile_z * source_texels_per_tile // source_block : (tile_z + window_tiles)
        * source_texels_per_tile
        // source_block,
        tile_x * source_texels_per_tile // source_block : (tile_x + window_tiles)
        * source_texels_per_tile
        // source_block,
    ]
    return groundedit.layered_weights(ground.ids[id_slice], ground.alphas[alpha_slice], ground.source_order)


def encode_map(ground: SGround, id_words: int, alpha_words: int) -> tuple[np.ndarray, np.ndarray, int]:
    """One target map (``n_ids`` id words and ``n_alphas`` alpha words across) from the source's ground, window by
    window; returns the id words, the alpha words and the used-material mask."""
    place = ground.place or largerdonor.SPlace(ground.size_tiles, ground.size_tiles)
    target_texels_per_tile, target_block = alpha_words // place.grid, alpha_words // id_words
    ids, alphas, used = np.zeros((id_words, id_words), np.uint32), np.zeros((alpha_words, alpha_words), np.uint32), 0
    window_tiles = math.gcd(WINDOW, ground.size_tiles)
    for window_z in range(0, place.grid, window_tiles):
        for window_x in range(0, place.grid, window_tiles):
            source_materials, weights = source_weights(ground, window_x - place.x, window_z - place.z, window_tiles)
            targets = sorted({ground.table.get(material, material) for material in source_materials})
            target_weights = np.zeros((len(targets), *weights.shape[1:]), np.float32)
            for k, material in enumerate(source_materials):
                target_weights[targets.index(ground.table.get(material, material))] += weights[k]
            target_weights = _resample(target_weights, window_tiles * target_texels_per_tile).astype(np.float32)
            new_ids, new_alphas, present = groundedit.CPainted(
                targets, target_weights, ground.target_order
            ).encode_materials(target_texels_per_tile, {}, target_block)
            ids[
                window_z * target_texels_per_tile // target_block : (window_z + window_tiles)
                * target_texels_per_tile
                // target_block,
                window_x * target_texels_per_tile // target_block : (window_x + window_tiles)
                * target_texels_per_tile
                // target_block,
            ] = new_ids
            alphas[
                window_z * target_texels_per_tile : (window_z + window_tiles) * target_texels_per_tile,
                window_x * target_texels_per_tile : (window_x + window_tiles) * target_texels_per_tile,
            ] = new_alphas
            for material in present:
                used |= 1 << int(material)
    return ids, alphas, used


def sea_bed_material(ids: np.ndarray) -> int:
    """The material most common along the edge of the source's ground (its first slot): what its sea bed is."""
    edge = np.concatenate([ids[0], ids[-1], ids[:, 0], ids[:, -1]]) & np.uint32(31)
    return int(np.bincount(edge.astype(np.int64)).argmax())


def carry_ground(ground: SGround, donor, donor_gamedata: groundedit.SGamedata, out: dict[str, bytes]) -> dict:
    """Every ground map the donor has, made from the source's ground; the used-material masks say what is used."""
    report, used = {"maps": 0}, 0
    for name in [
        file_name
        for file_name in donor_gamedata.names
        if file_name.startswith("materialmaps/") and file_name.endswith("/matid.dds")
    ]:
        alpha_name = name.replace("matid.dds", "matalpha.dds")
        ids_dds, alpha_dds = donor_gamedata.read(name), donor_gamedata.read(alpha_name)
        ids, alphas, mask = encode_map(
            ground, groundedit.dds_u32(ids_dds).shape[0], groundedit.dds_u32(alpha_dds).shape[0]
        )
        out[name], out[alpha_name] = replace_dds_pixels(ids_dds, ids), replace_dds_pixels(alpha_dds, alphas)
        used |= mask
        report["maps"] += 1
    terrain = donor.rd3d.path("Terrain")
    ids_tag, alpha_tag = terrain.find("MaterialIDs2"), terrain.find("MaterialAlphas2")
    if ids_tag is not None and alpha_tag is not None and ids_tag.data:
        id_words, alpha_words = (int(round((len(tag.data) // 4) ** 0.5)) for tag in (ids_tag, alpha_tag))
        ids, alphas, mask = encode_map(ground, id_words, alpha_words)
        set_bytes(ids_tag, ids)
        set_bytes(alpha_tag, alphas)
        used |= mask
        report["maps"] += 1
    used_bits_tag = terrain.find("UsedMaterialBits")
    if used_bits_tag is not None and len(used_bits_tag.data) % 4 == 0:
        used_bits_tag.data = np.full(len(used_bits_tag.data) // 4, used, "<u4").tobytes()
    report["used"] = used
    return report


def carry_props(
    source_rd: filedb.STag,
    donor_rd: filedb.STag,
    table: dict[str, str | None],
    place=None,
    ledger: portdrops.CLedger | None = None,
    exists=None,
) -> dict:
    """The source's props in the donor's ``PropGrid``, each instance in the donor's layout (a value only the target's
    instances have from the donor's first), their files by the prop table. ``exists`` (the target game's file test,
    set between games): a prop file no table names that the target lacks is removed with the others; every prop
    removed or renamed is counted by file name in ``ledger``."""
    source_prop_grid, target_prop_grid = source_rd.path("PropGrid"), donor_rd.path("PropGrid")
    if source_prop_grid is None or target_prop_grid is None:
        return {}
    sample = next(iter(target_prop_grid.find("Instances").children), None)
    target_prop_grid.find("FileNames").children = copy.deepcopy(source_prop_grid.find("FileNames").children)
    instances = []
    for prop_instance in source_prop_grid.find("Instances").children:
        if sample is None:
            instances.append(copy.deepcopy(prop_instance))
            continue
        mine = {child.name: child for child in prop_instance.children}
        instances.append(
            filedb.STag(prop_instance.name, [copy.deepcopy(mine.get(child.name, child)) for child in sample.children])
        )
    target_prop_grid.find("Instances").children = instances
    if place is not None and place.padded:
        largerdonor.shift_props(donor_rd, place)
    return regionconvert.apply_props(donor_rd, table, ledger, exists)


def _placed(object_tag: filedb.STag, place=None) -> dict:
    item = a7mread.placed(object_tag, "")
    scale = item.scale
    shift_x, shift_z = place.shift() if place is not None else (0, 0)
    return {
        "position": [item.position[0] + shift_x, item.position[1], item.position[2] + shift_z],
        "rotation": list(item.rotation),
        "scale": list(scale) if isinstance(scale, tuple) else scale,
    }


def carry_objects(
    source_side,
    target_side,
    source,
    donor,
    table: dict[int, int | None],
    where: tuple[str, largerdonor.SPlace],
    ledger: portdrops.CLedger | None = None,
) -> dict:
    """The source's objects in the donor: an asset both games know as it is (coast lines, rivers, lakes keep their
    curves), a mapped one as a copy of an object of the target asset (``objectclones``: the donor's own, else another
    island's), the rest left out. The donor's own objects are taken off first; ``where``: the donor's path and the
    source's place in it, which moves them; each object left out or swapped is named in ``ledger``."""
    donor_path, place = where
    ledger = ledger if ledger is not None else portdrops.CLedger()
    label = lambda guid, side=source_side: portdrops.asset_label(side.asset_names, guid)  # noqa: E731
    found = objectclones.samples(donor, a7mread.OBJECT_GROUPS)
    a7m_edit117.clear(donor)
    for _area, doc, _ids in donor._areas_data():
        object_list = a7mread.area_objects(doc, "EditorObject")
        if object_list is not None:
            object_list.children = [child for child in object_list.children if not isinstance(child, filedb.STag)]
    keys = {}
    for (_a, doc, _i), key in zip(donor._areas_data(), area_keys(donor.game_session_manager), strict=False):
        keys[key] = doc
    report = {"objects kept": 0, "objects copied": 0, "objects left out": 0}
    added, pairs = [], []
    for (_a, doc, _i), key in zip(source._areas_data(), area_keys(source.game_session_manager), strict=False):
        home = keys.get(key) or next(iter(keys.values()))
        if key not in keys:
            count = sum(
                len(object_tag.children)
                for group_name in a7mread.OBJECT_GROUPS
                if (object_tag := a7mread.area_objects(doc, group_name)) is not None
            )
            ledger.add("area", f"objects of area {key:#x} put in the donor's first area", portdrops.MOVED, count)
        for group in a7mread.OBJECT_GROUPS:
            object_list = a7mread.area_objects(doc, group)
            target = a7mread.area_objects(home, group)
            for object_tag in object_list.children if object_list is not None else []:
                guid = object_tag.find("guid") if isinstance(object_tag, filedb.STag) else None
                if guid is None or object_tag.find("Position") is None:
                    if isinstance(object_tag, filedb.STag):
                        ledger.add("object", f"{group} entry without asset or position", portdrops.LEFT_OUT)
                    continue
                asset = struct.unpack_from("<I", guid.data)[0]
                if (
                    asset not in table
                    and target is not None
                    and portdrops.object_kept(
                        source_side.asset_names, target_side.asset_names, asset, source_side.m_key == target_side.m_key
                    )
                ):
                    kept = copy.deepcopy(object_tag)
                    if place.padded:
                        largerdonor.shift_object(kept, place)
                    target.children.append(kept)
                    report["objects kept"] += 1
                elif table.get(asset) is not None:
                    added.append({"guid": table[asset], "clone": True, **_placed(object_tag, place)})
                    pairs.append((asset, table[asset]))
                else:
                    report["objects left out"] += 1
                    why = " (the target game has no such asset)" if asset not in table and target is not None else ""
                    why = why or (" (the target island has no such object list)" if asset not in table else "")
                    ledger.add("object", label(asset) + why, portdrops.LEFT_OUT)
    if added:
        models = CAssetModels.read(target_side.read, ASSET_GAMES[target_side.m_key])
        donors = templatedonors.for_game(target_side.m_key, target_side.m_files, models, donor_path)
        _rest, copied, missing = objectclones.place_copies({"added_objects": added}, found, donors=donors)
        report["objects copied"] = copied
        report["objects left out"] += len(missing)
    gone = Counter(int(missing_entry.split(":")[1]) for missing_entry in (missing if added else []))
    for asset, target_asset in pairs:
        if gone[target_asset] > 0:  # a mapped asset the target has no object to copy: placed nowhere
            gone[target_asset] -= 1
            ledger.add(
                "object",
                label(asset),
                portdrops.LEFT_OUT,
                1,
                f"{label(target_asset, target_side)}: no object of it to copy",
            )
        else:
            ledger.add("object", label(asset), portdrops.MAPPED, 1, label(target_asset, target_side))
    report["objects left out"] += portdrops.group_leftovers(source, ledger)
    return report


def neutral_displacement(island) -> list[str]:
    """117's two displacement layers set to no displacement, as the island build writes them: the game's
    ``DisplacementHeightMap`` (a displacement on top of the heights, not heights) zero, rd3d's
    ``CoarseDisplacementMap`` byte 128. Returns the layers the island has (island-regions "Displacement")."""
    done = []
    displacement = island.game_session_manager.path("TerrainManager/DisplacementHeightMap/HeightMap")
    if displacement is not None:
        displacement.data = bytes(len(displacement.data))
        done.append("DisplacementHeightMap")
    coarse = island.rd3d.path("Terrain/CoarseDisplacementMap/map")
    if coarse is not None:
        coarse.data = bytes([128]) * len(coarse.data)
        done.append("CoarseDisplacementMap")
    return done


def fit_target(source, donor) -> list[str]:
    """What only one of the two games keeps: the donor's 117-only maps follow the source's shape (no displacement,
    the ford and forest-chunk maps emptied), 1800's street map is emptied."""
    done = []
    if (
        source.game_session_manager.path("TerrainManager/DisplacementHeightMap") is None
    ):  # 1800 has none: none, not the donor's
        done += neutral_displacement(donor)
    if (
        source.game_session_manager.path("WorldManager/FordGrid") is None
        and (ford := donor.game_session_manager.path("WorldManager/FordGrid")) is not None
    ):
        write_bits(ford, [[0] * donor.size for _ in range(donor.size)])
        done.append("FordGrid")
    chunks = donor.game_session_manager.path("RegrowManager/ForestChunkMap")
    if chunks is not None and source.game_session_manager.path("RegrowManager/ForestChunkMap") is None:
        grid = read_sparse(chunks, "B")
        write_sparse(chunks, [[0] * len(grid[0]) for _ in grid] if grid else [], "B")
        done.append("ForestChunkMap")
    streets = donor.game_session_manager.path("WorldManager/StreetMap/StreetID/val")
    if streets is not None and source.game_session_manager.path("WorldManager/StreetMap") is None:
        streets.data = bytes(len(streets.data))
        done.append("StreetMap")
    return done


def link_slots(source_side, target_side, source, tables, target, saved: dict) -> dict:
    """The source's slots by the slot links (``slotlinks``: the rules' suggestions, the person's links over them) into
    the object table; the slots left without a target are named in the report, not dropped unseen."""
    src_assets, mine = source_side.slots()
    used = Counter(
        struct.unpack_from("<I", object_tag.find("guid").data)[0]
        for _g, object_tag in a7mread.object_tags(source.game_session_manager, a7mread.OBJECT_GROUPS)
        if object_tag.find("guid") is not None
    )
    if not any(asset in src_assets for asset in used):
        return {}
    dst_assets, theirs = target_side.slots()
    links = slotlinks.resolve(slotlinks.suggest(src_assets, dst_assets, mine, theirs, target.island_region), saved)
    linked = slotlinks.apply(tables.objects, links, used, src_assets)
    return {"slots linked": linked, "slots unlinked": slotlinks.unlinked(used, links, src_assets)}


def refresh_slots(donor) -> dict:
    """The donor's slot list after the objects changed: 117's by ``slotlist.refresh``, 1800's items rebuilt per group
    from the slot objects of the assets the group lists."""
    placed = [
        (
            object_tag.find("ID").data,
            struct.unpack_from("<I", object_tag.find("guid").data)[0],
            struct.unpack("<3f", object_tag.find("Position").data),
        )
        for _g, object_tag in a7mread.object_tags(donor.game_session_manager, SLOT_GROUPS)
        if object_tag.find("ID") is not None
        and object_tag.find("guid") is not None
        and object_tag.find("Position") is not None
    ]
    report = slotlist.refresh(donor.a7minfo, placed)
    items = donor.a7minfo.path(regionconvert.SLOT_ITEMS)
    for group in items.children if items is not None else []:
        if not isinstance(group, filedb.STag) or not group.children:
            continue
        sample = group.children[0]
        listed = {
            struct.unpack_from("<I", child.find("ObjectGuid").data)[0]
            for child in group.children
            if isinstance(child, filedb.STag) and child.find("ObjectGuid") is not None
        }
        if not listed:  # the group's header (its slot kind), not a list of slots
            continue
        group.children = []
        for object_id, asset, position in placed:
            if asset in listed:
                item = copy.deepcopy(sample)
                item.find("ObjectId").data, item.find("ObjectGuid").data = object_id, struct.pack("<I", asset)
                item.find("Position").data = struct.pack("<3f", *position)
                group.children.append(item)
        report["slot items"] = report.get("slot items", 0) + len(group.children)
    return report


def audit_data(source, donor, ledger: portdrops.CLedger, is_padded: bool) -> None:
    """What the source's three data files hold that no step of the port carries: each node with data the donor keeps
    its own value of (``DONOR``) or does not have (``LEFT_OUT``), by name and bytes. Run before the donor is changed."""
    gamedata_paths = {
        "TerrainManager/HeightMap" if path == "TerrainManager" and is_padded else path for path in GRAFT_GAMEDATA
    }
    portdrops.untaken(
        source.game_session_manager, donor.game_session_manager, gamedata_paths | UNTAKEN_GAMEDATA_HANDLED, "", ledger
    )
    handled = {f"Terrain/{path}" for path in GRAFT_TERRAIN} | UNTAKEN_TERRAIN_HANDLED
    portdrops.untaken(source.rd3d, donor.rd3d, {*GRAFT_RD3D, "PropGrid"} | handled, "", ledger)
    portdrops.untaken(source.a7minfo, donor.a7minfo, {*GRAFT_INFO, "ObjectMetaInfo"}, "", ledger)


def carry_shape(
    source,
    donor,
    donor_gamedata: groundedit.SGamedata,
    place: largerdonor.SPlace,
    out: dict[str, bytes],
    ledger: portdrops.CLedger | None = None,
) -> dict:
    """The source's shape written into the donor: its values grafted where the grids are one size, embedded in the
    donor's middle (``largerdonor``) where the donor is larger. Returns what was written; what is left out goes
    into ``ledger``."""
    report: dict = {}
    ledger = ledger if ledger is not None else portdrops.CLedger()
    audit_data(source, donor, ledger, place.padded)
    if not place.padded:
        report["grafted"] = graft_paths(source.game_session_manager, donor.game_session_manager, GRAFT_GAMEDATA, ledger)
        report["grafted"] += graft_paths(
            source.rd3d.path("Terrain"), donor.rd3d.path("Terrain"), GRAFT_TERRAIN, ledger, "Terrain/"
        )
        report["grafted"] += graft_paths(source.rd3d, donor.rd3d, GRAFT_RD3D, ledger)
        report["grafted"] += graft_paths(source.a7minfo, donor.a7minfo, GRAFT_INFO, ledger)
        report["fitted"] = fit_target(source, donor)
        set_area_values(donor.game_session_manager, area_values(source.game_session_manager, source.size), ledger)
        return report
    report["embedded"] = {"place": [place.x, place.z, place.size_tiles, place.grid]}
    report["embedded"]["heights"] = largerdonor.embed_heights(source, donor, donor_gamedata, place, out)
    report["embedded"]["grids"] = largerdonor.embed_bits(donor.game_session_manager, source.game_session_manager, place)
    report["embedded"]["sea"] = largerdonor.embed_sea(source, donor, place)
    report["embedded"]["environment"] = largerdonor.embed_environment(source, donor, place)
    report["fitted"] = fit_target(source, donor)
    values = np.zeros((place.grid, place.grid), np.uint16)
    values[place.window()] = area_values(source.game_session_manager, source.size)
    set_area_values(donor.game_session_manager, values, ledger)
    graft_paths(source.a7minfo, donor.a7minfo, ("ObjectMetaInfo/StartCoastDirection",))
    largerdonor.shift_rect(
        donor.a7minfo, donor.game_session_manager, source.a7minfo, source.game_session_manager, place
    )
    return report


def carry_shape_files(
    source_gamedata, donor_gamedata, out: dict[str, bytes], materials: dict[int, int], place
) -> dict[str, bytes]:
    """The ``_gamedata`` files of the ported island: the source's stored meshes (their used-material masks on the IDs
    of ``materials``), height pages, tint pages and map pictures where the grids are one size (the two games' files
    are one layout: island-regions "Meshes and pages in both games"); in a larger donor the donor's, with the edit's
    changes in ``out`` and the source's tints in its window."""
    if place.padded:
        tints = largerdonor.embed_tints(source_gamedata, donor_gamedata, place)
        return {
            file_name: out[file_name]
            if file_name in out
            else tints[file_name]
            if file_name in tints
            else donor_gamedata.read(file_name)
            for file_name in donor_gamedata.names
        }
    version = next(
        (
            filedb.version(donor_gamedata.read(file_name))
            for file_name in donor_gamedata.names
            if file_name.endswith(".tmc")
        ),
        3,
    )
    for name in source_gamedata.names:
        if name.startswith(SHAPE_FILES) or (
            "/" not in name and name in donor_gamedata.names
        ):  # the shape; the map pictures
            data = source_gamedata.read(name)
            if name.endswith(".tmc"):
                data = regionconvert.remap_mesh_bits(data, materials) or data
            out[name] = refile(name, data, version)
    return {
        file_name: out.get(file_name) or donor_gamedata.read(file_name)
        for file_name in donor_gamedata.names
        if not file_name.startswith(SHAPE_FILES)
    } | out


def tint_range(island) -> tuple[float, float] | None:
    """The tint over an island's land, as it opens: the mean and spread of its far tint picture's luminance byte."""
    image = tintpages.far_picture(island.rd3d)
    if image is None:
        return None
    centres = (np.arange(image.shape[0]) + 0.5) * island.size / image.shape[0]
    grid_z, grid_x = np.meshgrid(centres, centres, indexing="ij")
    return tintpages.land_spread(
        image, coarse(groundedit.coarse_heights(island), grid_x, grid_z) > groundedit.LAND_ABOVE
    )


def fit_tints(gamedata: dict[str, bytes], rd3d, ranges: tuple) -> int:
    """Every tint page and the far tint picture carried from the source, their tint fitted into the donor's range
    (``ranges``: the source's and the donor's ``tint_range``). A tint multiplies the ground's colours and each game
    sets it for its own materials: 1800's Old World lifts its dark rock up to 2.7 times, which turns Latium's light
    limestone white (island-regions "The tint between games"). Returns the pages fitted."""
    source_range, target_range = ranges
    if source_range is None or target_range is None:
        return 0
    pages = [file_name for file_name in gamedata if tintpages.PAGE.search(file_name)]
    for name in pages:
        gamedata[name] = tintpages.fit_blocks(gamedata[name], source_range, target_range)
    base = rd3d.path("Terrain/TintMapLevels/BaseTextureData/map")
    if base is not None:
        base.data = tintpages.fit_blocks(base.data, source_range, target_range)
    return len(pages)


def transfer(source_side, target_side, request, target, colour=None, log=lambda _line: None) -> SCarried:
    """Carry the island ``request.source`` of ``src``'s game into ``dst``'s, region ``target``; ``log`` hears the stages."""
    own = regions.region_of(source_side.m_key, request.source)
    source = source_side.open(request.source)
    source_gamedata = groundedit.gamedata(source_side.m_files, request.source)
    donor_path, place = donor_for(target_side, target, source, request.fit == "map")  # islandconvert.FIT_MAP
    log(tr("islands.carrying_island_onto", donor=donor_path.rsplit("/", 1)[-1]))
    donor = target_side.open(donor_path)
    donor_gamedata = groundedit.gamedata(target_side.m_files, donor_path)
    ranges = (tint_range(source), tint_range(donor))  # the donor's own land, before the source's shape is written
    regionconvert.set_material_set(donor.rd3d, target.material_set)  # the region's own set, not a donor's variant
    source_material_set, target_material_set = (
        source_side.text(material_set_of(source, source_side.m_key)),
        target_side.text(material_set_of(donor, target_side.m_key)),
    )
    tables = regiontables.build(
        regiontables.SSources(own, source_material_set, source_side.inventory(own), source_side.asset_names),
        regiontables.SSources(target, target_material_set, target_side.inventory(target), target_side.asset_names),
        colour,
    )
    materials = regiontables.with_unlisted(tables.materials, target_material_set)
    report: dict = {"donor": donor_path}
    ledger = portdrops.CLedger()
    report |= link_slots(source_side, target_side, source, tables, target, dict(request.slot_links))
    out: dict[str, bytes] = {}
    log(tr("islands.writing_shape_heights_land_rivers"))
    report |= carry_shape(source, donor, donor_gamedata, place, out, ledger)
    log(tr("islands.carrying_ground"))
    source_ids, source_alphas = regionconvert.material_maps(source, source_gamedata)
    portdrops.ground_entries(ledger, source_ids, source.size, (source_material_set, target_material_set), materials)
    portdrops.files_left(source_gamedata, donor_gamedata, ledger)
    ground = SGround(
        source_ids,
        source_alphas,
        source.size,
        materials,
        set_ranks(source_material_set),
        set_ranks(target_material_set),
        place,
        sea_bed_material(source_ids),
    )
    report["ground"] = carry_ground(ground, donor, donor_gamedata, out)
    log(tr("islands.carrying_objects_props"))
    reskinned = portreskin.SReskinned()
    if request.reskin:
        reskinned = portreskin.for_port(
            source_side, target_side, source.rd3d, tables, own, target, target_material_set, ledger
        )
        report["reskinned props"] = reskinned.count
    report |= carry_objects(
        source_side, target_side, source, donor, tables.objects, (donor_path, place), ledger
    )  # clears the donor's props too
    report |= carry_props(source.rd3d, donor.rd3d, tables.props, place, ledger, target_side.m_files.exists)
    report["slot list"] = refresh_slots(donor)
    gamedata = carry_shape_files(source_gamedata, donor_gamedata, out, materials, place)
    if target_side.m_key == "anno117":  # 1800 flags its top mesh level and stores nothing there; 117 draws it from afar
        report["meshes filled"] = fill_empty_meshes(donor.rd3d, donor.size, gamedata)
    if place.padded:
        report["meshes"] = largerdonor.mark_meshes(gamedata, report["ground"]["used"])
    report["far"] = portpictures.far_tint(source.rd3d, donor.rd3d, place)
    report["tint pages fitted"] = fit_tints(gamedata, donor.rd3d, ranges)
    ledger.add("ground", "tint pages", portdrops.CLAMPED, report["tint pages fitted"], "the target's tint range")
    if colour is not None:
        log(tr("islands.drawing_map_pictures"))
        final = groundedit.SGamedata(sorted(gamedata), gamedata.__getitem__)
        gamedata |= portpictures.redraw(donor, final, target_material_set, lambda file: colour("dst", file))
    report["dropped"] = ledger.entries
    return SCarried(donor.write(), donor.write_info(), gamedata, donor_path, tables, report, reskinned.files)
