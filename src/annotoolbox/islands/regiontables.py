"""The tables an island port between regions (or between 117 and 1800) follows, made from the games' own files:
material ID -> material ID from the two material sets, prop file -> prop file and asset -> asset from what the
regions' vanilla islands place.

``inventory`` reads what islands place, ``material_table``, ``prop_table`` and ``object_table`` make one table each,
``build`` all of them into a ``Tables``. The rules: island-regions "The mapping".
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
from .regions import Region, plain_name, same_look

INVENTORY_FORMAT = 1  # the kept inventory's layout: a change of ``Inventory`` raises it
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
class Material:
    id: int
    label: str
    ground: str  # GroundType: what the prop set grows on it
    file: str
    family: str


@dataclass
class Inventory:
    """What a set of islands places: prop file (lower case, forward slashes) and asset GUID -> how often."""

    islands: int = 0
    props: Counter = field(default_factory=Counter)
    objects: Counter = field(default_factory=Counter)

    def to_json(self) -> dict:
        return {
            "islands": self.islands,
            "props": dict(self.props),
            "objects": {str(k): v for k, v in self.objects.items()},
        }

    @classmethod
    def from_json(cls, data: dict) -> Inventory:
        objects = Counter({int(k): v for k, v in data.get("objects", {}).items()})
        return cls(int(data.get("islands", 0)), Counter(data.get("props", {})), objects)


@dataclass
class Tables:
    """What a port changes: material IDs, prop files, assets (``None``: the object is removed) and the session."""

    materials: dict[int, int] = field(default_factory=dict)
    props: dict[str, str | None] = field(default_factory=dict)  # None: the prop is removed
    objects: dict[int, int | None] = field(default_factory=dict)
    material_set: str | None = None  # the target's set ("" the engine's default), None: unchanged
    session: dict[str, str] = field(default_factory=dict)  # sessionsettings keys -> value

    def changes_ids(self) -> bool:
        return any(a != b for a, b in self.materials.items())


def prop_files(rd: filedb.Tag) -> list[str]:
    """The prop files of an island's ``PropGrid`` (as stored: UTF-16, the island's own slashes)."""
    names = rd.path("PropGrid/FileNames")
    return [c.data.decode("utf-16-le") for c in names.children] if names is not None else []


def norm(path: str) -> str:
    return path.replace("\\", "/").lower()


def island_inventory(gd: filedb.Tag, rd: filedb.Tag, into: Inventory) -> None:
    """Add what one island places (``gd`` / ``rd``: its two members) to ``into``."""
    files = [norm(p) for p in prop_files(rd)]
    inst = rd.path("PropGrid/Instances")
    for p in inst.children if inst is not None else []:
        index = p.find("Index") if isinstance(p, filedb.Tag) else None
        if index is not None and (i := struct.unpack_from("<I", index.data)[0]) < len(files):
            into.props[files[i]] += 1
    for _group, o in a7mread.object_tags(gd.path("GameSessionManager")):
        guid = o.find("guid")
        if guid is not None and len(guid.data) >= 4:
            into.objects[struct.unpack_from("<I", guid.data)[0]] += 1
    into.islands += 1


def inventory(read: Callable[[str], bytes], paths: Iterable[str]) -> Inventory:
    """What the islands at ``paths`` (game paths, read through ``read``) place; an island that does not read is skipped."""
    out = Inventory()
    for path in paths:
        try:
            gd, rd = (filedb.read(b) for b in a7mread.members(read(path), "gamedata.data", "rd3d.data"))
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


def set_materials(text: str) -> list[Material]:
    """A material set's entries in its list order (the order 117 and 1800 draw them in)."""
    out = []
    for entry in re.findall(r"<Material>.*?\r?\n</>", text, re.S):
        ident = re.search(r"<ID>(\d+)</>", entry)
        if not ident:
            continue
        label = (re.search(r"<ImportLabel>([^<]*)</>", entry) or [None, ""])[1]
        ground = (re.search(r"<GroundType>([^<]*)</>", entry) or [None, ""])[1]
        file = (re.search(r"<FileName>([^<]*)</>", entry) or [None, ""])[1].replace("\\", "/")
        out.append(Material(int(ident[1]), label, ground, file, material_family(label, file)))
    return out


def _candidates(fam: str, dst: list[Material]) -> list[Material]:
    for kind in (fam, *FALLBACK.get(fam, ())):
        found = [m for m in dst if m.family == kind]
        if found:
            return found
    return dst


def _distance(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) if a is not None and b is not None else 1e9


def material_table(
    src_text: str, dst_text: str, colour: Callable[[str, str], tuple | None] | None = None
) -> dict[int, int]:
    """Source set ID -> target set ID: the same family (or its fallback), then the same ``GroundType``, then the
    nearest mean diffuse colour (``colour(side, file)``, side ``"src"`` or ``"dst"``; None without), then list order."""
    src, dst = set_materials(src_text), [m for m in set_materials(dst_text) if m.id < ID_LIMIT]
    if not dst:
        return {}
    shade = colour or (lambda _side, _file: None)
    dst_colours = {m.id: shade("dst", m.file) for m in dst}
    table = {}
    for m in (m for m in src if m.id < ID_LIMIT):
        cands = _candidates(m.family, dst)
        own = shade("src", m.file)
        best = min(
            enumerate(cands),
            key=lambda kc: (kc[1].ground.lower() != m.ground.lower(), _distance(own, dst_colours[kc[1].id]), kc[0]),
        )
        table[m.id] = best[1].id
    return table


