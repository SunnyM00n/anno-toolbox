"""The asset schema of a game in one model: properties and their value definitions, templates, data sets and the
group defaults, read from whichever files the game ships (a full ``properties.xml``, 1800's ``properties-toolone.xml``,
117's ``properties-meta.xml`` beside the stripped export). Stdlib only.

``read_schema`` builds a ``Schema`` from the files' bytes; ``borrow`` lends one game's descriptions, categories and
expert flags to another's by property and value name (117 from 1800). Shapes: ``docs/tool-suite.md`` section 2.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .visibility import Conditions, read_conditions, read_hidden
from .xmltree import flag, number, parse, text, walk_groups

LISTS = ("Vector",)  # a list of ``Item`` children
ARRAYS = ("Array",)  # one child per item of the value's data set
STRUCTS = ("Struct",)
NESTED = ("AutoCreateAsset",)  # an asset made inline: ``Template`` and ``Values`` children
ASSET_TYPES = ("Asset", "AssetGroup", "Quest", "QuestGroup", "AIProfile", "Feature")
NUMBERS = ("Integer", "Int64", "UnsignedInt64", "RealInt", "Float", "FloatOrPercental", "Time", "Color")
KNOWN = {  # the children of a value definition read into fields; the rest stay in ``extras``
    "Name", "DataType", "Items", "DataSet", "InnerDataSet", "NeededProperty", "AllowedTemplates", "AllowedProperties",
    "Min", "Max", "Description", "Category", "IsProValue", "ReadOnly", "IsPercent", "AllowEmpty", "Filter",
}  # fmt: skip


@dataclass
class ValueSpec:
    """One value definition: its type, its constraints and how a form shows it."""

    name: str
    type: str
    items: list[ValueSpec] = field(default_factory=list)  # the item's values of a list, array or struct
    data_set: str = ""
    needed_property: str = ""  # a referenced asset must have this property
    allowed_templates: tuple[str, ...] = ()
    allowed_properties: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""
    category: str = ""
    expert: bool = False
    read_only: bool = False
    percent: bool = False
    allow_empty: bool = False
    file_filter: str = ""
    borrowed: bool = False  # the description is another game's
    written: bool = False  # the description is the editor's own (``descriptions.py``)
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        """How a form treats it: list, array, struct, nested, flags, choice, asset, bool, number, text."""
        t = self.type
        if t in LISTS:
            return "list"
        if t in ARRAYS:
            return "array"
        if t in STRUCTS:
            return "struct"
        if t in NESTED:
            return "nested"
        if t == "Flags":
            return "flags"
        if t == "Choice":
            return "choice"
        if t in ASSET_TYPES:
            return "asset"
        if t == "Boolean":
            return "bool"
        if t in NUMBERS:
            return "number"
        return "text"

    def child(self, name: str) -> ValueSpec | None:
        return next((v for v in self.items if v.name == name), None)


NESTED_FIELDS = {  # an inline asset's own children beside its ``Values``
    "Template": ValueSpec("Template", "String"),
    "IsBaseAutoCreateAsset": ValueSpec("IsBaseAutoCreateAsset", "Boolean"),
}


@dataclass
class PropertySpec:
    name: str
    group: tuple[str, ...]
    values: list[ValueSpec]
    description: str = ""
    written: bool = False  # the description is the editor's own

    def child(self, name: str) -> ValueSpec | None:
        return next((v for v in self.values if v.name == name), None)


@dataclass
class DataSet:
    name: str
    items: list[tuple[str, str]]  # (name, id); the id is empty where the game gives none

    @property
    def names(self) -> list[str]:
        return [n for n, _id in self.items]


@dataclass
class TemplateSpec:
    name: str
    group: tuple[str, ...]
    properties: ET.Element  # the template's ``Properties``: one child per property, with its default values
    hidden: tuple[str, ...] = ()  # the values it calls secondary (``HiddenValues``)

    @property
    def property_names(self) -> list[str]:
        return [c.tag for c in self.properties]


@dataclass
class Schema:
    game: str
    properties: dict[str, PropertySpec] = field(default_factory=dict)
    templates: dict[str, TemplateSpec] = field(default_factory=dict)
    data_sets: dict[str, DataSet] = field(default_factory=dict)
    defaults: dict[str, ET.Element] = field(default_factory=dict)  # per property: the group's ``DefaultValues``
    container_defaults: dict[str, ET.Element] = field(default_factory=dict)  # a new list item's values
    borrowed_from: str = ""
    conditions: Conditions = field(default_factory=dict)  # 1800: when a value is shown (``visibility.py``)

    def spec_at(self, path: tuple[str, ...]) -> ValueSpec | None:
        """The value definition at a ``Values``-relative path (its first name is the property): a list's ``Item``
        and an array's data set names are skipped, an inline asset's ``Values`` lead into its properties."""
        node = self._walk(path)
        return node if isinstance(node, ValueSpec) else None

    def known(self, path: tuple[str, ...]) -> bool:
        """Whether the path names a value or a property block (an inline asset's too) the schema defines."""
        return self._walk(path) is not None

    def _walk(self, path: tuple[str, ...]) -> PropertySpec | ValueSpec | None:
        node: PropertySpec | ValueSpec | None = self.properties.get(path[0]) if path else None
        rest = list(path[1:])
        while rest and node is not None:
            name = rest.pop(0)
            if isinstance(node, ValueSpec) and node.kind == "nested":
                if name in NESTED_FIELDS:
                    return NESTED_FIELDS[name] if not rest else None
                node = self.properties.get(rest.pop(0)) if name == "Values" and rest else None
                continue
            if isinstance(node, ValueSpec) and node.kind in ("list", "array") and node.child(name) is None:
                continue  # Item, or a data set item's name
            node = node.child(name)
        return node

    def counts(self) -> dict[str, int]:
        values = sum(_count(p.values) for p in self.properties.values())
        described = sum(_count(p.values, lambda v: bool(v.description)) for p in self.properties.values())
        return {
            "properties": len(self.properties),
            "values": values,
            "described": described,
            "templates": len(self.templates),
            "data_sets": len(self.data_sets),
        }


def _count(values: list[ValueSpec], keep=lambda v: True) -> int:
    return sum(keep(v) + _count(v.items, keep) for v in values)


def _split(raw: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in raw.split(";") if p.strip())


def read_value(el: ET.Element) -> ValueSpec:
    """A ``ValueDefinition`` element as a ``ValueSpec`` (its ``Items`` recursively)."""
    items = [read_value(v) for v in el.findall("Items/ValueDefinition")]
    extras = {c.tag: (c.text or "").strip() for c in el if c.tag not in KNOWN and not len(c)}
    return ValueSpec(
        name=text(el, "Name"),
        type=text(el, "DataType"),
        items=items,
        data_set=text(el, "DataSet") or text(el, "InnerDataSet"),
        needed_property=text(el, "NeededProperty"),
        allowed_templates=_split(text(el, "AllowedTemplates")),
        allowed_properties=_split(text(el, "AllowedProperties")),
        minimum=number(el, "Min"),
        maximum=number(el, "Max"),
        description=text(el, "Description"),
        category=text(el, "Category"),
        expert=flag(el, "IsProValue"),
        read_only=flag(el, "ReadOnly"),
        percent=flag(el, "IsPercent"),
        allow_empty=flag(el, "AllowEmpty"),
        file_filter=text(el, "Filter"),
        extras=extras,
    )


def read_properties(data: bytes, schema: Schema) -> None:
    """The properties of one properties file into ``schema``, and its groups' default values. A file without value
    definitions (the stripped export) adds only the defaults; a later file's definitions replace an earlier's."""
    root = parse(data)
    schema.conditions.update(read_conditions(root))
    for group, path in walk_groups(root):
        for p in group.findall("Property"):
            values = [read_value(v) for v in p.findall("ValueDefinition")]
            name = text(p, "Name")
            if values or name not in schema.properties:
                schema.properties[name] = PropertySpec(name, path, values, text(p, "Description"))
        for tag, into in (("DefaultValues", schema.defaults), ("DefaultContainerValues", schema.container_defaults)):
            block = group.find(tag)
            for child in block if block is not None else ():
                into.setdefault(child.tag, child)


def read_templates(data: bytes, schema: Schema) -> None:
    for group, path in walk_groups(parse(data)):
        for t in group.findall("Template"):
            props = t.find("Properties")
            schema.templates[text(t, "Name")] = TemplateSpec(
                text(t, "Name"), path, props if props is not None else ET.Element("Properties"), read_hidden(t)
            )


def read_data_sets(data: bytes, schema: Schema) -> None:
    for ds in parse(data).iter("DataSet"):
        items = [(text(i, "Name"), text(i, "Id")) for i in ds.findall("Items/Item")]
        schema.data_sets[text(ds, "Name")] = DataSet(text(ds, "Name"), items)


def read_schema(game: str, properties: list[bytes], templates: bytes | None, data_sets: bytes | None) -> Schema:
    """A game's schema from its files' bytes: the properties files in order (definitions, then defaults), the
    templates and the data sets (either may be missing)."""
    schema = Schema(game)
    for data in properties:
        read_properties(data, schema)
    if templates:
        read_templates(templates, schema)
    if data_sets:
        read_data_sets(data_sets, schema)
    return schema


def borrow(target: Schema, donor: Schema) -> int:
    """Fill ``target``'s empty descriptions and categories (and its expert flags) from ``donor`` where the property
    and the value names match; returns how many values gained a description."""
    gained = 0
    for name, prop in target.properties.items():
        other = donor.properties.get(name)
        if other is None:
            continue
        if not prop.description and other.description:
            prop.description = other.description
        gained += _borrow_values(prop.values, other.values)
    target.borrowed_from = donor.game
    return gained


def _borrow_values(values: list[ValueSpec], donors: list[ValueSpec]) -> int:
    by_name = {v.name: v for v in donors}
    gained = 0
    for v in values:
        d = by_name.get(v.name)
        if d is None:
            continue
        if not v.description and d.description:
            v.description, v.borrowed = d.description, True
            gained += 1
        v.category = v.category or d.category
        v.expert = v.expert or d.expert
        gained += _borrow_values(v.items, d.items)
    return gained
