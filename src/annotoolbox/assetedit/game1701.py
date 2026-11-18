"""Anno 1701's data as the asset editor's assets: ``property.ini`` (an asset per object), ``balancingtabelle.txt`` (its
columns as a ``Balancing`` block of the asset with the same GUID) and ``gamevalues.xml`` (one asset, GUID -1), shown
through generated files that look like an asset file and its schema, and written back as the game's own files.

``Props1701Files`` is the game's ``GameFiles`` with those generated files added (``assetgame.open_game`` uses it for the
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
from ..sources.gametree import GameTree, game_stamp, open_game
from ..sources.overlay import normalize
from .assetdoc import AssetDocument
from .gamefiles import GENERATED_1701, GameFiles

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


class Sources:
    """What the generated files are made from, read once: the objects, the table, the settings (as text)."""

    def __init__(self, files: GameFiles) -> None:
        self.props = props1701.parse_props(files.read(PROPS))
        self.table = props1701.parse_table(files.read(TABLE)) if files.exists(TABLE) else None
        self.values = files.read(VALUES) if files.exists(VALUES) else b""
        self.columns = column_names(self.table.header) if self.table else []
        self.types = self.props.value_types()

    def template_of(self, obj: props1701.PropObject) -> str:
        return " ".join([OBJECT, *(f"P{b.prop}" for b in obj.blocks)])


def _text(parent: ET.Element, tag: str, value: str = "") -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = value
    return el


def _asset(group: ET.Element, template: str, guid: int, name: str) -> tuple[ET.Element, ET.Element]:
    asset = ET.SubElement(group, "Asset")
    _text(asset, "Template", template)
    values = ET.SubElement(asset, "Values")
    standard = ET.SubElement(values, STANDARD)
    _text(standard, "GUID", str(guid))
    _text(standard, "Name", name)
    return asset, values


def assets_xml(src: Sources) -> bytes:
    """The asset file: one asset per object (grouped by template), per table row without an object, and the settings."""
    top = ET.Element("AssetList")
    root = ET.SubElement(top, "Groups")
    made: dict[str, ET.Element] = {}

    def group(name: str) -> ET.Element:
        if name not in made:
            g = ET.SubElement(root, "Group")
            _text(g, "Name", name)
            made[name] = ET.SubElement(g, "Assets")
        return made[name]

    table = src.table
    for obj in src.props.objects:
        _asset_of_object(group(src.template_of(obj)), src, obj)
    known = {o.guid for o in src.props.objects}
    for guid in table.order if table else ():
        if guid not in known:
            asset, values = _asset(group("Balancing only"), "Balancing only", guid, "")
            _balancing(values, src, guid)
    if src.values:
        asset, values = _asset(group("GameValues"), "GameValues", VALUES_GUID, "gamevalues.xml")
        for child in _settings(src.values):
            values.append(child)
    return ET.tostring(top, encoding="unicode").encode("utf-8")


def _asset_of_object(assets: ET.Element, src: Sources, obj: props1701.PropObject) -> None:
    asset, values = _asset(assets, src.template_of(obj), obj.guid, obj.name)
    _text(values.find(STANDARD), "Text", str(obj.guid))  # the object's name in the game's texts is the text of its GUID
    block = ET.SubElement(values, OBJECT)
    for v in obj.values:
        _text(block, f"V{v.id}", v.text)
    for b in obj.blocks:
        el = ET.SubElement(values, f"P{b.prop}")
        for v in b.values:
            _text(el, f"V{v.id}", v.text)
    _balancing(values, src, obj.guid)


def _balancing(values: ET.Element, src: Sources, guid: int) -> None:
    if src.table is None or guid not in src.table.rows:
        return
    block = ET.SubElement(values, BALANCING)
    for name, cell in zip(src.columns, src.table.rows[guid][1:], strict=True):
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


def _struct(el: ET.Element) -> str:
    if len(el):
        return _definition(el.tag, "Struct", items=[_struct(c) for c in el])
    return _definition(el.tag, _kind_of(el.text or ""))


def _property(name: str, definitions: list[str]) -> str:
    return f"<Property><Name>{name}</Name>{''.join(definitions)}</Property>"


def schema_xml(src: Sources) -> tuple[bytes, bytes, bytes]:
    """The property definitions, the templates and the (empty) data sets of the generated asset file."""
    types = src.types
    by_prop: dict[int, set[int]] = {}
    for obj in src.props.objects:
        for b in obj.blocks:
            by_prop.setdefault(b.prop, set()).update(v.id for v in b.values)
    own = sorted({v.id for o in src.props.objects for v in o.values})

    def numbered(ids) -> list[str]:
        return [_definition(f"V{i}", "Integer" if types.get(i) == 6 else "String") for i in sorted(ids)]

    props = [
        _property(
            STANDARD, [_definition("GUID", "Integer"), _definition("Name", "String"), _definition("Text", "Text")]
        ),
        _property(OBJECT, numbered(own)),
        *(_property(f"P{p}", numbered(ids)) for p, ids in sorted(by_prop.items())),
    ]
    if src.table:
        columns = [_definition(n, "String", h) for n, h in zip(src.columns, src.table.header[1:], strict=True)]
        props.append(_property(BALANCING, columns))
    sections = _settings(src.values) if src.values else []
    props += [_property(s.tag, [_struct(c) for c in s] if len(s) else [_definition(s.tag, "String")]) for s in sections]
    templates = {src.template_of(o): [STANDARD, OBJECT, *(f"P{b.prop}" for b in o.blocks)] for o in src.props.objects}
    templates["Balancing only"] = [STANDARD, BALANCING]
    templates["GameValues"] = [STANDARD, *(s.tag for s in sections)]
    listed = "".join(
        f"<Template><Name>{name}</Name><Properties>{''.join(f'<{p} />' for p in names)}</Properties></Template>"
        for name, names in templates.items()
    )
    meta = f"<Properties><Groups><Group><Name />{''.join(props)}</Group></Groups></Properties>"
    return (
        meta.encode(),
        f"<Templates><Group><Name>Objects</Name>{listed}</Group></Templates>".encode(),
        b"<DataSets />",
    )


# The files


class Props1701Files(GameFiles):
    """The game's files with the generated asset file, schema, templates and data sets laid over them."""

    def __init__(self, opener: Callable[[], GameTree], stamp: str, cache: Path) -> None:
        super().__init__(opener, f"{stamp}|1701-{VERSION}", cache)
        self._generated: dict[str, bytes] | None = None
        self._sources: Sources | None = None

    @property
    def sources(self) -> Sources:
        if self._sources is None:
            self._sources = Sources(_Plain(self))
        return self._sources

    def generated(self) -> dict[str, bytes]:
        if self._generated is None:
            src = self.sources
            meta, templates, datasets = schema_xml(src)
            self._generated = {ASSETS: assets_xml(src), META: meta, TEMPLATES: templates, DATASETS: datasets}
        return self._generated

    @property
    def paths(self) -> list[str]:
        return sorted({*self.tree.paths, *self.generated_names()})

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
        dest = (
            self.cache / "files" / f"{hashlib.sha1(f'{self.stamp}|{key}'.encode()).hexdigest()[:16]}-{Path(key).name}"
        )
        if not dest.is_file():
            write_atomic(dest, self.read(key), sync=False)
        return dest


