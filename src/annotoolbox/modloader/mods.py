"""The mods the mod loader would run: a mod folder and its ``modinfo.json``, the folders it searches, the active
profile, the load order, the options and the variables a ``Condition`` reads. Stdlib only.

``read_mod(folder)`` reads one mod (``Mod.nested`` the mods inside its folder), ``find_mods(folders)`` every mod of the
loader's folders (``loader_roots``), ``read_profile`` a profile file and ``enabled`` the mods it switches on,
``load_order`` their order, ``Variables`` what ``$name`` and ``#name`` stand for; ``resolve.py`` puts them together with
dependencies. The rules are ``docs/engine-modloader.md`` ("Where mods live", "Order", "Dependencies", "Nested mods",
"Variables and options").
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .profilefile import DEFAULT, ActiveFile, Profile, ProfileText

LATE = "*"  # ``LoadAfter: ["*"]``: the late phase
PROFILE_FILE = "profile.txt"
OPTIONS_FILE = "active-options.jsonc"
UBI = ".ubi"  # Ubisoft's example mods: on only when the profile lists them
GAME_NUMBER = {"anno117": 8}  # ``Anno`` in a mod's ``modinfo`` (1800's mods say none)
MODIO_GAME = {"anno117": "11358"}  # mod.io's number of the game: ``<public>/mod.io/<number>/mods``
DEPENDENCY_KINDS = ("Require", "Optional", "LoadAfter", "Incompatible", "Deprecate")  # the ``Dependencies`` keys
VARIABLE = re.compile(r"(!?)([$#])([A-Za-z_][\w\-]*(?:\.[A-Za-z_][\w\-]*)*)")


def strip_jsonc(text: str) -> str:
    """``//`` and ``/* */`` comments dropped outside strings (the loader allows them in its JSON files)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_jsonc(text: str) -> dict | None:
    """JSON text with comments as an object, None when it is none."""
    try:
        data = json.loads(strip_jsonc(text.lstrip("﻿")))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def read_jsonc(path: Path) -> dict | None:
    """A JSON file with comments, None when missing or not JSON."""
    try:
        return parse_jsonc(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return None


def localized(value) -> str:
    """A ``modinfo`` text: the English one of a language table, a plain string as it is."""
    if isinstance(value, dict):
        return str(value.get("English") or next(iter(value.values()), "") or "")
    return str(value or "")


def version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-]", str(version or "0")))


MODINFO_NAMES = ("modinfo.json", "modinfo.jsonc")


def walk_sizes(folder: Path):
    """``os.walk`` top down with each file's size read from the folder listing (``(folder, relative path, subfolder
    names to walk, [(file name, size)])``; a symlinked folder is listed, not walked). A ``stat`` per file costs a
    minute on half a million files, the listing carries the size on Windows."""
    stack = [(Path(folder), ".")]
    while stack:
        here, base = stack.pop()
        dirs: list[str] = []
        files: list[tuple[str, int]] = []
        try:
            with os.scandir(here) as entries:
                for entry in entries:
                    if entry.is_dir():
                        dirs.append(entry.name)
                        continue
                    try:
                        files.append((entry.name, entry.stat().st_size))
                    except OSError:
                        files.append((entry.name, 0))
        except OSError:
            continue
        dirs.sort()
        yield here, base, dirs, files
        stack += [
            (here / d, d if base == "." else f"{base}/{d}") for d in reversed(dirs) if not (here / d).is_symlink()
        ]


def parked(name: str) -> bool:
    """A folder name the loader skips without a word."""
    return name.startswith(("-", "."))


