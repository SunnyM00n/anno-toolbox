"""The mods the mod loader would run: a mod folder and its ``modinfo.json``, the folders it searches, the active
profile, the load order, the options and the variables a ``Condition`` reads. Stdlib only.

``read_mod(folder)`` reads one mod (``CMod.nested`` the mods inside its folder), ``find_mods(folders)`` every mod of the
loader's folders (``loader_roots``), ``read_profile`` a profile file and ``enabled`` the mods it switches on,
``load_order`` their order, ``CVariables`` what ``$name`` and ``#name`` stand for; ``resolve.py`` puts them together with
dependencies. The rules are ``docs/engine-modloader.md`` ("Where mods live", "Order", "Dependencies", "Nested mods",
"Variables and options").
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .profilefile import DEFAULT, CActiveFile, CProfileText, SProfile

LATE = "*"  # ``LoadAfter: ["*"]``: the late phase
PROFILE_FILE = "profile.txt"
OPTIONS_FILE = "active-options.jsonc"
UBI = ".ubi"  # Ubisoft's example mods: on only when the profile lists them
# a root's label: which of the loader's folders it is (the mod manager shows it in the interface's language)
ROOT_EXAMPLES, ROOT_GAME, ROOT_DOCUMENTS, ROOT_EXTRA = "Ubisoft's examples", "game", "Documents", "extra"
GAME_NUMBER = {"anno117": 8}  # ``Anno`` in a mod's ``modinfo`` (1800's mods say none)
MODIO_GAME = {"anno117": "11358"}  # mod.io's number of the game: ``<public>/mod.io/<number>/mods``
DEPENDENCY_KINDS = ("Require", "Optional", "LoadAfter", "Incompatible", "Deprecate")  # the ``Dependencies`` keys
VARIABLE = re.compile(r"(!?)([$#])([A-Za-z_][\w\-]*(?:\.[A-Za-z_][\w\-]*)*)")


def strip_jsonc(text: str) -> str:
    """``//`` and ``/* */`` comments dropped outside strings (the loader allows them in its JSON files)."""
    out, i, length = [], 0, len(text)
    while i < length:
        char = text[i]
        if char == '"':
            j = i + 1
            while j < length and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < length and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = length if end < 0 else end + 2
        else:
            out.append(char)
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
    """A JSON file with comments, None when missing, not UTF-8 or not JSON."""
    try:
        return parse_jsonc(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return None


def localized(value) -> str:
    """A ``modinfo`` text: the English one of a language table, a plain string as it is."""
    if isinstance(value, dict):
        return str(value.get("English") or next(iter(value.values()), "") or "")
    return str(value or "")


def version_key(version: str) -> tuple:
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.\-]", str(version or "0")))


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
            (here / directory, directory if base == "." else f"{base}/{directory}")
            for directory in reversed(dirs)
            if not (here / directory).is_symlink()
        ]


def parked(name: str) -> bool:
    """A folder name the loader skips without a word."""
    return name.startswith(("-", "."))


