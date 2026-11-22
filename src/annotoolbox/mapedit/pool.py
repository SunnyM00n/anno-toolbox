"""What a game says about its maps beside the template files: the island pool (``RandomIsland`` assets), the size
classes of the map generator, the ``MapTemplate`` assets, the ``Slot``, ``FertilitySet`` and ``Fertility`` assets, the game's island
files, each island's grid, its picture and its slot objects.

``CGameMaps.read(tree, game)`` reads them once from the game's asset list (off the window's thread); the window and the
checks ask it. docs/map-editor.md "The files per game" and "Mine slots and fertilities".
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from ..formats import a7mread, filedb

ASSET_FILES = {"anno117": "data/base/config/export/assets.xml", "anno1800": "data/config/export/main/asset/assets.xml"}
ISLAND_SUFFIX = {"anno117": ".a7m", "anno1800": ".a7m"}  # the older games' islands are .isd
# the generator's largest island per size class when the asset list has none (its MapGenerator asset, 2026-11-13)
DEFAULT_CLASSES = {
    "anno117": {0: 256, 1: 320, 2: 432, 3: 432, 4: 2048},
    "anno1800": {0: 192, 1: 272, 2: 384},
}
_CLASS_NAMES = {"Small": 0, "Medium": 1, "Large": 2, "XL": 3, "Continental": 4}
_NAME_CLASS = re.compile(r"_(s|m|l|xl)_\d|_(small|medium|large)_", re.I)
PICTURES = ("mapimage.png", "gamemapimage.png", "activemapimage.png", "provincemapimage.png")
OLD_PICTURES = (".png", "_minimap.png")  # beside an older game's .isd
_ISD_WIDTH = re.compile(rb"<(?:m_)?Width>(\d+)</")


@dataclass(frozen=True)
class SPoolIsland:
    guid: int
    name: str
    path: str
    region: str
    type: str  # the IslandType name
    size_class: int  # 0 Small ... (117: IslandPlaceholderSize, untagged Small; 1800: by the file's name)
    difficulty: str = ""


@dataclass(frozen=True)
class SIslandInfo:
    """An island file's grid edge and its active area inside the grid (x0, z0, x1, z1; None: the whole grid)."""

    size: int = 0
    active: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class STemplateAsset:
    guid: int
    name: str
    filename: str  # TemplateFilename (the .a7t)
    region: str = ""
    enlarged: str = ""  # EnlargedTemplateFilename
    text: str = ""  # the asset as the game's list has it


@dataclass(frozen=True)
class SSlotObject:
    """A ``Slot`` object of an island (a mine slot): its object id, its asset and that asset's name."""

    id: int
    guid: int
    name: str


@dataclass
class CGameMaps:
    m_game: str
    m_pool: list[SPoolIsland] = field(default_factory=list)
    m_classes: dict[int, int] = field(default_factory=dict)
    m_templates: list[STemplateAsset] = field(default_factory=list)
    m_islands: dict[str, SIslandInfo] = field(default_factory=dict)  # island path -> its grid and land (on demand)
    m_slot_assets: dict[int, str] = field(default_factory=dict)  # GUID -> name of the ``Slot`` assets
    m_fertility_sets: dict[int, str] = field(default_factory=dict)  # GUID -> name of the ``FertilitySet`` assets
    m_fertilities: dict[int, str] = field(default_factory=dict)  # GUID -> name of the ``Fertility`` assets
    m_island_files: list[str] = field(default_factory=list)  # every island file of the game
    m_positions: object = None  # the asset lists' position blocks (``grow.SAssetPositions``), read on the first growth

    @classmethod
    def read(cls, tree, game: str) -> CGameMaps:
        path = ASSET_FILES.get(game)
        text = tree.read(path).decode("utf-8", "replace") if path and tree.exists(path) else ""
        suffix = ISLAND_SUFFIX.get(game, ".isd")
        return cls(
            game,
            read_pool(text),
            size_classes(text, game),
            read_templates(text),
            m_slot_assets=named_assets(text, "Slot"),
            m_fertility_sets=named_assets(text, "FertilitySet"),
            m_fertilities=named_assets(text, "Fertility"),
            m_island_files=sorted(island_path for island_path in tree.m_paths if island_path.endswith(suffix)),
        )

    def asset_of(self, template_path: str) -> STemplateAsset | None:
        """The ``MapTemplate`` asset naming a template (its ``.a7t``, ``.a7tinfo`` or ``.a7te``)."""
        stem = stem_of(template_path)
        return next(
            (
                map_template
                for map_template in self.m_templates
                if stem in (stem_of(map_template.filename), stem_of(map_template.enlarged))
            ),
            None,
        )

    def island(self, tree, path: str) -> SIslandInfo:
        """A fixed island's grid and active area (its ``.a7minfo``), zero when it cannot be read."""
        key = path.replace("\\", "/").lower()
        if key not in self.m_islands:
            self.m_islands[key] = read_island_info(tree, key)
        return self.m_islands[key]