@dataclass
class Mod:
    folder: Path
    info: dict
    mod_id: str
    version: str = ""
    name: str = ""
    category: str = ""
    load_after: list[str] = field(default_factory=list)
    deprecate: list[str] = field(default_factory=list)
    options: dict[str, str] = field(default_factory=dict)  # the defaults of ``Options``
    require: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    incompatible: list[str] = field(default_factory=list)
    anno: int | None = None  # the game number the mod was read for (children are read for the same one)
    parent: Mod | None = field(default=None, repr=False, compare=False)  # the mod whose folder holds this one
    _files: list[str] | None = field(default=None, repr=False, compare=False)
    _sizes: dict[str, int] = field(default_factory=dict, repr=False, compare=False)
    _nested: list[Mod] = field(default_factory=list, repr=False, compare=False)
    _notes: list[str] = field(default_factory=list, repr=False, compare=False)

    @property
    def display(self) -> str:
        """What the loader sorts by: ``[<Category>] <ModName>``, the folder name without a name."""
        return f"[{self.category}] {self.name}" if self.name else self.folder.name

    @property
    def late(self) -> bool:
        return LATE in self.load_after

    @property
    def top(self) -> Mod:
        """The mod of the mods folder this one sits in (itself unless nested): the one a profile names."""
        return self.parent.top if self.parent else self

    @property
    def nested(self) -> list[Mod]:
        """The mods inside this mod's folder (their own nested ones are theirs), by folder path."""
        self._scan()
        return self._nested

    @property
    def nested_notes(self) -> list[str]:
        """Why a folder below this mod with a ``modinfo`` is not a mod (the loader disables it)."""
        self._scan()
        return self._notes

    def files(self) -> list[str]:
        """Every file of the mod, mod-relative, in path order, the nested mods' folders left out (read once: a mod
        may hold half a million)."""
        self._scan()
        return self._files or []

    def size_of(self, rel: str) -> int:
        """The size of one of ``files()`` (from the folder listing, no ``stat`` per file)."""
        self._scan()
        return self._sizes.get(rel, 0)

    def _scan(self) -> None:
        """One walk: the files, and the folders below with a ``modinfo`` of their own (the nested mods; a folder
        starting with ``-`` or ``.`` or inside one holds none, ``engine-modloader.md`` "Nested mods")."""
        if self._files is not None:
            return
        found: dict[str, int] = {}
        children: list[Path] = []
        for here, base, dirs, names in walk_sizes(self.folder):
            if base != "." and any(n in MODINFO_NAMES for n, _s in names) and not any(map(parked, base.split("/"))):
                children.append(here)
                dirs.clear()
                continue
            found.update({n if base == "." else f"{base}/{n}": size for n, size in names})
        self._files, self._sizes = sorted(found), found
        for child in children:
            mod, why = read_mod(child, self.anno)
            if mod is None:
                self._notes.append(why)
                continue
            mod.parent = self
            self._nested.append(mod)

    def patch_files(self) -> list[tuple[str, Path]]:
        """(mod-relative path, file) of every patch file, in path order (an include is not one)."""
        return [(rel, self.folder / rel) for rel in self.files() if patch_target(rel) is not None]

    @property
    def home(self) -> Path:
        """The mods folder the mod sits in (a ``.zip`` mod: the folder of the archive)."""
        return self.folder.parent

    def read(self, rel: str) -> bytes:
        """One file of the mod, by its mod-relative path."""
        return (self.folder / rel).read_bytes()

    def is_file(self, rel: str) -> bool:
        return (self.folder / rel).is_file()

    @property
    def source_root(self) -> str:
        """Where the mod's files live: a file's place is this followed by its mod-relative path (``source_of``)."""
        return f"{self.folder}/"

    def source_of(self, rel: str) -> str:
        """Where a file of the mod lives, for a report."""
        return self.source_root + rel


def patch_target(rel: str) -> str | None:
    """The game file a mod's file patches (lower case), None when the file is not a patch (``docs/engine-modloader.md``
    "What a mod can change"): ``x.xml`` patches ``x.xml``, ``x.cfg.xml`` patches ``x.cfg``, an include none."""
    low = rel.lower()
    if not low.endswith(".xml") or low.endswith(".include.xml"):
        return None
    if low.endswith((".cfg.xml", ".fc.xml", "export.bin.xml")):
        return low[: -len(".xml")]
    return low