@dataclass
class CMod:
    m_folder: Path
    m_info: dict
    m_mod_id: str
    m_version: str = ""
    m_name: str = ""
    m_category: str = ""
    m_load_after: list[str] = field(default_factory=list)
    m_deprecate: list[str] = field(default_factory=list)
    m_options: dict[str, str] = field(default_factory=dict)  # the defaults of ``Options``
    m_require: list[str] = field(default_factory=list)
    m_optional: list[str] = field(default_factory=list)
    m_incompatible: list[str] = field(default_factory=list)
    m_anno: int | None = None  # the game number the mod was read for (children are read for the same one)
    m_parent: CMod | None = field(default=None, repr=False, compare=False)  # the mod whose folder holds this one
    _m_files: list[str] | None = field(default=None, repr=False, compare=False)
    _m_sizes: dict[str, int] = field(default_factory=dict, repr=False, compare=False)
    _m_nested: list[CMod] = field(default_factory=list, repr=False, compare=False)
    _m_notes: list[str] = field(default_factory=list, repr=False, compare=False)

    @property
    def display(self) -> str:
        """What the loader sorts by: ``[<Category>] <ModName>``, the folder name without a name."""
        return f"[{self.m_category}] {self.m_name}" if self.m_name else self.m_folder.name

    @property
    def late(self) -> bool:
        return LATE in self.m_load_after

    @property
    def top(self) -> CMod:
        """The mod of the mods folder this one sits in (itself unless nested): the one a profile names."""
        return self.m_parent.top if self.m_parent else self

    @property
    def nested(self) -> list[CMod]:
        """The mods inside this mod's folder (their own nested ones are theirs), by folder path."""
        self._scan()
        return self._m_nested

    @property
    def nested_notes(self) -> list[str]:
        """Why a folder below this mod with a ``modinfo`` is not a mod (the loader disables it)."""
        self._scan()
        return self._m_notes

    def files(self) -> list[str]:
        """Every file of the mod, mod-relative, in path order, the nested mods' folders left out (read once: a mod
        may hold half a million)."""
        self._scan()
        return self._m_files or []

    def size_of(self, rel: str) -> int:
        """The size of one of ``files()`` (from the folder listing, no ``stat`` per file)."""
        self._scan()
        return self._m_sizes.get(rel, 0)

    def _scan(self) -> None:
        """One walk: the files, and the folders below with a ``modinfo`` of their own (the nested mods; a folder
        starting with ``-`` or ``.`` or inside one holds none, ``engine-modloader.md`` "Nested mods")."""
        if self._m_files is not None:
            return
        found: dict[str, int] = {}
        children: list[Path] = []
        for here, base, dirs, names in walk_sizes(self.m_folder):
            if (
                base != "."
                and any(name in MODINFO_NAMES for name, _size in names)
                and not any(map(parked, base.split("/")))
            ):
                children.append(here)
                dirs.clear()
                continue
            found.update({name if base == "." else f"{base}/{name}": size_bytes for name, size_bytes in names})
        self._m_files, self._m_sizes = sorted(found), found
        for child in children:
            mod, why = read_mod(child, self.m_anno)
            if mod is None:
                self._m_notes.append(why)
                continue
            mod.m_parent = self
            self._m_nested.append(mod)

    def patch_files(self) -> list[tuple[str, Path]]:
        """(mod-relative path, file) of every patch file, in path order (an include is not one)."""
        return [(rel, self.m_folder / rel) for rel in self.files() if patch_target(rel) is not None]

    @property
    def home(self) -> Path:
        """The mods folder the mod sits in (a ``.zip`` mod: the folder of the archive)."""
        return self.m_folder.parent

    def read(self, rel: str) -> bytes:
        """One file of the mod, by its mod-relative path."""
        return (self.m_folder / rel).read_bytes()

    def is_file(self, rel: str) -> bool:
        return (self.m_folder / rel).is_file()

    @property
    def source_root(self) -> str:
        """Where the mod's files live: a file's place is this followed by its mod-relative path (``source_of``)."""
        return f"{self.m_folder}/"

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


def read_mod(folder: Path, anno: int | None = None) -> tuple[CMod | None, str]:
    """The mod in ``folder``, or None and the reason the loader would skip it; ``anno``: the game's number in
    ``modinfo`` (117: 8; None takes any)."""
    folder = Path(folder)
    path = next((folder / name for name in MODINFO_NAMES if (folder / name).is_file()), None)
    if path is None:
        return None, f"{folder.name}: is disabled. It is missing a modinfo.json file"
    modinfo = read_jsonc(path)
    if modinfo is None:
        return None, f"{folder.name}: is disabled because of missing modinfo.json (not JSON)"
    return mod_from_info(folder, modinfo, anno)


def mod_from_info(
    folder: Path, modinfo: dict, anno: int | None, cls: type[CMod] = CMod, fallback: str = "", **extra
) -> tuple[CMod | None, str]:
    """The mod a read ``modinfo`` makes (``folder``: where it lives, the name a missing ``ModID`` falls back to;
    ``cls``, ``fallback`` and ``extra``: a mod kept in an archive)."""
    if anno is not None and modinfo.get("Anno") != anno:
        return None, f"{folder.name}: is not the correct game version"
    deps = modinfo.get("Dependencies") if isinstance(modinfo.get("Dependencies"), dict) else {}
    options = modinfo.get("Options") if isinstance(modinfo.get("Options"), dict) else {}

    def ids(key: str, old: str) -> list[str]:  # 117's ``Dependencies`` object, 1800's top-level lists
        for value in (deps.get(key), modinfo.get(old)):
            if isinstance(value, list) and value:  # a number or a string is no list of ids
                return [x for x in value if isinstance(x, str)]
        return []

    mod = cls(
        folder,
        modinfo,
        str(modinfo.get("ModID") or fallback or folder.name),
        str(modinfo.get("Version") or ""),
        localized(modinfo.get("ModName")),
        localized(modinfo.get("Category")),
        ids("LoadAfter", "LoadAfterIds"),  # a top-level "LoadAfter" is ignored by the loader (modlint I03)
        ids("Deprecate", "DeprecateIds"),
        {
            k: str(option.get("default", "")) if isinstance(option, dict) else str(option)
            for k, option in options.items()
        },
        ids("Require", "ModDependencies"),
        ids("Optional", "OptionalDependencies"),
        ids("Incompatible", "IncompatibleIds"),
        anno,
        **extra,
    )
    return mod, ""