def stem_of(path: str) -> str:
    name = path.replace("\\", "/").lower()
    return name.rsplit(".", 1)[0] if name.rsplit("/", 1)[-1].count(".") else name


def _field(block: str, name: str) -> str:
    match = re.search(rf"<{name}>([^<]*)</{name}>", block)
    return match.group(1).strip() if match else ""


def _blocks(text: str, template: str) -> list[str]:
    return re.findall(rf"<Asset>\s*<Template>{template}</Template>.*?</Asset>", text, re.S)


def read_pool(text: str) -> list[SPoolIsland]:
    out = []
    for block in _blocks(text, "RandomIsland"):
        path = _field(block, "FilePath")
        size = _field(block, "IslandPlaceholderSize")
        if size:
            size_class = _CLASS_NAMES.get(size, 0)
        else:
            match = _NAME_CLASS.search(path.rsplit("/", 1)[-1])
            letter = (match.group(1) or match.group(2) or "s").lower()[0] if match else "s"
            size_class = {"s": 0, "m": 1, "l": 2, "x": 3}[letter]
        out.append(
            SPoolIsland(
                int(_field(block, "GUID") or 0),
                _field(block, "Name"),
                path.replace("\\", "/").lower(),
                _field(block, "IslandRegion"),
                _field(block, "IslandType") or "Normal",
                size_class,
                _field(block, "IslandDifficulty"),
            )
        )
    return out


def size_classes(text: str, game: str) -> dict[int, int]:
    """The largest island of each size class, from the ``MapGenerator`` asset (``IslandSizes``)."""
    out = dict(DEFAULT_CLASSES.get(game, {0: 256}))
    block = next(iter(_blocks(text, "MapGenerator")), "")
    sizes = re.search(r"<IslandSizes>(.*?)</IslandSizes>", block, re.S)
    for name, inner in re.findall(r"<(\w+)>(.*?)</\1>", sizes.group(1) if sizes else "", re.S):
        max_width = _field(inner, "MaxWidth")
        if name in _CLASS_NAMES and max_width.isdigit():
            out[_CLASS_NAMES[name]] = int(max_width)
    return out


def named_assets(text: str, template: str) -> dict[int, str]:
    """GUID -> name of every asset of ``template`` in the asset list."""
    out = {}
    for block in _blocks(text, template):
        guid = _field(block, "GUID")
        if guid.isdigit():
            out[int(guid)] = _field(block, "Name")
    return out


