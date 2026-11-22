"""Anno 117 editing of the island editor: a vanilla Anno 117 island with hand edits, written into a mod either in place
of the original (same path: every game that rolls it gets the edited one) or as a new island next to it in the pool.

``CIslandFile`` opens a 117 ``.a7m`` and offers what ``islandedits`` works on, so objects, props, editor-only
objects and forest edit as on a built island; heights, paint, rivers and lakes go into the island and its
render data through ``groundedit``. ``CVanillaEditor`` opens one in the editor; the edit file names the island
(``source``) and how it is saved (``target``: ``replace`` or ``new``). Plan: island-editor "Islands and modes".
"""

from __future__ import annotations

import re
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ....formats import a7mread, filedb
from ....formats.rda import write_v22
from ....infra.i18n import tr
from ....islands import donor117, editorassets, islandedits, islandinfo, materialorder, surfmap, terrain2070
from ....islands.islandgrid import read_bits
from ....mod.modxml import modinfo
from ....modloader import modswap
from ....suite import features, modcheck
from ....suite.islandguids import IIslandNumbering
from ... import groundedit, islandmeta, objectclones, othermaterials, templatedonors
from ...assetmodels import ASSETS_117, CAssetModels
from ...islandeditor import CIslandEditor

EDIT_PREFIX = "117_"  # edit files of 117 islands: island-edits/117_<island>.json
NEW_DIR = "data/anno2070/islands117"  # where an edited island saved as a new one goes


ISLAND_GUIDS = (
    2_070_280_000,
    2_070_289_999,
)  # the GUIDs of islands saved as new ones, when no extension hands them out


class CIslandGuids:
    """The GUID of an island saved as a new one. An extension at ``features.GUIDS`` can hand out stable ones (the same
    on every run and machine); without, it is a hash of the island's path in ``ISLAND_GUIDS``, moved on when taken."""

    def __init__(self) -> None:
        make = next(iter(features.extensions(features.GUIDS).values()), None)
        self.m_stable = make() if make else None
        self.m_taken: set[int] = set()

    def get(self, source: str) -> int:
        if self.m_stable is not None:
            return self.m_stable.get(source)
        first, last = ISLAND_GUIDS
        guid = first + zlib.crc32(source.encode("utf-8")) % (last - first + 1)
        while guid in self.m_taken:
            guid = first if guid == last else guid + 1
        self.m_taken.add(guid)
        return guid

    def name_assets(self, xml: str) -> str:
        return self.m_stable.name_assets(xml) if self.m_stable is not None else xml

    def save(self) -> None:
        if self.m_stable is not None:
            self.m_stable.save()


class CIslandFile:
    """A 117 island opened for editing: ``gd`` / ``rd`` / ``info`` as FileDB trees."""

    def __init__(self, a7m: bytes, a7minfo: bytes) -> None:
        gamedata, rd3d = a7mread.members(a7m, "gamedata.data", "rd3d.data")
        self.name_ids_gamedata, self.name_ids_rd3d, self.name_ids_a7minfo = (
            filedb.name_ids(data) for data in (gamedata, rd3d, a7minfo)
        )
        self.gamedata, self.rd3d, self.a7minfo = (filedb.read(data) for data in (gamedata, rd3d, a7minfo))
        self.a7minfo_version = filedb.version(a7minfo)
        self.game_session_manager = self.gamedata.path("GameSessionManager")
        self.size = self.game_session_manager.path("WorldManager/Water").find("x").u32()
        self._areas = None

    # what islandedits works on (as the island builder offers it)
    def _areas_data(self):
        if self._areas is None:
            self._areas = donor117.read_areas(self.game_session_manager)
        return self._areas

    def main_objects(self, group: str) -> filedb.STag | None:
        areas = self._areas_data()
        return a7mread.area_objects(areas[min(1, len(areas) - 1)][1], group)

    def land_mask(self) -> list[list[int]]:
        return read_bits(self.game_session_manager.path("WorldManager/Water"))

    def write_info(self) -> bytes:
        """The island's ``.a7minfo`` as it is now, in the layout it was read in."""
        return filedb.write(self.a7minfo, *self.name_ids_a7minfo, version=self.a7minfo_version)

    def write(self) -> bytes:
        """The edited island as ``.a7m`` bytes."""
        for area, document, ids in self._areas_data():
            area.find("Data").data = filedb.write(document, *ids)
            byte_count = area.find("ByteCount")
            if byte_count is not None:
                byte_count.data = struct.pack("<I", len(area.find("Data").data))
        return write_v22(
            {
                "gamedata.data": filedb.write(self.gamedata, *self.name_ids_gamedata),
                "rd3d.data": filedb.write(self.rd3d, *self.name_ids_rd3d),
            }
        )


