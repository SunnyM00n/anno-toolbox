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


def _text(element: ET.Element | None) -> str:
    return (element.text or "").strip() if element is not None else ""


@dataclass
class SEvent:
    type: str
    slot: str
    script: str | None  # Lua; None: a handler of the dialog's own code


@dataclass
class SModel:
    """Lua component model of a CUSTOM widget: attributes (type, getter, setter), actions, events."""

    name: str
    uid: int | None
    attributes: dict[str, tuple[int, str, str]] = field(default_factory=dict)
    actions: dict[str, str] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    constructor: str = ""


@dataclass
class SWidget:
    kind: str  # element name: FRAME, PANEL, ... or TEMPLATE
    path_id: str
    uid: int | None
    templates: list[str]  # TEMPLATE_REF_ID (a few nodes carry two)
    props: dict[str, str | dict] = field(default_factory=dict)
    children: list[SWidget] = field(default_factory=list)
    events: list[SEvent] = field(default_factory=list)
    model: SModel | None = None

    @property
    def cls(self) -> str | None:
        return WIDGET_CLASSES.get(self.kind)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def _prop(element: ET.Element) -> str | dict:
    return _text(element) if len(element) == 0 else {child.tag: _prop(child) for child in element}


def _model(element: ET.Element) -> SModel:
    model = SModel(_text(element.find("NAME")), int(_text(element.find("UNIQUEID")) or 0) or None)
    for child in element:
        if child.tag == "ATTRIBUTE":
            model.attributes[_text(child.find("NAME"))] = (
                int(_text(child.find("TYPE")) or 0),
                _text(child.find("GETTER")),
                _text(child.find("SETTER")),
            )
        elif child.tag == "ACTION":
            model.actions[_text(child.find("NAME"))] = _text(child.find("SCRIPT"))
        elif child.tag == "EVENT":
            model.events.append(_text(child.find("NAME")))
        elif child.tag == "CONSTRUCTOR":
            model.constructor = _text(child)
    return model


def _widget(element: ET.Element) -> SWidget:
    uid = _text(element.find("UNIQUEID"))
    widget = SWidget(element.tag, _text(element.find("PATHID")), int(uid) if uid else None, [])
    for child in element:
        if child.find("PATHID") is not None:
            widget.children.append(_widget(child))
        elif child.tag == "TEMPLATE_REF_ID":
            if _text(child):
                widget.templates.append(_text(child))
        elif child.tag == "EVENTLIST":
            for event in child.findall("EVENT"):
                script = event.find("SCRIPT")
                widget.events.append(
                    SEvent(
                        _text(event.find("TYPE")),
                        _text(event.find("SLOT")),
                        None if script is None else script.text or "",
                    )
                )
        elif child.tag == "MODEL":
            widget.model = _model(child)
        elif child.tag not in ("PATHID", "UNIQUEID"):
            widget.props[child.tag] = _prop(child)
    return widget


def parse_widget_file(data: bytes | str) -> SWidget:
    """A ``.dlg`` (root FRAME) or ``.tpl`` (root TEMPLATE, a property bag without children)."""
    return _widget(_xml(data))


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, value in over.items():
        out[k] = _merge(out[k], value) if isinstance(value, dict) and isinstance(out.get(k), dict) else value
    return out


class CTemplates:
    """``TEMPLATE_REF_ID`` resolution: a template is found by its PATHID (= file name) and inherits its own."""

    def __init__(self, templates: list[SWidget]) -> None:
        self.m_by_name = {template.path_id: template for template in templates}

    def chain(self, names: list[str]) -> list[SWidget]:
        out, todo, seen = [], list(names), set()
        while todo:
            name = todo.pop(0)
            if name in seen or name not in self.m_by_name:
                continue
            seen.add(name)
            template = self.m_by_name[name]
            out.append(template)
            todo.extend(template.templates)
        return out

    def missing(self, names: list[str]) -> list[str]:
        return [name for name in names if name not in self.m_by_name]

    def effective(self, widget: SWidget) -> dict:
        """Own properties over the template chain (nearest wins; EXTENT, TEXTSETTING ... merge per field)."""
        props: dict = {}
        for template in reversed(self.chain(widget.templates)):
            props = _merge(props, template.props)
        return _merge(props, widget.props)


