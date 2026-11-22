"""Mods kept in a ``.zip`` file, read in place: Anno 1800's loader takes a ``.zip`` in a mods folder as mods (117's does
not unpack one). Stdlib only.

``read_zip_mods(archive, anno)`` lists the mods of an archive: ``modinfo.json`` at its root makes one mod, else each
folder at its root that holds a ``modinfo`` makes one (a collection); a mod's nested mods are found as for a folder.
A ``CZipMod`` answers what a ``CMod`` does (``files``, ``read``, ``size_of``) from the archive, nothing is unpacked, and
the archive is open only while a member is read (a mod lives as long as its window: the file must stay free).
The rules and where they come from: ``docs/engine-modloader.md`` "Anno 1800's loader".
"""

from __future__ import annotations

import posixpath
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .mods import MODINFO_NAMES, CMod, mod_from_info, parked, parse_jsonc

ZIP = ".zip"
LOCAL_HEADER = struct.Struct("<4s22xHH")  # the signature, then the lengths of the name and the extra field
LOCAL_SIGNATURE = b"PK\x03\x04"
ENCRYPTED = 0x1


# a mod file larger than this is not a mod file; a deflate bomb claims little and inflates to more
MAX_MEMBER_BYTES = 1 << 30


def _inflate(packed: bytes, size: int) -> bytes:
    """The deflate stream's bytes; ``OverflowError`` when it holds more than the ``size`` the directory states."""
    stream = zlib.decompressobj(-zlib.MAX_WBITS)
    data = stream.decompress(packed, size + 1)
    if len(data) > size or stream.unconsumed_tail:
        raise OverflowError(size)
    return data + stream.flush()


class CListing:
    """An archive's files (name -> size, directories left out), shared by the mods inside it. The directory is read
    once and the archive closed again; ``read`` opens it for the one member."""

    def __init__(self, archive: Path) -> None:
        self.m_archive = Path(archive)
        self.m_sizes: dict[str, int] = {}
        self._m_members: dict[str, zipfile.ZipInfo] = {}
        with zipfile.ZipFile(self.m_archive) as z:
            for member in z.infolist():
                if not member.is_dir():
                    name = member.filename.replace("\\", "/").lstrip("/")  # the name ``read`` is asked by
                    self.m_sizes[name], self._m_members[name] = member.file_size, member

    def read(self, name: str) -> bytes:
        """A member's bytes, from the place the directory gave (stored or deflated); any other kind of member is
        left to ``zipfile``, which reads the directory again."""
        member = self._m_members.get(name)
        if member is None:
            raise KeyError(f"{self.m_archive.name} holds no {name}")
        if member.flag_bits & ENCRYPTED or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with zipfile.ZipFile(self.m_archive) as z:
                return z.read(member)
        with self.m_archive.open("rb") as file_handle:
            file_handle.seek(member.header_offset)
            head = file_handle.read(LOCAL_HEADER.size)
            if len(head) < LOCAL_HEADER.size or not head.startswith(LOCAL_SIGNATURE):
                raise zipfile.BadZipFile(f"{self.m_archive.name}: {name} is not where the archive's directory says")
            _signature, name_size, extra_size = LOCAL_HEADER.unpack(head)
            file_handle.seek(name_size + extra_size, 1)
            packed = file_handle.read(member.compress_size)
        if member.file_size > MAX_MEMBER_BYTES:
            raise zipfile.BadZipFile(f"{self.m_archive.name}: {name} claims {member.file_size} bytes, over the limit")
        try:
            data = packed if member.compress_type == zipfile.ZIP_STORED else _inflate(packed, member.file_size)
        except zlib.error as exc:
            raise zipfile.BadZipFile(f"{self.m_archive.name}: {name} cannot be unpacked ({exc})") from exc
        except OverflowError:
            raise zipfile.BadZipFile(f"{self.m_archive.name}: {name} inflates past its stated size") from None
        if len(data) != member.file_size or zlib.crc32(data) != member.CRC:
            raise zipfile.BadZipFile(f"{self.m_archive.name}: {name} is damaged (its checksum differs)")
        return data


