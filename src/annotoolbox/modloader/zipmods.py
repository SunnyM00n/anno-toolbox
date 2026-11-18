"""Mods kept in a ``.zip`` file, read in place: Anno 1800's loader takes a ``.zip`` in a mods folder as mods (117's does
not unpack one). Stdlib only.

``read_zip_mods(archive, anno)`` lists the mods of an archive: ``modinfo.json`` at its root makes one mod, else each
folder at its root that holds a ``modinfo`` makes one (a collection); a mod's nested mods are found as for a folder.
A ``ZipMod`` answers what a ``Mod`` does (``files``, ``read``, ``size_of``) from the archive, nothing is unpacked, and
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

from .mods import MODINFO_NAMES, Mod, mod_from_info, parked, parse_jsonc

ZIP = ".zip"
LOCAL_HEADER = struct.Struct("<4s22xHH")  # the signature, then the lengths of the name and the extra field
LOCAL_SIGNATURE = b"PK\x03\x04"
ENCRYPTED = 0x1


class Listing:
    """An archive's files (name -> size, directories left out), shared by the mods inside it. The directory is read
    once and the archive closed again; ``read`` opens it for the one member."""

    def __init__(self, archive: Path) -> None:
        self.archive = Path(archive)
        self.sizes: dict[str, int] = {}
        self._members: dict[str, zipfile.ZipInfo] = {}
        with zipfile.ZipFile(self.archive) as z:
            for info in z.infolist():
                if not info.is_dir():
                    name = info.filename.replace("\\", "/").lstrip("/")  # the name ``read`` is asked by
                    self.sizes[name], self._members[name] = info.file_size, info

    def read(self, name: str) -> bytes:
        """A member's bytes, from the place the directory gave (stored or deflated); any other kind of member is
        left to ``zipfile``, which reads the directory again."""
        info = self._members.get(name)
        if info is None:
            raise KeyError(f"{self.archive.name} holds no {name}")
        if info.flag_bits & ENCRYPTED or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with zipfile.ZipFile(self.archive) as z:
                return z.read(info)
        with self.archive.open("rb") as fh:
            fh.seek(info.header_offset)
            head = fh.read(LOCAL_HEADER.size)
            if len(head) < LOCAL_HEADER.size or not head.startswith(LOCAL_SIGNATURE):
                raise zipfile.BadZipFile(f"{self.archive.name}: {name} is not where the archive's directory says")
            _signature, name_size, extra_size = LOCAL_HEADER.unpack(head)
            fh.seek(name_size + extra_size, 1)
            packed = fh.read(info.compress_size)
        try:
            data = packed if info.compress_type == zipfile.ZIP_STORED else zlib.decompress(packed, -zlib.MAX_WBITS)
        except zlib.error as exc:
            raise zipfile.BadZipFile(f"{self.archive.name}: {name} cannot be unpacked ({exc})") from exc
        if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
            raise zipfile.BadZipFile(f"{self.archive.name}: {name} is damaged (its checksum differs)")
        return data


@dataclass
class ZipMod(Mod):
    """A mod inside an archive: ``prefix`` is its folder in the archive ("" at the root, else ``Mod/``)."""

    listing: Listing | None = field(default=None, repr=False, compare=False)
    prefix: str = ""

    @property
    def home(self) -> Path:
        return self.listing.archive.parent

    def read(self, rel: str) -> bytes:
        return self.listing.read(self.prefix + rel)

    def is_file(self, rel: str) -> bool:
        return self.prefix + rel in self.listing.sizes

    @property
    def source_root(self) -> str:
        return f"{self.listing.archive}|{self.prefix}"

    def _scan(self) -> None:
        """The archive's files below the prefix and the folders with a ``modinfo`` of their own (nested mods)."""
        if self._files is not None:
            return
        below = {n[len(self.prefix) :]: s for n, s in self.listing.sizes.items() if n.startswith(self.prefix)}
        homes = sorted(
            posixpath.dirname(n)
            for n in below
            if "/" in n and posixpath.basename(n) in MODINFO_NAMES and not any(map(parked, n.split("/")))
        )
        children: list[str] = []
        for home in homes:
            if not any(home.startswith(c + "/") for c in children):
                children.append(home)
        found = {n: s for n, s in below.items() if not any(n.startswith(c + "/") for c in children)}
        self._files, self._sizes = sorted(found), found
        for child in children:
            mod, why = zip_mod(self.listing, f"{self.prefix}{child}/", self.anno)
            if mod is None:
                self._notes.append(why)
                continue
            mod.parent = self
            self._nested.append(mod)


def zip_mod(listing: Listing, prefix: str, anno: int | None) -> tuple[ZipMod | None, str]:
    """The mod whose folder in the archive is ``prefix``, or None and why the loader would skip it."""
    archive = listing.archive
    folder = archive / prefix.rstrip("/") if prefix else archive
    name = next((n for n in MODINFO_NAMES if prefix + n in listing.sizes), None)
    if name is None:
        return None, f"{folder.name}: is disabled. It is missing a modinfo.json file"
    info = parse_jsonc(listing.read(prefix + name).decode("utf-8-sig", errors="replace"))
    if info is None:
        return None, f"{folder.name}: is disabled because of missing modinfo.json (not JSON)"
    mod, why = mod_from_info(folder, info, anno, ZipMod, archive.stem, listing=listing, prefix=prefix)
    return mod, why  # type: ignore[return-value]


def read_zip_mods(archive: Path, anno: int | None = None) -> tuple[list[Mod], list[str]]:
    """The mods in a ``.zip`` and the notes of what was skipped (a ``-`` or ``.`` folder, a bad archive, no mod)."""
    try:
        listing = Listing(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        return [], [f"{Path(archive).name}: is not a readable archive ({exc})"]
    if any(n in listing.sizes for n in MODINFO_NAMES):
        mod, why = zip_mod(listing, "", anno)
        return ([mod] if mod else []), ([] if mod else [why])
    tops = sorted({n.split("/")[0] for n in listing.sizes if "/" in n})
    mods, notes = [], []
    for top in tops:
        if parked(top) or not any(top + "/" + n in listing.sizes for n in MODINFO_NAMES):
            continue
        mod, why = zip_mod(listing, top + "/", anno)
        if mod:
            mods.append(mod)
        else:
            notes.append(why)
    if not mods and not notes:
        notes.append(f"{Path(archive).name}: holds no mod (no modinfo.json at its root or in a folder at its root)")
    return mods, notes
