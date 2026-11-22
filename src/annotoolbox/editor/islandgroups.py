"""The group each island of a game's list belongs to, so the Islands dock can show the lists in collapsible
groups: a region, a climate or a scenario set, as the game itself arranges its files.

``island_groups(game, paths, tree)`` is the entry: it answers ``{path: group}`` for the paths of the native list.
The group comes from the game's data - 117's province folder, 1800's ``RandomIsland`` assets (``IslandRegion``) and
the region in its file names, the old engine's folders under ``islands/``, 2205's session names; an island with no
known group is ``Other``. Only the display names of a game's own region keys are a table (``LABELS``).
(docs/island-editor.md "Islands and modes")
"""

from __future__ import annotations

import re
from collections import Counter

from ..infra.i18n import N_

OTHER = "Other"  # the key of the group of what fits no other
OTHER_TITLE = N_("islands.other")  # how that group is shown
ASSETS_1800 = "data/config/export/main/asset/assets.xml"
# The display name of the game's own key (a folder or a region id), where the key is not the name.
LABELS = {
    "anno117": {"roman": "Latium", "celtic": "Albion"},
    "anno1800": {"Moderate": "Old World", "Colony01": "New World", "Arctic": "Arctic", "Africa": "Enbesa"},
    "anno1404": {"north": "Occident", "south": "Orient"},
}
ORDER = {  # groups in the game's own order; the rest follow alphabetically, Other last
    "anno117": ("Latium", "Albion"),
    "anno1800": ("Old World", "New World", "Arctic", "Enbesa"),
    "anno1404": ("Occident", "Orient"),
}
_RANDOM_ISLAND = re.compile(rb"<FilePath>([^<]*\.a7m)</FilePath>\s*<IslandRegion>([^<]*)</IslandRegion>")
_PROVINCE = re.compile(r"/provinces/([^/]+)/islands/")


def humanize(key: str) -> str:
    """A folder or region key as a title: ``world_event01`` -> ``World Event 01``."""
    return re.sub(r"[_\-\s]+", " ", re.sub(r"(?<=[a-z]{2})(?=\d)", " ", key)).strip().title() or OTHER


def island_groups(game: str, names: list[str], tree=None) -> dict[str, str]:
    """``{name: group}`` for a game's island paths (``names``); ``tree``: its archives, for 1800's assets."""
    if game == "anno117":
        return {name: _province(game, name) for name in names}
    if game == "anno1800":
        return groups_1800(names, _read_assets(tree))
    if game == "anno2205":
        return {name: _session(name) for name in names}
    return {name: _folders(game, name) for name in names}


def _province(game: str, path: str) -> str:
    found = _PROVINCE.search(path)
    return LABELS[game].get(found.group(1), humanize(found.group(1))) if found else OTHER


def _folders(game: str, path: str) -> str:
    """The old engine's sets: the folders under ``islands/`` (``north``, ``campaign/chapter 01``, ``scenarios``);
    an add-on's own level folder (``addon01``) is not a set."""
    parts = path.replace("\\", "/").lower().split("/")
    if "islands" not in parts:
        return OTHER
    sets = [part for part in parts[parts.index("islands") + 1 : -1] if not re.fullmatch(r"addon\d*", part)]
    if not sets:
        return OTHER
    first = LABELS.get(game, {}).get(sets[0], humanize(sets[0]))
    return " · ".join([first, *(humanize(part) for part in sets[1:2])])


def _session(path: str) -> str:
    """2205's session maps are named ``[event_]session_<region>_<n>`` (earth, arctic, moon, tundra, orbit)."""
    found = re.search(r"session_([a-z]+)", path.rsplit("/", 1)[-1])
    return humanize(found.group(1)) if found else OTHER


def _read_assets(tree) -> bytes | None:
    try:
        return tree.read_bytes(ASSETS_1800) if tree is not None else None
    except (OSError, KeyError, ValueError):
        return None  # the paths and names still group the islands


def groups_1800(paths: list[str], assets_xml: bytes | None) -> dict[str, str]:
    """1800: the region of an island's ``RandomIsland`` asset; any other island takes the region its file name or
    folders say (``colony01_...``, ``campaign/moderate``) as those assets name it, else its ``scenarioNN``."""
    by_path: dict[str, str] = {}
    by_prefix: dict[str, Counter] = {}
    for path, region in _RANDOM_ISLAND.findall(assets_xml or b""):
        path, region = path.decode("utf-8", "replace"), region.decode("utf-8", "replace")
        by_path[path] = region
        by_prefix.setdefault(path.rsplit("/", 1)[-1].split("_")[0], Counter())[region] += 1
    prefixes = {k: counter.most_common(1)[0][0] for k, counter in by_prefix.items()}
    labels = LABELS["anno1800"]
    out = {}
    for path in paths:
        region = by_path.get(path)
        if region is None:
            parts = path.rsplit("/", 1)[-1].removesuffix(".a7m").split("_") + path.split("/")[-2::-1]
            region = next((prefixes[part] for part in parts if part in prefixes), None)
        if region is not None:
            out[path] = labels.get(region, humanize(region))
            continue
        scenario = re.search(r"scenario\d+", path)
        out[path] = humanize(scenario.group()) if scenario else OTHER
    return out
