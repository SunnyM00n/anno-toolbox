"""Mods kept in a ``.zip`` file, read in place: Anno 1800's loader takes a ``.zip`` in a mods folder as mods (117's does
not unpack one). Stdlib only.

``read_zip_mods(archive, anno)`` lists the mods of an archive: ``modinfo.json`` at its root makes one mod, else each
folder at its root that holds a ``modinfo`` makes one (a collection); a mod's nested mods are found as for a folder.
A ``ZipMod`` answers what a ``Mod`` does (``files``, ``read``, ``size_of``) from the archive, nothing is unpacked.
The rules and where they come from: ``docs/engine-modloader.md`` "Anno 1800's loader".
"""

from __future__ import annotations

import posixpath
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .mods import MODINFO_NAMES, Mod, mod_from_info, parked, parse_jsonc

ZIP = ".zip"


class Listing:
    """An archive's files (name -> size, directories left out) and one open handle, shared by the mods inside it."""

    def __init__(self, archive: Path) -> None:
        self.archive = Path(archive)
        self.lock = threading.Lock()
        self._zip = zipfile.ZipFile(self.archive)
        self.sizes: dict[str, int] = {}
        for info in self._zip.infolist():
            if not info.is_dir():
                self.sizes[info.filename.replace("\\", "/").lstrip("/")] = info.file_size

    def read(self, name: str) -> bytes:
        with self.lock:
            return self._zip.read(name)


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
