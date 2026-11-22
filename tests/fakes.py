"""Stand-ins for the game's file tree and the asset database: the tree over a dict, the files and the 117 database the
island build reads."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from annotoolbox.othergames import installs
from annotoolbox.sources.anno117 import CAnno117Assets, CAnno117Files
from annotoolbox.sources.assetdb import SAsset
from annotoolbox.sources.overlay import CSourceTree


def _norm(path: str) -> str:
    return path.replace("\\", "/").lower()


class FakeTree:
    """``CSourceTree`` over a dict of game paths to text or bytes: the reads, lookups and listings the island build
    makes. A path is found as written or case- and slash-insensitively, as the real tree finds it. ``broken``
    names files whose read fails; ``archive`` is what every file is said to come from."""

    def __init__(
        self, files: dict[str, str | bytes] | None = None, *, archive: str = "Data3", broken: Iterable[str] = ()
    ) -> None:
        self.files: dict[str, str | bytes] = dict(files or {})
        self.archive = archive
        self.broken = tuple(broken)

    def _key(self, rel: str) -> str:
        if rel in self.files:
            return rel
        want = _norm(rel)
        for key in self.files:
            if _norm(key) == want:
                return key
        raise FileNotFoundError(rel)

    def exists(self, rel: str) -> bool:
        try:
            self._key(rel)
        except FileNotFoundError:
            return False
        return True

    def read_bytes(self, rel: str) -> bytes:
        key = self._key(rel)
        if key in self.broken:
            raise OSError("access denied")
        data = self.files[key]
        return data if isinstance(data, bytes) else data.encode("utf-8")

    def read_text(self, rel: str, encoding: str = "utf-8") -> str:
        key = self._key(rel)
        if key in self.broken:
            raise OSError("access denied")
        data = self.files[key]
        return data if isinstance(data, str) else data.decode(encoding)

    def texture_key(self, rel: str) -> str | None:
        """The file a texture name loads, by the real tree's rule: as written, else ``<stem>_0`` or ``<stem>``
        with a texture extension."""
        stem = _norm(rel).rpartition(".")[0]
        found = [rel] + [f"{stem}{n}{ext}" for ext in (".dds", ".png", ".jpg") for n in ("_0", "")]
        return next((self._key(p) for p in found if self.exists(p)), None)

    def iter_paths(self, prefix: str = "", suffix: str | None = None) -> Iterator[str]:
        return (p for p in self.files if p.startswith(prefix) and (suffix is None or p.endswith(suffix)))

    def glob(self, pattern: str) -> list[str]:
        return sorted(p for p in self.files if fnmatch.fnmatchcase(p, pattern))

    def archive_of(self, rel: str) -> str:
        return self.archive


class FakeFiles:
    """``CAnno117Files`` over a dict of game paths to bytes; ``default`` is what a path not in it reads as
    (None: not found)."""

    def __init__(self, files: dict[str, bytes] | None = None, *, default: bytes | None = None) -> None:
        self.files = dict(files or {})
        self.default = default

    def read(self, path: str) -> bytes:
        if path in self.files:
            return self.files[path]
        if self.default is not None:
            return self.default
        raise FileNotFoundError(path)

    def exists(self, path: str) -> bool:
        return path in self.files

    def iter_paths(self, prefix: str = "") -> list[str]:
        return sorted(p for p in self.files if p.startswith(prefix))

    def list(self, prefix: str) -> list[str]:
        """The editor's ``islandview.CFiles.list`` (a mod's files and the game's)."""
        return self.iter_paths(prefix)


class FakeDb:
    """The asset database as far as a reader asks it by GUID."""

    def __init__(self, *assets: SAsset) -> None:
        self.assets = {a.guid: a for a in assets}

    def by_guid(self, guid: int) -> SAsset | None:
        return self.assets.get(guid)

    def by_template(self, template: str):
        return (a for a in self.assets.values() if a.template == template)


def assets117(text: str) -> CAnno117Assets:
    """117's asset list given as text, no install needed."""
    source = CAnno117Assets.__new__(CAnno117Assets)
    source.m_text = text
    source._m_templates = None
    return source


def asset_xml(template: str, guid: int, name: str, body: str = "", *, standard: str = "", tail: str = "") -> str:
    """One ``<Asset>`` as 117's asset list writes it: ``standard`` adds to the Standard block, ``tail`` follows
    the body inside Values."""
    return (
        f"<Asset><Template>{template}</Template><Values><Standard><GUID>{guid}</GUID><Name>{name}</Name>{standard}"
        f"</Standard>{body}{tail}</Values></Asset>"
    )


INSTALL_VARS = {
    "anno1404": "ANNO1404_DIR",
    "anno1701": "ANNO1701_DIR",
    "anno2070": "ANNO2070_DIR",
    "anno2205": "ANNO2205_DIR",
    "anno1800": "ANNO1800_DIR",
    "anno117": "ANNO117_DIR",
}


def installed(game: str):
    """An installed game's files for a real-data test, read by the game's archive rules: the folder its variable in
    ``INSTALL_VARS`` names, or None without one (the test then skips). 117 gives ``CAnno117Files``, the rest a tree."""
    folder = os.environ.get(INSTALL_VARS[game])
    if not folder or not Path(folder).is_dir():
        return None
    if game == "anno2070":
        return CSourceTree(folder)
    if game == "anno117":
        return CAnno117Files(folder)
    return installs.GAMES[game].tree(Path(folder))


def value_definition(name: str, kind: str, extra: str = "", items: str = "") -> str:
    inner = f"<Items>{items}</Items>" if items else ""
    return f"<ValueDefinition><DataType>{kind}</DataType><Name>{name}</Name>{extra}{inner}</ValueDefinition>"


class FakeListedTree(FakeTree):
    """What 117's ``Files`` adds to a tree: ``list`` by prefix."""

    def list(self, prefix: str) -> list[str]:
        return sorted(p for p in self.files if p.startswith(prefix))
