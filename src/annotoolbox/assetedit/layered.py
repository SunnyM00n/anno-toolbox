"""The Assets app on top of an imported mod, layered (docs/mod-import.md, section 3.5 way B, "Phase 6 as built"). No Qt.

The mod's patch files stay carried and byte-identical. The app's base is the game with the mod's ModOps applied
(``assetgame.layered_game``); the person's edits are ModOps against that base, written to an include file beside the
mod's patch file with one ``<Include>`` appended to it. A project is layered or handed over (``handover``), not both.
``plan`` reports, ``take`` records the choice (``layered.json``), ``undo`` removes it, ``open_layered`` gives the base,
``render`` is the Assets part's build in a layered project.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..infra.i18n import number, tr
from ..modloader.mods import GAME_NUMBER, find_mods
from ..modloader.patchdoc import CPatchDoc
from ..suite import appfolders, carriedpart, importmeta
from ..suite import project as projects
from ..suite.buildout import CFileRef
from . import handover, modimport, modops
from .assetgame import CGame, layered_game, open_installed
from .changeset import CChangeSet
from .origins import SLayer
from .projectpart import change_set_path

SUPPORTED = handover.SUPPORTED
LAYERS = "layers"  # in the asset cache: one folder per layer, the patched asset file and ``layer.json``
FORMAT = 1
SHOWN = 12
LAZY = "__lazy"  # the attribute a still-unread asset carries while the patched file is written
CLOSING = re.compile(rb"</ModOps\s*>\s*\Z")
EMPTY_ROOT = re.compile(rb"<ModOps\s*/>\s*\Z")
LAZY_ASSET = re.compile(rb'<Asset __lazy="(\d+)"\s*/>')


class CLayerRefused(ValueError):
    """Layering cannot be made or undone; the message says why in a sentence."""


@dataclass
class SPlan:
    """What layering would do, read and not done: the patch file that gets the include, the include file, the layer,
    and the lines of the report."""

    patch: str
    include: str
    layer: SLayer
    kept: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        added, changed, removed = (number(count) for count in self.layer.counts())
        out = [
            tr("assets.layered_base", mods=", ".join(self.layer.mods), added=added, changed=changed, removed=removed),
            tr("assets.layered_edits", include=self.include, patch=self.patch),
            tr("assets.layered_rules"),
        ]
        if self.kept:
            out.append(tr("assets.layered_kept", list=handover.names(self.kept)))
        out.append(tr("assets.layered_texts"))
        out += [f"  {row}" for row in self.notes[: SHOWN * 2]]
        if len(self.notes) > SHOWN * 2:
            out.append("  " + tr("assets.and_n_more", n=len(self.notes) - SHOWN * 2))
        return out


@dataclass
class SBuilt:
    """What applying the mod gave: the patched asset file (a real file in the cache), the layer, and the stamp the file
    is named by. ``notes`` are the report's lines when the mod was applied just now, empty when it came from the cache."""

    file: Path
    layer: SLayer
    stamp: str
    notes: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)


def state(project: projects.CProject) -> dict | None:
    """The layering the project holds (``patch``, ``include``), None when none was taken."""
    return appfolders.layered_record(project)


def can_take(project: projects.CProject) -> bool:
    """Whether the Project window offers layering: an imported 117 or 1800 project whose patch of the asset file is still
    carried and that is neither layered nor handed over."""
    return (
        project.game in SUPPORTED
        and importmeta.read_meta(project.folder) is not None
        and carriedpart.carried_root(project).is_dir()
        and state(project) is None
        and handover.state(project) is None
    )


def has_edits(project: projects.CProject) -> bool:
    """Whether the change set holds work (the layered edits)."""
    path = change_set_path(project)
    if not path.is_file():
        return False
    held = CChangeSet.load(path)
    return bool(held.m_assets or held.m_new or held.m_removed or held.m_icons)


# the patched asset file
def patched_bytes(doc: CPatchDoc) -> bytes:
    """The patched file: touched and added assets serialised, every asset nobody read copied from its bytes (the game's
    file is never parsed whole)."""
    marked = []
    for element, (start, _end) in doc.m_lazy.items():
        element.set(LAZY, str(start))
        marked.append(element)
    try:
        text = b"\n".join(ET.tostring(root, encoding="utf-8", xml_declaration=False) for root in doc.m_top)
    finally:
        for element in marked:
            element.attrib.pop(LAZY, None)
    ends = dict(doc.m_lazy.values())
    return LAZY_ASSET.sub(lambda m: bytes(doc.m_data[int(m.group(1)) : ends[int(m.group(1))]]), text)