def read_mod(folder: Path, anno: int | None = None) -> tuple[Mod | None, str]:
    """The mod in ``folder``, or None and the reason the loader would skip it; ``anno``: the game's number in
    ``modinfo`` (117: 8; None takes any)."""
    folder = Path(folder)
    path = next((folder / n for n in MODINFO_NAMES if (folder / n).is_file()), None)
    if path is None:
        return None, f"{folder.name}: is disabled. It is missing a modinfo.json file"
    info = read_jsonc(path)
    if info is None:
        return None, f"{folder.name}: is disabled because of missing modinfo.json (not JSON)"
    return mod_from_info(folder, info, anno)


def mod_from_info(
    folder: Path, info: dict, anno: int | None, cls: type[Mod] = Mod, fallback: str = "", **extra
) -> tuple[Mod | None, str]:
    """The mod a read ``modinfo`` makes (``folder``: where it lives, the name a missing ``ModID`` falls back to;
    ``cls``, ``fallback`` and ``extra``: a mod kept in an archive)."""
    if anno is not None and info.get("Anno") != anno:
        return None, f"{folder.name}: is not the correct game version"
    deps = info.get("Dependencies") if isinstance(info.get("Dependencies"), dict) else {}
    options = info.get("Options") if isinstance(info.get("Options"), dict) else {}

    def ids(key: str, old: str) -> list[str]:  # 117's ``Dependencies`` object, 1800's top-level lists
        for value in (deps.get(key), info.get(old)):
            if isinstance(value, list) and value:  # a number or a string is no list of ids
                return [x for x in value if isinstance(x, str)]
        return []

    mod = cls(
        folder,
        info,
        str(info.get("ModID") or fallback or folder.name),
        str(info.get("Version") or ""),
        localized(info.get("ModName")),
        localized(info.get("Category")),
        ids("LoadAfter", "LoadAfterIds"),  # a top-level "LoadAfter" is ignored by the loader (modlint I03)
        ids("Deprecate", "DeprecateIds"),
        {k: str(v.get("default", "")) if isinstance(v, dict) else str(v) for k, v in options.items()},
        ids("Require", "ModDependencies"),
        ids("Optional", "OptionalDependencies"),
        ids("Incompatible", "IncompatibleIds"),
        anno,
        **extra,
    )
    return mod, ""


def find_mods(folders: list[Path], anno: int | None = None, zips: bool = False) -> tuple[list[Mod], list[str]]:
    """Every mod of the loader's folders (``Mod.nested`` the ones inside each), the same ModID kept at its higher
    version, the later one when equal; and the folders skipped, with the reason. ``zips``: a ``.zip`` is read as the
    mods inside it (1800 does, 117 does not unpack one: ``zipmods.py``)."""
    from .zipmods import ZIP, read_zip_mods

    found: dict[str, Mod] = {}
    notes = []
    for root in folders:
        entries = sorted(Path(root).iterdir()) if Path(root).is_dir() else ()
        for entry in entries:
            if parked(entry.name):
                continue  # parked: the loader skips them without a word
            if zips and entry.is_file() and entry.suffix.lower() == ZIP:
                inside, why = read_zip_mods(entry, anno)
                notes += why
            elif entry.is_dir():
                mod, reason = read_mod(entry, anno)
                inside, notes = [mod] if mod else [], notes + ([reason] if mod is None else [])
            else:
                continue
            for mod in inside:
                old = found.get(mod.mod_id)
                if old is None or version_key(mod.version) >= version_key(old.version):  # equal: the later one
                    found[mod.mod_id] = mod
    return list(found.values()), notes


@dataclass(frozen=True)
class Root:
    """One folder the loader searches. ``new_mods``: whether ``EnableNewMods`` switches its unlisted mods on."""

    path: Path
    label: str
    new_mods: bool = True


