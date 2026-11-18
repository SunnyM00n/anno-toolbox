"""The files an edited map becomes: the template over the vanilla one or as a new map with its ``MapTemplate`` asset
(117, 1800), its three difficulties, the world or random-map template at its own path (2070, 1404, 1701), and the
growth's report beside them; the GUIDs a new map may take.

``map_files(doc, choice)`` returns game path -> bytes for the project or a mod folder; ``variant_files`` the three
difficulties; ``write_mod(folder, ...)`` writes a stand-alone mod. docs/map-editor.md "Writing it" and "Three
difficulties from one map".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from ..infra.i18n import tr
from ..mod import modxml, xmlbuild
from ..suite import islandguids
from . import grow, pool, rules, template, variants
from .mapdoc import MapDoc

NEW_FOLDER = "data/mods/{mod}/maps/{name}/{name}"
COMMENT = "a map written by the map editor"
GUIDS = "map-guids.json"  # in the project: the GUID of each new map, kept so a build names it the same way
_NAME = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class Choice:
    """How a template is written: over the vanilla one, or as a new map ``name`` with the asset GUID ``guid``."""

    as_new: bool = False
    name: str = ""
    guid: int = 0
    mod_id: str = "my-map"


def _session_files(doc: MapDoc, target: str) -> dict[str, bytes]:
    """The map's ``.a7t`` and ``.a7te`` (grown, else the game's) under a new stem."""
    stem, grown = doc.path.rsplit(".", 1)[0], doc.grown.files if doc.grown else {}
    out = {}
    for suffix in (".a7t", ".a7te"):
        data = grown.get(suffix) or (doc.tree.read(stem + suffix) if doc.tree.exists(stem + suffix) else None)
        if data is not None:
            out[target + suffix] = data
    return out


def _check_new(choice: Choice) -> None:
    if not _NAME.match(choice.name) or not choice.guid:
        raise ValueError(tr("maps.new_map_needs_name_lower"))


def map_files(doc: MapDoc, choice: Choice | None = None) -> dict[str, bytes]:
    """Game path -> bytes of everything the edited map needs in a mod (or, for an older game, in its archives)."""
    choice = choice or Choice()
    if not doc.is_template:
        return {doc.path: doc.render()}
    stem = doc.path.rsplit(".", 1)[0]
    grown = doc.grown.files if doc.grown else {}
    ops = list(doc.grown.asset_ops) if doc.grown else []
    files: dict[str, bytes] = {}
    if choice.as_new:
        _check_new(choice)
        target = NEW_FOLDER.format(mod=choice.mod_id, name=choice.name)
        files.update(_session_files(doc, target))
        files[target + ".a7tinfo"] = doc.render()
        ops = [new_asset_op(doc, choice, target + ".a7t")]  # a new asset carries its horizon islands itself
    else:
        files[doc.path] = doc.render()
        for suffix in (".a7t", ".a7te"):
            if suffix in grown:
                files[stem + suffix] = grown[suffix]
    if ops:
        files[pool.ASSET_FILES[doc.game]] = modxml.modops(ops, COMMENT).encode("utf-8")
    return files


def variant_files(
    doc: MapDoc, choice: Choice, guids: dict[str, int] | None = None
) -> tuple[dict[str, bytes], dict[str, list[rules.Finding]]]:
    """The three difficulties of the open map (taken as the medium one): as three new maps ``<name>_<level>`` with
    ``guids`` per level, or over a vanilla ``_medium`` map and its two siblings. Returns the files and each level's
    findings."""
    if not doc.is_template:
        raise ValueError(tr("maps.difficulties_map_templates_117_1800"))
    if not choice.as_new and doc.grown is not None:
        raise ValueError(tr("maps.grown_maps_difficulties_written_new"))
    files: dict[str, bytes] = {}
    findings: dict[str, list[rules.Finding]] = {}
    ops = []
    base = doc.render()
    for level in variants.LEVELS:
        t = template.read(base)
        t.elements = variants.derive(t.elements, level.key, doc.maps.classes)
        findings[level.key] = rules.check(doc.facts(t.elements))
        if choice.as_new:
            own = replace(choice, name=f"{choice.name}_{level.key}", guid=(guids or {}).get(level.key, 0))
            _check_new(own)
            target = NEW_FOLDER.format(mod=own.mod_id, name=own.name)
            files.update(_session_files(doc, target))
            files[target + ".a7tinfo"] = t.render()
            ops.append(new_asset_op(doc, own, target + ".a7t", level.island_size))
            continue
        path = doc.path if level.key == "medium" else variants.sibling(doc.path, level.key)
        if not path or not doc.tree.exists(path):
            raise ValueError(tr("maps.no_vanilla_easy_hard_maps", path=doc.path))
        files[path] = t.render()
    if ops:
        files[pool.ASSET_FILES[doc.game]] = modxml.modops(ops, COMMENT).encode("utf-8")
    return files, findings


def new_asset_op(doc: MapDoc, choice: Choice, a7t: str, island_size: str = "") -> str:
    """The ModOp adding a ``MapTemplate`` asset for the new map: the vanilla one's copy (its horizon islands as the
    map has them: moved by a growth, the enlarged map's for an enlarged template) with the new GUID, name and file, no
    enlarged variant, ``island_size`` when given; after the vanilla asset."""
    asset = doc.maps.asset_of(doc.path)
    if asset is None:
        raise ValueError(tr("maps.no_maptemplate_asset_copy_new", path=doc.path))
    text = doc.grown.asset_text if doc.grown and doc.grown.asset_text else asset.text
    if doc.is_enlarged():
        own = grow.horizon_list(text, grow.ENLARGED_HORIZONS)
        renamed = own.replace(grow.ENLARGED_HORIZONS, grow.HORIZONS)
        text = text.replace(grow.horizon_list(text, grow.HORIZONS), renamed, 1) if own else text
    text = re.sub(r"<GUID>\d+</GUID>", f"<GUID>{choice.guid}</GUID>", text, count=1)
    text = re.sub(r"<Name>[^<]*</Name>", f"<Name>{choice.name}</Name>", text, count=1)
    text = re.sub(r"<TemplateFilename>[^<]*</TemplateFilename>", f"<TemplateFilename>{a7t}</TemplateFilename>", text)
    if island_size:
        text = re.sub(r"<IslandSize>[^<]*</IslandSize>", f"<IslandSize>{island_size}</IslandSize>", text, count=1)
    for name in ("EnlargedTemplateFilename", "EnlargeDLC", "EnlargedAdditionalDecoIslands"):
        text = re.sub(rf"\s*<{name}>[^<]*</{name}>", "", text)
    text = re.sub(
        r"\s*<(EnlargedHorizonIslands|EnlargedAdditionalPreplacedParticipants)>.*?</\1>", "", text, flags=re.S
    )
    return xmlbuild.add_after(asset.guid, text)


# the GUIDs of new maps
def taken_guids(project) -> set[int]:
    """The GUIDs the project's other work holds: its islands' numbers and the asset editor's new assets."""
    taken = set(islandguids.held(project.island_guids))
    try:
        data = json.loads((project.asset_edits / f"{project.game}.json").read_text(encoding="utf-8"))
        taken |= {int(g) for g in data.get("new", {})}
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return taken