GAME_OBJECT_GROUPS = ("GameObject", "EditorObject")  # the groups an island keeps its objects by GUID in
CLEARED = ("GameObject", "NaturePreset", "Prop")  # what a blank island starts without (``clear``)


def clear(island_file: CIslandFile) -> dict:
    """A blank island from a vanilla one (117 or 1800): every object of ``CLEARED`` and every prop taken off, the
    terrain, the water and the editor objects (coast lines, rivers, markers) kept. Returns what was removed."""
    removed = {}
    for _area, document, _ids in island_file._areas_data():
        for group in CLEARED:
            objs = a7mread.area_objects(document, group)
            if objs is not None:
                gone = [child for child in objs.children if isinstance(child, filedb.STag)]
                objs.children = [child for child in objs.children if not isinstance(child, filedb.STag)]
                removed[group] = removed.get(group, 0) + len(gone)
    props = island_file.rd3d.path("PropGrid/Instances")
    if props is not None:
        removed["props"] = len(props.children)
        props.children = []
    return removed


class CVanillaEditor(othermaterials.COtherMaterials, CIslandEditor):
    """A vanilla 117 island in the editor: its own rivers and lakes are what the water tool edits, and the paint
    palette is the island's own material set (a material of another set would need a set of the island's own). A
    blank island (``edit_meta`` ``clear``) shows without what the build takes off it: every object but the
    editor-only ones, every prop."""

    def island(self) -> dict:
        out = super().island()
        if self.m_edit_meta.get("clear"):
            out["objects"] = [island_object for island_object in out["objects"] if island_object.get("marker")]
            out["props"] = []
        return out

    def water_network(self) -> dict | None:
        return groundedit.water_network(self.m_view.splines())


@dataclass
class SEdited:
    """An island with its edits applied: the ``.a7m``, the ``_gamedata`` files that changed (paths inside the
    island's ``_gamedata/<name>/`` folder) and what was done."""

    a7m: bytes
    gamedata: dict[str, bytes] = field(default_factory=dict)
    report: dict = field(default_factory=dict)
    info: bytes | None = None  # the island's ``.a7minfo`` when an edit changed it (``islandinfo``)
    files: dict[str, bytes] = field(default_factory=dict)  # files at game paths the island names (its own material set)


def edit_name(source: str) -> str:
    """The edit file's name for a 117 island (its game path)."""
    return f"{EDIT_PREFIX}{Path(source).stem}.json"


def new_name(source: str) -> str:
    return f"{Path(source).stem}_edit"


