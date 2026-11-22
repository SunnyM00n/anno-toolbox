"""The tables an island port between regions (or between 117 and 1800) follows, made from the games' own files:
material ID -> material ID from the two material sets, prop file -> prop file and asset -> asset from what the
regions' vanilla islands place.

``inventory`` reads what islands place, ``material_table``, ``prop_table`` and ``object_table`` make one table each,
``build`` all of them into a ``STables``. The rules: island-regions "The mapping".
"""

from __future__ import annotations

import re
import struct
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..formats import a7mread, filedb
from .materialorder import FAMILY_WORDS
from .propgrid import path_category
from .regions import SRegion, plain_name, same_look

INVENTORY_FORMAT = 1  # the kept inventory's layout: a change of ``SInventory`` raises it
ID_LIMIT = 32  # the maps store a material ID in 5 bits
# families of their own before ``materialorder.FAMILY_WORDS``, the first word found decides
OWN_FAMILIES = (("underwater", "underwater"), ("snow", "snow"), ("ice", "snow"), ("marsh", "marsh"))
OWN_FAMILIES += (("swamp", "marsh"), ("reed", "marsh"))
FALLBACK = {  # where a family goes when the target set has none of it
    "snow": ("grass", "soil"),
    "marsh": ("grass", "soil"),
    "underwater": ("sand", "soil"),
    "water": ("sand", "soil"),
    "grass": ("snow", "soil", "sand"),  # the Arctic grows on snow what the others grow on grass
    "soil": ("sand", "grass"),
    "sand": ("soil", "grass"),
    "rock": ("soil", "sand"),
}
# what a prop is beyond its layer; words of a region's look (snow, palm, jungle) are not among them
SHAPE_WORDS = ("cliff", "mountain", "pebble", "dead", "stump", "reed", "flower", "fern", "skirt", "decal")


@dataclass(frozen=True)
class SMaterial:
    id: int
    label: str
    ground: str  # GroundType: what the prop set grows on it
    file: str
    family: str


@dataclass
class SInventory:
    """What a set of islands places: prop file (lower case, forward slashes) and asset GUID -> how often."""

    islands: int = 0
    props: Counter = field(default_factory=Counter)
    objects: Counter = field(default_factory=Counter)

    def to_json(self) -> dict:
        return {
            "islands": self.islands,
            "props": dict(self.props),
            "objects": {str(guid): count for guid, count in self.objects.items()},
        }

    @classmethod
    def from_json(cls, data: dict) -> SInventory:
        objects = Counter({int(guid): count for guid, count in data.get("objects", {}).items()})
        return cls(int(data.get("islands", 0)), Counter(data.get("props", {})), objects)


@dataclass
class STables:
    """What a port changes: material IDs, prop files, assets (``None``: the object is removed) and the session."""

    materials: dict[int, int] = field(default_factory=dict)
    props: dict[str, str | None] = field(default_factory=dict)  # None: the prop is removed
    objects: dict[int, int | None] = field(default_factory=dict)
    material_set: str | None = None  # the target's set ("" the engine's default), None: unchanged
    session: dict[str, str] = field(default_factory=dict)  # sessionsettings keys -> value

    def changes_ids(self) -> bool:
        return any(source_id != target_id for source_id, target_id in self.materials.items())


def prop_files(rd: filedb.STag) -> list[str]:
    """The prop files of an island's ``PropGrid`` (as stored: UTF-16, the island's own slashes)."""
    names = rd.path("PropGrid/FileNames")
    return [child.data.decode("utf-16-le") for child in names.children] if names is not None else []


def norm(path: str) -> str:
    return path.replace("\\", "/").lower()


def island_inventory(gd: filedb.STag, rd: filedb.STag, into: SInventory) -> None:
    """Add what one island places (``gd`` / ``rd``: its two members) to ``into``."""
    files = [norm(prop) for prop in prop_files(rd)]
    instances = rd.path("PropGrid/Instances")
    for prop in instances.children if instances is not None else []:
        index = prop.find("Index") if isinstance(prop, filedb.STag) else None
        if index is not None and (i := struct.unpack_from("<I", index.data)[0]) < len(files):
            into.props[files[i]] += 1
    for _group, placed in a7mread.object_tags(gd.path("GameSessionManager")):
        guid = placed.find("guid")
        if guid is not None and len(guid.data) >= 4:
            into.objects[struct.unpack_from("<I", guid.data)[0]] += 1
    into.islands += 1


