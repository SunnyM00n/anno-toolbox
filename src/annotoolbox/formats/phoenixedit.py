"""Working with a decoded Phoenix object tree: find and read objects, set fields, clone, insert, build new ones.

``phoenix`` turns a file into the tree and back; this module changes it. Start at ``set_field`` / ``objects`` for
edits of a shipped file, ``new_object`` for an object with every stored field as the shipped files have them, and
``CWidgetFile`` for a widget file of our own with custom properties and bindings
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
    for entry in node["fields"]:
        for child in entry["objs"]:
            yield from walk(child)


def get_entry(node: dict, field: str) -> dict | None:
    """The present field entry named ``field`` (name or hash), or None."""
    for entry in node["fields"]:
        if field in (entry["field"]["name"], entry["field"]["hash"]):
            return entry
    return None


def objects(node: dict, field: str) -> list[dict]:
    entry = get_entry(node, field)
    return entry["objs"] if entry else []


def u32_of(node: dict, field: str) -> int:
    return struct.unpack("<I", get_entry(node, field)["raw"][:4])[0]


def object_name(node: dict) -> str | None:
    entry = get_entry(node, NAME_FIELD)
    if entry is not None and "raw" in entry and len(entry["raw"]) > 1:
        return decode_string(entry["raw"])
    return None


def find_named(root: dict, name: str) -> list[dict]:
    return [node for node in walk(root) if object_name(node) == name]


def find_parent(root: dict, node: dict) -> tuple[dict, dict]:
    """The object and field entry holding ``node``."""
    for parent in walk(root):
        for entry in parent["fields"]:
            if any(child is node for child in entry["objs"]):
                return parent, entry
    raise KeyError("node not in this tree")


def get_ref(node: dict, field: str) -> str | None:
    """The GUID an asset reference field points at, or None when it is unset."""
    entry = get_entry(node, field)
    if entry is not None and "raw" in entry and entry["raw"][:1] == b"\x01":
        return str(uuid.UUID(bytes_le=entry["raw"][5:21]))
    return None


# Setting fields


def has_field(node: dict, field: str) -> bool:
    """Whether the object's layout has ``field`` (name or hash), present or not: the older layouts lack some."""
    return any(field in (field_info["name"], field_info["hash"]) for _, field_info in node.get("layout", ()))


def _layout_index(node: dict, field: str) -> int:
    index = next((i for i, (_, f) in enumerate(node["layout"]) if field in (f["name"], f["hash"])), None)
    if index is None:
        raise KeyError(f"{field} is not a field of {node['schema_type']}")
    return index


