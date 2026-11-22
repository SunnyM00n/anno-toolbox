"""The pictures of a game's UI: an ``ImageAsset``'s ``ImageId`` (or a picture named at run time) found among the
game's textures and decoded to RGBA, at the smallest stored level that still covers the size it is drawn at.

``CPictures(files).get(asset, image_id, size)`` is the entry; a picture is a ``SPicture`` (its RGBA as numpy), so any
drawing backend can take it. The name rules: ``x.png`` is stored as ``x_0.dds``; the ``fhd`` folder is stored as
``4k`` (docs/ui-editor.md, "Pictures"). Needs numpy (the ``desktop`` extra).
"""

from __future__ import annotations

import io
import math
import re
import zlib
from dataclasses import dataclass

import numpy as np

from ..models import ddsdecode
from . import fields, previewdefaults

MAX_SIDE = 2048  # a picture larger than this is read at a smaller level
ICON_FOLDER = re.compile(r"/(?:icon[a-z_]*|3dicons|icons)/")  # where the games keep their icon pictures
GENERIC_WORDS = {"icon", "image", "picture", "texture", "dynamic", "sprite"}  # no help in choosing an icon
# the icon folders the game draws for a widget of such a name, most first (seen in the game, 2026-11-16: "Icon" 38 %
# generic, 31 % attributes; the "2DHolder" pictures buildings and goods); the rest of the names: generic, attributes
FOLDERS = (("portrait", ("/portraits/",)), ("logo", ("/logos/",)), ("emblem", ("/logos/",)),
           ("2dholder", ("/building/", "/production_goods/")), ("production", ("/production_goods/",)),
           ("good", ("/production_goods/",)), ("status", ("/city_status/",)), ("need", ("/building/", "/generic/")),
           ("minimap", ("/hud_minimap/",)), ("", ("/icon_content/generic/", "/icon_content/attributes/")))  # fmt: skip


@dataclass
class SPicture:
    key: str  # the name and the size level it was decoded at: what a drawing backend keeps its own copies by
    rgba: np.ndarray  # (height, width, 4) uint8

    @property
    def width(self) -> int:
        return int(self.rgba.shape[1])

    @property
    def height(self) -> int:
        return int(self.rgba.shape[0])


def candidates(image_id: str) -> list[str]:
    """The game paths a picture's name may be stored under, the likeliest first."""
    name = image_id.replace("\\", "/").lower().strip()
    stem = re.sub(r"\.(png|tga|jpg|dds)$", "", name)
    out = [f"{stem}_0.dds", f"{stem}.dds"]
    if stem.startswith("data/ui/fhd/"):
        out.insert(0, "data/ui/4k/" + stem[len("data/ui/fhd/") :] + "_0.dds")
    if "/2kimages/" in stem:
        out.append(stem.replace("/2kimages/", "/4kimages/") + "_0.dds")
    if name.endswith((".png", ".tga", ".jpg")):
        out.append(name)  # the History Editions ship their pictures as plain files at the name
    return out


def size_level(size_px) -> int:
    """The size a picture is drawn at as a power-of-two step (0: up to 1 pixel, 7: 65 to 128): pictures drawn at
    sizes of one step are decoded at the same stored level, a larger step is decoded again."""
    want = max(float(size_px[0]), float(size_px[1]), 1.0)
    return (math.ceil(want) - 1).bit_length()


