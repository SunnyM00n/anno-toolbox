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

from ..formats.rda import CRdaArchive
from ..paths import cache_dir

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
class SArchive:
    """One archive (extracted folder or ``.rda`` file) and its place in the override order."""

    name: str
    path: Path
    rank: int
    language: str | None = None
    rda: CRdaArchive | None = None
    unique_id: str = ""  # names the archive's cache files; ``name`` repeats in 1404 (data0 in maindata and addon)

    @property
    def kind(self) -> str:
        return "rda" if self.rda is not None else "folder"

    @property
    def cache_id(self) -> str:
        return self.unique_id or self.name


def classify_archive(name: str) -> tuple[int, str | None] | None:
    """Return (rank, language) for an archive name, or None if unknown."""
    match = _ARCHIVE_RE.match(name)
    if not match:
        return None
    if match.group("data"):
        return 100 + int(match.group("data_n")), None
    if match.group("lang"):
        return 200 + int(match.group("lang_n")), match.group("lang").lower()
    if match.group("patch"):
        patch_number = int(match.group("patch_n"))
        return (300 if patch_number < 9 else 700) + patch_number, None  # patch9 is mounted after the add-on
    if match.group("exedata"):
        return 400, None
    if match.group("addon"):
        return 500 + int(match.group("addon_n")), None
    if match.group("alang"):
        return 600 + int(match.group("alang_n")), match.group("alang").lower()
    return None


def archive_root(root: str | Path) -> Path:
    """The folder holding the archives: ``root`` itself, or its ``maindata`` folder."""
    root = Path(root)
    if not CSourceTree._has_archives(root) and CSourceTree._has_archives(root / "maindata"):
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
    text = str(rel)
    if (
        not studio
        and "\\" not in text
        and "//" not in text
        and not text.startswith(("/", "./"))
        and text == text.strip()
    ):
        return text.lower() if lower else text  # the usual key, already clean: the half-million of an install's list
    text = re.sub(r"/{2,}", "/", text.replace("\\", "/").strip())
    if studio and (rooted := STUDIO_PATH.match(text)):
        text = text[rooted.end() :]
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    return text.lower() if lower else text


def _find_nocase(base: Path, key: str) -> Path | None:
    """The file ``key`` (a lower-case game path) names below ``base``, whatever case its folders and file are
    written in on disk (the index keys are lower case; a case-sensitive file system needs the real spelling)."""
    here = base
    for part in key.split("/"):
        if not part:
            continue
        next_path = here / part
        if not next_path.exists():
            try:
                next_path = next((child for child in here.iterdir() if child.name.lower() == part), None)
            except OSError:
                return None
            if next_path is None:
                return None
        here = next_path
    return here if here.is_file() else None


def files_directly_in(tree, folder: str, suffix: str) -> dict[str, str]:
    """name (the file name without ``suffix``) -> game path of the files of ``tree`` directly in ``folder``, not in
    its subfolders, sorted by name. ``tree`` is anything with ``iter_paths(prefix, suffix=)``."""
    out = {}
    for path in tree.iter_paths(folder, suffix=suffix):
        rel = path[len(folder) :]
        if "/" not in rel:
            out[rel[: -len(suffix)]] = path
    return dict(sorted(out.items()))


def cache_base() -> Path:
    """The cache folder (``CACHE_ENV_VAR``, else the tool's cache folder)."""
    env = os.environ.get(CACHE_ENV_VAR)
    return Path(env) if env else cache_dir()


def default_cache_dir(root: Path) -> Path:
    """Per-source cache folder for extracted files and archive indexes."""
    base = cache_base()
    digest = hashlib.sha1(str(root.resolve()).lower().encode("utf-8")).hexdigest()[:10]
    return base / f"{root.name}-{digest}"


