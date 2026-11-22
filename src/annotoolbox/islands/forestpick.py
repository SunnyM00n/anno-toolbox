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
    for placed in island.objects():
        tree_type = types.get(placed.guid)
        variation = placed.variation or 0
        if tree_type is not None and tree_type.template == TREE_TEMPLATE and variation < len(tree_type.variations):
            counts[(placed.guid, variation)] = counts.get((placed.guid, variation), 0) + 1
    if not counts:
        return [(tree_type.guid, 0) for tree_type in tree_types(types)][:FOREST_KINDS]
    return [key for key, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:FOREST_KINDS]]


def tile_pick(x: int, z: int) -> int:
    """A tile's fixed pick of tree and turn, the page's (forest drawing) to the bit."""
    return ((x * 73856093) ^ (z * 19349663)) & 0xFFFFFFFF


def tree_types(types: dict) -> list:
    """The 2070 tree types (``objects2070.SObjectType``) in GUID order, which fixes their 117 GUIDs."""
    return sorted(
        (tree_type for tree_type in types.values() if tree_type.template == TREE_TEMPLATE and tree_type.variations),
        key=lambda tree_type: tree_type.guid,
    )
