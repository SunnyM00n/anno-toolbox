"""The suite's settings: the game folders (detected from the Ubisoft and Steam installs and the drives), the
workspace, the theme, the interface language, the window layouts, kept in one file in ``%LOCALAPPDATA%`` that every app reads.

``Settings`` is the record (``load`` fills an unset game folder from ``detect``, once a day), ``appdata`` the folder; the island
editor's ``editorapp.App`` and the asset editor take it from here. Plan: ``docs/tool-suite.md`` (C3).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..infra import i18n
from ..infra.buildcache import write_atomic
from ..infra.i18n import N_, tr
from ..othergames import installs
from ..paths import config_dir, local_data_dir
from ..sources import gametree

THEMES = ("system", "light", "dark")
SETTINGS_FILE = "editor-app.json"  # every app's settings, in ``settings_file()``


def appdata() -> Path:
    return local_data_dir()


def settings_file() -> Path:
    """The settings file of every app (the config folder: the data folder on Windows, ``~/.config`` on Linux)."""
    return config_dir() / SETTINGS_FILE


def registry_installs() -> list[Path]:
    """Install folders the Ubisoft launcher and Steam know of (Windows only)."""
    found: list[Path] = []
    try:
        import winreg
    except ImportError:
        return found
    for root, key in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Ubisoft\Launcher\Installs"),
    ):
        try:
            with winreg.OpenKey(root, key) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        with winreg.OpenKey(k, winreg.EnumKey(k, i)) as sub:
                            found.append(Path(winreg.QueryValueEx(sub, "InstallDir")[0]))
                    except OSError:
                        continue
        except OSError:
            continue
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = Path(winreg.QueryValueEx(k, "SteamPath")[0])
        library = steam / "steamapps" / "libraryfolders.vdf"
        roots = [steam]
        if library.is_file():
            roots += [
                Path(p.replace("\\\\", "\\"))
                for p in re.findall(r'"path"\s+"([^"]+)"', library.read_text("utf-8", "ignore"))
            ]
        for r in roots:
            found += anno_folders(r / "steamapps" / "common")
    except OSError:
        pass
    return found


def anno_folders(parent: Path) -> list[Path]:
    """The entries of ``parent`` named like an Anno game; none for a folder that is not there or cannot be listed
    (a drive without a disc, a folder of another user)."""
    try:
        return [p for p in parent.iterdir() if p.name.lower().startswith("anno")]
    except OSError:
        return []


def candidates() -> list[Path]:
    out = registry_installs()
    for drive in "CDEFGH":
        for parent in (
            "Ubisoft",
            "Games",
            "Program Files (x86)/Ubisoft/Ubisoft Game Launcher/games",
            "Program Files/Ubisoft",
        ):
            out += anno_folders(Path(f"{drive}:/") / parent)
    return out


def detect() -> dict[str, str]:
    """The game folders this machine has, as far as they can be found."""
    return {"mods": str(gametree.user_dir("anno117") / "mods"), **installs.detect(candidates())}


def is_2070(folder: Path) -> bool:
    return installs.GAMES["anno2070"].is_install(folder)


def installed(game: str, given: str = "") -> tuple[Settings, Path | None]:
    """The suite's settings and ``game``'s install folder for a command: ``given`` (its ``--folder``), else the
    settings'; None for a folder that is not there."""
    settings = Settings.load(settings_file())
    folder = given or getattr(settings, game, "")
    return settings, Path(folder) if folder and Path(folder).is_dir() else None


