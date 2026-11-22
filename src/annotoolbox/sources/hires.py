"""Higher-resolution source art supplied beside the game: a folder with one subfolder per kind of art, each file named
by what it stands for; a file there replaces the enlarged game picture of it (docs/ui2070-panels.md
"Higher-resolution sources"). ``from_option`` gives the folder of ``--hires`` or ``ANNO_HIRES_DIR``, or None.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV = "ANNO_HIRES_DIR"
ICONS = "icons"  # 2070's icons, by the GUID icons.xml lists them under: icons/<GUID>.png
KINDS = (ICONS,)
SUFFIXES = (".png", ".tga", ".tif", ".tiff")  # pictures with alpha that Pillow reads


class CHiresSource:
    """The files of one folder of higher-resolution art, looked up by kind and key."""

    def __init__(self, root: Path) -> None:
        self.m_root = Path(root)
        self._m_found: dict[tuple[str, str], Path | None] = {}
        self._m_images: dict[Path, object] = {}

    def find(self, kind: str, key) -> Path | None:
        """The file for ``key`` (a GUID, a name) of art ``kind``, or None; the first suffix of ``SUFFIXES`` found."""
        cache_key = (kind, str(key))
        if cache_key not in self._m_found:
            self._m_found[cache_key] = self._lookup(kind, str(key))
        return self._m_found[cache_key]

    def _lookup(self, kind: str, key: str) -> Path | None:
        """The file named ``key`` under any casing of its suffix (``A.PNG`` too, also on a case-sensitive disk)."""
        folder = self.m_root / kind
        if not folder.is_dir():
            return None
        files = [
            candidate
            for candidate in folder.iterdir()
            if candidate.stem == key and candidate.suffix.lower() in SUFFIXES and candidate.is_file()
        ]
        return min(files, key=lambda candidate: SUFFIXES.index(candidate.suffix.lower()), default=None)

    def image(self, kind: str, key):
        """The file for ``key`` as an RGBA picture (Pillow), read once; None without one."""
        path = self.find(kind, key)
        if path is None:
            return None
        if path not in self._m_images:
            from PIL import Image

            with Image.open(path) as image:
                self._m_images[path] = image.convert("RGBA")
        return self._m_images[path]

    def keys(self, kind: str) -> list[str]:
        """Every key the folder holds for ``kind`` (file names without suffix), sorted."""
        folder = self.m_root / kind
        if not folder.is_dir():
            return []
        return sorted({path.stem for path in folder.iterdir() if path.suffix.lower() in SUFFIXES and path.is_file()})


def from_option(path: Path | None) -> CHiresSource | None:
    """The folder named by the option, else by ``ANNO_HIRES_DIR``; None when neither names a folder."""
    chosen = path or (Path(os.environ[ENV]) if os.environ.get(ENV) else None)
    if chosen is None:
        return None
    if not Path(chosen).is_dir():
        raise FileNotFoundError(f"higher-resolution sources: {chosen} is not a folder")
    return CHiresSource(Path(chosen))
