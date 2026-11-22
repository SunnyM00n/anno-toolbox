"""Build-menu icons of Anno 2070 (and the icon atlases of 1404 and 1701, which keep the same scheme).

``icons.xml`` maps an asset GUID to ``(IconFileID, IconIndex)``, ``iconfilemap.xml`` the file id to the atlas image
and its tile size; tiles run row by row. The atlas is named ``.png`` and shipped as ``<name>_0.dds`` (1404 and 1701
name a ``.tga``). ``load_icon_data(read, exists)`` reads both files of any of the games, ``CIconTable`` cuts a tile for
a mod's build, ``set_icons`` changes the icons of some GUIDs in the file's own text.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from ..models.textures import dds_to_png
from .overlay import CSourceTree, normalize

ICONS_FILE = "data/config/game/icons.xml"
ICON_FILE_MAP = "data/config/gui/iconfilemap.xml"
ICONS_FILES = (ICONS_FILE, "addondata/config/game/icons.xml")  # 1404's add-on adds entries; later files win


@dataclass(frozen=True)
class SIconAtlas:
    file_id: int
    path: str  # game path of the atlas image as referenced (``.png``)
    tile_width: int
    tile_height: int


@dataclass(frozen=True)
class SIconRef:
    file_id: int
    index: int


def parse_atlases(root: ET.Element) -> dict[int, SIconAtlas]:
    """The game's icon atlases (``IsGameIcon``); the tool-only ones are left out."""
    out = {}
    for element in root.iter("IconFile"):
        try:
            fid = int(element.findtext("IconFileID", ""))
            atlas = SIconAtlas(
                fid,
                normalize(element.findtext("IconFilename", "")),
                int(element.findtext("IconWidth", "")),
                int(element.findtext("IconHeight", "")),
            )
        except ValueError:
            continue
        if element.findtext("IsGameIcon", "1") == "1" and atlas.tile_width > 0 and atlas.tile_height > 0:
            out[fid] = atlas
    return out


def parse_icons(root: ET.Element) -> dict[int, list[SIconRef]]:
    """GUID -> every icon of its entry, in the file's order (1404's variations come after the first); a missing
    IconIndex is tile 0 (the sawmill's entry has none). An entry without an icon is left out."""
    out = {}
    for element in root.iter("i"):
        guid_text = element.findtext("GUID")
        if guid_text is None:
            continue
        try:
            refs = [
                SIconRef(int(icon.findtext("IconFileID", "")), int(icon.findtext("IconIndex", "0")))
                for icon in element.findall("Icons/i")
            ]
            guid = int(guid_text)
        except ValueError:
            continue
        if refs:
            out[guid] = refs
    return out


def load_icon_data(read, exists) -> tuple[dict[int, SIconAtlas], dict[int, list[SIconRef]]]:
    """The atlases and the GUID -> icons table of a game, through ``read(path)`` and ``exists(path)``; empty where
    the game has none of the files."""
    atlases = parse_atlases(ET.fromstring(read(ICON_FILE_MAP))) if exists(ICON_FILE_MAP) else {}
    icons: dict[int, list[SIconRef]] = {}
    for path in ICONS_FILES:
        if exists(path):
            icons |= parse_icons(ET.fromstring(read(path)))
    return atlases, icons


