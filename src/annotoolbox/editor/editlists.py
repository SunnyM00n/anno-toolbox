"""What the editor's panels list and the view hides: the island's entries by category and model (the object
tree), the library's picks by group (Object Groups), the island lists in groups (``group_rows``), and ``CHidden`` -
what is taken out of the view only, never of the edit file. The entries are ``editsession.SEntry``."""

from __future__ import annotations

from ..infra.i18n import tr
from .editsession import SEntry, group_key, is_foliage, label_of, type_of


class CHidden:
    """What the view hides (never the edit file): single entries, whole models, categories, foliage."""

    def __init__(self) -> None:
        self.m_entries: set = set()
        self.m_groups: set[str] = set()
        self.m_types: set[str] = set()
        self.m_foliage = False

    def hides(self, entry: SEntry) -> bool:
        return (
            entry in self.m_entries
            or group_key(entry.base) in self.m_groups
            or type_of(entry.base) in self.m_types
            or (self.m_foliage and is_foliage(entry.base))
        )

    def count(self) -> int:
        return len(self.m_entries) + len(self.m_groups) + len(self.m_types) + int(self.m_foliage)

    def clear(self) -> None:
        self.m_entries.clear()
        self.m_groups.clear()
        self.m_types.clear()
        self.m_foliage = False

    def apply(self, entries) -> set:
        """Set each entry's ``hidden``; returns those that changed."""
        changed = set()
        for entry in entries:
            hidden = self.hides(entry)
            if hidden != entry.hidden:
                entry.hidden = hidden
                changed.add(entry)
        return changed


def object_groups(entries) -> dict[str, dict[str, list[SEntry]]]:
    """The island's entries by category, then by model, as the object list shows them."""
    out: dict[str, dict[str, list[SEntry]]] = {}
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
    names = sorted(groups, key=lambda group: (group == other, rank.get(group, len(rank)), group.lower()))
    return [(group, groups[group]) for group in names]


def library_items(library: dict, source: str, query: str = "", forest_models=()) -> dict[str, list[dict]]:
    """The library's picks of one source (``2070``, ``117``, ``editor``) by group, the island's own trees
    first; ``query`` searches names and paths."""
    needle = query.strip().lower()
    items = [
        {
            "name": label_of({"cfg": cfg}),
            "cfg": cfg,
            "tree": tree_index,
            "group": tr("islands.forest_island"),
            "title": f"forest:{cfg}",
        }
        for tree_index, cfg in enumerate(forest_models)
    ]
    for entry in library.get(source, []):
        if source == "editor":
            title = f"{entry['template']} {entry['guid']}: {entry['name']}"
        else:
            title = entry.get("source") or entry.get("cfg") or entry["name"]
        items.append({**entry, "title": title})
    out: dict[str, list[dict]] = {}
    for entry in items:
        if not needle or needle in entry["title"].lower() or needle in entry["name"].lower():
            out.setdefault(entry["group"], []).append(entry)
    return dict(sorted(out.items()))