class CSourceTree:
    """Index of a 2070 installation honouring the archive order."""

    def __init__(
        self,
        root: str | Path,
        language: str = "eng",
        cache_dir: str | Path | None = None,
        *,
        archives: list[SArchive] | None = None,
        skip: tuple[str, ...] = (),
    ) -> None:
        """Index the 2070 install at ``root``; with ``archives`` given, index just those (``from_archives``);
        ``skip``: archive names (``patch9``) to leave out, such as the one the island editor wrote."""
        self.m_root = Path(root)
        self.m_language = language.lower()
        self.m_skip = {name.lower() for name in skip}
        self.m_ignored_folders: list[str] = []
        if archives is None:
            if not self.m_root.is_dir():
                raise FileNotFoundError(f"source root is not a directory: {self.m_root}")
            self.m_root = archive_root(self.m_root)
        self.m_cache_dir = Path(cache_dir) if cache_dir else default_cache_dir(self.m_root)
        self.m_archives = archives if archives is not None else self._discover()
        # key -> (rank, archive index, stored path inside the archive / physical path)
        self._m_index: dict[str, tuple[int, int, str]] = {}
        self._m_rda_key_tables: dict[str, dict[str, str]] = {}
        self._build_index()

    @classmethod
    def from_archives(cls, files, cache_dir: str | Path | None = None) -> CSourceTree:
        """The same lookups over archives given in their override order (later wins): the other games'
        installs (``othergames.installs``), whose archive names and order 2070's rules do not know."""
        files = [Path(file) for file in files if Path(file).is_file()]
        if not files:
            raise FileNotFoundError("no archives to read")
        root = files[0].parent
        cache = Path(cache_dir) if cache_dir else default_cache_dir(root)
        archives: list[SArchive] = []
        seen: set[str] = set()
        for rank, file in enumerate(files):
            uid = f"{file.parent.name}-{file.stem}"
            if uid in seen:  # the same folder and name under two roots
                uid = f"{uid}-{rank}"
            seen.add(uid)
            rda = CRdaArchive(file, cache_dir=cache / "index" / uid)
            archives.append(SArchive(file.stem, file, rank, None, rda, uid))
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

    def _discover(self) -> list[SArchive]:
        """The archives of ``root`` in rank order; a name that is no archive goes to ``ignored_folders``."""
        archives: list[SArchive] = []
        for child in sorted(self.m_root.iterdir()):
            if child.is_dir():
                name = child.name
            elif child.suffix.lower() == ".rda":
                name = child.stem
            else:
                continue
            if name.lower() in self.m_skip:
                continue
            archive_info = classify_archive(name)
            if archive_info is None:
                self.m_ignored_folders.append(child.name)
                continue
            rank, language = archive_info
            if language is not None and language != self.m_language:
                continue
            rda = CRdaArchive(child, cache_dir=self.m_cache_dir / "index") if child.is_file() else None
            archives.append(SArchive(name, child, rank, language, rda))
        return sorted(archives, key=lambda archive: archive.rank)

    LIST_NAME = "overlay-index"

    def _build_index(self) -> None:
        from ..infra import buildcache, listcache  # not at the top: the cache modules import this one

        folders = [member.path for member in self.m_archives if member.rda is None]
        files = [member.path for member in self.m_archives if member.rda is not None]
        self._m_signature = buildcache.digest(
            listcache.files_signature(files, buildcache.code_version("sources.overlay")),
            *(listcache.folder_signature(folder) for folder in folders),
            *(f"{member.name}:{member.rank}:{member.path}" for member in self.m_archives),
        )
        self._m_scope = "|".join(
            [str(self.m_root), self.m_language, *sorted(self.m_skip), *(str(member.path) for member in self.m_archives)]
        )
        if self._load_list(listcache.load(self.LIST_NAME, self._m_scope, self._m_signature)):
            return
        for i, archive in enumerate(self.m_archives):  # ascending rank: later writes win
            if archive.rda is not None:
                for entry in archive.rda:
                    self._m_index[normalize(entry.path)] = (archive.rank, i, entry.path)
            else:
                base = archive.path
                for path in base.rglob("*"):
                    if path.is_file():
                        key = normalize(path.relative_to(base).as_posix())
                        self._m_index[key] = (archive.rank, i, str(path))
        listcache.store(self.LIST_NAME, self._m_scope, self._m_signature, self._list())

    def _list(self) -> dict[str, tuple[int, str | int]]:
        """The index as plain data for ``listcache``: the archive's place, and the stored path (0: the key itself)."""
        return {
            key: (archive_index, 0 if stored == key else stored)
            for key, (_rank, archive_index, stored) in self._m_index.items()
        }

    def _load_list(self, kept) -> bool:
        try:
            index = {
                key: (self.m_archives[archive_index].rank, archive_index, key if stored == 0 else stored)
                for key, (archive_index, stored) in kept.items()
            }
        except (AttributeError, TypeError, ValueError, IndexError):  # none kept, or not what ``_list`` writes
            return False
        self._m_index = index
        return True

    # Lookups

    def __len__(self) -> int:
        return len(self._m_index)

    def _hit(self, rel: str | Path) -> tuple[SArchive, str] | None:
        hit = self._m_index.get(normalize(rel))
        if hit is None:
            return None
        return self.m_archives[hit[1]], hit[2]

    def resolve(self, rel: str | Path) -> Path | None:
        """Return a real file for the path, extracting from an archive if needed."""
        hit = self._hit(rel)
        if hit is None:
            return None
        archive, stored = hit
        if archive.rda is None:
            return Path(stored)
        return self._materialize(archive, stored)

    def _materialize(self, archive: SArchive, stored: str) -> Path:
        assert archive.rda is not None
        entry = archive.rda.entries[stored]
        relative = normalize(stored)
        if ".." in relative.split("/") or re.match(r"[a-z]:|/", relative):  # a hostile archive's path leaving the cache
            raise ValueError(f"{archive.name}: the path {stored!r} leaves the cache folder")
        dest = self.m_cache_dir / "files" / archive.cache_id / relative
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
        return normalize(rel) in self._m_index

    def archive_of(self, rel: str | Path) -> str | None:
        hit = self._hit(rel)
        return hit[0].name if hit else None

    def archive_file(self, rel: str | Path) -> Path | None:
        """The archive file (or folder) the path is read from."""
        hit = self._hit(rel)
        return hit[0].path if hit else None

    def entry_of(self, rel: str | Path):
        """The ``SRdaEntry`` the path is read from (its timestamp, size), or None for a folder or a missing path."""
        hit = self._hit(rel)
        return hit[0].rda.entries.get(hit[1]) if hit and hit[0].rda is not None else None

    def candidates(self, rel: str | Path) -> list[tuple[str, str]]:
        """Every archive that carries this path, lowest priority first."""
        key = normalize(rel)
        out = []
        for archive in self.m_archives:
            if archive.rda is not None:
                if (stored := self._rda_keys(archive).get(key)) is not None:
                    out.append((archive.name, f"{archive.path.name}!{stored}"))
            elif (candidate := _find_nocase(archive.path, key)) is not None:
                out.append((archive.name, str(candidate)))
        return out

    def _rda_keys(self, archive: SArchive) -> dict[str, str]:
        """An archive's normalised path -> stored path, made on the first ask (``candidates`` is a diagnostic)."""
        table = self._m_rda_key_tables.get(archive.cache_id)
        if table is None:
            table = {}
            for entry in archive.rda:
                table.setdefault(normalize(entry.path), entry.path)
            self._m_rda_key_tables[archive.cache_id] = table
        return table

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
        for key in sorted(self._m_index):
            if prefix and not key.startswith(prefix):
                continue
            if suffix and not key.endswith(suffix):
                continue
            yield key

    def glob(self, pattern: str) -> list[str]:
        pattern = normalize(pattern)
        return sorted(k for k in self._m_index if fnmatch.fnmatchcase(k, pattern))

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
        if key in self._m_index:
            return key
        stem, _dot, _ext = key.rpartition(".")
        if not stem:
            return None
        for extension in TEXTURE_EXTENSIONS:
            for variant in (f"{stem}_0{extension}", f"{stem}{extension}"):
                if variant in self._m_index:
                    return variant
        return None
