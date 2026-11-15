"""A project: one folder per mod with every app's change sets, the mod's identity and GUID range, and the one build
and install of the mod with every app's output (docs/tool-suite.md, C4).

``Project.open(folder)`` reads ``project.json`` (a folder without one is a project with a default identity). An app
adds its output as a ``Part``; ``build`` merges the parts into ``<project>/build/<mod id>`` and checks it, ``install``
copies it into a mods folder. The older games load archives: a ``Delivery`` packs the files and installs. Stdlib.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..infra.i18n import tr
from ..mod import modlint
from ..mod.modxml import modinfo
from ..modloader.mods import GAME_NUMBER, read_mod
from ..modloader.modswap import InstallError, Trash, check_folder_name, place_folder, recycle
from ..othergames import installs
from ..paths import documents_dir
from ..sources import gametree
from . import guidranges, islandguids, linkannounce, modcheck, projectdeps

FILE = "project.json"
FORMAT = 1
# What a workspace from before projects uses (kept so its islands keep their numbers). The community registry gives
# part of it to another modder: a new project chooses its range instead (``suite.guidranges``).
DEFAULT_RANGE = (2_100_000_000, 2_100_999_999)
GAMES = installs.keys(lambda g: g.mod_loader)  # the games whose mod loader takes one mod folder
# The games a project delivers an archive to (the suite's delivery table, ``editor.archivedelivery``).
ARCHIVE_GAMES = ("anno2070", "anno1404", "anno2205", "anno1701")
ALL_GAMES = (*GAMES, *ARCHIVE_GAMES)
MODS_GAME = {"anno117": "mods", "anno1800": "mods1800"}  # the settings field of each game's mods folder
ISLAND_EDITS, ASSET_EDITS, UI_EDITS = "island-edits", "asset-edits", "ui-edits"  # each app's work in a project
PART_SUFFIX = ".part"  # a file being written (``buildcache.write_atomic``): never part of a mod
MODEL_EDITS = "model-edits"  # the viewer's: models imported from glTF, by game and game path
_MODOPS = re.compile(r"^\s*(?:<\?xml[^>]*\?>\s*)?<ModOps(?:\s[^>]*)?>(.*)</ModOps>\s*$", re.S)


@dataclass
class Project:
    """The mod a project makes and where its apps keep their work."""

    folder: Path
    mod_id: str = ""
    name: str = ""
    version: str = "0.1.0"
    game: str = "anno117"
    category: str = "Modding"
    description: str = ""
    guid_range: tuple[int, int] = DEFAULT_RANGE
    guid_choice: str = guidranges.CUSTOM  # how the range was chosen: own, public or custom (``suite.guidranges``)
    guid_source: str = ""  # a public choice's key (``guidranges.PublicChoice.key``)
    dependencies: dict = field(default_factory=dict)  # kind -> ModIDs, written into modinfo (``mods.DEPENDENCY_KINDS``)
    bundle: list = field(default_factory=list)  # mod folders built in as nested mods (``projectdeps``)
    defined: bool = False  # whether the folder holds a project.json (else the defaults of an older workspace)

    @classmethod
    def open(cls, folder: Path) -> Project:
        folder = Path(folder)
        path = folder / FILE
        if not path.is_file():
            slug = _slug(folder.name) or "my-mod"
            return cls(folder, mod_id=slug, name=folder.name or slug)
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if data.get("format", FORMAT) > FORMAT:
                raise ValueError(tr("made by a newer version (format {format})", format=data["format"]))
            fields = {k: data[k] for k in cls.__dataclass_fields__ if k in data and k not in ("folder", "defined")}
            fields["guid_range"] = tuple(int(g) for g in data.get("guid_range", DEFAULT_RANGE))
            project = cls(folder, **fields, defined=True)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise ValueError(tr("the project file {path} does not read: {error}", path=path, error=exc)) from exc
        project.validate()
        return project

    def validate(self) -> None:
        lo, hi = self.guid_range
        if not 0 < lo <= hi <= 2**31 - 1:
            raise ValueError(tr("the GUID range {lo}-{hi} is not a range of positive 32-bit numbers", lo=lo, hi=hi))
        if self.guid_choice not in guidranges.CHOICES:
            raise ValueError(
                tr(
                    "the GUID range choice {choice} is none of {choices}",
                    choice=repr(self.guid_choice),
                    choices=", ".join(guidranges.CHOICES),
                )
            )
        if not re.fullmatch(r"[a-z0-9-]+", self.mod_id):
            raise ValueError(
                tr("the mod id {mod_id} may hold only a-z, 0-9 and '-' (the loader's rule)", mod_id=repr(self.mod_id))
            )
        projectdeps.validate(self.mod_id, self.dependencies, self.bundle)
        if self.game not in ALL_GAMES:
            raise ValueError(
                tr("a project makes a mod for {games}, not {game}", games=", ".join(ALL_GAMES), game=repr(self.game))
            )

    def save(self) -> Path:
        self.validate()
        held = [g for g in islandguids.held(self.island_guids) if not self.guid_range[0] <= g <= self.guid_range[1]]
        if held:
            raise ValueError(
                tr(
                    "islands of this project are numbered already ({guid} lies outside {lo}-{hi}): "
                    "a savegame holds those numbers, so the range cannot move away from them",
                    guid=held[0],
                    lo=self.guid_range[0],
                    hi=self.guid_range[1],
                )
            )
        data = {"format": FORMAT, **{k: v for k, v in asdict(self).items() if k not in ("folder", "defined")}}
        data["guid_range"] = list(self.guid_range)
        self.folder.mkdir(parents=True, exist_ok=True)
        if not self.defined and not any(self.island_edits.glob("*.json")):  # a new project, not an older workspace
            islandguids.create(self.island_guids, self.guid_range)
        path = self.folder / FILE
        write_atomic(path, (json.dumps(data, indent=1) + "\n").encode("utf-8"))
        self.defined = True
        return path

    def choose_range(self, choice: str, source: str, custom: tuple[int, int], settings, others=None) -> None:
        """Take the range the choice stands for (``guidranges.resolve``; ``others``: the user's other projects'
        ranges, by default read from the settings' known projects)."""
        taken = others if others is not None else other_ranges(settings, self.folder, self.game)
        self.guid_range = tuple(guidranges.resolve(choice, source, custom, settings, self.game, taken))
        self.guid_choice, self.guid_source = choice, source if choice == guidranges.PUBLIC else ""

    # where each app keeps its work (the island editor's workspace had these already)
    @property
    def island_edits(self) -> Path:
        return self.folder / ISLAND_EDITS

    @property
    def asset_edits(self) -> Path:
        return self.folder / ASSET_EDITS

    @property
    def ui_edits(self) -> Path:
        return self.folder / UI_EDITS

    @property
    def island_guids(self) -> Path:
        return self.folder / islandguids.FILE

    @property
    def build_dir(self) -> Path:
        return self.folder / "build" / self.mod_id


def folder_of(settings) -> Path:
    """The folder of the project the suite has open: the setting ``project``, else the workspace."""
    return Path(settings.project or settings.workspace)


def current(settings) -> Project:
    return Project.open(folder_of(settings))


def remember(settings, folder: Path) -> None:
    """Note a project folder in the settings (the caller saves them), so other projects can keep clear of its range."""
    folder = str(Path(folder).resolve())
    if folder not in settings.known_projects:
        settings.known_projects.append(folder)


def other_ranges(settings, folder: Path, game: str) -> dict[str, tuple[int, int]]:
    """The GUID ranges of the user's other mods for ``game``: the settings' known projects but ``folder``, and the
    change sets in the workspace when ``folder`` is not the workspace (they hold a range without a ``project.json``).
    Keyed by the folder's name, or by its whole path where two share a name."""
    found: list[tuple[Path, tuple[int, int]]] = []
    here = Path(folder).resolve()
    for known in settings.known_projects:
        path = Path(known)
        if path.resolve() == here or not (path / FILE).is_file():
            continue
        try:
            other = Project.open(path)
        except ValueError:
            continue
        if other.game == game:
            found.append((path, tuple(other.guid_range)))
    workspace = Path(getattr(settings, "workspace", "") or "")
    if str(workspace) not in ("", ".") and workspace.resolve() != here:
        found += [(path, span) for path, span in _workspace_ranges(workspace, game)]
    names = [p.name for p, _ in found]
    return {(p.name if names.count(p.name) == 1 else str(p)): span for p, span in found}


def _workspace_ranges(workspace: Path, game: str) -> list[tuple[Path, tuple[int, int]]]:
    """The ranges of the asset editor's change sets (``asset-edits/*.json``) of ``game`` in a workspace."""
    out = []
    for file in sorted((workspace / ASSET_EDITS).glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            if data.get("game") == game:
                lo, hi = data["guid_range"]
                out.append((file, (int(lo), int(hi))))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def _slug(text: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "-", text.lower())).strip("-")


@dataclass(frozen=True)
class Part:
    """One app's share of the mod. ``has_work(project)`` says whether the app holds anything for the project's game;
    ``render(project, settings, log)`` returns the mod's files by path (the ``modinfo.json`` is the project's)."""

    key: str
    title: str
    has_work: Callable[[Project], bool]
    render: Callable[[Project, object, Callable[[str], None]], dict[str, bytes]]


@dataclass(frozen=True)
class EditsFolder:
    """The folder an app keeps its work in, ``<project>/<edits>/<game>/<game path>``: the files' listing, the atomic
    save and the ``Part`` every app of the suite needs the same way."""

    edits: str
    games: tuple[str, ...] = ALL_GAMES

    def folder(self, project: Project, game: str | None = None) -> Path:
        return project.folder / self.edits / (game or project.game)

    def saved_files(self, project: Project, game: str | None = None) -> dict[str, Path]:
        """Game path -> the project's copy, for every file the app holds for a game (a half-written ``.part`` file
        is not one)."""
        root = self.folder(project, game)
        if not root.is_dir():
            return {}
        found = (p for p in sorted(root.rglob("*")) if p.is_file() and p.suffix != PART_SUFFIX)
        return {p.relative_to(root).as_posix(): p for p in found}

    def save_files(self, project: Project, game: str, files: dict[str, bytes]) -> list[Path]:
        """Keep files (game path -> bytes) in the project, each whole or not at all; a path that leaves the game's
        folder (``..``, a drive) is refused before anything is written."""
        root = self.folder(project, game)
        targets = []
        for path in files:
            parts = path.replace("\\", "/").strip("/").split("/")
            if not parts[0] or any(x in ("", ".", "..") or ":" in x for x in parts):
                raise ValueError(tr("{path} is not a path inside the game's folder: not saved", path=repr(path)))
            targets.append(root.joinpath(*parts))
        for dest, data in zip(targets, files.values(), strict=True):
            write_atomic(dest, data)
        return targets

    def save_announced(
        self, project: Project, game: str, files: dict[str, bytes], remove: Sequence[str] = ()
    ) -> linkannounce.Notice:
        """``save_files``, then take ``remove`` (the game paths an earlier save wrote in another form) out of the
        project; the notice says what the running game needs to show the files (``linkannounce``)."""
        self.save_files(project, game, files)
        root = self.folder(project, game)
        for path in remove:
            if path not in files:
                (root / path).unlink(missing_ok=True)
        return linkannounce.announce_files(project.mod_id, sorted(files))

    def has_work(self, project: Project) -> bool:
        return project.game in self.games and bool(self.saved_files(project))

    def part(self, key: str, title: str, label: str) -> Part:
        """The app's ``Part``: every saved file goes into the mod as it is (``label`` heads the log line)."""

        def render(project: Project, _settings, log: Callable[[str], None]) -> dict[str, bytes]:
            files = {path: p.read_bytes() for path, p in self.saved_files(project).items()}
            log(f"{label}: {len(files)} files")
            return files

        return Part(key, title, self.has_work, render)


@dataclass(frozen=True)
class Delivery:
    """How an older game takes the project: ``package(files, project, settings, log)`` turns the parts' merged files
    (game path -> bytes) into the archives to write (path in the install -> bytes), ``install(project, settings,
    log)`` puts the built archives into the game (its own files kept first) and returns a line to show."""

    package: Callable[[dict[str, bytes], Project, object, Callable[[str], None]], dict[str, bytes]]
    install: Callable[[Project, object, Callable[[str], None]], str]


@dataclass
class BuildResult:
    folder: Path
    parts: list[str] = field(default_factory=list)  # the parts that gave files
    files: int = 0
    problems: list[str] = field(default_factory=list)  # the mod check's sentences and the lint's findings

    def lines(self) -> list[str]:
        head = tr(
            "{files} files from {parts} into {folder}",
            files=self.files,
            parts=", ".join(self.parts) or tr("no part"),
            folder=self.folder,
        )
        return [head, *(f"  {p}" for p in self.problems)]


class Clash(ValueError):
    """Two parts write one path in ways that cannot be merged."""


def merge_files(outputs: list[tuple[str, dict[str, bytes]]]) -> dict[str, bytes]:
    """The parts' files as one mod: a ModOps file two parts write becomes one, the operations in the parts' order;
    any other path two parts write must have the same bytes."""
    merged: dict[str, tuple[str, bytes]] = {}
    clashes = []
    for part, files in outputs:
        for path, data in files.items():
            key = path.replace("\\", "/").lower()
            if key not in merged:
                merged[key] = (path, data)
                continue
            first, old = merged[key]
            if old == data:
                continue
            joined = _join_modops(old, data, part)
            if joined is None:
                clashes.append(tr("{path}: written by two parts, not both ModOps files", path=path))
            else:
                merged[key] = (first, joined)
    if clashes:
        raise Clash("; ".join(clashes))
    return dict(merged.values())


def _join_modops(a: bytes, b: bytes, part: str) -> bytes | None:
    ma, mb = _MODOPS.match(a.decode("utf-8", "replace")), _MODOPS.match(b.decode("utf-8", "replace"))
    if not (ma and mb):
        return None
    body = f"{ma.group(1).rstrip()}\n<!-- {part} -->\n{mb.group(1).strip()}\n"
    return f'<?xml version="1.0" encoding="utf-8"?>\n<ModOps>{body}</ModOps>\n'.encode()


def build(
    project: Project,
    parts: list[Part],
    settings,
    log: Callable[[str], None] = print,
    deliveries: dict[str, Delivery] | None = None,
) -> BuildResult:
    """Every part with work into ``project.build_dir`` (emptied first), the ``modinfo.json`` and the checks; for an
    older game the archives its ``Delivery`` makes of the parts' files."""
    project.validate()
    delivery = None
    if project.game in ARCHIVE_GAMES:
        delivery = (deliveries or {}).get(project.game)
        if delivery is None:
            raise ValueError(tr("building for {game} is not available in this edition", game=project.game))
    out = project.build_dir
    if out.name != project.mod_id or project.folder not in out.parents:
        raise ValueError(tr("refusing to rebuild {folder}: not the project's own build folder", folder=out))
    outputs = []
    for part in parts:
        if not part.has_work(project):
            continue
        log(f"{part.title}: writing")
        files = part.render(project, settings, log)
        files.pop("modinfo.json", None)
        if files:
            outputs.append((part.title, files))
    merged = merge_files(outputs)
    if delivery is not None:
        merged = delivery.package(merged, project, settings, log)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for path, data in sorted(merged.items()):
        dest = out / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    if delivery is not None:  # archives in the install's layout; no mod folder to describe or lint
        return BuildResult(out, [t for t, _ in outputs], len(merged))
    description = project.description or f"Made with {', '.join(t for t, _ in outputs) or 'the suite'}."
    deps, after = projectdeps.modinfo_fields(project.dependencies)
    info = modinfo(project.mod_id, project.category, project.name or project.mod_id, description,
                   version=project.version, dependencies=deps, load_after=after)  # fmt: skip
    (out / "modinfo.json").write_text(info, encoding="utf-8", newline="\n")
    mods_dir = mods_folder_of(project.game, settings)
    problems = projectdeps.copy_bundle(project.folder, project.bundle, out)
    problems += projectdeps.bundle_problems(project.mod_id, out, mods_dir, project.game)
    lint = modlint.lint_mod(out, folders=[mods_dir] if mods_dir else None)
    problems += modcheck.check(out) + [f.line() for f in lint.findings]
    return BuildResult(out, [t for t, _ in outputs], len(merged), problems)


def install_built(
    project: Project, settings, deliveries: dict[str, Delivery] | None, log: Callable[[str], None] = print
) -> str:
    """Put the built project into its game: the mod folder into the mods folder of the settings, or the archives."""
    if project.game in ARCHIVE_GAMES:
        delivery = (deliveries or {}).get(project.game)
        if delivery is None:
            raise ValueError(tr("installing for {game} is not available in this edition", game=project.game))
        return delivery.install(project, settings, log)
    return str(install(project, Path(getattr(settings, MODS_GAME[project.game], "")), log))


def mods_folder_of(game: str, settings) -> Path | None:
    """The mods folder of ``game`` that exists: the setting's, or the Documents folder's standard place when it is
    empty (as the mod manager does); None when there is none, so a check says it could not look."""
    given = str(getattr(settings, MODS_GAME.get(game, ""), "") or "").strip()
    standard = gametree.user_dir(game) if game in gametree.DOCUMENTS else documents_dir()
    folder = Path(given) if given else standard / "mods"
    return folder if folder.is_dir() else None


def install(project: Project, mods: Path, log: Callable[[str], None] = print, trash: Trash = recycle) -> Path:
    """Copy the built mod into the mods folder ``mods`` (an older copy of it replaced); returns where it went. The
    folder it would replace must hold this project's mod (or nothing), the copy is made beside it first and swapped in,
    and the old one goes to the recycle bin (``modloader.modswap``)."""
    source = project.build_dir
    if not (source / "modinfo.json").is_file():
        raise RuntimeError(tr("nothing built yet: build the project first"))
    mods = Path(mods)
    if not str(mods).strip() or not mods.is_absolute():
        raise RuntimeError(tr("set the game's mods folder to an absolute path first"))
    check_folder_name(project.mod_id)
    target = mods / project.mod_id
    if os.path.lexists(target):
        old, _ = read_mod(target, GAME_NUMBER.get(project.game))
        if old is None or old.mod_id != project.mod_id:
            raise InstallError(
                tr(
                    "the folder {folder} in the mods folder holds another mod ({mod}): not replaced",
                    folder=target.name,
                    mod=old.mod_id if old else tr("no mod"),
                )
            )
    before = linkannounce.snapshot(target)
    for note in place_folder(source, target, trash):
        log(note)
    log(
        tr(
            "installed into {folder} - asset values follow a reload in the game, new islands and assets a new game",
            folder=target,
        )
    )
    log(linkannounce.announce_install(project.mod_id, target, before).text)
    return target
