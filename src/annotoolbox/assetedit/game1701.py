"""Anno 1701's data as the asset editor's assets: ``property.ini`` (an asset per object), ``balancingtabelle.txt`` (its
columns as a ``Balancing`` block of the asset with the same GUID) and ``gamevalues.xml`` (one asset, GUID -1), shown
through generated files that look like an asset file and its schema, and written back as the game's own files.

``CProps1701Files`` is the game's ``CGameFiles`` with those generated files added (``assetgame.open_game`` uses it for the
layout's ``virtual`` flag); ``render_files(doc)`` turns a change set into ``property.ini``, the table and the settings.
Layout and mapping: ``docs/asset-editor.md`` "Anno 1701" and ``docs/formats/anno1701-property-ini.md``.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from xml.sax.saxutils import escape, unescape

from ..infra.buildcache import write_atomic
from ..sources import props1701
from ..sources.gametree import CGameTree, game_stamp, open_game
from ..sources.overlay import normalize
from .assetdoc import CAssetDocument
from .gamefiles import GENERATED_1701, CGameFiles

PROPS = "data/config/game/property.ini"
TABLE = "data/project/balancing/balancingtabelle.txt"
VALUES = "data/config/game/gamevalues.xml"
ASSETS = PROPS + ".assets.xml"  # the generated files (a name no game file has)
META, TEMPLATES, DATASETS = PROPS + ".properties.xml", PROPS + ".templates.xml", PROPS + ".datasets.xml"
assert ASSETS.startswith(GENERATED_1701) and META.startswith(GENERATED_1701)
VERSION = 1  # of the generated files: raise it when their shape changes (the editor's cached index follows)
VALUES_GUID = -1  # the asset that holds gamevalues.xml
OBJECT, BALANCING, STANDARD = "Object", "Balancing", "Standard"
UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
COLUMN = re.compile(r"^V(\d+)$")
BLOCK = re.compile(r"^P(\d+)$")


def column_names(header: list[str]) -> list[str]:
    """The XML name of each column of the table after the GUID: its header with the umlauts spelled out and anything
    but letters and digits made an underscore; a name two columns share gets a number."""
    names: list[str] = []
    for text in header[1:]:
        name = re.sub(r"[^A-Za-z0-9]+", "_", text.translate(UMLAUTS)).strip("_") or "Column"
        name = name if name[0].isalpha() else "C" + name
        while name in names:
            name += "_" + str(len(names) + 1)
        names.append(name)
    return names


class CSources:
    """What the generated files are made from, read once: the objects, the table, the settings (as text)."""

    def __init__(self, files: CGameFiles) -> None:
        self.m_props = props1701.parse_props(files.read(PROPS))
        self.m_table = props1701.parse_table(files.read(TABLE)) if files.exists(TABLE) else None
        self.m_values = files.read(VALUES) if files.exists(VALUES) else b""
        self.m_columns = column_names(self.m_table.header) if self.m_table else []
        self.m_types = self.m_props.value_types()

    def template_of(self, prop_object: props1701.SPropObject) -> str:
        return " ".join([OBJECT, *(f"P{block.prop}" for block in prop_object.blocks)])


def _text(parent: ET.Element, tag: str, value: str = "") -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = value
    return element


def _asset(group: ET.Element, template: str, guid: int, name: str) -> tuple[ET.Element, ET.Element]:
    asset = ET.SubElement(group, "Asset")
    _text(asset, "Template", template)
    values = ET.SubElement(asset, "Values")
    standard = ET.SubElement(values, STANDARD)
    _text(standard, "GUID", str(guid))
    _text(standard, "Name", name)
    return asset, values


def assets_xml(sources: CSources) -> bytes:
    """The asset file: one asset per object (grouped by template), per table row without an object, and the settings."""
    top = ET.Element("AssetList")
    root = ET.SubElement(top, "Groups")
    made: dict[str, ET.Element] = {}

    def group(name: str) -> ET.Element:
        if name not in made:
            group_element = ET.SubElement(root, "Group")
            _text(group_element, "Name", name)
            made[name] = ET.SubElement(group_element, "Assets")
        return made[name]

    table = sources.m_table
    for prop_object in sources.m_props.objects:
        _asset_of_object(group(sources.template_of(prop_object)), sources, prop_object)
    known = {candidate.guid for candidate in sources.m_props.objects}
    for guid in table.order if table else ():
        if guid not in known:
            asset, values = _asset(group("Balancing only"), "Balancing only", guid, "")
            _balancing(values, sources, guid)
    if sources.m_values:
        asset, values = _asset(group("GameValues"), "GameValues", VALUES_GUID, "gamevalues.xml")
        for child in _settings(sources.m_values):
            values.append(child)
    return ET.tostring(top, encoding="unicode").encode("utf-8")


def _asset_of_object(assets: ET.Element, sources: CSources, prop_object: props1701.SPropObject) -> None:
    asset, values = _asset(assets, sources.template_of(prop_object), prop_object.guid, prop_object.name)
    _text(
        values.find(STANDARD), "Text", str(prop_object.guid)
    )  # the object's name in the game's texts is the text of its GUID
    block = ET.SubElement(values, OBJECT)
    for value in prop_object.values:
        _text(block, f"V{value.id}", value.text)
    for prop_block in prop_object.blocks:
        element = ET.SubElement(values, f"P{prop_block.prop}")
        for value in prop_block.values:
            _text(element, f"V{value.id}", value.text)
    _balancing(values, sources, prop_object.guid)


def _balancing(values: ET.Element, sources: CSources, guid: int) -> None:
    if sources.m_table is None or guid not in sources.m_table.rows:
        return
    block = ET.SubElement(values, BALANCING)
    for name, cell in zip(sources.m_columns, sources.m_table.rows[guid][1:], strict=True):
        _text(block, name, cell)


def _settings(data: bytes) -> list[ET.Element]:
    """The sections of ``gamevalues.xml`` as elements (comments dropped; the file's comment ``--->`` is not XML)."""
    text = props1701.COMMENT.sub("", data.decode(props1701.TEXT)).lstrip("﻿")
    return list(ET.fromstring(text))


# The schema


def _definition(name: str, kind: str, description: str = "", items: list[str] | None = None) -> str:
    inner = f"<Items>{''.join(items)}</Items>" if items else ""
    note = f"<Description>{escape(description)}</Description>" if description else ""
    return f"<ValueDefinition><DataType>{kind}</DataType><Name>{name}</Name>{note}{inner}</ValueDefinition>"


def _kind_of(text: str) -> str:
    return (
        "Integer"
        if re.fullmatch(r"-?\d+", text.strip())
        else "Float"
        if re.fullmatch(r"-?\d*\.\d+", text.strip())
        else "String"
    )


def _struct(element: ET.Element) -> str:
    if len(element):
        return _definition(element.tag, "Struct", items=[_struct(child) for child in element])
    return _definition(element.tag, _kind_of(element.text or ""))


def _property(name: str, definitions: list[str]) -> str:
    return f"<Property><Name>{name}</Name>{''.join(definitions)}</Property>"


def schema_xml(sources: CSources) -> tuple[bytes, bytes, bytes]:
    """The property definitions, the templates and the (empty) data sets of the generated asset file."""
    types = sources.m_types
    by_prop: dict[int, set[int]] = {}
    for prop_object in sources.m_props.objects:
        for block in prop_object.blocks:
            by_prop.setdefault(block.prop, set()).update(value.id for value in block.values)
    own = sorted({value.id for candidate in sources.m_props.objects for value in candidate.values})

    def numbered(ids) -> list[str]:
        return [_definition(f"V{i}", "Integer" if types.get(i) == 6 else "String") for i in sorted(ids)]

    properties = [
        _property(
            STANDARD, [_definition("GUID", "Integer"), _definition("Name", "String"), _definition("Text", "Text")]
        ),
        _property(OBJECT, numbered(own)),
        *(_property(f"P{prop_id}", numbered(ids)) for prop_id, ids in sorted(by_prop.items())),
    ]
    if sources.m_table:
        columns = [
            _definition(column_name, "String", header)
            for column_name, header in zip(sources.m_columns, sources.m_table.header[1:], strict=True)
        ]
        properties.append(_property(BALANCING, columns))
    sections = _settings(sources.m_values) if sources.m_values else []
    properties += [
        _property(
            section_item.tag,
            [_struct(child) for child in section_item]
            if len(section_item)
            else [_definition(section_item.tag, "String")],
        )
        for section_item in sections
    ]
    templates = {
        sources.template_of(candidate): [STANDARD, OBJECT, *(f"P{block.prop}" for block in candidate.blocks)]
        for candidate in sources.m_props.objects
    }
    templates["Balancing only"] = [STANDARD, BALANCING]
    templates["GameValues"] = [STANDARD, *(section_item.tag for section_item in sections)]
    listed = "".join(
        f"<Template><Name>{name}</Name><Properties>{''.join(f'<{prop_id} />' for prop_id in names)}</Properties></Template>"
        for name, names in templates.items()
    )
    meta = f"<Properties><Groups><Group><Name />{''.join(properties)}</Group></Groups></Properties>"
    return (
        meta.encode(),
        f"<Templates><Group><Name>Objects</Name>{listed}</Group></Templates>".encode(),
        b"<DataSets />",
    )


# The files


class CProps1701Files(CGameFiles):
    """The game's files with the generated asset file, schema, templates and data sets laid over them."""

    def __init__(self, opener: Callable[[], CGameTree], stamp: str, cache: Path) -> None:
        super().__init__(opener, f"{stamp}|1701-{VERSION}", cache)
        self._m_generated: dict[str, bytes] | None = None
        self._m_sources: CSources | None = None

    @property
    def sources(self) -> CSources:
        if self._m_sources is None:
            self._m_sources = CSources(_CPlain(self))
        return self._m_sources

    def generated(self) -> dict[str, bytes]:
        if self._m_generated is None:
            sources = self.sources
            meta, templates, datasets = schema_xml(sources)
            self._m_generated = {ASSETS: assets_xml(sources), META: meta, TEMPLATES: templates, DATASETS: datasets}
        return self._m_generated

    @property
    def paths(self) -> list[str]:
        return sorted({*self.tree.m_paths, *self.generated_names()})

    @staticmethod
    def generated_names() -> tuple[str, ...]:
        return (ASSETS, META, TEMPLATES, DATASETS)

    def exists(self, path: str) -> bool:
        return normalize(path) in self.generated_names() or super().exists(path)

    def read(self, path: str) -> bytes:
        key = normalize(path)
        return self.generated()[key] if key in self.generated_names() else super().read(path)

    def local(self, path: str) -> Path:
        key = normalize(path)
        if key not in self.generated_names():
            return super().local(path)
        destination = (
            self.m_cache
            / "files"
            / f"{hashlib.sha1(f'{self.m_stamp}|{key}'.encode()).hexdigest()[:16]}-{Path(key).name}"
        )
        if not destination.is_file():
            write_atomic(destination, self.read(key), is_synchronous=False)
        return destination


class _CPlain:
    """The game's own files of a ``CProps1701Files`` (never the generated ones): what ``CSources`` reads."""

    def __init__(self, files: CProps1701Files) -> None:
        self._m_files = files

    def read(self, path: str) -> bytes:
        return self._m_files.tree.read(path)

    def exists(self, path: str) -> bool:
        return self._m_files.tree.exists(path)


def open_files(folder: Path, cache: Path) -> CProps1701Files:
    """The installed Anno 1701 at ``folder`` with the generated files."""
    folder = Path(folder)
    return CProps1701Files(lambda: open_game("anno1701", folder, cache), game_stamp("anno1701", folder), cache)


# Writing back


def is_1701(doc: CAssetDocument) -> bool:
    return isinstance(doc.m_files, CProps1701Files)


def _values_of(element: ET.Element | None) -> list[props1701.SValue]:
    out = []
    for child in element if element is not None else ():
        found = COLUMN.match(child.tag)
        if found:
            out.append(props1701.SValue(int(found.group(1)), 1, child.text or ""))
    return out


def object_of(guid: int, asset: ET.Element, types: dict[int, int]) -> props1701.SPropObject:
    """The object an asset of the editor stands for: its name, its own values, its property blocks."""
    values = asset.find("Values")
    prop_object = props1701.SPropObject(guid, (values.findtext(f"{STANDARD}/Name") or "") if values is not None else "")
    for child in values if values is not None else ():
        if child.tag == OBJECT or BLOCK.match(child.tag):
            row = [props1701.SValue(value.id, types.get(value.id, 1), value.text) for value in _values_of(child)]
            if child.tag == OBJECT:
                prop_object.values = row
            else:
                prop_object.blocks.append(props1701.SBlock(int(child.tag[1:]), row))
    return prop_object


def _cells(sources: CSources, asset: ET.Element) -> list[str] | None:
    block = asset.find(f"Values/{BALANCING}")
    if block is None or sources.m_table is None:
        return None
    given = {child.tag: child.text or "" for child in block}
    return [given.get(column, "") for column in sources.m_columns]


def render_files(doc: CAssetDocument) -> dict[str, bytes]:
    """``property.ini``, the balancing table and ``gamevalues.xml`` as far as the change set changes them (whole, the
    rest of each as the game has it); the texts are ``native.render_texts``."""
    sources = CSources(_CPlain(doc.m_files))
    changes = doc.m_changes
    objects = {prop_object.guid: prop_object for prop_object in sources.m_props.objects}
    out: dict[str, bytes] = {}
    for guid in [touched_guid for touched_guid in changes.touched() if touched_guid != VALUES_GUID]:
        _put(doc, sources, objects, guid)
    if changes.touched() and any(touched_guid != VALUES_GUID for touched_guid in changes.touched()):
        sources.m_props.objects = [objects[touched_guid] for touched_guid in _order(sources, objects, doc)]
        out[PROPS] = props1701.render_props(sources.m_props)
        if sources.m_table is not None and _table_changed(sources, doc):
            out[TABLE] = props1701.render_table(sources.m_table)
    if VALUES_GUID in changes.touched() and sources.m_values:
        out[VALUES] = _write_settings(doc, sources)
    return out


def _put(doc: CAssetDocument, sources: CSources, objects: dict[int, props1701.SPropObject], guid: int) -> None:
    if guid in doc.m_changes.m_removed or not doc.exists(guid):
        objects.pop(guid, None)
        if sources.m_table is not None and guid in sources.m_table.rows:
            del sources.m_table.rows[guid]
            sources.m_table.order.remove(guid)
        return
    asset = doc.asset(guid)
    if (asset.findtext("Template") or "") != "Balancing only":
        objects[guid] = object_of(guid, asset, sources.m_types)
    cells = _cells(sources, asset)
    if sources.m_table is not None and cells is not None and (guid in sources.m_table.rows or any(cells)):
        if guid not in sources.m_table.rows:
            sources.m_table.order.append(guid)
        sources.m_table.rows[guid] = [str(guid), *cells]


def _order(sources: CSources, objects: dict[int, props1701.SPropObject], doc: CAssetDocument) -> list[int]:
    """The objects in file order, a new one after the object it was made beside (else at the end)."""
    order = [prop_object.guid for prop_object in sources.m_props.objects if prop_object.guid in objects]
    for guid in sorted(object_guid for object_guid in objects if object_guid not in order):
        anchor = doc.m_changes.m_new[guid].anchor if guid in doc.m_changes.m_new else None
        order.insert(order.index(anchor) + 1 if anchor in order else len(order), guid)
    return order


def _table_changed(sources: CSources, doc: CAssetDocument) -> bool:
    return any(
        sources.m_table.rows.get(row_guid) != _original_row(sources, doc, row_guid)
        for row_guid in {*sources.m_table.rows, *sources.m_table.raw}
    )


def _original_row(sources: CSources, doc: CAssetDocument, guid: int) -> list[str] | None:
    raw = sources.m_table.raw.get(guid)
    return (
        None if raw is None else (raw.split("\t") + [""] * len(sources.m_table.header))[: len(sources.m_table.header)]
    )


def _flat(element: ET.Element, path: tuple[str, ...], out: dict[tuple[str, ...], str]) -> None:
    """Path -> text of every leaf below ``el``."""
    for child in element:
        _flat(child, (*path, child.tag), out) if len(child) else out.__setitem__((*path, child.tag), child.text or "")


def _write_settings(doc: CAssetDocument, sources: CSources) -> bytes:
    """The settings the change set changed, set in the file's own text."""
    before = {
        value_id: unescape(value) for value_id, value in props1701.game_values(sources.m_values).items()
    }  # the file's text, as ET reads it
    root = doc.asset(VALUES_GUID).find("Values")
    after: dict[tuple[str, ...], str] = {}
    if root is not None:
        _flat(root, (), after)
    changed = {
        value_id: escape(value) for value_id, value in after.items() if value_id in before and before[value_id] != value
    }
    return props1701.set_game_values(sources.m_values, changed)
