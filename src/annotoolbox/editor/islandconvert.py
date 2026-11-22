"""Island ports within an engine generation, as the Islands app and the command line run it: an island of 117 or
1800 made into an island of another region of its game (``regionconvert``) or of the other game (``gametransfer``),
written as a new island of a mod and registered in the target region's pool.

``targets`` lists what an island can become, ``CGameSide`` is one installed game (files, asset names, what its regions'
islands place), ``convert`` converts one island, ``convert_all`` several for one mod, ``write_mod`` writes the mod
(``islandcommands`` is the command line). docs/island-regions.md.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import filedb
from ..infra import buildcache
from ..infra.i18n import tr
from ..islands import regions, regiontables
from ..islands.regions import SRegion
from ..mod.modxml import modinfo
from ..modloader import modswap
from ..sources import gametree
from ..suite import features
from . import (
    gametransfer,
    groundedit,
    islandplaces,
    portdrops,
    portpictures,
    portreskin,
    regionconvert,
    slotkinds,
    slotlinks,
)
from .assetmodels import CAssetModels
from .formats.a7m import edit117 as a7m_edit117
from .formats.a7m.reader import CGameFiles, CIslandFile1800
from .islandfiles import island_paths
from .regionconvert import ASSET_GAMES, DEFAULT_SETS, material_set_of

SIDE_FILES = (".a7me",)  # copied beside the ported island (the .a7minfo is written)
FIT_MAP = "map"  # Request.fit: no pool asset; the island takes its donor's fixed places on the game's maps
FITS = (FIT_MAP,)
GAME_NAMES = {"anno117": "Anno 117", "anno1800": "Anno 1800"}
PORT_REPORT = "port-report.txt"  # in the mod: what each island's port left out or swapped, by name


@dataclass(frozen=True)
class SRequest:
    """One island to convert: its game and game path, the target region (``<game>:<key>``), the new island's name
    (default ``<island>_<region>``), the pool region it is registered in (default the target's) and how it is placed
    (``fit``: "" a pool asset of its size, ``FIT_MAP`` a fixed place on the game's maps, island-regions "A place on a
    map")."""

    game: str
    source: str
    target: str
    name: str = ""
    pool_region: str = ""
    fit: str = ""
    slot_links: tuple[tuple[int, int | None], ...] = ()  # the person's slot links between the games (``slotlinks``)
    reskin: bool = False  # models only the source has are kept, drawn with the target's ground (``portreskin``)


@dataclass
class SPorted:
    """A ported island: where it goes (a game path of the target game), its files and its pool asset."""

    request: SRequest
    target: str
    a7m: bytes
    info: bytes
    gamedata: dict[str, bytes]  # every file of its ``_gamedata`` folder, by path inside it
    side: dict[str, bytes] = field(default_factory=dict)  # files at game paths (its .a7me)
    asset: str = ""  # the ModOp that registers it, "" when no pool asset could be found
    report: dict = field(default_factory=dict)
    # a finished mod folder an extension built (the 2070 island build: models, shaders and the pool beside the
    # island): ``write_mod`` copies it in and removes it
    folder: Path | None = None


def targets(game: str, path: str = "") -> list[tuple[str, str]]:
    """What an island of ``game`` at ``path`` can become, as (region id, label): the other regions of its game and the
    regions of the other game of its engine generation, then what an extension at ``features.ISLAND_CONVERSIONS``
    offers (none in the public edition; an island of 2070 is offered the 117 island build there)."""
    own = regions.region_of(game, path)
    found = []
    if game in regions.GENERATION:
        found = [
            (region.id, label(region))
            for generation_game in regions.GENERATION
            for region in regions.regions(generation_game)
            if region != own
        ]
    for extension in features.extensions(features.ISLAND_CONVERSIONS).values():
        found += list(extension.targets(game, path))
    return found


def label(region: SRegion) -> str:
    """The region's name with its game, for a list of targets."""
    return f"{GAME_NAMES[region.game]}: {tr(region.label)}"


class CGameSide:
    """One installed game a port reads: its files (117: ``islandview.CFiles``; 1800: a tree or ``CGameFiles``),
    its assets' names, what each region's pool islands place (kept in ``cache`` when given); ``root`` is its folder."""

    def __init__(self, key: str, files, cache: Path | None = None, root: Path | None = None) -> None:
        self.m_key = key
        self.m_root = root  # the install's folder, for a port that runs the game's own builder (an extension)
        self.m_files = CGameFiles(files) if not hasattr(files, "read") else files
        self.m_cache = cache
        self._m_models: CAssetModels | None = None
        self._m_pool: list[str] | None = None
        self._m_slots: tuple | None = None
        self._m_inventories: dict[str, regiontables.SInventory] = {}

    def read(self, path: str) -> bytes:
        """The bytes of a file of the game."""
        return self.m_files.read(path)

    def text(self, path: str) -> str:
        """A text file of the game, "" when it has none."""
        try:
            return self.read(path).decode("utf-8", "ignore") if path else ""
        except (KeyError, OSError):
            return ""

    def asset_names(self, guid: int) -> tuple[str, str] | None:
        """The (template, name) of an asset, None where the game has none with that GUID."""
        if self._m_models is None:
            self._m_models = CAssetModels.read(self.read, ASSET_GAMES[self.m_key])
        asset = self._m_models.asset(guid)
        return (asset.template, asset.name) if asset is not None else None

    def slots(self) -> tuple[dict[int, slotkinds.SSlotAsset], slotlinks.SResources]:
        """The game's slot assets and what its slot buildings say of them (``slotlinks``), read once."""
        if self._m_slots is None:
            if self._m_models is None:
                self._m_models = CAssetModels.read(self.read, ASSET_GAMES[self.m_key])
            self._m_slots = (slotkinds.read_assets(self._m_models), slotlinks.SResources.read(self._m_models))
        return self._m_slots

    def pool(self) -> list[str]:
        """The game's ``RandomIsland`` assets."""
        if self._m_pool is None:
            self._m_pool = a7m_edit117.pool_assets(self.text(ASSET_GAMES[self.m_key].assets))
        return self._m_pool

    def island_paths(self, region: SRegion) -> list[str]:
        """The paths of the region's pool islands."""
        return [
            path
            for path in island_paths(self.m_key, self.m_files)
            if "/pool/" in path and regions.region_of(self.m_key, path) == region
        ]

    def files_stamp(self) -> str:
        """What changes when the game's files do: its tree's stamp, else its install folder's (``game_stamp``: the
        archives' names, sizes and times), "" for files that say neither (a test's stand-in)."""
        found = getattr(self.m_files, "m_stamp", "")
        if not found and self.m_root is not None and Path(self.m_root).is_dir():
            found = gametree.game_stamp(self.m_key, Path(self.m_root))
        return str(found)

    def inventory(self, region: SRegion) -> regiontables.SInventory:
        """What the region's pool islands place (read once, then from the cache)."""
        if region.id in self._m_inventories:
            return self._m_inventories[region.id]
        paths = self.island_paths(region)
        stamp = hashlib.sha1("\n".join([self.files_stamp(), *paths]).encode("utf-8")).hexdigest()[:16]
        file = self.m_cache / f"region-inventory-{region.game}-{region.key}.json" if self.m_cache else None
        match = {"stamp": stamp, "format": regiontables.INVENTORY_FORMAT}
        kept = buildcache.read_kept(file, match) if file is not None else None
        found = regiontables.SInventory.from_json(kept.get("inventory") or {}) if kept is not None else None
        if found is None:
            found = regiontables.inventory(self.read, paths)
            if file is not None:
                buildcache.write_kept(file, {**match, "inventory": found.to_json()})
        self._m_inventories[region.id] = found
        return found

    def open(self, path: str):
        """The island at ``path`` opened for editing (117's ``CIslandFile``, 1800's ``CIslandFile1800``)."""
        cls = CIslandFile1800 if self.m_key == "anno1800" else a7m_edit117.CIslandFile
        return cls(self.read(path), self.read(path[:-4] + ".a7minfo"))


def colour_reader(source_side: CGameSide, target_side: CGameSide) -> Callable[[str, str], tuple | None]:
    """A material file's mean diffuse colour on either side (``regiontables.material_table``), None where it cannot be
    read (no Pillow, a map the decoders do not read)."""
    from ..islands.terrain2070 import material_colour

    seen: dict[tuple[str, str], tuple | None] = {}

    def colour(side: str, file: str) -> tuple | None:
        if (side, file) not in seen:
            try:
                seen[side, file] = material_colour((source_side if side == "src" else target_side).m_files, file)
            except Exception:  # pattern: ok (a reader's failure: the colour is not known, the other rules decide)
                seen[side, file] = None
        return seen[side, file]

    return colour


def _quiet(_line: str) -> None:
    pass


def target_path(region: SRegion, name: str) -> str:
    """The game path of the ported island's ``.a7m`` in the target region's folder."""
    return f"{region.folder}/{name}/{name}.a7m"


def new_name(request: SRequest, region: SRegion) -> str:
    """The ported island's name: the request's, else the source's stem and the region."""
    stem = request.name or f"{Path(request.source).stem}_{region.key}"
    return re.sub(r"[^a-z0-9_]+", "_", stem.lower()).strip("_")


def convert(
    source_side: CGameSide,
    target_side: CGameSide,
    request: SRequest,
    colours: bool = True,
    log: Callable[[str], None] = _quiet,
) -> SPorted:
    """Port one island; ``target_side`` is ``source_side`` for a region of the same game; ``log`` is told each stage (it takes a minute).
    An island of another engine generation (2070, 1404, 1701) is ported only by an extension at
    ``features.ISLAND_CONVERSIONS``, older to newer."""
    if source_side.m_key not in regions.GENERATION:
        return _extension_for(source_side.m_key, request).run(request, source_side, target_side, log)
    if not any(region.id == request.target for region in regions.REGIONS):
        raise ValueError(tr("islands.unknown_region_island_regions_lists", region=request.target))
    target = regions.region(request.target)
    own = regions.region_of(source_side.m_key, request.source)
    if own is None:
        raise ValueError(tr("islands.not_island_region", path=request.source, game=source_side.m_key))
    if target.game != source_side.m_key:
        return _transferred(
            source_side, target_side, request, target, colour_reader(source_side, target_side) if colours else None, log
        )
    log(tr("islands.reading_island", island=request.source))
    island_file = source_side.open(request.source)
    gamedata = groundedit.gamedata(source_side.m_files, request.source)
    set_text = source_side.text(material_set_of(island_file, source_side.m_key))
    sides = (
        regiontables.SSources(own, set_text, source_side.inventory(own), source_side.asset_names),
        regiontables.SSources(
            target,
            source_side.text(target.material_set or DEFAULT_SETS[source_side.m_key]),
            source_side.inventory(target),
            source_side.asset_names,
        ),
    )
    log(tr("islands.making_tables_s_islands", region=tr(target.label)))
    tables = regiontables.build(
        *sides, colour_reader(source_side, source_side) if colours and not regions.same_look(own, target) else None
    )
    path = target_path(target, new_name(request, target))
    reskinned = portreskin.SReskinned()
    if regions.same_look(own, target):  # the region itself, or Cape Trelawney and the Old World
        a7m, a7minfo, changed, report = (
            source_side.read(request.source),
            source_side.read(request.source[:-4] + ".a7minfo"),
            {},
            {},
        )
    else:
        log(tr("islands.changing_ground_props_objects"))
        ledger = portdrops.CLedger()
        ids = regionconvert.material_maps(island_file, gamedata)[0]  # before the apply turns the IDs
        portdrops.ground_entries(
            ledger,
            ids,
            island_file.size,
            (set_text, sides[1].set_text),
            regiontables.with_unlisted(tables.materials, sides[1].set_text),
        )
        if request.reskin:
            reskinned = portreskin.for_port(
                source_side, target_side, island_file.rd3d, tables, own, target, sides[1].set_text, ledger
            )
        changed, report = regionconvert.apply(island_file, gamedata, tables, ledger, source_side.asset_names)
        report["reskinned props"] = reskinned.count
        report["dropped"] = ledger.entries
        a7m, a7minfo = island_file.write(), island_file.write_info()
        if colours:
            log(tr("islands.drawing_map_pictures"))
            changed |= _redrawn(source_side, island_file, gamedata, changed, sides[1].set_text)
    done = SPorted(
        request,
        path,
        a7m,
        a7minfo,
        {relative_path: changed.get(relative_path) or gamedata.read(relative_path) for relative_path in gamedata.names},
        report=report,
    )
    _side_files(done, source_side, request.source)
    done.side.update(reskinned.files)
    if request.fit == FIT_MAP:  # a region's maps place their own islands: the Maps app puts this one
        done.report["maps"] = []
    else:
        done.asset = registration(source_side, request, target, path, request.source, island_file.size)
    done.report["tables"] = _table_summary(tables)
    return done


def source_games() -> list[str]:
    """The games whose islands can be ported: the engine generation's, then those an extension takes (none in the
    public edition)."""
    found = list(regions.GENERATION)
    for extension in features.extensions(features.ISLAND_CONVERSIONS).values():
        found += [game for game in extension.sources if game not in found]
    return found


def _extension_for(game: str, request: SRequest):
    """The first extension at ``features.ISLAND_CONVERSIONS`` that offers the island its target."""
    for extension in features.extensions(features.ISLAND_CONVERSIONS).values():
        if any(target_id == request.target for target_id, _label in extension.targets(game, request.source)):
            return extension
    raise ValueError(tr("islands.no_port_to", path=request.source, game=game, region=request.target))


def convert_all(
    source_side: CGameSide,
    target_side: CGameSide,
    requests: list[SRequest],
    colours: bool = True,
    log: Callable[[str], None] = _quiet,
) -> list[SPorted]:
    """Port several islands of one game into one target game, in the order asked: an island of another engine
    generation goes with the others of its extension into one build (``run_many``), the rest one by one."""
    if source_side.m_key in regions.GENERATION:
        return [convert(source_side, target_side, request, colours, log) for request in requests]
    groups: list[tuple[object, list[SRequest]]] = []
    for request in requests:
        extension = _extension_for(source_side.m_key, request)
        for known, batch in groups:
            if known is extension:
                batch.append(request)
                break
        else:
            groups.append((extension, [request]))
    done: dict[SRequest, SPorted] = {}
    for extension, batch in groups:
        found = (
            extension.run_many(batch, source_side, target_side, log)
            if extension.run_many
            else [extension.run(request, source_side, target_side, log) for request in batch]
        )
        done.update(zip(batch, found, strict=True))
    return [done[request] for request in requests]


def _redrawn(
    side: CGameSide, island_file, gamedata: groundedit.SGamedata, changed: dict[str, bytes], set_text: str
) -> dict[str, bytes]:
    """The ported island's map pictures drawn from its new ground (``portpictures``)."""
    final = groundedit.SGamedata(gamedata.names, lambda name: changed[name] if name in changed else gamedata.read(name))
    colour = colour_reader(side, side)
    return portpictures.redraw(island_file, final, set_text, lambda file: colour("dst", file), keep=True)


def _transferred(
    source_side: CGameSide, target_side: CGameSide, request: SRequest, target: SRegion, colour, log
) -> SPorted:
    """An island carried into the other game of the generation (``gametransfer``), as a ``SPorted``."""
    carried = gametransfer.transfer(source_side, target_side, request, target, colour, log)
    path = target_path(target, new_name(request, target))
    done = SPorted(request, path, carried.a7m, carried.a7minfo, carried.gamedata, report=carried.report)
    done.side.update(carried.files)
    _side_files(done, target_side, carried.donor)
    if request.fit == FIT_MAP:
        places = islandplaces.take_place(target_side, carried.donor, path, Path(path).stem)
        done.side.update(places)
        done.report["maps"] = sorted(places)
    else:
        grid = gametransfer.grid_size(filedb.read(carried.a7minfo))
        done.asset = registration(target_side, request, target, path, carried.donor, grid)
    done.report["tables"] = _table_summary(carried.tables)
    return done


def _side_files(done: SPorted, side: CGameSide, island: str) -> None:
    """Copy the island's side files (``SIDE_FILES``) beside the ported island, where it has them."""
    for suffix in SIDE_FILES:
        try:
            done.side[done.target[:-4] + suffix] = side.read(island[:-4] + suffix)
        except (KeyError, OSError):
            continue


def _table_summary(tables: regiontables.STables) -> dict:
    """The tables' size as a report entry."""
    return {"materials": tables.materials, "props": len(tables.props), "objects": len(tables.objects)}


def registration(
    target_side: CGameSide, request: SRequest, region: SRegion, path: str, anchor: str, grid: int | None = None
) -> str:
    """The ModOp that adds the ported island's pool asset next to ``anchor``'s (a copy of it: the island it came
    from, or the donor), else next to the first of the target region's whose island has the ported one's ``grid``
    (the asset's slot size must hold it); "" when the game has none. A region whose pool has no island of that grid
    is refused: the map generator would put it in a smaller island's place."""
    blocks = target_side.pool()
    pool_region = request.pool_region or region.island_region
    vanilla = a7m_edit117.random_island(blocks, anchor) or next(
        (
            block
            for block in _region_blocks(blocks, pool_region)
            if grid is None or _grid_of(target_side, block) == grid
        ),
        None,
    )
    if vanilla is None and grid is not None and _region_blocks(blocks, pool_region):
        raise ValueError(tr("islands.no_pool_size_slot", region=pool_region, size=grid))
    if vanilla is None:
        return ""
    guids = a7m_edit117.CIslandGuids()
    guid = guids.get(path)
    guids.save()  # a stable numbering keeps the number only when saved
    asset = re.sub(r"<GUID>\d+</GUID>", f"<GUID>{guid}</GUID>", vanilla, count=1)
    asset = re.sub(r"<Name>[^<]*</Name>", f"<Name>{Path(path).stem}</Name>", asset, count=1)
    asset = re.sub(r"<FilePath>[^<]*</FilePath>", f"<FilePath>{path}</FilePath>", asset, count=1)
    asset = re.sub(r"<IslandRegion>[^<]*</IslandRegion>", f"<IslandRegion>{pool_region}</IslandRegion>", asset, count=1)
    sub_region_pattern = r"<IslandSubRegion>[^<]*</IslandSubRegion>"
    if not region.sub_region:  # a region without sub-regions: the anchor's (Cape Trelawney's) must not stay
        asset = re.sub(sub_region_pattern, "", asset, count=1)
    else:
        tag = f"<IslandSubRegion>{region.sub_region}</IslandSubRegion>"
        if "<IslandSubRegion>" in asset:
            asset = re.sub(sub_region_pattern, tag, asset, count=1)
        elif region.sub_region != "PrimaryRegion":
            asset = asset.replace("</IslandRegion>", "</IslandRegion>" + tag, 1)
    anchor_asset = re.search(r"<GUID>(\d+)</GUID>", vanilla)[1]
    return f'<ModOp Type="addNextSibling" GUID="{anchor_asset}">\n{asset}\n</ModOp>'


def _region_blocks(blocks: list[str], pool_region: str) -> list[str]:
    """The pool assets that belong to the pool region."""
    return [block for block in blocks if f"<IslandRegion>{pool_region}</IslandRegion>" in block]


def _grid_of(target_side: CGameSide, block: str) -> int | None:
    """The grid (tiles across) of the island a pool asset names, None where its ``.a7minfo`` does not read."""
    found = re.search(r"<FilePath>([^<|]+)", block)
    try:
        return gametransfer.grid_size(filedb.read(target_side.read(found[1][:-4] + ".a7minfo"))) if found else None
    except (KeyError, OSError, ValueError, AttributeError):  # no file, no FileDB, no MapSize
        return None


def write_mod(out: Path, mod_id: str, mod_name: str, game: str, done: list[SPorted], trash=None) -> list[str]:
    """Write the ported islands of one target game into the mod folder ``out``, afresh: the islands, their side files
    and ``_gamedata``, the pool's ModOps and ``modinfo.json``. The mod is written beside ``out`` and swapped in
    (``edit117.staged_mod``: ``out`` must hold this mod or nothing; the old copy to ``trash``, the recycle bin by
    default). Returns the islands written."""
    with a7m_edit117.staged_mod(out, mod_id, trash or modswap.recycle) as stage:
        _write_ported(stage, mod_id, mod_name, game, done)
    return [item.target for item in done]


def _write_ported(out: Path, mod_id: str, mod_name: str, game: str, done: list[SPorted]) -> None:
    for item in done:
        if item.folder is not None:
            shutil.copytree(item.folder, out, dirs_exist_ok=True)
            shutil.rmtree(item.folder, ignore_errors=True)
        for path, data in {item.target: item.a7m, item.target[:-4] + ".a7minfo": item.info, **item.side}.items():
            (out / path).parent.mkdir(parents=True, exist_ok=True)
            (out / path).write_bytes(data)
        a7m_edit117.write_gamedata(out, item.target, item.gamedata)
    ops = [item.asset for item in done if item.asset]
    if ops:
        assets = out / ASSET_GAMES[game].assets
        assets.parent.mkdir(parents=True, exist_ok=True)
        built = assets.read_text(encoding="utf-8").rsplit("</ModOps>", 1)[0] if assets.is_file() else "<ModOps>\n"
        assets.write_text(built + "\n".join(ops) + "\n</ModOps>\n", encoding="utf-8", newline="\n")
    (out / "modinfo.json").write_text(_modinfo(out, mod_id, mod_name, game), encoding="utf-8", newline="\n")
    if any(item.report.get("dropped") for item in done):
        (out / PORT_REPORT).write_text(port_report(done), encoding="utf-8", newline="\n")


def _modinfo(out: Path, mod_id: str, mod_name: str, game: str = "anno117") -> str:
    """The port mod's ``modinfo.json``; a build's folder copied in keeps its scripts and its game setup (a 2070 island
    build's random-map module, a new game needed). 1800's loader knows none of 117's game fields (``Anno``,
    ``Difficulty``, ``GameSetup``), so an 1800 mod's file has only what it reads (engine-modloader "Anno 1800's loader")."""
    built = json.loads((out / "modinfo.json").read_text(encoding="utf-8")) if (out / "modinfo.json").is_file() else {}
    setup = built.get("GameSetup", {})
    description = "Islands ported to another region or game in the island editor (local use only)."
    text = modinfo(
        mod_id,
        "Map",
        mod_name,
        description,
        version="0.1.0",
        safe_to_remove=setup.get("SafeToRemove", True),
        requires_new_game=setup.get("RequiresNewGame", False),
        scripts=built.get("Scripts"),
    )
    if game != "anno1800":
        return text
    fields = json.loads(text)
    for key in ("Anno", "Difficulty", "GameSetup", "Scripts"):
        fields.pop(key, None)
    return json.dumps(fields, indent=2, ensure_ascii=False) + chr(10)


def summary(done: SPorted) -> str:
    """What a port did: one line, then what it did not carry as it was, by name (``portdrops``; the log shows the
    first of each kind, ``port-report.txt`` of the mod all)."""
    report = done.report
    parts = [f"{done.request.source} -> {done.target}"]
    fates = portdrops.totals(report.get("dropped", []))
    if report.get("donor"):
        parts.append(f"donor {report['donor']}")
    for key in ("objects changed", "objects removed", "objects kept", "objects copied", "objects left out"):
        if report.get(key):
            parts.append(f"{key} {report[key]}")
    for key in ("prop files", "props removed"):
        if report.get(key):
            parts.append(f"{key} {report[key]}")
    if report.get("slots unlinked"):
        left = ", ".join(f"{slot['name'] or slot['guid']} x{slot['count']}" for slot in report["slots unlinked"])
        parts.append(tr("islands.slots_without_link", slots=left))
    if report.get("ground"):
        parts.append(f"ground maps {report['ground'].get('maps', 0)}")
    if "maps" in report:
        parts.append(
            tr("islands.placed_on_maps", n=len(report["maps"])) if report["maps"] else tr("islands.no_map_places")
        )
    if fates:
        parts.append(", ".join(f"{what} {count}" for what, count in fates.items()))
    return "\n  ".join([", ".join(parts), *portdrops.lines(report.get("dropped", []))])


def port_report(done: list[SPorted]) -> str:
    """Every island's whole drop list as text (the mod's ``port-report.txt``)."""
    out = []
    for item in done:
        out += [f"{item.request.source} -> {item.target}"]
        out += [f"  {x}" for x in portdrops.lines(item.report.get("dropped", []), None)] + [""]
    return "\n".join(out)
