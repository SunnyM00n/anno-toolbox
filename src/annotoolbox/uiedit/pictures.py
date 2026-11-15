"""The pictures of a game's UI: an ``ImageAsset``'s ``ImageId`` (or a picture named at run time) found among the
game's textures and decoded to RGBA, at the smallest stored level that still covers the size it is drawn at.

``Pictures(files).get(asset, image_id, size)`` is the entry; a picture is a ``Picture`` (its RGBA as numpy), so any
drawing backend can take it. The name rules: ``x.png`` is stored as ``x_0.dds``; the ``fhd`` folder is stored as
``4k`` (docs/ui-editor.md, "Pictures"). Needs numpy (the ``desktop`` extra).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import numpy as np

from ..models import ddsdecode
from . import fields

MAX_SIDE = 2048  # a picture larger than this is read at a smaller level


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
