"""The properties of a 2070-format island (2070, 1404) that sit at the top of its ``.isd``: ``Clime`` (0 above water,
1 underwater), ``Difficulty`` (0 on 325 of the 326 islands of the archives, 1 on one) and ``SeaLevel`` (0 on the
normal islands, -1 to -33 on pirate towers, third parties and underwater islands; absent where 0). The edit file keeps
``properties`` (``clime``, ``difficulty``, ``sea_level``), only what differs from the island. ``read`` gives the
island's, ``check`` names what the game has no island of, ``apply`` writes them into the parsed ``.isd``.
"""

from __future__ import annotations

from ....formats.isd2070 import SNode
from ....infra.i18n import tr

NODES = {"clime": "Clime", "difficulty": "Difficulty", "sea_level": "SeaLevel"}
RANGES = {"clime": (0, 1), "difficulty": (0, 1), "sea_level": (-40, 0)}  # the values the archives' islands use, widened
HEADER_1701 = ("clime", "difficulty")  # 1701 keeps these two in the island's ``.isc`` header, difficulty 0 to 2
RANGES_1701 = {**RANGES, "difficulty": (0, 2)}
AFTER = ("RiverGrid", "Rivers", "Lakes", "CoastBuildingLines")  # a missing SeaLevel goes after the first of these


def editable(root: SNode, only: tuple[str, ...] | None = None) -> list[str]:
    """The properties this island can set: those it has (limited to ``only``), and a ``SeaLevel`` when it is in 2070's
    layout (it has a ``Clime``; 1404's files have none of the three, so nothing is invented for them)."""
    has_clime = root.find("Clime") is not None
    return [
        k
        for k, name in NODES.items()
        if (only is None or k in only) and (root.find(name) is not None or (k == "sea_level" and has_clime))
    ]


def read(root: SNode) -> dict:
    """The island's own values: its size (tiles, not editable) and the three properties."""
    return {
        "width": root.int("Width"),
        "height": root.int("Height"),
        **{key: root.int(name) for key, name in NODES.items()},
    }


def check(props: dict, ranges: dict | None = None) -> list[str]:
    ranges = ranges or RANGES
    out = []
    for key, value in props.items():
        if key not in NODES:
            out.append(tr("islands.not_property_island_one", key=key, names=", ".join(NODES)))
        elif int(value) != value or not ranges[key][0] <= value <= ranges[key][1]:
            low, high = ranges[key]
            out.append(
                tr(
                    "islands.games_islands_use_whole_numbers",
                    name=NODES[key],
                    value=value,
                    low=low,
                    high=high,
                )
            )
    return out


def _like(name: str, text: str, ref: SNode) -> SNode:
    return SNode(name, text, short=ref.short, tail=ref.tail, head=ref.head)


def apply(root: SNode, props: dict, only: tuple[str, ...] | None = None, ranges: dict | None = None) -> int:
    """Set the properties of the parsed island ``root`` (1701: its header, ``only`` ``HEADER_1701``); returns how many
    were set. A missing ``SeaLevel`` is added where the islands that have one keep it; a value not in ``check``'s
    range is left out."""
    done = 0
    for key, value in props.items():
        if key not in NODES or check({key: value}, ranges) or key not in editable(root, only):
            continue
        node = root.find(NODES[key])
        if node is None:
            after = next((root.find(anchor_name) for anchor_name in AFTER if root.find(anchor_name) is not None), None)
            ref = root.find("Clime")
            if after is None:
                continue
            node = _like(NODES[key], "0", ref)
            root.children.insert(root.children.index(after) + 1, node)
        node.text = str(int(value))
        done += 1
    return done