def find_mods(folders: list[Path], anno: int | None = None, allow_zips: bool = False) -> tuple[list[CMod], list[str]]:
    """Every mod of the loader's folders (``CMod.nested`` the ones inside each), the same ModID kept at its higher
    version, the later one when equal; and the folders skipped, with the reason. ``allow_zips``: a ``.zip`` is read as the
    mods inside it (1800 does, 117 does not unpack one: ``zipmods.py``)."""
    from .zipmods import ZIP, read_zip_mods

    found: dict[str, CMod] = {}
    notes = []
    for root in folders:
        entries = sorted(Path(root).iterdir()) if Path(root).is_dir() else ()
        for entry in entries:
            if parked(entry.name):
                continue  # parked: the loader skips them without a word
            if allow_zips and entry.is_file() and entry.suffix.lower() == ZIP:
                inside, why = read_zip_mods(entry, anno)
                notes += why
            elif entry.is_dir():
                mod, reason = read_mod(entry, anno)
                inside, notes = [mod] if mod else [], notes + ([reason] if mod is None else [])
            else:
                continue
            for mod in inside:
                old = found.get(mod.m_mod_id)
                if old is None or version_key(mod.m_version) >= version_key(old.m_version):  # equal: the later one
                    found[mod.m_mod_id] = mod
    return list(found.values()), notes


@dataclass(frozen=True)
class SRoot:
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
) -> list[SRoot]:
    """The folders the game's loader searches, in its order: the install's ``mods/.ubi`` and ``mods``, the Documents
    ``mods`` folder (the one that holds ``profile.txt``), mod.io's under ``public_dir`` (the ``PUBLIC`` variable), then
    ``extra``."""
    out = []
    if game_dir:
        out += [SRoot(Path(game_dir) / "mods" / UBI, ROOT_EXAMPLES, False), SRoot(Path(game_dir) / "mods", ROOT_GAME)]
    if documents_mods:
        out.append(SRoot(Path(documents_mods), ROOT_DOCUMENTS))
    if public_dir and game in MODIO_GAME:
        out.append(SRoot(Path(public_dir) / "mod.io" / MODIO_GAME[game] / "mods", "mod.io"))
    return out + [SRoot(Path(folder), ROOT_EXTRA) for folder in extra]


def read_profile(text: str, name: str = "") -> SProfile:
    """A profile file: one ModID a line, ``#`` in front switches it off, ``EnableNewMods`` turns on the unlisted."""
    return CProfileText(text).to_profile(name)


def active_profile(mods_dir: Path) -> Path | None:
    """The profile file ``profile.txt`` names (``default`` without one), None when ``ActiveProfile`` is empty."""
    path = Path(mods_dir) / PROFILE_FILE
    named = CActiveFile.from_bytes(path.read_bytes()).profile if path.is_file() else DEFAULT
    return Path(mods_dir) / "profiles" / f"{named}.txt" if named else None


def switched_on(mods: list[CMod], profile: SProfile, new_folders: tuple[Path, ...] = ()) -> list[CMod]:
    """The mods of the mods folders the profile switches on: those it lists, with ``EnableNewMods`` every unlisted
    one of ``new_folders`` (Ubisoft's ``.ubi`` mods only when listed). A nested mod is never named: it follows its
    top mod (``CMod.nested``, ``resolve.resolve``)."""
    listed = set(profile.on) | set(profile.off)
    roots = {
        Path(os.path.abspath(folder)) for folder in new_folders
    }  # not resolved: a linked mod folder is still in its root
    return [
        mod for mod in mods
        if mod.m_mod_id in profile.on
        or profile.enable_new and mod.m_mod_id not in listed and Path(os.path.abspath(mod.m_folder)).parent in roots
    ]  # fmt: skip


def enabled(mods: list[CMod], profile: SProfile, new_folders: tuple[Path, ...] = ()) -> list[CMod]:
    """``switched_on`` with a ``Deprecate``d mod taken out (the nested mods and the dependencies are ``resolve``'s)."""
    enabled_mods = switched_on(mods, profile, new_folders)
    dropped = {old_id for mod in enabled_mods for old_id in mod.m_deprecate if old_id != mod.m_mod_id}
    return [mod for mod in enabled_mods if mod.m_mod_id not in dropped]


@dataclass
class SOrdering:
    mods: list[CMod]
    cycles: list[list[str]] = field(default_factory=list)  # ModIDs round a ``LoadAfter`` circle, the first repeated
    ignored: list[tuple[str, str]] = field(default_factory=list)  # (mod, late mod it wants to follow): cannot


