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
from ..modloader.patchdoc import PatchDoc
from ..suite import appfolders, carriedpart, importmeta
from ..suite import project as projects
from ..suite.buildout import FileRef
from . import handover, modimport, modops
from .assetgame import Game, layered_game, open_installed
from .changeset import ChangeSet
from .origins import Layer
from .projectpart import change_set_path

SUPPORTED = handover.SUPPORTED
LAYERS = "layers"  # in the asset cache: one folder per layer, the patched asset file and ``layer.json``
FORMAT = 1
SHOWN = 12
LAZY = "__lazy"  # the attribute a still-unread asset carries while the patched file is written
CLOSING = re.compile(rb"</ModOps\s*>\s*\Z")
EMPTY_ROOT = re.compile(rb"<ModOps\s*/>\s*\Z")
LAZY_ASSET = re.compile(rb'<Asset __lazy="(\d+)"\s*/>')


class LayerRefused(ValueError):
    """Layering cannot be made or undone; the message says why in a sentence."""


@dataclass
class Plan:
    """What layering would do, read and not done: the patch file that gets the include, the include file, the layer,
    and the lines of the report."""

    patch: str
    include: str
    layer: Layer
    kept: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        added, changed, removed = (number(n) for n in self.layer.counts())
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
class Built:
    """What applying the mod gave: the patched asset file (a real file in the cache), the layer, and the stamp the file
    is named by. ``notes`` are the report's lines when the mod was applied just now, empty when it came from the cache."""

    file: Path
    layer: Layer
    stamp: str
    notes: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)


def state(project: projects.Project) -> dict | None:
    """The layering the project holds (``patch``, ``include``), None when none was taken."""
    return appfolders.layered_record(project)


def can_take(project: projects.Project) -> bool:
    """Whether the Project window offers layering: an imported 117 or 1800 project whose patch of the asset file is still
    carried and that is neither layered nor handed over."""
    return (
        project.game in SUPPORTED
        and importmeta.read_meta(project.folder) is not None
        and carriedpart.carried_root(project).is_dir()
        and state(project) is None
        and handover.state(project) is None
    )


def has_edits(project: projects.Project) -> bool:
    """Whether the change set holds work (the layered edits)."""
    path = change_set_path(project)
    if not path.is_file():
        return False
    held = ChangeSet.load(path)
    return bool(held.assets or held.new or held.removed or held.icons)


# the patched asset file
def patched_bytes(doc: PatchDoc) -> bytes:
    """The patched file: touched and added assets serialised, every asset nobody read copied from its bytes (the game's
    file is never parsed whole)."""
    marked = []
    for el, (start, _end) in doc.lazy.items():
        el.set(LAZY, str(start))
        marked.append(el)
    try:
        text = b"\n".join(ET.tostring(root, encoding="utf-8", xml_declaration=False) for root in doc.top)
    finally:
        for el in marked:
            el.attrib.pop(LAZY, None)
    ends = dict(doc.lazy.values())
    return LAZY_ASSET.sub(lambda m: bytes(doc.data[int(m.group(1)) : ends[int(m.group(1))]]), text)


def _digest(project: projects.Project, game: Game, search: list[Path]) -> str:
    """What the layer depends on: the game's stamp, every ModOps file the mod carries (by content) and the mods the
    required ones could come from (id and version)."""
    root, h = carriedpart.carried_root(project), hashlib.sha1(f"{FORMAT}|{game.files.stamp}".encode())
    for rel in sorted(carriedpart.files_of(root)):
        if rel.lower().endswith(".xml"):
            h.update(rel.lower().encode() + hashlib.sha1((root / rel).read_bytes()).digest())
    for mod in sorted(find_mods(search, GAME_NUMBER.get(project.game))[0], key=lambda m: m.mod_id):
        h.update(f"|{mod.mod_id} {mod.version}".encode())
    return h.hexdigest()[:20]


