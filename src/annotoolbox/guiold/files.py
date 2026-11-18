"""The old interface of Anno 2070, 1404 and 1701 as files: dialogs (``.dlg``), templates (``.tpl``), backgrounds
(``.bkg``, ``.bg2``, ``.bg3``), sequences, texts and the icon atlases, found in a game tree and parsed on demand.

``OldGui(tree)`` is the entry: ``dialogs()`` lists the windows, ``dialog(path)`` parses one, ``templates()`` resolves the
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
from .gui2070 import (
    Background,
    Templates,
    Widget,
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
LANGUAGES = ("eng", "english", "ger", "german")  # folders of ``data/loca``, the first one the game has is read


@dataclass(frozen=True)
class Tile:
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
class Look:
    """One state of a background: its grid of tiles and the client area offsets."""

    name: str
    columns: int
    rows: int
    tiles: tuple[Tile, ...]
    offsets: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    tint: tuple[float, ...] | None = None  # m_Color (HSVA, 0..1), None: unchanged
    icon_tint: tuple[float, ...] | None = None  # m_IconColor, the widget's icon


@dataclass(frozen=True)
class Font:
    """A font of the game's font table: the face, its pixel size at 1024 high, its weight, the file it ships in."""

    name: str
    family: str
    size: float
    weight: int = 400
    file: str = ""


@dataclass
class Entry:
    path: str
    kind: str  # dialog, template, background