def _digest(project: projects.CProject, game: CGame, search: list[Path]) -> str:
    """What the layer depends on: the game's stamp, every ModOps file the mod carries (by content) and the mods the
    required ones could come from (id and version)."""
    root, hasher = carriedpart.carried_root(project), hashlib.sha1(f"{FORMAT}|{game.m_files.m_stamp}".encode())
    for relative_path in sorted(carriedpart.files_of(root)):
        if relative_path.lower().endswith(".xml"):
            hasher.update(relative_path.lower().encode() + hashlib.sha1((root / relative_path).read_bytes()).digest())
    for mod in sorted(find_mods(search, GAME_NUMBER.get(project.game))[0], key=lambda m: m.m_mod_id):
        hasher.update(f"|{mod.m_mod_id} {mod.m_version}".encode())
    return hasher.hexdigest()[:20]


def _cached(folder: Path, name: str) -> SBuilt | None:
    try:
        layer_json = json.loads((folder / "layer.json").read_text(encoding="utf-8"))
        target = folder / posixpath.basename(name)
        if layer_json.get("format") == FORMAT and target.is_file():
            return SBuilt(target, SLayer.from_json(layer_json["layer"]), folder.name)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def build_layer(
    project: projects.CProject,
    game: CGame,
    say: Callable[[str], None] = lambda _t: None,
    search: list[Path] | None = None,
    fresh: bool = False,
) -> SBuilt:
    """The mod laid over ``game``: read from the cache of the asset index when the carried files and the game are
    the ones it was made from, else applied now (``fresh``: always, for the report) and cached. ``search``: the mods
    folders that hold the mods the mod requires."""
    search = search or []
    key = _digest(project, game, search)
    folder = game.m_files.m_cache / LAYERS / key
    target = game.m_layout.patch_target
    if not fresh and (found := _cached(folder, target)) is not None:
        return found
    say(tr("assets.layered_applying"))
    imported, mod = handover.read_carried(project, game, say, search)
    doc = imported.session.m_docs.get(target.lower()) if imported.session else None
    if doc is None:
        raise CLayerRefused(tr("assets.layered_no_patch", path=target))
    scratch = modimport.SImported(CChangeSet(game.m_layout.key), [])
    modimport.assets_against(game, doc, scratch)
    layer = SLayer(
        [imported_mod.m_mod_id for imported_mod in imported.mods],
        {*scratch.changes.m_new},
        {*scratch.changed},
        {*scratch.removed},
    )
    file = folder / posixpath.basename(target)
    write_atomic(file, patched_bytes(doc), is_synchronous=False)
    write_atomic(
        folder / "layer.json",
        json.dumps({"format": FORMAT, "layer": layer.to_json()}, indent=1).encode("utf-8"),
        is_synchronous=False,
    )
    asked = {other for event in imported.conditional for other in event.others} - {
        mod.m_mod_id,
        *(imported_mod.m_mod_id for imported_mod in imported.mods),
    }
    notes = [tr("assets.layered_dropped", line=event.line()) for event in imported.dropped] + list(imported.lost)
    return SBuilt(
        file, layer, key, notes, [event.line() for event in imported.conditional if asked.intersection(event.others)]
    )


def open_layered(
    project: projects.CProject,
    game: CGame,
    say: Callable[[str], None] = lambda _t: None,
    search: list[Path] | None = None,
) -> CGame:
    """``game`` with the project's mod laid over it (the Assets app's base); ``game`` itself for a project that is not
    layered."""
    if state(project) is None or project.game != game.m_layout.key:
        return game
    built = build_layer(project, game, say, search)
    return layered_game(game, {game.m_layout.patch_target: built.file}, built.layer, built.stamp)


