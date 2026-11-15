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
from ..islands.islandview import Files
from ..modloader import modswap
from ..othergames import installs, isd1701
from ..render.nativelook import apply_hardware
from ..suite import extspecs, features, linkannounce, project
from ..suite.settings import SETTINGS_FILE, Settings, appdata, is_2070
from . import archivedelivery, blankisland, islandeditor, oldenginebuild
from .formats.a7m import edit117 as a7m_edit117
from .formats.a7m import edit1800 as a7m_edit1800
from .formats.isd import blank as isd_blank
from .formats.isd import islandlist as isd_islandlist
from .islandcatalogue import BUILDABLE, ISLANDS_2070, NATIVE, POOL, IslandCatalogue, builder_for

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
VIEWER_CLASSES = {  # ``installs.Game.editor`` -> (module, class) of the editor that opens its islands
    "a7m": ("a7mviewer", "A7mViewer"),
    "a6m": ("a7mviewer", "A6mViewer"),
    "isd": ("gameviewers", "IsdViewer"),
    "isd1701": ("gameviewers", "Isd1701Viewer"),
}


def viewer_class(kind: str):
    """The editor class of another game's islands (``installs.Game.editor``), imported when asked for: the 2070-format
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


class Job:
    """One background task (a build, an install) with its log."""

    def __init__(self, name: str, work) -> None:
        self.name = name
        self.lines: list[str] = []
        self.state = "running"
        self.result = None
        self._thread = threading.Thread(target=self._run, args=(work,), daemon=True)
        self._thread.start()

    def log(self, line: str) -> None:
        self.lines.append(str(line))

    def _run(self, work) -> None:
        try:
            self.result = work(self)
            self.state = "done"
        except (Exception, SystemExit) as exc:  # the window shows what went wrong; the app stays up
            self.lines += traceback.format_exc().splitlines()
            self.log(tr("failed: {error}", error=exc))
            self.state = "failed"

    def status(self, since: int = 0) -> dict:
        return {"name": self.name, "state": self.state, "lines": self.lines[since:], "next": len(self.lines)}


class App(IslandCatalogue):
    """What the window acts on: the settings, the open island's editor and what it is (``island``: kind, name,
    path, editable, buildable), the running job."""

    def __init__(
        self,
        settings_path: Path | None = None,
        games: tuple[str, ...] | None = None,
        settings: Settings | None = None,
    ) -> None:
        """``games``: the keys of the only games the application deals with, None for all;
        ``settings``: the record the suite's other apps share (read from ``settings_path`` when not given)."""
        unknown = [g for g in games or () if g not in installs.GAMES]
        if unknown:
            raise ValueError(f"unknown games {unknown}: {', '.join(installs.GAMES)}")
        self.games = tuple(games) if games else None
        self.settings_path = settings_path or appdata() / SETTINGS_FILE
        self.settings = settings or Settings.load(self.settings_path)
        apply_hardware(self.settings.hardware)  # "Hardware acceleration": read when a window makes its 3D device
        self.editor: islandeditor.IslandEditor | None = None
        self.island: dict | None = None
        self.job: Job | None = None
        self._game117: tuple[str, object] | None = None  # (install folder, its Anno117Files)
        self._trees: dict[str, tuple[str, object]] = {}  # game key -> (folder, its archives' tree)
        self.trash: modswap.Trash = modswap.recycle  # where a replaced build or install goes

    def wants(self, key: str) -> bool:
        """Whether the game (``anno117``, ``anno1800``, ...) is part of this application."""
        return self.games is None or key in self.games

    def problems(self) -> list[str]:
        """What keeps every tab from working (the workspace); a missing game shows on its own tab (``need``)."""
        return self.settings.problems(())

    def need(self, *keys: str, mods: bool = False) -> None:
        """A ``RuntimeError`` naming what an action lacks: the workspace, the games ``keys``, with ``mods`` 117's mods
        folder (an install into it)."""
        missing = [*self.problems(), *(m for key in keys if (m := self.settings.missing(key)))]
        missing += self.settings.mods_problems() if mods else []
        if missing:
            raise RuntimeError("; ".join(missing))

    # paths
    @property
    def workspace(self) -> Path:
        return Path(self.settings.workspace)

    @property
    def mod_dir(self) -> Path:
        return self.workspace / MOD_ID

    @property
    def edits_dir(self) -> Path:
        """The open project's island edits (the workspace's when no project is open)."""
        return project.folder_of(self.settings) / project.ISLAND_EDITS

    @property
    def cache_dir(self) -> Path:
        return self.workspace / "editor-cache"

    # the games' files
    def tree2070(self):
        """2070's files; the patch9.rda the editor wrote is left out, so the editor and a build start from the game's."""
        return archivedelivery.tree2070(Path(self.settings.anno2070))

    def game117(self):
        """The 117 install's files (``Anno117Files``), read once per folder."""
        from ..sources.anno117 import Anno117Files

        if self._game117 is None or self._game117[0] != self.settings.anno117:
            self._game117 = (self.settings.anno117, Anno117Files(Path(self.settings.anno117)))
        return self._game117[1]

    def files(self, mod: Path | None = None) -> Files:
        """117's files with a mod folder in front (the workspace's built islands unless ``mod`` says otherwise; a mod
        asked for by folder is read with its archives too)."""
        return Files(self.game117(), self.mod_dir if mod is None else mod, archives=mod is not None)

    def mod_islands(self, mod: Path) -> list[str]:
        """The islands (game paths of ``.a7m`` files) a mod folder holds, loose or packed in one of its archives; the
        build's ``_base`` copies, which only carry what the editor draws edits on, are left out."""
        return [p for p in self.files(Path(mod)).mod_paths(".a7m") if not Path(p).stem.endswith("_base")]

    def game_tree(self, key: str):
        """The archives of another game's install (``installs``), read once per folder."""
        folder = getattr(self.settings, key)
        if key not in self._trees or self._trees[key][0] != folder:
            replace = None
            if key in OLD_ENGINE:  # an archive the editor wrote is read as the game's own
                replace = oldenginebuild.replacement(
                    key, Path(folder), archivedelivery.old_engine_backups(self.settings, key)
                )
            self._trees[key] = (folder, installs.GAMES[key].tree(Path(folder), replace))
        return self._trees[key][1]

    # settings
    def set_settings(self, data: dict) -> None:
        known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
        for k, v in known.items():  # all checked before any is set
            if not isinstance(v, type(getattr(self.settings, k))):
                raise ValueError(f"setting {k}: expected {type(getattr(self.settings, k)).__name__}, got {v!r}")
        for k, v in known.items():
            setattr(self.settings, k, v)
        self.settings.save(self.settings_path)
        self._game117 = None

    def save_layout(self, layout: dict) -> None:
        """Keep the window's layout (``{}`` forgets it) next to the settings, for the next run."""
        self.settings.layout = layout
        self.settings.save(self.settings_path)

    # opening an island
    def open(self, kind: str, name: str) -> dict:
        """Open an island in the editor: a built one (``built``), a vanilla 117 one (``anno117``, 117
        mode) or a 2070 one for 2070 itself (``anno2070``: its name in ``ISLANDS_2070``, or its path), or
        another game's (its key). A 2070 island to build, or a new one, is built first (``build``).
        Returns what is open (``island``)."""
        if not self.wants(kind) and kind in (*installs.GAMES, "anno117", "anno2070", "built"):
            raise ValueError(tr("this application only opens {games} islands", games=", ".join(self.games or ())))
        if kind == "anno2070":
            return self._open2070(name)
        if kind in installs.GAMES and kind not in NATIVE:
            return self._open_other(kind, name)
        if kind == "built":
            stem = f"a2070_{name}"
            files = self.files()
            base = f"{POOL}/{stem}_base/{stem}_base.a7m"
            path = base if files.where(base) else f"{POOL}/{stem}/{stem}.a7m"
            return self._open117(files, path, self.edits_dir / f"{stem}.json", kind, name, buildable=True)
        if kind == "anno117" and blankisland.grid_of(name):
            return self.open_blank117(blankisland.grid_of(name))
        if kind == "anno117":  # Anno 117 editing: edited in place, saved by build117
            edits = self.edits_dir / a7m_edit117.edit_name(name)
            return self._open117(self.files(), name, edits, kind, name, buildable=True, meta={"source": name})
        raise ValueError(tr("cannot open a {kind} island; build it first", kind=repr(kind)))

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
        return self._open117(files, island, edits, "file", stem, buildable=False)

    def _open117(
        self, files: Files, path: str, edits: Path, kind: str, name: str, buildable: bool, meta: dict | None = None
    ) -> dict:
        """A 117 island (built or vanilla) with its edit file; the mod's assets.xml names the models of slots
        and trees, the 2070 install (when found) fills the library."""
        assets = files.mod_root / "data/base/config/export/assets.xml" if files.mod_root else None
        mod_assets = assets.read_text(encoding="utf-8") if assets and assets.is_file() else ""
        tree = self.tree2070() if is_2070(Path(self.settings.anno2070)) else None
        editor = a7m_edit117.VanillaEditor if kind == "anno117" else islandeditor.IslandEditor
        self.editor = editor(files, path, self.cache_dir, mod_assets, edits, tree)
        self.editor.edit_meta = dict(meta or {})
        if kind == "anno117":
            self.editor.other_games = self.material_games("anno117")
        self.island = {"kind": kind, "name": name, "path": path, "editable": True, "buildable": buildable}
        return self.island

    def _open2070(self, name: str) -> dict:
        from .formats.isd import edit as isd_edit

        tree = isd_blank.with_new_islands(self.tree2070(), self.edits_dir, isd_edit.EDIT_PREFIX)
        source = name if name.endswith(".isd") else self.islands2070()[0].get(name, f"{ISLANDS_2070}{name}.isd")
        if not tree.exists(source):
            raise FileNotFoundError(tr("no 2070 island {source}", source=source))
        edits = self.edits_dir / edit_file2070(source)
        self.editor = isd_edit.Island2070Editor(tree, source, self.cache_dir, edits)
        self.island = {
            "kind": "anno2070",
            "name": Path(source).stem,
            "path": source,
            "editable": True,
            "buildable": True,
        }
        return self.island

    def open_blank2070(self, size: int) -> dict:
        """A new 2070 island from scratch (``formats.isd.blank``) for Anno 2070 editing: its edit file names it on the
        first open and is kept after, the island being the mapper's own from then on."""
        edits = self.edits_dir / edit_file2070(isd_blank.source(size))
        if not edits.is_file():
            islandedits.save(edits, isd_blank.new_edits(size))
        return self._open2070(isd_blank.name(size))

    def _open_other(self, key: str, path: str) -> dict:
        """Another game's island (``installs``, ``gameviewers``): edited into an edit file of its own (the
        native file stays untouched; ``BUILDABLE`` says whose edits a build reads)."""
        game = installs.GAMES[key]
        if game.editor is None:
            raise ValueError(tr("{game}: no editor reads its islands yet", game=game.name))
        tree = self.game_tree(key)
        if not tree.exists(path):
            raise FileNotFoundError(tr("no {game} island {path}", game=game.name, path=path))
        edits = self.edits_dir / f"{installs.GAMES[key].edit_prefix}{Path(path).stem}.json"
        self.editor = viewer_class(game.editor)(game, tree, path, self.cache_dir / key, edits)
        if key == "anno1800":
            self.editor.other_games = self.material_games(key)
        self.island = {
            "kind": key, "name": Path(path).stem, "path": path, "editable": True, "buildable": key in BUILDABLE
        }  # fmt: skip
        return self.island

    def open_blank117(self, grid: int) -> dict:
        """A new 117 island from scratch (``blankisland``): the vanilla island of ``grid`` tiles, cleared and
        reshaped by the edit file made on the first open (kept after: the island is the mapper's from then on)."""
        source, name = blankisland.donor(grid), blankisland.name(grid)
        edits = self.edits_dir / f"{a7m_edit117.EDIT_PREFIX}{name}.json"
        meta = {"source": source, "target": "new", "name": name, "clear": True}
        info = self._open117(self.files(), source, edits, "anno117", name, buildable=True, meta=meta)
        if not edits.is_file():
            corners = self.editor.view.hm[::2, ::2] / HEIGHT_UNITS  # the game's map has 2 samples a tile
            islandedits.save(edits, blankisland.blank_edits(corners, grid, self.editor.forest()["tiles"]))
        return info

    def open_blank(self, key: str, path: str, shaped: bool = False) -> dict:
        """A new blank island of 1800, 1404 or 1701: the game's island ``path`` with its objects (and props, rivers)
        taken off at the build (``"clear"`` in its edit file), its terrain and coast kept. ``shaped``: its ground is
        also reshaped into a plateau island (``blankisland``) when the edit file holds no heights yet - after that the
        ground is the mapper's own. 2205 has no such island: its sessions are whole maps (island-editor "New islands")."""
        if key not in BLANKABLE:
            raise ValueError(tr("{game}: no blank islands", game=installs.GAMES[key].name))
        edits = self.edits_dir / f"{installs.GAMES[key].edit_prefix}{Path(path).stem}.json"
        islandedits.save(edits, {**islandedits.load(edits), "source": path, "game": key, "clear": True})
        info = self._open_other(key, path)
        if key in PLAIN_GROUND and not islandedits.load(edits).get("paint"):
            self._plain_ground(key, edits)
        if shaped and not islandedits.load(edits).get("heights"):
            corners, size = self._corner_heights(key)
            shape = blankisland.blank_edits(corners, size, source=path)
            islandedits.save(edits, {**islandedits.load(edits), "heights": shape["heights"]})
        return info

    def _plain_ground(self, key: str, edits: Path) -> None:
        """A blank island's ground made one material, the donor's most common (``blankisland.plain_ground``), so the
        donor island's own painting does not show; the island is left as it is when its ground cannot be read."""
        try:
            ground = self.editor.ground()
            material = blankisland.dominant_material(ground, self.editor.ground_weights())
            size = self._corner_heights(key)[1]
        except (OSError, KeyError, ValueError, AttributeError, NotImplementedError):
            return
        if material is not None:
            islandedits.save(edits, {**islandedits.load(edits), "paint": blankisland.plain_ground(size, material)})

    def _corner_heights(self, key: str) -> tuple[np.ndarray, int]:
        """The open island's corner heights in the view's units (one a tile corner) and its size in tiles."""
        view = getattr(self.editor, "view", None)
        if view is not None:  # a new-engine island: two samples a tile
            heights = view.heights()
            step = (heights.shape[0] - 1) // view.size
            return heights[::step, ::step], view.size
        isl = self.editor.isl
        scale = 1 / isd1701.TILE if key == "anno1701" else 1.0
        return np.asarray(isl.heightmap(), np.float64) * scale, isl.width

    # jobs
    def _start(self, name: str, work) -> Job:
        if self.job and self.job.state == "running":
            raise RuntimeError(tr("{name} is still running", name=self.job.name))
        self.job = Job(name, work)
        return self.job

    def convert(self, source_game: str, island: str, target_game: str) -> Job:
        """Build an island of one game for another, as a background job (only 2070 for 117 exists)."""
        if builder_for(source_game, target_game) is None:
            note = (
                ""
                if features.extensions(features.BUILDERS)
                else tr(" (building islands is {state})", state=tr(features.NOT_IN_EDITION))
            )
            raise ValueError(
                tr(
                    "no island build from {source} to {target} yet{note}",
                    source=source_game,
                    target=target_game,
                    note=note,
                )
            )
        if source_game == "anno2070" and (reason := self.unconvertible(island)):
            raise ValueError(tr("{island} cannot be built: {reason}", island=island, reason=reason))
        return self.build(Path(island).stem.lower() if island.endswith(".isd") else island, install=False)

    def unconvertible(self, island: str) -> str | None:
        """Why a 2070 island (its list name, or path) has no island build; None when it has one or is a new island."""
        names = self.islands2070()[0] if not self.settings.missing("anno2070") else {}
        path = names.get(island) or (island if island.endswith(".isd") else None)
        if path is None:
            return None  # a bare name the pool holds, or a blank island: the build looks it up itself
        build = builder_for("anno2070", "anno117")
        if build is None:
            return tr(features.NOT_IN_EDITION)
        return isd_islandlist.convertible([*names.values(), path], build.island_dirs).get(path)

    def build(self, island: str, install: bool = False) -> Job:
        """Build the mod for ``island`` (a 2070 name, or ``new<SIZE>``) with its edit file, in the
        background; then install it into the mods folder if asked."""
        if not re.fullmatch(r"new\d+|[a-z0-9_]+", island):
            raise ValueError(f"not an island name: {island!r}")
        self.need("anno2070", "anno117", mods=install)

        builder = builder_for("anno2070", "anno117")
        if builder is None:
            raise RuntimeError(tr("building 2070 islands is {state}", state=tr(features.NOT_IN_EDITION)))

        def work(job: Job):
            builder.run(
                extspecs.BuildRequest(
                    src=Path(self.settings.anno2070),
                    anno117=Path(self.settings.anno117),
                    out=self.mod_dir,
                    island=island,
                    edits_dir=self.edits_dir,
                    mod_id=MOD_ID,
                    mod_name=MOD_NAME,
                    log=job.log,
                )
            )
            job.log(tr("built {island} into {folder}", island=island, folder=self.mod_dir))
            if install:
                self._install(job)
            return island

        return self._start(tr("build {island}", island=island), work)

    def build117(self, install: bool = False) -> Job:
        """Anno 117 editing: write every edited 117 island into its mod (``formats.a7m.edit117``), then install it."""
        self.need("anno117", mods=install)
        out = self.workspace / MOD_ID_117

        def work(job: Job):
            files = Files(self.game117())  # the vanilla islands, nothing of the 2070 mod
            others = self.material_games("anno117")
            written = a7m_edit117.build(
                files, self.edits_dir, out, MOD_ID_117, MOD_NAME_117, job.log, others=others, trash=self.trash
            )
            job.log(tr("{n} islands into {folder}", n=len(written), folder=out))
            if install:
                self._copy_mod(job, out, MOD_ID_117)
            return written

        return self._start(tr("build 117 islands"), work)

    def material_games(self, key: str) -> dict:
        """The other games of ``key``'s engine generation whose installs are set, as their files (``othermaterials``
        reads their ground materials for a palette)."""
        from ..islands.regions import GENERATION
        from .formats.a7m.reader import GameFiles

        out = {}
        for other in (g for g in GENERATION if g != key):
            if not installs.GAMES[other].is_install(Path(getattr(self.settings, other, "") or ".")):
                continue
            out[other] = Files(self.game117()) if other == "anno117" else Files(GameFiles(self.game_tree(other)))
        return out

    def port_side(self, key: str):
        """A game as ``islandconvert`` reads it (117's files, the others' archives), with its folder."""
        from .islandconvert import GameSide

        root = Path(getattr(self.settings, key, "") or ".")
        if key == "anno117":
            return GameSide(key, Files(self.game117()), self.cache_dir, root)
        if not installs.GAMES[key].is_install(root):
            raise RuntimeError(
                tr("{game} not found: set its folder (Games and folders)", game=installs.GAMES[key].name)
            )
        return GameSide(key, self.game_tree(key), self.cache_dir, root)

    def convert_region(self, key: str, path: str, target: str, install: bool = False) -> Job:
        """An island of ``key`` made into an island of the region ``target`` (``islandconvert``), written as a new island
        into the workspace's port mod of the target game; with ``install``, copied into that game's mods folder."""
        from . import islandconvert

        game = target.split(":", 1)[0]
        mods = Path(self.settings.mods if game == "anno117" else self.settings.mods1800 or ".")
        if install and not mods.is_absolute():
            raise RuntimeError(
                tr("set the target game's mods folder (Games and folders) to an absolute path to install")
            )
        out = self.workspace / PORT_MODS[game]

        def work(job: Job):
            # the games' indexes are opened here, in the job: reading an install is seconds of work
            job.log(tr("opening {game}", game=key))
            src = self.port_side(key)
            dst = src if game == key else self.port_side(game)
            done = islandconvert.convert(src, dst, islandconvert.Request(key, path, target), log=job.log)
            job.log(islandconvert.summary(done))
            written = islandconvert.write_mod(out, PORT_MODS[game], PORT_MODS[game], game, [done], self.trash)
            job.log(tr("{n} islands into {folder}", n=len(written), folder=out))
            if install:
                self._copy_mod(job, out, PORT_MODS[game], mods)
            return written

        return self._start(tr("port {island}", island=Path(path).stem), work)

    def build1800(self, install: bool = False) -> Job:
        """Anno 1800 editing: write every edited 1800 island into a mod that replaces it (``formats.a7m.edit1800``) in the
        workspace; with ``install``, copy the mod into 1800's mods folder (the setting ``mods1800``)."""
        mods = Path(self.settings.mods1800) if self.settings.mods1800.strip() else None
        if install and (mods is None or not mods.is_absolute()):
            raise RuntimeError(tr("set Anno 1800's mods folder (Games and folders) to an absolute path to install"))
        if not installs.GAMES["anno1800"].is_install(Path(self.settings.anno1800)):
            raise RuntimeError(tr("Anno 1800 not found: set its folder (Games and folders)"))
        out = self.workspace / MOD_ID_1800

        def work(job: Job):
            from .formats.a7m.reader import GameFiles

            written = a7m_edit1800.build(
                GameFiles(self.game_tree("anno1800")),
                self.edits_dir,
                out,
                MOD_ID_1800,
                MOD_NAME_1800,
                job.log,
                self.material_games("anno1800"),
            )
            job.log(tr("{n} islands into {folder}", n=len(written), folder=out))
            if install:
                self._copy_mod(job, out, MOD_ID_1800, mods)
            return written

        return self._start(tr("build 1800 islands"), work)

    def build_archives(self, key: str, install: bool = False) -> Job:
        """Every edited island (2205: session) of a game that loads archives into the archives it loads
        (``archivedelivery.ISLAND_DELIVERIES``), in the workspace; with ``install``, into the game (its own files are
        kept first)."""
        d = archivedelivery.ISLAND_DELIVERIES[key]
        if install:
            d.install_ready(self.settings)
        d.ready(self.settings)
        out = self.workspace / d.out_name

        def work(job: Job):
            tree = d.tree(self.settings)
            files, written = d.edited(tree, self.edits_dir, job.log)
            archives = d.package(files, written, self.settings, job.log, tree)
            if out.exists():
                shutil.rmtree(out)  # the build's own folder: its name is the record's
            if not (written and archives):
                job.log(tr("no edit changes a {what}: nothing written", what=d.what_one))
                return []
            for rel, data in archives.items():
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                (out / rel).write_bytes(data)
            job.log(tr("{n} {what} into {folder}", n=len(written), what=tr(d.what), folder=out))
            if install:
                job.log(d.install(out, self.settings, job.log))
            return written

        return self._start(tr("build {game} {what}", game=key.removeprefix("anno"), what=tr(d.what)), work)

    def installed(self, key: str) -> bool:
        """Whether an archive of the editor's lies in the game (a restore is then offered)."""
        d = archivedelivery.ISLAND_DELIVERIES.get(key)
        return d is not None and self.wants(key) and d.is_ours(self.settings)

    def restore(self, key: str) -> Job:
        """Give a game its own files back and remove the editor's archives (``IslandDelivery.restore``)."""
        d = archivedelivery.ISLAND_DELIVERIES[key]
        return self._start(
            tr("restore {game}", game=key.removeprefix("anno")), lambda job: d.restore(self.settings, job.log)
        )

    def install(self) -> Job:
        self.need(mods=True)
        return self._start(tr("install"), self._install)

    def _install(self, job: Job) -> str:
        return self._copy_mod(job, self.mod_dir, MOD_ID)

    def _copy_mod(self, job: Job, source: Path, mod_id: str, mods: Path | None = None) -> str:
        """Copy a built mod into the mods folder (117's by default) through a staging folder beside it
        (``modswap.place_folder``): a copy that fails leaves the installed mod as it was, the old copy goes to
        ``trash``, and a folder that holds anything but this mod is never replaced."""
        if not (source / "modinfo.json").is_file():
            raise RuntimeError(tr("nothing built yet"))
        target = (mods or Path(self.settings.mods)) / modswap.check_folder_name(mod_id)
        if not target.is_absolute() or target.name != mod_id:
            raise RuntimeError(
                tr("refusing to replace {target}: set the mods folder to an absolute path", target=target)
            )
        modswap.check_holds(target, mod_id)
        before = linkannounce.snapshot(target)
        ignore = shutil.ignore_patterns("*_base", "*.water.json", "tmp.a7m")
        for note in modswap.place_folder(source, target, self.trash, ignore=ignore):
            job.log(note)
        job.log(tr("installed into {target} - start a new game to see changed assets", target=target))
        job.log(linkannounce.announce_install(mod_id, target, before).text)
        return str(target)
