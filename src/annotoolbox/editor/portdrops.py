"""What an island port does not carry as it was, said by name and count: objects and props whose asset the target
game lacks, ground materials merged or replaced, island data the target keeps from its donor or does not have.

A port collects every such case in a ``Ledger``; ``report["dropped"]`` holds its entries (``Ledger.entries``: dicts of
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
class Ledger:
    """The cases of one port, one entry per (category, what, fate, to) with the count added up."""

    found: Counter = field(default_factory=Counter)

    def add(self, category: str, what: str, fate: str = LEFT_OUT, count: int = 1, to: str = "") -> None:
        if count > 0:
            self.found[category, what, fate, to] += count

    @property
    def entries(self) -> list[dict]:
        order = {c: i for i, c in enumerate(CATEGORIES)}
        keys = sorted(self.found, key=lambda k: (order.get(k[0], 99), k[2] != LEFT_OUT, -self.found[k], k[1]))
        return [{"category": c, "what": w, "fate": f, "count": self.found[c, w, f, t], "to": t} for c, w, f, t in keys]

    def count(self, fate: str = LEFT_OUT) -> int:
        """How many things have this fate."""
        return sum(n for (_c, _w, f, _t), n in self.found.items() if f == fate)


def line(entry: dict) -> str:
    """One entry as a sentence ("object Slot Roman River: left out, 3")."""
    to = f" -> {entry['to']}" if entry.get("to") else ""
    return f"{entry['category']} {entry['what']}: {entry['fate']}{to}, {entry['count']}"


def lines(entries: list[dict], limit: int | None = SHOWN) -> list[str]:
    """The entries as log lines, at most ``limit`` per category (the rest as one line that says how many)."""
    out, per = [], Counter()
    hidden: Counter = Counter()
    for e in entries:
        per[e["category"]] += 1
        if limit is not None and per[e["category"]] > limit:
            hidden[e["category"]] += 1
        else:
            out.append(line(e))
    out += [tr("islands.drops_more", n=n, category=c) for c, n in hidden.items()]
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
    info = names(guid) if names is not None else None
    return f"{info[0]}/{info[1]}" if info else f"asset {guid}"


def data_size(tag) -> int:
    """The bytes of a FileDB node's values (0: nothing stored)."""
    if isinstance(tag, filedb.Attrib):
        return len(tag.data) if any(tag.data) else 0
    return sum(data_size(c) for c in tag.children)


def untaken(src: filedb.Tag, dst: filedb.Tag, handled: set[str], where: str, ledger: Ledger) -> None:
    """The children of ``src`` that hold data and are not in ``handled`` (full paths from ``where``, which the caller's
    tree starts at): each is ``DONOR`` when ``dst`` has the node with data (its value stays the donor's) and
    ``LEFT_OUT`` when it does not, counted in bytes. A node that only some of ``handled`` lies below is walked into."""
    for c in src.children:
        path = f"{where}/{c.name}" if where else c.name
        if path in handled or c.name.startswith("#") or not (size := data_size(c)):
            continue
        other = dst.find(c.name)
        if isinstance(c, filedb.Tag) and any(h.startswith(path + "/") for h in handled):
            if other is not None:
                untaken(c, other, handled, path, ledger)
            else:
                ledger.add("island data", path, LEFT_OUT, size)
            continue
        ledger.add("island data", path, DONOR if other is not None and data_size(other) else LEFT_OUT, size)


def group_leftovers(source, ledger: Ledger) -> int:
    """The objects of an island's areas in groups no port carries (``a7mread.OBJECT_GROUPS`` are carried: editor-only
    objects, say), by group; returns how many."""
    from ..formats import a7mread

    total = 0
    for _area, doc, _ids in source._areas_data():
        manager = doc.find("AreaObjectManager") or doc
        for group in manager.children:
            objs = group.find("objects") if isinstance(group, filedb.Tag) else None
            if objs is None or group.name in a7mread.OBJECT_GROUPS:
                continue
            n = sum(isinstance(c, filedb.Tag) for c in objs.children)
            ledger.add("object", f"{group.name} group", LEFT_OUT, n)
            total += n
    return total


CARRIED_FOLDERS = ("meshes", "normalmaps", "tintmaps", "materialmaps")  # the ``_gamedata`` folders a port writes


def files_left(sgd, dgd, ledger: Ledger) -> None:
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


def ground_entries(ledger: Ledger, ids, size: int, texts: tuple[str, str], final: dict[int, int]) -> None:
    """The ground materials a port swaps, by name with the tiles whose first layer each covers: ``final`` is what the
    maps use (``regiontables.with_unlisted``: an ID the source set does not list goes to the sea bed, which is
    ``CLAMPED``); several source materials on one target are ``MERGED``, one to a different one ``MAPPED``."""
    import numpy as np

    from ..islands.regiontables import set_materials

    src = {m.id: m for m in set_materials(texts[0])}
    dst = {m.id: m for m in set_materials(texts[1])}
    first = np.asarray(ids, np.uint32) & np.uint32(31)
    per_tile = max(1, first.shape[0] // size) ** 2
    tiles = {int(i): int(n) // per_tile for i, n in enumerate(np.bincount(first.ravel(), minlength=32)) if n}
    onto: dict[int, set[int]] = {}
    for i in tiles:
        onto.setdefault(final.get(i, i), set()).add(i)

    def label(table: dict, i: int) -> str:
        m = table.get(i)
        return f"{m.family} {m.file.rsplit('/', 1)[-1]}" if m is not None and m.file else f"ground {i}"

    for i, n in tiles.items():
        target = final.get(i, i)
        if i not in src:
            ledger.add("ground", f"unlisted ground {i}", CLAMPED, n, label(dst, target))
        elif len(onto[target]) > 1 or label(src, i) != label(dst, target):
            ledger.add("ground", label(src, i), MERGED if len(onto[target]) > 1 else MAPPED, n, label(dst, target))
