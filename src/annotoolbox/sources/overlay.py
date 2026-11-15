"""An Anno 2070 installation as the game sees it: every path resolved through the archive override order.

The game mounts ``data0..5``, the language pack, ``patch1..8``, ``exedata``, the add-on and its language pack,
then ``patch9``, and a later archive replaces earlier files path by path (engine-notes "Anno 2070 archives";
``classify_archive`` ranks the names that way). The root is the ``maindata`` folder (or the install that holds
it), whose ``.rda`` files are read in place, or a folder with each archive extracted into a sub-folder of its
name (``Data0``, ``patch1``, ``eng0``); both can be mixed. A file inside an ``.rda`` is read directly when the
bytes suffice and extracted into the cache folder when a caller needs a real path (an external tool).
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from ..formats.rda import RdaArchive
from ..paths import local_data_dir

_ARCHIVE_RE = re.compile(
    r"""^(?:
        (?P<data>data)(?P<data_n>\d+)
      | (?P<exedata>exedata)
      | (?P<addon>addon)(?P<addon_n>\d+)
      | (?P<patch>patch)(?P<patch_n>\d+)
      | addon_(?P<alang>[a-z]{3})(?P<alang_n>\d+)
      | (?P<lang>[a-z]{3})(?P<lang_n>\d+)
    )$""",
    re.IGNORECASE | re.VERBOSE,
)

TEXTURE_EXTENSIONS = (".dds", ".png", ".jpg")
CACHE_ENV_VAR = "ANNO2070_CACHE"
STUDIO_PATH = re.compile(
    r"^[a-z]:/(?:[^/]+/)*?(?=(data|shared)/)", re.IGNORECASE
)  # a drive's path up to the game's folders


@dataclass(frozen=True)
class Archive:
    """One archive (extracted folder or ``.rda`` file) and its place in the override order."""

    name: str
    path: Path
    rank: int
    language: str | None = None
    rda: RdaArchive | None = None
    unique_id: str = ""  # names the archive's cache files; ``name`` repeats in 1404 (data0 in maindata and addon)

    @property
    def kind(self) -> str:
        return "rda" if self.rda is not None else "folder"

    @property
    def cache_id(self) -> str:
        return self.unique_id or self.name


def classify_archive(name: str) -> tuple[int, str | None] | None:
    """Return (rank, language) for an archive name, or None if unknown."""
    m = _ARCHIVE_RE.match(name)
    if not m:
        return None
    if m.group("data"):
        return 100 + int(m.group("data_n")), None
    if m.group("lang"):
        return 200 + int(m.group("lang_n")), m.group("lang").lower()
    if m.group("patch"):
        n = int(m.group("patch_n"))
        return (300 if n < 9 else 700) + n, None  # patch9 is mounted after the add-on
    if m.group("exedata"):
        return 400, None
    if m.group("addon"):
        return 500 + int(m.group("addon_n")), None
    if m.group("alang"):
        return 600 + int(m.group("alang_n")), m.group("alang").lower()
    return None


def archive_root(root: str | Path) -> Path:
    """The folder holding the archives: ``root`` itself, or its ``maindata`` folder."""
    root = Path(root)
    if not SourceTree._has_archives(root) and SourceTree._has_archives(root / "maindata"):
        return root / "maindata"
    return root


def archive_paths(root: str | Path) -> list[Path]:
    """Every archive of an install (``.rda`` files and extracted folders with an archive's name), the language packs
    of every language included, without indexing any: what a run's fingerprint is taken over."""
    out = []
    for child in sorted(archive_root(root).iterdir()):
        name = child.name if child.is_dir() else child.stem if child.suffix.lower() == ".rda" else ""
        if name and classify_archive(name):
            out.append(child)
    return out


def normalize(rel: str | Path, *, lower: bool = True, studio: bool = False) -> str:
    """A game path as a key: forward slashes, no ``./``, leading or doubled slash (1800 writes ``maps//x.psd``),
    lower case unless ``lower`` is False; ``studio`` cuts a path on the studio's machine (``d:/projects/anno8/game/
    data/x``, 117) back to its ``data/`` or ``shared/`` folder."""
    s = re.sub(r"/{2,}", "/", str(rel).replace("\\", "/").strip())
    if studio and (rooted := STUDIO_PATH.match(s)):
        s = s[rooted.end() :]
    while s.startswith("./"):
        s = s[2:]
    s = s.lstrip("/")
    return s.lower() if lower else s


def cache_base() -> Path:
    """The cache folder (``CACHE_ENV_VAR``, else under ``%LOCALAPPDATA%``)."""
    env = os.environ.get(CACHE_ENV_VAR)
    return Path(env) if env else local_data_dir() / "cache"


def default_cache_dir(root: Path) -> Path:
    """Per-source cache folder for extracted files and archive indexes."""
    base = cache_base()
    digest = hashlib.sha1(str(root.resolve()).lower().encode("utf-8")).hexdigest()[:10]
    return base / f"{root.name}-{digest}"


class SourceTree:
    """Index of a 2070 installation honouring the archive order."""

    def __init__(
        self,
        root: str | Path,
        language: str = "eng",
        cache_dir: str | Path | None = None,
        *,
        archives: list[Archive] | None = None,
        skip: tuple[str, ...] = (),
    ) -> None:
        """Index the 2070 install at ``root``; with ``archives`` given, index just those (``from_archives``);
        ``skip``: archive names (``patch9``) to leave out, such as the one the island editor wrote."""
        self.root = Path(root)
        self.language = language.lower()
        self.skip = {name.lower() for name in skip}
        self.ignored_folders: list[str] = []
        if archives is None:
            if not self.root.is_dir():
                raise FileNotFoundError(f"source root is not a directory: {self.root}")
            self.root = archive_root(self.root)
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir(self.root)
        self.archives = archives if archives is not None else self._discover()
        # key -> (rank, archive index, stored path inside the archive / physical path)
        self._index: dict[str, tuple[int, int, str]] = {}
        self._build_index()

    @classmethod
    def from_archives(cls, files, cache_dir: str | Path | None = None) -> SourceTree:
        """The same lookups over archives given in their override order (later wins): the other games'
        installs (``othergames.installs``), whose archive names and order 2070's rules do not know."""
        files = [Path(f) for f in files if Path(f).is_file()]
        if not files:
            raise FileNotFoundError("no archives to read")
        root = files[0].parent
        cache = Path(cache_dir) if cache_dir else default_cache_dir(root)
        archives: list[Archive] = []
        seen: set[str] = set()
        for rank, f in enumerate(files):
            uid = f"{f.parent.name}-{f.stem}"
            if uid in seen:  # the same folder and name under two roots
                uid = f"{uid}-{rank}"
            seen.add(uid)
            rda = RdaArchive(f, cache_dir=cache / "index" / uid)
            archives.append(Archive(f.stem, f, rank, None, rda, uid))
        return cls(root, "", cache, archives=archives)

    # Construction

    @staticmethod
    def _has_archives(folder: Path) -> bool:
        if not folder.is_dir():
            return False
        for child in folder.iterdir():
            if child.is_dir() or child.suffix.lower() == ".rda":
                if classify_archive(child.stem if child.is_file() else child.name):
                    return True
        return False

    def _discover(self) -> list[Archive]:
        """The archives of ``root`` in rank order; a name that is no archive goes to ``ignored_folders``."""
        archives: list[Archive] = []
        for child in sorted(self.root.iterdir()):
            if child.is_dir():
                name = child.name
            elif child.suffix.lower() == ".rda":
                name = child.stem
            else:
                continue
            if name.lower() in self.skip:
                continue
            info = classify_archive(name)
            if info is None:
                self.ignored_folders.append(child.name)
                continue
            rank, lang = info
            if lang is not None and lang != self.language:
                continue
            rda = RdaArchive(child, cache_dir=self.cache_dir / "index") if child.is_file() else None
            archives.append(Archive(name, child, rank, lang, rda))
        return sorted(archives, key=lambda a: a.rank)

    def _build_index(self) -> None:
        for i, archive in enumerate(self.archives):  # ascending rank: later writes win
            if archive.rda is not None:
                for entry in archive.rda:
                    self._index[normalize(entry.path)] = (archive.rank, i, entry.path)
            else:
                base = archive.path
                for p in base.rglob("*"):
                    if p.is_file():
                        key = normalize(p.relative_to(base).as_posix())
                        self._index[key] = (archive.rank, i, str(p))

    # Lookups

    def __len__(self) -> int:
        return len(self._index)

    def _hit(self, rel: str | Path) -> tuple[Archive, str] | None:
        hit = self._index.get(normalize(rel))
        if hit is None:
            return None
        return self.archives[hit[1]], hit[2]

    def resolve(self, rel: str | Path) -> Path | None:
        """Return a real file for the path, extracting from an archive if needed."""
        hit = self._hit(rel)
        if hit is None:
            return None
        archive, stored = hit
        if archive.rda is None:
            return Path(stored)
        return self._materialize(archive, stored)

    def _materialize(self, archive: Archive, stored: str) -> Path:
        assert archive.rda is not None
        entry = archive.rda.entries[stored]
        dest = self.cache_dir / "files" / archive.cache_id / normalize(stored)
        try:
            # an archive replaced after the extraction (a game update) leaves a stale file of the same size
            if (
                dest.is_file()
                and dest.stat().st_size == entry.size
                and dest.stat().st_mtime_ns >= archive.path.stat().st_mtime_ns
            ):
                return dest
        except OSError:
            pass
        return archive.rda.extract(stored, dest)

    def locate(self, rel: str | Path) -> str | None:
        """Describe where the path comes from without extracting anything."""
        hit = self._hit(rel)
        if hit is None:
            return None
        archive, stored = hit
        return stored if archive.rda is None else f"{archive.path.name}!{stored}"

    def exists(self, rel: str | Path) -> bool:
        return normalize(rel) in self._index

    def archive_of(self, rel: str | Path) -> str | None:
        hit = self._hit(rel)
        return hit[0].name if hit else None

    def archive_file(self, rel: str | Path) -> Path | None:
        """The archive file (or folder) the path is read from."""
        hit = self._hit(rel)
        return hit[0].path if hit else None

    def entry_of(self, rel: str | Path):
        """The ``RdaEntry`` the path is read from (its timestamp, size), or None for a folder or a missing path."""
        hit = self._hit(rel)
        return hit[0].rda.entries.get(hit[1]) if hit and hit[0].rda is not None else None

    def candidates(self, rel: str | Path) -> list[tuple[str, str]]:
        """Every archive that carries this path, lowest priority first."""
        key = normalize(rel)
        out = []
        for a in self.archives:
            if a.rda is not None:
                for entry in a.rda:
                    if normalize(entry.path) == key:
                        out.append((a.name, f"{a.path.name}!{entry.path}"))
                        break
            else:
                p = a.path / key
                if p.is_file():
                    out.append((a.name, str(p)))
        return out

    def read_bytes(self, rel: str | Path) -> bytes:
        hit = self._hit(rel)
        if hit is None:
            raise FileNotFoundError(rel)
        archive, stored = hit
        if archive.rda is None:
            return Path(stored).read_bytes()
        return archive.rda.read(stored)

    def read_text(self, rel: str | Path, encoding: str = "utf-8", errors: str = "replace") -> str:
        return self.read_bytes(rel).decode(encoding, errors)

    def iter_paths(self, prefix: str = "", suffix: str | None = None) -> Iterator[str]:
        prefix = normalize(prefix)
        suffix = suffix.lower() if suffix else None
        for key in sorted(self._index):
            if prefix and not key.startswith(prefix):
                continue
            if suffix and not key.endswith(suffix):
                continue
            yield key

    def glob(self, pattern: str) -> list[str]:
        pattern = normalize(pattern)
        return sorted(k for k in self._index if fnmatch.fnmatchcase(k, pattern))

    def resolve_texture(self, rel: str | Path) -> Path | None:
        """Resolve a texture reference to a real file (see :meth:`texture_key`)."""
        key = self.texture_key(rel)
        return self.resolve(key) if key else None

    def texture_key(self, rel: str | Path) -> str | None:
        """Return the indexed key a texture reference maps to, or None.

        Configs reference textures as ``maps/foo_diff.png`` but the archives ship
        them as ``maps/foo_diff_0.dds`` (mip 0) with ``_1`` and ``_2`` as smaller
        levels. Try the literal path first, then the shipped variants.
        """
        key = normalize(rel)
        if key in self._index:
            return key
        stem, _dot, _ext = key.rpartition(".")
        if not stem:
            return None
        for ext in TEXTURE_EXTENSIONS:
            for variant in (f"{stem}_0{ext}", f"{stem}{ext}"):
                if variant in self._index:
                    return variant
        return None
