"""The old interface of Anno 2070, 1404 and 1701 as files: dialogs (``.dlg``), templates (``.tpl``), backgrounds
(``.bkg``, ``.bg2``, ``.bg3``), sequences, texts and the icon atlases, found in a game tree and parsed on demand.

``COldGui(tree)`` is the entry: ``dialogs()`` lists the windows, ``dialog(path)`` parses one, ``templates()`` resolves the
``TEMPLATE_REF_ID`` chains, ``background(path)`` a background's states, ``text(id)`` a text by its id. The formats are
docs/ui/2070/files.md and backgrounds.md; 1404 and 1701 keep the same scheme (docs/ui/old-gui.md).
"""

from __future__ import annotations

import colorsys
import re
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import cached_property

from ..sources import icons2070, loca2070
from . import fontsloca, richtext
from .fontsloca import LANGUAGES, number  # LANGUAGES: the folders of ``data/loca``, the first one the game has is read
from .gui2070 import (
    CTemplates,
    SBackground,
    SWidget,
    argb_rgb,
    decode_text,
    parse_background,
    parse_color,
    parse_text_colors,
    parse_widget_file,
    read_hsva,
    struct_templates,
)

ROOTS = ("data/config/gui/", "addondata/config/gui/")  # a game's own files, then its add-on's (which win)
GAMES = ("anno2070", "anno1404", "anno1701")
KINDS = {"dlg": "dialog", "tpl": "template", "bkg": "background", "bg2": "background", "bg3": "background"}
TEXT_ROOTS = ("data/loca/", "addondata/loca/")
ICON_MAP = "data/config/gui/iconfilemap.xml"
PROPERTIES = "data/config/texteditor/properties.xml"  # 2070: the known colours and the font table
UNCHANGED = (0.0, 1.0, 1.0, 1.0)  # a state colour that leaves the pictures as they are (docs/ui/2070/backgrounds.md)


@dataclass(frozen=True)
class STile:
    """One cell of a background's grid: a rectangle of an atlas picture, and how it is stretched."""

    atlas: str  # game path of the picture as the file names it
    x: int
    y: int
    w: int
    h: int
    flags: int = 0
    stretch_x: bool = False
    stretch_y: bool = False


@dataclass(frozen=True)
class SLook:
    """One state of a background: its grid of tiles and the client area offsets."""

    name: str
    columns: int
    rows: int
    tiles: tuple[STile, ...]
    offsets: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    tint: tuple[float, ...] | None = None  # m_Color (HSVA, 0..1), None: unchanged
    icon_tint: tuple[float, ...] | None = None  # m_IconColor, the widget's icon


@dataclass(frozen=True)
class SFont:
    """A font of the game's font table: the face, its pixel size at 1024 high, its weight, the file it ships in."""

    name: str
    family: str
    size: float
    weight: int = 400
    file: str = ""


@dataclass
class SEntry:
    path: str
    kind: str  # dialog, template, background