def _cached(folder: Path, name: str) -> Built | None:
    try:
        info = json.loads((folder / "layer.json").read_text(encoding="utf-8"))
        target = folder / posixpath.basename(name)
        if info.get("format") == FORMAT and target.is_file():
            return Built(target, Layer.from_json(info["layer"]), folder.name)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def build_layer(
    project: projects.Project,
    game: Game,
    say: Callable[[str], None] = lambda _t: None,
    search: list[Path] | None = None,
    fresh: bool = False,
) -> Built:
    """The mod laid over ``game``: read from the cache of the asset index when the carried files and the game are
    the ones it was made from, else applied now (``fresh``: always, for the report) and cached. ``search``: the mods
    folders that hold the mods the mod requires."""
    search = search or []
    key = _digest(project, game, search)
    folder = game.files.cache / LAYERS / key
    target = game.layout.patch_target
    if not fresh and (found := _cached(folder, target)) is not None:
        return found
    say(tr("assets.layered_applying"))
    imported, mod = handover.read_carried(project, game, say, search)
    doc = imported.session.docs.get(target.lower()) if imported.session else None
    if doc is None:
        raise LayerRefused(tr("assets.layered_no_patch", path=target))
    scratch = modimport.Imported(ChangeSet(game.layout.key), [])
    modimport.assets_against(game, doc, scratch)
    layer = Layer([m.mod_id for m in imported.mods], {*scratch.changes.new}, {*scratch.changed}, {*scratch.removed})
    file = folder / posixpath.basename(target)
    write_atomic(file, patched_bytes(doc), sync=False)
    write_atomic(
        folder / "layer.json",
        json.dumps({"format": FORMAT, "layer": layer.to_json()}, indent=1).encode("utf-8"),
        sync=False,
    )
    asked = {o for e in imported.conditional for o in e.others} - {mod.mod_id, *(m.mod_id for m in imported.mods)}
    notes = [tr("assets.layered_dropped", line=e.line()) for e in imported.dropped] + list(imported.lost)
    return Built(file, layer, key, notes, [e.line() for e in imported.conditional if asked.intersection(e.others)])


def open_layered(
    project: projects.Project,
    game: Game,
    say: Callable[[str], None] = lambda _t: None,
    search: list[Path] | None = None,
) -> Game:
    """``game`` with the project's mod laid over it (the Assets app's base); ``game`` itself for a project that is not
    layered."""
    if state(project) is None or project.game != game.layout.key:
        return game
    built = build_layer(project, game, say, search)
    return layered_game(game, {game.layout.patch_target: built.file}, built.layer, built.stamp)


# plan, take, undo
def plan(
    project: projects.Project,
    game: Game,
    say: Callable[[str], None] = lambda _t: None,
    search: list[Path] | None = None,
) -> Plan:
    """What layering the project's patch of the asset file would do (nothing is changed). Raises ``LayerRefused``."""
    if importmeta.read_meta(project.folder) is None:
        raise LayerRefused(tr("assets.handover_not_imported"))
    if project.game not in SUPPORTED or game.layout.key != project.game:
        raise LayerRefused(tr("assets.layered_game", game=project.game))
    if state(project) is not None:
        raise LayerRefused(tr("assets.layered_already"))
    if handover.state(project) is not None:
        raise LayerRefused(tr("assets.layered_handed_over"))
    if has_edits(project):
        raise LayerRefused(tr("assets.layered_has_work"))
    root = carriedpart.carried_root(project)
    files = carriedpart.files_of(root)
    patch = next((rel for rel in sorted(files) if rel.lower() == game.layout.patch_target.lower()), "")
    if not patch:
        raise LayerRefused(tr("assets.layered_no_patch", path=game.layout.patch_target))
    data = (root / patch).read_bytes()
    if not (CLOSING.search(data) or EMPTY_ROOT.search(data)):
        raise LayerRefused(tr("assets.layered_no_closing_tag", file=patch))
    built = build_layer(project, game, say, search, fresh=True)
    if built.conditions:
        raise LayerRefused(tr("assets.layered_conditions", list=handover.names(built.conditions)))
    _taken, kept = handover.patch_files(project, game.layout, files)
    return Plan(
        patch,
        include_name(project, patch, files),
        built.layer,
        [k for k in kept if k.lower() != patch.lower()],
        built.notes,
    )