def ground(
    files,
    source: str,
    island_file: CIslandFile,
    edits: dict,
    default_set: str = "",
    target: str | None = None,
    others: dict | None = None,
) -> tuple[dict[str, bytes], dict, dict[str, bytes]]:
    """The ground edits (``groundedit``) on an opened island: the changed ``_gamedata`` files, the report and the files
    at game paths the island now names. The materials the palette added (``edits["materials"]``) need a set of the
    island's own: the island's set with them (``terrain2070.compose_set``) beside the island written at ``target``,
    named in the island's ``MaterialSetFileName``; no other island's set is touched (island-editor "An own material set").
    A material of the other game of the generation is brought in through its files in ``others`` (``othermaterials``)."""
    if not any(
        edits.get(k)
        for k in ("heights", "paint", "tint", "rivers", "lakes", "coast", "lake_outlines", "ford", "environment")
    ):
        return {}, {}, {}
    name = island_file.rd3d.path("Terrain").find("MaterialSetFileName")
    raw_name = name.data.decode("utf-16-le") if name is not None else ""
    set_path = raw_name.replace("\\", "/") or default_set
    try:
        text = files.read(set_path).decode("utf-8", "ignore") if set_path else ""
    except (KeyError, FileNotFoundError):
        text = ""
    own_files: dict[str, bytes] = {}
    missing: list[str] = []
    if edits.get("materials"):
        game = "anno1800" if default_set else "anno117"  # only 1800's build names a default set
        palette, files, own_files, missing = othermaterials.bring_in_all(files, edits["materials"], game, others)
        edits = {**edits, "materials": palette}
    if edits.get("materials") and text and name is not None and target:
        own_path = own_set_path(target)
        text = terrain2070.compose_set(text, files, edits["materials"])
        name.data = own_path.replace("/", "\\" if "\\" in raw_name else "/").encode("utf-16-le")
        own_files[own_path] = text.encode("utf-8")
    else:  # no own set is written: the brought-in files would be named by nothing
        own_files = {}
    colours = paint_colours(files, text, edits) if edits.get("paint") else {}
    changed, report = groundedit.apply(
        island_file, groundedit.gamedata(files, source), edits, materialorder.set_ranks(text), colours
    )
    if missing:
        report["unmatched"] = report.get("unmatched", []) + missing
    set_named = [path for path in own_files if path.endswith("_materialset.cfg")]
    return changed, report | ({"material set": set_named[0]} if set_named else {}), own_files


def own_set_path(target: str) -> str:
    """Where an island written at the game path ``target`` keeps its own material set: beside it."""
    return f"{target.rsplit('/', 1)[0]}/{Path(target).stem}_materialset.cfg"


def paint_colours(files, set_text: str, edits: dict) -> dict[int, tuple]:
    """The colour (rgb, the mean of the diffuse map) of each material the edit file paints: the island's set entry, or
    the file of a material the palette added; a material with no readable diffuse map is left out."""
    names = materialorder.set_files(set_text) | {
        int(k): material["file"] for k, material in edits.get("materials", {}).items()
    }
    out = {}
    for material_id in {
        int(stroke["material"]) for stroke in edits["paint"] if isinstance(stroke.get("material"), int)
    } & names.keys():
        try:
            colour = terrain2070.material_colour(files, names[material_id])
        except (KeyError, OSError, ValueError):
            colour = None
        if colour is not None:
            out[material_id] = colour
    return out


def _donors(files, source: str, edits: dict) -> templatedonors.CTemplateDonors | None:
    """Where a building placed from the asset list finds its template's blocks when the island has none of its kind:
    the game's other islands; none without the game's assets."""
    if not any(
        objectclones.is_copy(added_object) and added_object.get("plain")
        for added_object in edits.get("added_objects", [])
    ):
        return None
    try:
        return templatedonors.for_game("anno117", files, CAssetModels.read(files.read, ASSETS_117), source)
    except (KeyError, OSError):
        return None


def apply(
    files, source: str, edits: dict, template: filedb.STag, samples=None, target: str | None = None, others=None
) -> SEdited:
    """Open ``source`` (a game path) and apply ``edits``: ground first (the land decides where trees grow), then
    objects, props and forest. ``target``: where the island is written (default ``source``), for the files it names;
    ``others``: the other games' files by key, for their materials in the palette (``ground``)."""
    island_file = CIslandFile(files.read(source), files.read(source[:-4] + ".a7minfo"))
    changed, ground_report, extra = ground(files, source, island_file, edits, target=target or source, others=others)
    found = objectclones.samples(
        island_file, GAME_OBJECT_GROUPS
    )  # before clearing: a blank island's copies are of what it carried
    cleared = clear(island_file) if edits.get("clear") else {}  # a blank island (blankisland)
    rest, copied, missing = objectclones.place_copies(edits, found, donors=_donors(files, source, edits))
    report = islandedits.apply(island_file, rest, template, samples)
    report["objects added"] += copied
    report["unmatched"] += missing
    if cleared:
        report["cleared"] = cleared
    report["forest"] = islandedits.apply_forest(island_file, edits)
    report["unmatched"] += groundedit.unmatched(ground_report)
    info_report = islandinfo.apply(island_file, edits)
    report |= surfmap.apply(island_file, edits.get("surf"))
    return SEdited(
        island_file.write(),
        changed,
        {**report, **ground_report, **info_report},
        island_file.write_info() if info_report or "slot list" in report else None,
        extra,
    )


