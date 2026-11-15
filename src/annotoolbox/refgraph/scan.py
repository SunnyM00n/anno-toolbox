"""What one game file names: the file names found in its bytes, each turned into the file the game loads for it, and a
sound bank's media by id.

``carriers(paths)`` says which files are read, ``name_patterns(paths)`` are the regexes that find names (built from the
game's own suffixes), ``file_refs`` returns a ``Ref`` (file named, the asset it stands in, the field) for each name and ``bank_refs`` the media a
bank plays. ``file_refs`` runs one finder per way a file names another (``_written_names``, ``_by_value``, ``_wwise_ids``,
``_joined_names``): a new way is a new finder in its tuple. The rules and their limits: docs/references.md.
"""

from __future__ import annotations

import bisect
import itertools
import posixpath
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import NamedTuple

from ..exporting import banks
from ..models.fileresolve import loaded_file
from ..models.fileusers import ASSET_START, asset_at
from ..sources.overlay import normalize
from .wwise import BANK, WwiseIndex

# Suffixes of files that hold media or baked data and no names: never read (a game's textures, meshes, sounds, ...).
PAYLOAD = frozenset(
    "dds png jpg jpeg tga bmp psd gif exr hdr cur ico rdm gr2 bg2 fbx wav mp3 wem ogg sp bik bk2 mp4 ttf otf ttc "
    "fxo ctt tmc bfg pyc exe ifo isc a7tinfo a7minfo bin dat data checksum preview db".split()
)  # fmt: skip  (db: Windows' thumbs.db lists the pictures of its folder; it names nothing)
SOURCE_SUFFIXES = frozenset("psd fbx tga png bmp jpg dds".split())  # names of the studio's sources: no game ships them
SKIP_BYTES = 1 << 30  # a file over this is no carrier (none is: 1800's largest asset file is 290 MB)


def carriers(paths: Iterable[str]) -> list[str]:
    """The files that may name other files: everything but the payload suffixes (and the banks, read apart)."""
    return [p for p in paths if suffix_of(p) not in PAYLOAD and not p.endswith(BANK)]


def suffix_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1] if "." in name else ""


@dataclass(frozen=True)
class NamePatterns:
    """The regexes that find a name a file writes: ``plain`` (one byte a character, UTF-8 or a code page) and ``wide``
    (UTF-16LE, the way the credits' texts, the 2070 interface scripts and 1404's templates write them)."""

    plain: re.Pattern[bytes]
    wide: re.Pattern[bytes]


NAME_CHARS = rb"[\w$./\\\x80-\xff-]"  # a letter, digit or path sign; a byte over 0x7f is part of a UTF-8 letter


def name_patterns(paths: Iterable[str]) -> NamePatterns:
    """The patterns of a game: a path-like word ending in one of the game's own suffixes (and the studio's source
    suffixes)."""
    own = {s for p in paths if (s := suffix_of(p)) and len(s) <= 12 and s.isalnum()}
    suffixes = sorted(own | SOURCE_SUFFIXES, key=len, reverse=True)
    ending = "|".join(map(re.escape, suffixes)).encode()
    plain = re.compile(NAME_CHARS + rb"{3,300}\.(?:" + ending + rb")(?![\w])", re.I)
    wide_ending = b"|".join(b"\x00".join(bytes([c]) for c in s.encode()) + b"\x00" for s in suffixes)
    wide = re.compile(rb"(?:" + NAME_CHARS + rb"\x00){3,300}\.\x00(?:" + wide_ending + rb")(?!\w\x00)", re.I)
    return NamePatterns(plain, wide)


# Fields whose value is a file's name without its folder or suffix, and the suffix of the files they name: 117 and
# 1800 find an island by ``<NameOfIsland>``, in the folder ``<name>/`` of any island pool (docs/references.md).
VALUE_FIELDS = {"NameOfIsland": ".a7m"}
VALUE_FIELD = re.compile(rb"<(" + b"|".join(map(str.encode, VALUE_FIELDS)) + rb")>\s*([^<>\s][^<>]{0,200}?)\s*</\1>")
WWISE_ID = re.compile(rb"<WwiseID>\s*(\d+)\s*</WwiseID>")
# ``"head/with/folder/" .. expression .. "tail.suffix"`` in a Lua script (or ``+`` in a Python one), on one line
JOIN = re.compile(
    rb"""["']([\w$./\\ -]*[/\\][\w$./\\ -]*)["']\s*(?:\.\.|\+)[^"'\n;]*?(?:\.\.|\+)\s*["']([\w$./\\ -]*\.\w{2,5})["']"""
)
MIN_FOLDERS = 2  # a head names at least this many folders, or it would match half the game
MAX_JOINED = 500  # a join that matches more files than this is a guess, not a name
_OPEN_TAG = re.compile(r"<([\w.]+)>")
_TAG_BEFORE = re.compile(r"<([\w.]+)>\s*$")
FIRE_FOLDER = "extractedfirefiles/"  # where 2205's atlas entries (``.\PNGs\icons\x.png``) lie, below ``data/ui/``


