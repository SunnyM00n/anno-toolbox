"""Props in an island's render data: appending to the ``PropGrid`` (model files and one instance per prop), and the
layer the game files a prop under.

``append_props`` is the entry; the island editor adds props with it, and so does the island build. ``prop_layer``
says which of the game's prop layers a model file lands in.
"""

from __future__ import annotations

import math
import struct

from ..formats import filedb

# The game names a prop's category by the first of these words its model path holds (lower case, "/"); the empty
# slot is never matched. Clearing under roads and buildings works by layer (island-build "Prop layers").
PATH_CATEGORIES = (
    "default",
    "marshveg",
    "grass",
    "bushes",
    "trees",
    "",
    "rocks",
    "quay",
    "water",
    "detail",
    "units",
    "portrait",
    "world_map",
    "mud_decal",
)
NO_LAYER_FLAG = 1 << 28  # RenderPropertyFlags bit: no clearing ever reaches the prop (rocks and cliffs carry it)


def path_category(path: str) -> str:
    """The category word of a model path (``"grass"``, ``"trees"``, ...), ``""`` when it holds none of them."""
    key = path.lower().replace("\\", "/")
    return next((word for word in PATH_CATEGORIES if word and word in key), "")


def prop_layer(path: str, render_flags: int = 0) -> str:
    """The layer a prop of this model is cleared in: ``"grass"``, ``"trees"``, ``"other"`` (every other category,
    bushes and rocks included), or ``"none"`` when its flags keep every clearing away."""
    if render_flags & NO_LAYER_FLAG:
        return "none"
    word = path_category(path)
    return word if word in ("grass", "trees") else "other"


def _quaternion(turn) -> tuple[float, float, float, float]:
    if isinstance(turn, tuple):
        return turn
    return (0.0, math.sin(turn / 2), 0.0, math.cos(turn / 2))


def append_props(builder, items) -> int:
    """Add props to the island's ``PropGrid``, keeping what is already in it.

    ``items``: the prop's cfg (a game path), its position in world units, its turn in radians and
    its scale - one number, or (x, y, z) where a prop is stretched along one axis only, as a
    waterfall is to reach from its lip to the pool - and, optionally, its tint (r, g, b, a; white by default).
    A waterfall, a rainbow, a fire or a light is a prop like any other."""
    pg = builder.rd.path("PropGrid")
    names = pg.find("FileNames") if pg is not None else None
    inst = pg.find("Instances") if pg is not None else None
    if names is None or inst is None or not items:
        return 0
    known = [c.data.decode("utf-16-le") for c in names.children]
    for cfg, (x, y, z), yaw, scale, *tint in items:
        sx, sy, sz = scale if isinstance(scale, tuple) else (scale, scale, scale)
        path = cfg.replace("/", "\\")
        if path not in known:
            known.append(path)
            names.children.append(filedb.Attrib("#32768", path.encode("utf-16-le")))
        inst.children.append(
            filedb.Tag(
                "#1",
                [
                    filedb.Attrib("Index", struct.pack("<I", known.index(path))),
                    filedb.Attrib("Position", struct.pack("<3f", float(x), float(y), float(z))),
                    # ``yaw`` is the quaternion's own angle (objects2070.prop_turn for a 2070 object), or
                    # a whole quaternion (x, y, z, w) where a prop also leans (a waterfall)
                    filedb.Attrib("Rotation", struct.pack("<4f", *_quaternion(yaw))),
                    filedb.Attrib("Scale", struct.pack("<3f", float(sx), float(sy), float(sz))),
                    filedb.Attrib(
                        "Color", struct.pack("<4f", *(tint[0] if tint and tint[0] else (1.0, 1.0, 1.0, 1.0)))
                    ),
                ],
            )
        )
    return len(items)