def prop_kind(path: str) -> tuple[str, str]:
    """A prop file's kind: the game's prop layer word and the first shape word of its path."""
    low = norm(path)
    return path_category(low), next((w for w in SHAPE_WORDS if w in low), "")


def _ranked(props: Counter, keep: set[str]) -> dict[tuple[str, str], list[str]]:
    groups: dict[tuple[str, str], list[str]] = {}
    for path, _n in sorted(((p, n) for p, n in props.items() if p in keep), key=lambda pn: (-pn[1], pn[0])):
        groups.setdefault(prop_kind(path), []).append(path)
    return groups


def prop_table(src: Inventory, dst: Inventory, drop_unmapped: bool = False) -> dict[str, str | None]:
    """Prop file -> prop file for the source region's own props (placed by its islands, not by the target's), each
    to the target's own of the same kind in order of use; a kind the target lacks by the layer word alone. Without
    any, a prop is kept (left out of the table) or, with ``drop_unmapped`` (another game), removed (None)."""
    own_src, own_dst = set(src.props) - set(dst.props), set(dst.props) - set(src.props)
    targets = _ranked(dst.props, own_dst)
    by_layer: dict[str, list[str]] = {}
    for path, _n in sorted(((p, dst.props[p]) for p in own_dst), key=lambda pn: (-pn[1], pn[0])):
        by_layer.setdefault(prop_kind(path)[0], []).append(path)
    table: dict[str, str | None] = {}
    for kind, paths in _ranked(src.props, own_src).items():
        cands = targets.get(kind) or by_layer.get(kind[0]) or []
        for k, path in enumerate(paths):
            if cands:
                table[path] = cands[k % len(cands)]
            elif drop_unmapped:
                table[path] = None
    return table


AssetNames = Callable[[int], tuple[str, str] | None]  # GUID -> (template, name), None for an asset not found


def words(name: str, template: str = "") -> set[str]:
    """The words of a plain name that say what an asset is beyond its template: three letters or more, no numbers,
    none the template's name holds (``slot`` of a Slot)."""
    return {w for w in name.split() if len(w) >= 3 and w.isalpha() and w not in template.lower()}


def _closest(plain: str, template: str, cands: list[tuple[int, str]]) -> int | None:
    """The candidate (GUID, plain name; most placed first) sharing the most words with ``plain``, None if none shares one."""
    own = words(plain, template)
    best = max(cands, key=lambda c: len(own & words(c[1], template)), default=None)
    return best[0] if best is not None and own & words(best[1], template) else None


def object_table(src: Inventory, dst: Inventory, names: tuple[AssetNames, AssetNames], games: tuple[str, str]):
    """Asset -> asset for the source region's own objects: the target's own asset of the same template and the same
    name without the region words, else (for an asset whose name carries a region word, or any of another game) the
    target's own of that template sharing the most words of the name (most placed first), else None (removed). An
    asset whose name carries no region word, or one both games have alike, is kept (not listed)."""
    src_names, dst_names = names
    other_game = games[0] != games[1]
    # a GUID is one asset only within a game: the other game's islands placing the same number place another asset
    shared = set() if other_game else set(src.objects) & set(dst.objects)
    by_name: dict[tuple[str, str], int] = {}
    by_template: dict[str, list[tuple[int, str]]] = {}
    for g in (g for g, _n in dst.objects.most_common() if g not in shared):
        info = dst_names(g)
        if info is not None:
            plain = plain_name(info[1], games[1])
            by_name.setdefault((info[0], plain), g)
            by_template.setdefault(info[0], []).append((g, plain))
    table: dict[int, int | None] = {}
    for g in sorted(set(src.objects) - shared):
        info = src_names(g)
        if info is None:
            if other_game:
                table[g] = None
            continue
        template, name = info
        plain = plain_name(name, games[0])
        if other_game and dst_names(g) == info:
            continue  # the engine's own asset in both games (coast lines, rivers): kept
        if (template, plain) in by_name:
            table[g] = by_name[template, plain]
        elif other_game or plain != plain_name(name, ""):
            table[g] = _closest(plain, template, by_template.get(template, []))
    return table


@dataclass(frozen=True)
class Sources:
    """One side of a port: its region, its material set's text, its inventory and its asset names."""

    region: Region
    set_text: str
    inventory: Inventory
    names: AssetNames


def build(src: Sources, dst: Sources, colour: Callable[[str, str], tuple | None] | None = None) -> Tables:
    """Every table from one region to another (of the same game or of the other game of the generation)."""
    other_game = src.region.game != dst.region.game
    same = same_look(src.region, dst.region)
    tables = Tables()
    if not same:
        tables.materials = material_table(src.set_text, dst.set_text, colour) if src.set_text and dst.set_text else {}
        tables.props = prop_table(src.inventory, dst.inventory, drop_unmapped=other_game)
        tables.objects = object_table(
            src.inventory, dst.inventory, (src.names, dst.names), (src.region.game, dst.region.game)
        )
        if src.region.material_set != dst.region.material_set:
            tables.material_set = dst.region.material_set
        tables.session = {"ambient": dst.region.ambient, "vegetation": dst.region.vegetation}
    return tables