def struct_templates(custom_windows: SWidget) -> dict[int, SWidget]:
    """``INSTANCE``/``STRUCTTEMPLATEREF`` targets: the CUSTOM components of ``__custom_windows.dlg`` by UNIQUEID."""
    return {
        window.uid: window for window in custom_windows.children if window.kind == "CUSTOM" and window.uid is not None
    }


def extent(props: dict) -> tuple[int, int, int, int]:
    """(left, top, width, height) in the 1280x1024 reference space, relative to the parent's client area."""
    extent_props = props.get("EXTENT") or {}
    extent_props = extent_props if isinstance(extent_props, dict) else {}
    return tuple(int(extent_props.get(k) or 0) for k in ("LEFT", "TOP", "WIDTH", "HEIGHT"))  # type: ignore[return-value]


def tooltip_text_id(prop: str, guid: int) -> int:
    """Key of a tooltip in loca ``tt_normal`` / ``tt_disabled`` / ``tt_pressed``: state digit + 7-digit GUID."""
    return TOOLTIP_TEXT_OFFSET[prop] + guid


def parse_color(text: str) -> tuple[str, tuple[float, ...]] | str | int | None:
    """``HSV;h;s;v`` (degrees, percent), a 0xRRGGBB int, or a KnownColors name (texteditor/properties.xml)."""
    if not text:
        return None
    if text.startswith("HSV;"):
        return ("HSV", tuple(float(x) for x in text[4:].split(";")))
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def argb_rgb(value: int) -> tuple[int, int, int]:
    """An ARGB (or 0xRRGGBB) integer as RGB."""
    argb = value & 0xFFFFFFFF
    return (argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF


def parse_text_colors(data: bytes | str) -> dict[str, tuple[int, int, int]]:
    """The named text colours of ``texteditor/properties.xml`` (``KnownColors``: ARGB integers) as RGB."""
    out: dict[str, tuple[int, int, int]] = {}
    for item in _xml(data).iter("Item"):
        name, value = item.findtext("ColorName"), item.findtext("Color")
        if name and value and re.fullmatch(r"-?\d+", value.strip()):
            out[name] = argb_rgb(int(value))
    return out


@dataclass
class STile:
    file_id: int
    x: int
    y: int
    w: int
    h: int
    flags: int  # TILE_STRETCH_X, TILE_STRETCH_Y
    width: int
    height: int


@dataclass
class SBackgroundState:
    name: str  # Normal, Normal+MouseOver, Checked+Disabled, PB_Bar, Thumb_Normal ... (layoutnames.txt)
    grid: tuple[int, int]
    color_hsva: tuple[float, ...]
    icon_color_hsva: tuple[float, ...] | None
    client_offsets: tuple[int, int, int, int]
    stencil_icon: int
    flags: int
    tiles: list[STile]


@dataclass
class SBackground:
    files: list[str]  # LocalFileID -> texture
    states: dict[str, SBackgroundState]


def read_hsva(element: ET.Element, prefix: str) -> tuple[float, ...] | None:
    """``<prefix>_H`` ... ``_A`` of an element (a background state's ``m_Color``), None without them."""
    vals = [element.findtext(f"{prefix}_{channel}") for channel in "HSVA"]
    return None if None in vals else tuple(float(value) for value in vals)  # type: ignore[arg-type]


def parse_background(data: bytes | str) -> SBackground:
    """A ``.bkg``: per state a grid of atlas tiles drawn as a nine-slice (``XSize`` x ``YSize``, row-major)."""
    root = _xml(data)
    files = [_text(i) for i in root.findall("m_localIconFileMap/i")]
    states = {}
    for background_info in root.findall("BackgroundInfo/i"):
        client_offsets = background_info.find("ClientAreaOffsets")
        tiles = [
            STile(
                *(
                    int(i.findtext(k) or 0)
                    for k in ("LocalFileID", "tileX", "tileY", "tileWidth", "tileHeight", "flags", "Width", "Height")
                )
            )
            for i in background_info.findall("IconList/i")
        ]
        name = _text(background_info.find("LayoutName"))
        states[name] = SBackgroundState(
            name,
            (int(background_info.findtext("m_XSize") or 0), int(background_info.findtext("m_YSize") or 0)),
            read_hsva(background_info, "m_Color") or (0.0, 1.0, 1.0, 1.0),
            read_hsva(background_info, "m_IconColor"),
            tuple(int(client_offsets.findtext(k) or 0) for k in ("left", "top", "right", "bottom"))  # type: ignore[arg-type]
            if client_offsets is not None
            else (0, 0, 0, 0),
            int(background_info.findtext("m_StencilIconGUID") or -1),
            int(background_info.findtext("flags") or 0),
            tiles,
        )
    return SBackground(files, states)


@dataclass
class SSequence:
    duration_ms: int
    blocking: bool
    tracks: list[list[tuple[float, dict]]]  # per track: (time 0..1, animated properties)


def parse_sequence(data: bytes | str) -> SSequence:
    """``sequences/*.xml``: keyframed property animation (Alpha, RGBColor, ScaleX, Extent ...)."""
    root = _xml(data)
    tracks = []
    for track in root.findall("track"):
        keys = []
        for k in track.findall("keyframe"):
            properties_element = k.find("properties")
            props = (
                {child.tag: _prop(child) for child in properties_element if child.tag != "time"}
                if properties_element is not None
                else {}
            )
            keys.append(
                (float(properties_element.findtext("time") or 0) if properties_element is not None else 0.0, props)
            )
        tracks.append(keys)
    return SSequence(int(root.findtext("duration") or 0), root.findtext("blocking") == "1", tracks)


def parse_editor_schema(data: bytes | str) -> dict[str, dict]:
    """``properties.xml``: the GUI editor's class tree, property groups and value types."""
    out: dict[str, dict] = {}

    def walk(class_element: ET.Element, parent: str | None) -> None:
        name = _text(class_element.find("Name"))
        props = {
            _text(property_element.find("Name")): [
                (
                    _text(value_definition.find("Name")),
                    _text(value_definition.find("DataType")),
                    _text(value_definition.find("DataSet")) or None,
                )
                for value_definition in property_element.findall("ValueDefinition")
            ]
            for property_element in class_element.findall("Property")
        }
        events = [
            _text(value_definition.find("Name"))
            for value_definition in class_element.findall("Events/Property/ValueDefinition")
        ]
        out[name] = {
            "parent": parent,
            "properties": props,
            "events": events,
            "description": _text(class_element.find("Description")),
        }
        for sub_class in class_element.findall("GUIClassDefinition"):
            walk(sub_class, name)

    for class_element in _xml(data).findall("GUIClassDefinition"):
        walk(class_element, None)
    return out


def parse_css(text: str) -> list[tuple[list[str], dict[str, str]]]:
    """``textstyles.css`` / ``html4.css``: plain CSS rules for the rich-text engine."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    rules = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        decl = {}
        for declaration in body.split(";"):
            if ":" in declaration:
                k, value = declaration.split(":", 1)
                decl[k.strip()] = value.strip()
        rules.append(([selector.strip() for selector in sel.split(",") if selector.strip()], decl))
    if re.sub(r"([^{}]+)\{([^{}]*)\}", "", text).strip():
        raise ValueError("unparsed CSS left over")
    return rules


def parse_tcd(text: str) -> ET.Element:
    """``.tcd`` rich-text samples: HTML-like markup (p, br, img, icon, link, table, colour tags, {guid})."""
    return ET.fromstring(f"<tcd>{text}</tcd>")


@dataclass
class SGoldTables:
    header: str
    records: list[list]


def _string_end(data: bytes, start: int) -> int:
    """The offset of the UTF-16 terminator after ``start``; a file that ends inside a string is not a table file."""
    i = start
    while data[i : i + 2] != b"\0\0":
        if i >= len(data):
            raise ValueError(f"unterminated string at {start:#x}")
        i += 2
    return i


def parse_cgt(data: bytes) -> SGoldTables:
    """GOLD Parser compiled grammar tables v1 (``css.cgt``, ``tcd.cgt``)."""
    i = _string_end(data, 0)
    header = data[:i].decode("utf-16-le")
    i += 2
    records = []
    while i < len(data):
        if data[i] != ord("M"):
            raise ValueError(f"record tag {data[i]:#x} at {i:#x}")
        count = struct.unpack_from("<H", data, i + 1)[0]
        i += 3
        rec: list = []
        for _ in range(count):
            type_char = chr(data[i])
            i += 1
            if type_char == "E":
                rec.append(None)
            elif type_char in "bB":
                rec.append(data[i])
                i += 1
            elif type_char == "I":
                rec.append(struct.unpack_from("<h", data, i)[0])
                i += 2
            elif type_char == "S":
                j = _string_end(data, i)
                rec.append(data[i:j].decode("utf-16-le"))
                i = j + 2
            else:
                raise ValueError(f"entry type {type_char!r} at {i - 1:#x}")
        records.append(rec)
    return SGoldTables(header, records)


def parse_events_map(text: str) -> dict[str, str]:
    """``events.map``: editor help, ``OnEvent#description`` (German)."""
    return dict(line.split("#", 1) for line in text.splitlines() if "#" in line)


def parse_path_map(data: bytes) -> dict[int, str]:
    """``path.map``: count, then (u32 id, length, latin-1 path) entries; 32- or 64-bit counts and lengths."""
    for size, struct_format in ((4, "<I"), (8, "<Q")):
        try:
            count = struct.unpack_from(struct_format, data, 0)[0]
            i, out = size, {}
            for _ in range(count):
                pid = struct.unpack_from("<I", data, i)[0]
                length = struct.unpack_from(struct_format, data, i + 4)[0]
                out[pid] = data[i + 4 + size : i + 4 + size + length].decode("latin-1")
                i += 4 + size + length
            if 0 <= len(data) - i <= 16:
                return out
        except struct.error:
            pass
    raise ValueError("not a path map")


def parse_layout_names(text: str) -> dict[str, list[tuple[str, bool]]]:
    """``layoutnames.txt``: background state names per control kind; ``*`` marks the required ones."""
    out: dict[str, list[tuple[str, bool]]] = {}
    current_layouts = None
    for line in text.splitlines():
        line = line.strip()
        if line.endswith("-Layouts:"):
            current_layouts = out.setdefault(line[: -len("-Layouts:")], [])
        elif current_layouts is not None and line and not line.startswith("-"):
            current_layouts.append((line.lstrip("*"), line.startswith("*")))
    return out


def read_gui_file(path: str, data: bytes):
    """Parse any file of ``data/config/gui`` by its kind; raises on anything it cannot read."""
    name = path.rsplit("/", 1)[-1].lower()
    suffix = name.rsplit(".", 1)[-1]
    if suffix in ("dlg", "tpl"):
        return parse_widget_file(data)
    if suffix == "bkg":
        return parse_background(data)
    if suffix == "png":
        if len(data) == 4:
            return None  # a 4-byte stub instead of a preview
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("not a PNG")
        return struct.unpack(">II", data[16:24])  # editor preview of the .bkg beside it
    if suffix == "cgt":
        return parse_cgt(data)
    if suffix == "css":
        return parse_css(decode_text(data))
    if suffix == "tcd":
        return parse_tcd(decode_text(data))
    if name == "events.map":
        return parse_events_map(decode_text(data))
    if suffix == "map":
        return parse_path_map(data)
    if name == "properties.xml":
        return parse_editor_schema(data)
    if "/sequences/" in path.lower():
        return parse_sequence(data)
    if suffix == "xml":
        return _xml(data)
    if name == "layoutnames.txt":
        return parse_layout_names(decode_text(data))
    if suffix in ("txt", "sh"):
        return decode_text(data).splitlines()
    if suffix == "db":
        if not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise ValueError("not an OLE compound file")
        return "thumbs.db"  # a Windows thumbnail cache shipped by accident
    raise ValueError(f"unknown GUI file kind {suffix!r}")