def order_mods(mods: list[CMod], redirect: dict[str, str] | None = None) -> SOrdering:
    """The order the loader applies them in: a depth-first walk by display name as bytes, each mod after the mods its
    ``LoadAfter`` names (when they are on), the ``"*"`` ones in a late phase after all others. ``redirect`` maps a
    ``Deprecate``d id to the mod that replaces it; an id nobody has is ignored; a circle is cut where it closes."""
    result = SOrdering([])
    late_ids = {mod.m_mod_id for mod in mods if mod.late}
    for late in (False, True):
        group = sorted((mod for mod in mods if mod.late == late), key=_sort_key)
        walk = _CWalk(result, {mod.m_mod_id: mod for mod in group}, redirect or {}, late_ids, late)
        for mod in group:
            if mod.m_mod_id not in walk.state:
                walk.visit(mod)
    return result


class _CWalk:
    """One phase's depth-first walk."""

    def __init__(
        self, result: SOrdering, by_id: dict[str, CMod], redirect: dict[str, str], late_ids: set[str], late: bool
    ):
        self.result, self.by_id, self.redirect, self.late_ids, self.late = result, by_id, redirect, late_ids, late
        self.state: dict[str, int] = {}  # 1 on the way down, 2 placed
        self.stack: list[str] = []

    def visit(self, mod: CMod) -> None:
        self.state[mod.m_mod_id] = 1
        self.stack.append(mod.m_mod_id)
        for want in mod.m_load_after:
            want = self.redirect.get(want, want)
            if not self.late and want in self.late_ids:
                self.result.ignored.append((mod.m_mod_id, want))
            dep = self.by_id.get(want)
            if dep is None or dep is mod:
                continue
            if self.state.get(want) == 1:
                self.result.cycles.append([*self.stack[self.stack.index(want) :], want])
            elif want not in self.state:
                self.visit(dep)
        self.stack.pop()
        self.state[mod.m_mod_id] = 2
        self.result.mods.append(mod)


def load_order(mods: list[CMod]) -> list[CMod]:
    """``order_mods(mods).mods``."""
    return order_mods(mods).mods


def _sort_key(mod: CMod) -> tuple[bool, bytes]:
    # a mod without a category sorts first, seen in the loader's log (engine-modloader "Order")
    return bool(mod.m_category), mod.display.encode("utf-8")


def read_options(mods_dir: Path | None) -> dict[str, dict[str, str]]:
    """``active-options.jsonc``: ModID -> option -> value (the player's), empty without the file."""
    data = read_jsonc(Path(mods_dir) / OPTIONS_FILE) if mods_dir is not None else None
    return {
        k: {name: str(value) for name, value in vals.items()}
        for k, vals in (data or {}).items()
        if isinstance(vals, dict)
    }


@dataclass
class SExpanded:
    text: str
    mods: set[str] = field(default_factory=set)  # other mods the expression asks about
    unknown: list[str] = field(default_factory=list)


class CVariables:
    """What ``#mod-id`` and ``$name`` stand for while one mod's patches run: the mods that are on, every mod's id with
    the value ``true`` and ``<ModID>.<option>``; ``$option`` inside the mod that owns it."""

    def __init__(self, enabled_mods: list[CMod], chosen: dict[str, dict[str, str]] | None = None) -> None:
        self.m_enabled_ids = {mod.m_mod_id for mod in enabled_mods}
        self.m_values: dict[str, str] = {}
        for mod in enabled_mods:
            self.m_values[mod.m_mod_id] = "true"
            for name, default in mod.m_options.items():
                self.m_values[f"{mod.m_mod_id}.{name}"] = (chosen or {}).get(mod.m_mod_id, {}).get(name, default)

    def expand(self, expression: str, owner: str) -> SExpanded:
        """``expression`` with every variable replaced (outside quotes): ``#id`` by ``true()`` / ``false()``, ``$name`` by
        its value as a string; an unknown ``$name`` by the empty string."""
        out = SExpanded("")
        parts = re.split(r"('[^']*'|\"[^\"]*\")", expression)
        for k, part in enumerate(parts):
            parts[k] = part if k % 2 else VARIABLE.sub(lambda match: self._term(match, owner, out), part)
        out.text = "".join(parts)
        return out

    def _term(self, match: re.Match, owner: str, out: SExpanded) -> str:
        negate, sign, name = match.groups()
        if sign == "#":
            if name != owner:
                out.mods.add(name)
            value = (name in self.m_enabled_ids) != bool(negate)
            return "true()" if value else "false()"
        full = name if name in self.m_values else f"{owner}.{name}"
        mod = full.split(".")[0] if "." in full else full
        if mod != owner and (name in self.m_values or name.split(".")[0] in self.m_enabled_ids):
            out.mods.add(mod)
        if full not in self.m_values:
            out.unknown.append(name)
            text = ""
        else:
            text = self.m_values[full]
        quoted = f"'{text}'" if "'" not in text else f'"{text}"'
        return f"not({quoted})" if negate else quoted