# plan, take, undo
def plan(
    project: projects.CProject,
    game: CGame,
    say: Callable[[str], None] = lambda _t: None,
    search: list[Path] | None = None,
) -> SPlan:
    """What layering the project's patch of the asset file would do (nothing is changed). Raises ``CLayerRefused``."""
    if importmeta.read_meta(project.folder) is None:
        raise CLayerRefused(tr("assets.handover_not_imported"))
    if project.game not in SUPPORTED or game.m_layout.key != project.game:
        raise CLayerRefused(tr("assets.layered_game", game=project.game))
    if state(project) is not None:
        raise CLayerRefused(tr("assets.layered_already"))
    if handover.state(project) is not None:
        raise CLayerRefused(tr("assets.layered_handed_over"))
    if has_edits(project):
        raise CLayerRefused(tr("assets.layered_has_work"))
    root = carriedpart.carried_root(project)
    files = carriedpart.files_of(root)
    patch = next(
        (
            relative_path
            for relative_path in sorted(files)
            if relative_path.lower() == game.m_layout.patch_target.lower()
        ),
        "",
    )
    if not patch:
        raise CLayerRefused(tr("assets.layered_no_patch", path=game.m_layout.patch_target))
    data = (root / patch).read_bytes()
    if not (CLOSING.search(data) or EMPTY_ROOT.search(data)):
        raise CLayerRefused(tr("assets.layered_no_closing_tag", file=patch))
    built = build_layer(project, game, say, search, fresh=True)
    if built.conditions:
        raise CLayerRefused(tr("assets.layered_conditions", list=handover.names(built.conditions)))
    _taken, kept = handover.patch_files(project, game.m_layout, files)
    return SPlan(
        patch,
        include_name(project, patch, files),
        built.layer,
        [k for k in kept if k.lower() != patch.lower()],
        built.notes,
    )


def include_name(project: projects.CProject, patch: str, files: dict[str, int]) -> str:
    """The include file's path: beside ``patch``, named for the mod, a name no carried file has."""
    taken = {relative_path.lower() for relative_path in files}
    folder = posixpath.dirname(patch)
    for copy_number in range(1, 1000):
        name = posixpath.join(
            folder, f"{project.mod_id}-edits{'' if copy_number == 1 else f'-{copy_number}'}.include.xml"
        )
        if name.lower() not in taken:
            return name
    raise CLayerRefused(tr("assets.layered_no_name"))


def take(project: projects.CProject, made: SPlan) -> dict:
    """Record the choice: from now on the Assets app opens the game with the mod laid over it and the build writes the
    edits as an include. Nothing of the mod's files is moved or changed. Raises ``CLayerRefused``."""
    if state(project) is not None or handover.state(project) is not None:
        raise CLayerRefused(tr("assets.layered_already"))
    record = {"format": FORMAT, "game": project.game, "patch": made.patch, "include": made.include}
    write_atomic(project.folder / appfolders.LAYERED_RECORD, (json.dumps(record, indent=1) + "\n").encode("utf-8"))
    return record


def undo(project: projects.CProject, discard: bool = False) -> bool:
    """Stop layering: the record goes, the build carries the mod's patch file as it is. Edits the layer holds are against
    the mod's result, so they are dropped with ``discard`` and named (``CLayerRefused``) without it."""
    if state(project) is None:
        raise CLayerRefused(tr("assets.layered_none"))
    if has_edits(project):
        if not discard:
            raise CLayerRefused(tr("assets.layered_edited"))
        change_set_path(project).unlink(missing_ok=True)
    (project.folder / appfolders.LAYERED_RECORD).unlink(missing_ok=True)
    return True


# the build
def append_include(data: bytes, include: str, patch: str = "") -> bytes:
    """The mod's patch file with one ``<Include>`` of ``include`` before the closing ``ModOps`` tag: every byte before
    it is the original's."""
    tag = f'<!-- written by the asset editor -->\n<Include File="{posixpath.basename(include)}"/>\n'.encode()
    if match := CLOSING.search(data):
        return data[: match.start()] + tag + data[match.start() :]
    if match := EMPTY_ROOT.search(data):
        return data[: match.start()] + b"<ModOps>\n" + tag + b"</ModOps>\n"
    raise CLayerRefused(tr("assets.layered_no_closing_tag", file=patch))


def render(project: projects.CProject, settings, log: Callable[[str], None]) -> dict:
    """The Assets part's files of a layered project: the mod's patch file (as it is, or with the include appended when
    the change set holds edits), the include file, and any other file the change set writes."""
    record = state(project)
    root = carriedpart.carried_root(project)
    patch, include = record["patch"], record["include"]
    own = CFileRef(str(root / patch))
    if not has_edits(project):
        log("assets: layered, no edits")
        return {patch: own}
    changes = replace(CChangeSet.load(change_set_path(project)), m_texts={})
    game = open_layered(
        project, open_installed(settings, project.game, log), log, handover.mods_folders(settings, project.game)
    )
    written = modops.render(game.document(changes), game.m_layout)
    text = written.pop(game.m_layout.patch_target, None)
    out: dict = {path: body.encode("utf-8") for path, body in written.items()}
    if text is None:
        out[patch] = own
    else:
        out[patch] = append_include(Path(own).read_bytes(), include, patch)
        out[include] = text.encode("utf-8")
    log(f"assets: layered, {len(changes.m_assets)} changed, {len(changes.m_new)} new, {len(changes.m_removed)} removed")
    return out
