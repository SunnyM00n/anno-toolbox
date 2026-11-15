"""What the editor's panels list and the view hides: the island's entries by category and model (the object
tree), the library's picks by group (Object Groups), the island lists in groups (``group_rows``), and ``Hidden`` -
what is taken out of the view only, never of the edit file. The entries are ``editsession.Entry``."""

from __future__ import annotations

from ..infra.i18n import tr
from .editsession import Entry, group_key, is_foliage, label_of, type_of


class Hidden:
    """What the view hides (never the edit file): single entries, whole models, categories, foliage."""

    def __init__(self) -> None:
        self.entries: set = set()
        self.groups: set[str] = set()
        self.types: set[str] = set()
        self.foliage = False

    def hides(self, e: Entry) -> bool:
        return (
            e in self.entries
            or group_key(e.base) in self.groups
            or type_of(e.base) in self.types
            or (self.foliage and is_foliage(e.base))
        )

    def count(self) -> int:
        return len(self.entries) + len(self.groups) + len(self.types) + int(self.foliage)

    def clear(self) -> None:
        self.entries.clear()
        self.groups.clear()
        self.types.clear()
        self.foliage = False

    def apply(self, entries) -> set:
        """Set each entry's ``hidden``; returns those that changed."""
        changed = set()
        for e in entries:
            h = self.hides(e)
            if h != e.hidden:
                e.hidden = h
                changed.add(e)
        return changed


def object_groups(entries) -> dict[str, dict[str, list[Entry]]]:
    """The island's entries by category, then by model, as the object list shows them."""
    out: dict[str, dict[str, list[Entry]]] = {}
    for e in entries:
        if e.alive:
            out.setdefault(type_of(e.base), {}).setdefault(group_key(e.base), []).append(e)
    return out


def group_rows(rows: list, group_of, order=(), other: str = "Other") -> list[tuple[str, list]]:
    """Rows (any shape; ``group_of(row)`` names the group) in groups, the Islands dock's collapsible lists: the
    groups of ``order`` first, then the rest alphabetically, ``other`` last; a row keeps its place within its group."""
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(group_of(row) or other, []).append(row)
    rank = {name: i for i, name in enumerate(order)}
    names = sorted(groups, key=lambda g: (g == other, rank.get(g, len(rank)), g.lower()))
    return [(g, groups[g]) for g in names]


def library_items(library: dict, source: str, query: str = "", forest_models=()) -> dict[str, list[dict]]:
    """The library's picks of one source (``2070``, ``117``, ``editor``) by group, the island's own trees
    first; ``query`` searches names and paths."""
    q = query.strip().lower()
    items = [
        {
            "name": label_of({"cfg": cfg}),
            "cfg": cfg,
            "tree": v,
            "group": tr("forest of this island"),
            "title": f"forest:{cfg}",
        }
        for v, cfg in enumerate(forest_models)
    ]
    for e in library.get(source, []):
        if source == "editor":
            title = f"{e['template']} {e['guid']}: {e['name']}"
        else:
            title = e.get("source") or e.get("cfg") or e["name"]
        items.append({**e, "title": title})
    out: dict[str, list[dict]] = {}
    for e in items:
        if not q or q in e["title"].lower() or q in e["name"].lower():
            out.setdefault(e["group"], []).append(e)
    return dict(sorted(out.items()))
