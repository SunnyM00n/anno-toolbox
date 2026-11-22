"""The draw order of ground materials from different games in one material set.

117 lays an island's materials over one another in its material set's list order (rendering/terrain-
materials 1.4); 2070 lays a chunk's layers in ascending ``SplatID`` (rendering-2070 2.1). Each game keeps
its own order; a material brought in from another game goes where its family stands in the island's
set: before the first of the island's own materials of a higher family, the families ranked as 2070's
SplatIDs rank them (water, sand, soil and grass, rock). Several brought-in materials of a family
keep their own game's order. Plan: ``docs/island-editor.md``, "Ground materials of other games".
"""

from __future__ import annotations

import re

# the families in the order 2070's SplatIDs lay them (water 1, beach 770-780, grass and earth 800-825,
# mountain 840-870): a later family is drawn over an earlier one
FAMILY_RANK = {"water": 0, "sand": 1, "soil": 2, "grass": 2, "rock": 3}
FAMILY_WORDS = (  # the first word found in a material's name decides (2070's and 117's names)
    ("water", "water"),
    ("beach", "sand"),
    ("coast", "sand"),
    ("sand", "sand"),
    ("swamp_grass", "grass"),
    ("gras", "grass"),
    ("earth", "soil"),
    ("dirt", "soil"),
    ("mud", "soil"),
    ("flow", "soil"),
    ("deposit", "soil"),
    ("gravel", "soil"),
    ("pavement", "soil"),
    ("swamp", "soil"),
    ("mountain", "rock"),
    ("stone", "rock"),
    ("rock", "rock"),
    ("cliff", "rock"),
    ("boulder", "rock"),
    ("lava", "rock"),
)


def family(name: str) -> str:
    """A ground material's family from its name (an underwater ground by the rest of it); ``soil`` when no
    word says."""
    low = name.lower().replace("underwater", "")
    return next((kind for word, kind in FAMILY_WORDS if word in low), "soil")


def merged_order(own: list[tuple[int, str]], foreign: list[tuple[int, str, float]]) -> list[int]:
    """The material IDs in draw order (first drawn first): ``own`` - the island's set as (ID, name) in its
    list order - unchanged, each ``foreign`` (ID, name, its rank in its own game) before the first material
    whose family ranks higher: a brought-in grass never covers the island's rock."""
    order = [material_id for material_id, _ in own]
    family_ranks = {material_id: FAMILY_RANK[family(name)] for material_id, name in own}
    for material_id, name, _ in sorted(foreign, key=lambda entry: (FAMILY_RANK[family(entry[1])], entry[2])):
        rank = FAMILY_RANK[family(name)]
        position = next((k for k, material in enumerate(order) if family_ranks[material] > rank), len(order))
        order.insert(position, material_id)
        family_ranks[material_id] = rank
    return order


def ranks(order: list[int]) -> dict[int, int]:
    """Material ID -> its place in the draw order."""
    return {mid: k for k, mid in enumerate(order)}


def set_ranks(set_text: str) -> dict[int, int]:
    """Material ID -> its place in a material set's list, the order 117 and 1800 draw the ground in."""
    return ranks([int(material_id) for material_id in re.findall(r"<Material>\s*<ID>(\d+)</", set_text)])


def set_files(set_text: str) -> dict[int, str]:
    """Material ID -> the material file its entry in a material set names (game path, forward slashes)."""
    out: dict[int, str] = {}
    for entry in re.findall(r"<Material>.*?\r?\n</>", set_text, re.S):
        ident, name = re.search(r"<ID>(\d+)</>", entry), re.search(r"<FileName>([^<]*)</>", entry)
        if ident and name:
            out[int(ident.group(1))] = name.group(1).replace("\\", "/")
    return out
