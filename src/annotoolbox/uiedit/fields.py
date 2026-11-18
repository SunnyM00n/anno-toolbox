"""An object's fields as typed values: decoded from and encoded into the codec's tree by type code, with the names
of enum values and the built-in properties a binding reaches on each widget class.

``fields_of(node)`` lists every field of an object's layout (present or not) as a ``Field``; ``set_value`` writes
one back through ``phoenixedit``. ``BUILTINS`` is the table of built-in properties
(docs/formats/phoenix-scene-format.md, "Built-in properties"); ``ENUMS`` the value names the editor shows.
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass

from ..formats import leb128, phoenix, phoenixedit
from ..infra.i18n import tr

# type codes (docs/formats/phoenix-scene-format.md, "Object encoding")
BOOL, U8, U16, I32, U32, I64, U64, ENUM, FLOAT = 0x100, 0x400, 0x600, 0x700, 0x800, 0x900, 0xA00, 0xB00, 0x1000
VEC2, VEC3, VEC4, STRING, ID, KEY = 0x1100, 0x1200, 0x1300, 0x1700, 0x1900, 0x1A00
U32_LIST, BOXED, EMBEDDED, OBJECT, OBJECTS = 0x801, 0x1F00, 0x4000, 0x4200, 0x4201
REFS = (0x4300, 0x4400)
REF_LISTS = (0x4401, 0x4100)
PLAIN = {BOOL, U8, U16, I32, U32, I64, U64, ENUM, FLOAT, VEC2, VEC3, VEC4, STRING, ID, KEY, U32_LIST}
CODE_NAMES = {
    BOOL: "bool", U8: "u8", U16: "u16", I32: "int", U32: "u32", I64: "i64", U64: "u64", ENUM: "enum",
    FLOAT: "float", VEC2: "vec2", VEC3: "vec3", VEC4: "vec4", STRING: "string", ID: "id", KEY: "key",
    U32_LIST: "ids", BOXED: "boxed", EMBEDDED: "object", OBJECT: "object", OBJECTS: "objects",
    0x4300: "reference", 0x4400: "reference", 0x4401: "references", 0x4100: "references", 0x401: "bytes",
}  # fmt: skip
_VEC = {VEC2: 2, VEC3: 3, VEC4: 4}


def decode(code: int, raw: bytes):
    """A plain, boxed or reference value from its stored bytes (``None`` for an unset reference)."""
    if code == BOOL:
        return bool(raw[0])
    if code in (U8,):
        return raw[0]
    if code == U16:
        return struct.unpack("<H", raw[:2])[0]
    if code == I32:
        return struct.unpack("<i", raw[:4])[0]
    if code in (U32, ENUM):
        return struct.unpack("<I", raw[:4])[0]
    if code in (I64, KEY):
        return struct.unpack("<q", raw[:8])[0]
    if code == U64:
        return struct.unpack("<Q", raw[:8])[0]
    if code == FLOAT:
        return struct.unpack("<f", raw[:4])[0]
    if code in _VEC:
        return struct.unpack(f"<{_VEC[code]}f", raw[: 4 * _VEC[code]])
    if code == STRING:
        return phoenix.decode_string(raw)
    if code == ID:
        return str(uuid.UUID(bytes_le=raw[:16]))
    if code == U32_LIST:
        n, used = leb128.decode(raw)
        return list(struct.unpack(f"<{n}I", raw[used : used + 4 * n]))
    if code in REFS:
        return str(uuid.UUID(bytes_le=raw[5:21])) if raw[:1] == b"\x01" else None
    if code in REF_LISTS:
        n, used = leb128.decode(raw)
        return [str(uuid.UUID(bytes_le=raw[used + 20 * i + 4 : used + 20 * i + 20])) for i in range(n)]
    if code == BOXED:
        return decode_boxed(raw)
    return raw


def decode_boxed(raw: bytes) -> tuple[int, object]:
    """A boxed value as (type code, value); code 0 is an empty box (an event, an action, a game object)."""
    code = struct.unpack("<H", raw[:2])[0]
    if code == 0 or code not in PLAIN:
        return code, None
    return code, decode(code, raw[2:])


def encode(code: int, value) -> bytes:
    """A plain value in the encoding of its type code (the codec's, with the integer kinds it lacks)."""
    if code in (I64, KEY):
        return struct.pack("<q", int(value))
    if code == U64:
        return struct.pack("<Q", int(value))
    if code == I32:
        return struct.pack("<i", int(value))
    return phoenix.encode_value(code, value)


@dataclass
class Field:
    """One field of an object: its schema record and field, whether the file stores it, its value or objects."""

    record: str
    schema: dict  # the schema field (name, hash, code, elem, flags)
    present: bool
    value: object = None
    objs: tuple = ()

    @property
    def name(self) -> str:
        return self.schema["name"] or self.schema["hash"]

    @property
    def code(self) -> int:
        return int(self.schema["code"], 16)

    @property
    def kind(self) -> str:
        return CODE_NAMES.get(self.code, f"{self.code:#x}")

    @property
    def editable(self) -> bool:
        return self.code in PLAIN or self.code in REFS or self.code == BOXED


def fields_of(node: dict) -> list[Field]:
    """Every field of the object's layout, Object's base bit left out, in file order."""
    entries = {id(e["field"]): e for e in node["fields"]}
    out = []
    for rec, f in node["layout"][1:] if node.get("layout") else []:
        e = entries.get(id(f))
        if e is None:
            out.append(Field(rec["hash"], f, False))
        elif e["objs"]:
            out.append(Field(rec["hash"], f, True, None, tuple(e["objs"])))
        else:
            out.append(Field(rec["hash"], f, True, value_of(node, e)))
    return out


def value_of(node: dict, entry: dict):
    """The value of a present plain entry (a label's TextStyle through its own form)."""
    f = entry["field"]
    if f["name"] == "TextStyle":
        return phoenixedit.get_ref(node, "TextStyle")
    code = int(f["code"], 16)
    if code in (OBJECTS, OBJECT) and entry.get("raw", b"\x00")[:1] == b"\x00":
        return None
    return decode(code, entry["raw"]) if "raw" in entry else None


def get(node: dict, field: str, default=None):
    """The value of the field ``field`` (name or hash), ``default`` when the file does not store it."""
    e = phoenixedit.get_entry(node, field)
    if e is None or "raw" not in e:
        return default
    return value_of(node, e)


def set_value(node: dict, field: str, value) -> None:
    """Write ``value`` into the field ``field``: a plain value, a reference (a GUID, None to unset), a boxed
    ``(code, value)``; the field becomes present when it was not."""
    f = _schema_field(node, field)
    code = int(f["code"], 16)
    if f["name"] == "TextStyle" and value is not None and phoenixedit.get_entry(node, field) is not None:
        phoenixedit.set_text_style(node, value)
    elif code in REFS:
        type_hash = f.get("elem") or "00000000"
        phoenixedit.set_raw(node, field, phoenix.ref_bytes(type_hash, value) if value else b"\x00")
    elif code == BOXED:
        inner, v = value
        phoenixedit.set_raw(node, field, struct.pack("<H", inner) + (encode(inner, v) if inner else b""))
    elif code in (I64, KEY, U64, I32):
        phoenixedit.set_raw(node, field, encode(code, value))
    else:
        phoenixedit.set_field(node, field, value)


def _schema_field(node: dict, field: str) -> dict:
    for _rec, f in node["layout"]:
        if field in (f["name"], f["hash"]):
            return f
    raise KeyError(tr("ui.field_not_type", field=field, type=node["schema_type"]))


# Colours: ColorType (a palette asset, the palette entry's id, the plain RGB)


def color_of(node: dict, field: str = "ColorTint") -> tuple[str | None, int, tuple[int, int, int]] | None:
    """(palette GUID, palette entry id, RGB) of a ColorType field, None when the object has none."""
    objs = phoenixedit.objects(node, field)
    if not objs:
        return None
    ctype = objs[0]
    rgb = (255, 255, 255)
    color = phoenixedit.objects(ctype, "Color")
    if color:
        rgb = tuple(int(get(color[0], h, 255)) for h in ("5767df55", "3aba3bbe", "4ad0cf31"))
    return get(ctype, "ColorPaletteAsset"), int(get(ctype, "ReferencedColor", 0) or 0), rgb


def set_color(node: dict, field: str, rgb: tuple[int, int, int], palette: str | None = None, entry: int = 0) -> None:
    """A ColorType field to an RGB colour, with a palette entry when ``palette`` is given."""
    phoenixedit.set_color(node, field, rgb)
    if palette:
        (ctype,) = phoenixedit.objects(node, field)
        phoenixedit.set_raw(ctype, "ColorPaletteAsset", phoenix.ref_bytes("6af47f78", palette))
        phoenixedit.set_field(ctype, "ReferencedColor", entry)


# The History Editions' widget placement in 117's terms (phoenix-scene-format.md, "The History Editions' files")

ANCHOR_FIELDS = ("AnchorPoint", "XRepositioning", "YRepositioning", "WidthResizing", "HeightResizing", "AspectRatio")
_HISTORY_MODES = {"XRepositioning": "HorizontalAnchorMetrics", "YRepositioning": "VerticalAnchorMetrics",
                  "WidthResizing": "HorizontalResizeMetrics", "HeightResizing": "VerticalResizeMetrics"}  # fmt: skip
_HISTORY_SIDE = {0: 0, 1: 2, 2: 1}  # left / top, right / bottom, centre -> 117's column / row of the anchor


def anchor_fields(node: dict) -> dict[str, int]:
    """A widget's anchor point (0..8, row by row from the top left), repositioning, resizing and aspect ratio modes:
    its own (117, 1800), or what the History Editions' anchor constraints, metrics and KeepAspectRatio say."""
    h = get(node, "HorizontalAnchorConstraint")
    if h is None:
        return {name: int(get(node, name, 0) or 0) for name in ANCHOR_FIELDS}
    v = int(get(node, "VerticalAnchorConstraint", 0) or 0)
    out = {"AnchorPoint": _HISTORY_SIDE.get(int(h), 0) + 3 * _HISTORY_SIDE.get(v, 0)}
    out.update({name: int(get(node, old, 0) or 0) for name, old in _HISTORY_MODES.items()})
    out["AspectRatio"] = 1 if get(node, "KeepAspectRatio") else 0  # keep it: fit the smaller axis (inferred)
    return out


def alignment_index(value) -> int:
    """A label's ``AlignmentX`` as 0 left, 1 centre, 2 right, 3 justify (117 and the History Editions store these)."""
    v = int(value or 0)
    return v if v in (1, 2, 3) else 0


# Enum value names the editor shows (docs/ui/117/layout.md, widget-types.md; the Widget Library's enums)


def _names(text: str) -> dict[int, str]:
    return dict(enumerate(text.split()))


# fmt: off
ENUMS: dict[str, dict[int, str]] = {
    "AnchorPoint": _names("TopLeft TopCenter TopRight MiddleLeft Center MiddleRight BottomLeft BottomCenter BottomRight"),
    "AspectRatio": _names("Off Fit FollowWidth FollowHeight"),
    "BlendingMode": {0: "Normal", 2: "Add", 3: "Multiply", 4: "None"},
    "XRepositioning": _names("Proportional Fixed FixedToParent"),  # LayoutMetrics
    "YRepositioning": _names("Proportional Fixed FixedToParent"),
    "WidthResizing": _names("Proportional Fixed FixedToParent"),
    "HeightResizing": _names("Proportional Fixed FixedToParent"),
    "HorizontalOverflow": _names("DeprecatedAutosize Wrap ClipPixel ClipCharacter Ellipsis Visible"),
    "VerticalOverflow": _names("DeprecatedAutosize Ellipsis ClipPixel ClipLine Visible"),
    "HorizontalSizing": _names("TextPlane DisplayedContent Largest Smallest"),  # TextBoxSizing
    "VerticalSizing": _names("TextPlane DisplayedContent Largest Smallest"),
    "AlignmentX": _names("Left Center Right Justify"),
    "AlignmentY": _names("Top Center Baseline Bottom"),
    "ActiveAreaType": _names("TextPlane DisplayedContent"),
    "HorizontalAnchorConstraint": _names("Left Right Center"),  # the History Editions' widgets
    "VerticalAnchorConstraint": _names("Top Bottom Center"),
    "HorizontalAnchorMetrics": _names("Proportional Fixed FixedToParent"),
    "VerticalAnchorMetrics": _names("Proportional Fixed FixedToParent"),
    "HorizontalResizeMetrics": _names("Proportional Fixed FixedToParent"),
    "VerticalResizeMetrics": _names("Proportional Fixed FixedToParent"),
    "PropertyType": {0: "Boolean", 1: "Int32", 2: "UInt32", 3: "Float32", 4: "String", 5: "Vector2", 6: "Vector3",
                     9: "Event", 10: "Action", 11: "WidgetSibling", 12: "Array", 13: "LocalizationId",
                     16: "AnyGameObject", 17: "Enum", 18: "Color", 19: "GameObject", 22: "Spacing", 23: "WidgetChild"},
    "PropertyAccessibility": {0: "Public", 1: "Internal", 2: "Hub"},
    "DataMode": {0: "Child", 1: "Self", 2: "Animation", 3: "Item", 4: "Element", 5: "Group"},
}
# fmt: on
FIELD_LABELS = {  # names for the hash fields the schema leaves unnamed (widget-types.md "Every widget")
    "c7501213": "Name",
    "c90bf7fc": "Masked",
    "570c3823": "InputCount",
    "3f22df21": "OutputCount",
}  # fmt: skip


def label(f: Field) -> str:
    """The name the property editor shows for a field: the schema's, ours for a known hash, else the hash."""
    return f.schema["name"] or FIELD_LABELS.get(f.schema["hash"], f.schema["hash"])


def enum_names(field_name: str) -> dict[int, str]:
    return ENUMS.get(field_name, {})


# Built-in properties (docs/formats/phoenix-scene-format.md, "Built-in properties"): per widget class the
# properties, events and actions, as (name, id - 0x80000000)


@dataclass(frozen=True)
class Builtin:
    uid: int
    name: str
    owner: str  # Widget, CompositeWidget, LabelWidget, ImageWidget, VectorShapeWidget, VideoWidget, Animation
    kind: str  # property, event, action
    vtype: str = "value"  # bool, float, vec2, vec3, string, color, int, event, action, value


_TYPES = {
    "Position": "vec3", "PivotPoint": "vec3", "RotationAngle": "float", "Size": "vec2", "ColorTint": "color",
    "AlphaTint": "float", "Visible": "bool", "Scale": "vec3", "BoundingBoxPosition": "vec3",
    "BoundingBoxSize": "vec2", "LayoutScale": "vec3", "ZIndex": "int", "ConsiderSize": "bool",
    "ReferenceSize": "vec2", "Text": "string", "Html": "bool", "LocalizationId": "string", "LineSpacing": "float",
    "LetterSpacing": "float", "TextPlane": "vec2", "DisplayedText": "string", "DisplayedSize": "vec2",
    "DisplayedOffset": "vec3", "BaseColor": "color", "OutlineColor": "color", "ShadowColor": "color",
    "DynamicImageId": "string", "UVOffset": "vec2", "UVScale": "vec2", "UVRotation": "float",
    "DynamicVideoId": "string", "AutoStart": "bool", "Volume": "float", "Mute": "bool", "Repeat": "int",
    "IsOutlineEnabled": "bool", "IsShadowEnabled": "bool", "Italic": "bool", "Bold": "bool", "Underline": "bool",
    "Strikethrough": "bool", "ShadowAlpha": "float", "OutlineAlpha": "float",
}  # fmt: skip
_TABLE = (
    ("Widget", "Position 01 PivotPoint 02 RotationAngle 03 Size 04 ColorTint 05 AlphaTint 06 Visible 07 Scale 35"
     " BoundingBoxPosition 36 BoundingBoxSize 37 LayoutScale 3f ZIndex 46 ConsiderSize 80",
     "AddedToScene 3c RemovingFromScene 3d RemovedFromScene 4d", ""),
    ("CompositeWidget", "ReferenceSize 3e", "WidgetCollectionModified 66", ""),
    ("LabelWidget", "Text 08 Html 09 LocalizationId 0a LineSpacing 0b LetterSpacing 0c RelativeOffsetY 0d"
     " AbsoluteOffsetY 0e TextPlane 0f FlipWhenRTL 44 DisplayedText 4f DisplayedOffset 51 DisplayedSize 52"
     " MaxCharCountPerLine 53 MaxLineCount 54 HorizontalSizing 55 VerticalSizing 56 BaselineY 57 AlignmentX 61"
     " AlignmentY 62 HorizontalOverflow 63 VerticalOverflow 64 ActiveAreaType 65 Leading 67 BaseColor 68"
     " IsOutlineEnabled 69 OutlineColor 70 IsShadowEnabled 71 ShadowColor 72 ShadowAlpha 73 ShadowDistance 74"
     " ShadowAngle 75 Italic 77 Underline 78 OutlineAlpha 79 Strikethrough 81 Bold 82 TextSizingFactor 83"
     " NumbersStyle 88 Fractions 89 StandardLigatures 8a DiscretionaryLigatures 8b StylisticAlternate 8c", "", ""),
    ("ImageWidget", "DynamicImageId 2a UVOffset 38 UVScale 39 UVRotation 3a UVApplyRotationAfterScale 50",
     "LoadingStarted 40 LoadingCompleted 41", ""),
    ("VectorShapeWidget", "", "LoadingStarted 4b LoadingCompleted 4c", ""),
    ("VideoWidget", "DynamicVideoId 3b AutoStart 45 Volume 47 Mute 48 Repeat 49",
     "Started 18 Stopped 19 Paused 1a Resumed 1b Restarted 1c Completed 1d Looped 4a BufferingStarted 84"
     " BufferingStopped 85 SeekingStarted 86 SeekingStopped 87 LoadingStarted 42 LoadingCompleted 43",
     "Start 13 Stop 14 Pause 15 Resume 16 Restart 17"),
    ("Animation", "", "Started 23 Stopped 24 Paused 25 Resumed 26 Completed 28", "Start 1f Stop 20 Pause 21 Resume 22"),
)  # fmt: skip


def _builtins() -> tuple[Builtin, ...]:
    out = []
    for owner, props, events, actions in _TABLE:
        for kind, text in (("property", props), ("event", events), ("action", actions)):
            w = text.split()
            for i in range(0, len(w), 2):
                vtype = kind if kind != "property" else _TYPES.get(w[i], "value")
                out.append(Builtin(0x80000000 + int(w[i + 1], 16), w[i], owner, kind, vtype))
    return tuple(out)


BUILTINS = _builtins()
BUILTIN_BY_ID = {b.uid: b for b in BUILTINS}
# the widget classes an instance kind has (its own and its bases')
CLASSES = {
    "container": ("Widget", "CompositeWidget"),
    "group": ("Widget", "CompositeWidget"),
    "label": ("Widget", "CompositeWidget", "LabelWidget"),
    "image": ("Widget", "ImageWidget"),
    "video": ("Widget", "VideoWidget"),
    "shape": ("Widget", "VectorShapeWidget"),
    "animation": ("Animation",),
}


def builtins_of(kind: str) -> list[Builtin]:
    """The built-in properties, events and actions of an element kind (``elements.KINDS``)."""
    owners = CLASSES.get(kind, ("Widget",))
    return [b for b in BUILTINS if b.owner in owners]


def property_values(node: dict) -> dict[tuple[int, ...], object]:
    """An instance's own values of its template's and components' properties, by id path (placing reads them
    through ``layout.Context.values``, which keeps them until an edit)."""
    out = {}
    for pv in phoenixedit.objects(node, "PropertyValues"):
        path = tuple(get(pv, "PropertyIdPath", []) or [])
        boxed = get(pv, "Value")
        out[path] = boxed[1] if isinstance(boxed, tuple) else None
    return out


def dynamic_properties(node: dict) -> list[tuple[int, object]]:
    """The History Editions' ``DynamicProperties`` of a container instance: (AtomPropertyId, value) of what it gives
    the properties of the file it places (an inline object, a colour, as None, as a boxed object reads in 117's)."""
    e = phoenixedit.get_entry(node, "DynamicProperties")
    values = phoenix.typed_values(e) if e is not None else []
    return [(atom, None if isinstance(raw, dict) else decode(code, raw)) for atom, code, raw in values]