def inventory(read: Callable[[str], bytes], paths: Iterable[str]) -> SInventory:
    """What the islands at ``paths`` (game paths, read through ``read``) place; an island that does not read is skipped."""
    out = SInventory()
    for path in paths:
        try:
            gd, rd = (filedb.read(member) for member in a7mread.members(read(path), "gamedata.data", "rd3d.data"))
        except (KeyError, OSError, ValueError):
            continue
        island_inventory(gd, rd, out)
    return out


def _family_in(name: str) -> str | None:
    low = name.lower()
    return next((kind for word, kind in (*OWN_FAMILIES, *FAMILY_WORDS) if word in low), None)


def material_family(label: str, file: str) -> str:
    """A set entry's family: read off its material file's name (the texture it is), else its label, by
    ``OWN_FAMILIES`` and ``materialorder.FAMILY_WORDS``; ``soil`` when neither says."""
    return _family_in(file.rsplit("/", 1)[-1]) or _family_in(label) or "soil"


def set_materials(text: str) -> list[SMaterial]:
    """A material set's entries in its list order (the order 117 and 1800 draw them in)."""
    out = []
    for entry in re.findall(r"<Material>.*?\r?\n</>", text, re.S):
        ident = re.search(r"<ID>(\d+)</>", entry)
        if not ident:
            continue
        label = (re.search(r"<ImportLabel>([^<]*)</>", entry) or [None, ""])[1]
        ground = (re.search(r"<GroundType>([^<]*)</>", entry) or [None, ""])[1]
        file = (re.search(r"<FileName>([^<]*)</>", entry) or [None, ""])[1].replace("\\", "/")
        out.append(SMaterial(int(ident[1]), label, ground, file, material_family(label, file)))
    return out


def family_candidates(family: str, target: list[SMaterial]) -> list[SMaterial]:
    """The target's materials of a family, its fallback families when it has none (``FALLBACK``), any when none."""
    for kind in (family, *FALLBACK.get(family, ())):
        found = [material for material in target if material.family == kind]
        if found:
            return found
    return target


def _distance(first, second) -> float:
    return (
        sum((x - y) ** 2 for x, y in zip(first, second, strict=True))
        if first is not None and second is not None
        else 1e9
    )


def material_table(
    source_text: str, target_text: str, colour: Callable[[str, str], tuple | None] | None = None
) -> dict[int, int]:
    """Source set ID -> target set ID: the same family (or its fallback), then the same ``GroundType``, then the
    nearest mean diffuse colour (``colour(side, file)``, side ``"src"`` or ``"dst"``; None without), then list order."""
    source_materials, target_materials = (
        set_materials(source_text),
        [material for material in set_materials(target_text) if material.id < ID_LIMIT],
    )
    if not target_materials:
        return {}
    shade = colour or (lambda _side, _file: None)
    dst_colours = {material.id: shade("dst", material.file) for material in target_materials}
    table = {}
    for material in (material for material in source_materials if material.id < ID_LIMIT):
        candidates = family_candidates(material.family, target_materials)
        own = shade("src", material.file)
        best = min(
            enumerate(candidates),
            key=lambda indexed_candidate: (
                indexed_candidate[1].ground.lower() != material.ground.lower(),
                _distance(own, dst_colours[indexed_candidate[1].id]),
                indexed_candidate[0],
            ),
        )
        table[material.id] = best[1].id
    return table


def with_unlisted(table: dict[int, int], target_text: str) -> dict[int, int]:
    """``table`` with every ID its source set does not list (a map may hold one: 1800's ``colony03_a02_02`` lays its
    deep sea bed in ID 31, which ``arctic_01`` lacks) sent where the underwater family goes: kept as it is, the ID
    names nothing or another material in the target set (island-regions "Between Anno 117 and Anno 1800")."""
    target_materials = [material for material in set_materials(target_text) if material.id < ID_LIMIT]
    if not target_materials:
        return dict(table)
    sea = family_candidates("underwater", target_materials)[0].id
    return {i: table.get(i, sea) for i in range(ID_LIMIT)}


def prop_kind(path: str) -> tuple[str, str]:
    """A prop file's kind: the game's prop layer word and the first shape word of its path."""
    low = norm(path)
    return path_category(low), next((word for word in SHAPE_WORDS if word in low), "")


def _ranked(props: Counter, keep: set[str]) -> dict[tuple[str, str], list[str]]:
    groups: dict[tuple[str, str], list[str]] = {}
    for path, _n in sorted(
        ((prop, count) for prop, count in props.items() if prop in keep),
        key=lambda prop_count: (-prop_count[1], prop_count[0]),
    ):
        groups.setdefault(prop_kind(path), []).append(path)
    return groups


