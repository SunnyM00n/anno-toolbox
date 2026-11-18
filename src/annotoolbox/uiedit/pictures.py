"""The pictures of a game's UI: an ``ImageAsset``'s ``ImageId`` (or a picture named at run time) found among the
game's textures and decoded to RGBA, at the smallest stored level that still covers the size it is drawn at.

``Pictures(files).get(asset, image_id, size)`` is the entry; a picture is a ``Picture`` (its RGBA as numpy), so any
drawing backend can take it. The name rules: ``x.png`` is stored as ``x_0.dds``; the ``fhd`` folder is stored as
``4k`` (docs/ui-editor.md, "Pictures"). Needs numpy (the ``desktop`` extra).
"""

from __future__ import annotations

import io
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
class Picture:
    key: str
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


class Pictures:
    """Decoded pictures by name, kept; ``missing`` the names no texture was found for."""

    def __init__(self, files) -> None:
        self.files = files
        self._cache: dict[str, Picture | None] = {}
        self.missing: set[str] = set()
        self.standins: set[str] = set()  # the run-time slots no game icon could stand in for
        self._samples: dict[tuple[str, str], Picture | None] = {}
        self._icons: list[str] | None = None
        self.defaults = None  # the preview's ``previewdefaults.Defaults`` (None: the campaign's)

    def image_id(self, asset: str | None) -> str | None:
        root = self.files.root(asset) if asset else None
        return fields.get(root, "ImageId") if root is not None else None

    def get(self, asset: str | None = None, image_id: str | None = None, size=(0.0, 0.0)) -> Picture | None:
        """The picture of an image asset, or of a picture name; None when the game has neither."""
        name = image_id or self.image_id(asset)
        if not name:
            return None
        if name in self._cache:
            return self._cache[name]
        found = None
        for path in candidates(name):
            if self.files.tree.exists(path):
                found = self._decode(name, path, size)
                if found is not None:
                    break
        if found is None:
            self.missing.add(name)
        self._cache[name] = found
        return found

    def sample(self, slot: str, size=(0.0, 0.0)) -> Picture | None:
        """A real icon of the game for a picture slot the game fills at run time (docs/ui-editor.md "Pictures"): the
        picture of the character the slot names (a portrait, an emblem: ``previewdefaults.picture_for``), else the
        icon whose file name shares the longest word with the slot's name, else one picked by the name; None when the
        game has no icon files (the canvas then draws its tile, and ``standins`` lists the slot)."""
        defaults = self.defaults or previewdefaults.CAMPAIGN_DEFAULTS
        key = (slot, defaults.key())
        if key not in self._samples:
            named = previewdefaults.picture_for(slot, defaults)
            found = self.get(image_id=named, size=size) if named else None
            path = None if found is not None else self._sample_path(slot)
            found = found or (self.get(image_id=path, size=size) if path else None)
            self._samples[key] = found
            if found is None:
                self.standins.add(slot)
        return self._samples[key]

    def _icon_paths(self) -> list[str]:
        if self._icons is None:
            paths = getattr(self.files.tree, "paths", None) or ()
            self._icons = [p for p in paths if ICON_FOLDER.search(p) and p.endswith(("_0.dds", ".png"))]
        return self._icons

    def _sample_path(self, slot: str) -> str | None:
        """An icon for a run-time slot: one sharing the longest word of the slot's name, from the folders the game
        draws for such a name (``FOLDERS``) where they hold one, else from every icon folder."""
        icons = self._icon_paths()
        if not icons:
            return None
        low = slot.lower().replace("_", "")
        folders = next(f for word, f in FOLDERS if word in low)
        icons = [p for p in icons if any(f in p for f in folders)] or icons
        words = [w for w in re.findall(r"[A-Z]?[a-z]+|\d+", slot) if len(w) > 3 and w.lower() not in GENERIC_WORDS]
        for word in sorted(words, key=len, reverse=True):
            hits = [p for p in icons if word.lower() in p.rsplit("/", 1)[-1]]
            if hits:
                return hits[zlib.crc32(slot.encode()) % len(hits)]
        return icons[zlib.crc32(slot.encode()) % len(icons)]

    def _decode(self, name: str, path: str, size) -> Picture | None:
        if not path.endswith(".dds"):
            return self._decode_plain(name, path)
        try:
            dds = ddsdecode.DdsFile(self.files.tree.read(path))
            mip = 0
            want = max(float(size[0]), float(size[1]), 1.0)
            while mip + 1 < dds.info.mips:
                smaller_covers = max(dds.size_of(mip + 1)) >= want
                if not (smaller_covers or max(dds.size_of(mip)) > MAX_SIDE):
                    break
                mip += 1
            return Picture(name, np.ascontiguousarray(dds.level(mip)))
        except (ddsdecode.UnsupportedDds, ValueError, IndexError, OSError):
            return None

    def _decode_plain(self, name: str, path: str) -> Picture | None:
        """A PNG, TGA or JPEG file (needs Pillow, an optional extra)."""
        try:
            from PIL import Image

            with Image.open(io.BytesIO(self.files.tree.read(path))) as image:
                return Picture(name, np.ascontiguousarray(np.asarray(image.convert("RGBA"), dtype=np.uint8)))
        except (ImportError, OSError, ValueError):
            return None
