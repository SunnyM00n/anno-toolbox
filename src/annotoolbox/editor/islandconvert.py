"""Island ports within an engine generation, as the Islands app and the command line run it: an island of 117 or
1800 made into an island of another region of its game (``regionconvert``) or of the other game (``gametransfer``),
written as a new island of a mod and registered in the target region's pool.

``targets`` lists what an island can become, ``GameSide`` is one installed game (files, asset names, what its regions'
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

from ..infra import buildcache
from ..infra.i18n import tr
from ..islands import regions, regiontables
from ..islands.regions import Region
from ..mod.modxml import modinfo
from ..modloader import modswap
from ..sources import gametree
from ..suite import features
from . import gametransfer, groundedit, portpictures, regionconvert
from .assetmodels import AssetModels
from .formats.a7m import edit117 as a7m_edit117
from .formats.a7m.reader import GameFiles, IslandFile1800
from .islandfiles import island_paths
from .regionconvert import ASSET_GAMES, DEFAULT_SETS, material_set_of

SIDE_FILES = (".a7me",)  # copied beside the ported island (the .a7minfo is written)
GAME_NAMES = {"anno117": "Anno 117", "anno1800": "Anno 1800"}


@dataclass(frozen=True)
class Request:
    """One island to convert: its game and game path, the target region (``<game>:<key>``), the new island's name
    (default ``<island>_<region>``) and the pool region it is registered in (default the target's)."""

    game: str
    source: str
    target: str
    name: str = ""
    pool_region: str = ""


@dataclass
class Ported:
    """A ported island: where it goes (a game path of the target game), its files and its pool asset."""

    request: Request
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
        found = [(r.id, label(r)) for g in regions.GENERATION for r in regions.regions(g) if r != own]
    for ext in features.extensions(features.ISLAND_CONVERSIONS).values():
        found += list(ext.targets(game, path))
    return found


def label(region: Region) -> str:
    return f"{GAME_NAMES[region.game]}: {tr(region.label)}"


class GameSide:
    """One installed game a port reads: its files (117: ``islandview.Files``; 1800: a tree or ``GameFiles``),
    its assets' names, what each region's pool islands place (kept in ``cache`` when given); ``root`` is its folder."""

    def __init__(self, key: str, files, cache: Path | None = None, root: Path | None = None) -> None:
        self.key = key
        self.root = root  # the install's folder, for a port that runs the game's own builder (an extension)
        self.files = GameFiles(files) if not hasattr(files, "read") else files
        self.cache = cache
        self._models: AssetModels | None = None
        self._pool: list[str] | None = None
        self._inventories: dict[str, regiontables.Inventory] = {}

    def read(self, path: str) -> bytes:
        return self.files.read(path)

    def text(self, path: str) -> str:
        """A text file of the game, "" when it has none."""
        try:
            return self.read(path).decode("utf-8", "ignore") if path else ""
        except (KeyError, OSError):
            return ""

    def asset_names(self, guid: int) -> tuple[str, str] | None:
        if self._models is None:
            self._models = AssetModels.read(self.read, ASSET_GAMES[self.key])
        info = self._models.asset(guid)
        return (info.template, info.name) if info is not None else None

    def pool(self) -> list[str]:
        """The game's ``RandomIsland`` assets."""
        if self._pool is None:
            self._pool = a7m_edit117.pool_assets(self.text(ASSET_GAMES[self.key].assets))
        return self._pool

    def island_paths(self, region: Region) -> list[str]:
        return [
            p for p in island_paths(self.key, self.files) if "/pool/" in p and regions.region_of(self.key, p) == region
        ]

    def files_stamp(self) -> str:
        """What changes when the game's files do: its tree's stamp, else its install folder's (``game_stamp``: the
        archives' names, sizes and times), "" for files that say neither (a test's stand-in)."""
        found = getattr(self.files, "stamp", "")
        if not found and self.root is not None and Path(self.root).is_dir():
            found = gametree.game_stamp(self.key, Path(self.root))
        return str(found)

    def inventory(self, region: Region) -> regiontables.Inventory:
        """What the region's pool islands place (read once, then from the cache)."""
        if region.id in self._inventories:
            return self._inventories[region.id]
        paths = self.island_paths(region)
        stamp = hashlib.sha1("\n".join([self.files_stamp(), *paths]).encode("utf-8")).hexdigest()[:16]
        file = self.cache / f"region-inventory-{region.game}-{region.key}.json" if self.cache else None
        match = {"stamp": stamp, "format": regiontables.INVENTORY_FORMAT}
        kept = buildcache.read_kept(file, match) if file is not None else None
        found = regiontables.Inventory.from_json(kept.get("inventory") or {}) if kept is not None else None
        if found is None:
            found = regiontables.inventory(self.read, paths)
            if file is not None:
                buildcache.write_kept(file, {**match, "inventory": found.to_json()})
        self._inventories[region.id] = found
        return found

    def open(self, path: str):
        """The island at ``path`` opened for editing (117's ``IslandFile``, 1800's ``IslandFile1800``)."""
        cls = IslandFile1800 if self.key == "anno1800" else a7m_edit117.IslandFile
        return cls(self.read(path), self.read(path[:-4] + ".a7minfo"))


def colour_reader(src: GameSide, dst: GameSide) -> Callable[[str, str], tuple | None]:
    """A material file's mean diffuse colour on either side (``regiontables.material_table``), None where it cannot be
    read (no Pillow, a map the decoders do not read)."""
    from ..islands.terrain2070 import material_colour

    seen: dict[tuple[str, str], tuple | None] = {}

    def colour(side: str, file: str) -> tuple | None:
        if (side, file) not in seen:
            try:
                seen[side, file] = material_colour((src if side == "src" else dst).files, file)
            except Exception:  # any reader's failure: the colour is not known, the other rules decide
                seen[side, file] = None
        return seen[side, file]

    return colour


def _quiet(_line: str) -> None:
    pass


def target_path(region: Region, name: str) -> str:
    return f"{region.folder}/{name}/{name}.a7m"


def new_name(request: Request, region: Region) -> str:
    stem = request.name or f"{Path(request.source).stem}_{region.key}"
    return re.sub(r"[^a-z0-9_]+", "_", stem.lower()).strip("_")


def convert(
    src: GameSide, dst: GameSide, request: Request, colours: bool = True, log: Callable[[str], None] = _quiet
) -> Ported:
    """Port one island; ``dst`` is ``src`` for a region of the same game; ``log`` is told each stage (it takes a minute).
    An island of another engine generation (2070, 1404, 1701) is ported only by an extension at
    ``features.ISLAND_CONVERSIONS``, older to newer."""
    if src.key not in regions.GENERATION:
        return _extension_for(src.key, request).run(request, src, dst, log)
    if not any(r.id == request.target for r in regions.REGIONS):
        raise ValueError(tr("unknown region {region}: island regions <game> lists them", region=request.target))
    target = regions.region(request.target)
    own = regions.region_of(src.key, request.source)
    if own is None:
        raise ValueError(tr("{path}: not an island of a region of {game}", path=request.source, game=src.key))
    if target.game != src.key:
        return _transferred(src, dst, request, target, colour_reader(src, dst) if colours else None, log)
    log(tr("reading {island}", island=request.source))
    isl = src.open(request.source)
    gd = groundedit.gamedata(src.files, request.source)
    set_text = src.text(material_set_of(isl, src.key))
    sides = (
        regiontables.Sources(own, set_text, src.inventory(own), src.asset_names),
        regiontables.Sources(
            target, src.text(target.material_set or DEFAULT_SETS[src.key]), src.inventory(target), src.asset_names
        ),
    )
    log(tr("making the tables from {region}'s islands", region=tr(target.label)))
    tables = regiontables.build(
        *sides, colour_reader(src, src) if colours and not regions.same_look(own, target) else None
    )
    path = target_path(target, new_name(request, target))
    if regions.same_look(own, target):  # the region itself, or Cape Trelawney and the Old World
        a7m, info, changed, report = src.read(request.source), src.read(request.source[:-4] + ".a7minfo"), {}, {}
    else:
        log(tr("changing the ground, props and objects"))
        changed, report = regionconvert.apply(isl, gd, tables)
        a7m, info = isl.write(), isl.write_info()
        if colours:
            log(tr("drawing the map pictures"))
            changed |= _redrawn(src, isl, gd, changed, sides[1].set_text)
    done = Ported(request, path, a7m, info, {rel: changed.get(rel) or gd.read(rel) for rel in gd.names}, report=report)
    _side_files(done, src, request.source)
    done.asset = registration(src, request, target, path, request.source)
    done.report["tables"] = _table_summary(tables)
    return done


def source_games() -> list[str]:
    """The games whose islands can be ported: the engine generation's, then those an extension takes (none in the
    public edition)."""
    found = list(regions.GENERATION)
    for ext in features.extensions(features.ISLAND_CONVERSIONS).values():
        found += [g for g in ext.sources if g not in found]
    return found


def _extension_for(game: str, request: Request):
    """The first extension at ``features.ISLAND_CONVERSIONS`` that offers the island its target."""
    for ext in features.extensions(features.ISLAND_CONVERSIONS).values():
        if any(t == request.target for t, _label in ext.targets(game, request.source)):
            return ext
    raise ValueError(
        tr("{path}: {game} has no port to {region}", path=request.source, game=game, region=request.target)
    )


def convert_all(
    src: GameSide, dst: GameSide, requests: list[Request], colours: bool = True, log: Callable[[str], None] = _quiet
) -> list[Ported]:
    """Port several islands of one game into one target game, in the order asked: an island of another engine
    generation goes with the others of its extension into one build (``run_many``), the rest one by one."""
    if src.key in regions.GENERATION:
        return [convert(src, dst, r, colours, log) for r in requests]
    groups: dict[int, tuple] = {}
    for r in requests:
        ext = _extension_for(src.key, r)
        groups.setdefault(id(ext), (ext, []))[1].append(r)
    done: dict[Request, Ported] = {}
    for ext, batch in groups.values():
        found = ext.run_many(batch, src, dst, log) if ext.run_many else [ext.run(r, src, dst, log) for r in batch]
        done.update(zip(batch, found, strict=True))
    return [done[r] for r in requests]


def _redrawn(
    side: GameSide, isl, gd: groundedit.Gamedata, changed: dict[str, bytes], set_text: str
) -> dict[str, bytes]:
    """The ported island's map pictures drawn from its new ground (``portpictures``)."""
    final = groundedit.Gamedata(gd.names, lambda n: changed[n] if n in changed else gd.read(n))
    colour = colour_reader(side, side)
    return portpictures.redraw(isl, final, set_text, lambda file: colour("dst", file), keep=True)


def _transferred(src: GameSide, dst: GameSide, request: Request, target: Region, colour, log) -> Ported:
    """An island carried into the other game of the generation (``gametransfer``), as a ``Ported``."""
    carried = gametransfer.transfer(src, dst, request, target, colour, log)
    path = target_path(target, new_name(request, target))
    done = Ported(request, path, carried.a7m, carried.info, carried.gamedata, report=carried.report)
    _side_files(done, dst, carried.donor)
    done.asset = registration(dst, request, target, path, carried.donor)
    done.report["tables"] = _table_summary(carried.tables)
    return done


def _side_files(done: Ported, side: GameSide, island: str) -> None:
    for ext in SIDE_FILES:
        try:
            done.side[done.target[:-4] + ext] = side.read(island[:-4] + ext)
        except (KeyError, OSError):
            continue


def _table_summary(tables: regiontables.Tables) -> dict:
    return {"materials": tables.materials, "props": len(tables.props), "objects": len(tables.objects)}


def registration(dst: GameSide, request: Request, region: Region, path: str, anchor: str) -> str:
    """The ModOp that adds the ported island's pool asset next to ``anchor``'s (a copy of it: the island it came
    from, or the donor), else next to the first of the target region's; "" when the game has none."""
    blocks = dst.pool()
    pool_region = request.pool_region or region.island_region
    vanilla = a7m_edit117.random_island(blocks, anchor) or next(
        (b for b in blocks if f"<IslandRegion>{pool_region}</IslandRegion>" in b), None
    )
    if vanilla is None:
        return ""
    guid = a7m_edit117.IslandGuids().get(path)
    asset = re.sub(r"<GUID>\d+</GUID>", f"<GUID>{guid}</GUID>", vanilla, count=1)
    asset = re.sub(r"<Name>[^<]*</Name>", f"<Name>{Path(path).stem}</Name>", asset, count=1)
    asset = re.sub(r"<FilePath>[^<]*</FilePath>", f"<FilePath>{path}</FilePath>", asset, count=1)
    asset = re.sub(r"<IslandRegion>[^<]*</IslandRegion>", f"<IslandRegion>{pool_region}</IslandRegion>", asset, count=1)
    sub = r"<IslandSubRegion>[^<]*</IslandSubRegion>"
    if not region.sub_region:  # a region without sub-regions: the anchor's (Cape Trelawney's) must not stay
        asset = re.sub(sub, "", asset, count=1)
    else:
        tag = f"<IslandSubRegion>{region.sub_region}</IslandSubRegion>"
        if "<IslandSubRegion>" in asset:
            asset = re.sub(sub, tag, asset, count=1)
        elif region.sub_region != "PrimaryRegion":
            asset = asset.replace("</IslandRegion>", "</IslandRegion>" + tag, 1)
    anchor_guid = re.search(r"<GUID>(\d+)</GUID>", vanilla)[1]
    return f'<ModOp Type="addNextSibling" GUID="{anchor_guid}">\n{asset}\n</ModOp>'


def write_mod(out: Path, mod_id: str, mod_name: str, game: str, done: list[Ported], trash=None) -> list[str]:
    """Write the ported islands of one target game into the mod folder ``out``, afresh: the islands, their side files
    and ``_gamedata``, the pool's ModOps and ``modinfo.json``. The mod is written beside ``out`` and swapped in
    (``edit117.staged_mod``: ``out`` must hold this mod or nothing; the old copy to ``trash``, the recycle bin by
    default). Returns the islands written."""
    with a7m_edit117.staged_mod(out, mod_id, trash or modswap.recycle) as stage:
        _write_ported(stage, mod_id, mod_name, game, done)
    return [item.target for item in done]


def _write_ported(out: Path, mod_id: str, mod_name: str, game: str, done: list[Ported]) -> None:
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
    (out / "modinfo.json").write_text(_modinfo(out, mod_id, mod_name), encoding="utf-8", newline="\n")


def _modinfo(out: Path, mod_id: str, mod_name: str) -> str:
    """The port mod's ``modinfo.json``; a build's folder copied in keeps its scripts and its game setup (a 2070 island
    build's random-map module, a new game needed)."""
    built = json.loads((out / "modinfo.json").read_text(encoding="utf-8")) if (out / "modinfo.json").is_file() else {}
    setup = built.get("GameSetup", {})
    description = "Islands ported to another region or game in the island editor (local use only)."
    return modinfo(
        mod_id,
        "Map",
        mod_name,
        description,
        version="0.1",
        safe_to_remove=setup.get("SafeToRemove", True),
        requires_new_game=setup.get("RequiresNewGame", False),
        scripts=built.get("Scripts"),
    )


def summary(done: Ported) -> str:
    """One line on what a port did."""
    r = done.report
    parts = [f"{done.request.source} -> {done.target}"]
    if r.get("donor"):
        parts.append(f"donor {r['donor']}")
    for key in ("objects changed", "objects removed", "objects kept", "objects copied", "objects left out"):
        if r.get(key):
            parts.append(f"{key} {r[key]}")
    for key in ("prop files", "props removed"):
        if r.get(key):
            parts.append(f"{key} {r[key]}")
    if r.get("ground"):
        parts.append(f"ground maps {r['ground'].get('maps', 0)}")
    return ", ".join(parts)
