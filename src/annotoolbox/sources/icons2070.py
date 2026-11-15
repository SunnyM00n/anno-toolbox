"""Build-menu icons of Anno 2070 (and the icon atlases of 1404 and 1701, which keep the same scheme).

``icons.xml`` maps an asset GUID to ``(IconFileID, IconIndex)``, ``iconfilemap.xml`` the file id to the atlas image
and its tile size; tiles run row by row. The atlas is named ``.png`` and shipped as ``<name>_0.dds`` (1404 and 1701
name a ``.tga``). ``load_icon_data(read, exists)`` reads both files of any of the games, ``IconTable`` cuts a tile for
a mod's build, ``set_icons`` changes the icons of some GUIDs in the file's own text.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from ..models.textures import dds_to_png
from .overlay import SourceTree, normalize

ICONS_FILE = "data/config/game/icons.xml"
ICON_FILE_MAP = "data/config/gui/iconfilemap.xml"
ICONS_FILES = (ICONS_FILE, "addondata/config/game/icons.xml")  # 1404's add-on adds entries; later files win


@dataclass(frozen=True)
class IconAtlas:
    file_id: int
    path: str  # game path of the atlas image as referenced (``.png``)
    tile_width: int
    tile_height: int


@dataclass(frozen=True)
class IconRef:
    file_id: int
    index: int


def parse_atlases(root: ET.Element) -> dict[int, IconAtlas]:
    """The game's icon atlases (``IsGameIcon``); the tool-only ones are left out."""
    out = {}
    for el in root.iter("IconFile"):
        try:
            fid = int(el.findtext("IconFileID", ""))
            atlas = IconAtlas(
                fid,
                normalize(el.findtext("IconFilename", "")),
                int(el.findtext("IconWidth", "")),
                int(el.findtext("IconHeight", "")),
            )
        except ValueError:
            continue
        if el.findtext("IsGameIcon", "1") == "1" and atlas.tile_width > 0 and atlas.tile_height > 0:
            out[fid] = atlas
    return out


def parse_icons(root: ET.Element) -> dict[int, list[IconRef]]:
    """GUID -> every icon of its entry, in the file's order (1404's variations come after the first); a missing
    IconIndex is tile 0 (the sawmill's entry has none). An entry without an icon is left out."""
    out = {}
    for el in root.iter("i"):
        guid_text = el.findtext("GUID")
        if guid_text is None:
            continue
        try:
            refs = [
                IconRef(int(icon.findtext("IconFileID", "")), int(icon.findtext("IconIndex", "0")))
                for icon in el.findall("Icons/i")
            ]
            guid = int(guid_text)
        except ValueError:
            continue
        if refs:
            out[guid] = refs
    return out


def load_icon_data(read, exists) -> tuple[dict[int, IconAtlas], dict[int, list[IconRef]]]:
    """The atlases and the GUID -> icons table of a game, through ``read(path)`` and ``exists(path)``; empty where
    the game has none of the files."""
    atlases = parse_atlases(ET.fromstring(read(ICON_FILE_MAP))) if exists(ICON_FILE_MAP) else {}
    icons: dict[int, list[IconRef]] = {}
    for path in ICONS_FILES:
        if exists(path):
            icons |= parse_icons(ET.fromstring(read(path)))
    return atlases, icons