def fire_names(name: str) -> list[str]:
    """The files 2205's Flash movies (``.feu``) mean by a picture name: ``ExtractedFireFiles\\pngs\\UIAssets\\x.png``
    lies without the exporter's ``pngs`` folder, an atlas entry ``.\\PNGs\\icons\\x.png`` below ``extractedfirefiles/``,
    ``.\\blobs\\x.bfd`` below ``data/ui/``. Names to try after the one as written, from the movie's folder."""
    flat = name.replace("\\", "/")
    bare = re.sub(r"(?i)(^|/)pngs/", r"\1", flat.removeprefix("./"))
    return [bare, FIRE_FOLDER + bare] if flat.startswith("./") else [bare]


class Resolver:
    """A name -> the file the game loads, kept per name; a name that is no path from the top is tried from the folder of
    the file that writes it (a script's or a config's neighbour). ``paths`` (the game's files) let a name alone find
    the files of ``VALUE_FIELDS``."""

    def __init__(
        self,
        exists: Callable[[str], bool],
        game: str,
        paths: Iterable[str] = (),
        wwise: WwiseIndex | None = None,
    ) -> None:
        self.exists, self.game, self.wwise = exists, game, wwise
        self._found: dict[str, str] = {}
        self._near: dict[tuple[str, str], str] = {}
        self._stems: dict[tuple[str, str], list[str]] = {}  # (suffix, lower-case stem) -> the files
        self._basenames: dict[str, list[str]] = {}  # a file name -> the files of that name
        self._sorted = sorted(paths := list(paths))  # for the files that start with a head
        for p in paths:
            suffix = "." + suffix_of(p)
            self._basenames.setdefault(posixpath.basename(p), []).append(p)
            if suffix in VALUE_FIELDS.values():
                self._stems.setdefault((suffix, posixpath.basename(p)[: -len(suffix)].lower()), []).append(p)

    def by_name(self, field: str, value: str) -> list[str]:
        """The files a field names by its value alone (every file of that suffix with that name)."""
        return self._stems.get((VALUE_FIELDS[field], value.lower()), [])

    def joined(self, head: str, tail: str) -> list[str]:
        """The files that start with ``head`` and end with ``tail`` (both as a script writes them); none when the head
        names fewer than ``MIN_FOLDERS`` folders or the join matches more than ``MAX_JOINED`` files."""
        head, tail = normalize(head), normalize(tail)
        if head.count("/") < MIN_FOLDERS:
            return []
        at = bisect.bisect_left(self._sorted, head)
        found = []
        while at < len(self._sorted) and self._sorted[at].startswith(head):
            if self._sorted[at].endswith(tail) and len(self._sorted[at]) >= len(head) + len(tail):
                found.append(self._sorted[at])
            at += 1
        return found if len(found) <= MAX_JOINED else []

    def of(self, name: str, folder: str) -> str:
        found = self._found.get(name)
        if found is None:
            found = loaded_file(name, self.exists, self.game)
            self._found[name] = found
        if found or not folder:
            return found
        near = (folder, name)
        if near not in self._near:
            path = posixpath.join(folder, name.replace("\\", "/"))
            self._near[near] = loaded_file(path, self.exists, self.game) or self.alone(name) or self.fire(name, folder)
        return self._near[near]

    def fire(self, name: str, folder: str) -> str:
        """A picture name of a 2205 Flash movie (``fire_names``), from the movie's folder."""
        if self.game != "anno2205" or "\\" not in name:
            return ""
        for form in fire_names(name):
            if found := loaded_file(posixpath.join(folder, form), self.exists, self.game):
                return found
        return ""

    def alone(self, name: str) -> str:
        """A bare file name (no folder: a text writes ``010c.jpg``, the picture lies in ``ui/datalog/``) that exactly
        one file of the game bears."""
        if "/" in name or "\\" in name:
            return ""
        files = self._basenames.get(name.lower(), [])
        return files[0] if len(files) == 1 else ""


_WORD = re.compile(NAME_CHARS + rb"+$")  # the word that ends right before a position
MAX_SPACES = 6  # words a folder or file name may be made of (``third party``, ``world events``, ``builder residence``)
Found = Iterator[tuple[int, list[str]]]  # (position in the file, the files a name there finds)


