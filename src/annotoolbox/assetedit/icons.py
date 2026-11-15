"""The icons of assets, from the game's own icon files: the PNG or DDS a ``Standard/IconFilename`` names, or the tile
of an atlas that ``icons.xml`` gives a GUID (2070, 1404, 1701), decoded once.

``IconSource(read)`` takes ``read(path)`` (the game file's bytes, FileNotFoundError or KeyError without it); ``key_of``
names where an asset's icon is, ``image`` decodes a key (off the window's thread: a ``QImage``), ``icon`` and ``picture``
make the tree's small icon and the Icons tab's picture. ``use_atlases`` switches the atlas route on (docs/asset-editor.md).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImage, QPixmap

from ..infra.i18n import tr
from ..models import ddsdecode
from ..sources import icons2070
from .assetdoc import AssetDocument

SIZE = 28  # pixels: the tree row's icon


def candidates(path: str) -> list[str]:
    """The files that can hold the picture an icon path names: the path itself, then the ``_0`` texture the games
    ship beside a UI picture, as PNG and as DDS, and the same under the folders 117 ships its pictures in (a path
    names the ``fhd`` size; the files are ``4k``, also under ``2kimages``)."""
    homes = [path]
    if "/ui/fhd/" in path:
        homes += [path.replace("/ui/fhd/", "/ui/4k/", 1), path.replace("/ui/fhd/", "/ui/2kimages/4k/", 1)]
    out = []
    for home in homes:
        stem = home.rsplit(".", 1)[0] if "." in home.rsplit("/", 1)[-1] else home
        out += [home, f"{stem}_0.png", f"{stem}.png", f"{stem}_0.dds", f"{stem}.dds"]
    return list(dict.fromkeys(out))


def decode(data: bytes, longest: int = 256) -> QImage | None:
    """A PNG (or any format Qt reads) or a DDS as an image; None where neither reads. A DDS is read at the first
    mip level that is at most ``longest`` pixels along its long side (an icon needs no more)."""
    image = QImage.fromData(data)
    if not image.isNull():
        return image
    if data[:4] == b"DDS ":
        try:
            dds = ddsdecode.DdsFile(data)
            mip = next((m for m in range(dds.info.mips) if max(dds.size_of(m)) <= longest), dds.info.mips - 1)
            rgba = dds.level(mip)
        except (ddsdecode.UnsupportedDds, ValueError, IndexError):
            return None
        height, width = rgba.shape[:2]
        return QImage(rgba.tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
    return None


ATLAS = "atlas:"  # a key ``atlas:<guid>`` (``atlas:<guid>:<n>`` for the n-th icon): a tile of icons.xml, not a file


def atlas_key(guid: int, position: int = 0) -> str:
    return f"{ATLAS}{guid}" + (f":{position}" if position else "")


def _split_key(key: str) -> tuple[int, int]:
    guid, _, position = key[len(ATLAS) :].partition(":")
    return int(guid), int(position or 0)


class IconSource:
    def __init__(self, read: Callable[[str], bytes]) -> None:
        self.read = read
        self._images: dict[str, QImage | None] = {}
        self._atlases: dict[int, icons2070.IconAtlas] = {}
        self._refs: dict[int, list[icons2070.IconRef]] = {}
        self.edits: dict[int, list[list[int]]] = {}  # the open change set's icons (``ChangeSet.icons``), live

    def use_atlases(self, atlases: dict, refs: dict) -> None:
        """The game's atlases and GUID -> icons table (``icons2070.load_icon_data``); the tree's keys follow them."""
        self._atlases, self._refs = atlases, refs
        self._images.clear()  # another game's pictures are not this one's

    def image(self, key: str) -> QImage | None:
        if key not in self._images:
            self._images[key] = self._tile(*_split_key(key)) if key.startswith(ATLAS) else self._load(key)
        return self._images[key]

    def _load(self, path: str) -> QImage | None:
        for candidate in candidates(path):
            try:
                found = decode(self.read(candidate))
            except (FileNotFoundError, KeyError, OSError):
                continue
            if found is not None:
                return found
        return None

    @property
    def editable(self) -> bool:
        """Whether the game keeps its icons in ``icons.xml`` (the atlases are known): an icon can be set."""
        return bool(self._atlases)

    def count_of(self, guid: int) -> int:
        """How many icons the asset's entry has (the game's, or the change set's when it adds one)."""
        return max(len(self._refs.get(guid, ())), len(self.edits.get(guid, ())))

    def ref_of(self, guid: int, position: int = 0) -> icons2070.IconRef | None:
        """The asset's icon number ``position``: the change set's, else the game's."""
        edited = self.edits.get(guid, [])
        if position < len(edited) and edited[position]:
            return icons2070.IconRef(*edited[position])
        game = self._refs.get(guid, [])
        return game[position] if position < len(game) else None

    def forget(self, guid: int) -> None:
        """The asset's tiles changed: decode them again next time."""
        for key in [k for k in self._images if k.startswith(ATLAS) and _split_key(k)[0] == guid]:
            del self._images[key]

    def _tile(self, guid: int, position: int) -> QImage | None:
        ref = self.ref_of(guid, position)
        atlas = self._atlases.get(ref.file_id) if ref else None
        sheet = self.image(atlas.path) if atlas else None
        box = icons2070.tile_box(atlas, sheet.width(), sheet.height(), ref.index) if sheet is not None else None
        return sheet.copy(*box) if box else None

    @staticmethod
    def path_of(doc: AssetDocument, guid: int) -> str:
        found = doc.layers(guid).lookup(("Standard", "IconFilename")) if doc.exists(guid) else None
        return (found.text if found else "").strip()

    def key_of(self, doc: AssetDocument, guid: int) -> str:
        """Where the asset's icon is: the file its ``Standard/IconFilename`` names, else its atlas tile; "" for none."""
        path = self.path_of(doc, guid)
        if path:
            return path
        ref = self.ref_of(guid)
        return atlas_key(guid) if ref is not None and ref.file_id in self._atlases else ""

    @staticmethod
    def icon(image: QImage | None) -> QIcon | None:
        if image is None:
            return None
        return QIcon(QPixmap.fromImage(image.scaled(SIZE, SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                                                    Qt.TransformationMode.SmoothTransformation)))  # fmt: skip

    def describe(self, key: str) -> str:
        """The line the Icons tab shows for a key: the file path, or which tile of which atlas."""
        if not key.startswith(ATLAS):
            return key
        ref = self.ref_of(*_split_key(key))
        atlas = self._atlases.get(ref.file_id) if ref else None
        return tr("tile {index} of {path}", index=ref.index, path=atlas.path) if ref and atlas else key

    def picture(self, key: str) -> QPixmap:
        image = self.image(key) if key else None
        return QPixmap.fromImage(image) if image is not None else QPixmap()