def write_info(out: Path, target: str, done: SEdited) -> None:
    """The island's ``.a7minfo`` beside the island written at ``target`` (a game path) under ``out``, when an edit
    changed it (the game reads the loose file over its own)."""
    if done.info is not None:
        path = (out / target).with_suffix(".a7minfo")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(done.info)


def write_files(out: Path, files: dict[str, bytes]) -> None:
    """Files at game paths (``SEdited.files``) under ``out``."""
    for path, data in files.items():
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_bytes(data)


def write_gamedata(out: Path, target: str, files: dict[str, bytes]) -> None:
    """The changed ``_gamedata`` files next to the island written at ``target`` (a game path) under ``out``."""
    folder = out / target.rsplit("/", 1)[0] / "_gamedata" / Path(target).stem
    for relative_path, data in files.items():
        (folder / relative_path).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative_path).write_bytes(data)


def pool_assets(assets_xml: str) -> list[str]:
    """Every ``RandomIsland`` asset of a game's assets (117's, 1800's), as XML, in file order."""
    return re.findall(r"<Asset>(?:(?!</Asset>).)*?<Template>RandomIsland</Template>.*?</Asset>", assets_xml, re.S)


def random_island(assets_xml: str | list[str], source: str) -> str | None:
    """The vanilla ``RandomIsland`` asset that puts ``source`` in the pool, as XML (``assets_xml``: the assets' text,
    or ``pool_assets`` of it)."""
    blocks = pool_assets(assets_xml) if isinstance(assets_xml, str) else assets_xml
    return next((block for block in blocks if f"<FilePath>{source}</FilePath>" in block), None)


@dataclass
class _CBuild:
    """What ``build`` carries from one island to the next: the sources, the mod being written and the ops so far."""

    m_files: object
    m_out: Path
    m_log: Callable[[str], None]
    m_islands: IIslandNumbering
    m_assets_xml: str
    m_ops: list[str] = field(default_factory=list)
    m_written: list[str] = field(default_factory=list)
    m_template: filedb.STag | None = None
    m_samples: object = None

    def sources_for(self, edits: dict) -> None:
        """Load the donors an island's added objects need, once: the editor's own assets for a placed GUID, a vanilla
        decoration's template for a placed model."""
        added = edits.get("added_objects", [])
        if self.m_samples is None and any(
            "guid" in added_object and not objectclones.is_copy(added_object) for added_object in added
        ):
            self.m_samples = editorassets.samples(self.m_files, editorassets.load(self.m_files)[1])
        if self.m_template is None and any("guid" not in added_object for added_object in added):
            self.m_template = donor117.load_template(self.m_files)  # a placed model is a copy of a vanilla decoration

    def new_island(self, edits: dict, source: str) -> str:
        """Copy the island's side files into a folder of its own and register it in the pool; returns its game path."""
        name = edits.get("name") or new_name(source)  # a blank island has a name of its own
        folder = f"{NEW_DIR}/{name}/"
        target = f"{folder}{name}.a7m"
        (self.m_out / folder).mkdir(parents=True, exist_ok=True)
        for extension in (".a7minfo", ".a7me"):
            (self.m_out / folder / f"{name}{extension}").write_bytes(self.m_files.read(source[:-4] + extension))
        gamedata_folder = source.rsplit("/", 1)[0] + f"/_gamedata/{Path(source).stem}/"
        for path in self.m_files.list(gamedata_folder):
            target_file = self.m_out / folder / "_gamedata" / name / path[len(gamedata_folder) :]
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(self.m_files.read(path))
        self.register(edits, source, target)
        return target

    def register(self, edits: dict, source: str, target: str) -> None:
        """The ``RandomIsland`` asset that puts the new island ``target`` in the pool, next to the original's."""
        vanilla = random_island(self.m_assets_xml, source)
        if vanilla is None:
            self.m_log(tr("islands.no_randomisland_asset_found_written", source=source))
            return
        guid = self.m_islands.get(target if edits.get("name") else source)
        asset = re.sub(r"<GUID>\d+</GUID>", f"<GUID>{guid}</GUID>", vanilla, count=1)
        label = edits.get("name") or r"\1 (edited)"
        asset = re.sub(r"<Name>([^<]*)</Name>", rf"<Name>{label}</Name>", asset, count=1)
        asset = asset.replace(f"<FilePath>{source}</FilePath>", f"<FilePath>{target}</FilePath>")
        asset = islandmeta.apply(asset, edits.get("registration", {}))
        anchor = re.search(r"<GUID>(\d+)</GUID>", vanilla)[1]
        self.m_ops.append(f'<ModOp Type="addNextSibling" GUID="{anchor}">\n{asset}\n</ModOp>')

    def write_island(self, target: str, done: SEdited) -> None:
        (self.m_out / target).parent.mkdir(parents=True, exist_ok=True)
        (self.m_out / target).write_bytes(done.a7m)
        write_info(self.m_out, target, done)
        write_gamedata(self.m_out, target, done.gamedata)
        write_files(self.m_out, done.files)
        self.m_written.append(target)

    def write_pool(self, mod_id: str, mod_name: str) -> None:
        """The pool's ``assets.xml``, the island numbers and ``modinfo.json``."""
        self.m_ops.append("</ModOps>")
        assets = self.m_out / "data/base/config/export/assets.xml"
        assets.parent.mkdir(parents=True, exist_ok=True)
        text = self.m_islands.name_assets("\n".join(self.m_ops) + "\n")
        assets.write_text(text, encoding="utf-8", newline="\n")
        self.m_islands.save()
        description = "Anno 117 islands edited in the island editor (local use only)."
        modinfo_text = modinfo(mod_id, "Map", mod_name, description, version="0.1")
        (self.m_out / "modinfo.json").write_text(modinfo_text, encoding="utf-8", newline="\n")