@dataclass
class COldGui:
    """The old interface files of a game tree (``sources.gametree.CGameTree``)."""

    m_tree: object
    m_entries: dict[str, SEntry] = field(default_factory=dict)
    _m_parsed: dict[str, object] = field(default_factory=dict)
    _m_lock: threading.Lock = field(default_factory=threading.Lock)  # the page reads files on more than one thread

    def __post_init__(self) -> None:
        for path in self.m_tree.m_paths:
            if path.startswith(ROOTS):
                kind = KINDS.get(path.rsplit(".", 1)[-1]) if "." in path.rsplit("/", 1)[-1] else None
                if kind:
                    self.m_entries[path] = SEntry(path, kind)

    def dialogs(self) -> list[str]:
        """The windows by path, ``__custom_windows`` (the library of components) not among them."""
        return sorted(
            path for path, entry in self.m_entries.items() if entry.kind == "dialog" and "__custom" not in path
        )

    def read(self, path: str) -> bytes:
        return self.m_tree.read(path)

    def dialog(self, path: str) -> SWidget:
        """A dialog parsed (kept)."""
        if path not in self._m_parsed:
            self._m_parsed[path] = parse_widget_file(self.read(path))
        return self._m_parsed[path]  # type: ignore[return-value]

    @cached_property
    def _template_paths(self) -> list[str]:
        return sorted(path for path, entry in self.m_entries.items() if entry.kind == "template")

    def templates(self) -> CTemplates:
        """Every template by its PATHID (the add-on's over the game's), for ``CTemplates.effective``; read once, also
        when two dialogs are opened at once."""
        with self._m_lock:
            if "templates" not in self._m_parsed:
                found = []
                for path in self._template_paths:
                    try:
                        found.append(parse_widget_file(self.read(path)))
                    except ET.ParseError:
                        continue
                self._m_parsed["templates"] = CTemplates(found)
        return self._m_parsed["templates"]  # type: ignore[return-value]

    def components(self) -> dict[int, SWidget]:
        """The components an ``INSTANCE`` names by ``STRUCTTEMPLATEREF``: the ``CUSTOM`` widgets of
        ``__custom_windows.dlg`` (2070; the other games ship none) by ``UNIQUEID``."""
        if "components" not in self._m_parsed:
            found: dict[int, SWidget] = {}
            for path in sorted(
                entry_path
                for entry_path, entry in self.m_entries.items()
                if entry.kind == "dialog" and "__custom" in entry_path
            ):
                try:
                    found.update(struct_templates(self.dialog(path)))
                except ET.ParseError:
                    continue
            self._m_parsed["components"] = found
        return self._m_parsed["components"]  # type: ignore[return-value]

    @cached_property
    def sheet(self) -> richtext.CStyleSheet:
        """The rich text's style sheet (2070's ``textstyles.css``; empty for a game without one)."""
        try:
            return richtext.CStyleSheet.read(self.m_tree)
        except ValueError:
            return richtext.CStyleSheet()

    # backgrounds

    def background(self, path: str) -> dict[str, SLook]:
        """The states of a background file by name, empty for a file the game does not have or cannot read."""
        key = path.replace("\\", "/").lower()
        if key not in self._m_parsed:
            self._m_parsed[key] = self._read_background(key)
        return self._m_parsed[key]  # type: ignore[return-value]

    def _read_background(self, path: str) -> dict[str, SLook]:
        if not self.m_tree.exists(path):
            return {}
        try:
            data = self.read(path)
            if path.endswith(".bkg"):
                return _looks_of_bkg(parse_background(data))
            return _looks_of_bg2(decode_text(data), self.atlases())
        except (ET.ParseError, ValueError):
            return {}

    @cached_property
    def _atlas_table(self) -> dict[int, icons2070.SIconAtlas]:
        if not self.m_tree.exists(ICON_MAP):
            return {}
        try:
            return icons2070.parse_atlases(ET.fromstring(decode_text(self.read(ICON_MAP))))
        except ET.ParseError:
            return {}

    def atlases(self) -> dict[int, icons2070.SIconAtlas]:
        """The icon atlases by file id (``iconfilemap.xml``): the picture and the size of one icon."""
        return self._atlas_table

    # colours, icons and fonts

    @cached_property
    def _properties(self):
        return ET.fromstring(decode_text(self.read(PROPERTIES))) if self.m_tree.exists(PROPERTIES) else None

    @cached_property
    def known_colours(self) -> dict[str, tuple[int, int, int]]:
        """The named colours (2070's ``KnownColors``), empty for a game without the file."""
        return parse_text_colors(self.read(PROPERTIES)) if self._properties is not None else {}

    def colour(self, value) -> tuple[int, int, int] | None:
        """A colour as the files write it: a known colour's name, ``0xRRGGBB`` or ARGB, ``HSV;h;s;v``. None for
        none, 0 or a name the game does not know (the font's colour)."""
        parsed = parse_color(str(value).strip()) if value not in (None, "") else None
        if isinstance(parsed, int):
            return argb_rgb(parsed) if parsed else None
        if isinstance(parsed, tuple):
            hue, saturation, brightness = (list(parsed[1]) + [100.0, 100.0])[:3]
            return tuple(min(255, round(channel * 255)) for channel in hsv_rgb(hue, saturation / 100, brightness / 100))  # type: ignore[return-value]
        return self.known_colours.get(parsed) if isinstance(parsed, str) else None

    @cached_property
    def fonts(self) -> dict[str, SFont]:
        """The font table by name (2070's ``DefaultValues/Fonts/LocaFont``, its first language), empty without it."""
        table = self._properties.find("DefaultValues/Fonts/LocaFont") if self._properties is not None else None
        out = {}
        for item in table.iter("Item") if table is not None else []:
            name = item.findtext("FontName")
            if name:
                out[name] = SFont(name, item.findtext("FontFamilyName") or "", number(item.findtext("FontSize"), 12.0),
                                 int(number(item.findtext("Weight"), 400)), item.findtext("FontFileName") or "")  # fmt: skip
        if not out:  # 1404 and 1701 keep the table in their localisation files
            out = {
                font_name: SFont(font_name, family, size, weight)
                for font_name, family, size, weight in fontsloca.read(self.m_tree.m_paths, self.read)
            }
        return out

    def font_file(self, font: SFont) -> bytes | None:
        """The font file of the game's first language that has it, None where none ships."""
        if not font.file:
            return None
        for root in TEXT_ROOTS:
            for lang in LANGUAGES:
                path = f"{root}{lang}/{font.file}".lower()
                if self.m_tree.exists(path):
                    return self.read(path)
        return None

    @cached_property
    def _icons(self) -> dict[int, list[icons2070.SIconRef]]:
        return icons2070.load_icon_data(self.read, self.m_tree.exists)[1]

    def icon(self, guid) -> STile | None:
        """An ``ICONGUID``'s icon as a tile of its atlas (the cell number in ``x``, as a ``.bg2`` tile)."""
        try:
            refs = self._icons.get(int(guid))
        except (TypeError, ValueError):
            return None
        atlas = self.atlases().get(refs[0].file_id) if refs else None
        return STile(atlas.path, refs[0].index, -1, atlas.tile_width, atlas.tile_height) if atlas else None

    # texts

    @cached_property
    def _texts(self) -> dict[int, str]:
        """Every text of the game's first language by id (``data/loca/<language>/txt``)."""
        out: dict[int, str] = {}
        folders = [f"{root}{lang}/txt/" for lang in LANGUAGES for root in TEXT_ROOTS]
        folder = next(
            (
                candidate
                for candidate in folders
                if any(text_path.startswith(candidate) for text_path in self.m_tree.m_paths)
            ),
            None,
        )
        for path in sorted(
            text_path
            for text_path in self.m_tree.m_paths
            if folder and text_path.startswith(folder) and text_path.endswith(".txt")
        ):
            out.update(loca2070.parse_guid_text(loca2070.decode_text_file(self.read(path))))
        return out

    def text(self, ident: str | int | None) -> str:
        """The text with this id ('' without one); a text with no entry shows its id."""
        if ident in (None, "", "-1", -1):
            return ""
        try:
            number = int(ident)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return str(ident)
        return self._texts.get(number, f"#{number}")