@dataclass
class CZipMod(CMod):
    """A mod inside an archive: ``prefix`` is its folder in the archive ("" at the root, else ``CMod/``)."""

    m_listing: CListing | None = field(default=None, repr=False, compare=False)
    m_prefix: str = ""

    @property
    def home(self) -> Path:
        return self.m_listing.m_archive.parent

    def read(self, rel: str) -> bytes:
        return self.m_listing.read(self.m_prefix + rel)

    def is_file(self, rel: str) -> bool:
        return self.m_prefix + rel in self.m_listing.m_sizes

    @property
    def source_root(self) -> str:
        return f"{self.m_listing.m_archive}|{self.m_prefix}"

    def _scan(self) -> None:
        """The archive's files below the prefix and the folders with a ``modinfo`` of their own (nested mods)."""
        if self._m_files is not None:
            return
        below = {
            name[len(self.m_prefix) :]: size_bytes
            for name, size_bytes in self.m_listing.m_sizes.items()
            if name.startswith(self.m_prefix)
        }
        homes = sorted(
            posixpath.dirname(name)
            for name in below
            if "/" in name and posixpath.basename(name) in MODINFO_NAMES and not any(map(parked, name.split("/")))
        )
        children: list[str] = []
        for home in homes:
            if not any(home.startswith(nested + "/") for nested in children):
                children.append(home)
        found = {
            name: size_bytes
            for name, size_bytes in below.items()
            if not any(name.startswith(nested + "/") for nested in children)
        }
        self._m_files, self._m_sizes = sorted(found), found
        for child in children:
            mod, why = zip_mod(self.m_listing, f"{self.m_prefix}{child}/", self.m_anno)
            if mod is None:
                self._m_notes.append(why)
                continue
            mod.m_parent = self
            self._m_nested.append(mod)


def zip_mod(listing: CListing, prefix: str, anno: int | None) -> tuple[CZipMod | None, str]:
    """The mod whose folder in the archive is ``prefix``, or None and why the loader would skip it."""
    archive = listing.m_archive
    folder = archive / prefix.rstrip("/") if prefix else archive
    name = next((known for known in MODINFO_NAMES if prefix + known in listing.m_sizes), None)
    if name is None:
        return None, f"{folder.name}: is disabled. It is missing a modinfo.json file"
    member = parse_jsonc(listing.read(prefix + name).decode("utf-8-sig", errors="replace"))
    if member is None:
        return None, f"{folder.name}: is disabled because of missing modinfo.json (not JSON)"
    mod, why = mod_from_info(folder, member, anno, CZipMod, archive.stem, m_listing=listing, m_prefix=prefix)
    return mod, why  # type: ignore[return-value]


def read_zip_mods(archive: Path, anno: int | None = None) -> tuple[list[CMod], list[str]]:
    """The mods in a ``.zip`` and the notes of what was skipped (a ``-`` or ``.`` folder, a bad archive, no mod)."""
    try:
        listing = CListing(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        return [], [f"{Path(archive).name}: is not a readable archive ({exc})"]
    if any(name in listing.m_sizes for name in MODINFO_NAMES):
        mod, why = zip_mod(listing, "", anno)
        return ([mod] if mod else []), ([] if mod else [why])
    tops = sorted({name.split("/")[0] for name in listing.m_sizes if "/" in name})
    mods, notes = [], []
    for top in tops:
        if parked(top) or not any(top + "/" + name in listing.m_sizes for name in MODINFO_NAMES):
            continue
        mod, why = zip_mod(listing, top + "/", anno)
        if mod:
            mods.append(mod)
        else:
            notes.append(why)
    if not mods and not notes:
        notes.append(f"{Path(archive).name}: holds no mod (no modinfo.json at its root or in a folder at its root)")
    return mods, notes