def loader_roots(
    game: str,
    game_dir: Path | None,
    documents_mods: Path | None,
    public_dir: Path | None = None,
    extra: list[Path] = (),
) -> list[Root]:
    """The folders the game's loader searches, in its order: the install's ``mods/.ubi`` and ``mods``, the Documents
    ``mods`` folder (the one that holds ``profile.txt``), mod.io's under ``public_dir`` (the ``PUBLIC`` variable), then
    ``extra``."""
    out = []
    if game_dir:
        out += [Root(Path(game_dir) / "mods" / UBI, "Ubisoft's examples", False), Root(Path(game_dir) / "mods", "game")]
    if documents_mods:
        out.append(Root(Path(documents_mods), "Documents"))
    if public_dir and game in MODIO_GAME:
        out.append(Root(Path(public_dir) / "mod.io" / MODIO_GAME[game] / "mods", "mod.io"))
    return out + [Root(Path(p), "extra") for p in extra]


def read_profile(text: str, name: str = "") -> Profile:
    """A profile file: one ModID a line, ``#`` in front switches it off, ``EnableNewMods`` turns on the unlisted."""
    return ProfileText(text).to_profile(name)


def active_profile(mods_dir: Path) -> Path | None:
    """The profile file ``profile.txt`` names (``default`` without one), None when ``ActiveProfile`` is empty."""
    path = Path(mods_dir) / PROFILE_FILE
    named = ActiveFile.from_bytes(path.read_bytes()).profile if path.is_file() else DEFAULT
    return Path(mods_dir) / "profiles" / f"{named}.txt" if named else None


def switched_on(mods: list[Mod], profile: Profile, new_folders: tuple[Path, ...] = ()) -> list[Mod]:
    """The mods of the mods folders the profile switches on: those it lists, with ``EnableNewMods`` every unlisted
    one of ``new_folders`` (Ubisoft's ``.ubi`` mods only when listed). A nested mod is never named: it follows its
    top mod (``Mod.nested``, ``resolve.resolve``)."""
    listed = set(profile.on) | set(profile.off)
    roots = {Path(os.path.abspath(f)) for f in new_folders}  # not resolved: a linked mod folder is still in its root
    return [
        m for m in mods
        if m.mod_id in profile.on
        or profile.enable_new and m.mod_id not in listed and Path(os.path.abspath(m.folder)).parent in roots
    ]  # fmt: skip


def enabled(mods: list[Mod], profile: Profile, new_folders: tuple[Path, ...] = ()) -> list[Mod]:
    """``switched_on`` with a ``Deprecate``d mod taken out (the nested mods and the dependencies are ``resolve``'s)."""
    on = switched_on(mods, profile, new_folders)
    dropped = {d for m in on for d in m.deprecate if d != m.mod_id}
    return [m for m in on if m.mod_id not in dropped]


@dataclass
class Ordering:
    mods: list[Mod]
    cycles: list[list[str]] = field(default_factory=list)  # ModIDs round a ``LoadAfter`` circle, the first repeated
    ignored: list[tuple[str, str]] = field(default_factory=list)  # (mod, late mod it wants to follow): cannot


def order_mods(mods: list[Mod], redirect: dict[str, str] | None = None) -> Ordering:
    """The order the loader applies them in: a depth-first walk by display name as bytes, each mod after the mods its
    ``LoadAfter`` names (when they are on), the ``"*"`` ones in a late phase after all others. ``redirect`` maps a
    ``Deprecate``d id to the mod that replaces it; an id nobody has is ignored; a circle is cut where it closes."""
    result = Ordering([])
    late_ids = {m.mod_id for m in mods if m.late}
    for late in (False, True):
        group = sorted((m for m in mods if m.late == late), key=_sort_key)
        walk = _Walk(result, {m.mod_id: m for m in group}, redirect or {}, late_ids, late)
        for m in group:
            if m.mod_id not in walk.state:
                walk.visit(m)
    return result


