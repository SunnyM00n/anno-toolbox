"""An island's session settings: ``GameSessionManager/SessionSettings`` names the ambient light the game draws the island
with (``GlobalAmbientName``, UTF-16: ``amb_roman_01``), the set the vegetation props are drawn from
(``VegetationPropSetName``, plain text) and the playable area (``PlayableArea``, four int32 tiles x0, z0, x1, z1). The
edit file's ``session`` holds the changes by short names (``FIELDS``; island-editor "Session settings").
"""

from __future__ import annotations

import re
import struct

from ..formats import filedb
from ..infra.i18n import tr

AMBIENT_FOLDER = "data/base/config/engine/ambientsettings/"  # one file per ambient light preset, its stem the name
PROP_SETS = "data/base/config/engine/prop_sets.xml"  # the vegetation sets (island-build "Vegetation: prop sets")

FIELDS = {"ambient": "GlobalAmbientName", "vegetation": "VegetationPropSetName", "playable_area": "PlayableArea"}


def read(gm: filedb.Tag) -> dict:
    """The island's settings as plain values (a key is left out when the island has none of it); ``gm`` is the
    ``GameSessionManager``."""
    node = gm.find("SessionSettings")
    out: dict = {}
    for key, name in FIELDS.items():
        attr = node.find(name) if node is not None else None
        if attr is None:
            continue
        if key == "ambient":
            out[key] = attr.data.decode("utf-16-le")
        elif key == "vegetation":
            out[key] = attr.data.decode("utf-8")
        else:
            out[key] = list(struct.unpack("<4i", attr.data))
    return out


def problem(key: str, value, grid: int) -> str | None:
    """Why the game would not take ``value`` for the setting ``key`` on an island of ``grid`` tiles, None when it would."""
    if key in ("ambient", "vegetation"):
        return None if str(value).strip() else tr("the session's {key} is empty", key=key)
    try:
        x0, z0, x1, z1 = (int(v) for v in value)
    except (TypeError, ValueError):
        return tr("the playable area {area} is not four numbers", area=value)
    if not (0 <= x0 < x1 <= grid and 0 <= z0 < z1 <= grid):
        return tr(
            "the playable area {area} must lie inside the {grid}-tile grid, corners in order",
            area=(x0, z0, x1, z1),
            grid=grid,
        )
    return None


def apply(gm: filedb.Tag, edit: dict, grid: int) -> dict:
    """Write the edit file's ``session`` into the island (``gm``: its ``GameSessionManager``, ``grid`` its tiles);
    returns the keys ``set`` and ``unmatched``: what the island has no attribute for, and what the game would not take."""
    node = gm.find("SessionSettings")
    report: dict = {"set": [], "unmatched": []}
    for key, value in edit.items():
        attr = node.find(FIELDS[key]) if node is not None and key in FIELDS else None
        why = problem(key, value, grid) if attr is not None else tr("session {key}", key=key)
        if why:
            report["unmatched"].append(why)
            continue
        if key == "ambient":
            attr.data = str(value).encode("utf-16-le")
        elif key == "vegetation":
            attr.data = str(value).encode("utf-8")
        else:
            attr.data = struct.pack("<4i", *(int(v) for v in value))
        report["set"].append(key)
    return report


def ambient_names(files) -> list[str]:
    """The ambient settings the game ships (the stems of its ``ambientsettings`` files, plus the mod's own): the ``amb_``
    ones, which the islands use, first, then the portrait and interface lights; ``files`` has ``list(prefix)``. None of
    them is checked to load on an island."""
    names = {path.rsplit("/", 1)[-1][: -len(".xml")] for path in files.list(AMBIENT_FOLDER) if path.endswith(".xml")}
    return sorted(names, key=lambda name: (not name.startswith("amb_"), name))


def vegetation_names(files) -> list[str]:
    """The names of the vegetation prop sets of the game's ``prop_sets.xml``, in file order; none when it cannot be read."""
    try:
        text = files.read(PROP_SETS).decode("utf-8", "ignore")
    except (KeyError, OSError):
        return []
    return re.findall(r"<PropSet>\s*<Name>([^<]+)</", text)
