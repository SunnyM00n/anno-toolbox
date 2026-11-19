"""The apps take the files of an imported mod (docs/mod-import.md, sections 3.4, 12 and 13). Stdlib only.

An imported file whose kind an app edits (objects, UI, sound, effects, maps, islands) does not stay in ``mod-files``:
it lives in the app's own project folder (``object-edits/<game>/<mod path>``), where the app already reads what the
project holds over the game's files. ``FOLDERS`` is the table owner -> folder; ``root_for`` says where the import puts
a file, ``take_into_apps`` moves the files of a project imported before, ``held_paths`` is what the build's carried part
must not write again (one owner per path). ``edited`` and ``laid`` make the imported file the app's *base*, not a
change: a saved file whose bytes are still the imported ones is not "your edit" and is laid under the game as part of it.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from ..sources.gametree import GameTree
from ..sources.overlay import normalize
from . import importmeta, modclassify
from . import project as projects
from .project import ALL_GAMES, ISLAND_EDITS, UI_EDITS, EditsFolder

SOUND_GAMES = ("anno117", "anno1800", "anno2205", "anno2070", "anno1404", "anno1701")
# owner -> (the app's folder in the project, the games the app keeps work for): the folders of the apps' ``EDITS``
FOLDERS: dict[str, tuple[str, tuple[str, ...]]] = {
    modclassify.OBJECTS: ("object-edits", ALL_GAMES),
    modclassify.UI: (UI_EDITS, ("anno117", "anno1800", "anno1404", "anno1701")),
    modclassify.SOUND: ("sound-edits", SOUND_GAMES),
    modclassify.PARTICLES: ("particle-edits", ALL_GAMES),
    # a map is kept whole where the Map editor keeps its files; an island's files are the base its edit file
    # (island-edits/117_<island>.json) is made against, beside the edit files in a folder per game (117 only: 1800's
    # island build reads the game's files, not the project's)
    modclassify.MAPS: ("map-edits", ("anno117", "anno1800")),
    modclassify.ISLANDS: (ISLAND_EDITS, ("anno117",)),
}
Say = Callable[[str], None]


def owner_of(game: str, rel: str, nested: tuple[str, ...] = ()) -> str:
    """The whole-file app that takes the mod path ``rel`` (else ``modclassify.CARRIED``). A file inside a nested mod
    stays carried at its own path: it is not a path of the game."""
    low = rel.replace("\\", "/").lower()
    if any(low.startswith(n.lower().rstrip("/") + "/") for n in nested):
        return modclassify.CARRIED
    owner = modclassify.classify(game, rel).owner
    folder = FOLDERS.get(owner)
    return owner if folder and game in folder[1] else modclassify.CARRIED


def root_for(project_dir: Path | str, game: str, owner: str) -> Path:
    """The folder a file of ``owner`` goes to: its app's folder for the game, else the carried folder."""
    folder = FOLDERS.get(owner)
    return Path(project_dir) / (folder[0] if folder else importmeta.MOD_FILES) / game


def nested_of(meta: importmeta.Meta | None) -> tuple[str, ...]:
    return tuple(n["path"] for n in meta.nested) if meta else ()


def _apps_files(project: projects.Project) -> dict[str, Path]:
    """Lower-case game path -> the project's file, over every whole-file app's folder."""
    found: dict[str, Path] = {}
    for name, games in FOLDERS.values():
        if project.game in games:
            found.update({p.lower(): f for p, f in EditsFolder(name, games).saved_files(project).items()})
    return found


LAYERED_RECORD = "layered.json"  # in the project folder: the Assets app edits on top of the mod's patch file


