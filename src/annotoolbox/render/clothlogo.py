"""The player logo on 117's cloth: the logo texture the game puts into its run-time atlas table, rebuilt from the
game's own logo data (the ``PlayerLogo`` assets) for the games' cloth shader to read.

``default_logo(files)`` is the texture of the first logo the install holds (or of ``guid``), ``atlas_word`` its entry
in ``cTextureAtlas`` (the bindless index, bit 31 for an sRGB format); ``render.clothmesh`` binds it into a cloth draw.
docs/engine-cloth.md "The logo atlas".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import ddsdecode

ASSETS = "data/base/config/export/assets.xml"
LOGO_MAP = "<player logo>"  # the name its map has in a draw's texture table
ATLAS = "cTextureAtlas"
ATLAS_BUFFER = "cb_" + ATLAS  # what the structured buffer becomes (render.gameshader)
LOGO_INDEX = 1  # the atlas entry given to the logo (0 is "none")
SRGB_BIT = 1 << 31  # an atlas entry: the texture is stored as sRGB
_BLOCK = re.compile(r"<Template>PlayerLogo</Template>(.*?)</Asset>", re.S)
_FIELD = r"<{0}>([^<]*)</{0}>"


@dataclass(frozen=True)
class Logo:
    """A ``PlayerLogo`` asset: its GUID, name and the picture file its ``DefaultLogo`` names (as written)."""

    guid: int
    name: str
    path: str


def logos(text: str) -> list[Logo]:
    """The ``PlayerLogo`` assets of the asset file's text that name a ``DefaultLogo``, in file order."""
    out = []
    for block in _BLOCK.findall(text):
        guid, name, path = (re.search(_FIELD.format(t), block) for t in ("GUID", "Name", "DefaultLogo"))
        if guid and path and path.group(1):
            out.append(Logo(int(guid.group(1)), name.group(1) if name else "", path.group(1)))
    return out


def texture_candidates(path: str) -> list[str]:
    """The files a ``.png`` the assets name is stored as: its ``_0.dds`` twin in the folder written and in ``4k``."""
    twin = re.sub(r"\.png$", "_0.dds", path, flags=re.I)
    return [twin, twin.replace("/fhd/", "/4k/")]


def has_logos(files) -> bool:
    """Whether the game's files hold the asset list (a cheap test: the other games hold none)."""
    return bool(files.exists(ASSETS))


def default_logo(files, guid: int | None = None) -> ddsdecode.DdsFile | None:
    """The texture of the logo ``guid`` (the first one whose file is there when None), None without one."""
    data = files.read(ASSETS) if has_logos(files) else None
    if not data:
        return None
    for logo in logos(data.decode("utf-8", "replace")):
        if guid is not None and logo.guid != guid:
            continue
        for candidate in texture_candidates(logo.path):
            if files.exists(candidate):
                try:
                    return ddsdecode.DdsFile(files.read(candidate))
                except ddsdecode.UnsupportedDds:
                    break
    return None


def atlas_word(slot: int, dds) -> int:
    """The atlas entry of a texture at bindless index ``slot``: the index, with bit 31 set for an sRGB format."""
    return (slot & 0x7FFFFFFF) | (SRGB_BIT if dds.info.srgb else 0)