def suggest_guid(project_range: tuple[int, int], taken: set[int], name: str, kept: dict[str, int]) -> int:
    """A new map's GUID: the one it had before, else the highest free number of the range (the islands and the asset
    editor take the lowest, so they do not meet)."""
    if name in kept:
        return kept[name]
    lo, hi = project_range
    used = taken | set(kept.values())
    return next((g for g in range(hi, lo - 1, -1) if g not in used), 0)


def guid_problem(guid: int, project_range: tuple[int, int], taken: set[int], kept: dict[str, int], name: str) -> str:
    """Why a new map may not take ``guid`` ("" when it may): outside the range, another asset's or another map's."""
    lo, hi = project_range
    if not lo <= guid <= hi:
        return tr("maps.guid_lies_outside_projects_range", guid=guid, lo=lo, hi=hi)
    if guid in taken:
        return tr("maps.guid_belongs_another_asset_project", guid=guid)
    other = next((n for n, g in kept.items() if g == guid and n != name), "")
    return tr("maps.guid_belongs_new_map", guid=guid, name=other) if other else ""


def read_guids(folder: Path) -> dict[str, int]:
    path = folder / GUIDS
    try:
        return {str(k): int(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError, AttributeError, TypeError):  # no file, or one that is no list of GUIDs
        return {}


def keep_guid(folder: Path, name: str, guid: int) -> None:
    kept = read_guids(folder)
    kept[name] = guid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / GUIDS).write_text(json.dumps(kept, indent=1, sort_keys=True), encoding="utf-8", newline="\n")


def write_mod(folder: Path, files: dict[str, bytes], mod_id: str, title: str, report: str = "") -> Path:
    """A stand-alone mod: ``modinfo.json`` and the files under ``folder/<mod_id>``, a growth's ``report`` beside the
    mod folder (``<mod_id>.grow.json``: not a file of the mod); returns the mod folder."""
    out = folder / mod_id
    if report:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{mod_id}.grow.json").write_text(report, encoding="utf-8", newline="\n")
    for path, data in files.items():
        dest = out / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    info = modxml.modinfo(mod_id, "Map", title, tr("maps.map_written_editor"), requires_new_game=True)
    (out / "modinfo.json").write_text(info, encoding="utf-8", newline="\n")
    return out