class _Plain:
    """The game's own files of a ``Props1701Files`` (never the generated ones): what ``Sources`` reads."""

    def __init__(self, files: Props1701Files) -> None:
        self._files = files

    def read(self, path: str) -> bytes:
        return self._files.tree.read(path)

    def exists(self, path: str) -> bool:
        return self._files.tree.exists(path)


def open_files(folder: Path, cache: Path) -> Props1701Files:
    """The installed Anno 1701 at ``folder`` with the generated files."""
    folder = Path(folder)
    return Props1701Files(lambda: open_game("anno1701", folder, cache), game_stamp("anno1701", folder), cache)


# Writing back


def is_1701(doc: AssetDocument) -> bool:
    return isinstance(doc.files, Props1701Files)


def _values_of(element: ET.Element | None) -> list[props1701.Value]:
    out = []
    for child in element if element is not None else ():
        found = COLUMN.match(child.tag)
        if found:
            out.append(props1701.Value(int(found.group(1)), 1, child.text or ""))
    return out


def object_of(guid: int, asset: ET.Element, types: dict[int, int]) -> props1701.PropObject:
    """The object an asset of the editor stands for: its name, its own values, its property blocks."""
    values = asset.find("Values")
    obj = props1701.PropObject(guid, (values.findtext(f"{STANDARD}/Name") or "") if values is not None else "")
    for child in values if values is not None else ():
        if child.tag == OBJECT or BLOCK.match(child.tag):
            row = [props1701.Value(v.id, types.get(v.id, 1), v.text) for v in _values_of(child)]
            if child.tag == OBJECT:
                obj.values = row
            else:
                obj.blocks.append(props1701.Block(int(child.tag[1:]), row))
    return obj