def tile_box(atlas: SIconAtlas, width_px: int, height_px: int, index: int) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) of tile ``index`` in an atlas image of ``width`` x ``height``, None when it lies outside."""
    per_row = max(1, width_px // atlas.tile_width)
    x = (index % per_row) * atlas.tile_width
    y = (index // per_row) * atlas.tile_height
    if y + atlas.tile_height > height_px:
        return None
    return x, y, atlas.tile_width, atlas.tile_height


class CIconTable:
    """GUID -> icon lookup plus lazy atlas decoding."""

    def __init__(self, tree: CSourceTree, work_dir: Path) -> None:
        self.m_tree = tree
        self.m_work_dir = work_dir
        self._m_decoded: dict[int, object] = {}  # file id -> PIL image
        self.m_atlases, self.m_icons = load_icon_data(tree.read_bytes, tree.exists)

    def lookup(self, guid: int) -> SIconRef | None:
        refs = self.m_icons.get(guid)
        if not refs or refs[0].file_id not in self.m_atlases:
            return None
        return refs[0]

    def _atlas_image(self, file_id: int):
        image = self._m_decoded.get(file_id)
        if image is None:
            from PIL import Image  # lazy: Pillow is only needed to cut the icons out

            atlas = self.m_atlases[file_id]
            texture_path = self.m_tree.resolve_texture(atlas.path)
            if texture_path is None:
                raise FileNotFoundError(f"icon atlas missing: {atlas.path}")
            png = (
                texture_path
                if texture_path.suffix.lower() == ".png"
                else dds_to_png(texture_path, self.m_work_dir / "atlas")
            )
            image = Image.open(png).convert("RGBA")
            self._m_decoded[file_id] = image
        return image

    def tile(self, guid: int):
        """The icon of ``guid`` as a PIL image at the atlas' size, or None."""
        ref = self.lookup(guid)
        if ref is None:
            return None
        atlas = self.m_atlases[ref.file_id]
        image = self._atlas_image(ref.file_id)
        box = tile_box(atlas, image.width, image.height, ref.index)
        return image.crop((box[0], box[1], box[0] + box[2], box[1] + box[3])) if box else None

    def export(self, guid: int, destination: Path, scale: int = 2) -> Path | None:
        """Write the icon of ``guid`` as PNG (upscaled ``scale`` times) or return None."""
        tile = self.tile(guid)
        if tile is None:
            return None
        from PIL import Image

        if scale > 1:
            tile = tile.resize((tile.width * scale, tile.height * scale), Image.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tile.save(destination)
        return destination


def export_icon(ctx, guid: int, what: str = "icon", name: int | None = None) -> str | None:
    """Cut icon ``guid`` into the mod as ``ui/icons/<name or guid>.png`` and return its mod path, or None when
    there is no icon table or no such icon. A failure is a warning naming ``what``, never an exception."""
    if ctx.m_icons is None:
        return None
    rel = f"data/{ctx.m_asset_folder}/ui/icons/{name or guid}.png"
    try:
        return rel if ctx.m_icons.export(guid, ctx.out_path(rel)) is not None else None
    except (OSError, ValueError) as error:
        ctx.warn(f"{what} export failed for {guid}: {error}")
        return None


CRLF = bytes((13, 10))  # the shipped files' line ending


class CIconsError(ValueError):
    """An edit of ``icons.xml`` that cannot be made."""


def _entry(data: bytes, guid: int) -> re.Match[bytes] | None:
    """The ``<Icons>`` block that belongs to ``<GUID>guid</GUID>`` (empty or not)."""
    pattern = rb"<GUID>\s*%d\s*</GUID>\s*<Icons\s*(?:/>|>.*?</Icons>)" % guid
    return re.compile(pattern, re.S).search(data)


def has_entry(data: bytes, guid: int) -> bool:
    """Whether ``icons.xml`` has an entry for ``guid``."""
    return _entry(data, guid) is not None


_TILE = re.compile(rb"<IconFileID>[^<]*</IconFileID>(?:(\s*)<IconIndex>[^<]*</IconIndex>)?")


def set_icon(data: bytes, guid: int, file_id: int, index: int, position: int = 0) -> bytes:
    """``icons.xml`` with icon number ``position`` of ``guid`` (0: the first) at tile ``index`` of atlas ``file_id``;
    the rest as it was. An entry without that icon gets it appended (``position`` at most the icon count), a GUID
    the file lacks a new entry at the end (in the file's style)."""
    eol = CRLF if CRLF in data[:400] else bytes((10,))
    tile = b"<IconFileID>%d</IconFileID>%s<IconIndex>%d</IconIndex>" % (file_id, eol, index)
    found = _entry(data, guid)
    if found is None:
        if position:
            raise CIconsError(f"no entry {guid} to put icon {position} into")
        end = data.rfind(b"</Icons>")
        if end < 0:
            raise CIconsError("not an icons.xml: no closing </Icons>")
        entry = b"<i><GUID>%d</GUID>%s<Icons><i>%s%s</i></Icons>%s</i>%s" % (guid, eol, tile, eol, eol, eol)
        return data[:end] + entry + data[end:]
    block = found.group(0)
    tiles = list(_TILE.finditer(block))
    if position < len(tiles):  # a tile 0 entry has no index: it gets one, after the line the file's own style gives
        tile_match = tiles[position]
        gap = tile_match.group(1) if tile_match.group(1) is not None else eol
        block = (
            block[: tile_match.start()]
            + b"<IconFileID>%d</IconFileID>%s<IconIndex>%d</IconIndex>" % (file_id, gap, index)
            + block[tile_match.end() :]
        )
    elif position == len(tiles) and tiles:
        close = block.rfind(b"</Icons>")
        block = block[:close] + b"<i>" + tile + b"</i>" + block[close:]
    elif position == 0:
        block = re.sub(
            rb"<Icons\s*(?:/>|>\s*</Icons>)", lambda _m: b"<Icons><i>" + tile + b"</i></Icons>", block, count=1
        )
    else:
        raise CIconsError(f"entry {guid} has {len(tiles)} icons: no icon {position} to set")
    return data[: found.start()] + block + data[found.end() :]


def set_icons(data: bytes, edits: dict[int, list]) -> bytes:
    """``icons.xml`` with each GUID's icons set: per GUID the list of (atlas file id, tile index) by position, an
    empty place (``()``) leaves that icon as it is."""
    for guid, tiles in edits.items():
        for position, tile in enumerate(tiles):
            if tile:
                data = set_icon(data, guid, *tile, position=position)
    return data