class CPictures:
    """Decoded pictures by name, kept; ``missing`` the names no texture was found for."""

    def __init__(self, files) -> None:
        self.m_files = files
        self._m_cache: dict[tuple[str, int], SPicture | None] = {}
        self.m_missing: set[str] = set()
        self.m_standins: set[str] = set()  # the run-time slots no game icon could stand in for
        self._m_samples: dict[tuple[str, str], SPicture | None] = {}
        self._m_icons: list[str] | None = None
        self.m_defaults = None  # the preview's ``previewdefaults.SDefaults`` (None: the campaign's)

    def image_id(self, asset: str | None) -> str | None:
        root = self.m_files.root(asset) if asset else None
        return fields.get(root, "ImageId") if root is not None else None

    def get(self, asset: str | None = None, image_id: str | None = None, size_px=(0.0, 0.0)) -> SPicture | None:
        """The picture of an image asset, or of a picture name; None when the game has neither."""
        name = image_id or self.image_id(asset)
        if not name:
            return None
        level = size_level(size_px)
        if (name, level) in self._m_cache:
            return self._m_cache[name, level]
        found = None
        for path in candidates(name):
            if self.m_files.m_tree.exists(path):
                found = self._decode(name, path, size_px, level)
                if found is not None:
                    break
        if found is None:
            self.m_missing.add(name)
        self._m_cache[name, level] = found
        return found

    def sample(self, slot: str, size_px=(0.0, 0.0)) -> SPicture | None:
        """A real icon of the game for a picture slot the game fills at run time (docs/ui-editor.md "Pictures"): the
        picture of the character the slot names (a portrait, an emblem: ``previewdefaults.picture_for``), else the
        icon whose file name shares the longest word with the slot's name, else one picked by the name; None when the
        game has no icon files (the canvas then draws its tile, and ``standins`` lists the slot)."""
        defaults = self.m_defaults or previewdefaults.CAMPAIGN_DEFAULTS
        key = (slot, defaults.key())
        if key not in self._m_samples:
            named = previewdefaults.picture_for(slot, defaults)
            found = self.get(image_id=named, size_px=size_px) if named else None
            path = None if found is not None else self._sample_path(slot)
            found = found or (self.get(image_id=path, size_px=size_px) if path else None)
            self._m_samples[key] = found
            if found is None:
                self.m_standins.add(slot)
        return self._m_samples[key]

    def _icon_paths(self) -> list[str]:
        if self._m_icons is None:
            paths = getattr(self.m_files.m_tree, "m_paths", None) or ()
            self._m_icons = [path for path in paths if ICON_FOLDER.search(path) and path.endswith(("_0.dds", ".png"))]
        return self._m_icons

    def _sample_path(self, slot: str) -> str | None:
        """An icon for a run-time slot: one sharing the longest word of the slot's name, from the folders the game
        draws for such a name (``FOLDERS``) where they hold one, else from every icon folder."""
        icons = self._icon_paths()
        if not icons:
            return None
        low = slot.lower().replace("_", "")
        folders = next(folder_name for word, folder_name in FOLDERS if word in low)
        icons = [path for path in icons if any(folder_name in path for folder_name in folders)] or icons
        words = [
            candidate_word
            for candidate_word in re.findall(r"[A-Z]?[a-z]+|\d+", slot)
            if len(candidate_word) > 3 and candidate_word.lower() not in GENERIC_WORDS
        ]
        for word in sorted(words, key=len, reverse=True):
            hits = [path for path in icons if word.lower() in path.rsplit("/", 1)[-1]]
            if hits:
                return hits[zlib.crc32(slot.encode()) % len(hits)]
        return icons[zlib.crc32(slot.encode()) % len(icons)]

    def _decode(self, name: str, path: str, size_px, level: int) -> SPicture | None:
        key = f"{name}@{level}"
        if not path.endswith(".dds"):
            return self._decode_plain(key, path)
        try:
            dds = ddsdecode.CDdsFile(self.m_files.m_tree.read(path))
            mip = 0
            want = max(float(size_px[0]), float(size_px[1]), 1.0)
            while mip + 1 < dds.m_info.mips:
                smaller_covers = max(dds.size_of(mip + 1)) >= want
                if not (smaller_covers or max(dds.size_of(mip)) > MAX_SIDE):
                    break
                mip += 1
            return SPicture(key, np.ascontiguousarray(dds.level(mip)))
        except (ddsdecode.CUnsupportedDds, ValueError, IndexError, OSError):
            return None

    def _decode_plain(self, name: str, path: str) -> SPicture | None:
        """A PNG, TGA or JPEG file (needs Pillow, an optional extra)."""
        try:
            from PIL import Image

            with Image.open(io.BytesIO(self.m_files.m_tree.read(path))) as image:
                return SPicture(name, np.ascontiguousarray(np.asarray(image.convert("RGBA"), dtype=np.uint8)))
        except (ImportError, OSError, ValueError):
            return None