@dataclass
class OldGui:
    """The old interface files of a game tree (``sources.gametree.GameTree``)."""

    tree: object
    entries: dict[str, Entry] = field(default_factory=dict)
    _parsed: dict[str, object] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)  # the page reads files on more than one thread

    def __post_init__(self) -> None:
        for path in self.tree.paths:
            if path.startswith(ROOTS):
                kind = KINDS.get(path.rsplit(".", 1)[-1]) if "." in path.rsplit("/", 1)[-1] else None
                if kind:
                    self.entries[path] = Entry(path, kind)

    def dialogs(self) -> list[str]:
        """The windows by path, ``__custom_windows`` (the library of components) not among them."""
        return sorted(p for p, e in self.entries.items() if e.kind == "dialog" and "__custom" not in p)

    def read(self, path: str) -> bytes:
        return self.tree.read(path)

    def dialog(self, path: str) -> Widget:
        """A dialog parsed (kept)."""
        if path not in self._parsed:
            self._parsed[path] = parse_widget_file(self.read(path))
        return self._parsed[path]  # type: ignore[return-value]

    @cached_property
    def _template_paths(self) -> list[str]:
        return sorted(p for p, e in self.entries.items() if e.kind == "template")

    def templates(self) -> Templates:
        """Every template by its PATHID (the add-on's over the game's), for ``Templates.effective``; read once, also
        when two dialogs are opened at once."""
        with self._lock:
            if "templates" not in self._parsed:
                found = []
                for path in self._template_paths:
                    try:
                        found.append(parse_widget_file(self.read(path)))
                    except ET.ParseError:
                        continue
                self._parsed["templates"] = Templates(found)
        return self._parsed["templates"]  # type: ignore[return-value]

    def components(self) -> dict[int, Widget]:
        """The components an ``INSTANCE`` names by ``STRUCTTEMPLATEREF``: the ``CUSTOM`` widgets of
        ``__custom_windows.dlg`` (2070; the other games ship none) by ``UNIQUEID``."""
        if "components" not in self._parsed:
            found: dict[int, Widget] = {}
            for path in sorted(p for p, e in self.entries.items() if e.kind == "dialog" and "__custom" in p):
                try:
                    found.update(struct_templates(self.dialog(path)))
                except ET.ParseError:
                    continue
            self._parsed["components"] = found
        return self._parsed["components"]  # type: ignore[return-value]

    @cached_property
    def sheet(self) -> richtext.StyleSheet:
        """The rich text's style sheet (2070's ``textstyles.css``; empty for a game without one)."""
        try:
            return richtext.StyleSheet.read(self.tree)
        except ValueError:
            return richtext.StyleSheet()

    # backgrounds

    def background(self, path: str) -> dict[str, Look]:
        """The states of a background file by name, empty for a file the game does not have or cannot read."""
        key = path.replace("\\", "/").lower()
        if key not in self._parsed:
            self._parsed[key] = self._read_background(key)
        return self._parsed[key]  # type: ignore[return-value]

    def _read_background(self, path: str) -> dict[str, Look]:
        if not self.tree.exists(path):
            return {}
        try:
            data = self.read(path)
            if path.endswith(".bkg"):
                return _looks_of_bkg(parse_background(data))
            return _looks_of_bg2(decode_text(data), self.atlases())
        except (ET.ParseError, ValueError):
            return {}

    @cached_property
    def _atlas_table(self) -> dict[int, icons2070.IconAtlas]:
        if not self.tree.exists(ICON_MAP):
            return {}
        try:
            return icons2070.parse_atlases(ET.fromstring(decode_text(self.read(ICON_MAP))))
        except ET.ParseError:
            return {}

    def atlases(self) -> dict[int, icons2070.IconAtlas]:
        """The icon atlases by file id (``iconfilemap.xml``): the picture and the size of one icon."""
        return self._atlas_table

    # colours, icons and fonts

    @cached_property
    def _properties(self):
        return ET.fromstring(decode_text(self.read(PROPERTIES))) if self.tree.exists(PROPERTIES) else None

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
            h, s, v = (list(parsed[1]) + [100.0, 100.0])[:3]
            return tuple(min(255, round(c * 255)) for c in hsv_rgb(h, s / 100, v / 100))  # type: ignore[return-value]
        return self.known_colours.get(parsed) if isinstance(parsed, str) else None

    @cached_property
    def fonts(self) -> dict[str, Font]:
        """The font table by name (2070's ``DefaultValues/Fonts/LocaFont``, its first language), empty without it."""
        table = self._properties.find("DefaultValues/Fonts/LocaFont") if self._properties is not None else None
        out = {}
        for item in table.iter("Item") if table is not None else []:
            name = item.findtext("FontName")
            if name:
                out[name] = Font(name, item.findtext("FontFamilyName") or "", number(item.findtext("FontSize"), 12.0),
                                 int(number(item.findtext("Weight"), 400)), item.findtext("FontFileName") or "")  # fmt: skip
        if not out:  # 1404 and 1701 keep the table in their localisation files
            out = {
                n: Font(n, family, size, weight)
                for n, family, size, weight in fontsloca.read(self.tree.paths, self.read)
            }
        return out

    def font_file(self, font: Font) -> bytes | None:
        """The font file of the game's first language that has it, None where none ships."""
        if not font.file:
            return None
        for root in TEXT_ROOTS:
            for lang in LANGUAGES:
                path = f"{root}{lang}/{font.file}".lower()
                if self.tree.exists(path):
                    return self.read(path)
        return None

    @cached_property
    def _icons(self) -> dict[int, list[icons2070.IconRef]]:
        return icons2070.load_icon_data(self.read, self.tree.exists)[1]

    def icon(self, guid) -> Tile | None:
        """An ``ICONGUID``'s icon as a tile of its atlas (the cell number in ``x``, as a ``.bg2`` tile)."""
        try:
            refs = self._icons.get(int(guid))
        except (TypeError, ValueError):
            return None
        atlas = self.atlases().get(refs[0].file_id) if refs else None
        return Tile(atlas.path, refs[0].index, -1, atlas.tile_width, atlas.tile_height) if atlas else None

    # texts

    @cached_property
    def _texts(self) -> dict[int, str]:
        """Every text of the game's first language by id (``data/loca/<language>/txt``)."""
        out: dict[int, str] = {}
        folders = [f"{root}{lang}/txt/" for lang in LANGUAGES for root in TEXT_ROOTS]
        folder = next((f for f in folders if any(p.startswith(f) for p in self.tree.paths)), None)
        for path in sorted(p for p in self.tree.paths if folder and p.startswith(folder) and p.endswith(".txt")):
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


