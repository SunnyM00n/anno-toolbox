"""Every island of Anno 2070 for the editor's 2070 list, and the group each belongs to.

``island_paths`` finds the ``.isd`` files through ``overlay.SourceTree`` (the base game, its patches and the Deep
Ocean add-on, ``addondata/``); ``island_groups`` names the group of each from the folders and from the game's own
data: the scenario missions (``ScenarioMission`` assets and their ``.www`` worlds, which name the islands they place)
give a scenario island its scenario's name. ``convertible`` says which islands the island build for 117 takes by name.
(docs/island-editor.md "Islands and modes")
"""

from __future__ import annotations

import re

from ....infra.i18n import tr
from ....sources.balancingtext import tag_text
from ...islandgroups import OTHER, humanize

SEP = " · "
_ASSETS = re.compile(r"data/config/(?:game|dlc_\d+)/assets\.xml")
_MISSION = re.compile(r"<Template>(\w*Mission)</Template>")
_FILENAME = re.compile(rb"<Filename>([^<]*)</Filename>")
_LEVEL_DIRS = ("data/levels/islands/normal/", "addondata/levels/islands/normal/")


def island_paths(tree) -> list[str]:
    """Every ``.isd`` the archives hold, base game and add-on, by path."""
    return sorted(p for p in tree.iter_paths("") if p.endswith(".isd"))


def missions(tree) -> list[dict]:
    """The scenario missions of the game's assets (``name``, ``level`` - the ``.www`` world, normalised -, ``dlc``)."""
    files = [p for p in tree.iter_paths("data/config/") if _ASSETS.fullmatch(p)]
    files += [p for p in tree.iter_paths("addondata/config/") if p.endswith(".xml")]
    out = []
    for path in files:
        raw = tree.read_bytes(path)
        if b"ScenarioMission</Template>" not in raw:
            continue
        text = raw.decode("utf-8", "replace")
        for m in _MISSION.finditer(text):
            if m.group(1) != "ScenarioMission":
                continue
            body = text[m.end() : text.find("</Asset>", m.end())]
            name, level = tag_text(body, "Name").strip(), tag_text(body, "LevelFile").strip().replace("\\", "/").lower()
            if name and level.endswith(".www"):
                out.append({"name": name, "level": level})
    return out


def scenario_label(mission: dict) -> str:
    """A mission's name as the list shows it: the asset's name (its ``ulc01`` / ``dlc02`` typo prefix replaced by the
    DLC's number), and ``(multiplayer)`` for a multiplayer world."""
    name = re.sub(r"^(?:ulc|dlc)\d+\s*", "", mission["name"]).strip()
    dlc = re.search(r"/dlc_(\d+)/", mission["level"])
    label = (f"DLC {dlc.group(1)} " if dlc else "") + name
    return tr("islands.name_multiplayer", name=label) if "/multiplayer/" in mission["level"] else label


def owners(tree, paths: list[str]) -> dict[str, list[str]]:
    """``{island path: [scenario names]}``: the islands each scenario's world places (its ``IslandConfig``
    ``Filename`` values, found beside the world, in a pool folder or as a full path)."""
    known, out = set(paths), {}
    for mission in missions(tree):
        level = mission["level"]
        if not tree.exists(level):
            continue
        label = scenario_label(mission)
        for raw in _FILENAME.findall(tree.read_bytes(level)):
            name = raw.decode("latin-1").replace("\\", "/").lower()
            found = next(
                (
                    c
                    for c in (name, f"{level.rsplit('/', 1)[0]}/{name}", *(d + name for d in _LEVEL_DIRS))
                    if c in known
                ),
                None,
            )
            if found and label not in out.setdefault(found, []):
                out[found].append(label)
    return out


def island_groups(tree, paths: list[str]) -> dict[str, str]:
    """``{path: group}``: the folder's set (Normal, Underwater, Campaign · Chapter 01, World events, ...), a
    scenario island under its scenario's name, and ``Deep Ocean`` in front of what the add-on brings."""
    named = owners(tree, paths)
    beside: dict[str, list[str]] = {}  # an island no scenario names takes the scenario of its folder's neighbours
    for p, labels in named.items():
        for label in labels:
            if label not in beside.setdefault(p.rsplit("/", 1)[0], []):
                beside[p.rsplit("/", 1)[0]].append(label)
    return {p: SEP.join(_segments(p, named.get(p) or beside.get(p.rsplit("/", 1)[0], []))) for p in paths}


def group_order(groups) -> list[str]:
    """The groups in the order a person looks for them: the pool first, then levels, the add-on, the tests."""
    kinds = (
        "Normal",
        "Underwater",
        "Third Parties",
        "Campaign",
        "Scenarios",
        "World events",
        "Deep Ocean",
        "Test worlds",
    )
    return sorted(set(groups), key=lambda g: (kinds.index(g.split(SEP)[0]) if g.split(SEP)[0] in kinds else 99, g))


def _segments(path: str, scenarios: list[str]) -> list[str]:
    parts = path.split("/")  # data | addondata, levels, ...folders..., file
    root = ["Deep Ocean"] if parts[0] == "addondata" else []
    folders = parts[2:-1]
    if folders[:1] == ["islands"]:
        folders = folders[1:]
    first = folders[0] if folders else ""
    rest = [humanize(f) for f in folders[1:2]]
    if first in ("normal", "underwater", "third parties", "campaign", "benchmark"):
        return [*root, humanize(first), *rest]
    if first == "testworlds":
        return [*root, "Test worlds", *rest]
    if first == "world events":
        return [*root, "World events", *rest]
    if scenarios:
        return [*root, "Scenarios", " / ".join(scenarios)]
    return [*root, *([humanize(first), *rest] if first else [OTHER])]


def convertible(paths: list[str], dirs: tuple[str, ...]) -> dict[str, str | None]:
    """``{path: why not}`` (None: it builds): the island build for 117 takes an island by its name and looks for it in
    ``dirs`` (the build's ``ISLAND_DIRS``) in order, so a scenario, campaign, world event or test island - or one whose
    name another island of ``dirs`` already holds - has no build."""
    first: dict[str, str] = {}
    for folder in dirs:
        for p in paths:
            if p.startswith(folder):
                first.setdefault(p.rsplit("/", 1)[-1].removesuffix(".isd"), p)
    out: dict[str, str | None] = {}
    for p in paths:
        stem = p.rsplit("/", 1)[-1].removesuffix(".isd")
        if not any(p.startswith(d) for d in dirs):
            out[p] = tr("islands.levels_own_island_build_makes")
        elif first.get(stem) != p or not re.fullmatch(r"[a-z0-9_]+", stem):
            out[p] = tr("islands.name_not_one_build_look")
        else:
            out[p] = None
    return out
