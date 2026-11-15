"""Working with a decoded Phoenix object tree: find and read objects, set fields, clone, insert, build new ones.

``phoenix`` turns a file into the tree and back; this module changes it. Start at ``set_field`` / ``objects`` for
edits of a shipped file, ``new_object`` for an object with every stored field as the shipped files have them, and
``WidgetFile`` for a widget file of our own with custom properties and bindings
(``docs/formats/phoenix-scene-format.md``, "Editing and new files" and "Bindings").
"""

from __future__ import annotations

import struct
import uuid
import zlib

from . import leb128
from .phoenix import (
    SIZES,
    boxed,
    chain_records,
    decode_string,
    encode_value,
    file_layout,
    full_schema,
    ref_bytes,
    type_chain,
)

NAME_FIELD = "c7501213"  # an object's name string (WidgetInstance, ComponentInstance, CustomPropertyInstance ...)
INSTANCE_REF = "bb96607d"  # the type hash a data object's reference to a widget instance carries
COMPONENT_TEMPLATE = "84f12f7b"
WIDGET_TEMPLATE = "da06bd6e"
IMAGE_ASSET = "232cec0d"

# Reading the tree


def walk(node: dict):
    """Every object of the subtree, pre-order."""
    yield node
    for e in node["fields"]:
        for o in e["objs"]:
            yield from walk(o)


def get_entry(node: dict, field: str) -> dict | None:
    """The present field entry named ``field`` (name or hash), or None."""
    for e in node["fields"]:
        if field in (e["field"]["name"], e["field"]["hash"]):
            return e
    return None


def objects(node: dict, field: str) -> list[dict]:
    e = get_entry(node, field)
    return e["objs"] if e else []


def u32_of(node: dict, field: str) -> int:
    return struct.unpack("<I", get_entry(node, field)["raw"][:4])[0]


def object_name(node: dict) -> str | None:
    e = get_entry(node, NAME_FIELD)
    if e is not None and "raw" in e and len(e["raw"]) > 1:
        return decode_string(e["raw"])
    return None


def find_named(root: dict, name: str) -> list[dict]:
    return [n for n in walk(root) if object_name(n) == name]


def find_parent(root: dict, node: dict) -> tuple[dict, dict]:
    """The object and field entry holding ``node``."""
    for n in walk(root):
        for e in n["fields"]:
            if any(o is node for o in e["objs"]):
                return n, e
    raise KeyError("node not in this tree")


def get_ref(node: dict, field: str) -> str | None:
    """The GUID an asset reference field points at, or None when it is unset."""
    e = get_entry(node, field)
    if e is not None and "raw" in e and e["raw"][:1] == b"\x01":
        return str(uuid.UUID(bytes_le=e["raw"][5:21]))
    if e is not None and e["field"]["name"] == "TextStyle" and e.get("raw", b"")[:1] == bytes([TEXT_STYLE_TAIL]):
        return str(uuid.UUID(bytes_le=e["raw"][1:17]))  # the label form ``set_text_style`` writes
    return None


# Setting fields


def has_field(node: dict, field: str) -> bool:
    """Whether the object's layout has ``field`` (name or hash), present or not: the older layouts lack some."""
    return any(field in (f["name"], f["hash"]) for _, f in node.get("layout", ()))


def _layout_index(node: dict, field: str) -> int:
    idx = next((i for i, (_, f) in enumerate(node["layout"]) if field in (f["name"], f["hash"])), None)
    if idx is None:
        raise KeyError(f"{field} is not a field of {node['schema_type']}")
    return idx


