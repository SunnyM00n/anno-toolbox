"""Read Anno 2070's GUI definitions under ``data/config/gui``: dialogs, templates, backgrounds and
the support files (formats: ``docs/ui/2070/files.md``).

Everything is text XML except ``.cgt`` (GOLD parser tables), ``path.map`` and ``thumbs.db``. The game
itself loads compiled pools (``guidata64/<Pool>.cdf`` + ``.cdf.lua``) built from these sources; the
sources are what a port reads.
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# dlg element -> the GUI editor's class (properties.xml)
WIDGET_CLASSES = {
    "FRAME": "CGUIWndFrame",
    "TEMPLATE": None,
    "PANEL": "CGUIWndPanel",
    "LABEL": "CGUIWndLabel",
    "BUTTON": "CGUIWndButton",
    "INSTANCE": "CGUIWndInstance",
    "BOXLAYOUT": "CGUIWndBoxLayout",
    "CARDLAYOUT": "CGUIWndCardLayout",
    "TILELAYOUT": "CGUIWndTileLayout",
    "DRAG": "CGUIWndDrag",
    "DROPZONE": "CGUIWndDropZone",
    "CUSTOM": "CGUIWndCustom",
    "VIDEOPANEL": "CGUIWndVideoPanel",
    "PROGRESSBAR": "CGUIWndProgressBar",
    "CIRCULARLYPROGRESSBAR": "CGUIWndCircularlyProgressBar",
    "EFFECTPANEL": "CGUIWndEffectPanel",
    "PICTUREBOX": "CGUIWndPictureBox",
    "COMBOBOX": "CGUIWndComboBox",
    "OBJECT": "CGUIWndObject",
    "TEXTFIELD": "CGUIWndTextField",
    "SCROLLBOX": "CGUIWndScrollBox",
    "SLIDER": "CGUIWndSlider",
    "WEBVIEW": "CGUIWndWebView",
    "ITEMBROWSER": "CGUIWndItemBrowser",
}
LAYOUT_H = {0: "None", 1: "Fill", 2: "Left", 3: "Center", 4: "Right"}
LAYOUT_V = {0: "None", 1: "Fill", 2: "Top", 3: "Center", 4: "Bottom"}
TILE_STRETCH_X, TILE_STRETCH_Y = 4 | 1, 64 | 2  # tile flags: stretched (4, 64) or repeated (1, 2), drawn stretched
REFERENCE_SIZE = (1280, 1024)  # the space EXTENT is given in
TOOLTIP_TEXT_OFFSET = {"TOOLTIP": 10_000_000, "TOOLTIP_DISABLED": 20_000_000, "TOOLTIP_PRESSED": 30_000_000}


def decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _xml(data: bytes | str) -> ET.Element:
    return ET.fromstring(decode_text(data) if isinstance(data, bytes) else data)


def _text(e: ET.Element | None) -> str:
    return (e.text or "").strip() if e is not None else ""


@dataclass
class Event:
    type: str
    slot: str
    script: str | None  # Lua; None: a handler of the dialog's own code


@dataclass
class Model:
    """Lua component model of a CUSTOM widget: attributes (type, getter, setter), actions, events."""

    name: str
    uid: int | None
    attributes: dict[str, tuple[int, str, str]] = field(default_factory=dict)
    actions: dict[str, str] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    constructor: str = ""


@dataclass
class Widget:
    kind: str  # element name: FRAME, PANEL, ... or TEMPLATE
    path_id: str
    uid: int | None
    templates: list[str]  # TEMPLATE_REF_ID (a few nodes carry two)
    props: dict[str, str | dict] = field(default_factory=dict)
    children: list[Widget] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    model: Model | None = None

    @property
    def cls(self) -> str | None:
        return WIDGET_CLASSES.get(self.kind)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def _prop(e: ET.Element) -> str | dict:
    return _text(e) if len(e) == 0 else {c.tag: _prop(c) for c in e}


def _model(e: ET.Element) -> Model:
    m = Model(_text(e.find("NAME")), int(_text(e.find("UNIQUEID")) or 0) or None)
    for c in e:
        if c.tag == "ATTRIBUTE":
            m.attributes[_text(c.find("NAME"))] = (
                int(_text(c.find("TYPE")) or 0),
                _text(c.find("GETTER")),
                _text(c.find("SETTER")),
            )
        elif c.tag == "ACTION":
            m.actions[_text(c.find("NAME"))] = _text(c.find("SCRIPT"))
        elif c.tag == "EVENT":
            m.events.append(_text(c.find("NAME")))
        elif c.tag == "CONSTRUCTOR":
            m.constructor = _text(c)
    return m


def _widget(e: ET.Element) -> Widget:
    uid = _text(e.find("UNIQUEID"))
    w = Widget(e.tag, _text(e.find("PATHID")), int(uid) if uid else None, [])
    for c in e:
        if c.find("PATHID") is not None:
            w.children.append(_widget(c))
        elif c.tag == "TEMPLATE_REF_ID":
            if _text(c):
                w.templates.append(_text(c))
        elif c.tag == "EVENTLIST":
            for ev in c.findall("EVENT"):
                sc = ev.find("SCRIPT")
                w.events.append(
                    Event(_text(ev.find("TYPE")), _text(ev.find("SLOT")), None if sc is None else sc.text or "")
                )
        elif c.tag == "MODEL":
            w.model = _model(c)
        elif c.tag not in ("PATHID", "UNIQUEID"):
            w.props[c.tag] = _prop(c)
    return w


def parse_widget_file(data: bytes | str) -> Widget:
    """A ``.dlg`` (root FRAME) or ``.tpl`` (root TEMPLATE, a property bag without children)."""
    return _widget(_xml(data))


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class Templates:
    """``TEMPLATE_REF_ID`` resolution: a template is found by its PATHID (= file name) and inherits its own."""

    def __init__(self, templates: list[Widget]) -> None:
        self.by_name = {t.path_id: t for t in templates}

    def chain(self, names: list[str]) -> list[Widget]:
        out, todo, seen = [], list(names), set()
        while todo:
            n = todo.pop(0)
            if n in seen or n not in self.by_name:
                continue
            seen.add(n)
            t = self.by_name[n]
            out.append(t)
            todo.extend(t.templates)
        return out

    def missing(self, names: list[str]) -> list[str]:
        return [n for n in names if n not in self.by_name]

    def effective(self, w: Widget) -> dict:
        """Own properties over the template chain (nearest wins; EXTENT, TEXTSETTING ... merge per field)."""
        props: dict = {}
        for t in reversed(self.chain(w.templates)):
            props = _merge(props, t.props)
        return _merge(props, w.props)


def struct_templates(custom_windows: Widget) -> dict[int, Widget]:
    """``INSTANCE``/``STRUCTTEMPLATEREF`` targets: the CUSTOM components of ``__custom_windows.dlg`` by UNIQUEID."""
    return {c.uid: c for c in custom_windows.children if c.kind == "CUSTOM" and c.uid is not None}


def extent(props: dict) -> tuple[int, int, int, int]:
    """(left, top, width, height) in the 1280x1024 reference space, relative to the parent's client area."""
    ex = props.get("EXTENT") or {}
    ex = ex if isinstance(ex, dict) else {}
    return tuple(int(ex.get(k) or 0) for k in ("LEFT", "TOP", "WIDTH", "HEIGHT"))  # type: ignore[return-value]


