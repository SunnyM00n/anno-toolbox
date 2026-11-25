"""The island editor's application state, behind the workbench window (``desktop``): the settings (the suite's,
``suite.settings``), the island lists per game (``islandcatalogue``), opening an island (a built one, a vanilla 117 one, a 2070 one,
another game's, or an island file of any mod folder), and the build / install jobs that write the mods, in the
background with their log.
Everything the app writes lives in its workspace folder: the mods it builds, the edit files, the cached
meshes and textures. Plan and use: ``docs/island-editor.md``.
"""

from __future__ import annotations

import importlib
import re
import shutil
import threading
import traceback
from pathlib import Path

import numpy as np

from ..infra.i18n import tr
from ..islands import islandedits
from ..islands.islandgrid import HEIGHT_UNITS
from ..islands.islandview import CFiles
from ..modloader import modswap
from ..othergames import installs, isd1701
from ..render import nativegpu
from ..render.nativelook import apply_hardware
from ..suite import extspecs, features, linkannounce, project
from ..suite.settings import CSettings, is_2070, settings_file
from . import archivedelivery, blankisland, islandeditor, oldenginebuild
from .formats.a7m import edit117 as a7m_edit117
from .formats.a7m import edit1800 as a7m_edit1800
from .formats.isd import blank as isd_blank
from .formats.isd import islandlist as isd_islandlist
from .islandcatalogue import BUILDABLE, ISLANDS_2070, NATIVE, POOL, CIslandCatalogue, builder_for

MOD_ID = "anno2070-islands"
MOD_NAME = "Anno 2070 islands (island editor)"
MOD_ID_117 = "anno117-islands"  # Anno 117 editing: vanilla islands edited (formats.a7m.edit117)
MOD_NAME_117 = "Anno 117 islands (island editor)"
MOD_ID_1800 = "anno1800-islands"  # Anno 1800 editing: islands edited in place (formats.a7m.edit1800)
MOD_NAME_1800 = "Anno 1800 islands (island editor)"
PORT_MODS = {"anno117": "anno117-ported-islands", "anno1800": "anno1800-ported-islands"}  # islandconvert
PLAIN_GROUND = ("anno1404", "anno1701")  # their blank islands are painted one material (``blankisland.plain_ground``)
BLANKABLE = ("anno1800", "anno1404", "anno1701")  # a blank island is one of the game's own, cleared (``open_blank``)
OLD_ENGINE = installs.keys(
    lambda game: game.granny
)  # 1404, 1701: built into one of the game's archives (oldenginebuild)
NEW_SIZES = (128, 160, 192, 224, 256)
VIEWER_CLASSES = {  # ``installs.SGame.editor`` -> (module, class) of the editor that opens its islands
    "a7m": ("a7mviewer", "CA7mViewer"),
    "a6m": ("a7mviewer", "CA6mViewer"),
    "isd": ("gameviewers", "CIsdViewer"),
    "isd1701": ("gameviewers", "CIsd1701Viewer"),
}


def viewer_class(kind: str):
    """The editor class of another game's islands (``installs.SGame.editor``), imported when asked for: the 2070-format
    viewers draw models and are not needed by an application restricted to 1800."""
    module, name = VIEWER_CLASSES[kind]
    return getattr(importlib.import_module(f"{__package__}.{module}"), name)


def edit_file2070(source: str) -> str:
    """The edit file of a 2070 island: by its stem under ``ISLANDS_2070``, else by its folders too,
    so two islands of one name (a campaign's ``n_s01`` and the pool's) never share one."""
    from .formats.isd import edit as isd_edit

    if source.startswith(ISLANDS_2070):
        return isd_edit.edit_name(source)
    slug = re.sub(r"[^a-z0-9_]+", "-", source.lower().removesuffix(".isd").replace("/levels/", "/")).strip("-")
    return f"{isd_edit.EDIT_PREFIX}{slug}.json"


class CJobStopped(Exception):
    """The person stopped the job: ``CJob.log`` raises it on the job's thread."""