def _entry(node: dict, field: str) -> dict:
    """The entry of ``field``, made present (an empty raw value, the presence bit set) if it was absent."""
    idx = _layout_index(node, field)
    rec, f = node["layout"][idx]
    for e in node["fields"]:
        if e["field"] is f:
            return e
    mask = bytearray(node["mask"])
    mask[idx // 8] |= 0x80 >> (idx % 8)
    node["mask"] = bytes(mask)
    order = {id(g): i for i, (_, g) in enumerate(node["layout"])}
    entry = {"field": f, "record": rec["hash"], "objs": [], "raw": b""}
    pos = next((k for k, e in enumerate(node["fields"]) if order[id(e["field"])] > idx), len(node["fields"]))
    node["fields"].insert(pos, entry)
    return entry


def set_field(node: dict, field: str, value) -> None:
    """Set a plain field (by name or hash) of an object node; an absent field becomes present."""
    e = _entry(node, field)
    e["raw"] = encode_value(int(e["field"]["code"], 16), value)
    e["objs"] = []
    e.pop("head", None)


def set_raw(node: dict, field: str, raw: bytes) -> None:
    """Set a field to already encoded bytes (a boxed value, a reference, an empty list)."""
    e = _entry(node, field)
    e["raw"] = raw
    e["objs"] = []
    e.pop("head", None)


def set_objects(node: dict, field: str, objs: list[dict]) -> None:
    """Set an object field: a list (0x4201) to ``objs``, a single or embedded object (0x4200, 0x4000) to one."""
    e = _entry(node, field)
    code = int(e["field"]["code"], 16)
    e.pop("raw", None)
    e.pop("head", None)
    if code == 0x4201:
        if not objs:
            e["raw"], e["objs"] = b"\x00", []
            return
        e["head"], e["objs"] = leb128.encode(len(objs)), list(objs)
    elif code in (0x4200, 0x4000):
        if len(objs) != 1:
            raise ValueError(f"{field} holds one object")
        e["head"], e["objs"] = b"", list(objs)
    else:
        raise ValueError(f"{field} is not an object field")


def set_ref(node: dict, field: str, type_hash: str, guid: str) -> None:
    """Point an asset reference field (0x4300/0x4400) at another file."""
    e = get_entry(node, field)
    if e is None:
        raise KeyError(f"{field} not present")
    e["raw"] = ref_bytes(type_hash, guid)


# A label's TextStyle as the shipped files store it: flag 01, the type hash c697c96c, the GUID. The decoder cuts it
# four bytes late (the enum before it holds "01 6c c9 97", the entry starts at the hash's last byte), so ``set_ref``
# shifted the hash into the GUID (engine-notes "Text style references").
TEXT_STYLE_HEAD = b"\x01\x6c\xc9\x97"
TEXT_STYLE_TAIL = 0xC6


def set_text_style(node: dict, guid: str) -> None:
    """Point a label's ``TextStyle`` at the style file ``guid``."""
    e = get_entry(node, "TextStyle")
    at = node["fields"].index(e) if e else 0
    before = node["fields"][at - 1] if at > 0 else None
    if before is None or before["raw"] != TEXT_STYLE_HEAD or len(e["raw"]) != 21 or e["raw"][0] != TEXT_STYLE_TAIL:
        raise ValueError("TextStyle is not stored the way the shipped labels have it")
    e["raw"] = bytes([TEXT_STYLE_TAIL]) + uuid.UUID(guid).bytes_le + e["raw"][17:]


def set_name(node: dict, name: str) -> None:
    """Rename an object: the name string and NameCrc (CRC32 of the name), each where the layout has it (the History
    Editions' widgets keep only the CRC; their names are in the editor documents beside the files)."""
    if has_field(node, NAME_FIELD):
        set_field(node, NAME_FIELD, name)
    if has_field(node, "NameCrc"):
        set_field(node, "NameCrc", zlib.crc32(name.encode("utf-8")))


def set_color(node: dict, field: str, rgb: tuple[int, int, int]) -> None:
    """A ColorType field (ColorTint, BackgroundColor) to a plain colour: no palette entry, the given RGB."""
    (ctype,) = objects(node, field)
    set_raw(ctype, "ColorPaletteAsset", b"\x00")
    set_field(ctype, "974ece0d", 0)
    (color,) = objects(ctype, "Color")
    for fname, v in zip(("5767df55", "3aba3bbe", "4ad0cf31"), rgb, strict=True):
        set_field(color, fname, v)


# Moving objects between trees


def clone_object(node: dict, fresh: bool = True, salt: str = "") -> dict:
    """A deep copy of an object node. With ``fresh``, objects that carry their own GUID (widget and
    animation instances) get new ones, and references to them inside the copy (animation targets
    are ``ref ManagedObject <instance guid>``) follow. Field records stay shared with the schema.
    The new GUIDs derive from the old ones and ``salt`` (give each copy of one node its own), so a
    rebuild writes the same bytes."""
    remap: dict[bytes, bytes] = {}
    if fresh:
        for n in walk(node):
            if n.get("guid") is not None and n.get("flags") is not None:
                remap[n["guid"]] = uuid.uuid5(uuid.NAMESPACE_OID, salt + n["guid"].hex()).bytes_le

    def copy(n: dict) -> dict:
        out = {k: v for k, v in n.items() if k not in ("fields", "start")}
        out["guid"] = remap.get(n.get("guid"), n.get("guid"))
        out["fields"] = []
        for e in n["fields"]:
            e2 = {**e, "objs": [copy(o) for o in e["objs"]]}
            for key in ("raw", "head"):
                if key in e2:
                    for old, new in remap.items():
                        e2[key] = e2[key].replace(old, new)
            out["fields"].append(e2)
        return out

    return copy(node)


def insert_object(entry: dict, obj: dict, index: int | None = None) -> None:
    """Insert an object into an object-list field entry (0x4201) and update its count."""
    if int(entry["field"]["code"], 16) != 0x4201:
        raise ValueError(f"{entry['field']['name']} is not an object list")
    objs = entry["objs"]
    objs.insert(len(objs) if index is None else index, obj)
    entry["head"] = leb128.encode(len(objs))
    entry.pop("raw", None)


def adopt(target, source, obj: dict) -> None:
    """Make the types of ``obj`` (taken from the ``source`` Decoder) known to the ``target`` Decoder's type table.
    The layout versions must agree: an object is stored in the layout its file names for each record."""
    for n in walk(obj):
        for rec in type_chain(source.s, n["schema_type"]) + [f"{n['type']:08x}"]:
            g = source.guid_of.get(rec)
            if g is None:
                continue
            if target.guid_of.setdefault(rec, g) != g:
                raise ValueError(f"layout version of {rec} differs: {target.guid_of[rec]} vs {g}")


# Constructing


# fmt: off
DEFAULTS: dict[tuple[str, str], object] = {  # non-zero values most shipped objects of the type carry (check-comments: ok)
    ("0c454dec", "Scale"): (1.0, 1.0, 1.0),
    ("0c454dec", "ConsiderSize"): True,
    ("0c454dec", "AlphaTint"): 1.0,
    ("0c454dec", "Visible"): True,
    ("0c454dec", "IsStatic"): True,
    ("a79767ed", "5767df55"): 255,  # Color: red, green, blue
    ("a79767ed", "3aba3bbe"): 255,
    ("a79767ed", "4ad0cf31"): 255,
    ("c19f99dd", "4ebb2751"): 3,
    ("c19f99dd", "b01b49aa"): 1,
    ("c19f99dd", "UVScale"): (1.0, 1.0),
    ("c19f99dd", "UVApplyRotationAfterScale"): True,
    ("c19f99dd", "DynamicImageSizing"): 1,
    ("41a404e5", "0d2df3ef"): True,
    ("41a404e5", "AllowResize"): True,
    ("41a404e5", "ResizeAnchor"): 0x97C96C01,
    ("41a404e5", "AlignmentY"): 0x3F800000,  # a u32 field holding 1.0f
    ("41a404e5", "HorizontalSizing"): 4,
    ("41a404e5", "VerticalOverflow"): 1,
    ("da06bd6e", "ReferenceSize"): (1920.0, 1080.0),
    ("232cec0d", "4ebb2751"): 3,
    ("c7613662", "BottomRight"): (1.0, 1.0),
    ("6fd1ffc4", "570c3823"): 1,
    ("6fd1ffc4", "3f22df21"): 1,
    ("edcf0433", "IsStatic"): True,
    ("1bb72d66", "UseCount"): 1,
    ("9df79719", "PropertyField"): 1,
    ("9df79719", "UseCount"): 1,
    ("6e5a7f0e", "PropertyField"): 1,
}
ASSET_TYPES = {"da06bd6e": 1, "232cec0d": 2}  # Asset/AssetType of a widget file root and of an image asset
# fmt: on


def _empty_value(code: int) -> bytes:
    """The encoding of a zero or empty value of a type code."""
    if code in SIZES:
        return bytes(SIZES[code])
    if code == 0x1000:
        return bytes(4)
    if code == 0x1F00:
        return bytes(2)  # boxed, type code 0: no value
    return b"\x00"  # empty string, list, reference or object


def new_object(
    schema: dict,
    type_hash: str,
    *,
    guid: bytes | None = None,
    root: bool = False,
    versions: dict[str, str] | None = None,
) -> dict:
    """An object of ``type_hash`` with every stored field present, as the shipped files store them: the
    values most shipped objects carry (``DEFAULTS``), zero or empty otherwise, embedded objects built alike.
    ``guid``: the instance GUID of a widget or animation instance (inline frame flag 2) or of a root;
    ``versions``: the layout version per record (``phoenix.layout_versions``), the full schema layout where none."""
    schema = full_schema(schema)
    versions = versions or {}
    layout = file_layout(schema, type_hash, versions)
    n = len(layout)
    mask = bytearray((n + 7) // 8)
    for i in range(1, n):  # bit 0 is the Object base, never stored
        mask[i // 8] |= 0x80 >> (i % 8)
    node = {
        "flags": None if root else (2 if guid else 0),
        "guid": guid if (root or guid) else None,
        "type": int(type_hash, 16),
        "schema_type": type_hash,
        "n": n,
        "mask": bytes(mask),
        "layout": layout,
        "fields": [],
    }
    for rec, f in layout[1:]:
        code = int(f["code"], 16)
        e = {"field": f, "record": rec["hash"], "objs": []}
        if code == 0x4000:
            e["head"], e["objs"] = b"", [new_object(schema, f["elem"], versions=versions)]
        else:
            e["raw"] = _empty_value(code)
        node["fields"].append(e)
    chain = [r["hash"] for r in chain_records(schema, type_hash)]
    for (rec_hash, fname), value in DEFAULTS.items():
        if rec_hash in chain and any(fname in (f["name"], f["hash"]) for _, f in layout):
            set_field(node, fname, value)
    if type_hash in ASSET_TYPES:
        set_field(node, "AssetType", ASSET_TYPES[type_hash])
    return node


def component(schema: dict, template_guid: str, name: str, uid: int, versions: dict[str, str] | None = None) -> dict:
    """A ComponentInstance of a shipped component template (NumberToString ...)."""
    c = new_object(schema, "51a18126", versions=versions)
    set_field(c, "UniqueId", uid)
    set_raw(c, "ComponentTemplate", ref_bytes(COMPONENT_TEMPLATE, template_guid))
    set_field(c, NAME_FIELD, name)
    return c


def image_asset(
    schema: dict, guid: str, image_id: str, size: tuple[float, float], versions: dict[str, str] | None = None
) -> dict:
    """An ImageAsset root: one picture (``ImageId``, the 1080p size), no nine-slice, no atlas."""
    a = new_object(schema, IMAGE_ASSET, guid=uuid.UUID(guid).bytes_le, root=True, versions=versions)
    set_field(a, "ImageId", image_id)
    set_field(a, "Size", size)
    return a


# Bindings


def _ids(text: str) -> dict[str, int]:
    """``"Name id Name id ..."`` (ids in hex above 0x80000000) as name -> UniqueId."""
    w = text.split()
    return {w[i]: 0x80000000 + int(w[i + 1], 16) for i in range(0, len(w), 2)}


# built-in widget properties as binding ends, from the game's own tables (phoenix-scene-format "Built-in properties")
BUILTIN = _ids(
    "Position 01 PivotPoint 02 RotationAngle 03 Size 04 ColorTint 05 AlphaTint 06 Visible 07 Scale 35"
    " BoundingBoxPosition 36 BoundingBoxSize 37 ZIndex 46 ConsiderSize 80 LayoutScale 3f"
    " AddedToScene 3c RemovingFromScene 3d RemovedFromScene 4d ReferenceSize 3e WidgetCollectionModified 66"
    " Text 08 Html 09 LocalizationId 0a LineSpacing 0b LetterSpacing 0c RelativeOffsetY 0d AbsoluteOffsetY 0e"
    " TextPlane 0f FlipWhenRTL 44 DisplayedText 4f DisplayedOffset 51 DisplayedSize 52 MaxCharCountPerLine 53"
    " MaxLineCount 54 HorizontalSizing 55 VerticalSizing 56 BaselineY 57 AlignmentX 61 AlignmentY 62"
    " HorizontalOverflow 63 VerticalOverflow 64 ActiveAreaType 65 Leading 67 BaseColor 68 IsOutlineEnabled 69"
    " OutlineColor 70 IsShadowEnabled 71 ShadowColor 72 ShadowAlpha 73 ShadowDistance 74 ShadowAngle 75 Italic 77"
    " Underline 78 OutlineAlpha 79 Strikethrough 81 Bold 82 TextSizingFactor 83 NumbersStyle 88 Fractions 89"
    " StandardLigatures 8a DiscretionaryLigatures 8b StylisticAlternate 8c"
    " DynamicImageId 2a ImageId 2a UVOffset 38 UVScale 39 UVRotation 3a UVApplyRotationAfterScale 50"
)  # ImageId: the name the UI code uses; an image's LoadingStarted / LoadingCompleted events are 40 / 41
BUILTIN_VIDEO = _ids(
    "DynamicVideoId 3b AutoStart 45 Volume 47 Mute 48 Repeat 49 Start 13 Stop 14 Pause 15 Resume 16 Restart 17"
    " Started 18 Stopped 19 Paused 1a Resumed 1b Restarted 1c Completed 1d Looped 4a"
)
BUILTIN_ANIMATION = _ids("Start 1f Stop 20 Pause 21 Resume 22 Started 23 Stopped 24 Paused 25 Resumed 26 Completed 28")
PROPERTY_TYPES = {
    "bool": 0,
    "int": 1,
    "u32": 2,
    "float": 3,
    "string": 4,
    "vec2": 5,
    "vec3": 6,
    "event": 9,
    "action": 10,
    "array": 12,
    "enum": 17,
    "color": 18,
    "gameobject": 19,
}
BOXED_CODE = {
    "bool": 0x100,
    "int": 0x700,
    "u32": 0x800,
    "enum": 0x800,
    "float": 0x1000,
    "string": 0x1700,
    "vec2": 0x1100,
    "vec3": 0x1200,
    "id": 0x1900,  # a GUID (an instance a component refers to)
}
ZERO = {
    "bool": False,
    "int": 0,
    "u32": 0,
    "enum": 0,
    "float": 0.0,
    "string": "",
    "vec2": (0.0, 0.0),
    "vec3": (0.0,) * 3,
}
# Data modes of a binding's data object (phoenix-scene-format "Bindings"): a list's element and its item widget
ELEMENT, ITEM = 4, 3
ELEMENT_OBJECT = 0x30000002  # the element's game object (DataMode 4)


def property_value(schema: dict, path: list[int], kind: str, value, versions: dict[str, str] | None = None) -> dict:
    """A PropertyValue: an instance's own value of a property of its template (``path``: UniqueIds, a component's
    first) as a boxed ``kind`` of BOXED_CODE."""
    v = new_object(schema, "2df5f3e4", versions=versions)
    set_field(v, "PropertyIdPath", list(path))
    set_raw(v, "Value", boxed(BOXED_CODE[kind], value))
    return v


def unique_id(base: int, *parts: str) -> int:
    """A stable id in the range the editor gives that kind of id (custom properties 0x1..., components 0x7...)."""
    return base | (zlib.crc32("/".join(parts).encode("utf-8")) & 0x0FFFFFFF)


class WidgetFile:
    """A ContainerWidgetTemplate being assembled: custom properties, child instances, data objects, property
    containers, custom property containers and identity bindings, written into the root by ``finish``.

    A binding end is ``end`` (a property path on a child instance or the widget itself) or ``own`` (one of the
    widget's custom properties); ``bind`` joins two of them (phoenix-scene-format "Bindings")."""

    def __init__(self, schema: dict, root: dict, versions: dict[str, str] | None = None) -> None:
        self.schema = full_schema(schema)
        self.versions = versions or {}
        self.root = root
        self.instances: list[dict] = list(objects(root, "WidgetInstances"))
        self.properties: list[dict] = []
        self.data_objects: list[dict] = []  # each a b0dd1389 with its path of 7e4fae85 entries
        self._dobj_index: dict[int | None, int] = {}
        self.containers: list[dict] = []  # PropertyContainer
        self.self_containers: list[dict] = []  # CustomPropertyContainer
        self.bindings: list[tuple[tuple[str, int], tuple[str, int]]] = []
        self.uids: dict[str, int] = {}

    def _new(self, type_hash: str) -> dict:
        return new_object(self.schema, type_hash, versions=self.versions)

    def add_property(self, name: str, kind: str, default=None) -> int:
        """A public custom property of the widget (``kind`` of PROPERTY_TYPES); returns its UniqueId."""
        if name in self.uids:
            raise ValueError(f"custom property {name} twice")
        uid = unique_id(0x10000000, name)
        if uid in self.uids.values():
            raise ValueError(f"UniqueId of {name} collides")
        p = self._new("1294a820")
        set_field(p, "UniqueId", uid)
        set_field(p, "PropertyType", PROPERTY_TYPES[kind])
        if kind in BOXED_CODE:  # an event, an action, an array or a game object has no default (an empty box)
            set_raw(p, "DefaultValue", boxed(BOXED_CODE[kind], ZERO[kind] if default is None else default))
        set_field(p, "PropertyNameCrc", zlib.crc32(name.encode("utf-8")))
        set_field(p, NAME_FIELD, name)
        self.properties.append(p)
        self.uids[name] = uid
        return uid

    def add_instance(self, node: dict) -> dict:
        self.instances.append(node)
        return node

    def data_object(self, instance: dict | int | None) -> int:
        """The index of the data object for ``instance`` (a direct child), of the widget itself (None), or of a
        list's element or item widget (``ELEMENT`` / ``ITEM``, in a list widget file)."""
        key = None if instance is None else instance if isinstance(instance, int) else id(instance)
        if key in self._dobj_index:
            return self._dobj_index[key]
        item = self._new("7e4fae85")
        if instance is None:
            set_field(item, "DataMode", 1)
        elif isinstance(instance, int):
            set_field(item, "DataMode", instance)
        else:
            set_field(item, "DataMode", 0)
            set_raw(item, "b25f63de", ref_bytes(INSTANCE_REF, instance["guid"]))
        dobj = self._new("b0dd1389")
        set_objects(dobj, "15a30137", [item])
        self.data_objects.append(dobj)
        self._dobj_index[key] = len(self.data_objects) - 1
        return self._dobj_index[key]

    def end(self, instance: dict | int | None, path: list[int], field: int = 1) -> tuple[str, int]:
        """A binding end: a property path (UniqueIds) on a child instance, the widget itself or a list's element or
        item (``data_object``), the whole value of its last property or, with ``field`` 2 / 3 / 4, its x / y / z (a
        scale or a position). An event at the source fires whatever the target is (an event, an action)."""
        pc = self._new("1bb72d66")
        ids = []
        for i, uid in enumerate(path):
            pid = self._new("6e5a7f0e")
            set_field(pid, "UniqueId", uid)
            if i == len(path) - 1 and field != 1:
                set_field(pid, "PropertyField", field)
            ids.append(pid)
        set_objects(pc, "PropertyPath", ids)
        set_field(pc, "a86f2815", self.data_object(instance))
        set_field(pc, "UseCount", 0)
        self.containers.append(pc)
        return ("pc", len(self.containers) - 1)

    def own(self, name: str) -> tuple[str, int]:
        """A binding end: one of this widget's own custom properties."""
        index = next(i for i, p in enumerate(self.properties) if object_name(p) == name)
        cpc = self._new("9df79719")
        set_field(cpc, "CustomPropertyIndex", index)
        set_field(cpc, "UseCount", 0)
        self.self_containers.append(cpc)
        return ("cpc", len(self.self_containers) - 1)

    def bind(self, src: tuple[str, int], dst: tuple[str, int]) -> None:
        self.bindings.append((src, dst))

    def finish(self, salt: str) -> dict:
        """Write everything into the root; ``salt`` makes the bindings' profile ids stable per widget file."""
        offset = {"pc": 0, "cpc": len(self.containers)}  # constants (8e4aa971) would sit between: none written
        holders = {"pc": self.containers, "cpc": self.self_containers}
        out = []
        for k, (src, dst) in enumerate(self.bindings):
            b = self._new("edcf0433")
            set_objects(b, "ConverterInstance", [self._new("c346a1bd")])
            set_field(b, "28c38074", [offset[src[0]] + src[1]])
            set_field(b, "cec4084b", [offset[dst[0]] + dst[1]])
            set_field(b, "ProfileID", str(uuid.uuid5(uuid.NAMESPACE_URL, f"anno-toolbox/ui/{salt}/binding/{k}")))
            for kind, i in (src, dst):
                node = holders[kind][i]
                set_field(node, "UseCount", u32_of(node, "UseCount") + 1)
            out.append(b)
        set_objects(self.root, "WidgetInstances", self.instances)
        set_objects(self.root, "18b0dff0", self.properties)
        set_objects(self.root, "a4336119", out)
        set_objects(self.root, "8fda299c", self.data_objects)
        set_objects(self.root, "PropertyContainers", self.containers)
        set_objects(self.root, "8e4aa971", [])
        set_objects(self.root, "7ff8905c", self.self_containers)
        return self.root
