"""An opened 117 or 1800 island turned into an island of another region of its game, by the tables of
``islands.regiontables``: material IDs in every ground map and used-material mask, the material set, the ambient and
vegetation set, the prop files, the objects' assets and the slot list.

``apply(island, gamedata, tables)`` is the entry (``island``: a ``formats.a7m.edit117.CIslandFile`` or
``formats.a7m.reader.CIslandFile1800``, ``gamedata`` its ``groundedit.SGamedata``); it returns the changed ``_gamedata`` files and a report, and leaves an
island the tables do not touch as it was. Rules: island-regions "The mapping".
"""

from __future__ import annotations

import struct
from collections import Counter
from collections.abc import Callable

import numpy as np

from ..formats import a7mread, filedb
from ..islands import sessionsettings, slotlist
from ..islands.islandterrain import replace_dds_pixels, set_bytes
from ..islands.regiontables import STables, norm
from . import portdrops
from .assetmodels import ASSETS_117, ASSETS_1800
from .formats.a7m.reader import DEFAULT_MATERIAL_SET
from .groundedit import SGamedata, dds_u32

SLOTS = 6  # material slots per id word
SLOT_ITEMS = "ObjectMetaInfo/SlotObjects"  # 1800's slot list: items with an ObjectGuid each
ASSET_GAMES = {"anno117": ASSETS_117, "anno1800": ASSETS_1800}
DEFAULT_SETS = {"anno117": "", "anno1800": DEFAULT_MATERIAL_SET}  # the set an island naming none is drawn with


def material_set_of(island, game: str) -> str:
    """The material set an opened island is drawn with (its own name, else its game's default)."""
    attribute = island.rd3d.path("Terrain").find("MaterialSetFileName")
    named = attribute.data.decode("utf-16-le").replace("\\", "/") if attribute is not None else ""
    return named or DEFAULT_SETS[game]