def tooltip_text_id(prop: str, guid: int) -> int:
    """Key of a tooltip in loca ``tt_normal`` / ``tt_disabled`` / ``tt_pressed``: state digit + 7-digit GUID."""
    return TOOLTIP_TEXT_OFFSET[prop] + guid


def parse_color(v: str) -> tuple[str, tuple[float, ...]] | str | int | None:
    """``HSV;h;s;v`` (degrees, percent), a 0xRRGGBB int, or a KnownColors name (texteditor/properties.xml)."""
    if not v:
        return None
    if v.startswith("HSV;"):
        return ("HSV", tuple(float(x) for x in v[4:].split(";")))
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def argb_rgb(value: int) -> tuple[int, int, int]:
    """An ARGB (or 0xRRGGBB) integer as RGB."""
    v = value & 0xFFFFFFFF
    return (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF


def parse_text_colors(data: bytes | str) -> dict[str, tuple[int, int, int]]:
    """The named text colours of ``texteditor/properties.xml`` (``KnownColors``: ARGB integers) as RGB."""
    out: dict[str, tuple[int, int, int]] = {}
    for item in _xml(data).iter("Item"):
        name, value = item.findtext("ColorName"), item.findtext("Color")
        if name and value and re.fullmatch(r"-?\d+", value.strip()):
            out[name] = argb_rgb(int(value))
    return out


@dataclass
class Tile:
    file_id: int
    x: int
    y: int
    w: int
    h: int
    flags: int  # TILE_STRETCH_X, TILE_STRETCH_Y
    width: int
    height: int


@dataclass
class BackgroundState:
    name: str  # Normal, Normal+MouseOver, Checked+Disabled, PB_Bar, Thumb_Normal ... (layoutnames.txt)
    grid: tuple[int, int]
    color_hsva: tuple[float, ...]
    icon_color_hsva: tuple[float, ...] | None
    client_offsets: tuple[int, int, int, int]
    stencil_icon: int
    flags: int
    tiles: list[Tile]


@dataclass
class Background:
    files: list[str]  # LocalFileID -> texture
    states: dict[str, BackgroundState]


def read_hsva(e: ET.Element, prefix: str) -> tuple[float, ...] | None:
    """``<prefix>_H`` ... ``_A`` of an element (a background state's ``m_Color``), None without them."""
    vals = [e.findtext(f"{prefix}_{c}") for c in "HSVA"]
    return None if None in vals else tuple(float(v) for v in vals)  # type: ignore[arg-type]


def parse_background(data: bytes | str) -> Background:
    """A ``.bkg``: per state a grid of atlas tiles drawn as a nine-slice (``XSize`` x ``YSize``, row-major)."""
    r = _xml(data)
    files = [_text(i) for i in r.findall("m_localIconFileMap/i")]
    states = {}
    for b in r.findall("BackgroundInfo/i"):
        co = b.find("ClientAreaOffsets")
        tiles = [
            Tile(
                *(
                    int(i.findtext(k) or 0)
                    for k in ("LocalFileID", "tileX", "tileY", "tileWidth", "tileHeight", "flags", "Width", "Height")
                )
            )
            for i in b.findall("IconList/i")
        ]
        name = _text(b.find("LayoutName"))
        states[name] = BackgroundState(
            name,
            (int(b.findtext("m_XSize") or 0), int(b.findtext("m_YSize") or 0)),
            read_hsva(b, "m_Color") or (0.0, 1.0, 1.0, 1.0),
            read_hsva(b, "m_IconColor"),
            tuple(int(co.findtext(k) or 0) for k in ("left", "top", "right", "bottom"))  # type: ignore[arg-type]
            if co is not None
            else (0, 0, 0, 0),
            int(b.findtext("m_StencilIconGUID") or -1),
            int(b.findtext("flags") or 0),
            tiles,
        )
    return Background(files, states)


@dataclass
class Sequence:
    duration_ms: int
    blocking: bool
    tracks: list[list[tuple[float, dict]]]  # per track: (time 0..1, animated properties)


def parse_sequence(data: bytes | str) -> Sequence:
    """``sequences/*.xml``: keyframed property animation (Alpha, RGBColor, ScaleX, Extent ...)."""
    r = _xml(data)
    tracks = []
    for t in r.findall("track"):
        keys = []
        for k in t.findall("keyframe"):
            p = k.find("properties")
            props = {c.tag: _prop(c) for c in p if c.tag != "time"} if p is not None else {}
            keys.append((float(p.findtext("time") or 0) if p is not None else 0.0, props))
        tracks.append(keys)
    return Sequence(int(r.findtext("duration") or 0), r.findtext("blocking") == "1", tracks)


def parse_editor_schema(data: bytes | str) -> dict[str, dict]:
    """``properties.xml``: the GUI editor's class tree, property groups and value types."""
    out: dict[str, dict] = {}

    def walk(c: ET.Element, parent: str | None) -> None:
        name = _text(c.find("Name"))
        props = {
            _text(p.find("Name")): [
                (_text(v.find("Name")), _text(v.find("DataType")), _text(v.find("DataSet")) or None)
                for v in p.findall("ValueDefinition")
            ]
            for p in c.findall("Property")
        }
        events = [_text(v.find("Name")) for v in c.findall("Events/Property/ValueDefinition")]
        out[name] = {
            "parent": parent,
            "properties": props,
            "events": events,
            "description": _text(c.find("Description")),
        }
        for s in c.findall("GUIClassDefinition"):
            walk(s, name)

    for c in _xml(data).findall("GUIClassDefinition"):
        walk(c, None)
    return out


def parse_css(text: str) -> list[tuple[list[str], dict[str, str]]]:
    """``textstyles.css`` / ``html4.css``: plain CSS rules for the rich-text engine."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    rules = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        decl = {}
        for d in body.split(";"):
            if ":" in d:
                k, v = d.split(":", 1)
                decl[k.strip()] = v.strip()
        rules.append(([s.strip() for s in sel.split(",") if s.strip()], decl))
    if re.sub(r"([^{}]+)\{([^{}]*)\}", "", text).strip():
        raise ValueError("unparsed CSS left over")
    return rules


def parse_tcd(text: str) -> ET.Element:
    """``.tcd`` rich-text samples: HTML-like markup (p, br, img, icon, link, table, colour tags, {guid})."""
    return ET.fromstring(f"<tcd>{text}</tcd>")


@dataclass
class GoldTables:
    header: str
    records: list[list]


def parse_cgt(data: bytes) -> GoldTables:
    """GOLD Parser compiled grammar tables v1 (``css.cgt``, ``tcd.cgt``)."""
    i = 0
    while data[i : i + 2] != b"\0\0":
        i += 2
    header = data[:i].decode("utf-16-le")
    i += 2
    records = []
    while i < len(data):
        if data[i] != ord("M"):
            raise ValueError(f"record tag {data[i]:#x} at {i:#x}")
        n = struct.unpack_from("<H", data, i + 1)[0]
        i += 3
        rec: list = []
        for _ in range(n):
            t = chr(data[i])
            i += 1
            if t == "E":
                rec.append(None)
            elif t in "bB":
                rec.append(data[i])
                i += 1
            elif t == "I":
                rec.append(struct.unpack_from("<h", data, i)[0])
                i += 2
            elif t == "S":
                j = i
                while data[j : j + 2] != b"\0\0":
                    j += 2
                rec.append(data[i:j].decode("utf-16-le"))
                i = j + 2
            else:
                raise ValueError(f"entry type {t!r} at {i - 1:#x}")
        records.append(rec)
    return GoldTables(header, records)


def parse_events_map(text: str) -> dict[str, str]:
    """``events.map``: editor help, ``OnEvent#description`` (German)."""
    return dict(line.split("#", 1) for line in text.splitlines() if "#" in line)


def parse_path_map(data: bytes) -> dict[int, str]:
    """``path.map``: count, then (u32 id, length, latin-1 path) entries; 32- or 64-bit counts and lengths."""
    for size, fmt in ((4, "<I"), (8, "<Q")):
        try:
            n = struct.unpack_from(fmt, data, 0)[0]
            i, out = size, {}
            for _ in range(n):
                pid = struct.unpack_from("<I", data, i)[0]
                ln = struct.unpack_from(fmt, data, i + 4)[0]
                out[pid] = data[i + 4 + size : i + 4 + size + ln].decode("latin-1")
                i += 4 + size + ln
            if 0 <= len(data) - i <= 16:
                return out
        except struct.error:
            pass
    raise ValueError("not a path map")


def parse_layout_names(text: str) -> dict[str, list[tuple[str, bool]]]:
    """``layoutnames.txt``: background state names per control kind; ``*`` marks the required ones."""
    out: dict[str, list[tuple[str, bool]]] = {}
    cur = None
    for line in text.splitlines():
        line = line.strip()
        if line.endswith("-Layouts:"):
            cur = out.setdefault(line[: -len("-Layouts:")], [])
        elif cur is not None and line and not line.startswith("-"):
            cur.append((line.lstrip("*"), line.startswith("*")))
    return out


def read_gui_file(path: str, data: bytes):
    """Parse any file of ``data/config/gui`` by its kind; raises on anything it cannot read."""
    name = path.rsplit("/", 1)[-1].lower()
    ext = name.rsplit(".", 1)[-1]
    if ext in ("dlg", "tpl"):
        return parse_widget_file(data)
    if ext == "bkg":
        return parse_background(data)
    if ext == "png":
        if len(data) == 4:
            return None  # a 4-byte stub instead of a preview
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("not a PNG")
        return struct.unpack(">II", data[16:24])  # editor preview of the .bkg beside it
    if ext == "cgt":
        return parse_cgt(data)
    if ext == "css":
        return parse_css(decode_text(data))
    if ext == "tcd":
        return parse_tcd(decode_text(data))
    if name == "events.map":
        return parse_events_map(decode_text(data))
    if ext == "map":
        return parse_path_map(data)
    if name == "properties.xml":
        return parse_editor_schema(data)
    if "/sequences/" in path.lower():
        return parse_sequence(data)
    if ext == "xml":
        return _xml(data)
    if name == "layoutnames.txt":
        return parse_layout_names(decode_text(data))
    if ext in ("txt", "sh"):
        return decode_text(data).splitlines()
    if ext == "db":
        if not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise ValueError("not an OLE compound file")
        return "thumbs.db"  # a Windows thumbnail cache shipped by accident
    raise ValueError(f"unknown GUI file kind {ext!r}")
