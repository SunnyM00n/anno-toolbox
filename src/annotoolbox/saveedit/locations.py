"""Where each game keeps its savegames, and the saves found there (``docs/save-editor.md``, "Where each game keeps
its saves"). ``find(game)`` lists them newest first; nothing here opens a save.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..paths import documents_dir


@dataclass(frozen=True)
class SLocation:
    folders: tuple[str, ...]  # candidates under Documents, the first that exists wins
    patterns: tuple[str, ...]  # globs below it
    skip: tuple[str, ...] = ()  # file names that are no save
    seen: bool = False  # saves of this kind were read on the author's machine (save-editor.md)


LOCATIONS = {
    "anno117": SLocation(("Anno 117 - Pax Romana",), ("accounts/*/*/*.a8s",), ("accountdata.a8s",), seen=True),
    "anno1800": SLocation(("Anno 1800",), ("accounts/*/*/*.a7s",), ("accountdata.a7s",)),
    "anno2205": SLocation(("Anno 2205",), ("accounts/**/*.a6s",)),
    "anno2070": SLocation(("ANNO 2070",), ("Accounts/*/Savegames/*.sww",), seen=True),
    "anno1404": SLocation(("Anno 1404 History Edition", "Anno 1404 HE", "Anno 1404"), ("**/Savegames*/*.sww",)),
    "anno1701": SLocation(("Anno 1701 History Edition", "Anno 1701 HE", "Anno 1701"), ("**/Savegames*/*.sww",)),
}
EXTENSIONS = {".a8s": "anno117", ".a7s": "anno1800", ".a6s": "anno2205", ".sww": "anno2070"}


@dataclass(frozen=True)
class SSaveRef:
    game: str
    path: Path
    group: str  # the profile (117, 1800) or the account folder
    size_bytes: int
    mtime: float

    @property
    def name(self) -> str:
        return self.path.stem


def folder(game: str, documents: Path | None = None) -> Path | None:
    """The game's folder under Documents, None when it has none on this computer."""
    base = Path(documents) if documents is not None else documents_dir()
    for name in LOCATIONS[game].folders:
        if (base / name).is_dir():
            return base / name
    return None


def _group(game: str, root: Path, path: Path) -> str:
    rel = path.relative_to(root).parts
    if game in ("anno117", "anno1800") and len(rel) >= 3:
        return rel[2]  # accounts/<account>/<profile>/<file>
    if game == "anno2070" and len(rel) >= 2:
        return rel[1]  # Accounts/<account>/Savegames/<file>
    return "/".join(rel[:-1])


def find(game: str, documents: Path | None = None) -> list[SSaveRef]:
    """The game's saves in its folder, newest first (an unreadable file is left out)."""
    root = folder(game, documents)
    if root is None:
        return []
    loc, found = LOCATIONS[game], {}
    for pattern in loc.patterns:
        for path in root.glob(pattern):
            if path.name.lower() in loc.skip or not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            found[path] = SSaveRef(game, path, _group(game, root, path), stat.st_size, stat.st_mtime)
    return sorted(found.values(), key=lambda found_file: (-found_file.mtime, str(found_file.path)))


def game_of(path: Path, shown: str = "") -> str:
    """The game a save file is of, by its extension; ``shown`` (the game the caller has open) wins where it writes
    that extension too (1404 and 1701 also write ``.sww``) and for an extension no game is known by."""
    suffix = Path(path).suffix.lower()
    if shown in LOCATIONS and any(pattern.lower().endswith(suffix) for pattern in LOCATIONS[shown].patterns):
        return shown
    return EXTENSIONS.get(suffix, shown)