def material_maps(island, gamedata: SGamedata) -> tuple[np.ndarray, np.ndarray]:
    """The most detailed ground maps of an island: 117's level 0 in ``_gamedata``, else rd3d's."""
    if "materialmaps/0/matid.dds" in gamedata.names:
        return dds_u32(gamedata.read("materialmaps/0/matid.dds")), dds_u32(gamedata.read("materialmaps/0/matalpha.dds"))
    terrain = island.rd3d.path("Terrain")
    ids, alphas = terrain.find("MaterialIDs2").data, terrain.find("MaterialAlphas2").data
    alpha_side, ids_side = int(round((len(alphas) // 4) ** 0.5)), int(round((len(ids) // 4) ** 0.5))
    return np.frombuffer(ids, "<u4").reshape(ids_side, ids_side), np.frombuffer(alphas, "<u4").reshape(
        alpha_side, alpha_side
    )


def lookup(table: dict[int, int]) -> np.ndarray:
    """The 32 material IDs as the table sends them (an ID it does not name stays)."""
    id_table = np.arange(32, dtype=np.uint32)
    for source_id, target_id in table.items():
        if 0 <= source_id < 32:
            id_table[source_id] = target_id
    return id_table


def remap_ids(words: np.ndarray, table: dict[int, int]) -> np.ndarray:
    """Id words (six 5-bit IDs each) with every ID replaced by its target."""
    id_table, out = lookup(table), np.zeros_like(words, dtype=np.uint32)
    for slot in range(SLOTS):
        out |= id_table[(words >> np.uint32(5 * slot)) & np.uint32(31)] << np.uint32(5 * slot)
    return out | (words & np.uint32(0xC0000000))  # the two bits over the six IDs are kept


def remap_bits(masks: np.ndarray, table: dict[int, int]) -> np.ndarray:
    """Used-material masks (a bit per ID) with each set bit moved to its target ID's."""
    id_table, out = lookup(table), np.zeros_like(masks, dtype=np.uint32)
    for bit in range(32):
        out |= np.where(masks & np.uint32(1 << bit), np.uint32(1) << id_table[bit], np.uint32(0)).astype(np.uint32)
    return out


def rewrite_mesh_bits(raw: bytes, new: Callable[[int], int]) -> bytes | None:
    """A stored mesh file with each node's used-material mask ``new(mask)``; None when no node changes."""
    doc, changed = filedb.read(raw), False
    nodes = doc.path("Meshes/map")
    for entry in nodes.children if nodes is not None else []:
        bits = entry.find("UsedMaterialBits") if isinstance(entry, filedb.STag) else None
        if bits is not None and len(bits.data) == 4:
            value = struct.pack("<I", new(struct.unpack("<I", bits.data)[0]))
            if value != bits.data:
                bits.data, changed = value, True
    return filedb.write(doc, *filedb.name_ids(raw), version=filedb.version(raw)) if changed else None


def remap_mesh_bits(raw: bytes, table: dict[int, int]) -> bytes | None:
    """A stored mesh file with the used-material masks of its nodes on the IDs of ``table``."""
    return rewrite_mesh_bits(raw, lambda mask: int(remap_bits(np.array([mask], np.uint32), table)[0]))


def apply_materials(island, gamedata: SGamedata, table: dict[int, int], out: dict[str, bytes]) -> dict:
    """Every ground map and used-material mask of the island with the IDs of ``table``."""
    report = {"maps": 0, "meshes": 0}
    for name in [
        file_name
        for file_name in gamedata.names
        if file_name.startswith("materialmaps/") and file_name.endswith("/matid.dds")
    ]:
        raw = gamedata.read(name)
        new = replace_dds_pixels(raw, remap_ids(dds_u32(raw), table))
        if new != raw:
            out[name] = new
            report["maps"] += 1
    terrain = island.rd3d.path("Terrain")
    ids = terrain.find("MaterialIDs2")
    if ids is not None and ids.data:
        set_bytes(ids, remap_ids(np.frombuffer(ids.data, "<u4"), table))
        report["maps"] += 1
    used_bits = terrain.find("UsedMaterialBits")
    if used_bits is not None and len(used_bits.data) % 4 == 0:
        set_bytes(used_bits, remap_bits(np.frombuffer(used_bits.data, "<u4"), table))
    for name in [
        file_name for file_name in gamedata.names if file_name.startswith("meshes/") and file_name.endswith(".tmc")
    ]:
        new = remap_mesh_bits(gamedata.read(name), table)
        if new is not None:
            out[name] = new
            report["meshes"] += 1
    return report


def set_material_set(rd: filedb.STag, path: str) -> str:
    """Name the material set ``path`` in the island's ``Terrain`` ("" removes the name: the engine's default set);
    returns what was named before ("" for none)."""
    terrain = rd.path("Terrain")
    attribute = terrain.find("MaterialSetFileName")
    old = attribute.data.decode("utf-16-le") if attribute is not None else ""
    if not path:
        if attribute is not None:
            terrain.children.remove(attribute)
        return old
    data = (path.replace("/", "\\") if "\\" in old else path).encode("utf-16-le")
    if attribute is not None:
        attribute.data = data
    else:  # 1800 names it after Levels, before the maps
        at = next(
            (k for k, child in enumerate(terrain.children) if child.name == "MaterialIDs2"), len(terrain.children)
        )
        terrain.children.insert(at, filedb.SAttrib("MaterialSetFileName", data))
    return old


def apply_props(
    rd: filedb.STag, table: dict[str, str | None], ledger: portdrops.CLedger | None = None, exists=None
) -> dict:
    """Prop files renamed by the table; the instances of a prop the table removes (None) taken off. ``exists`` (the
    target game's file test, set between games) also removes a prop file no table names that the target lacks.
    Every prop swapped or removed is counted by file in ``ledger`` (its instances)."""
    names = rd.path("PropGrid/FileNames")
    instances = rd.path("PropGrid/Instances")
    report = {"prop files": 0, "props removed": 0}
    if names is None or not (table or exists):
        return report
    used: Counter = Counter()
    for prop in instances.children if instances is not None else []:
        if isinstance(prop, filedb.STag) and prop.find("Index") is not None:
            used[struct.unpack_from("<I", prop.find("Index").data)[0]] += 1
    short = lambda path: norm(path).split("data/", 1)[-1]  # noqa: E731
    gone = set()
    for k, attribute in enumerate(names.children):
        key = norm(attribute.data.decode("utf-16-le"))
        if key not in table and exists is not None and not exists(key):
            table = {**table, key: None}
            why = " (the target game has no such file)"
        else:
            why = ""
        if key not in table:
            continue
        if ledger is not None and used[k]:
            ledger.add("prop", short(key) + why, portdrops.LEFT_OUT if table[key] is None else portdrops.MAPPED,
                       used[k], "" if table[key] is None else short(table[key]))  # fmt: skip
        if table[key] is None:
            gone.add(k)
            continue
        old = attribute.data.decode("utf-16-le")
        attribute.data = (table[key].replace("/", "\\") if "\\" in old else table[key]).encode("utf-16-le")
        report["prop files"] += 1
    if gone and instances is not None:
        keep = [prop for prop in instances.children if struct.unpack_from("<I", prop.find("Index").data)[0] not in gone]
        report["props removed"] = len(instances.children) - len(keep)
        instances.children = keep
    return report


def apply_objects(island, table: dict[int, int | None], ledger: portdrops.CLedger | None = None, names=None) -> dict:
    """Each object's asset as the table says; an object whose asset goes to None is removed. Returns the counts;
    ``ledger`` names each object removed or swapped (``names``: GUID -> (template, name))."""
    report = {"objects changed": 0, "objects removed": 0}
    if not table:
        return report
    for _area, doc, _ids in island._areas_data():
        for group in a7mread.OBJECT_GROUPS:
            objects = a7mread.area_objects(doc, group)
            if objects is None:
                continue
            keep = []
            for placed_object in objects.children:
                guid = placed_object.find("guid") if isinstance(placed_object, filedb.STag) else None
                asset_guid = (
                    struct.unpack_from("<I", guid.data)[0] if guid is not None and len(guid.data) >= 4 else None
                )
                if asset_guid in table and table[asset_guid] is None:
                    report["objects removed"] += 1
                    if ledger is not None:
                        ledger.add("object", portdrops.asset_label(names, asset_guid), portdrops.LEFT_OUT)
                    continue
                if asset_guid in table:
                    if ledger is not None:
                        ledger.add("object", portdrops.asset_label(names, asset_guid), portdrops.MAPPED, 1,
                                   portdrops.asset_label(names, table[asset_guid]))  # fmt: skip
                    guid.data = struct.pack("<I", table[asset_guid]) + guid.data[4:]
                    report["objects changed"] += 1
                keep.append(placed_object)
            objects.children = keep
    return report


def apply_slot_list(a7minfo: filedb.STag, table: dict[int, int | None]) -> int:
    """The ``.a7minfo``'s slot list after the object table (117's by slot asset, 1800's per item); returns the
    entries changed or dropped."""
    done = 0
    listed = slotlist.read(a7minfo)
    if listed and any(asset_guid in table for asset_guid in listed):
        merged: dict[int, list] = {}
        for guid, entries in listed.items():
            target = table.get(guid, guid)
            done += len(entries) if guid in table else 0
            if target is not None:
                merged.setdefault(target, []).extend(entries)
        slotlist.write(a7minfo, merged)
    items = a7minfo.path(SLOT_ITEMS)
    for group in items.children if items is not None else []:
        if not isinstance(group, filedb.STag):
            continue
        keep = []
        for item in group.children:
            guid = item.find("ObjectGuid") if isinstance(item, filedb.STag) else None
            asset_guid = struct.unpack_from("<I", guid.data)[0] if guid is not None else None
            if asset_guid in table:
                done += 1
                if table[asset_guid] is None:
                    continue
                guid.data = struct.pack("<I", table[asset_guid])
            keep.append(item)
        group.children = keep
    return done


def apply(
    island, gamedata: SGamedata, tables: STables, ledger: portdrops.CLedger | None = None, names=None
) -> tuple[dict[str, bytes], dict]:
    """Turn the opened island into the target region's by ``tables``; returns the changed ``_gamedata`` files and what
    was done (``ledger`` and ``names``: what is removed or swapped, by name)."""
    out: dict[str, bytes] = {}
    report: dict = {}
    if tables.changes_ids():
        report["ground"] = apply_materials(island, gamedata, tables.materials, out)
    if tables.material_set is not None:
        report["material set"] = [set_material_set(island.rd3d, tables.material_set), tables.material_set]
    if tables.session:
        report["session"] = sessionsettings.apply(island.game_session_manager, tables.session, island.size)
    report |= apply_props(island.rd3d, tables.props, ledger)
    report |= apply_objects(island, tables.objects, ledger, names)
    report["slot list"] = apply_slot_list(island.a7minfo, tables.objects)
    return out, report
