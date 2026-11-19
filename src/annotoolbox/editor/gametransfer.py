"""An island of Anno 117 carried into Anno 1800, or back: a vanilla pool island of the target game and region with the
same grid (the donor) gives the target game's files, and the source island's shape, ground, props and the objects both
games know are written into it.

``transfer(src, dst, request, own, target)`` is the entry (``src`` / ``dst``: ``islandconvert.GameSide``); it returns
the parts of the ported island as a ``Carried``. What comes from which side: island-regions "Between Anno
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
from ..islands.donor117 import area_keys
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
from .assetmodels import AssetModels
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
class Carried:
    a7m: bytes
    info: bytes
    gamedata: dict[str, bytes]
    donor: str
    tables: regiontables.Tables
    report: dict = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)  # files for the mod beside the island (reskinned models)


def graft(src, dst, lost: list | None = None, where: str = "") -> int:
    """Write the source node's values over the donor's, child by child by name (the n-th of a name to the n-th):
    what only the donor has stays, what only the source has is left out (appended to ``lost`` as (path, node) when
    given). Returns the values written."""
    if isinstance(src, filedb.Attrib) and isinstance(dst, filedb.Attrib):
        dst.data = src.data
        return 1
    if not (isinstance(src, filedb.Tag) and isinstance(dst, filedb.Tag)):
        return 0
    seen: dict[str, int] = {}
    pool: dict[str, list] = {}
    for c in src.children:
        pool.setdefault(c.name, []).append(c)
    done = 0
    for c in dst.children:
        k = seen.get(c.name, 0)
        seen[c.name] = k + 1
        mine = pool.get(c.name, [])
        if k < len(mine):
            done += graft(mine[k], c, lost, f"{where}/{c.name}")
    whole_list = len(dst.children) != len(src.children) and src.children and all(c.name == "#1" for c in src.children)
    if lost is not None and not whole_list:
        lost += [(f"{where}/{n}", c) for n, cs in pool.items() for c in cs[seen.get(n, 0) :] if not n.startswith("#")]
    if whole_list:
        dst.children = copy.deepcopy(src.children)  # a list of entries (levels, blocks): the source's whole
    return done


def graft_paths(
    src: filedb.Tag, dst: filedb.Tag, paths: tuple[str, ...], ledger: portdrops.Ledger | None = None, root: str = ""
) -> list[str]:
    """``graft`` at each path both trees have; returns the paths written. What the source holds there that the donor
    has no place for, and a path only the source has, go into ``ledger`` (``root``: the tree's name in its lines)."""
    done, lost = [], []
    for p in paths:
        a, b = src.path(p), dst.path(p)
        if a is not None and b is None:
            lost.append((p, a))
        elif a is not None and b is not None and graft(a, b, lost, p):
            done.append(p)
    for p, node in lost if ledger is not None else []:
        ledger.add("island data", f"{root}{p}", portdrops.LEFT_OUT, portdrops.data_size(node))
    return done


def grid_size(info: filedb.Tag) -> int:
    return struct.unpack_from("<I", info.find("MapSize").data)[0]


def active_area(info: filedb.Tag) -> int:
    tag = info.find("ActiveMapRect")
    x0, z0, x1, z1 = struct.unpack("<4i", tag.data) if tag is not None else (0, 0, 0, 0)
    return (x1 - x0) * (z1 - z0)


def registered(side) -> set[str]:
    """The islands a ``RandomIsland`` asset of the game puts in its pool (game paths, lower case)."""
    return {m.lower() for block in side.pool() for m in re.findall(r"<FilePath>([^<|]+)", block)}


def donor_for(dst, region, source, on_maps: bool = False) -> tuple[str, largerdonor.Place]:
    """The target region's pool island that carries ``source`` and where the source lies in it: one of the source's
    grid whose active rectangle is nearest, else the smallest larger one that draws its land (``largerdonor``). Only
    an island the pool lists counts where the region has a pool: Latium's 768-tile continental island stands on fixed
    maps only, and an island carried onto it took a small island's place (island-regions "The report of 2026-11-17").
    ``on_maps``: the island is placed on maps, not drawn from the pool: an island a map places fixedly comes first
    (island-regions "A place on a map")."""
    size, area = grid_size(source.info), active_area(source.info)
    paths = dst.island_paths(region)
    if on_maps:
        fixed = {p.island for p in islandplaces.fixed_islands(dst)}
        paths = [p for p in paths if p.lower() in fixed] or paths
    else:
        listed = registered(dst)
        paths = [p for p in paths if p.lower() in listed] or paths  # the Nile has no pool: its map's islands
    pool = []
    for path in paths:
        try:
            info = filedb.read(dst.read(path[:-4] + ".a7minfo"))
        except (KeyError, OSError, ValueError):
            continue
        pool.append((grid_size(info), abs(active_area(info) - area), path))
    same = [(diff, path) for grid, diff, path in pool if grid == size]
    if same:
        return min(same)[1], largerdonor.Place(size, size)
    if pool and not on_maps and not any(grid > size for grid, _diff, _path in pool):  # 1800's 768 in Latium
        raise ValueError(tr("islands.no_pool_size_slot", region=tr(region.label), size=size))
    return largerdonor.choose(
        pool, size, largerdonor.source_land(source), lambda p: largerdonor.active_cells(dst.open(p))
    )


def area_values(gm: filedb.Tag, size: int) -> np.ndarray:
    """The island's area ids per tile: 117 keeps them sparse, 1800 dense (``val``)."""
    node = gm.find("AreaIDs")
    if node.find("val") is not None:
        return np.frombuffer(node.find("val").data, "<u2").reshape(size, size).copy()
    return np.asarray(read_sparse(node, "H"), np.uint16)


def fit_areas(gm: filedb.Tag, values: np.ndarray, ledger: portdrops.Ledger | None = None) -> np.ndarray:
    """``values`` (area ids per tile) with every id the island ``gm`` has no area for (``AreaManagerData``) put in the
    island's first area, where its objects go (``carry_objects``): a tile naming an area the game has no record of
    breaks the island (Cinis' third area 0x4001 in 1800; island-regions "The areas")."""
    keys = area_keys(gm)
    if not keys:
        return values
    values = values.copy()
    for known in np.unique(values):
        if known and int(known) not in keys:
            mask = values == known
            values[mask] = keys[0]
            if ledger is not None:
                ledger.add(
                    "area", f"tiles of area {int(known):#x} put in area {keys[0]:#x}", portdrops.MOVED, int(mask.sum())
                )
    return values


def set_area_values(gm: filedb.Tag, values: np.ndarray, ledger: portdrops.Ledger | None = None) -> None:
    """Write the area ids per tile into the island's ``AreaIDs`` in its layout, the ids its areas lack fitted first."""
    values = fit_areas(gm, values, ledger)
    node = gm.find("AreaIDs")
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


def _resample(w: np.ndarray, n: int) -> np.ndarray:
    have = w.shape[1]
    if have > n:
        f = have // n
        return w.reshape(w.shape[0], n, f, n, f).mean(axis=(2, 4))
    if have < n:
        f = n // have
        return np.repeat(np.repeat(w, f, axis=1), f, axis=2)
    return w


@dataclass(frozen=True)
class Ground:
    """The source's ground as the game lays it, and how its materials and the target's are ordered and mapped."""

    ids: np.ndarray
    alphas: np.ndarray
    size: int
    table: dict[int, int]
    src_order: dict[int, int]
    dst_order: dict[int, int]
    place: largerdonor.Place | None = None  # the source's place in a larger donor (None: the donor's grid is its own)
    fill: int = 0  # the source material the donor's sea bed round the source is laid in


def source_weights(ground: Ground, sx: int, sz: int, win: int) -> tuple[list[int], np.ndarray]:
    """The layered weights of the source's ``win`` tiles at (``sx``, ``sz``); outside the source (a larger donor's sea)
    its sea bed material alone."""
    s_tpt = ground.alphas.shape[0] // ground.size
    if not (0 <= sx < ground.size and 0 <= sz < ground.size):
        return [ground.fill], np.ones((1, win * s_tpt, win * s_tpt), np.float32)
    s_block = ground.alphas.shape[0] // ground.ids.shape[0]
    sa = np.s_[sz * s_tpt : (sz + win) * s_tpt, sx * s_tpt : (sx + win) * s_tpt]
    si = np.s_[
        sz * s_tpt // s_block : (sz + win) * s_tpt // s_block, sx * s_tpt // s_block : (sx + win) * s_tpt // s_block
    ]
    return groundedit.layered_weights(ground.ids[si], ground.alphas[sa], ground.src_order)


def encode_map(ground: Ground, n_ids: int, n_alphas: int) -> tuple[np.ndarray, np.ndarray, int]:
    """One target map (``n_ids`` id words and ``n_alphas`` alpha words across) from the source's ground, window by
    window; returns the id words, the alpha words and the used-material mask."""
    place = ground.place or largerdonor.Place(ground.size, ground.size)
    d_tpt, d_block = n_alphas // place.grid, n_alphas // n_ids
    ids, alphas, used = np.zeros((n_ids, n_ids), np.uint32), np.zeros((n_alphas, n_alphas), np.uint32), 0
    win = math.gcd(WINDOW, ground.size)
    for z0 in range(0, place.grid, win):
        for x0 in range(0, place.grid, win):
            mats, w = source_weights(ground, x0 - place.x, z0 - place.z, win)
            targets = sorted({ground.table.get(m, m) for m in mats})
            dw = np.zeros((len(targets), *w.shape[1:]), np.float32)
            for k, m in enumerate(mats):
                dw[targets.index(ground.table.get(m, m))] += w[k]
            dw = _resample(dw, win * d_tpt).astype(np.float32)
            new_ids, new_alphas, present = groundedit.Painted(targets, dw, ground.dst_order).encode_materials(
                d_tpt, {}, d_block
            )
            ids[
                z0 * d_tpt // d_block : (z0 + win) * d_tpt // d_block,
                x0 * d_tpt // d_block : (x0 + win) * d_tpt // d_block,
            ] = new_ids
            alphas[z0 * d_tpt : (z0 + win) * d_tpt, x0 * d_tpt : (x0 + win) * d_tpt] = new_alphas
            for m in present:
                used |= 1 << int(m)
    return ids, alphas, used


def sea_bed_material(ids: np.ndarray) -> int:
    """The material most common along the edge of the source's ground (its first slot): what its sea bed is."""
    edge = np.concatenate([ids[0], ids[-1], ids[:, 0], ids[:, -1]]) & np.uint32(31)
    return int(np.bincount(edge.astype(np.int64)).argmax())


def carry_ground(ground: Ground, donor, dgd: groundedit.Gamedata, out: dict[str, bytes]) -> dict:
    """Every ground map the donor has, made from the source's ground; the used-material masks say what is used."""
    report, used = {"maps": 0}, 0
    for name in [n for n in dgd.names if n.startswith("materialmaps/") and n.endswith("/matid.dds")]:
        alpha_name = name.replace("matid.dds", "matalpha.dds")
        ids_dds, al_dds = dgd.read(name), dgd.read(alpha_name)
        ids, alphas, mask = encode_map(
            ground, groundedit.dds_u32(ids_dds).shape[0], groundedit.dds_u32(al_dds).shape[0]
        )
        out[name], out[alpha_name] = replace_dds_pixels(ids_dds, ids), replace_dds_pixels(al_dds, alphas)
        used |= mask
        report["maps"] += 1
    t = donor.rd.path("Terrain")
    ids_tag, al_tag = t.find("MaterialIDs2"), t.find("MaterialAlphas2")
    if ids_tag is not None and al_tag is not None and ids_tag.data:
        n_ids, n_al = (int(round((len(a.data) // 4) ** 0.5)) for a in (ids_tag, al_tag))
        ids, alphas, mask = encode_map(ground, n_ids, n_al)
        set_bytes(ids_tag, ids)
        set_bytes(al_tag, alphas)
        used |= mask
        report["maps"] += 1
    umb = t.find("UsedMaterialBits")
    if umb is not None and len(umb.data) % 4 == 0:
        umb.data = np.full(len(umb.data) // 4, used, "<u4").tobytes()
    report["used"] = used
    return report


def carry_props(
    source_rd: filedb.Tag,
    donor_rd: filedb.Tag,
    table: dict[str, str | None],
    place=None,
    ledger: portdrops.Ledger | None = None,
    exists=None,
) -> dict:
    """The source's props in the donor's ``PropGrid``, each instance in the donor's layout (a value only the target's
    instances have from the donor's first), their files by the prop table. ``exists`` (the target game's file test,
    set between games): a prop file no table names that the target lacks is removed with the others; every prop
    removed or renamed is counted by file name in ``ledger``."""
    src_pg, dst_pg = source_rd.path("PropGrid"), donor_rd.path("PropGrid")
    if src_pg is None or dst_pg is None:
        return {}
    sample = next(iter(dst_pg.find("Instances").children), None)
    dst_pg.find("FileNames").children = copy.deepcopy(src_pg.find("FileNames").children)
    instances = []
    for p in src_pg.find("Instances").children:
        if sample is None:
            instances.append(copy.deepcopy(p))
            continue
        mine = {c.name: c for c in p.children}
        instances.append(filedb.Tag(p.name, [copy.deepcopy(mine.get(c.name, c)) for c in sample.children]))
    dst_pg.find("Instances").children = instances
    if place is not None and place.padded:
        largerdonor.shift_props(donor_rd, place)
    return regionconvert.apply_props(donor_rd, table, ledger, exists)


def _placed(o: filedb.Tag, place=None) -> dict:
    item = a7mread.placed(o, "")
    scale = item.scale
    dx, dz = place.shift() if place is not None else (0, 0)
    return {
        "position": [item.position[0] + dx, item.position[1], item.position[2] + dz],
        "rotation": list(item.rotation),
        "scale": list(scale) if isinstance(scale, tuple) else scale,
    }


def carry_objects(
    src,
    dst,
    source,
    donor,
    table: dict[int, int | None],
    where: tuple[str, largerdonor.Place],
    ledger: portdrops.Ledger | None = None,
) -> dict:
    """The source's objects in the donor: an asset both games know as it is (coast lines, rivers, lakes keep their
    curves), a mapped one as a copy of an object of the target asset (``objectclones``: the donor's own, else another
    island's), the rest left out. The donor's own objects are taken off first; ``where``: the donor's path and the
    source's place in it, which moves them; each object left out or swapped is named in ``ledger``."""
    donor_path, place = where
    ledger = ledger if ledger is not None else portdrops.Ledger()
    label = lambda guid, side=src: portdrops.asset_label(side.asset_names, guid)  # noqa: E731
    found = objectclones.samples(donor, a7mread.OBJECT_GROUPS)
    a7m_edit117.clear(donor)
    for _area, doc, _ids in donor._areas_data():
        objs = a7mread.area_objects(doc, "EditorObject")
        if objs is not None:
            objs.children = [c for c in objs.children if not isinstance(c, filedb.Tag)]
    keys = {}
    for (_a, doc, _i), key in zip(donor._areas_data(), area_keys(donor.gm), strict=False):
        keys[key] = doc
    report = {"objects kept": 0, "objects copied": 0, "objects left out": 0}
    added, pairs = [], []
    for (_a, doc, _i), key in zip(source._areas_data(), area_keys(source.gm), strict=False):
        home = keys.get(key) or next(iter(keys.values()))
        if key not in keys:
            n = sum(len(o.children) for g in a7mread.OBJECT_GROUPS if (o := a7mread.area_objects(doc, g)) is not None)
            ledger.add("area", f"objects of area {key:#x} put in the donor's first area", portdrops.MOVED, n)
        for group in a7mread.OBJECT_GROUPS:
            objs = a7mread.area_objects(doc, group)
            target = a7mread.area_objects(home, group)
            for o in objs.children if objs is not None else []:
                guid = o.find("guid") if isinstance(o, filedb.Tag) else None
                if guid is None or o.find("Position") is None:
                    if isinstance(o, filedb.Tag):
                        ledger.add("object", f"{group} entry without asset or position", portdrops.LEFT_OUT)
                    continue
                g = struct.unpack_from("<I", guid.data)[0]
                if (
                    g not in table
                    and target is not None
                    and portdrops.object_kept(src.asset_names, dst.asset_names, g, src.key == dst.key)
                ):
                    kept = copy.deepcopy(o)
                    if place.padded:
                        largerdonor.shift_object(kept, place)
                    target.children.append(kept)
                    report["objects kept"] += 1
                elif table.get(g) is not None:
                    added.append({"guid": table[g], "clone": True, **_placed(o, place)})
                    pairs.append((g, table[g]))
                else:
                    report["objects left out"] += 1
                    why = " (the target game has no such asset)" if g not in table and target is not None else ""
                    why = why or (" (the target island has no such object list)" if g not in table else "")
                    ledger.add("object", label(g) + why, portdrops.LEFT_OUT)
    if added:
        models = AssetModels.read(dst.read, ASSET_GAMES[dst.key])
        donors = templatedonors.for_game(dst.key, dst.files, models, donor_path)
        _rest, copied, missing = objectclones.place_copies({"added_objects": added}, found, donors=donors)
        report["objects copied"] = copied
        report["objects left out"] += len(missing)
    gone = Counter(int(m.split(":")[1]) for m in (missing if added else []))
    for g, to in pairs:
        if gone[to] > 0:  # a mapped asset the target has no object to copy: placed nowhere
            gone[to] -= 1
            ledger.add("object", label(g), portdrops.LEFT_OUT, 1, f"{label(to, dst)}: no object of it to copy")
        else:
            ledger.add("object", label(g), portdrops.MAPPED, 1, label(to, dst))
    report["objects left out"] += portdrops.group_leftovers(source, ledger)
    return report


def neutral_displacement(island) -> list[str]:
    """117's two displacement layers set to no displacement, as the island build writes them: the game's
    ``DisplacementHeightMap`` (a displacement on top of the heights, not heights) zero, rd3d's
    ``CoarseDisplacementMap`` byte 128. Returns the layers the island has (island-regions "Displacement")."""
    done = []
    disp = island.gm.path("TerrainManager/DisplacementHeightMap/HeightMap")
    if disp is not None:
        disp.data = bytes(len(disp.data))
        done.append("DisplacementHeightMap")
    coarse = island.rd.path("Terrain/CoarseDisplacementMap/map")
    if coarse is not None:
        coarse.data = bytes([128]) * len(coarse.data)
        done.append("CoarseDisplacementMap")
    return done


def fit_target(source, donor) -> list[str]:
    """What only one of the two games keeps: the donor's 117-only maps follow the source's shape (no displacement,
    the ford and forest-chunk maps emptied), 1800's street map is emptied."""
    done = []
    if source.gm.path("TerrainManager/DisplacementHeightMap") is None:  # 1800 has none: none, not the donor's
        done += neutral_displacement(donor)
    if source.gm.path("WorldManager/FordGrid") is None and (ford := donor.gm.path("WorldManager/FordGrid")) is not None:
        write_bits(ford, [[0] * donor.size for _ in range(donor.size)])
        done.append("FordGrid")
    chunks = donor.gm.path("RegrowManager/ForestChunkMap")
    if chunks is not None and source.gm.path("RegrowManager/ForestChunkMap") is None:
        grid = read_sparse(chunks, "B")
        write_sparse(chunks, [[0] * len(grid[0]) for _ in grid] if grid else [], "B")
        done.append("ForestChunkMap")
    streets = donor.gm.path("WorldManager/StreetMap/StreetID/val")
    if streets is not None and source.gm.path("WorldManager/StreetMap") is None:
        streets.data = bytes(len(streets.data))
        done.append("StreetMap")
    return done


def link_slots(src, dst, source, tables, target, saved: dict) -> dict:
    """The source's slots by the slot links (``slotlinks``: the rules' suggestions, the person's links over them) into
    the object table; the slots left without a target are named in the report, not dropped unseen."""
    src_assets, mine = src.slots()
    used = Counter(
        struct.unpack_from("<I", o.find("guid").data)[0]
        for _g, o in a7mread.object_tags(source.gm, a7mread.OBJECT_GROUPS)
        if o.find("guid") is not None
    )
    if not any(g in src_assets for g in used):
        return {}
    dst_assets, theirs = dst.slots()
    links = slotlinks.resolve(slotlinks.suggest(src_assets, dst_assets, mine, theirs, target.island_region), saved)
    linked = slotlinks.apply(tables.objects, links, used, src_assets)
    return {"slots linked": linked, "slots unlinked": slotlinks.unlinked(used, links, src_assets)}


def refresh_slots(donor) -> dict:
    """The donor's slot list after the objects changed: 117's by ``slotlist.refresh``, 1800's items rebuilt per group
    from the slot objects of the assets the group lists."""
    placed = [
        (
            o.find("ID").data,
            struct.unpack_from("<I", o.find("guid").data)[0],
            struct.unpack("<3f", o.find("Position").data),
        )
        for _g, o in a7mread.object_tags(donor.gm, SLOT_GROUPS)
        if o.find("ID") is not None and o.find("guid") is not None and o.find("Position") is not None
    ]
    report = slotlist.refresh(donor.info, placed)
    items = donor.info.path(regionconvert.SLOT_ITEMS)
    for group in items.children if items is not None else []:
        if not isinstance(group, filedb.Tag) or not group.children:
            continue
        sample = group.children[0]
        listed = {
            struct.unpack_from("<I", i.find("ObjectGuid").data)[0]
            for i in group.children
            if isinstance(i, filedb.Tag) and i.find("ObjectGuid") is not None
        }
        if not listed:  # the group's header (its slot kind), not a list of slots
            continue
        group.children = []
        for oid, g, pos in placed:
            if g in listed:
                item = copy.deepcopy(sample)
                item.find("ObjectId").data, item.find("ObjectGuid").data = oid, struct.pack("<I", g)
                item.find("Position").data = struct.pack("<3f", *pos)
                group.children.append(item)
        report["slot items"] = report.get("slot items", 0) + len(group.children)
    return report


def audit_data(source, donor, ledger: portdrops.Ledger, padded: bool) -> None:
    """What the source's three data files hold that no step of the port carries: each node with data the donor keeps
    its own value of (``DONOR``) or does not have (``LEFT_OUT``), by name and bytes. Run before the donor is changed."""
    gm = {"TerrainManager/HeightMap" if p == "TerrainManager" and padded else p for p in GRAFT_GAMEDATA}
    portdrops.untaken(source.gm, donor.gm, gm | UNTAKEN_GAMEDATA_HANDLED, "", ledger)
    handled = {f"Terrain/{p}" for p in GRAFT_TERRAIN} | UNTAKEN_TERRAIN_HANDLED
    portdrops.untaken(source.rd, donor.rd, {*GRAFT_RD3D, "PropGrid"} | handled, "", ledger)
    portdrops.untaken(source.info, donor.info, {*GRAFT_INFO, "ObjectMetaInfo"}, "", ledger)


def carry_shape(
    source,
    donor,
    dgd: groundedit.Gamedata,
    place: largerdonor.Place,
    out: dict[str, bytes],
    ledger: portdrops.Ledger | None = None,
) -> dict:
    """The source's shape written into the donor: its values grafted where the grids are one size, embedded in the
    donor's middle (``largerdonor``) where the donor is larger. Returns what was written; what is left out goes
    into ``ledger``."""
    report: dict = {}
    ledger = ledger if ledger is not None else portdrops.Ledger()
    audit_data(source, donor, ledger, place.padded)
    if not place.padded:
        report["grafted"] = graft_paths(source.gm, donor.gm, GRAFT_GAMEDATA, ledger)
        report["grafted"] += graft_paths(
            source.rd.path("Terrain"), donor.rd.path("Terrain"), GRAFT_TERRAIN, ledger, "Terrain/"
        )
        report["grafted"] += graft_paths(source.rd, donor.rd, GRAFT_RD3D, ledger)
        report["grafted"] += graft_paths(source.info, donor.info, GRAFT_INFO, ledger)
        report["fitted"] = fit_target(source, donor)
        set_area_values(donor.gm, area_values(source.gm, source.size), ledger)
        return report
    report["embedded"] = {"place": [place.x, place.z, place.size, place.grid]}
    report["embedded"]["heights"] = largerdonor.embed_heights(source, donor, dgd, place, out)
    report["embedded"]["grids"] = largerdonor.embed_bits(donor.gm, source.gm, place)
    report["embedded"]["sea"] = largerdonor.embed_sea(source, donor, place)
    report["embedded"]["environment"] = largerdonor.embed_environment(source, donor, place)
    report["fitted"] = fit_target(source, donor)
    values = np.zeros((place.grid, place.grid), np.uint16)
    values[place.window()] = area_values(source.gm, source.size)
    set_area_values(donor.gm, values, ledger)
    graft_paths(source.info, donor.info, ("ObjectMetaInfo/StartCoastDirection",))
    largerdonor.shift_rect(donor.info, donor.gm, source.info, source.gm, place)
    return report


def carry_shape_files(sgd, dgd, out: dict[str, bytes], materials: dict[int, int], place) -> dict[str, bytes]:
    """The ``_gamedata`` files of the ported island: the source's stored meshes (their used-material masks on the IDs
    of ``materials``), height pages, tint pages and map pictures where the grids are one size (the two games' files
    are one layout: island-regions "Meshes and pages in both games"); in a larger donor the donor's, with the edit's
    changes in ``out`` and the source's tints in its window."""
    if place.padded:
        tints = largerdonor.embed_tints(sgd, dgd, place)
        return {n: out[n] if n in out else tints[n] if n in tints else dgd.read(n) for n in dgd.names}
    version = next((filedb.version(dgd.read(n)) for n in dgd.names if n.endswith(".tmc")), 3)
    for name in sgd.names:
        if name.startswith(SHAPE_FILES) or ("/" not in name and name in dgd.names):  # the shape; the map pictures
            data = sgd.read(name)
            if name.endswith(".tmc"):
                data = regionconvert.remap_mesh_bits(data, materials) or data
            out[name] = refile(name, data, version)
    return {n: out.get(n) or dgd.read(n) for n in dgd.names if not n.startswith(SHAPE_FILES)} | out


def tint_range(island) -> tuple[float, float] | None:
    """The tint over an island's land, as it opens: the mean and spread of its far tint picture's luminance byte."""
    img = tintpages.far_picture(island.rd)
    if img is None:
        return None
    at = (np.arange(img.shape[0]) + 0.5) * island.size / img.shape[0]
    zz, xx = np.meshgrid(at, at, indexing="ij")
    return tintpages.land_spread(img, coarse(groundedit.coarse_heights(island), xx, zz) > groundedit.LAND_ABOVE)


def fit_tints(gamedata: dict[str, bytes], rd, ranges: tuple) -> int:
    """Every tint page and the far tint picture carried from the source, their tint fitted into the donor's range
    (``ranges``: the source's and the donor's ``tint_range``). A tint multiplies the ground's colours and each game
    sets it for its own materials: 1800's Old World lifts its dark rock up to 2.7 times, which turns Latium's light
    limestone white (island-regions "The tint between games"). Returns the pages fitted."""
    src, dst = ranges
    if src is None or dst is None:
        return 0
    pages = [n for n in gamedata if tintpages.PAGE.search(n)]
    for name in pages:
        gamedata[name] = tintpages.fit_blocks(gamedata[name], src, dst)
    base = rd.path("Terrain/TintMapLevels/BaseTextureData/map")
    if base is not None:
        base.data = tintpages.fit_blocks(base.data, src, dst)
    return len(pages)


def transfer(src, dst, request, target, colour=None, log=lambda _line: None) -> Carried:
    """Carry the island ``request.source`` of ``src``'s game into ``dst``'s, region ``target``; ``log`` hears the stages."""
    own = regions.region_of(src.key, request.source)
    source = src.open(request.source)
    sgd = groundedit.gamedata(src.files, request.source)
    donor_path, place = donor_for(dst, target, source, request.fit == "map")  # islandconvert.FIT_MAP
    log(tr("islands.carrying_island_onto", donor=donor_path.rsplit("/", 1)[-1]))
    donor = dst.open(donor_path)
    dgd = groundedit.gamedata(dst.files, donor_path)
    ranges = (tint_range(source), tint_range(donor))  # the donor's own land, before the source's shape is written
    regionconvert.set_material_set(donor.rd, target.material_set)  # the region's own set, not a donor's variant
    src_set, dst_set = src.text(material_set_of(source, src.key)), dst.text(material_set_of(donor, dst.key))
    tables = regiontables.build(
        regiontables.Sources(own, src_set, src.inventory(own), src.asset_names),
        regiontables.Sources(target, dst_set, dst.inventory(target), dst.asset_names),
        colour,
    )
    materials = regiontables.with_unlisted(tables.materials, dst_set)
    report: dict = {"donor": donor_path}
    ledger = portdrops.Ledger()
    report |= link_slots(src, dst, source, tables, target, dict(request.slot_links))
    out: dict[str, bytes] = {}
    log(tr("islands.writing_shape_heights_land_rivers"))
    report |= carry_shape(source, donor, dgd, place, out, ledger)
    log(tr("islands.carrying_ground"))
    sids, salphas = regionconvert.material_maps(source, sgd)
    portdrops.ground_entries(ledger, sids, source.size, (src_set, dst_set), materials)
    portdrops.files_left(sgd, dgd, ledger)
    ground = Ground(
        sids,
        salphas,
        source.size,
        materials,
        set_ranks(src_set),
        set_ranks(dst_set),
        place,
        sea_bed_material(sids),
    )
    report["ground"] = carry_ground(ground, donor, dgd, out)
    log(tr("islands.carrying_objects_props"))
    reskinned = portreskin.Reskinned()
    if request.reskin:
        reskinned = portreskin.for_port(src, dst, source.rd, tables, own, target, dst_set, ledger)
        report["reskinned props"] = reskinned.count
    report |= carry_objects(
        src, dst, source, donor, tables.objects, (donor_path, place), ledger
    )  # clears the donor's props too
    report |= carry_props(source.rd, donor.rd, tables.props, place, ledger, dst.files.exists)
    report["slot list"] = refresh_slots(donor)
    gamedata = carry_shape_files(sgd, dgd, out, materials, place)
    if dst.key == "anno117":  # 1800 flags its top mesh level and stores nothing there; 117 draws it from afar
        report["meshes filled"] = fill_empty_meshes(donor.rd, donor.size, gamedata)
    if place.padded:
        report["meshes"] = largerdonor.mark_meshes(gamedata, report["ground"]["used"])
    report["far"] = portpictures.far_tint(source.rd, donor.rd, place)
    report["tint pages fitted"] = fit_tints(gamedata, donor.rd, ranges)
    ledger.add("ground", "tint pages", portdrops.CLAMPED, report["tint pages fitted"], "the target's tint range")
    if colour is not None:
        log(tr("islands.drawing_map_pictures"))
        final = groundedit.Gamedata(sorted(gamedata), gamedata.__getitem__)
        gamedata |= portpictures.redraw(donor, final, dst_set, lambda file: colour("dst", file))
    report["dropped"] = ledger.entries
    return Carried(donor.write(), donor.write_info(), gamedata, donor_path, tables, report, reskinned.files)
