"""Anno 1800 editing of the island editor: an Anno 1800 island with hand edits, written into a mod that replaces the island.

``apply`` opens an island of the install (``editor.formats.a7m.reader.IslandFile1800``), puts the edit file's ground
edits (``groundedit``), its object, prop and copy edits (``islandedits``, the code that edits 117's islands) on it
and writes it in the layout it was read in; ``build`` does that for every island with an edit file and writes the
mod folder ``<out>/data/<island path>``, the changed ``_gamedata`` files and its ``modinfo.json``. Format and plan:
island-editor "Other games".
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from ....formats import a7mread
from ....infra.i18n import tr
from ....islands import islandedits, islandinfo, surfmap
from ....modloader import modswap
from ....suite import modcheck
from ... import groundedit, objectclones, templatedonors
from ...assetmodels import ASSETS_1800, AssetModels
from .edit117 import Edited, clear, ground, staged_mod, write_files, write_gamedata, write_info
from .reader import DEFAULT_MATERIAL_SET, IslandFile1800

EDIT_PREFIX = "1800_"  # edit files of 1800 islands: island-edits/1800_<island>.json
ASSETS = "data/config/export/main/asset/assets.xml"


def edit_name(source: str) -> str:
    """The edit file's name for an 1800 island (its game path)."""
    return f"{EDIT_PREFIX}{Path(source).stem}.json"


def apply(files, source: str, edits: dict, assets_xml: bytes | None = None, others: dict | None = None) -> Edited:
    """Open ``source`` (a game path) and apply ``edits``: the ground first, the clearing of a blank island
    (``"clear"``), then objects (the natural presets and props too), props and copies. ``assets_xml``: 1800's
    assets, to copy an asset the island does not carry; ``others``: the other games' files, for their materials."""
    isl = IslandFile1800(files.read(source), files.read(source[:-4] + ".a7minfo"))
    isl.object_groups = a7mread.OBJECT_GROUPS
    changed, ground_report, extra = ground(files, source, isl, edits, DEFAULT_MATERIAL_SET, source, others)
    samples = objectclones.samples(
        isl, a7mread.OBJECT_GROUPS
    )  # before clearing: a blank island's copies are of what it carried
    cleared = clear(isl) if edits.get("clear") else {}
    templates = donors = None
    if assets_xml is not None and any(objectclones.is_copy(a) for a in edits.get("added_objects", [])):
        models = AssetModels(assets_xml, game=ASSETS_1800)
        templates = {g: info.template for g in samples if (info := models.asset(g)) is not None}
        if any(a.get("plain") for a in edits["added_objects"]):
            donors = templatedonors.for_game("anno1800", files, models, source)
    rest, added, missing = objectclones.place_copies(edits, samples, templates, ASSETS_1800.library, donors)
    report = islandedits.apply(isl, rest)
    if cleared:
        report["cleared"] = cleared
    report["objects added"] += added
    report["unmatched"] += missing + [f"cfg:{a['cfg']}" for a in rest.get("added_objects", []) if "guid" not in a]
    report["unmatched"] += groundedit.unmatched(ground_report)
    info = islandinfo.apply(isl, edits)
    report |= surfmap.apply(isl, edits.get("surf"))
    return Edited(isl.write(), changed, {**report, **ground_report, **info}, isl.write_info() if info else None, extra)


def _modinfo(mod_id: str, name: str) -> str:
    info = {
        "ModID": mod_id,
        "Version": "0.1",
        "Category": {"English": "Map"},
        "ModName": {"English": name},
        "Description": {"English": "Anno 1800 islands edited in the island editor (local use only)."},
        "CreatorName": "anno-toolbox",
    }
    return json.dumps(info, indent=2) + "\n"


def build(
    files,
    edits_dir: Path,
    out: Path,
    mod_id: str,
    mod_name: str,
    log: Callable[[str], None] = print,
    others: dict | None = None,
    trash: modswap.Trash = modswap.recycle,
) -> list[str]:
    """Write every 1800 island with an edit file into the mod ``out``, in place of the original (the mod loader
    lays a mod's ``data`` folder over the game's); ``others``: the other games' files, for their materials in a
    palette. Returns the islands written (game paths). The mod is written beside ``out`` and swapped in
    (``edit117.staged_mod``; the old copy to ``trash``): a build that fails leaves the last one as it was."""
    written, assets_xml = [], None
    with staged_mod(out, mod_id, trash, log) as stage:
        for path in sorted(edits_dir.glob(f"{EDIT_PREFIX}*.json")):
            edits = islandedits.load(path)
            source = edits.get("source")
            if not source:
                log(tr("islands.no_source_island_skipped", name=path.name))
                continue
            if assets_xml is None and any(a.get("guid") for a in edits.get("added_objects", [])):
                try:
                    assets_xml = files.read(ASSETS)
                except (KeyError, FileNotFoundError):
                    assets_xml = b""
            done = apply(files, source, edits, assets_xml or None, others)
            (stage / source).parent.mkdir(parents=True, exist_ok=True)
            (stage / source).write_bytes(done.a7m)
            write_info(stage, source, done)
            write_gamedata(stage, source, done.gamedata)
            write_files(stage, done.files)
            written.append(source)
            log(tr("islands.source_report", source=source, report=done.report))
        (stage / "modinfo.json").write_text(_modinfo(mod_id, mod_name), encoding="utf-8", newline="\n")
    for problem in modcheck.check(out, modcheck.readable(files)):
        log(tr("islands.mod_check_problem", problem=problem))
    return written
