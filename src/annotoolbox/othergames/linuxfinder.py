"""Finding the Anno games on Linux, where they run under Proton or Wine (``docs/linux.md`` section 2).

``scan`` reads Steam's libraries and manifests and every Wine prefix it can name (Steam, Lutris, Heroic, Bottles,
``~/.wine``), Ubisoft Connect installs inside them included; ``Found`` says where a game came from and which prefix
holds its user folder. Functions take a home folder, so tests use a synthetic tree; ``detect`` and ``user_dir`` ask
the real machine. Nothing is run or created, and names match without regard to case. Start: ``docs/linux.md`` "Phase 2".
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# The Steam application ids (docs/linux.md 2.1); the install folder comes from the manifest, never from a name.
STEAM_APPS = {
    "anno117": 3274580,
    "anno1800": 916440,
    "anno2205": 375910,
    "anno2070": 48240,
    "anno1404": 1281630,
    "anno1701": 2820140,
}
LAUNCHERS = {"steam": "Steam", "lutris": "Lutris", "heroic": "Heroic", "bottles": "Bottles", "wine": "Wine"}
# Where Ubisoft Connect puts a game below ``drive_c`` (the Windows code's list), then the free-form ones.
UBISOFT_PARENTS = (
    "Program Files (x86)/Ubisoft/Ubisoft Game Launcher/games",
    "Program Files/Ubisoft",
    "Ubisoft",
    "Games",
)
INSTALLS_KEY = "ubisoft\\launcher\\installs\\"  # the lower-cased registry path every install sits below


@dataclass(frozen=True)
class Prefix:
    """A Wine prefix: its folder, the launcher that made it (``LAUNCHERS``) and that launcher's name for it."""

    path: Path
    kind: str
    name: str = ""

    @property
    def drive_c(self) -> Path | None:
        return find_ci(self.path, "drive_c")


@dataclass(frozen=True)
class Found:
    """An install: ``via`` is "library" (a Steam library, ``where``) or "prefix" (inside the Wine prefix ``where``);
    ``prefix`` is the one that holds the game's user folder (None for a Steam game that was never started)."""

    game: str
    folder: Path
    kind: str
    via: str
    where: Path
    prefix: Prefix | None = None


def applies() -> bool:
    """Whether the finder takes the place of the registry and the drive letters (anywhere but Windows)."""
    return os.name != "nt"


def find_ci(root: Path | str, *parts: str) -> Path | None:
    """``root/parts`` as it exists on disk, matching every name without regard to case; None when a part is missing.
    A part may hold slashes. The exact spelling wins where it exists."""
    here = Path(root)
    for part in (p for chunk in parts for p in chunk.replace("\\", "/").split("/") if p):
        direct = here / part
        if direct.exists():
            here = direct
            continue
        try:
            names = sorted(os.listdir(here))
        except OSError:
            return None
        low = part.lower()
        hit = next((n for n in names if n.lower() == low), None)
        if hit is None:
            return None
        here = here / hit
    return here if here.exists() else None


def is_install_ci(game, folder: Path) -> bool:
    """``Game.is_install`` with every name matched without regard to case (the files must be regular files)."""
    folder = Path(folder)

    def has(name: str) -> bool:
        found = find_ci(folder, name)
        return found is not None and found.is_file()

    return any(has(e) for e in game.exes) and all(has(n) for n in game.needs)


# Steam

_PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"\s+"((?:[^"\\]|\\.)*)"')


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def vdf_pairs(text: str) -> list[tuple[str, str]]:
    """The ``"key" "value"`` pairs of a Steam text file (``.vdf``, ``.acf``) in file order, escapes resolved."""
    return [(_unescape(k), _unescape(v)) for k, v in _PAIR.findall(text)]


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8", "ignore")
    except OSError:
        return ""


def _unique(paths) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        try:
            key = p.resolve()
        except OSError:
            key = p
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _xdg(home: Path | None, variable: str, fallback: str) -> Path:
    """An XDG base folder: below ``home`` when one is given (a test), else the variable or ``~/<fallback>``."""
    if home is not None:
        return home / fallback
    value = os.environ.get(variable, "")
    return Path(value) if value and os.path.isabs(value) else Path.home() / fallback


def steam_roots(home: Path | None = None) -> list[Path]:
    """The Steam folders that exist: the native one, its usual symlinks and the Flatpak copy."""
    base = home if home is not None else Path.home()
    places = [
        _xdg(home, "XDG_DATA_HOME", ".local/share") / "Steam",
        base / ".steam/steam",
        base / ".steam/root",
        base / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    return _unique(p for p in places if find_ci(p, "steamapps") is not None)


def libraries(root: Path) -> list[Path]:
    """The Steam root and the other library folders its ``libraryfolders.vdf`` lists (those that exist)."""
    steamapps = find_ci(root, "steamapps")
    listed = []
    if steamapps is not None and (vdf := find_ci(steamapps, "libraryfolders.vdf")) is not None:
        listed = [Path(v) for k, v in vdf_pairs(_read(vdf)) if k == "path"]
    return _unique(p for p in [root, *listed] if find_ci(p, "steamapps") is not None)


def steam_libraries(home: Path | None = None) -> list[Path]:
    return _unique(lib for root in steam_roots(home) for lib in libraries(root))


def manifests(library: Path) -> list[dict[str, str]]:
    """The ``AppState`` records of a library's ``appmanifest_<id>.acf`` files (``appid``, ``installdir``, ...)."""
    steamapps = find_ci(library, "steamapps")
    if steamapps is None:
        return []
    out = []
    for acf in sorted(steamapps.glob("appmanifest_*.acf")):
        record = {}
        for key, value in vdf_pairs(_read(acf)):
            record.setdefault(key.lower(), value)
        if record.get("appid") and record.get("installdir"):
            out.append(record)
    return out


def steam_prefix(libs: list[Path], appid: str, first: Path | None = None) -> Prefix | None:
    """The Proton prefix of ``appid``: in the library of the install first, then the others."""
    for lib in _unique([*([first] if first else []), *libs]):
        pfx = find_ci(lib, "steamapps", "compatdata", appid, "pfx")
        if pfx is not None and Prefix(pfx, "steam", appid).drive_c is not None:
            return Prefix(pfx, "steam", appid)
    return None


def steam_installs(games, home: Path | None = None) -> list[Found]:
    """The games Steam knows of, by application id; a manifest whose folder is gone or is no install is skipped."""
    ids = {str(v): k for k, v in STEAM_APPS.items()}
    libs = steam_libraries(home)
    out = []
    for lib in libs:
        for record in manifests(lib):
            key = ids.get(record["appid"])
            folder = find_ci(lib, "steamapps", "common", record["installdir"]) if key else None
            if folder is not None and key in games and is_install_ci(games[key], folder):
                out.append(Found(key, folder, "steam", "library", lib, steam_prefix(libs, record["appid"], lib)))
    return out


def steam_prefixes(home: Path | None = None) -> list[Prefix]:
    """Every Proton prefix of every library (a Ubisoft Connect shortcut has a large made-up id)."""
    out = []
    for lib in steam_libraries(home):
        compat = find_ci(lib, "steamapps", "compatdata")
        try:
            ids = sorted(os.listdir(compat)) if compat is not None else []
        except OSError:
            ids = []
        out += [Prefix(p, "steam", i) for i in ids if (p := find_ci(compat, i, "pfx")) is not None]
    return [p for p in out if p.drive_c is not None]


# Wine prefixes of the other launchers


def _children(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        return []


def _wine_dirs(folders, kind: str) -> list[Prefix]:
    return [Prefix(p, kind, p.name) for f in folders for p in _children(f) if Prefix(p, kind).drive_c is not None]


def lutris_prefixes(home: Path | None = None) -> list[Prefix]:
    """Lutris: the ``prefix:`` of each game's yml, and the folders below ``~/Games`` that hold a ``drive_c``."""
    base = home if home is not None else Path.home()
    configs = [
        _xdg(home, "XDG_CONFIG_HOME", ".config") / "lutris" / "games",
        base / ".var/app/net.lutris.Lutris/config/lutris/games",
    ]
    named = []
    for folder in configs:
        for yml in sorted(folder.glob("*.yml")) if folder.is_dir() else []:
            for m in re.finditer(r"^\s*prefix:\s*['\"]?([^'\"\r\n]+?)['\"]?\s*$", _read(yml), re.M):
                named.append(Path(m[1].replace("~", str(base), 1) if m[1].startswith("~") else m[1]))
    out = [Prefix(p, "lutris", p.name) for p in named if Prefix(p, "lutris").drive_c is not None]
    return out + _wine_dirs([base / "Games"], "lutris")


def heroic_prefixes(home: Path | None = None) -> list[Prefix]:
    """Heroic: ``~/Games/Heroic/Prefixes`` and the ``winePrefix`` of each game's config."""
    base = home if home is not None else Path.home()
    prefixes = base / "Games/Heroic/Prefixes"
    out = _wine_dirs([prefixes, prefixes / "default"], "heroic")
    for config in (
        _xdg(home, "XDG_CONFIG_HOME", ".config") / "heroic",
        base / ".var/app/com.heroicgameslauncher.hgl/config/heroic",
    ):
        games = config / "GamesConfig"
        for js in sorted(games.glob("*.json")) if games.is_dir() else []:
            for m in re.finditer(r'"winePrefix"\s*:\s*"((?:[^"\\]|\\.)*)"', _read(js)):
                p = Path(_unescape(m[1]))
                if Prefix(p, "heroic").drive_c is not None:
                    out.append(Prefix(p, "heroic", p.name))
    return out


def bottles_prefixes(home: Path | None = None) -> list[Prefix]:
    base = home if home is not None else Path.home()
    folders = [
        _xdg(home, "XDG_DATA_HOME", ".local/share") / "bottles/bottles",
        base / ".var/app/com.usebottles.bottles/data/bottles/bottles",
    ]
    return _wine_dirs(folders, "bottles")


def prefixes(home: Path | None = None) -> list[Prefix]:
    """All Wine prefixes found, Steam's first (each once, by its real folder)."""
    base = home if home is not None else Path.home()
    plain = Prefix(base / ".wine", "wine", ".wine")
    every = [
        *steam_prefixes(home),
        *lutris_prefixes(home),
        *heroic_prefixes(home),
        *bottles_prefixes(home),
        *([plain] if plain.drive_c is not None else []),
    ]
    seen: set[Path] = set()
    out = []
    for p in every:
        if (key := _unique([p.path])[0].resolve()) not in seen:
            seen.add(key)
            out.append(p)
    return out


# Inside a prefix


def read_registry(path: Path) -> dict[str, dict[str, str]]:
    """The string values of a Wine ``.reg`` text file (``system.reg``, ``user.reg``): lower-cased key path -> name ->
    value. Binary and numeric values are left out."""
    out: dict[str, dict[str, str]] = {}
    section: dict[str, str] | None = None
    for line in _read(path).splitlines():
        if line.startswith("["):
            m = re.match(r"\[(.*)\](?: \d+)?\s*$", line)
            section = out.setdefault(_unescape(m[1]).lower(), {}) if m else None
        elif section is not None and (m := re.match(r'"((?:[^"\\]|\\.)*)"="((?:[^"\\]|\\.)*)"\s*$', line)):
            section[_unescape(m[1])] = _unescape(m[2])
    return out


def host_path(prefix: Prefix, windows: str) -> Path | None:
    """A Windows path of the prefix (``C:\\Games\\x``) as the Linux folder it is: a letter other than C goes through
    ``dosdevices``, Z is the root. None for a path that is not there."""
    m = re.match(r"([A-Za-z]):[\\/]*(.*)$", windows.strip())
    if m is None:
        return None
    letter, rest = m[1].lower(), m[2]
    if letter == "c":
        base = prefix.drive_c
    elif letter == "z":
        base = Path("/")
    else:
        base = find_ci(prefix.path, "dosdevices", f"{letter}:")
    return find_ci(base, rest) if base is not None else None


def registry_installs(prefix: Prefix) -> list[Path]:
    """The ``InstallDir`` of every Ubisoft Connect install key of the prefix's two registry files."""
    out = []
    for name in ("system.reg", "user.reg"):
        if (reg := find_ci(prefix.path, name)) is None:
            continue
        for key, values in read_registry(reg).items():
            if INSTALLS_KEY in key + "\\" and (folder := values.get("InstallDir")):
                if (p := host_path(prefix, folder)) is not None and p.is_dir():
                    out.append(p)
    return out


def _anno_dirs(parent: Path | None) -> list[Path]:
    try:
        return (
            [p for p in sorted(parent.iterdir()) if p.is_dir() and p.name.lower().startswith("anno")] if parent else []
        )
    except OSError:
        return []


def prefix_installs(prefix: Prefix) -> list[Path]:
    """The folders of the prefix that may hold an Anno game: the Ubisoft Connect and free-form places, and the install
    keys of the registry."""
    drive_c = prefix.drive_c
    if drive_c is None:
        return []
    found = [d for parent in UBISOFT_PARENTS for d in _anno_dirs(find_ci(drive_c, parent))]
    return _unique([*found, *registry_installs(prefix)])


def user_folder(game: str, prefix: Prefix) -> Path | None:
    """Where ``game`` keeps its mods, logs and saves in the prefix (``drive_c/users/<user>/Documents/<name>``): the
    Proton user first, then the others. None for a game with no such folder, or one that was never started."""
    from . import installs

    name = installs.GAMES[game].documents
    users = find_ci(prefix.drive_c, "users") if prefix.drive_c is not None else None
    if not name or users is None:
        return None
    people = sorted((p.name for p in _children(users) if p.name.lower() != "public"), key=lambda n: n != "steamuser")
    for person in people:
        for documents in ("Documents", "My Documents"):
            if (p := find_ci(users, person, documents, name)) is not None and p.is_dir():
                return p
    return None


# Putting it together


def scan(home: Path | None = None) -> list[Found]:
    """Every Anno install found: Steam's by manifest, then those inside the prefixes (Ubisoft Connect and the rest),
    a folder once."""
    from . import installs

    out = steam_installs(installs.GAMES, home)
    seen = {f.folder.resolve() for f in out}
    for prefix in prefixes(home):
        for folder in prefix_installs(prefix):
            for key, game in installs.GAMES.items():
                if folder.resolve() not in seen and is_install_ci(game, folder):
                    seen.add(folder.resolve())
                    out.append(Found(key, folder, prefix.kind, "prefix", prefix.path, prefix))
    return out


def first_found(game: str, found: list[Found]) -> Found | None:
    return next((f for f in found if f.game == game), None)


def user_folder_of(game: str, found: list[Found]) -> Found | None:
    """The first install of ``game`` whose prefix has its user folder."""
    return next((f for f in found if f.game == game and f.prefix and user_folder(game, f.prefix)), None)


def candidates(found: list[Found]) -> list[Path]:
    return [f.folder for f in found]


def detect_from(found: list[Found]) -> dict[str, str]:
    """The settings' game folders (key -> folder) and ``mods``: 117's ``mods`` folder in its prefix, "" while there is
    none (the game was never started)."""
    from . import installs

    out = {key: str(f.folder) if (f := first_found(key, found)) else "" for key in installs.GAMES}
    owner = user_folder_of("anno117", found)
    return {"mods": str(user_folder("anno117", owner.prefix) / "mods") if owner else "", **out}


@lru_cache(maxsize=1)
def _machine() -> tuple[Found, ...]:
    return tuple(scan())


def forget() -> None:
    """Look again at the next question (the setup page's "look for the games")."""
    _machine.cache_clear()


def machine() -> list[Found]:
    """What the real machine has (looked at once per run)."""
    return list(_machine())


def detect() -> dict[str, str]:
    return detect_from(machine())


def user_dir(game: str, found: list[Found] | None = None) -> Path | None:
    """The game's user folder in a prefix of an install found, None where there is none."""
    found = machine() if found is None else found
    owner = user_folder_of(game, found)
    return user_folder(game, owner.prefix) if owner else None


def origin_of(folder: str | Path, found: list[Found] | None = None) -> tuple[Found, bool] | None:
    """Where a settings folder came from: (the record, whether the folder is its user folder rather than its
    install), None for a folder the finder did not find (typed by hand)."""
    text = str(folder).strip()
    if not text:
        return None
    here = Path(text)
    for f in machine() if found is None else found:
        if _same(f.folder, here):
            return f, False
        if f.prefix is not None and (u := user_folder(f.game, f.prefix)) is not None and _same(u, here):
            return f, True
    return None


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b
