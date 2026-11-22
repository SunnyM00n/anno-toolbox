"""The installed Anno 117 as the editor reads it.

``CAnno117Assets`` reads the ``<Asset>`` blocks of 117's asset list by GUID, ``CAnno117Files`` any file of the
install (``mod.asset117`` writes assets from what they give).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from ..formats import filedb
from ..formats.rda import CRdaArchive
from ..infra import buildcache, listcache
from .overlay import normalize

ASSETS_XML_PATH = "data/base/config/export/assets.xml"
TEMPLATES_XML_PATH = "data/base/config/export/templates.xml"
ASSET_TAG = re.compile(r"</?Asset>")


def _newest_archive(config: Path, path: str) -> CRdaArchive:
    """The archive the game reads ``path`` from: the last patch archive beside ``config.rda`` that ships it, else
    ``config.rda`` (a game update may ship a new asset list in a patch; engine-notes "Anno 117 archives")."""
    for patch in sorted(config.parent.glob("zz_patchfiles_*.rda"), reverse=True):
        archive = CRdaArchive(patch)
        if any(normalize(stored) == normalize(path) for stored in archive.entries):
            return archive
    return CRdaArchive(config)


class CAnno117Assets:
    """Random access to ``<Asset>`` blocks of Anno 117's asset list by GUID."""

    def __init__(self, install_root: str | Path) -> None:
        root = Path(install_root)
        config = root / "maindata" / "config.rda"
        if not config.is_file():
            config = root / "config.rda"
        if not config.is_file():
            raise FileNotFoundError(f"config.rda not found under {install_root}")
        self._m_config = config
        self.m_text = _newest_archive(config, ASSETS_XML_PATH).read(ASSETS_XML_PATH).decode("utf-8", errors="replace")
        self._m_templates: str | None = None

    @property
    def maindata(self) -> Path:
        """The install's archive folder (the patch archives name the game build)."""
        return self._m_config.parent

    @property
    def templates_text(self) -> str:
        """``templates.xml``: each template's defaults (read on first use)."""
        if self._m_templates is None:
            raw = _newest_archive(self._m_config, TEMPLATES_XML_PATH).read(TEMPLATES_XML_PATH)
            self._m_templates = raw.decode("utf-8", errors="replace")
        return self._m_templates

    def asset_xml(self, guid: int) -> str:
        """The ``<Asset>`` block of ``guid`` as text."""
        # The asset's own GUID opens its Standard block (a reference to it stands elsewhere); assets nest
        # (quests embed assets), so the block ends at the close tag of its own depth.
        match = re.search(rf"<Standard>\s*<GUID>{guid}</GUID>", self.m_text)
        if match is None:
            raise KeyError(f"no asset with GUID {guid} in {ASSETS_XML_PATH}")
        start = self.m_text.rfind("<Asset>", 0, match.start())
        depth = 0
        for tag in ASSET_TAG.finditer(self.m_text, start):  # from ``start``: no copy of the rest of a 100 MB list
            depth += 1 if tag.group() == "<Asset>" else -1
            if depth == 0:
                return self.m_text[start : tag.end()]
        raise ValueError(f"unbalanced <Asset> tags around GUID {guid}")


ARCHIVE_INDEX_SUBDIR = "index117"
ARCHIVE_MAP = "file_h.db"
LIST_NAME, HOLDERS_NAME = "anno117-index", "anno117-holders"  # what ``listcache`` keeps of an install
FLAG_DELETED = 8  # a path the game does not have any more (the entry stays in the map)


@dataclass(frozen=True, slots=True)  # the archive map holds about half a million: no dict each
class SMapEntry:
    archive: str  # the archive's file name, as ``maindata/<name>`` is listed in the map
    flags: int

    @property
    def deleted(self) -> bool:
        return bool(self.flags & FLAG_DELETED)


class CArchiveMapError(ValueError):
    """``file_h.db`` is not a readable archive map."""


def read_archive_map(data: bytes) -> dict[str, SMapEntry]:
    """``file_h.db`` (docs/formats/archive-map.md): normalised path -> the archive the game reads it from."""
    try:
        return _read_entries(data)
    except (filedb.CFileDBError, struct.error, UnicodeDecodeError) as error:
        raise CArchiveMapError(f"not a readable archive map: {error}") from error


def _read_entries(data: bytes) -> dict[str, SMapEntry]:
    root = filedb.read(data).find("ArchiveMap")
    if root is None or root.find("FileMap") is None or root.find("ArchiveFiles") is None:
        raise CArchiveMapError("not an archive map: no ArchiveMap/FileMap/ArchiveFiles")
    archives = [
        _utf16(entry.children[0].data).rsplit("/", 1)[-1].lower()
        for entry in root.find("ArchiveFiles").children
        if entry.children
    ]
    out: dict[str, SMapEntry] = {}
    files = root.find("FileMap").children
    for tag in files:
        name = tag.find("FileName") if isinstance(tag, filedb.STag) else None
        if name is None:
            continue
        index = tag.find("ArchiveFileIndex")  # a missing index is the first archive
        flags = tag.find("Flags")
        archive_index = struct.unpack("<I", index.data)[0] if index else 0
        if archive_index < len(archives):
            out[normalize(_utf16(name.data))] = SMapEntry(
                archives[archive_index], struct.unpack("<I", flags.data)[0] if flags else 0
            )
    while files:  # let go a few at a time: the half-million tags freed at once held the interpreter for 0.2 s
        del files[-1024:]
    return out


def _utf16(raw: bytes) -> str:
    return raw.decode("utf-16-le")


