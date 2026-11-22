"""The mod manager's logic behind one object, with no window: ``CModManager`` for one game's mods folder.

It scans the folders the loader searches, reads and writes the profiles, builds the load order and its problems,
reads the last start's log, installs and removes mods, and knows whether the game runs. The window
(``window.CModManagerWindow``) only shows what this returns and calls what this does; tests use it on a synthetic
mods folder. ``docs/mod-manager.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..infra.i18n import tr
from ..modloader import loadlog
from ..modloader.mods import GAME_NUMBER, loader_roots
from ..modloader.profilefile import CProfileText
from ..sources import gametree
from ..suite import linkannounce, livelink
from . import catalog, checks, gameguard, install
from .profiles import CProfileError, CProfileStore

GAMES = {key: gametree.GAMES[key] for key in ("anno117", "anno1800")}  # the games with a mod manager: title


class CModManager:
    """One game's mods: ``mods_dir`` is the Documents ``mods`` folder (profiles, ``profile.txt``, the log live there),
    ``game_dir`` the install (its ``mods`` folders are searched too), ``public_dir`` where mod.io keeps its mods."""

    def __init__(
        self,
        game: str,
        mods_dir: Path,
        game_dir: Path | None = None,
        public_dir: Path | None = None,
        cache: Path | None = None,
        lister: Callable[[], set[str]] | None = None,
        trash: install.Trash = install.recycle,
    ) -> None:
        if game not in GAMES:
            raise ValueError(f"no mod manager for {game}: one of {', '.join(GAMES)}")
        self.m_game, self.m_mods_dir = game, Path(mods_dir)
        self.m_game_dir = Path(game_dir) if game_dir else None
        self.m_cache, self.m_lister, self.m_trash = cache, lister, trash
        self.m_roots = loader_roots(game, self.m_game_dir, self.m_mods_dir, public_dir)
        self.m_anno = GAME_NUMBER.get(game)
        self.m_store = CProfileStore(self.m_mods_dir)
        self.m_catalog = catalog.SCatalog(self.m_roots)
        self.m_stamp: tuple | None = None  # the folders' change times at the last scan
        self.m_notice = ""  # the live link's sentence for the last removal
        self.m_deferred: tuple | None = None  # (background, say): announce off the window's thread, say it later

    @property
    def name(self) -> str:
        return GAMES[self.m_game]

    # the mods

    def _announce(self, call: Callable[[], linkannounce.SNotice]) -> str:
        """The sentence of ``call`` (an announcement); with a window attached (``deferred``) it is said by the window
        when the game has answered, and "" comes back."""
        if self.m_deferred is None:
            return call().text
        background, say = self.m_deferred
        linkannounce.announce_in_background(background, say, call)
        return ""

    def scan(self) -> catalog.SCatalog:
        self.m_stamp = self._stamp()
        self.m_catalog = catalog.scan(self.m_roots, self.m_anno)
        return self.m_catalog

    def _stamp(self) -> tuple:
        """The change times of the mods folders: an install or a removal changes the folder that holds the mod."""
        stamp = []
        for root in self.m_roots:
            try:
                stamp.append(root.path.stat().st_mtime_ns)
            except OSError:
                stamp.append(None)
        return tuple(stamp)

    def rescan_if_changed(self) -> bool:
        """Scan again when a mods folder changed since the last scan (a stat per folder, no listing); True if it did."""
        if self.m_stamp == self._stamp():
            return False
        self.scan()
        return True

    def install(
        self,
        source: Path,
        progress: Callable[[str], None] | None = None,
        placed: list[install.SInstalled] | None = None,
    ) -> list[install.SInstalled]:
        """A mod folder, a folder of mods or a zip into the mods folder (the Documents one). ``progress`` is told
        each step and may raise to stop; ``placed`` then holds the mods that are in (the game is told of them)."""
        found = [] if placed is None else placed
        try:
            install.install_path(source, self.m_mods_dir, self.m_anno, self.m_trash, progress, found)
        finally:
            for one in found:  # one announcement per mod; the whole folder counts as changed
                one.notice = linkannounce.announce_install(one.mod_id, one.folder, {}).text
        return found

    def remove(self, card: catalog.SModCard, announce: bool = True) -> str:
        """The mod folder of ``card`` to the recycle bin; the profiles keep its line (the game notes it as not installed).
        A window that moves the folder off its thread passes ``announce=False`` and calls ``announce_removal`` on its own."""
        if card.mod is not None and card.mod.m_parent is not None:
            raise install.CInstallError(tr("mods.nested_mod_part_mods_folder"))
        where = install.remove_mod(card.folder, [root.path for root in self.m_roots], self.m_trash)
        if announce:
            self.announce_removal(card)
        return where

    def announce_removal(self, card: catalog.SModCard) -> None:
        """Tell the running game that the mod of ``card`` is gone (``notice`` holds the sentence unless it is said later)."""
        self.m_notice = self._announce(lambda: linkannounce.announce((livelink.ASSETS, livelink.TEXTS), card.mod_id))

    # the profiles

    def profile_names(self) -> list[str]:
        return self.m_store.names()

    def active(self) -> str:
        return self.m_store.active()

    def read(self, name: str) -> CProfileText | None:
        """A profile's text; None for the empty name (no profile: no mods) or a profile whose file is missing."""
        if not name or not self.m_store.exists(name):
            return None
        return self.m_store.read(name)

    def set_mod(self, name: str, mod_id: str, is_on: bool) -> str:
        """Switch a mod on or off in a profile (its line changed, or added). Returns the live link's sentence for the
        status line: what the running game needs for it (nothing changed: "")."""
        text = self.m_store.read(name)
        if not text.set_state(mod_id, is_on):
            return ""
        self.m_store.save(name, text)
        return self._announce(lambda: linkannounce.announce((livelink.ASSETS, livelink.TEXTS), mod_id))

    def set_enable_new(self, name: str, is_on: bool) -> str:
        text = self.m_store.read(name)
        if not text.set_enable_new(is_on):
            return ""
        self.m_store.save(name, text)
        return self._announce(lambda: linkannounce.announce((livelink.ASSETS, livelink.TEXTS)))

    def create_profile(self, name: str, from_name: str = "", enable_new: bool = False) -> Path:
        """A new profile: a copy of ``from_name``, else an empty one (nothing listed; ``enable_new`` for
        ``EnableNewMods``)."""
        if from_name:
            return self.m_store.copy(from_name, name)
        return self.m_store.create(name, enable_new=enable_new)

    # the order and what is wrong

    def plan(self, name: str) -> checks.SPlan:
        return checks.plan_profile(self.m_catalog, self.read(name), name)

    def deep_check(self, name: str, progress: Callable[[str], None] | None = None) -> list[checks.SIssue]:
        """The slow checks (``checks.deep_check``) with the game's files when the install is known."""
        say = progress or (lambda _t: None)
        plan = self.plan(name)
        tree = None
        if self.m_game_dir is not None and self.m_game_dir.is_dir():
            say(tr("mods.opening_game_files", game=self.name))
            try:
                tree = gametree.open_game(self.m_game, self.m_game_dir, self.m_cache)
            except (OSError, ValueError, KeyError) as error:
                say(tr("mods.game_files_not_opened_patches_not_applied", error=error))
        read_base = None if tree is None else (lambda path: tree.read(path) if tree.exists(path) else None)
        has_base = None if tree is None else tree.exists
        return checks.deep_check(plan, self.m_mods_dir, read_base, has_base, progress, lint=self.m_anno is not None)

    # the last start

    def log(self) -> loadlog.SLoadLog | None:
        return loadlog.read_log(self.m_mods_dir)

    # the game

    def game_state(self) -> gameguard.SGameState:
        return gameguard.state(self.m_game, self.m_lister)

    def warning(self, editing: str) -> str:
        """What to say before a profile change while the game runs."""
        return gameguard.change_warning(self.game_state(), editing, self.active())


MODS_FIELD = {"anno117": "mods", "anno1800": "mods1800"}  # the setting of each game's Documents ``mods`` folder


def for_settings(settings, game: str, **kwargs) -> CModManager:
    """The manager of ``game`` for the suite's settings (``suite.settings.CSettings``): the install, the mods folder
    (the Documents folder's standard place when the setting is empty) and a cache inside the workspace."""
    mods = str(getattr(settings, MODS_FIELD[game], "") or "").strip() or str(gametree.user_dir(game) / "mods")
    installed = str(getattr(settings, game, "") or "").strip()
    workspace = str(getattr(settings, "workspace", "") or "").strip()
    cache = Path(workspace) / "mods-cache" / game if workspace else None
    return CModManager(
        game, Path(mods), Path(installed) if installed else None, catalog.public_folder(), cache, **kwargs
    )


__all__ = ["GAMES", "CModManager", "CProfileError", "for_settings"]