def _read(path: Path) -> dict:
    """The settings file's record ({} when there is none); one that does not read is renamed to
    ``<name>.unreadable-<time>`` (kept for the person to look at) and {} returned."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):  # ValueError: not JSON, or not UTF-8
        pass
    try:
        os.replace(path, path.with_name(f"{path.name}.unreadable-{datetime.now():%Y%m%d-%H%M%S}"))
    except OSError:
        pass  # left where it is: the next save writes over it
    return {}


@dataclass
class Settings:
    anno117: str = ""
    anno2070: str = ""
    mods: str = ""
    anno1404: str = ""  # the other games (installs.GAMES), each optional
    anno1701: str = ""
    anno1800: str = ""
    anno2205: str = ""
    game: str = ""  # the game every app of the suite is on (``suite.gamesync``); empty: none picked yet
    mods1800: str = ""  # 1800's mods folder (Documents/Anno 1800/mods): "Build & install" copies there, set by hand
    patch2205: str = ""  # the 2205 install folder "Build & install" writes into (its maindata/file.db): set by hand
    theme: str = "system"  # system | light | dark (``theme``)
    language: str = "en"  # the interface language, a language of ``i18n.languages()``; read at the start of an app
    renderer: str = ""  # the 3D views' device (``render.rhiview.BACKENDS``); empty: the default
    rendering: dict = field(default_factory=dict)  # per game: its look, backend and frame switches (render.nativelook)
    quality: dict = field(default_factory=dict)  # per game (render.quality.GAMES): its preset, or a Custom record
    hardware: bool = True  # hardware acceleration; off: a software adapter from the next start (render.nativelook)
    check_updates: bool = True  # look for a newer release at the start, once a day (``suite.updates``)
    last_update_check: str = ""  # the ISO date of the last check that got an answer
    helper_seen: str = ""  # SHA256 of the link helper the last checked release carried (``updates.helper_check``)
    last_game_scan: str = ""  # the ISO date of the last look for the games on this machine (``find_games``)
    setup_done: bool = False  # the first-run setup page was answered (``suite.setupchoices``)
    shortcut_start_menu: bool = True  # the Start menu entry the setup page keeps (``suite.shortcuts``)
    shortcut_desktop: bool = True  # the desktop shortcut the setup page keeps
    workspace: str = field(default_factory=lambda: str(appdata() / "workspace"))
    project: str = ""  # the open project's folder (``suite.project``); empty: the workspace is the project
    own_reserve: list = field(default_factory=list)  # the user's own GUID reserve (``suite.guidranges``), made once
    known_projects: list = field(
        default_factory=list
    )  # the project folders opened or saved, to keep their GUID ranges apart
    layout: dict = field(default_factory=dict)  # the window's geometry and dock state, base64 (workbench)
    apps: dict = field(default_factory=dict)  # per app of the suite: its own remembered state (``shell``)

    @classmethod
    def load(cls, path: Path, today: date | None = None) -> Settings:
        """The settings in ``path``; a file that does not read (cut short, not JSON) is set aside beside it and the
        defaults are used, so the apps still start. ``today``: a test's date for the daily look for the games."""
        s = cls()
        for key, value in _read(Path(path)).items():
            # a value of another kind (a file edited by hand: "apps": null) would stop an app much later
            if key in cls.__dataclass_fields__ and type(value) is type(getattr(s, key)):
                setattr(s, key, value)
        s.find_games(today or date.today())
        s.language = i18n.use(s.language)  # every app loads its settings first: that is where the language is chosen
        return s

    def find_games(self, today: date | None = None) -> None:
        """Fill the unset game folders from ``detect``. Few people have every game, and the look over the registry
        and the drives waits on a drive that sleeps or has no disc: with ``today`` it is made once a day at most
        (every start asks), without it now (the setup page, asked for by the person)."""
        if all(getattr(self, k) for k in ("mods", *installs.GAMES)):
            return
        if today is not None and self.last_game_scan == today.isoformat():
            return
        for k, v in detect().items():
            if not getattr(self, k) and v:
                setattr(self, k, v)
        self.last_game_scan = (today or date.today()).isoformat()

    def index_dir(self, key: str) -> Path:
        """Where the apps keep the index of one installed game's archives (one for every app of the suite)."""
        return Path(self.workspace or appdata()) / "game-index" / key

    def save(self, path: Path) -> None:
        """Whole or not at all: a crash while saving keeps the file as it was."""
        write_atomic(Path(path), json.dumps(asdict(self), indent=1).encode("utf-8"))

    def problems(self, games: tuple[str, ...] | None = None) -> list[str]:
        """What is missing for the games in use (``games``: their keys, None for 117 and 2070 with the others)."""
        out = [m for key in (("anno117", "anno2070") if games is None else games) if (m := self.missing(key))]
        out += _not_absolute(N_("common.workspace"), self.workspace)
        return out + (self.mods_problems() if games is None else [])

    def missing(self, key: str) -> str:
        """Why the game ``key``'s folder is not its install; "" when it is."""
        game = installs.GAMES[key]
        if game.is_install(Path(getattr(self, key))):
            return ""
        looking = tr("common.and").join([game.exes[0], *game.needs])
        return tr("common.not_found_looking_folder", game=game.name, files=looking)

    def mods_problems(self) -> list[str]:
        """What keeps an install into 117's mods folder from running (its Documents folder may not be there yet)."""
        out = _not_absolute(N_("common.mods_folder"), self.mods)
        if not out and not Path(self.mods).parent.is_dir():
            out.append(tr("common.mods_folders_parent_not_exist"))
        return out


def _not_absolute(label: str, value: str) -> list[str]:
    if value.strip() and Path(value).is_absolute():
        return []
    return [tr("common.must_set_absolute_path_written", folder=tr(label))]