def _text_of(raw: bytes) -> str:
    """A name's bytes as text: UTF-8 (an umlaut in a folder name), else one byte a character."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _with_spaces(m: re.Match[bytes], data: bytes, resolver: Resolver, folder: str) -> str:
    """The file a match names. The pattern stops at a space, so a name with spaces matches only its last word: when
    that finds no file, the words before it (up to ``MAX_SPACES``) are put back one at a time."""
    found = resolver.of(_text_of(m.group()), folder)
    start = m.start()
    for _ in range(MAX_SPACES):
        if found or start < 2 or data[start - 1 : start] != b" ":
            break
        word = _WORD.search(data, max(0, start - 300), start - 1)
        if word is None:
            break
        start = word.start()
        found = resolver.of(_text_of(data[start : m.end()]), folder)
    return found


def _written_names(data: bytes, patterns: NamePatterns, resolver: Resolver, folder: str) -> Found:
    """The names a file writes out as paths, one byte a character and (where the file holds any ``.`` + NUL) UTF-16."""
    for m in patterns.plain.finditer(data):
        yield m.start(), [_with_spaces(m, data, resolver, folder)]
    if b".\x00" in data:  # the cheap test first: a text file has no NUL
        for m in patterns.wide.finditer(data):
            yield m.start(), [resolver.of(m.group().decode("utf-16-le", "replace"), folder)]


def _by_value(data: bytes, resolver: Resolver) -> Found:
    """The files named by the value of a field that gives a file's name alone (``VALUE_FIELDS``)."""
    if not any(f"<{tag}>".encode() in data for tag in VALUE_FIELDS):  # the cheap test first: most files have none
        return
    for m in VALUE_FIELD.finditer(data):
        yield m.start(), resolver.by_name(m.group(1).decode("latin-1"), m.group(2).decode("latin-1"))


def _joined_names(data: bytes, resolver: Resolver) -> Found:
    """The files a script names by joining pieces: ``"data/x/pic_" .. n .. ".bkg"`` names every file of that folder that
    starts with the literal head and ends with the literal tail (``JOIN``). A name with no literal folder in its head
    (``base .. ".bkg"``) is out of reach."""
    if b".." not in data and b"+" not in data:
        return
    for m in JOIN.finditer(data):
        yield m.start(), resolver.joined(_text_of(m.group(1)), _text_of(m.group(2)))


def _wwise_ids(data: bytes, resolver: Resolver) -> Found:
    """The files of the sound events and banks an asset gives by their Wwise id (``wwise.WwiseIndex``)."""
    if resolver.wwise is None or b"<WwiseID>" not in data:
        return
    for m in WWISE_ID.finditer(data):
        yield m.start(), resolver.wwise.files_of(int(m.group(1)))


class Ref(NamedTuple):
    """One name that found a file: the file, and where it stands in an asset file (the asset's GUID and name, and the
    element the name is the value of; all empty outside an asset)."""

    file: str
    guid: str = ""
    asset: str = ""
    field: str = ""


def _field_at(text: str, position: int) -> str:
    """The element a name at ``position`` is the value of (``<Filename>x.cfg``), or the element that starts there."""
    tag = _OPEN_TAG.match(text, position) or _TAG_BEFORE.search(text, max(0, position - 80), position)
    return tag.group(1) if tag else ""


def file_refs(data: bytes, source: str, patterns: NamePatterns, resolver: Resolver) -> list[Ref]:
    """A ``Ref`` for every name in ``data`` that finds a file; each (file, asset) once, a file never naming itself."""
    text = data.decode("latin-1")
    folder = posixpath.dirname(source)
    starts = [m.start() for m in ASSET_START.finditer(text)] if "<Asset>" in text else []
    seen: set[tuple[str, str]] = set()
    out: list[Ref] = []
    found_in = (
        _written_names(data, patterns, resolver, folder),
        _by_value(data, resolver),
        _wwise_ids(data, resolver),
        _joined_names(data, resolver),
    )
    for position, files in itertools.chain(*found_in):
        owner = asset_at(text, starts, position) if starts else None
        for found in files:
            guid = str(owner[0]) if owner else ""
            if found and found != source and (found, guid) not in seen:
                seen.add((found, guid))
                out.append(Ref(found, guid, owner[1], _field_at(text, position)) if owner else Ref(found))
    return out


def bank_refs(bank: str, data: bytes, exists: Callable[[str], bool], read: Callable[[str], bytes]) -> list[str]:
    """The media files a sound bank plays (by id, as ``exporting.banks.media_file`` finds them), and the bank's own
    info file; a bank that does not read names nothing."""
    try:
        listing = banks.bank_listing(data)
        info = banks.read_info(bank, exists, lambda p: read(p).decode("utf-8", "replace"))
    except (ValueError, KeyError, IndexError, OSError):
        return []
    ids = {s.media for s in listing.sounds} | set(info.media)
    found = {banks.media_file(bank, i, info, exists) for i in ids}
    own = bank[: -len(BANK)] + ".json"
    return sorted(p for p in found | {own if exists(own) else ""} if p)


def banks_of(paths: Iterable[str]) -> list[str]:
    return [p for p in paths if p.endswith(BANK)]


def asset_label(path: str, guid: str) -> str:
    """The source a use is kept under: the file, with the asset's GUID after ``#`` when it stands in one."""
    return f"{path}#{guid}" if guid else path


def split_source(source: str) -> tuple[str, str]:
    """A source as ``asset_label`` made it -> (file, GUID or "")."""
    path, _, guid = source.partition("#")
    return path, guid
