"""An opened 117 or 1800 island turned into an island of another region of its game, by the tables of
``islands.regiontables``: material IDs in every ground map and used-material mask, the material set, the ambient and
vegetation set, the prop files, the objects' assets and the slot list.

``apply(isl, gamedata, tables)`` is the entry (``isl``: a ``formats.a7m.edit117.IslandFile`` or
``formats.a7m.reader.IslandFile1800``, ``gamedata`` its ``groundedit.Gamedata``); it returns the changed ``_gamedata`` files and a report, and leaves an
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
from ..islands.regiontables import Tables, norm
from . import portdrops
from .assetmodels import ASSETS_117, ASSETS_1800
from .formats.a7m.reader import DEFAULT_MATERIAL_SET
from .groundedit import Gamedata, dds_u32

SLOTS = 6  # material slots per id word
SLOT_ITEMS = "ObjectMetaInfo/SlotObjects"  # 1800's slot list: items with an ObjectGuid each
ASSET_GAMES = {"anno117": ASSETS_117, "anno1800": ASSETS_1800}
DEFAULT_SETS = {"anno117": "", "anno1800": DEFAULT_MATERIAL_SET}  # the set an island naming none is drawn with


def material_set_of(isl, game: str) -> str:
    """The material set an opened island is drawn with (its own name, else its game's default)."""
    attr = isl.rd.path("Terrain").find("MaterialSetFileName")
    named = attr.data.decode("utf-16-le").replace("\\", "/") if attr is not None else ""
    return named or DEFAULT_SETS[game]


def material_maps(isl, gd: Gamedata) -> tuple[np.ndarray, np.ndarray]:
    """The most detailed ground maps of an island: 117's level 0 in ``_gamedata``, else rd3d's."""
    if "materialmaps/0/matid.dds" in gd.names:
        return dds_u32(gd.read("materialmaps/0/matid.dds")), dds_u32(gd.read("materialmaps/0/matalpha.dds"))
    t = isl.rd.path("Terrain")
    ids, alphas = t.find("MaterialIDs2").data, t.find("MaterialAlphas2").data
    n, m = int(round((len(alphas) // 4) ** 0.5)), int(round((len(ids) // 4) ** 0.5))
    return np.frombuffer(ids, "<u4").reshape(m, m), np.frombuffer(alphas, "<u4").reshape(n, n)


def lookup(table: dict[int, int]) -> np.ndarray:
    """The 32 material IDs as the table sends them (an ID it does not name stays)."""
    lut = np.arange(32, dtype=np.uint32)
    for src, dst in table.items():
        if 0 <= src < 32:
            lut[src] = dst
    return lut


def remap_ids(words: np.ndarray, table: dict[int, int]) -> np.ndarray:
    """Id words (six 5-bit IDs each) with every ID replaced by its target."""
    lut, out = lookup(table), np.zeros_like(words, dtype=np.uint32)
    for s in range(SLOTS):
        out |= lut[(words >> np.uint32(5 * s)) & np.uint32(31)] << np.uint32(5 * s)
    return out | (words & np.uint32(0xC0000000))  # the two bits over the six IDs are kept


def remap_bits(masks: np.ndarray, table: dict[int, int]) -> np.ndarray:
    """Used-material masks (a bit per ID) with each set bit moved to its target ID's."""
    lut, out = lookup(table), np.zeros_like(masks, dtype=np.uint32)
    for bit in range(32):
        out |= np.where(masks & np.uint32(1 << bit), np.uint32(1) << lut[bit], np.uint32(0)).astype(np.uint32)
    return out


def rewrite_mesh_bits(raw: bytes, new: Callable[[int], int]) -> bytes | None:
    """A stored mesh file with each node's used-material mask ``new(mask)``; None when no node changes."""
    doc, changed = filedb.read(raw), False
    nodes = doc.path("Meshes/map")
    for e in nodes.children if nodes is not None else []:
        bits = e.find("UsedMaterialBits") if isinstance(e, filedb.Tag) else None
        if bits is not None and len(bits.data) == 4:
            value = struct.pack("<I", new(struct.unpack("<I", bits.data)[0]))
            if value != bits.data:
                bits.data, changed = value, True
    return filedb.write(doc, *filedb.name_ids(raw), version=filedb.version(raw)) if changed else None


def remap_mesh_bits(raw: bytes, table: dict[int, int]) -> bytes | None:
    """A stored mesh file with the used-material masks of its nodes on the IDs of ``table``."""
    return rewrite_mesh_bits(raw, lambda mask: int(remap_bits(np.array([mask], np.uint32), table)[0]))


def apply_materials(isl, gd: Gamedata, table: dict[int, int], out: dict[str, bytes]) -> dict:
    """Every ground map and used-material mask of the island with the IDs of ``table``."""
    report = {"maps": 0, "meshes": 0}
    for name in [n for n in gd.names if n.startswith("materialmaps/") and n.endswith("/matid.dds")]:
        raw = gd.read(name)
        new = replace_dds_pixels(raw, remap_ids(dds_u32(raw), table))
        if new != raw:
            out[name] = new
            report["maps"] += 1
    t = isl.rd.path("Terrain")
    ids = t.find("MaterialIDs2")
    if ids is not None and ids.data:
        set_bytes(ids, remap_ids(np.frombuffer(ids.data, "<u4"), table))
        report["maps"] += 1
    umb = t.find("UsedMaterialBits")
    if umb is not None and len(umb.data) % 4 == 0:
        set_bytes(umb, remap_bits(np.frombuffer(umb.data, "<u4"), table))
    for name in [n for n in gd.names if n.startswith("meshes/") and n.endswith(".tmc")]:
        new = remap_mesh_bits(gd.read(name), table)
        if new is not None:
            out[name] = new
            report["meshes"] += 1
    return report


def set_material_set(rd: filedb.Tag, path: str) -> str:
    """Name the material set ``path`` in the island's ``Terrain`` ("" removes the name: the engine's default set);
    returns what was named before ("" for none)."""
    t = rd.path("Terrain")
    attr = t.find("MaterialSetFileName")
    old = attr.data.decode("utf-16-le") if attr is not None else ""
    if not path:
        if attr is not None:
            t.children.remove(attr)
        return old
    data = (path.replace("/", "\\") if "\\" in old else path).encode("utf-16-le")
    if attr is not None:
        attr.data = data
    else:  # 1800 names it after Levels, before the maps
        at = next((k for k, c in enumerate(t.children) if c.name == "MaterialIDs2"), len(t.children))
        t.children.insert(at, filedb.Attrib("MaterialSetFileName", data))
    return old


def apply_props(
    rd: filedb.Tag, table: dict[str, str | None], ledger: portdrops.Ledger | None = None, exists=None
) -> dict:
    """Prop files renamed by the table; the instances of a prop the table removes (None) taken off. ``exists`` (the
    target game's file test, set between games) also removes a prop file no table names that the target lacks.
    Every prop swapped or removed is counted by file in ``ledger`` (its instances)."""
    names = rd.path("PropGrid/FileNames")
    inst = rd.path("PropGrid/Instances")
    report = {"prop files": 0, "props removed": 0}
    if names is None or not (table or exists):
        return report
    used: Counter = Counter()
    for p in inst.children if inst is not None else []:
        if isinstance(p, filedb.Tag) and p.find("Index") is not None:
            used[struct.unpack_from("<I", p.find("Index").data)[0]] += 1
    short = lambda path: norm(path).split("data/", 1)[-1]  # noqa: E731
    gone = set()
    for k, attr in enumerate(names.children):
        key = norm(attr.data.decode("utf-16-le"))
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
        old = attr.data.decode("utf-16-le")
        attr.data = (table[key].replace("/", "\\") if "\\" in old else table[key]).encode("utf-16-le")
        report["prop files"] += 1
    if gone and inst is not None:
        keep = [p for p in inst.children if struct.unpack_from("<I", p.find("Index").data)[0] not in gone]
        report["props removed"] = len(inst.children) - len(keep)
        inst.children = keep
    return report


def apply_objects(isl, table: dict[int, int | None], ledger: portdrops.Ledger | None = None, names=None) -> dict:
    """Each object's asset as the table says; an object whose asset goes to None is removed. Returns the counts;
    ``ledger`` names each object removed or swapped (``names``: GUID -> (template, name))."""
    report = {"objects changed": 0, "objects removed": 0}
    if not table:
        return report
    for _area, doc, _ids in isl._areas_data():
        for group in a7mread.OBJECT_GROUPS:
            objs = a7mread.area_objects(doc, group)
            if objs is None:
                continue
            keep = []
            for o in objs.children:
                guid = o.find("guid") if isinstance(o, filedb.Tag) else None
                g = struct.unpack_from("<I", guid.data)[0] if guid is not None and len(guid.data) >= 4 else None
                if g in table and table[g] is None:
                    report["objects removed"] += 1
                    if ledger is not None:
                        ledger.add("object", portdrops.asset_label(names, g), portdrops.LEFT_OUT)
                    continue
                if g in table:
                    if ledger is not None:
                        ledger.add("object", portdrops.asset_label(names, g), portdrops.MAPPED, 1,
                                   portdrops.asset_label(names, table[g]))  # fmt: skip
                    guid.data = struct.pack("<I", table[g]) + guid.data[4:]
                    report["objects changed"] += 1
                keep.append(o)
            objs.children = keep
    return report


def apply_slot_list(info: filedb.Tag, table: dict[int, int | None]) -> int:
    """The ``.a7minfo``'s slot list after the object table (117's by slot asset, 1800's per item); returns the
    entries changed or dropped."""
    done = 0
    listed = slotlist.read(info)
    if listed and any(g in table for g in listed):
        merged: dict[int, list] = {}
        for guid, entries in listed.items():
            target = table.get(guid, guid)
            done += len(entries) if guid in table else 0
            if target is not None:
                merged.setdefault(target, []).extend(entries)
        slotlist.write(info, merged)
    items = info.path(SLOT_ITEMS)
    for group in items.children if items is not None else []:
        if not isinstance(group, filedb.Tag):
            continue
        keep = []
        for item in group.children:
            guid = item.find("ObjectGuid") if isinstance(item, filedb.Tag) else None
            g = struct.unpack_from("<I", guid.data)[0] if guid is not None else None
            if g in table:
                done += 1
                if table[g] is None:
                    continue
                guid.data = struct.pack("<I", table[g])
            keep.append(item)
        group.children = keep
    return done


def apply(
    isl, gd: Gamedata, tables: Tables, ledger: portdrops.Ledger | None = None, names=None
) -> tuple[dict[str, bytes], dict]:
    """Turn the opened island into the target region's by ``tables``; returns the changed ``_gamedata`` files and what
    was done (``ledger`` and ``names``: what is removed or swapped, by name)."""
    out: dict[str, bytes] = {}
    report: dict = {}
    if tables.changes_ids():
        report["ground"] = apply_materials(isl, gd, tables.materials, out)
    if tables.material_set is not None:
        report["material set"] = [set_material_set(isl.rd, tables.material_set), tables.material_set]
    if tables.session:
        report["session"] = sessionsettings.apply(isl.gm, tables.session, isl.size)
    report |= apply_props(isl.rd, tables.props, ledger)
    report |= apply_objects(isl, tables.objects, ledger, names)
    report["slot list"] = apply_slot_list(isl.info, tables.objects)
    return out, report