def include_name(project: projects.Project, patch: str, files: dict[str, int]) -> str:
    """The include file's path: beside ``patch``, named for the mod, a name no carried file has."""
    taken = {rel.lower() for rel in files}
    folder = posixpath.dirname(patch)
    for n in range(1, 1000):
        name = posixpath.join(folder, f"{project.mod_id}-edits{'' if n == 1 else f'-{n}'}.include.xml")
        if name.lower() not in taken:
            return name
    raise LayerRefused(tr("assets.layered_no_name"))


def take(project: projects.Project, made: Plan) -> dict:
    """Record the choice: from now on the Assets app opens the game with the mod laid over it and the build writes the
    edits as an include. Nothing of the mod's files is moved or changed. Raises ``LayerRefused``."""
    if state(project) is not None or handover.state(project) is not None:
        raise LayerRefused(tr("assets.layered_already"))
    record = {"format": FORMAT, "game": project.game, "patch": made.patch, "include": made.include}
    write_atomic(project.folder / appfolders.LAYERED_RECORD, (json.dumps(record, indent=1) + "\n").encode("utf-8"))
    return record


def undo(project: projects.Project, discard: bool = False) -> bool:
    """Stop layering: the record goes, the build carries the mod's patch file as it is. Edits the layer holds are against
    the mod's result, so they are dropped with ``discard`` and named (``LayerRefused``) without it."""
    if state(project) is None:
        raise LayerRefused(tr("assets.layered_none"))
    if has_edits(project):
        if not discard:
            raise LayerRefused(tr("assets.layered_edited"))
        change_set_path(project).unlink(missing_ok=True)
    (project.folder / appfolders.LAYERED_RECORD).unlink(missing_ok=True)
    return True


# the build
def append_include(data: bytes, include: str, patch: str = "") -> bytes:
    """The mod's patch file with one ``<Include>`` of ``include`` before the closing ``ModOps`` tag: every byte before
    it is the original's."""
    tag = f'<!-- written by the asset editor -->\n<Include File="{posixpath.basename(include)}"/>\n'.encode()
    if m := CLOSING.search(data):
        return data[: m.start()] + tag + data[m.start() :]
    if m := EMPTY_ROOT.search(data):
        return data[: m.start()] + b"<ModOps>\n" + tag + b"</ModOps>\n"
    raise LayerRefused(tr("assets.layered_no_closing_tag", file=patch))


def render(project: projects.Project, settings, log: Callable[[str], None]) -> dict:
    """The Assets part's files of a layered project: the mod's patch file (as it is, or with the include appended when
    the change set holds edits), the include file, and any other file the change set writes."""
    record = state(project)
    root = carriedpart.carried_root(project)
    patch, include = record["patch"], record["include"]
    own = FileRef(str(root / patch))
    if not has_edits(project):
        log("assets: layered, no edits")
        return {patch: own}
    changes = replace(ChangeSet.load(change_set_path(project)), texts={})
    game = open_layered(
        project, open_installed(settings, project.game, log), log, handover.mods_folders(settings, project.game)
    )
    written = modops.render(game.document(changes), game.layout)
    text = written.pop(game.layout.patch_target, None)
    out: dict = {path: body.encode("utf-8") for path, body in written.items()}
    if text is None:
        out[patch] = own
    else:
        out[patch] = append_include(Path(own).read_bytes(), include, patch)
        out[include] = text.encode("utf-8")
    log(f"assets: layered, {len(changes.assets)} changed, {len(changes.new)} new, {len(changes.removed)} removed")
    return out
