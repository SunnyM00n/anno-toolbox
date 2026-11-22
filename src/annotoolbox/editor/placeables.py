"""What can be placed on an island of a game: its buildings and other objects with a model, read from the game's asset
files by the asset index's scan, grouped like the game's build menu (or the asset files' groups, else the template).

``scan(files, templates)`` is the entry: one ``SPlaceable`` per asset with a model (its own variations, its base
asset's or its template's), with its build-menu group, whether it is a building, the ground it may stand on and its
icon. ``search`` and ``grouped`` are the panel's filters; ``pick`` turns a row into what the Place tool takes. The
1701 objects (``.ocf`` files) are ``from_object_configs``. Rules and sources: island-editor "Placing from the list".
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from ..assetedit.assetindex import scan_file
from ..sources.overlay import normalize
from .assetmodels import PLACEHOLDERS, template_models, variations_of

# fmt: off
# the ground a building may stand on, by the words of each game's asset data (Building/TerrainType2 in 117,
# Building/TerrainType in 1800, Blocking/BlockingRules in 2070 and 1404)
TERRAIN_WORDS = {
    "Water": {"water"}, "Coast": {"coast"}, "NormalLand": {"land"}, "Marsh": {"land"}, "MarshStatic": {"land"},
    "Embankment": {"land", "coast"},
    "Water_Including_Coast": {"water", "coast"}, "Water_Excluding_Coast": {"water"}, "Beach": {"coast"},
    "Terrain_Including_Beach": {"land", "coast"}, "Terrain": {"land"},
    "CoastBuilding": {"coast"}, "HabourBuilding": {"coast"},
}
# fmt: on
TERRAIN = re.compile(rb"<(TerrainType2|TerrainType|BlockingRules)>([^<]*)</\1>")
ICON = re.compile(rb"<IconFilename>([^<]+)</IconFilename>")
MEMBER = re.compile(rb"<Building>(\d+)</Building>")
BUILDING = re.compile(rb"<Building>\s*<|<Building\s*/>")  # a Building block, not a list item naming one
HARBOR = re.compile(rb"<HarborType>[1-9]\d*</HarborType>")  # 2070 and 1404: a building of the harbor
DEV_FOLDERS = ("data/dev/",)  # the developers' stand-ins (an empty model): nothing to draw or place
MENU_TEMPLATE = "ConstructionCategory"  # 117's and 1800's build menu: a category lists buildings and categories
MENU = "Build menu"
FORMAT = "placeables-1"  # of the cached list: raise it when ``SPlaceable`` or the scan changes
GROUP_DEPTH = 3  # group names kept from the top
# the turn steps a game builds in (117 turns a building in eighths, the others in quarters)
TURNS = {"anno117": "eighth"}
DEFAULT_TURN = "quarter"


@dataclass(frozen=True)
class SPlaceable:
    guid: int
    name: str
    template: str
    group: tuple[str, ...]
    cfg: str  # the first model, as the game names it
    variations: int
    building: bool
    terrain: tuple[str, ...] = ()  # "land", "coast", "water": what it may stand on (empty: anything)
    icon: str = ""


def turn_step(game: str) -> str:
    """The ``snapping.TURNS`` step a game turns its buildings by."""
    return TURNS.get(game, DEFAULT_TURN)


def _terrain(body: bytes) -> set[str] | None:
    """The ground the asset's own values name, None when they name none."""
    found = TERRAIN.search(body)
    if HARBOR.search(body):
        return {"coast"}
    if found is None:
        return None
    words = found.group(2).decode("utf-8", "replace").split(";")
    kinds = set().union(*(TERRAIN_WORDS.get(word.strip(), set()) for word in words))
    return kinds or ({"land"} if found.group(1) != b"BlockingRules" else None)


def _template_facts(templates: str) -> tuple[set[str], dict[str, set[str]]]:
    """The templates whose properties hold a ``Building`` block, and the ground each one's defaults name."""
    buildings, terrain = set(), {}
    for name, body in re.findall(r"<Template>\s*<Name>([^<]+)</Name>(.*?)</Template>", templates, re.S):
        raw = body.encode("utf-8")
        if BUILDING.search(raw):
            buildings.add(name)
        found = _terrain(raw)
        if found:
            terrain[name] = found
    return buildings, terrain


def _menu_paths(rows: dict[int, dict]) -> dict[int, tuple[str, ...]]:
    """Per asset the build-menu path that lists it: the names of the categories from the top one down."""
    parent: dict[int, int] = {}
    for guid, row in rows.items():
        if row["template"] == MENU_TEMPLATE:
            for member in row["members"]:
                parent.setdefault(member, guid)
    out = {}
    for guid in parent:
        path, node, seen = [], parent[guid], set()
        while node is not None and node not in seen:
            seen.add(node)
            path.append(rows[node]["name"] if node in rows else str(node))
            node = parent.get(node)
        out[guid] = (MENU, *reversed(path))
    return out