def _entry(node: dict, field: str) -> dict:
    """The entry of ``field``, made present (an empty raw value, the presence bit set) if it was absent."""
    index = _layout_index(node, field)
    record, field_info = node["layout"][index]
    for existing in node["fields"]:
        if existing["field"] is field_info:
            return existing
    mask = bytearray(node["mask"])
    mask[index // 8] |= 0x80 >> (index % 8)
    node["mask"] = bytes(mask)
    order = {id(other_field): i for i, (_, other_field) in enumerate(node["layout"])}
    entry = {"field": field_info, "record": record["hash"], "objs": [], "raw": b""}
    position = next(
        (k for k, existing in enumerate(node["fields"]) if order[id(existing["field"])] > index), len(node["fields"])
    )
    node["fields"].insert(position, entry)
    return entry


def set_field(node: dict, field: str, value) -> None:
    """Set a plain field (by name or hash) of an object node; an absent field becomes present."""
    entry = _entry(node, field)
    entry["raw"] = encode_value(int(entry["field"]["code"], 16), value)
    entry["objs"] = []
    entry.pop("head", None)


def set_raw(node: dict, field: str, raw: bytes) -> None:
    """Set a field to already encoded bytes (a boxed value, a reference, an empty list)."""
    entry = _entry(node, field)
    entry["raw"] = raw
    entry["objs"] = []
    entry.pop("head", None)


def set_objects(node: dict, field: str, children: list[dict]) -> None:
    """Set an object field: a list (0x4201) to ``children``, a single or embedded object (0x4200, 0x4000) to one."""
    entry = _entry(node, field)
    code = int(entry["field"]["code"], 16)
    entry.pop("raw", None)
    entry.pop("head", None)
    if code == 0x4201:
        if not children:
            entry["raw"], entry["objs"] = b"\x00", []
            return
        entry["head"], entry["objs"] = leb128.encode(len(children)), list(children)
    elif code in (0x4200, 0x4000):
        if len(children) != 1:
            raise ValueError(f"{field} holds one object")
        entry["head"], entry["objs"] = b"", list(children)
    else:
        raise ValueError(f"{field} is not an object field")


def set_ref(node: dict, field: str, type_hash: str, guid: str) -> None:
    """Point an asset reference field (0x4300/0x4400) at another file."""
    entry = get_entry(node, field)
    if entry is None:
        raise KeyError(f"{field} not present")
    entry["raw"] = ref_bytes(type_hash, guid)


TEXT_STYLE = "c697c96c"  # the type hash a label's TextStyle reference carries


def set_text_style(node: dict, guid: str) -> None:
    """Point a label's ``TextStyle`` at the style file ``guid`` (present or not)."""
    set_raw(node, "TextStyle", ref_bytes(TEXT_STYLE, guid))


def set_name(node: dict, name: str) -> None:
    """Rename an object: the name string and NameCrc (CRC32 of the name), each where the layout has it (the History
    Editions' widgets keep only the CRC; their names are in the editor documents beside the files)."""
    if has_field(node, NAME_FIELD):
        set_field(node, NAME_FIELD, name)
    if has_field(node, "NameCrc"):
        set_field(node, "NameCrc", zlib.crc32(name.encode("utf-8")))


def set_color(node: dict, field: str, rgb: tuple[int, int, int]) -> None:
    """A ColorType field (ColorTint, BackgroundColor) to a plain colour: no palette entry, the given RGB."""
    (color_type,) = objects(node, field)
    set_raw(color_type, "ColorPaletteAsset", b"\x00")
    set_field(color_type, "974ece0d", 0)
    (color,) = objects(color_type, "Color")
    for field_name, channel in zip(("5767df55", "3aba3bbe", "4ad0cf31"), rgb, strict=True):
        set_field(color, field_name, channel)


# Moving objects between trees


def clone_object(node: dict, fresh: bool = True, salt: str = "") -> dict:
    """A deep copy of an object node. With ``fresh``, objects that carry their own GUID (widget and
    animation instances) get new ones, and references to them inside the copy (animation targets
    are ``ref ManagedObject <instance guid>``) follow. Field records stay shared with the schema.
    The new GUIDs derive from the old ones and ``salt`` (give each copy of one node its own), so a
    rebuild writes the same bytes."""
    remap: dict[bytes, bytes] = {}
    if fresh:
        for descendant in walk(node):
            if descendant.get("guid") is not None and descendant.get("flags") is not None:
                remap[descendant["guid"]] = uuid.uuid5(uuid.NAMESPACE_OID, salt + descendant["guid"].hex()).bytes_le

    def copy(source_node: dict) -> dict:
        result = {key: value for key, value in source_node.items() if key not in ("fields", "start")}
        result["guid"] = remap.get(source_node.get("guid"), source_node.get("guid"))
        result["fields"] = []
        for entry in source_node["fields"]:
            entry_copy = {**entry, "objs": [copy(child) for child in entry["objs"]]}
            for key in ("raw", "head"):
                if key in entry_copy:
                    for old, new in remap.items():
                        entry_copy[key] = entry_copy[key].replace(old, new)
            result["fields"].append(entry_copy)
        return result

    return copy(node)


def insert_object(entry: dict, child: dict, index: int | None = None) -> None:
    """Insert an object into an object-list field entry (0x4201) and update its count."""
    if int(entry["field"]["code"], 16) != 0x4201:
        raise ValueError(f"{entry['field']['name']} is not an object list")
    children = entry["objs"]
    children.insert(len(children) if index is None else index, child)
    entry["head"] = leb128.encode(len(children))
    entry.pop("raw", None)


def adopt(target, source, node: dict) -> None:
    """Make the types of ``node`` (taken from the ``source`` Decoder) known to the ``target`` Decoder's type table.
    The layout versions must agree: an object is stored in the layout its file names for each record."""
    for descendant in walk(node):
        for record in type_chain(source.m_schema, descendant["schema_type"]) + [f"{descendant['type']:08x}"]:
            layout_guid = source.m_guid_of.get(record)
            if layout_guid is None:
                continue
            if target.m_guid_of.setdefault(record, layout_guid) != layout_guid:
                raise ValueError(f"layout version of {record} differs: {target.m_guid_of[record]} vs {layout_guid}")


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
    ("41a404e5", "LineSpacing"): 1.0,
    ("41a404e5", "HorizontalOverflow"): 5,  # Visible
    ("41a404e5", "VerticalOverflow"): 4,  # Visible
    ("41a404e5", "VerticalSizing"): 1,  # DisplayedContent
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
    field_count = len(layout)
    mask = bytearray((field_count + 7) // 8)
    for i in range(1, field_count):  # bit 0 is the Object base, never stored
        mask[i // 8] |= 0x80 >> (i % 8)
    node = {
        "flags": None if root else (2 if guid else 0),
        "guid": guid if (root or guid) else None,
        "type": int(type_hash, 16),
        "schema_type": type_hash,
        "n": field_count,
        "mask": bytes(mask),
        "layout": layout,
        "fields": [],
    }
    for record, field_info in layout[1:]:
        code = int(field_info["code"], 16)
        entry = {"field": field_info, "record": record["hash"], "objs": []}
        if code == 0x4000:
            entry["head"], entry["objs"] = b"", [new_object(schema, field_info["elem"], versions=versions)]
        else:
            entry["raw"] = _empty_value(code)
        node["fields"].append(entry)
    chain = [record["hash"] for record in chain_records(schema, type_hash)]
    for (rec_hash, field_name), value in DEFAULTS.items():
        if rec_hash in chain and any(
            field_name in (field_info["name"], field_info["hash"]) for _, field_info in layout
        ):
            set_field(node, field_name, value)
    if type_hash in ASSET_TYPES:
        set_field(node, "AssetType", ASSET_TYPES[type_hash])
    return node


def component(schema: dict, template: str, name: str, identifier: int, versions: dict[str, str] | None = None) -> dict:
    """A ComponentInstance of a shipped component template (NumberToString ...)."""
    component_node = new_object(schema, "51a18126", versions=versions)
    set_field(component_node, "UniqueId", identifier)
    set_raw(component_node, "ComponentTemplate", ref_bytes(COMPONENT_TEMPLATE, template))
    set_field(component_node, NAME_FIELD, name)
    return component_node


def image_asset(
    schema: dict, guid: str, image_id: str, size_px: tuple[float, float], versions: dict[str, str] | None = None
) -> dict:
    """An ImageAsset root: one picture (``ImageId``, the 1080p size), no nine-slice, no atlas."""
    asset = new_object(schema, IMAGE_ASSET, guid=uuid.UUID(guid).bytes_le, root=True, versions=versions)
    set_field(asset, "ImageId", image_id)
    set_field(asset, "Size", size_px)
    return asset


# Bindings


def _ids(text: str) -> dict[str, int]:
    """``"Name id Name id ..."`` (ids in hex above 0x80000000) as name -> UniqueId."""
    words = text.split()
    return {words[i]: 0x80000000 + int(words[i + 1], 16) for i in range(0, len(words), 2)}


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
    value_node = new_object(schema, "2df5f3e4", versions=versions)
    set_field(value_node, "PropertyIdPath", list(path))
    set_raw(value_node, "Value", boxed(BOXED_CODE[kind], value))
    return value_node


def unique_id(base: int, *parts: str) -> int:
    """A stable id in the range the editor gives that kind of id (custom properties 0x1..., components 0x7...)."""
    return base | (zlib.crc32("/".join(parts).encode("utf-8")) & 0x0FFFFFFF)


class CWidgetFile:
    """A ContainerWidgetTemplate being assembled: custom properties, child instances, data objects, property
    containers, custom property containers and identity bindings, written into the root by ``finish``.

    A binding end is ``end`` (a property path on a child instance or the widget itself) or ``own`` (one of the
    widget's custom properties); ``bind`` joins two of them (phoenix-scene-format "Bindings")."""

    def __init__(self, schema: dict, root: dict, versions: dict[str, str] | None = None) -> None:
        self.m_schema = full_schema(schema)
        self.m_versions = versions or {}
        self.m_root = root
        self.m_instances: list[dict] = list(objects(root, "WidgetInstances"))
        self.m_properties: list[dict] = []
        self.m_data_objects: list[dict] = []  # each a b0dd1389 with its path of 7e4fae85 entries
        self._m_data_object_index: dict[int | None, int] = {}
        self.m_containers: list[dict] = []  # PropertyContainer
        self.m_self_containers: list[dict] = []  # CustomPropertyContainer
        self.m_bindings: list[tuple[tuple[str, int], tuple[str, int]]] = []
        self.m_unique_ids: dict[str, int] = {}

    def _new(self, type_hash: str) -> dict:
        return new_object(self.m_schema, type_hash, versions=self.m_versions)

    def add_property(self, name: str, kind: str, default=None) -> int:
        """A public custom property of the widget (``kind`` of PROPERTY_TYPES); returns its UniqueId."""
        if name in self.m_unique_ids:
            raise ValueError(f"custom property {name} twice")
        identifier = unique_id(0x10000000, name)
        if identifier in self.m_unique_ids.values():
            raise ValueError(f"UniqueId of {name} collides")
        custom_property = self._new("1294a820")
        set_field(custom_property, "UniqueId", identifier)
        set_field(custom_property, "PropertyType", PROPERTY_TYPES[kind])
        if kind in BOXED_CODE:  # an event, an action, an array or a game object has no default (an empty box)
            set_raw(
                custom_property, "DefaultValue", boxed(BOXED_CODE[kind], ZERO[kind] if default is None else default)
            )
        set_field(custom_property, "PropertyNameCrc", zlib.crc32(name.encode("utf-8")))
        set_field(custom_property, NAME_FIELD, name)
        self.m_properties.append(custom_property)
        self.m_unique_ids[name] = identifier
        return identifier

    def add_instance(self, node: dict) -> dict:
        self.m_instances.append(node)
        return node

    def data_object(self, instance: dict | int | None) -> int:
        """The index of the data object for ``instance`` (a direct child), of the widget itself (None), or of a
        list's element or item widget (``ELEMENT`` / ``ITEM``, in a list widget file)."""
        key = None if instance is None else instance if isinstance(instance, int) else id(instance)
        if key in self._m_data_object_index:
            return self._m_data_object_index[key]
        item = self._new("7e4fae85")
        if instance is None:
            set_field(item, "DataMode", 1)
        elif isinstance(instance, int):
            set_field(item, "DataMode", instance)
        else:
            set_field(item, "DataMode", 0)
            set_raw(item, "b25f63de", ref_bytes(INSTANCE_REF, instance["guid"]))
        data_node = self._new("b0dd1389")
        set_objects(data_node, "15a30137", [item])
        self.m_data_objects.append(data_node)
        self._m_data_object_index[key] = len(self.m_data_objects) - 1
        return self._m_data_object_index[key]

    def end(self, instance: dict | int | None, path: list[int], field: int = 1) -> tuple[str, int]:
        """A binding end: a property path (UniqueIds) on a child instance, the widget itself or a list's element or
        item (``data_object``), the whole value of its last property or, with ``field`` 2 / 3 / 4, its x / y / z (a
        scale or a position). An event at the source fires whatever the target is (an event, an action)."""
        property_container = self._new("1bb72d66")
        path_ids = []
        for i, identifier in enumerate(path):
            path_id = self._new("6e5a7f0e")
            set_field(path_id, "UniqueId", identifier)
            if i == len(path) - 1 and field != 1:
                set_field(path_id, "PropertyField", field)
            path_ids.append(path_id)
        set_objects(property_container, "PropertyPath", path_ids)
        set_field(property_container, "a86f2815", self.data_object(instance))
        set_field(property_container, "UseCount", 0)
        self.m_containers.append(property_container)
        return ("pc", len(self.m_containers) - 1)

    def own(self, name: str) -> tuple[str, int]:
        """A binding end: one of this widget's own custom properties."""
        index = next(
            (i for i, custom_property in enumerate(self.m_properties) if object_name(custom_property) == name), None
        )
        if index is None:
            raise KeyError(f"{name} is not a custom property of this widget file")
        custom_container = self._new("9df79719")
        set_field(custom_container, "CustomPropertyIndex", index)
        set_field(custom_container, "UseCount", 0)
        self.m_self_containers.append(custom_container)
        return ("cpc", len(self.m_self_containers) - 1)

    def bind(self, source_end: tuple[str, int], target_end: tuple[str, int]) -> None:
        self.m_bindings.append((source_end, target_end))

    def finish(self, salt: str) -> dict:
        """Write everything into the root; ``salt`` makes the bindings' profile ids stable per widget file."""
        index_offset = {"pc": 0, "cpc": len(self.m_containers)}  # constants (8e4aa971) would sit between: none written
        holders = {"pc": self.m_containers, "cpc": self.m_self_containers}
        binding_nodes = []
        for k, (source_end, target_end) in enumerate(self.m_bindings):
            binding = self._new("edcf0433")
            set_objects(binding, "ConverterInstance", [self._new("c346a1bd")])
            set_field(binding, "28c38074", [index_offset[source_end[0]] + source_end[1]])
            set_field(binding, "cec4084b", [index_offset[target_end[0]] + target_end[1]])
            set_field(
                binding, "ProfileID", str(uuid.uuid5(uuid.NAMESPACE_URL, f"anno-toolbox/ui/{salt}/binding/{k}"))
            )
            for kind, i in (source_end, target_end):
                node = holders[kind][i]
                set_field(node, "UseCount", u32_of(node, "UseCount") + 1)
            binding_nodes.append(binding)
        set_objects(self.m_root, "WidgetInstances", self.m_instances)
        set_objects(self.m_root, "18b0dff0", self.m_properties)
        set_objects(self.m_root, "a4336119", binding_nodes)
        set_objects(self.m_root, "8fda299c", self.m_data_objects)
        set_objects(self.m_root, "PropertyContainers", self.m_containers)
        set_objects(self.m_root, "8e4aa971", [])
        set_objects(self.m_root, "7ff8905c", self.m_self_containers)
        return self.m_root
