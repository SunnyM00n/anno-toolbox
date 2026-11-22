"""The contract of an app someone else adds to the Toolbox: the shapes it declares and what the Toolbox gives it.

A Python app returns an ``SExternalApp`` (from an entry point of the group ``ENTRY_POINT_GROUP`` or from an apps
folder's manifest, ``suite.appplugins``); its page maker gets an ``CAppContext``. ``API_VERSION`` is the version of
this contract: an app declares the one it was written for and is refused outside ``OLDEST_API`` to ``API_VERSION``
(``refusal``). Stability promise, the kinds of external app and an example: ``docs/app-api.md``. Qt is imported only
when an app asks for the background runner or the status bar, so a part's ``render`` runs without a window.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..infra.i18n import tr
from ..sources import gametree
from . import project as projects
from .settings import CSettings

log = logging.getLogger(__name__)
API_VERSION = 1  # the contract below; raised for a change an older app cannot ignore (docs/app-api.md, "Stability")
OLDEST_API = 1  # the oldest app contract this Toolbox still takes
ENTRY_POINT_GROUP = "anno_toolbox.apps"
KEY_PATTERN = re.compile(r"[a-z][a-z0-9_-]{1,31}")  # an app's key: its settings entry, its project part, its folder
DEFAULT_ICON = "app_external"  # a key of ``suite.icons.ICONS``; an app may name another key or a picture file


def refusal(declared: object) -> str:
    """Why an app that declares ``declared`` as its ``api_version`` is refused; "" when this Toolbox takes it."""
    if isinstance(declared, bool) or not isinstance(declared, int):
        return tr("appapi.no_api_version")
    if declared > API_VERSION:
        return tr("appapi.api_too_new", declared=declared, current=API_VERSION)
    if declared < OLDEST_API:
        return tr("appapi.api_too_old", declared=declared, oldest=OLDEST_API)
    return ""


@dataclass(frozen=True)
class SAppPart:
    """An app's share of a project's mod (``suite.project.SPart``): ``has_work(project)`` says whether it holds
    anything for the project's game; ``render(project, context, log)`` returns the mod's files, game path -> bytes
    (a ``FileRef`` for a large file taken from where it is). A part that raises stops the build with its app's name."""

    has_work: Callable[[projects.CProject], bool]
    render: Callable[[projects.CProject, CAppContext, Callable[[str], None]], dict[str, Any]]


@dataclass(frozen=True)
class SExternalApp:
    """What a Python app declares. ``make(context)`` returns the page (a ``QWidget``), made when the app is first
    shown. ``games``: the games it can show; the page may answer them with ``open_game(key)`` and ``game_key()``
    (the suite's one game selection, docs/suite.md). ``links``: per ``suite.links`` name, ``handler(page, *args)``
    for what the app answers the others. ``icon``: a key of the suite's icons or the absolute path of a picture."""

    key: str
    title: str
    make: Callable[[CAppContext], Any]
    api_version: int  # the contract the app was written for (``refusal``); no default, an app says it
    summary: str = ""
    icon: str = DEFAULT_ICON
    games: tuple[str, ...] = ()
    window: bool = False  # a window of its own the switcher opens (``make`` returns it) instead of a page
    part: SAppPart | None = None
    links: dict[str, Callable[..., Any]] = field(default_factory=dict)


class CAppContext:
    """What the Toolbox gives an app: the settings, the open project, the game trees, the shared runner and status
    bar. Read the folders and trees through this, not from files of the Toolbox: they are what stays stable."""

    api_version = API_VERSION

    def __init__(self, key: str, settings: CSettings, settings_path: Path, shell: Any = None) -> None:
        self.key, self.settings, self.settings_path, self.shell = key, settings, settings_path, shell
        self._background: Any = None

    @property
    def language(self) -> str:
        return self.settings.language

    @property
    def mods_dir(self) -> Path | None:
        return Path(self.settings.mods) if self.settings.mods else None

    @property
    def selected_game(self) -> str:
        """The game every app of the suite is on ("" for none picked)."""
        return self.settings.game

    @property
    def project_folder(self) -> Path:
        """The open project's folder (the workspace when none is open); apps keep their work below it."""
        return projects.folder_of(self.settings)

    def project(self) -> projects.CProject:
        """The open project (its mod's identity, game and GUID range); raises ValueError for a file that does not read."""
        return projects.CProject.open(self.project_folder)

    @property
    def state(self) -> dict:
        """The app's own remembered state: a dict kept in the settings file under the app's key (``save_settings``)."""
        return self.settings.apps.setdefault(self.key, {})

    def save_settings(self) -> None:
        self.settings.save(self.settings_path)

    def game_dir(self, game: str) -> Path | None:
        """The install folder of ``game`` (a key of ``sources.gametree.GAMES``); None when none is set."""
        folder = getattr(self.settings, game, "") if game in gametree.GAMES else ""
        return Path(folder) if folder else None

    def open_game(self, game: str) -> gametree.CGameTree:
        """The game's files, read only, through the overlay the game itself uses (the same as every app's); raises
        ValueError when the game's folder is not set. Opening indexes the archives: do it in ``background``."""
        folder = self.game_dir(game)
        if folder is None:
            raise ValueError(tr("appapi.no_game_folder", game=gametree.GAMES.get(game, game)))
        return gametree.open_game(game, folder, self.settings.index_dir(game))

    @property
    def background(self):
        """The shared runner: ``background.run(work, callback)`` runs ``work`` off the window's thread and calls
        ``callback(result)`` back on it (``suite.background.CBackground``); needs the Toolbox's window."""
        if self._background is None:
            from .background import CBackground

            self._background = CBackground(self.shell)
        return self._background

    def show_app(self, key: str) -> bool:
        """Bring another app's page to the front (the keys are the switcher's: ``islands``, ``assets``, ...); False
        when there is no such page or no window."""
        return self.shell is not None and self.shell.show_app(key) is not None

    def say(self, text: str) -> None:
        """A line for the Toolbox's status bar (the log when there is no window)."""
        bar = self.shell.statusBar() if self.shell is not None else None
        if bar is not None:
            bar.showMessage(text)
        else:
            log.info("%s: %s", self.key, text)

    def tr(self, ident: str, **fields: object) -> str:
        """A text of the Toolbox's own language files, in the interface language; an app brings its own texts."""
        return tr(ident, **fields)