def layered_record(project: projects.Project) -> dict | None:
    """The layered Assets record (``patch``: the mod's patch file it adds an include to, ``include``: the include file
    it writes; docs/mod-import.md, "Phase 6 as built"), None when the project is not layered."""
    try:
        data = json.loads((project.folder / LAYERED_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("patch") else None


def held_paths(project: projects.Project) -> set[str]:
    """The (lower-case) mod paths the whole-file apps hold, and the patch file a layered Assets project writes itself:
    the carried part leaves them out, so each path is written by exactly one part."""
    if importmeta.read_meta(project.folder) is None:
        return set()
    held = set(_apps_files(project))
    if (layered := layered_record(project)) is not None:
        held.add(str(layered["patch"]).lower())
    return held


def take_into_apps(project: projects.Project, say: Say = lambda _t: None) -> Counter:
    """Move the files an import left in ``mod-files`` that a whole-file app takes into that app's folder (a project
    imported before the app learned the kind, or a file given back by mistake); ``owner -> files moved``. Nothing is
    read or copied: a move within the project. ``say`` is called between chunks and may raise to stop."""
    meta = importmeta.read_meta(project.folder)
    moved: Counter = Counter()
    if meta is None:
        return moved
    carried = project.folder / importmeta.MOD_FILES / project.game
    nested, made = nested_of(meta), set()
    for done, (rel, _size, _digest) in enumerate(importmeta.read_manifest(project.folder)):
        owner = owner_of(project.game, rel, nested)
        source = carried / rel
        if owner == modclassify.CARRIED or not source.is_file():
            continue
        target = root_for(project.folder, project.game, owner) / rel
        if target.exists():
            continue  # the app already holds it (an edit): the carried one is left for the check to name
        if target.parent not in made:
            target.parent.mkdir(parents=True, exist_ok=True)
            made.add(target.parent)
        os.replace(source, target)
        moved[owner] += 1
        if done % 1000 == 0:
            say("")
    return moved


@lru_cache(maxsize=8)
def _manifest(folder: str, stamp: tuple[int, int], game: str, nested: tuple[str, ...]) -> dict[str, tuple[int, str]]:
    """Lower-case mod path -> (size, sha256 or "") of the files the apps took at the import."""
    return {
        rel.lower(): (size, digest)
        for rel, size, digest in importmeta.read_manifest(Path(folder))
        if owner_of(game, rel, nested) != modclassify.CARRIED
    }


def imported(project: projects.Project) -> dict[str, tuple[int, str]]:
    """What the import gave the apps: lower-case mod path -> (size, sha256 or "" when the import did not hash)."""
    meta = importmeta.read_meta(project.folder)
    path = project.folder / importmeta.FILES_FILE
    if meta is None or not path.is_file():
        return {}
    st = path.stat()
    return _manifest(str(project.folder), (st.st_mtime_ns, st.st_size), project.game, nested_of(meta))


_checked: dict[tuple[str, int, int], bool] = {}


def is_base(base: dict[str, tuple[int, str]], rel: str, file: Path) -> bool:
    """Whether ``file`` (the project's copy of mod path ``rel``) still holds the bytes the import gave: the size, and
    the SHA-256 when the import kept one. An import without hashes can only compare sizes."""
    wanted = base.get(rel.lower())
    if wanted is None:
        return False
    try:
        st = file.stat()
    except OSError:
        return False
    if st.st_size != wanted[0]:
        return False
    if not wanted[1]:
        return True
    key = (f"{file}|{wanted[1]}", st.st_mtime_ns, st.st_size)
    if key not in _checked:
        with file.open("rb") as fh:
            _checked[key] = hashlib.file_digest(fh, "sha256").hexdigest() == wanted[1]
    return _checked[key]


def edited(project: projects.Project, edits: EditsFolder, game: str | None = None) -> dict[str, Path]:
    """``edits.saved_files`` without the files that are still the imported ones: what the person saved, so an app does
    not show an imported file as its change, nor takes it out again when an edit is undone."""
    saved = edits.saved_files(project, game)
    base = imported(project) if (game or project.game) == project.game else {}
    return {p: f for p, f in saved.items() if not is_base(base, p, f)} if base else saved


def laid(tree: GameTree, project: projects.Project, edits: EditsFolder) -> GameTree:
    """``tree`` with the imported files of this app laid under it as part of the game: they are what the app's
    documents are read from and compared against, so an imported file is the base and not a change. A file the person
    saved over one is not laid (the project's copy is then the app's own work, restored over the game's by the app).
    Content is read when first asked for and kept."""
    if tree.game != project.game:
        return tree
    base = imported(project)
    keys = {normalize(p): f for p, f in edits.saved_files(project).items() if base and is_base(base, p, f)}
    if not keys:
        return tree
    cache: dict[str, bytes] = {}

    def read(key: str) -> bytes:
        if key not in keys:
            return tree.read(key)
        if key not in cache:
            cache[key] = keys[key].read_bytes()
        return cache[key]

    top = GameTree(
        f"{tree.name} + {project.mod_id}",
        read,
        [*tree.paths, *keys],
        lambda key: project.mod_id if key in keys else tree.origin(key),
        tree.game,
        deleted=tree.deleted,
        stamp=f"{tree.stamp}|{project.mod_id}",
    )
    top.folder = tree.folder
    top.local_of = lambda key: keys.get(key) or tree.local(key)
    return top
