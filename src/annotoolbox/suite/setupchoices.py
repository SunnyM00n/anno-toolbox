"""The setup page's choices without the window (``setuppage`` shows them): which start asks, what is asked, what is kept.

The page opens on the first start after an installation (``installevents`` leaves the mark) and, for a program that
has no installer (a source run), when there was no settings file yet. Its answers go into the suite's settings;
the games' folders are the ones ``Settings.load`` already found, each confirmed or changed here. ``docs/installer.md``,
"First-run setup".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..infra import i18n
from ..othergames import installs
from .settings import Settings


@dataclass
class Choices:
    language: str
    check_updates: bool
    start_menu: bool
    desktop: bool
    folders: dict[str, str] = field(default_factory=dict)  # game key -> its install folder ("" for none)
    remove_old: tuple[str, ...] = ()  # product codes of old installs to uninstall


def pending(settings: Settings, mark: bool, settings_existed: bool) -> bool:
    """Whether the setup page opens at this start: never once it was answered; yes after the installer's first start
    (``mark``) and for a person who never had a settings file."""
    return not settings.setup_done and (mark or not settings_existed)


def game_keys() -> tuple[str, ...]:
    return tuple(installs.GAMES)


def folder_problem(key: str, folder: str) -> str:
    """Why ``folder`` is not game ``key``'s install; "" when it is, or when it is empty (the game is optional)."""
    folder = folder.strip()
    if not folder or installs.GAMES[key].is_install(Path(folder)):
        return ""
    return Settings(**{key: folder}).missing(key)


def defaults(settings: Settings, carried: tuple[bool, bool] | None = None) -> Choices:
    """What the page starts from: the settings as they are (``Settings.load`` has found the games), the shortcut
    choice of the old install when there was one, else both ticked."""
    start_menu, desktop = carried if carried is not None else (settings.shortcut_start_menu, settings.shortcut_desktop)
    return Choices(
        settings.language,
        settings.check_updates,
        start_menu,
        desktop,
        {key: getattr(settings, key, "") for key in game_keys()},
    )


def apply(settings: Settings, choices: Choices) -> list[str]:
    """Write the answers into ``settings`` and mark the page answered. A folder that is not that game's install is
    left as it was and named in the result."""
    problems = []
    for key, folder in choices.folders.items():
        if text := folder_problem(key, folder):
            problems.append(text)
        else:
            setattr(settings, key, folder.strip())
    settings.language = i18n.use(choices.language) if choices.language in i18n.languages() else settings.language
    settings.check_updates = choices.check_updates
    settings.shortcut_start_menu, settings.shortcut_desktop = choices.start_menu, choices.desktop
    settings.setup_done = True
    return problems