def _cells(src: Sources, asset: ET.Element) -> list[str] | None:
    block = asset.find(f"Values/{BALANCING}")
    if block is None or src.table is None:
        return None
    given = {c.tag: c.text or "" for c in block}
    return [given.get(n, "") for n in src.columns]


def render_files(doc: AssetDocument) -> dict[str, bytes]:
    """``property.ini``, the balancing table and ``gamevalues.xml`` as far as the change set changes them (whole, the
    rest of each as the game has it); the texts are ``native.render_texts``."""
    src = Sources(_Plain(doc.files))
    changes = doc.changes
    objects = {o.guid: o for o in src.props.objects}
    out: dict[str, bytes] = {}
    for guid in [g for g in changes.touched() if g != VALUES_GUID]:
        _put(doc, src, objects, guid)
    if changes.touched() and any(g != VALUES_GUID for g in changes.touched()):
        src.props.objects = [objects[g] for g in _order(src, objects, doc)]
        out[PROPS] = props1701.render_props(src.props)
        if src.table is not None and _table_changed(src, doc):
            out[TABLE] = props1701.render_table(src.table)
    if VALUES_GUID in changes.touched() and src.values:
        out[VALUES] = _write_settings(doc, src)
    return out


def _put(doc: AssetDocument, src: Sources, objects: dict[int, props1701.PropObject], guid: int) -> None:
    if guid in doc.changes.removed or not doc.exists(guid):
        objects.pop(guid, None)
        if src.table is not None and guid in src.table.rows:
            del src.table.rows[guid]
            src.table.order.remove(guid)
        return
    asset = doc.asset(guid)
    if (asset.findtext("Template") or "") != "Balancing only":
        objects[guid] = object_of(guid, asset, src.types)
    cells = _cells(src, asset)
    if src.table is not None and cells is not None and (guid in src.table.rows or any(cells)):
        if guid not in src.table.rows:
            src.table.order.append(guid)
        src.table.rows[guid] = [str(guid), *cells]


def _order(src: Sources, objects: dict[int, props1701.PropObject], doc: AssetDocument) -> list[int]:
    """The objects in file order, a new one after the object it was made beside (else at the end)."""
    order = [o.guid for o in src.props.objects if o.guid in objects]
    for guid in sorted(g for g in objects if g not in order):
        anchor = doc.changes.new[guid].anchor if guid in doc.changes.new else None
        order.insert(order.index(anchor) + 1 if anchor in order else len(order), guid)
    return order


def _table_changed(src: Sources, doc: AssetDocument) -> bool:
    return any(src.table.rows.get(g) != _original_row(src, doc, g) for g in {*src.table.rows, *src.table.raw})


def _original_row(src: Sources, doc: AssetDocument, guid: int) -> list[str] | None:
    raw = src.table.raw.get(guid)
    return None if raw is None else (raw.split("\t") + [""] * len(src.table.header))[: len(src.table.header)]


def _flat(el: ET.Element, path: tuple[str, ...], out: dict[tuple[str, ...], str]) -> None:
    """Path -> text of every leaf below ``el``."""
    for child in el:
        _flat(child, (*path, child.tag), out) if len(child) else out.__setitem__((*path, child.tag), child.text or "")


def _write_settings(doc: AssetDocument, src: Sources) -> bytes:
    """The settings the change set changed, set in the file's own text."""
    before = {p: unescape(v) for p, v in props1701.game_values(src.values).items()}  # the file's text, as ET reads it
    root = doc.asset(VALUES_GUID).find("Values")
    after: dict[tuple[str, ...], str] = {}
    if root is not None:
        _flat(root, (), after)
    changed = {p: escape(v) for p, v in after.items() if p in before and before[p] != v}
    return props1701.set_game_values(src.values, changed)