def tile_box(atlas: IconAtlas, width: int, height: int, index: int) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) of tile ``index`` in an atlas image of ``width`` x ``height``, None when it lies outside."""
    per_row = max(1, width // atlas.tile_width)
    x = (index % per_row) * atlas.tile_width
    y = (index // per_row) * atlas.tile_height
    if y + atlas.tile_height > height:
        return None
    return x, y, atlas.tile_width, atlas.tile_height


class IconTable:
    """GUID -> icon lookup plus lazy atlas decoding."""

    def __init__(self, tree: SourceTree, work_dir: Path) -> None:
        self.tree = tree
        self.work_dir = work_dir
        self._decoded: dict[int, object] = {}  # file id -> PIL image
        self.atlases, self.icons = load_icon_data(tree.read_bytes, tree.exists)

    def lookup(self, guid: int) -> IconRef | None:
        refs = self.icons.get(guid)
        if not refs or refs[0].file_id not in self.atlases:
            return None
        return refs[0]

    def _atlas_image(self, file_id: int):
        img = self._decoded.get(file_id)
        if img is None:
            from PIL import Image  # lazy: Pillow is only needed to cut the icons out

            atlas = self.atlases[file_id]
            src = self.tree.resolve_texture(atlas.path)
            if src is None:
                raise FileNotFoundError(f"icon atlas missing: {atlas.path}")
            png = src if src.suffix.lower() == ".png" else dds_to_png(src, self.work_dir / "atlas")
            img = Image.open(png).convert("RGBA")
            self._decoded[file_id] = img
        return img

    def tile(self, guid: int):
        """The icon of ``guid`` as a PIL image at the atlas' size, or None."""
        ref = self.lookup(guid)
        if ref is None:
            return None
        atlas = self.atlases[ref.file_id]
        img = self._atlas_image(ref.file_id)
        box = tile_box(atlas, img.width, img.height, ref.index)
        return img.crop((box[0], box[1], box[0] + box[2], box[1] + box[3])) if box else None

    def export(self, guid: int, dst: Path, scale: int = 2) -> Path | None:
        """Write the icon of ``guid`` as PNG (upscaled ``scale`` times) or return None."""
        tile = self.tile(guid)
        if tile is None:
            return None
        from PIL import Image

        if scale > 1:
            tile = tile.resize((tile.width * scale, tile.height * scale), Image.LANCZOS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tile.save(dst)
        return dst


def export_icon(ctx, guid: int, what: str = "icon", name: int | None = None) -> str | None:
    """Cut icon ``guid`` into the mod as ``ui/icons/<name or guid>.png`` and return its mod path, or None when
    there is no icon table or no such icon. A failure is a warning naming ``what``, never an exception."""
    if ctx.icons is None:
        return None
    rel = f"data/{ctx.asset_folder}/ui/icons/{name or guid}.png"
    try:
        return rel if ctx.icons.export(guid, ctx.out_path(rel)) is not None else None
    except (OSError, ValueError) as exc:
        ctx.warn(f"{what} export failed for {guid}: {exc}")
        return None


CRLF = bytes((13, 10))  # the shipped files' line ending


class IconsError(ValueError):
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
            raise IconsError(f"no entry {guid} to put icon {position} into")
        end = data.rfind(b"</Icons>")
        if end < 0:
            raise IconsError("not an icons.xml: no closing </Icons>")
        entry = b"<i><GUID>%d</GUID>%s<Icons><i>%s%s</i></Icons>%s</i>%s" % (guid, eol, tile, eol, eol, eol)
        return data[:end] + entry + data[end:]
    block = found.group(0)
    tiles = list(_TILE.finditer(block))
    if position < len(tiles):  # a tile 0 entry has no index: it gets one, after the line the file's own style gives
        at = tiles[position]
        gap = at.group(1) if at.group(1) is not None else eol
        block = (
            block[: at.start()]
            + b"<IconFileID>%d</IconFileID>%s<IconIndex>%d</IconIndex>" % (file_id, gap, index)
            + block[at.end() :]
        )
    elif position == len(tiles) and tiles:
        close = block.rfind(b"</Icons>")
        block = block[:close] + b"<i>" + tile + b"</i>" + block[close:]
    elif position == 0:
        block = re.sub(
            rb"<Icons\s*(?:/>|>\s*</Icons>)", lambda _m: b"<Icons><i>" + tile + b"</i></Icons>", block, count=1
        )
    else:
        raise IconsError(f"entry {guid} has {len(tiles)} icons: no icon {position} to set")
    return data[: found.start()] + block + data[found.end() :]


def set_icons(data: bytes, edits: dict[int, list]) -> bytes:
    """``icons.xml`` with each GUID's icons set: per GUID the list of (atlas file id, tile index) by position, an
    empty place (``()``) leaves that icon as it is."""
    for guid, tiles in edits.items():
        for position, tile in enumerate(tiles):
            if tile:
                data = set_icon(data, guid, *tile, position=position)
    return data