class CJob:
    """One background task (a build, an install) with its log. ``stop()`` ends it at its next ``log`` line, as a
    failure of that step would (what a step wrote is swapped in whole or not at all); a step that must run whole
    (an install, a restore) reports through ``note``."""

    def __init__(self, name: str, work) -> None:
        self.m_name = name
        self.m_lines: list[str] = []
        self.m_state = "running"
        self.m_result = None
        self._m_stop = threading.Event()
        self._m_thread = threading.Thread(target=self._run, args=(work,), daemon=True)
        self._m_thread.start()

    def log(self, line: str) -> None:
        """A line of the log, and the place where a stopped job ends."""
        if self._m_stop.is_set():
            raise CJobStopped
        self.m_lines.append(str(line))

    def note(self, line: str) -> None:
        """A line of the log from a step a stop must not cut short."""
        self.m_lines.append(str(line))

    def stop(self) -> None:
        """Ask the job to end at its next log line (state ``stopped``); nothing happens to one that has ended."""
        self._m_stop.set()

    def _run(self, work) -> None:
        try:
            self.m_result = work(self)
            self.m_state = "done"
        except CJobStopped:
            self.note(tr("islands.job_stopped", name=self.m_name))
            self.m_state = "stopped"
        except (Exception, SystemExit) as error:  # the window shows what went wrong; the app stays up
            self.m_lines += traceback.format_exc().splitlines()
            self.note(tr("islands.failed_error", error=error))
            self.m_state = "failed"

    def status(self, since: int = 0) -> dict:
        """The job's state and its log from line ``since``; ``next`` is the line to ask from the next time."""
        state, lines = self.m_state, self.m_lines[since:]  # the state first: a job that ended has logged its last line
        return {"name": self.m_name, "state": state, "lines": lines, "next": since + len(lines)}

    def wait(self, timeout_s: float | None = None) -> bool:
        """Wait up to ``timeout_s`` seconds (None: until it ends) for the job's thread; whether it has ended."""
        self._m_thread.join(timeout_s)
        return not self._m_thread.is_alive()


