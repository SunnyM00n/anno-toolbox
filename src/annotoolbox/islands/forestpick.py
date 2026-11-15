"""The forest brush's picks: which trees a planted tile gets and which of them.

Shared by the island editor's drawing, its edits and the island build, so a tile shows the tree it later becomes.
``forest_kinds`` is the entry; ``tile_pick`` is the per-tile choice.
"""

from __future__ import annotations

from .objects2070 import TREE_TEMPLATE

FOREST_KINDS = 6  # the island's commonest trees a planted tile picks from


def forest_kinds(island, types: dict) -> list[tuple[int, int]]:
    """The trees the forest brush plants, (GUID, variation), commonest first: the island's own, or
    2070's first tree assets on an island without trees. The page draws the same list."""
    counts: dict[tuple[int, int], int] = {}
    for o in island.objects():
        t = types.get(o.guid)
        var = o.variation or 0
        if t is not None and t.template == TREE_TEMPLATE and var < len(t.variations):
            counts[(o.guid, var)] = counts.get((o.guid, var), 0) + 1
    if not counts:
        return [(t.guid, 0) for t in tree_types(types)][:FOREST_KINDS]
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:FOREST_KINDS]]


def tile_pick(x: int, z: int) -> int:
    """A tile's fixed pick of tree and turn, the page's (forest drawing) to the bit."""
    return ((x * 73856093) ^ (z * 19349663)) & 0xFFFFFFFF


def tree_types(types: dict) -> list:
    """The 2070 tree types (``objects2070.ObjectType``) in GUID order, which fixes their 117 GUIDs."""
    return sorted((t for t in types.values() if t.template == TREE_TEMPLATE and t.variations), key=lambda t: t.guid)
