"""What an island port does not carry as it was, said by name and count: objects and props whose asset the target
game lacks, ground materials merged or replaced, island data the target keeps from its donor or does not have.

A port collects every such case in a ``CLedger``; ``report["dropped"]`` holds its entries (``CLedger.entries``: dicts of
``category``, ``what``, ``fate``, ``count``, ``to``) and ``lines`` writes them for the log, the command line and the
mod's ``port-report.txt``. Nothing a port leaves out or swaps is silent. docs/island-regions.md "What a port does not carry".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..formats import filedb
from ..infra.i18n import tr

# fates: what became of the thing
LEFT_OUT = "left out"  # not in the ported island at all
MAPPED = "mapped"  # replaced by the target's own asset, material or prop of the same kind (``to`` names it)
DONOR = "from the donor"  # the target takes the donor island's value, not the source's
MERGED = "merged"  # several source ground materials share one target material (``to``)
CLAMPED = "clamped"  # kept, with the value cut to the target's range
MOVED = "moved"  # kept, in another place than the source's (an area the donor lacks)

CATEGORIES = ("object", "prop", "ground", "island data", "height", "area")
SHOWN = 12  # entries per category in the log; the report file holds all


@dataclass
class CLedger:
    """The cases of one port, one entry per (category, what, fate, to) with the count added up."""

    m_found: Counter = field(default_factory=Counter)

    def add(self, category: str, what: str, fate: str = LEFT_OUT, count: int = 1, target: str = "") -> None:
        if count > 0:
            self.m_found[category, what, fate, target] += count

    @property
    def entries(self) -> list[dict]:
        order = {category: i for i, category in enumerate(CATEGORIES)}
        keys = sorted(self.m_found, key=lambda k: (order.get(k[0], 99), k[2] != LEFT_OUT, -self.m_found[k], k[1]))
        return [
            {
                "category": category,
                "what": what,
                "fate": fate,
                "count": self.m_found[category, what, fate, target],
                "to": target,
            }
            for category, what, fate, target in keys
        ]

    def count(self, fate: str = LEFT_OUT) -> int:
        """How many things have this fate."""
        return sum(
            amount for (_category, _what, entry_fate, _target), amount in self.m_found.items() if entry_fate == fate
        )


def line(entry: dict) -> str:
    """One entry as a sentence ("object Slot Roman River: left out, 3")."""
    target_suffix = f" -> {entry['to']}" if entry.get("to") else ""
    return f"{entry['category']} {entry['what']}: {entry['fate']}{target_suffix}, {entry['count']}"


def lines(entries: list[dict], limit: int | None = SHOWN) -> list[str]:
    """The entries as log lines, at most ``limit`` per category (the rest as one line that says how many)."""
    out, per = [], Counter()
    hidden: Counter = Counter()
    for entry in entries:
        per[entry["category"]] += 1
        if limit is not None and per[entry["category"]] > limit:
            hidden[entry["category"]] += 1
        else:
            out.append(line(entry))
    out += [tr("islands.drops_more", n=hidden_count, category=category) for category, hidden_count in hidden.items()]
    return out


def totals(entries: list[dict]) -> dict[str, int]:
    """The summary line's numbers, "<category> <fate>": objects and props as counted, ground materials and island data
    by entry (their counts are tiles and bytes)."""
    out: Counter = Counter()
    for e in entries:
        out[f"{e['category']} {e['fate']}"] += e["count"] if e["category"] in ("object", "prop", "area") else 1
    return dict(out)


def asset_label(names, guid: int) -> str:
    """An asset as ``template/name`` (``names``: GUID -> (template, name) or None), else its number."""
    template_and_name = names(guid) if names is not None else None
    return f"{template_and_name[0]}/{template_and_name[1]}" if template_and_name else f"asset {guid}"


def data_size(tag) -> int:
    """The bytes of a FileDB node's values (0: nothing stored)."""
    if isinstance(tag, filedb.SAttrib):
        return len(tag.data) if any(tag.data) else 0
    return sum(data_size(child) for child in tag.children)