def prop_table(source: SInventory, target: SInventory, drop_unmapped: bool = False) -> dict[str, str | None]:
    """Prop file -> prop file for the source region's own props (placed by its islands, not by the target's), each
    to the target's own of the same kind in order of use; a kind the target lacks by the layer word alone. Without
    any, a prop is kept (left out of the table) or, with ``drop_unmapped`` (another game), removed (None)."""
    own_source, own_target = set(source.props) - set(target.props), set(target.props) - set(source.props)
    targets = _ranked(target.props, own_target)
    by_layer: dict[str, list[str]] = {}
    for path, _n in sorted(
        ((prop, target.props[prop]) for prop in own_target), key=lambda prop_count: (-prop_count[1], prop_count[0])
    ):
        by_layer.setdefault(prop_kind(path)[0], []).append(path)
    table: dict[str, str | None] = {}
    for kind, paths in _ranked(source.props, own_source).items():
        candidates = targets.get(kind) or by_layer.get(kind[0]) or []
        for k, path in enumerate(paths):
            if candidates:
                table[path] = candidates[k % len(candidates)]
            elif drop_unmapped:
                table[path] = None
    return table


AssetNames = Callable[[int], tuple[str, str] | None]  # GUID -> (template, name), None for an asset not found


def words(name: str, template: str = "") -> set[str]:
    """The words of a plain name that say what an asset is beyond its template: three letters or more, no numbers,
    none the template's name holds (``slot`` of a Slot)."""
    return {word for word in name.split() if len(word) >= 3 and word.isalpha() and word not in template.lower()}


def _closest(plain: str, template: str, candidates: list[tuple[int, str]]) -> int | None:
    """The candidate (GUID, plain name; most placed first) sharing the most words with ``plain``, None if none shares one."""
    own = words(plain, template)
    best = max(candidates, key=lambda candidate: len(own & words(candidate[1], template)), default=None)
    return best[0] if best is not None and own & words(best[1], template) else None


def object_table(source: SInventory, target: SInventory, names: tuple[AssetNames, AssetNames], games: tuple[str, str]):
    """Asset -> asset for the source region's own objects: the target's own asset of the same template and the same
    name without the region words, else (for an asset whose name carries a region word, or any of another game) the
    target's own of that template sharing the most words of the name (most placed first), else None (removed). An
    asset whose name carries no region word, or one both games have alike, is kept (not listed)."""
    source_names, target_names = names
    other_game = games[0] != games[1]
    # a GUID is one asset only within a game: the other game's islands placing the same number place another asset
    shared = set() if other_game else set(source.objects) & set(target.objects)
    by_name: dict[tuple[str, str], int] = {}
    by_template: dict[str, list[tuple[int, str]]] = {}
    for guid in (guid for guid, _n in target.objects.most_common() if guid not in shared):
        asset = target_names(guid)
        if asset is not None:
            plain = plain_name(asset[1], games[1])
            by_name.setdefault((asset[0], plain), guid)
            by_template.setdefault(asset[0], []).append((guid, plain))
    table: dict[int, int | None] = {}
    for guid in sorted(set(source.objects) - shared):
        asset = source_names(guid)
        if asset is None:
            if other_game:
                table[guid] = None
            continue
        template, name = asset
        plain = plain_name(name, games[0])
        if other_game and target_names(guid) == asset:
            continue  # the engine's own asset in both games (coast lines, rivers): kept
        if (template, plain) in by_name:
            table[guid] = by_name[template, plain]
        elif other_game or plain != plain_name(name, ""):
            table[guid] = _closest(plain, template, by_template.get(template, []))
    return table


@dataclass(frozen=True)
class SSources:
    """One side of a port: its region, its material set's text, its inventory and its asset names."""

    region: SRegion
    set_text: str
    inventory: SInventory
    names: AssetNames


def build(source: SSources, target: SSources, colour: Callable[[str, str], tuple | None] | None = None) -> STables:
    """Every table from one region to another (of the same game or of the other game of the generation)."""
    other_game = source.region.game != target.region.game
    same = same_look(source.region, target.region)
    tables = STables()
    if not same:
        tables.materials = (
            material_table(source.set_text, target.set_text, colour) if source.set_text and target.set_text else {}
        )
        tables.props = prop_table(source.inventory, target.inventory, drop_unmapped=other_game)
        tables.objects = object_table(
            source.inventory, target.inventory, (source.names, target.names), (source.region.game, target.region.game)
        )
        if source.region.material_set != target.region.material_set:
            tables.material_set = target.region.material_set
        tables.session = {"ambient": target.region.ambient, "vegetation": target.region.vegetation}
    return tables
