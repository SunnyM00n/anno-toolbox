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


class HiresSource:
    """The files of one folder of higher-resolution art, looked up by kind and key."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._found: dict[tuple[str, str], Path | None] = {}
        self._images: dict[Path, object] = {}

    def find(self, kind: str, key) -> Path | None:
        """The file for ``key`` (a GUID, a name) of art ``kind``, or None; the first suffix of ``SUFFIXES`` found."""
        at = (kind, str(key))
        if at not in self._found:
            folder = self.root / kind
            self._found[at] = next((p for s in SUFFIXES if (p := folder / f"{key}{s}").is_file()), None)
        return self._found[at]

    def image(self, kind: str, key):
        """The file for ``key`` as an RGBA picture (Pillow), read once; None without one."""
        path = self.find(kind, key)
        if path is None:
            return None
        if path not in self._images:
            from PIL import Image

            with Image.open(path) as img:
                self._images[path] = img.convert("RGBA")
        return self._images[path]

    def keys(self, kind: str) -> list[str]:
        """Every key the folder holds for ``kind`` (file names without suffix), sorted."""
        folder = self.root / kind
        if not folder.is_dir():
            return []
        return sorted({p.stem for p in folder.iterdir() if p.suffix.lower() in SUFFIXES and p.is_file()})


def from_option(path: Path | None) -> HiresSource | None:
    """The folder named by the option, else by ``ANNO_HIRES_DIR``; None when neither names a folder."""
    chosen = path or (Path(os.environ[ENV]) if os.environ.get(ENV) else None)
    if chosen is None:
        return None
    if not Path(chosen).is_dir():
        raise FileNotFoundError(f"higher-resolution sources: {chosen} is not a folder")
    return HiresSource(Path(chosen))