def untaken(
    source_tag: filedb.STag, destination_tag: filedb.STag, handled: set[str], where: str, ledger: CLedger
) -> None:
    """The children of ``src`` that hold data and are not in ``handled`` (full paths from ``where``, which the caller's
    tree starts at): each is ``DONOR`` when ``dst`` has the node with data (its value stays the donor's) and
    ``LEFT_OUT`` when it does not, counted in bytes. A node that only some of ``handled`` lies below is walked into."""
    for child in source_tag.children:
        path = f"{where}/{child.name}" if where else child.name
        if path in handled or child.name.startswith("#") or not (size := data_size(child)):
            continue
        other = destination_tag.find(child.name)
        if isinstance(child, filedb.STag) and any(handled_path.startswith(path + "/") for handled_path in handled):
            if other is not None:
                untaken(child, other, handled, path, ledger)
            else:
                ledger.add("island data", path, LEFT_OUT, size)
            continue
        ledger.add("island data", path, DONOR if other is not None and data_size(other) else LEFT_OUT, size)


def group_leftovers(source, ledger: CLedger) -> int:
    """The objects of an island's areas in groups no port carries (``a7mread.OBJECT_GROUPS`` are carried: editor-only
    objects, say), by group; returns how many."""
    from ..formats import a7mread

    total = 0
    for _area, doc, _ids in source._areas_data():
        manager = doc.find("AreaObjectManager") or doc
        for group in manager.children:
            object_list = group.find("objects") if isinstance(group, filedb.STag) else None
            if object_list is None or group.name in a7mread.OBJECT_GROUPS:
                continue
            node_count = sum(isinstance(child, filedb.STag) for child in object_list.children)
            ledger.add("object", f"{group.name} group", LEFT_OUT, node_count)
            total += node_count
    return total


CARRIED_FOLDERS = ("meshes", "normalmaps", "tintmaps", "materialmaps")  # the ``_gamedata`` folders a port writes


def files_left(sgd, dgd, ledger: CLedger) -> None:
    """The source's ``_gamedata`` files in no carried folder and with no file of the same name in the donor's, by
    folder (a picture the donor has no place for, a folder only the source's game keeps)."""
    mine = set(dgd.names)
    for name in sgd.names:
        head = name.split("/", 1)[0]
        if head in CARRIED_FOLDERS or name in mine:
            continue
        ledger.add("island data", f"_gamedata/{head if '/' in name else name}", LEFT_OUT, 1)


def object_kept(src_names, dst_names, guid: int, same_game: bool) -> bool:
    """Whether an object whose asset no region table names may stay as it is: within a game always, between games
    only when the target has this asset number as the same asset (a GUID is one asset only within a game)."""
    if same_game:
        return True
    mine = src_names(guid)
    return mine is not None and dst_names(guid) == mine


def ground_entries(ledger: CLedger, ids, size_tiles: int, texts: tuple[str, str], final: dict[int, int]) -> None:
    """The ground materials a port swaps, by name with the tiles whose first layer each covers: ``final`` is what the
    maps use (``regiontables.with_unlisted``: an ID the source set does not list goes to the sea bed, which is
    ``CLAMPED``); several source materials on one target are ``MERGED``, one to a different one ``MAPPED``."""
    import numpy as np

    from ..islands.regiontables import set_materials

    source_materials = {material.id: material for material in set_materials(texts[0])}
    target_materials = {material.id: material for material in set_materials(texts[1])}
    first = np.asarray(ids, np.uint32) & np.uint32(31)
    per_tile = max(1, first.shape[0] // size_tiles) ** 2
    tiles = {
        int(i): int(tile_count) // per_tile
        for i, tile_count in enumerate(np.bincount(first.ravel(), minlength=32))
        if tile_count
    }
    onto: dict[int, set[int]] = {}
    for i in tiles:
        onto.setdefault(final.get(i, i), set()).add(i)

    def label(table: dict, i: int) -> str:
        material = table.get(i)
        return (
            f"{material.family} {material.file.rsplit('/', 1)[-1]}"
            if material is not None and material.file
            else f"ground {i}"
        )

    for i, tile_count in tiles.items():
        target = final.get(i, i)
        if i not in source_materials:
            ledger.add("ground", f"unlisted ground {i}", CLAMPED, tile_count, label(target_materials, target))
        elif len(onto[target]) > 1 or label(source_materials, i) != label(target_materials, target):
            ledger.add(
                "ground",
                label(source_materials, i),
                MERGED if len(onto[target]) > 1 else MAPPED,
                tile_count,
                label(target_materials, target),
            )
