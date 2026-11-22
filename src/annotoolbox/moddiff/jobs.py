"""A question of the Mod diff app as a record and its answer: ``SJob`` names the mode, the game and what to compare;
``run_job`` finds the mods (the installed ones by ModID through the mod manager, or a folder) and returns the
``Report`` over a game opened by ``assetgame.open_installed``. All of it is meant to run off the window's thread.
``docs/mod-diff.md`` "The six questions".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..assetedit.assetgame import CGame
from ..infra.i18n import tr
from ..modloader import activations
from ..modloader.activations import ACTIVATIONS  # in 1800 it stands for the profile
from ..modloader.mods import GAME_NUMBER, CMod, find_mods, read_mod
from ..modloader.resolve import resolve
from ..modloader.zipmods import ZIP, CZipMod, read_zip_mods
from ..modmgr.manager import GAMES as MOD_GAMES
from ..modmgr.manager import CModManager, for_settings
from ..suite import links
from . import compare
from .model import SReport

MOD, PROFILE = links.MODE_MOD, links.MODE_PROFILE
VERSIONS, TWO, ARCHIVE, ARCHIVES = "versions", "mods", "archive", "archives"
MODES = (MOD, PROFILE, VERSIONS, TWO, ARCHIVE, ARCHIVES)
ARCHIVE_GAMES = (
    "anno2070",
    "anno1404",
    "anno1701",
    "anno2205",
    *MOD_GAMES,
)  # whole-file mods; a loose folder of 117 too
GAMES = {MOD: tuple(MOD_GAMES), PROFILE: tuple(MOD_GAMES), VERSIONS: tuple(MOD_GAMES), TWO: tuple(MOD_GAMES),
         ARCHIVE: ARCHIVE_GAMES, ARCHIVES: ARCHIVE_GAMES}  # fmt: skip
ZIP_GAMES = ("anno1800",)  # the games whose loader reads mods from a .zip in place (``modloader.zipmods``)
Progress = Callable[[str], None] | None


@dataclass(frozen=True)
class SJob:
    """What to compare. ``a`` and ``b``: a ModID of the game's mods folders or a mod folder (``archive``: ``a`` is the
    archive file or folder); ``profile``: the profile of ``profile``."""

    mode: str
    game: str
    first: str = ""
    second: str = ""
    profile: str = ""


def warm(game: CGame) -> None:
    """Make the game's file tree now (the first lookup builds it, half a minute on 117), so a comparison starts at once."""
    game.m_files.exists("data")


def find_mod(ref: str, manager: CModManager | None, game: str) -> CMod:
    """The mod ``ref`` names: a folder, or a ModID the manager has found."""
    if not ref.strip():
        raise ValueError(tr("moddiff.no_mod_chosen"))
    card = manager.m_catalog.by_id(ref) if manager is not None and not Path(ref).is_absolute() else None
    if (
        card is not None and card.mod is not None
    ):  # an id is an id, though a folder of that name lies in the working dir
        return card.mod
    if Path(ref).is_dir():
        mod, why = read_mod(Path(ref), GAME_NUMBER.get(game))
        if mod is None:
            raise ValueError(why)
        return mod
    if game in ZIP_GAMES:
        return _zip_mod(ref, manager, game)
    raise ValueError(tr("moddiff.no_installed_mod", id=ref))


def _zip_mod(ref: str, manager: CModManager | None, game: str) -> CMod:
    """A mod of a ``.zip`` (1800 reads them): the archive's path, or a ModID of a zip in the mods folders."""
    if Path(ref).is_file() and Path(ref).suffix.lower() == ZIP:
        found, notes = read_zip_mods(Path(ref), GAME_NUMBER.get(game))
        if not found:
            raise ValueError("; ".join(notes))
        return found[0]
    roots = [root.path for root in manager.m_roots] if manager is not None else []
    for mod in find_mods(roots, GAME_NUMBER.get(game), allow_zips=True)[0]:
        if mod.m_mod_id == ref:
            return mod
    raise ValueError(tr("moddiff.no_installed_mod", id=ref))


def run_job(job: SJob, game: CGame, manager: CModManager | None = None, progress: Progress = None) -> SReport:
    """The answer to ``job`` on the opened ``game``; ``manager`` (the game's ``CModManager``, scanned) finds the mods."""
    if job.mode == ARCHIVE:
        return compare.archive_against_game(game, Path(job.first), progress)
    if job.mode == ARCHIVES:
        return compare.two_archives(game, Path(job.first), Path(job.second), progress)
    search = [root.path for root in manager.m_roots] if manager is not None else None
    if job.mode == PROFILE:
        if manager is None:
            raise ValueError(tr("moddiff.no_mod_manager_game"))
        order, notes = profile_order(manager, job.profile)
        return compare.profile_conflicts(game, order, manager.m_mods_dir, job.profile, progress, notes)
    first = find_mod(job.first, manager, job.game)
    if job.mode == MOD:
        return compare.mod_against_game(game, first, search, progress)
    second = find_mod(job.second, manager, job.game)
    if job.mode == VERSIONS:
        return compare.two_versions(game, first, second, search, progress)
    return compare.two_mods(game, first, second, search, progress)


def profile_names(manager: CModManager | None) -> list[str]:
    """What the profile list offers: 117's profiles; for 1800, which has none, the one entry that stands for its
    ``activations.json``."""
    if manager is None:
        return []
    return [ACTIVATIONS] if manager.m_game == "anno1800" else manager.profile_names()


def profile_order(manager: CModManager, profile: str) -> tuple[list[CMod], list[str]]:
    """The mods that are on in a profile, in the loader's order, and what to tell about how the set was made. 1800
    has no profile: every mod is on unless its folder's ``activations.json`` lists it (``modloader.activations``)."""
    if manager.m_game != "anno1800":
        return manager.plan(profile).order, []
    roots = [root.path for root in manager.m_roots]
    found, _skipped = find_mods(roots, manager.m_anno, allow_zips=True)
    switched_on = activations.switched_on(found, roots)
    notes = [tr("moddiff.1800_switches_mod_unless_folders", file=ACTIVATIONS)]
    zipped = sum(1 for mod in switched_on if isinstance(mod, CZipMod))
    if zipped:
        notes.append(tr("moddiff.mods_read_place_zip_files", n=zipped))
    return resolve(switched_on, [mod for mod in found if mod not in switched_on]).order, notes


def manager_for(settings, key: str) -> CModManager | None:
    """The scanned mod manager of a game with a mod loader (117, 1800), None for the others."""
    if key not in MOD_GAMES:
        return None
    manager = for_settings(settings, key)
    manager.scan()
    return manager