def staged_mod(out: Path, mod_id: str, trash: modswap.Trash = modswap.recycle, log=None):
    """The mod folder ``out`` written afresh (``modswap.staged``: beside it, swapped in when done, the old copy to
    ``trash``); only ever an absolute path ending in the mod's id that holds that mod or nothing."""
    if not out.is_absolute() or out.name != mod_id or mod_id in (".", ".."):
        raise ValueError(
            tr(
                "islands.refusing_rebuild_not_absolute_path_lower",
                out=out,
                mod_id=repr(mod_id),
            )
        )
    return modswap.staged(out, mod_id, trash, log)


def build(
    files,
    edits_dir: Path,
    out: Path,
    mod_id: str,
    mod_name: str,
    log: Callable[[str], None] = print,
    guids: IIslandNumbering | None = None,
    others: dict | None = None,
    trash: modswap.Trash = modswap.recycle,
    wanted: Callable[[dict], bool] | None = None,
) -> list[str]:
    """Write every 117 island with an edit file into the mod ``out``: replaced in place, or as a new
    island registered next to the original (its GUID from ``guids``, else the editor's own scheme); ``others``: the
    other games' files by key, for their materials in a palette; ``wanted``: which edit files count (default all).
    Returns the islands written (game paths). The mod is written beside ``out`` and swapped in (``staged_mod``; the
    old copy to ``trash``)."""
    with staged_mod(out, mod_id, trash, log) as stage:
        assets_xml = files.read("data/base/config/export/assets.xml").decode("utf-8", "ignore")
        ops = ["<ModOps>", "<!-- generated by anno-toolbox (formats.a7m.edit117); local use only -->"]
        run = _CBuild(files, stage, log, guids or CIslandGuids(), assets_xml, ops)
        for path in sorted(edits_dir.glob(f"{EDIT_PREFIX}*.json")):
            edits = islandedits.load(path)
            source = edits.get("source")
            if not source:
                log(tr("islands.no_source_island_skipped", name=path.name))
                continue
            if wanted is not None and not wanted(edits):
                continue
            run.sources_for(edits)
            target = run.new_island(edits, source) if edits.get("target", "replace") == "new" else source
            done = apply(files, source, edits, run.m_template, run.m_samples, target, others)
            run.write_island(target, done)
            log(tr("islands.source_target_report", source=source, target=target, report=done.report))
        run.write_pool(mod_id, mod_name)
    for problem in modcheck.check(out, modcheck.readable(files)):
        log(tr("islands.mod_check_problem", problem=problem))
    return run.m_written