class CApp(CIslandCatalogue):
    """What the window acts on: the settings, the open island's editor and what it is (``island``: kind, name,
    path, editable, buildable), the running job."""

    def __init__(
        self,
        settings_path: Path | None = None,
        games: tuple[str, ...] | None = None,
        settings: CSettings | None = None,
    ) -> None:
        """``games``: the keys of the only games the application deals with, None for all;
        ``settings``: the record the suite's other apps share (read from ``settings_path`` when not given)."""
        unknown = [game for game in games or () if game not in installs.GAMES]
        if unknown:
            raise ValueError(f"unknown games {unknown}: {', '.join(installs.GAMES)}")
        self.m_games = tuple(games) if games else None
        self.m_settings_path = settings_path or settings_file()
        self.m_settings = settings or CSettings.load(self.m_settings_path)
        apply_hardware(self.m_settings.hardware)  # "Hardware acceleration": read when a window makes its 3D device
        nativegpu.want(self.m_settings.native_shaders)  # View > Rendering > The game's shipped shaders
        self.m_editor: islandeditor.CIslandEditor | None = None
        self.m_island: dict | None = None
        self.m_job: CJob | None = None
        self._m_game117: tuple[str, object] | None = None  # (install folder, its Anno117Files)
        self._m_trees: dict[str, tuple[str, object]] = {}  # game key -> (folder, its archives' tree)
        self.m_trash: modswap.Trash = modswap.recycle  # where a replaced build or install goes

    def wants(self, key: str) -> bool:
        """Whether the game (``anno117``, ``anno1800``, ...) is part of this application."""
        return self.m_games is None or key in self.m_games

    def problems(self) -> list[str]:
        """What keeps every tab from working (the workspace); a missing game shows on its own tab (``need``)."""
        return self.m_settings.problems(())

    def need(self, *keys: str, needs_mods_folder: bool = False) -> None:
        """A ``RuntimeError`` naming what an action lacks: the workspace, the games ``keys``, with ``mods`` 117's mods
        folder (an install into it)."""
        missing = [*self.problems(), *(message for key in keys if (message := self.m_settings.missing(key)))]
        missing += self.m_settings.mods_problems() if needs_mods_folder else []
        if missing:
            raise RuntimeError("; ".join(missing))

    # paths
    @property
    def workspace(self) -> Path:
        return Path(self.m_settings.workspace)

    @property
    def mod_dir(self) -> Path:
        return self.workspace / MOD_ID

    @property
    def edits_dir(self) -> Path:
        """The open project's island edits (the workspace's when no project is open)."""
        return project.folder_of(self.m_settings) / project.ISLAND_EDITS

    @property
    def cache_dir(self) -> Path:
        return self.workspace / "editor-cache"

    # the games' files
    def tree2070(self):
        """2070's files; the patch9.rda the editor wrote is left out, so the editor and a build start from the game's."""
        return archivedelivery.tree2070(Path(self.m_settings.anno2070))

    def game117(self):
        """The 117 install's files (``CAnno117Files``), read once per folder."""
        from ..sources.anno117 import CAnno117Files

        if self._m_game117 is None or self._m_game117[0] != self.m_settings.anno117:
            self._m_game117 = (self.m_settings.anno117, CAnno117Files(Path(self.m_settings.anno117)))
        return self._m_game117[1]

    def files(self, mod: Path | None = None) -> CFiles:
        """117's files with a mod folder in front (the workspace's built islands unless ``mod`` says otherwise; a mod
        asked for by folder is read with its archives too)."""
        return CFiles(self.game117(), self.mod_dir if mod is None else mod, include_archives=mod is not None)

    def mod_islands(self, mod: Path) -> list[str]:
        """The islands (game paths of ``.a7m`` files) a mod folder holds, loose or packed in one of its archives; the
        build's ``_base`` copies, which only carry what the editor draws edits on, are left out."""
        return [
            game_path
            for game_path in self.files(Path(mod)).mod_paths(".a7m")
            if not Path(game_path).stem.endswith("_base")
        ]

    def project_islands(self) -> list[str]:
        """The islands (game paths of ``.a7m`` files) of the mod the open project imported: the base its edit files are
        made against (``projectpart.base_folder``); the build's ``_base`` copies are left out."""
        root = self.edits_dir / "anno117"
        if not root.is_dir():
            return []
        found = CFiles(self.game117(), root).mod_paths(".a7m")
        return [game_path for game_path in found if not Path(game_path).stem.endswith("_base")]

    def open_project_island(self, path: str) -> dict:
        """An island of the imported mod as the project holds it: that file is the base, the edit file
        (``island-edits/117_<island>.json``) holds what is changed, and nothing is changed by opening it."""
        files = CFiles(self.game117(), self.edits_dir / "anno117")
        path = path.replace("\\", "/")
        edits = self.edits_dir / a7m_edit117.edit_name(path)
        return self._open117(files, path, edits, "anno117", Path(path).stem, is_buildable=True, meta={"source": path})

    def game_tree(self, key: str):
        """The archives of another game's install (``installs``), read once per folder."""
        folder = getattr(self.m_settings, key)
        if key not in self._m_trees or self._m_trees[key][0] != folder:
            replace = None
            if key in OLD_ENGINE:  # an archive the editor wrote is read as the game's own
                replace = oldenginebuild.replacement(
                    key, Path(folder), archivedelivery.old_engine_backups(self.m_settings, key)
                )
            self._m_trees[key] = (folder, installs.GAMES[key].tree(Path(folder), replace))
        return self._m_trees[key][1]

    # settings
    def set_settings(self, data: dict) -> None:
        known = {name: value for name, value in data.items() if name in CSettings.__dataclass_fields__}
        for name, value in known.items():  # all checked before any is set
            if not isinstance(value, type(getattr(self.m_settings, name))):
                raise ValueError(
                    f"setting {name}: expected {type(getattr(self.m_settings, name)).__name__}, got {value!r}"
                )
        for name, value in known.items():
            setattr(self.m_settings, name, value)
        self.m_settings.save(self.m_settings_path)
        self._m_game117 = None

    def save_layout(self, layout: dict) -> None:
        """Keep the window's layout (``{}`` forgets it) next to the settings, for the next run."""
        self.m_settings.layout = layout
        self.m_settings.save(self.m_settings_path)

    # opening an island
    def open(self, kind: str, name: str) -> dict:
        """Open an island in the editor: a built one (``built``), a vanilla 117 one (``anno117``, 117
        mode) or a 2070 one for 2070 itself (``anno2070``: its name in ``ISLANDS_2070``, or its path), or
        another game's (its key). A 2070 island to build, or a new one, is built first (``build``).
        Returns what is open (``island``)."""
        if not self.wants(kind) and kind in (*installs.GAMES, "anno117", "anno2070", "built"):
            raise ValueError(tr("islands.application_only_opens_islands", games=", ".join(self.m_games or ())))
        if kind == "anno2070":
            return self._open2070(name)
        if kind in installs.GAMES and kind not in NATIVE:
            return self._open_other(kind, name)
        if kind == "built":
            stem = f"a2070_{name}"
            files = self.files()
            base = f"{POOL}/{stem}_base/{stem}_base.a7m"
            path = base if files.where(base) else f"{POOL}/{stem}/{stem}.a7m"
            return self._open117(files, path, self.edits_dir / f"{stem}.json", kind, name, is_buildable=True)
        if kind == "anno117" and blankisland.grid_of(name):
            return self.open_blank117(blankisland.grid_of(name))
        if kind == "anno117":  # Anno 117 editing: edited in place, saved by build117
            edits = self.edits_dir / a7m_edit117.edit_name(name)
            return self._open117(self.files(), name, edits, kind, name, is_buildable=True, meta={"source": name})
        raise ValueError(tr("islands.cannot_open_island_build_first", kind=repr(kind)))

    def open_path(self, island: str, mod: Path | None = None, edits: Path | None = None) -> dict:
        """An island file by its game path, in a mod folder (one ``scripts/island_test.py`` built, say) or the
        game: the island build's ``_base`` copy when the mod has one (the edits draw on top), the edit file
        ``edits`` (default: the checkout's island-edits/<island>.json, as ``island_test.py`` reads it). Such an
        island is built by ``island_test.py``, not by the app."""
        files = self.files(mod)
        island = island.replace("\\", "/")
        stem = Path(island).stem
        base = f"{island.rsplit('/', 2)[0]}/{stem}_base/{stem}_base.a7m"
        if files.where(base):
            island = base
        if edits is None:
            build = builder_for("anno2070", "anno117")
            edits = (build.default_edits_dir() if build else self.edits_dir) / f"{stem}.json"
        return self._open117(files, island, edits, "file", stem, is_buildable=False)

    def _open117(
        self, files: CFiles, path: str, edits: Path, kind: str, name: str, is_buildable: bool, meta: dict | None = None
    ) -> dict:
        """A 117 island (built or vanilla) with its edit file; the mod's assets.xml names the models of slots
        and trees, the 2070 install (when found) fills the library."""
        assets = files.m_mod_root / "data/base/config/export/assets.xml" if files.m_mod_root else None
        mod_assets = assets.read_text(encoding="utf-8") if assets and assets.is_file() else ""
        tree = self.tree2070() if is_2070(Path(self.m_settings.anno2070)) else None
        editor = a7m_edit117.CVanillaEditor if kind == "anno117" else islandeditor.CIslandEditor
        self.m_editor = editor(files, path, self.cache_dir, mod_assets, edits, tree)
        self.m_editor.m_edit_meta = dict(meta or {})
        if kind == "anno117":
            self.m_editor.m_other_games = self.material_games("anno117")
        self.m_island = {"kind": kind, "name": name, "path": path, "editable": True, "buildable": is_buildable}
        return self.m_island

    def _open2070(self, name: str) -> dict:
        from .formats.isd import edit as isd_edit

        tree = isd_blank.with_new_islands(self.tree2070(), self.edits_dir, isd_edit.EDIT_PREFIX)
        source = name if name.endswith(".isd") else self.islands2070()[0].get(name, f"{ISLANDS_2070}{name}.isd")
        if not tree.exists(source):
            raise FileNotFoundError(tr("islands.no_2070_island", source=source))
        edits = self.edits_dir / edit_file2070(source)
        self.m_editor = isd_edit.CIsland2070Editor(tree, source, self.cache_dir, edits)
        self.m_island = {
            "kind": "anno2070",
            "name": Path(source).stem,
            "path": source,
            "editable": True,
            "buildable": True,
        }
        return self.m_island

    def open_blank2070(self, size_tiles: int) -> dict:
        """A new 2070 island from scratch (``formats.isd.blank``) for Anno 2070 editing: its edit file names it on the
        first open and is kept after, the island being the mapper's own from then on."""
        edits = self.edits_dir / edit_file2070(isd_blank.source(size_tiles))
        if not edits.is_file():
            islandedits.save(edits, isd_blank.new_edits(size_tiles))
        return self._open2070(isd_blank.name(size_tiles))

    def _open_other(self, key: str, path: str) -> dict:
        """Another game's island (``installs``, ``gameviewers``): edited into an edit file of its own (the
        native file stays untouched; ``BUILDABLE`` says whose edits a build reads)."""
        game = installs.GAMES[key]
        if game.editor is None:
            raise ValueError(tr("islands.no_editor_reads_islands_yet", game=game.name))
        tree = self.game_tree(key)
        if not tree.exists(path):
            raise FileNotFoundError(tr("islands.no_game_island_path", game=game.name, path=path))
        edits = self.edits_dir / f"{installs.GAMES[key].edit_prefix}{Path(path).stem}.json"
        self.m_editor = viewer_class(game.editor)(game, tree, path, self.cache_dir / key, edits)
        if key == "anno1800":
            self.m_editor.m_other_games = self.material_games(key)
        self.m_island = {
            "kind": key, "name": Path(path).stem, "path": path, "editable": True, "buildable": key in BUILDABLE
        }  # fmt: skip
        return self.m_island

    def open_blank117(self, grid_tiles: int) -> dict:
        """A new 117 island from scratch (``blankisland``): the vanilla island of ``grid`` tiles, cleared and
        reshaped by the edit file made on the first open (kept after: the island is the mapper's from then on)."""
        source, name = blankisland.donor(grid_tiles), blankisland.name(grid_tiles)
        edits = self.edits_dir / f"{a7m_edit117.EDIT_PREFIX}{name}.json"
        meta = {"source": source, "target": "new", "name": name, "clear": True}
        opened_island = self._open117(self.files(), source, edits, "anno117", name, is_buildable=True, meta=meta)
        if not edits.is_file():
            corners = self.m_editor.m_view.height_map[::2, ::2] / HEIGHT_UNITS  # the game's map has 2 samples a tile
            islandedits.save(edits, blankisland.blank_edits(corners, grid_tiles, self.m_editor.forest()["tiles"]))
        return opened_island

    def open_blank(self, key: str, path: str, is_shaped: bool = False) -> dict:
        """A new blank island of 1800, 1404 or 1701: the game's island ``path`` with its objects (and props, rivers)
        taken off at the build (``"clear"`` in its edit file), its terrain and coast kept. ``is_shaped``: its ground is
        also reshaped into a plateau island (``blankisland``) when the edit file holds no heights yet - after that the
        ground is the mapper's own. 2205 has no such island: its sessions are whole maps (island-editor "New islands")."""
        if key not in BLANKABLE:
            raise ValueError(tr("islands.no_blank_islands", game=installs.GAMES[key].name))
        edits = self.edits_dir / f"{installs.GAMES[key].edit_prefix}{Path(path).stem}.json"
        islandedits.save(edits, {**islandedits.load(edits), "source": path, "game": key, "clear": True})
        opened_island = self._open_other(key, path)
        if key in PLAIN_GROUND and not islandedits.load(edits).get("paint"):
            self._plain_ground(key, edits)
        if is_shaped and not islandedits.load(edits).get("heights"):
            corners, size_tiles = self._corner_heights(key)
            shape = blankisland.blank_edits(corners, size_tiles, source=path)
            islandedits.save(edits, {**islandedits.load(edits), "heights": shape["heights"]})
        return opened_island

    def _plain_ground(self, key: str, edits: Path) -> None:
        """A blank island's ground made one material, the donor's most common (``blankisland.plain_ground``), so the
        donor island's own painting does not show; the island is left as it is when its ground cannot be read."""
        try:
            ground = self.m_editor.ground()
            material = blankisland.dominant_material(ground, self.m_editor.ground_weights())
            size_tiles = self._corner_heights(key)[1]
        except (OSError, KeyError, ValueError, AttributeError, NotImplementedError):
            return
        if material is not None:
            islandedits.save(
                edits, {**islandedits.load(edits), "paint": blankisland.plain_ground(size_tiles, material)}
            )

    def _corner_heights(self, key: str) -> tuple[np.ndarray, int]:
        """The open island's corner heights in the view's units (one a tile corner) and its size in tiles."""
        view = getattr(self.m_editor, "m_view", None)
        if view is not None:  # a new-engine island: two samples a tile
            heights = view.heights()
            step = (heights.shape[0] - 1) // view.size
            return heights[::step, ::step], view.size
        island_data = self.m_editor.m_isl
        scale = 1 / isd1701.TILE if key == "anno1701" else 1.0
        return np.asarray(island_data.heightmap(), np.float64) * scale, island_data.m_width

    # jobs
    def _start(self, name: str, work) -> CJob:
        if self.m_job and self.m_job.m_state == "running":
            raise RuntimeError(tr("islands.name_still_running", name=self.m_job.m_name))
        self.m_job = CJob(name, work)
        return self.m_job

    def convert(self, source_game: str, island: str, target_game: str) -> CJob:
        """Build an island of one game for another, as a background job (only 2070 for 117 exists)."""
        if builder_for(source_game, target_game) is None:
            note = (
                ""
                if features.extensions(features.BUILDERS)
                else tr("islands.building_islands_state", state=tr(features.NOT_IN_EDITION))
            )
            raise ValueError(
                tr(
                    "islands.no_island_build_yet",
                    source=source_game,
                    target=target_game,
                    note=note,
                )
            )
        if source_game == "anno2070" and (reason := self.unconvertible(island)):
            raise ValueError(tr("islands.island_cannot_built_reason", island=island, reason=reason))
        return self.build(Path(island).stem.lower() if island.endswith(".isd") else island, install_enabled=False)

    def unconvertible(self, island: str) -> str | None:
        """Why a 2070 island (its list name, or path) has no island build; None when it has one or is a new island."""
        names = self.islands2070()[0] if not self.m_settings.missing("anno2070") else {}
        path = names.get(island) or (island if island.endswith(".isd") else None)
        if path is None:
            return None  # a bare name the pool holds, or a blank island: the build looks it up itself
        build = builder_for("anno2070", "anno117")
        if build is None:
            return tr(features.NOT_IN_EDITION)
        return isd_islandlist.convertible([*names.values(), path], build.island_dirs).get(path)

    def build(self, island: str, install_enabled: bool = False) -> CJob:
        """Build the mod for ``island`` (a 2070 name, or ``new<SIZE>``) with its edit file, in the
        background; then install it into the mods folder if asked."""
        if not re.fullmatch(r"new\d+|[a-z0-9_]+", island):
            raise ValueError(f"not an island name: {island!r}")
        self.need("anno2070", "anno117", needs_mods_folder=install_enabled)

        builder = builder_for("anno2070", "anno117")
        if builder is None:
            raise RuntimeError(tr("islands.building_2070_islands", state=tr(features.NOT_IN_EDITION)))

        def work(job: CJob):
            builder.run(
                extspecs.SBuildRequest(
                    source_folder=Path(self.m_settings.anno2070),
                    anno117=Path(self.m_settings.anno117),
                    out=self.mod_dir,
                    island=island,
                    edits_dir=self.edits_dir,
                    mod_id=MOD_ID,
                    mod_name=MOD_NAME,
                    log=job.log,
                )
            )
            job.log(tr("islands.built_island_into_folder", island=island, folder=self.mod_dir))
            if install_enabled:
                self._install(job)
            return island

        return self._start(tr("islands.build_island", island=island), work)

    def build117(self, install_enabled: bool = False) -> CJob:
        """Anno 117 editing: write every edited 117 island into its mod (``formats.a7m.edit117``), then install it."""
        self.need("anno117", needs_mods_folder=install_enabled)
        out = self.workspace / MOD_ID_117

        def work(job: CJob):
            files = CFiles(self.game117())  # the vanilla islands, nothing of the 2070 mod
            others = self.material_games("anno117")
            written = a7m_edit117.build(
                files, self.edits_dir, out, MOD_ID_117, MOD_NAME_117, job.log, others=others, trash=self.m_trash
            )
            job.log(tr("islands.n_islands_into_folder", n=len(written), folder=out))
            if install_enabled:
                self._copy_mod(job, out, MOD_ID_117)
            return written

        return self._start(tr("islands.build_117_islands"), work)

    def material_games(self, key: str) -> dict:
        """The other games of ``key``'s engine generation whose installs are set, as their files (``othermaterials``
        reads their ground materials for a palette)."""
        from ..islands.regions import GENERATION
        from .formats.a7m.reader import CGameFiles

        out = {}
        for other in (game for game in GENERATION if game != key):
            if not installs.GAMES[other].is_install(Path(getattr(self.m_settings, other, "") or ".")):
                continue
            out[other] = CFiles(self.game117()) if other == "anno117" else CFiles(CGameFiles(self.game_tree(other)))
        return out

    def port_side(self, key: str):
        """A game as ``islandconvert`` reads it (117's files, the others' archives), with its folder."""
        from .islandconvert import CGameSide

        root = Path(getattr(self.m_settings, key, "") or ".")
        if key == "anno117":
            return CGameSide(key, CFiles(self.game117()), self.cache_dir, root)
        if not installs.GAMES[key].is_install(root):
            raise RuntimeError(tr("islands.not_found_set_folder_games", game=installs.GAMES[key].name))
        return CGameSide(key, self.game_tree(key), self.cache_dir, root)

    def convert_region(
        self,
        key: str,
        path: str,
        target: str,
        install_enabled: bool = False,
        fit: str = "",
        reskin_enabled: bool = False,
    ) -> CJob:
        """An island of ``key`` made into an island of the region ``target`` (``islandconvert``), written as a new island
        into the workspace's port mod of the target game; with ``install``, copied into that game's mods folder.
        ``fit``: how the game places it (``islandconvert.SRequest.fit``); ``reskin``: keep the models only the source
        has, drawn with the target's ground (``portreskin``)."""
        from . import islandconvert, slotlinks

        game = target.split(":", 1)[0]
        mods = Path(self.m_settings.mods if game == "anno117" else self.m_settings.mods1800 or ".")
        if install_enabled and not mods.is_absolute():
            raise RuntimeError(tr("islands.set_target_games_mods_folder"))
        out = self.workspace / PORT_MODS[game]

        def work(job: CJob):
            # the games' indexes are opened here, in the job: reading an install is seconds of work
            job.log(tr("islands.opening_game", game=key))
            source_side = self.port_side(key)
            target_side = source_side if game == key else self.port_side(game)
            links = slotlinks.load(slotlinks.path_for(self.workspace, key, game))
            request = islandconvert.SRequest(
                key, path, target, fit=fit, reskin=reskin_enabled, slot_links=tuple(links.items())
            )
            done = islandconvert.convert(source_side, target_side, request, log=job.log)
            job.log(islandconvert.summary(done))
            written = islandconvert.write_mod(out, PORT_MODS[game], PORT_MODS[game], game, [done], self.m_trash)
            job.log(tr("islands.n_islands_into_folder", n=len(written), folder=out))
            if install_enabled:
                self._copy_mod(job, out, PORT_MODS[game], mods)
            return written

        return self._start(tr("islands.port_island", island=Path(path).stem), work)

    def build1800(self, install_enabled: bool = False) -> CJob:
        """Anno 1800 editing: write every edited 1800 island into a mod that replaces it (``formats.a7m.edit1800``) in the
        workspace; with ``install``, copy the mod into 1800's mods folder (the setting ``mods1800``)."""
        mods = Path(self.m_settings.mods1800) if self.m_settings.mods1800.strip() else None
        if install_enabled and (mods is None or not mods.is_absolute()):
            raise RuntimeError(tr("islands.set_anno_1800s_mods_folder"))
        if not installs.GAMES["anno1800"].is_install(Path(self.m_settings.anno1800)):
            raise RuntimeError(tr("islands.anno_1800_not_found_set"))
        out = self.workspace / MOD_ID_1800

        def work(job: CJob):
            from .formats.a7m.reader import CGameFiles

            written = a7m_edit1800.build(
                CGameFiles(self.game_tree("anno1800")),
                self.edits_dir,
                out,
                MOD_ID_1800,
                MOD_NAME_1800,
                job.log,
                self.material_games("anno1800"),
                trash=self.m_trash,
            )
            job.log(tr("islands.n_islands_into_folder", n=len(written), folder=out))
            if install_enabled:
                self._copy_mod(job, out, MOD_ID_1800, mods)
            return written

        return self._start(tr("islands.build_1800_islands"), work)

    def build_archives(self, key: str, install_enabled: bool = False) -> CJob:
        """Every edited island (2205: session) of a game that loads archives into the archives it loads
        (``archivedelivery.ISLAND_DELIVERIES``), in the workspace; with ``install``, into the game (its own files are
        kept first)."""
        delivery = archivedelivery.ISLAND_DELIVERIES[key]
        if install_enabled:
            delivery.install_ready(self.m_settings)
        delivery.ready(self.m_settings)
        out = self.workspace / delivery.out_name

        def work(job: CJob):
            tree = delivery.tree(self.m_settings)
            files, written = delivery.edited(tree, self.edits_dir, job.log)
            archives = delivery.package(files, written, self.m_settings, job.log, tree)
            if out.exists():
                shutil.rmtree(out)  # the build's own folder: its name is the record's
            if not (written and archives):
                job.log(tr("islands.no_edit_changes_nothing_written", what=delivery.what_one))
                return []
            for relative_path, data in archives.items():
                (out / relative_path).parent.mkdir(parents=True, exist_ok=True)
                (out / relative_path).write_bytes(data)
            job.log(tr("islands.n_what_into_folder", n=len(written), what=tr(delivery.what), folder=out))
            if install_enabled:
                job.log("")  # the last place to stop: the install into the game runs whole
                job.note(delivery.install(out, self.m_settings, job.note))
            return written

        return self._start(tr("islands.build_game_what", game=key.removeprefix("anno"), what=tr(delivery.what)), work)

    def installed(self, key: str) -> bool:
        """Whether an archive of the editor's lies in the game (a restore is then offered)."""
        delivery = archivedelivery.ISLAND_DELIVERIES.get(key)
        return delivery is not None and self.wants(key) and delivery.is_ours(self.m_settings)

    def restore(self, key: str) -> CJob:
        """Give a game its own files back and remove the editor's archives (``SIslandDelivery.restore``)."""
        delivery = archivedelivery.ISLAND_DELIVERIES[key]
        return self._start(
            tr("islands.restore_game", game=key.removeprefix("anno")),
            lambda job: delivery.restore(self.m_settings, job.note),
        )

    def install(self) -> CJob:
        self.need(needs_mods_folder=True)
        return self._start(tr("islands.install"), self._install)

    def _install(self, job: CJob) -> str:
        return self._copy_mod(job, self.mod_dir, MOD_ID)

    def _copy_mod(self, job: CJob, source: Path, mod_id: str, mods: Path | None = None) -> str:
        """Copy a built mod into the mods folder (117's by default) through a staging folder beside it
        (``modswap.place_folder``): a copy that fails leaves the installed mod as it was, the old copy goes to
        ``trash``, and a folder that holds anything but this mod is never replaced."""
        if not (source / "modinfo.json").is_file():
            raise RuntimeError(tr("islands.nothing_built_yet"))
        target = (mods or Path(self.m_settings.mods)) / modswap.check_folder_name(mod_id)
        if not target.is_absolute() or target.name != mod_id:
            raise RuntimeError(tr("islands.refusing_replace_set_mods_folder", target=target))
        modswap.check_holds(target, mod_id)
        before = linkannounce.snapshot(target)
        ignore = shutil.ignore_patterns("*_base", "*.water.json", "tmp.a7m")
        for note in modswap.place_folder(source, target, self.m_trash, ignore=ignore):
            job.note(note)  # the mod is in: the game is told of it whatever the person asks now
        job.note(tr("islands.installed_start_new_game_see", target=target))
        job.note(linkannounce.announce_install(mod_id, target, before).text)
        return str(target)