class _Walk:
    """One phase's depth-first walk."""

    def __init__(
        self, result: Ordering, by_id: dict[str, Mod], redirect: dict[str, str], late_ids: set[str], late: bool
    ):
        self.result, self.by_id, self.redirect, self.late_ids, self.late = result, by_id, redirect, late_ids, late
        self.state: dict[str, int] = {}  # 1 on the way down, 2 placed
        self.stack: list[str] = []

    def visit(self, m: Mod) -> None:
        self.state[m.mod_id] = 1
        self.stack.append(m.mod_id)
        for want in m.load_after:
            want = self.redirect.get(want, want)
            if not self.late and want in self.late_ids:
                self.result.ignored.append((m.mod_id, want))
            dep = self.by_id.get(want)
            if dep is None or dep is m:
                continue
            if self.state.get(want) == 1:
                self.result.cycles.append([*self.stack[self.stack.index(want) :], want])
            elif want not in self.state:
                self.visit(dep)
        self.stack.pop()
        self.state[m.mod_id] = 2
        self.result.mods.append(m)


def load_order(mods: list[Mod]) -> list[Mod]:
    """``order_mods(mods).mods``."""
    return order_mods(mods).mods


def _sort_key(mod: Mod) -> tuple[bool, bytes]:
    # a mod without a category sorts first, seen in the loader's log (engine-modloader "Order")
    return bool(mod.category), mod.display.encode("utf-8")


def read_options(mods_dir: Path | None) -> dict[str, dict[str, str]]:
    """``active-options.jsonc``: ModID -> option -> value (the player's), empty without the file."""
    data = read_jsonc(Path(mods_dir) / OPTIONS_FILE) if mods_dir is not None else None
    return {k: {n: str(v) for n, v in vals.items()} for k, vals in (data or {}).items() if isinstance(vals, dict)}


@dataclass
class Expanded:
    text: str
    mods: set[str] = field(default_factory=set)  # other mods the expression asks about
    unknown: list[str] = field(default_factory=list)


class Variables:
    """What ``#mod-id`` and ``$name`` stand for while one mod's patches run: the mods that are on, every mod's id with
    the value ``true`` and ``<ModID>.<option>``; ``$option`` inside the mod that owns it."""

    def __init__(self, on: list[Mod], chosen: dict[str, dict[str, str]] | None = None) -> None:
        self.on = {m.mod_id for m in on}
        self.values: dict[str, str] = {}
        for m in on:
            self.values[m.mod_id] = "true"
            for name, default in m.options.items():
                self.values[f"{m.mod_id}.{name}"] = (chosen or {}).get(m.mod_id, {}).get(name, default)

    def expand(self, expr: str, owner: str) -> Expanded:
        """``expr`` with every variable replaced (outside quotes): ``#id`` by ``true()`` / ``false()``, ``$name`` by
        its value as a string; an unknown ``$name`` by the empty string."""
        out = Expanded("")
        parts = re.split(r"('[^']*'|\"[^\"]*\")", expr)
        for k, part in enumerate(parts):
            parts[k] = part if k % 2 else VARIABLE.sub(lambda m: self._term(m, owner, out), part)
        out.text = "".join(parts)
        return out

    def _term(self, m: re.Match, owner: str, out: Expanded) -> str:
        negate, sign, name = m.groups()
        if sign == "#":
            if name != owner:
                out.mods.add(name)
            value = (name in self.on) != bool(negate)
            return "true()" if value else "false()"
        full = name if name in self.values else f"{owner}.{name}"
        mod = full.split(".")[0] if "." in full else full
        if mod != owner and (name in self.values or name.split(".")[0] in self.on):
            out.mods.add(mod)
        if full not in self.values:
            out.unknown.append(name)
            text = ""
        else:
            text = self.values[full]
        quoted = f"'{text}'" if "'" not in text else f'"{text}"'
        return f"not({quoted})" if negate else quoted