def island_slots(tree, path: str, slots: dict[int, str]) -> list[SSlotObject]:
    """The objects of an island (117, 1800) whose asset is one of ``slots`` (its mine slots), in file order; none
    when the island does not read."""
    try:
        session = filedb.read(a7mread.members(tree.read(path), "gamedata.data")[0]).path("GameSessionManager")
    except (ValueError, struct.error, KeyError, OSError):  # an archive or island error is a ValueError
        return []
    out = []
    for _group, tag in a7mread.object_tags(session) if session is not None else ():
        oid, guid = tag.find("ID"), tag.find("guid")
        if isinstance(oid, filedb.SAttrib) and len(oid.data) == 8 and isinstance(guid, filedb.SAttrib):
            guid_value = struct.unpack("<I", guid.data[:4])[0]
            if guid_value in slots:
                out.append(SSlotObject(struct.unpack("<Q", oid.data)[0], guid_value, slots[guid_value]))
    return out


def read_templates(text: str) -> list[STemplateAsset]:
    out = []
    for block in _blocks(text, "MapTemplate"):
        filename = _field(block, "TemplateFilename")
        if filename:
            out.append(
                STemplateAsset(
                    int(_field(block, "GUID") or 0),
                    _field(block, "Name"),
                    filename.lower(),
                    _field(block, "TemplateRegion"),
                    _field(block, "EnlargedTemplateFilename").lower(),
                    block,
                )
            )
    return out


def read_island_info(tree, path: str) -> SIslandInfo:
    """``MapSize`` and ``ActiveMapRect`` of a 117 or 1800 island's ``.a7minfo``; an older game's ``Width`` from the
    head of its ``.isd``."""
    if not path.endswith(".a7m"):
        head = tree.read(path)[:4096] if tree.exists(path) else b""
        match = _ISD_WIDTH.search(head)
        return SIslandInfo(int(match.group(1)) if match else 0)
    info_path = path.rsplit(".", 1)[0] + ".a7minfo"
    if not tree.exists(info_path):
        return SIslandInfo()
    try:
        doc = filedb.read(tree.read(info_path))
    except (ValueError, struct.error):
        return SIslandInfo()
    size, rect = doc.find("MapSize"), doc.find("ActiveMapRect")
    sized = isinstance(size, filedb.SAttrib) and len(size.data) >= 8
    edge = struct.unpack("<2i", size.data[:8])[0] if sized else 0
    active = struct.unpack("<4i", rect.data[:16]) if isinstance(rect, filedb.SAttrib) and len(rect.data) >= 16 else None
    return SIslandInfo(edge, active if active and active[2] > active[0] else None)


def picture_path(tree, island: str) -> str:
    """The game path of an island's map picture, "" when it has none (117 and 1800: ``_gamedata``; older: beside)."""
    key = island.replace("\\", "/").lower()
    folder, _, name = key.rpartition("/")
    stem = name.rsplit(".", 1)[0]
    if key.endswith(".a7m"):
        candidates = [f"{folder}/_gamedata/{stem}/{picture}" for picture in PICTURES]
    else:
        candidates = [f"{folder}/{stem}{picture}" for picture in OLD_PICTURES]
    return next((candidate for candidate in candidates if tree.exists(candidate)), "")


CLAMP_ROWS = 4  # a run this long of rows that repeat the one before, down to the picture's edge, is the clamp
DARK = 12  # a pixel whose red, green and blue add up to no more than this is the black around the land


def clean_picture(rgba):
    """An island picture (rows x columns x RGBA, uint8) as the map draws it: the black around the land and the
    stripes below it transparent. The game renders some pictures past the island's last row (Cinis), which repeats
    that row down to the edge."""
    import numpy as np

    out = np.array(rgba, dtype=np.uint8, copy=True)
    rgb = out[..., :3].astype(np.int16)
    out[rgb.sum(axis=-1) <= DARK, 3] = 0
    repeats = np.all(np.abs(rgb[1:] - rgb[:-1]) <= 2, axis=(1, 2))  # row i + 1 repeats row i
    run = 0
    while run < len(repeats) and repeats[len(repeats) - 1 - run]:
        run += 1
    if run >= CLAMP_ROWS:
        out[len(out) - run :, :, 3] = 0
    return out