def _looks_of_bkg(bg: Background) -> dict[str, Look]:
    out = {}
    for name, state in bg.states.items():
        tiles = tuple(
            Tile(
                bg.files[t.file_id] if 0 <= t.file_id < len(bg.files) else "",
                t.x, t.y, t.w, t.h, t.flags,
                bool(t.flags & 5), bool(t.flags & 66),
            )
            for t in state.tiles
        )  # fmt: skip
        tint = state.color_hsva if tuple(state.color_hsva) != UNCHANGED else None
        out[name] = Look(name, state.grid[0], state.grid[1], tiles, state.client_offsets, tint, state.icon_color_hsva)
    return out


def _looks_of_bg2(text: str, atlases: dict[int, icons2070.IconAtlas]) -> dict[str, Look]:
    """A ``.bg2``: the icons are cells of an atlas (``IconIndex`` row-major, the cell size from ``iconfilemap.xml``).
    The atlas's width is not in the file, so a tile carries the cell number in ``x`` and 0 in ``y`` for the loader to
    cut once the picture is known (``view.cut``)."""
    root = ET.fromstring(text)
    out = {}
    for el in _items(root):
        tiles = []
        icons = el.find("IconList")
        for icon in _items(icons) if icons is not None else []:
            atlas = atlases.get(int(icon.findtext("FileID") or -1))
            if atlas is None:
                tiles.append(Tile("", 0, 0, 0, 0))
                continue
            tiles.append(
                Tile(atlas.path, int(icon.findtext("IconIndex") or 0), -1, atlas.tile_width, atlas.tile_height)
            )
        columns, rows = int(el.findtext("m_XSize") or 1), int(el.findtext("m_YSize") or 1)
        offsets = tuple(int(el.findtext(f"ClientAreaOffsets/{k}") or 0) for k in ("left", "top", "right", "bottom"))
        name = (el.findtext("LayoutName") or "").strip()
        tint = read_hsva(el, "m_Color")
        tint = tint if tint is not None and tint != UNCHANGED else None
        out[name] = Look(name, columns, rows, tuple(_stretch_by_grid(tiles, columns, rows)), offsets, tint,  # type: ignore[arg-type]
                         read_hsva(el, "m_IconColor"))  # fmt: skip
    return out


def _items(parent: ET.Element) -> list[ET.Element]:
    """The elements of an array: ``<i>`` in 1404's files, ``<i0000>``, ``<i0001>`` ... in 1701's."""
    return [c for c in parent if c.tag == "i" or re.fullmatch(r"i\d+", c.tag)]


def _stretch_by_grid(tiles: list[Tile], columns: int, rows: int) -> list[Tile]:
    """Which tiles of a ``.bg2`` grid stretch: not stored in the file, so the middle of three (and a lone one) does."""
    out = []
    for i, t in enumerate(tiles):
        col, row = i % columns, i // columns
        sx = columns == 1 or (columns >= 3 and 0 < col < columns - 1)
        sy = rows == 1 or (rows >= 3 and 0 < row < rows - 1)
        out.append(Tile(t.atlas, t.x, t.y, t.w, t.h, t.flags, sx, sy))
    return out


def stem(path: str) -> str:
    """A picture's path without its extension and with forward slashes."""
    return re.sub(r"\.(png|tga|dds|jpg)$", "", path.replace("\\", "/").lower())


def hsv_rgb(h: float, s: float, v: float) -> tuple[float, float, float]:
    """Hue in degrees, saturation and value as fractions (value may pass 1: brighter) to RGB fractions."""
    r, g, b = colorsys.hsv_to_rgb((h % 360.0) / 360.0, max(0.0, min(1.0, s)), 1.0)
    return r * v, g * v, b * v


def number(text, default: float) -> float:
    """``text`` as a number, ``default`` where it is none."""
    try:
        return float(text) if text not in (None, "") else default
    except (TypeError, ValueError):
        return default
