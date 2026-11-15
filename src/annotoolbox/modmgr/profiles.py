"""The profiles of one mods folder: ``mods/profiles/<name>.txt`` and the ``mods/profile.txt`` that names the active one.

``ProfileStore(mods_dir)`` lists, reads, saves, creates, copies, renames and deletes profiles and switches the active
one; a failure is a ``ProfileError`` with a sentence for the person. Files are written whole beside the old one and
moved over it, so a game that reads the folder never sees half a file. ``docs/mod-manager.md`` "Profiles".
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..infra.i18n import tr
from ..modloader.mods import PROFILE_FILE
from ..modloader.profilefile import DEFAULT, ActiveFile, ProfileText, new_active, new_profile

FOLDER = "profiles"
NAME_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}
)
MAX_NAME = 64


class ProfileError(ValueError):
    """A profile operation that cannot be done; the message says why."""


def name_problem(name: str) -> str:
    """Why ``name`` cannot be a profile's file name ("" when it can)."""
    if not name or name != name.strip():
        return tr("the name is empty or starts or ends with a space")
    if len(name) > MAX_NAME:
        return tr("the name is longer than {n} characters", n=MAX_NAME)
    if NAME_FORBIDDEN.search(name):
        return tr('the name has a character a file name cannot hold (< > : " / \\ | ? *)')
    if name.endswith(".") or name.split(".")[0].lower() in RESERVED:
        return tr("the name is one Windows does not take for a file")
    return ""


class ProfileStore:
    def __init__(self, mods_dir: Path) -> None:
        self.mods_dir = Path(mods_dir)

    @property
    def folder(self) -> Path:
        return self.mods_dir / FOLDER

    def path(self, name: str) -> Path:
        return self.folder / f"{name}.txt"

    def names(self) -> list[str]:
        """The profiles, by name without case."""
        if not self.folder.is_dir():
            return []
        return sorted((p.stem for p in self.folder.glob("*.txt") if p.is_file()), key=str.lower)

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def _find(self, name: str) -> str | None:
        return next((n for n in self.names() if n.lower() == name.lower()), None)

    # the profile files

    def read(self, name: str) -> ProfileText:
        try:
            return ProfileText.from_bytes(self.path(name).read_bytes())
        except OSError as exc:
            raise ProfileError(
                tr("the profile {name} cannot be read: {reason}", name=name, reason=exc.strerror or exc)
            ) from exc

    def save(self, name: str, text: ProfileText) -> None:
        _write(self.path(name), text.to_bytes())

    def create(self, name: str, on: list[str] = (), off: list[str] = (), enable_new: bool = False) -> Path:
        """A new profile in the game's layout, ``on`` and ``off`` as its mod lines."""
        self._free(name)
        self.save(name, new_profile(name, list(on), list(off), enable_new))
        return self.path(name)

    def copy(self, source: str, name: str) -> Path:
        """``source`` as a new profile ``name`` (its lines kept; its display name too when it was the file's name)."""
        self._free(name)
        text = self.read(source)
        if text.display_name == source:
            text.set_display_name(name)
        self.save(name, text)
        return self.path(name)

    def rename(self, old: str, name: str) -> Path:
        """``old`` under a new name; the active profile follows."""
        if old == name:
            return self.path(old)
        if old.lower() == name.lower():  # only the case changes
            if name_problem(name):
                raise ProfileError(name_problem(name))
        else:
            self._free(name)
        text = self.read(old)
        if text.display_name == old:
            text.set_display_name(name)
        was_active = self.active().lower() == old.lower()
        self.save(old, text)
        try:
            os.replace(self.path(old), self.path(name))
        except OSError as exc:
            raise ProfileError(
                tr("the profile {name} cannot be renamed: {reason}", name=old, reason=exc.strerror or exc)
            ) from exc
        if was_active:
            self.set_active(name)
        return self.path(name)

    def delete(self, name: str) -> None:
        """Delete a profile; the active one is not deleted (the game would find none)."""
        if self.active().lower() == name.lower():
            raise ProfileError(tr("{name} is the active profile: make another one active first", name=name))
        try:
            self.path(name).unlink()
        except OSError as exc:
            raise ProfileError(
                tr("the profile {name} cannot be deleted: {reason}", name=name, reason=exc.strerror or exc)
            ) from exc

    def _free(self, name: str) -> None:
        why = name_problem(name)
        if why:
            raise ProfileError(why)
        if self._find(name) is not None:
            raise ProfileError(tr("a profile named {name} exists already", name=self._find(name)))

    # the active profile

    def active_file(self) -> ActiveFile | None:
        path = self.mods_dir / PROFILE_FILE
        if not path.is_file():
            return None
        return ActiveFile.from_bytes(path.read_bytes())

    def active(self) -> str:
        """The profile the game starts with: ``default`` without a ``profile.txt``; "" when it names none (no mods)."""
        found = self.active_file()
        return found.profile if found is not None else DEFAULT

    def set_active(self, name: str) -> None:
        """Make ``name`` the active profile ("" for none: the game starts without mods)."""
        if name and not self.exists(name):
            raise ProfileError(tr("there is no profile named {name}", name=name))
        found = self.active_file() or new_active(name)
        found.set_name(name)
        _write(self.mods_dir / PROFILE_FILE, found.to_bytes())


def _write(path: Path, data: bytes) -> None:
    """The file's bytes written beside it and moved over it."""
    try:
        write_atomic(path, data)
    except OSError as exc:
        raise ProfileError(
            tr("{name} cannot be written: {reason}", name=path.name, reason=exc.strerror or exc)
        ) from exc
