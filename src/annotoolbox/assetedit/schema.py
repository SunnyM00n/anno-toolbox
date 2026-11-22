"""The asset schema of a game in one model: properties and their value definitions, templates, data sets and the
group defaults, read from whichever files the game ships (a full ``properties.xml``, 1800's ``properties-toolone.xml``,
117's ``properties-meta.xml`` beside the stripped export). Stdlib only.

``read_schema`` builds a ``SSchema`` from the files' bytes; ``borrow`` lends one game's descriptions, categories and
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
class SValueSpec:
    """One value definition: its type, its constraints and how a form shows it."""

    name: str
    type: str
    items: list[SValueSpec] = field(default_factory=list)  # the item's values of a list, array or struct
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
        type_name = self.type
        if type_name in LISTS:
            return "list"
        if type_name in ARRAYS:
            return "array"
        if type_name in STRUCTS:
            return "struct"
        if type_name in NESTED:
            return "nested"
        if type_name == "Flags":
            return "flags"
        if type_name == "Choice":
            return "choice"
        if type_name in ASSET_TYPES:
            return "asset"
        if type_name == "Boolean":
            return "bool"
        if type_name in NUMBERS:
            return "number"
        return "text"

    def child(self, name: str) -> SValueSpec | None:
        return next((value_spec for value_spec in self.items if value_spec.name == name), None)


NESTED_FIELDS = {  # an inline asset's own children beside its ``Values``
    "Template": SValueSpec("Template", "String"),
    "IsBaseAutoCreateAsset": SValueSpec("IsBaseAutoCreateAsset", "Boolean"),
}


@dataclass
class SPropertySpec:
    name: str
    group: tuple[str, ...]
    values: list[SValueSpec]
    description: str = ""
    written: bool = False  # the description is the editor's own

    def child(self, name: str) -> SValueSpec | None:
        return next((value_spec for value_spec in self.values if value_spec.name == name), None)


@dataclass
class SDataSet:
    name: str
    items: list[tuple[str, str]]  # (name, id); the id is empty where the game gives none

    @property
    def names(self) -> list[str]:
        return [name for name, _id in self.items]


@dataclass
class STemplateSpec:
    name: str
    group: tuple[str, ...]
    properties: ET.Element  # the template's ``Properties``: one child per property, with its default values
    hidden: tuple[str, ...] = ()  # the values it calls secondary (``HiddenValues``)

    @property
    def property_names(self) -> list[str]:
        return [child.tag for child in self.properties]


@dataclass
class SSchema:
    game: str
    properties: dict[str, SPropertySpec] = field(default_factory=dict)
    templates: dict[str, STemplateSpec] = field(default_factory=dict)
    data_sets: dict[str, SDataSet] = field(default_factory=dict)
    defaults: dict[str, ET.Element] = field(default_factory=dict)  # per property: the group's ``DefaultValues``
    container_defaults: dict[str, ET.Element] = field(default_factory=dict)  # a new list item's values
    borrowed_from: str = ""
    conditions: Conditions = field(default_factory=dict)  # 1800: when a value is shown (``visibility.py``)

    def spec_at(self, path: tuple[str, ...]) -> SValueSpec | None:
        """The value definition at a ``Values``-relative path (its first name is the property): a list's ``Item``
        and an array's data set names are skipped, an inline asset's ``Values`` lead into its properties."""
        node = self._walk(path)
        return node if isinstance(node, SValueSpec) else None

    def known(self, path: tuple[str, ...]) -> bool:
        """Whether the path names a value or a property block (an inline asset's too) the schema defines."""
        return self._walk(path) is not None

    def _walk(self, path: tuple[str, ...]) -> SPropertySpec | SValueSpec | None:
        node: SPropertySpec | SValueSpec | None = self.properties.get(path[0]) if path else None
        rest = list(path[1:])
        while rest and node is not None:
            name = rest.pop(0)
            if isinstance(node, SValueSpec) and node.kind == "nested":
                if name in NESTED_FIELDS:
                    return NESTED_FIELDS[name] if not rest else None
                node = self.properties.get(rest.pop(0)) if name == "Values" and rest else None
                continue
            if isinstance(node, SValueSpec) and node.kind in ("list", "array") and node.child(name) is None:
                continue  # Item, or a data set item's name
            node = node.child(name)
        return node

    def counts(self) -> dict[str, int]:
        values = sum(_count(property_spec.values) for property_spec in self.properties.values())
        described = sum(
            _count(property_spec.values, lambda v: bool(v.description)) for property_spec in self.properties.values()
        )
        return {
            "properties": len(self.properties),
            "values": values,
            "described": described,
            "templates": len(self.templates),
            "data_sets": len(self.data_sets),
        }


def _count(values: list[SValueSpec], keep=lambda value_spec: True) -> int:
    return sum(keep(value_spec) + _count(value_spec.items, keep) for value_spec in values)


def _split(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(";") if part.strip())


def read_value(element: ET.Element) -> SValueSpec:
    """A ``ValueDefinition`` element as a ``SValueSpec`` (its ``Items`` recursively)."""
    items = [read_value(item) for item in element.findall("Items/ValueDefinition")]
    extras = {child.tag: (child.text or "").strip() for child in element if child.tag not in KNOWN and not len(child)}
    return SValueSpec(
        name=text(element, "Name"),
        type=text(element, "DataType"),
        items=items,
        data_set=text(element, "DataSet") or text(element, "InnerDataSet"),
        needed_property=text(element, "NeededProperty"),
        allowed_templates=_split(text(element, "AllowedTemplates")),
        allowed_properties=_split(text(element, "AllowedProperties")),
        minimum=number(element, "Min"),
        maximum=number(element, "Max"),
        description=text(element, "Description"),
        category=text(element, "Category"),
        expert=flag(element, "IsProValue"),
        read_only=flag(element, "ReadOnly"),
        percent=flag(element, "IsPercent"),
        allow_empty=flag(element, "AllowEmpty"),
        file_filter=text(element, "Filter"),
        extras=extras,
    )


def read_properties(data: bytes, schema: SSchema) -> None:
    """The properties of one properties file into ``schema``, and its groups' default values. A file without value
    definitions (the stripped export) adds only the defaults; a later file's definitions replace an earlier's."""
    root = parse(data)
    schema.conditions.update(read_conditions(root))
    for group, path in walk_groups(root):
        for property_element in group.findall("Property"):
            values = [read_value(value_element) for value_element in property_element.findall("ValueDefinition")]
            name = text(property_element, "Name")
            if values or name not in schema.properties:
                schema.properties[name] = SPropertySpec(name, path, values, text(property_element, "Description"))
        for tag, into in (("DefaultValues", schema.defaults), ("DefaultContainerValues", schema.container_defaults)):
            block = group.find(tag)
            for child in block if block is not None else ():
                into.setdefault(child.tag, child)


def read_templates(data: bytes, schema: SSchema) -> None:
    for group, path in walk_groups(parse(data)):
        for template_element in group.findall("Template"):
            properties_element = template_element.find("Properties")
            schema.templates[text(template_element, "Name")] = STemplateSpec(
                text(template_element, "Name"),
                path,
                properties_element if properties_element is not None else ET.Element("Properties"),
                read_hidden(template_element),
            )


def read_data_sets(data: bytes, schema: SSchema) -> None:
    for data_set in parse(data).iter("DataSet"):
        items = [(text(i, "Name"), text(i, "Id")) for i in data_set.findall("Items/Item")]
        schema.data_sets[text(data_set, "Name")] = SDataSet(text(data_set, "Name"), items)


def read_schema(game: str, properties: list[bytes], templates: bytes | None, data_sets: bytes | None) -> SSchema:
    """A game's schema from its files' bytes: the properties files in order (definitions, then defaults), the
    templates and the data sets (either may be missing)."""
    schema = SSchema(game)
    for data in properties:
        read_properties(data, schema)
    if templates:
        read_templates(templates, schema)
    if data_sets:
        read_data_sets(data_sets, schema)
    return schema


def borrow(target: SSchema, donor: SSchema) -> int:
    """Fill ``target``'s empty descriptions and categories (and its expert flags) from ``donor`` where the property
    and the value names match; returns how many values gained a description."""
    gained = 0
    for name, property_spec in target.properties.items():
        other = donor.properties.get(name)
        if other is None:
            continue
        if not property_spec.description and other.description:
            property_spec.description = other.description
        gained += _borrow_values(property_spec.values, other.values)
    target.borrowed_from = donor.game
    return gained


def _borrow_values(values: list[SValueSpec], donors: list[SValueSpec]) -> int:
    by_name = {value_spec.name: value_spec for value_spec in donors}
    gained = 0
    for value_spec in values:
        donor = by_name.get(value_spec.name)
        if donor is None:
            continue
        if not value_spec.description and donor.description:
            value_spec.description, value_spec.borrowed = donor.description, True
            gained += 1
        value_spec.category = value_spec.category or donor.category
        value_spec.expert = value_spec.expert or donor.expert
        gained += _borrow_values(value_spec.items, donor.items)
    return gained