def _looks_of_bkg(background: SBackground) -> dict[str, SLook]:
    out = {}
    for name, state in background.states.items():
        tiles = tuple(
            STile(
                background.files[tile.file_id] if 0 <= tile.file_id < len(background.files) else "",
                tile.x, tile.y, tile.w, tile.h, tile.flags,
                bool(tile.flags & 5), bool(tile.flags & 66),
            )
            for tile in state.tiles
        )  # fmt: skip
        tint = state.color_hsva if tuple(state.color_hsva) != UNCHANGED else None
        out[name] = SLook(name, state.grid[0], state.grid[1], tiles, state.client_offsets, tint, state.icon_color_hsva)
    return out


def _looks_of_bg2(text: str, atlases: dict[int, icons2070.SIconAtlas]) -> dict[str, SLook]:
    """A ``.bg2``: the icons are cells of an atlas (``IconIndex`` row-major, the cell size from ``iconfilemap.xml``).
    The atlas's width is not in the file, so a tile carries the cell number in ``x`` and 0 in ``y`` for the loader to
    cut once the picture is known (``view.cut``)."""
    root = ET.fromstring(text)
    out = {}
    for item in _items(root):
        tiles = []
        icons = item.find("IconList")
        for icon in _items(icons) if icons is not None else []:
            atlas = atlases.get(int(icon.findtext("FileID") or -1))
            if atlas is None:
                tiles.append(STile("", 0, 0, 0, 0))
                continue
            tiles.append(
                STile(atlas.path, int(icon.findtext("IconIndex") or 0), -1, atlas.tile_width, atlas.tile_height)
            )
        columns, rows = int(item.findtext("m_XSize") or 1), int(item.findtext("m_YSize") or 1)
        offsets = tuple(int(item.findtext(f"ClientAreaOffsets/{k}") or 0) for k in ("left", "top", "right", "bottom"))
        name = (item.findtext("LayoutName") or "").strip()
        tint = read_hsva(item, "m_Color")
        tint = tint if tint is not None and tint != UNCHANGED else None
        out[name] = SLook(name, columns, rows, tuple(_stretch_by_grid(tiles, columns, rows)), offsets, tint,  # type: ignore[arg-type]
                         read_hsva(item, "m_IconColor"))  # fmt: skip
    return out


def _items(parent: ET.Element) -> list[ET.Element]:
    """The elements of an array: ``<i>`` in 1404's files, ``<i0000>``, ``<i0001>`` ... in 1701's."""
    return [child for child in parent if child.tag == "i" or re.fullmatch(r"i\d+", child.tag)]


def _stretch_by_grid(tiles: list[STile], columns: int, rows: int) -> list[STile]:
    """Which tiles of a ``.bg2`` grid stretch: not stored in the file, so the middle of three (and a lone one) does."""
    out = []
    for i, tile in enumerate(tiles):
        col, row = i % columns, i // columns
        stretch_x = columns == 1 or (columns >= 3 and 0 < col < columns - 1)
        stretch_y = rows == 1 or (rows >= 3 and 0 < row < rows - 1)
        out.append(STile(tile.atlas, tile.x, tile.y, tile.w, tile.h, tile.flags, stretch_x, stretch_y))
    return out


def stem(path: str) -> str:
    """A picture's path without its extension and with forward slashes."""
    return re.sub(r"\.(png|tga|dds|jpg)$", "", path.replace("\\", "/").lower())


def hsv_rgb(hue: float, saturation: float, brightness: float) -> tuple[float, float, float]:
    """Hue in degrees, saturation and value as fractions (value may pass 1: brighter) to RGB fractions."""
    red, green, blue = colorsys.hsv_to_rgb((hue % 360.0) / 360.0, max(0.0, min(1.0, saturation)), 1.0)
    return red * brightness, green * brightness, blue * brightness