def scan(files: list[bytes], templates: str = "") -> list[SPlaceable]:
    """Every asset of ``files`` (in the game's order, a later definition winning) that draws a model."""
    groups: list[tuple[str, ...]] = []
    group_ids: dict = {}
    rows: dict[int, dict] = {}
    for k, data in enumerate(files):
        entries, _refs = scan_file(data, k, groups, group_ids)
        for entry in entries:
            body = data[entry.start : entry.end]
            icon = ICON.search(body)
            rows[entry.guid] = {
                "name": entry.name, "template": entry.template, "base": entry.base, "group": groups[entry.group],
                "variations": variations_of(body), "terrain": _terrain(body), "building": bool(BUILDING.search(body)),
                "members": [int(member) for member in MEMBER.findall(body)] if entry.template == MENU_TEMPLATE else [],
                "icon": normalize(icon.group(1).decode("utf-8", "replace"), lower=False) if icon else "",
            }  # fmt: skip
    buildings, template_terrain = _template_facts(templates)
    defaults = template_models(templates)
    menu = _menu_paths(rows)
    out = []
    for guid, row in rows.items():
        found = _inherited(rows, guid)
        variations = found["variations"] or defaults.get(found["template"], [])
        files_named = [variation for variation in variations if variation]
        if not files_named or any(placeholder in files_named[0].lower() for placeholder in PLACEHOLDERS + DEV_FOLDERS):
            continue
        building = found["building"] or found["template"] in buildings or "Building" in found["template"]
        terrain = found["terrain"] or template_terrain.get(found["template"]) or ({"land"} if building else set())
        group = menu.get(guid) or (found["group"][:GROUP_DEPTH] if found["group"] else ("Templates", found["template"]))
        out.append(SPlaceable(guid, row["name"] or f"guid {guid}", found["template"], tuple(group), files_named[0],
                             len(variations), building, tuple(sorted(terrain)), found["icon"]))  # fmt: skip
    return out


def _inherited(rows: dict[int, dict], guid: int) -> dict:
    """An asset's facts with what it leaves out taken from its base assets (template, models, ground, group)."""
    row = dict(rows[guid])
    node, depth = row.get("base"), 0
    while node in rows and depth < 8:
        base = rows[node]
        for key in ("template", "variations", "terrain", "icon"):
            row[key] = row[key] or base[key]
        row["building"] = row["building"] or base["building"]
        row["group"] = row["group"] or base["group"]
        node, depth = base.get("base"), depth + 1
    return row


def from_object_configs(texts: dict[str, str]) -> list[SPlaceable]:
    """1701's objects: one per ``.ocf`` file (its ``GUID``, ``Name`` and ``MeshConfig`` variations), grouped by its
    folders. 1701's files say nothing of a building's ground: every one may stand anywhere."""
    out = []
    for path, text in sorted(texts.items()):
        guid = re.search(r"<GUID>(\d+)</GUID>", text)
        meshes = [normalize(mesh) for mesh in re.findall(r"<MeshConfig>([^<]+)</MeshConfig>", text)]
        if not guid or not meshes:
            continue
        name = (re.search(r"<Name>([^<]*)</Name>", text) or [None, ""])[1] or path.rsplit("/", 1)[-1]
        folders = path.split("/")[3:-2][:GROUP_DEPTH] or ["objects"]
        building = "buildings" in path.split("/")
        out.append(SPlaceable(int(guid.group(1)), name, folders[0], tuple(folders), meshes[0], len(meshes), building))
    return out


def search(rows: list[SPlaceable], query: str = "", buildings_only: bool = False) -> list[SPlaceable]:
    """The rows whose name, GUID or template holds ``query`` (case-insensitive), a GUID match first."""
    needle = query.strip().lower()
    found = [placeable for placeable in rows if not buildings_only or placeable.building]
    if not needle:
        return found
    exact = [placeable for placeable in found if needle.isdigit() and placeable.guid == int(needle)]
    return exact + [placeable for placeable in found if placeable not in exact and (needle in placeable.name.lower() or needle in placeable.template.lower()
                                                             or needle in str(placeable.guid))]  # fmt: skip


def grouped(rows: list[SPlaceable]) -> dict[str, list[SPlaceable]]:
    """The rows by their group (``a / b / c``), the groups and each group's rows sorted by name."""
    out: dict[str, list[SPlaceable]] = {}
    for placeable in rows:
        out.setdefault(" / ".join(placeable.group), []).append(placeable)
    return {
        k: sorted(group, key=lambda placeable: placeable.name.lower())
        for k, group in sorted(out.items(), key=lambda kv: kv[0].lower())
    }


def pick(row: SPlaceable, cfg: str) -> dict:
    """What the Place tool places for ``row``: its asset, drawn with ``cfg`` (the model the view can draw), written
    as a copy of an object of the island or, with none of its kind there, of a plain placed object (``plain``)."""
    return {
        "guid": row.guid, "name": row.name, "template": row.template, "cfg": cfg, "clone": True, "variation": 0,
        "plain": True, "building": row.building, "terrain": list(row.terrain), "title": f"{row.name} ({row.guid})",
        "group": " / ".join(row.group),
    }  # fmt: skip


def to_json(rows: list[SPlaceable]) -> list[dict]:
    return [asdict(placeable) for placeable in rows]


def from_json(data: list[dict]) -> list[SPlaceable]:
    return [
        SPlaceable(**{**fields, "group": tuple(fields["group"]), "terrain": tuple(fields["terrain"])})
        for fields in data
    ]