def game_build(files) -> str:
    """The newest patch archive of the install (an ``CAnno117Files``): the build a mod records as the one it was
    generated from."""
    names = sorted(archive.m_path.name for archive in getattr(files, "m_archives", []) if hasattr(archive, "m_path"))
    patches = [name for name in names if name.startswith("zz_patchfiles")]
    return (patches or names or ["unknown"])[-1]


class CAnno117Files:
    """Lookup of game paths across every ``.rda`` of an Anno 117 install."""

    def __init__(self, install_root: str | Path, cache_dir: str | Path | None = None) -> None:
        root = Path(install_root)
        maindata = root / "maindata" if (root / "maindata").is_dir() else root
        self.m_archives = [
            CRdaArchive(archive_path, cache_dir=Path(cache_dir) / ARCHIVE_INDEX_SUBDIR if cache_dir else None)
            for archive_path in sorted(maindata.glob("*.rda"))
        ]
        self._m_holders: dict[str, list[str]] | None = None  # path -> every archive carrying it, made when asked
        self._m_db = maindata / ARCHIVE_MAP
        self._m_map: dict[str, SMapEntry] | None = None
        self._m_scope = str(maindata)
        self._m_signature = listcache.files_signature(
            [*(member.m_path for member in self.m_archives), self._m_db], buildcache.code_version("sources.anno117")
        )
        self._m_index: dict[str, tuple[CRdaArchive, str]] = {}
        self._m_deleted: list[str] = []
        if self._load_kept():  # the same archives as when the list was made: no archive is opened
            return
        # by name: the patches are zz_patchfiles_<3 digits>, so they sort last and in order (engine-notes
        # "Anno 117 archives"); a later archive overrides the earlier ones
        for archive in self.m_archives:
            for entry in archive:
                self._m_index[normalize(entry.path)] = (archive, entry.path)
        if self.archive_map is not None:
            self._apply_archive_map()
        self._m_deleted = self._find_deleted()
        listcache.store(LIST_NAME, self._m_scope, self._m_signature, self._kept())

    @property
    def archive_map(self) -> dict[str, SMapEntry] | None:
        """The archive map (``file_h.db``), read on first use; None when the install has none."""
        if self._m_map is None and self._m_db.is_file():
            self._m_map = read_archive_map(self._m_db.read_bytes())
        return self._m_map

    def _apply_archive_map(self) -> None:
        """The game's own choice: the archive the map names, and no file where it marks the path deleted. A path the
        map does not list keeps the name rule's archive (the loose and the mod files are not in it)."""
        by_name = {archive_entry.m_path.name.lower(): archive_entry for archive_entry in self.m_archives}
        keys: dict[str, dict[str, str]] = {}  # archive name -> normalised path -> stored path, built when needed
        for key, entry in self.archive_map.items():
            hit = self._m_index.get(key)
            archive = by_name.get(entry.archive)
            if entry.deleted or archive is None:
                self._m_index.pop(key, None)
            elif hit is not None and hit[0] is not archive:
                table = keys.setdefault(entry.archive, {normalize(stored): stored for stored in archive.entries})
                if key in table:
                    self._m_index[key] = (archive, table[key])

    def _load_kept(self) -> bool:
        kept = listcache.load(LIST_NAME, self._m_scope, self._m_signature)
        try:
            index = {
                key: (self.m_archives[archive_index], key if stored == 0 else stored)
                for key, (archive_index, stored) in kept["index"].items()
            }
            deleted = list(kept["deleted"])
        except (TypeError, KeyError, IndexError, ValueError):  # not what ``_kept`` writes: made again
            return False
        self._m_index, self._m_deleted = index, deleted
        return True

    def _kept(self) -> dict:
        """The index as plain data for ``listcache``: the archive's place in ``archives``, and the stored path
        (0 where it is the key itself)."""
        place = {id(archive): i for i, archive in enumerate(self.m_archives)}
        index = {
            k: (place[id(archive)], 0 if stored == k else stored) for k, (archive, stored) in self._m_index.items()
        }
        return {"index": index, "deleted": self._m_deleted}

    def exists(self, path: str) -> bool:
        return normalize(path) in self._m_index

    def archive_of(self, path: str) -> str | None:
        """The name of the archive the game reads ``path`` from (the one that wins), None when it has no such file."""
        hit = self._m_index.get(normalize(path))
        return hit[0].m_path.name if hit else None

    def holders(self, path: str) -> list[str]:
        """Every archive that carries ``path``, in the order they are read (the name rule's: the last would win); the
        archive map may choose another or mark the path deleted (``archive_of``, ``deleted``)."""
        if self._m_holders is None:
            self._m_holders = listcache.cached(HOLDERS_NAME, self._m_scope, self._m_signature, self._all_holders)
        return list(self._m_holders.get(normalize(path), []))

    def _all_holders(self) -> dict[str, list[str]]:
        held: dict[str, list[str]] = {}
        for archive in self.m_archives:
            name = archive.m_path.name  # one string object for the archive: the stored list shares it
            for entry in archive:
                held.setdefault(normalize(entry.path), []).append(name)
        return held

    def deleted(self) -> list[str]:
        """The paths an archive carries but the archive map marks deleted: files the game does not have."""
        return list(self._m_deleted)

    def _find_deleted(self) -> list[str]:
        if self.archive_map is None:
            return []
        return sorted(key for key, entry in self.archive_map.items() if entry.deleted and self.holders(key))

    def read(self, path: str) -> bytes:
        hit = self._m_index.get(normalize(path))
        if hit is None:
            raise FileNotFoundError(path)
        archive, stored = hit
        return archive.read(stored)

    def iter_paths(self, prefix: str, suffix: str | None = None) -> list[str]:
        prefix = normalize(prefix)
        suffix = suffix.lower() if suffix else ""
        return sorted(
            stored for key, (_a, stored) in self._m_index.items() if key.startswith(prefix) and key.endswith(suffix)
        )
