"""The mods a save needs, against the mods installed for its game (the mod loader's folders, ``modloader.mods``).
``check(mods, installed)`` compares; ``installed_mods(settings, game)`` reads what is installed (``docs/save-editor.md``,
"The window").
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..modloader import mods as loader
from ..sources import gametree
from .records import SModEntry

INSTALLED, OTHER, MISSING = "installed", "other version", "missing"
MOD_GAMES = ("anno117", "anno1800")
MODS_SETTING = {"anno117": "mods", "anno1800": "mods1800"}  # the setting of each game's Documents ``mods`` folder


@dataclass(frozen=True)
class SNeed:
    mod_id: str
    version: str  # as the save has it
    state: str
    installed: str = ""  # the installed version
    folder: str = ""


def installed_mods(settings, game: str) -> list[loader.CMod]:
    """Every mod the game's loader would find: the install's ``mods`` folders and the Documents one."""
    if game not in MOD_GAMES:
        return []
    documents = str(getattr(settings, MODS_SETTING[game], "") or "").strip() or str(gametree.user_dir(game) / "mods")
    install = str(getattr(settings, game, "") or "").strip()
    roots = loader.loader_roots(game, Path(install) if install else None, Path(documents))
    found, _notes = loader.find_mods([root.path for root in roots], loader.GAME_NUMBER.get(game))
    return found


def check(mods: list[SModEntry], installed: list[loader.CMod]) -> list[SNeed]:
    """Each mod of the save: installed in its version, in another one, or missing."""
    by_id = {mod.m_mod_id: mod for mod in installed}
    out = []
    for entry in mods:
        have = by_id.get(entry.mod_id)
        if have is None:
            out.append(SNeed(entry.mod_id, entry.version, MISSING))
            continue
        state = INSTALLED if have.m_version == entry.version else OTHER
        out.append(SNeed(entry.mod_id, entry.version, state, have.m_version, str(have.m_folder)))
    return out
