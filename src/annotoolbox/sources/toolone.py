"""Parsers for the ToolOne XML exports (assets, templates, properties).

Both Anno 2070 and Anno 117 ship these files; the element shapes are the
same, only the vocabulary differs. Everything here is stdlib ElementTree.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .assetdb import Asset, AssetDatabase, PropertyDef, Template, ValueDef, deep_merge
from .overlay import SourceTree

ASSET_FILE_PATTERNS = (
    "data/config/game/assets.xml",
    "data/config/dlc_*/assets.xml",
    "addondata/config/balancing/*_assets.xml",
)
TEMPLATE_FILE = "data/config/game/templates.xml"
PROPERTY_FILE = "data/config/game/properties.xml"


class RepeatedList(list):
    """Marker list: only ever created from repeated sibling tags."""


def element_to_data(el: ET.Element) -> Any:
    """Turn an element into str (leaf) or dict; repeated child tags become lists."""
    children = list(el)
    if not children:
        return (el.text or "").strip()
    out: dict[str, Any] = {}
    for child in children:
        value = element_to_data(child)
        if child.tag in out:
            existing = out[child.tag]
            if isinstance(existing, RepeatedList):
                existing.append(value)
            else:
                out[child.tag] = RepeatedList([existing, value])
        else:
            out[child.tag] = value
    return out


def _parse_int(text: Any) -> int | None:
    if isinstance(text, str) and text.strip().lstrip("-").isdigit():
        return int(text)
    return None


def _group_name(group: ET.Element) -> str:
    return (group.findtext("Name") or "").strip()


def _walk_groups(group: ET.Element, path: tuple[str, ...]) -> Iterator[tuple[ET.Element, tuple[str, ...]]]:
    """(group element, group path) for a group and every nested group: assets and properties nest them under
    ``Groups``, the templates file directly."""
    name = _group_name(group)
    here = path + (name,) if name else path
    yield group, here
    for sub in group.findall("Group"):
        yield from _walk_groups(sub, here)
    groups = group.find("Groups")
    if groups is not None:
        for sub in groups.findall("Group"):
            yield from _walk_groups(sub, here)


def _top_groups(root: ET.Element) -> Iterator[ET.Element]:
    """The root's groups: under ``Groups`` (``AssetList``, ``Properties``) or direct (``Templates``); a root that
    is itself a ``Group`` (the add-on and DLC asset files) counts as one."""
    if root.tag == "Group":
        yield root
        return
    groups = root.find("Groups")
    if groups is not None:
        yield from groups.findall("Group")
    yield from root.findall("Group")


def iter_assets(root: ET.Element, source: str) -> Iterator[Asset]:
    """Every ``Asset`` of an asset file with its group path, in file order."""
    for top in _top_groups(root):
        for group, path in _walk_groups(top, ()):
            assets = group.find("Assets")
            if assets is not None:
                for asset_el in assets.findall("Asset"):
                    yield _asset_from_element(asset_el, path, source)
    if root.tag != "Group":  # an AssetList's group-less assets come after its groups
        assets = root.find("Assets")
        if assets is not None:
            for asset_el in assets.findall("Asset"):
                yield _asset_from_element(asset_el, (), source)


def _asset_from_element(el: ET.Element, group: tuple[str, ...], source: str) -> Asset:
    template = (el.findtext("Template") or "").strip()
    values_el = el.find("Values")
    values = element_to_data(values_el) if values_el is not None else {}
    if not isinstance(values, dict):
        values = {}
    standard = values.get("Standard")
    if not isinstance(standard, dict):
        standard = {}
    guid = _parse_int(standard.get("GUID"))
    name = standard.get("Name", "")
    if not isinstance(name, str):
        name = ""
    base_guid = _parse_int(el.findtext("BaseAssetGUID") or "")
    return Asset(
        guid=guid, name=name, template=template, group=group, values=values, source=source, base_guid=base_guid
    )


def resolve_inheritance(db: AssetDatabase) -> list[str]:
    """Fill in template and values for assets that derive from a BaseAssetGUID.

    Derived assets carry only their overrides (and usually no Template). The
    result keeps the raw overrides in ``own_values`` and the merged tree in
    ``values``. Returns human-readable problems (missing bases, cycles).
    """
    problems: list[str] = []
    resolved: set[int] = set()

    def resolve(asset: Asset, chain: tuple[int, ...]) -> None:
        if asset.base_guid is None or (asset.guid is not None and asset.guid in resolved):
            return
        if asset.guid is not None and asset.guid in chain:
            problems.append(f"inheritance cycle: {' -> '.join(map(str, chain + (asset.guid,)))}")
            return
        base = db.by_guid(asset.base_guid)
        if base is None:
            problems.append(f"asset {asset.guid} ({asset.name}): base {asset.base_guid} not found")
            return
        resolve(base, chain + ((asset.guid,) if asset.guid is not None else ()))
        if asset.own_values is None:
            asset.own_values = asset.values
        asset.values = deep_merge(base.values, asset.own_values)
        if not asset.template:
            asset.template = base.template
        if not asset.name:
            asset.name = base.name
        if asset.guid is not None:
            resolved.add(asset.guid)

    for asset in db.assets:
        resolve(asset, ())
    return problems


def apply_template_defaults(db: AssetDatabase) -> None:
    """Lay each asset's template property values underneath its own.

    ToolOne stores in an asset only what differs from its template (a missing ``MaxHitpoints`` is the
    template's 300), so the template's values are the asset's defaults; the asset's win.
    """
    for asset in db.assets:
        template = db.templates.get(asset.template)
        if template is not None and template.properties:
            asset.values = deep_merge(copy.deepcopy(template.properties), asset.values)  # no shared subtrees


def load_assets_file(path: Path, source: str | None = None) -> list[Asset]:
    root = ET.parse(path).getroot()
    return list(iter_assets(root, source or str(path)))


def iter_templates(root: ET.Element, source: str) -> Iterator[Template]:
    for top in _top_groups(root):
        for group, path in _walk_groups(top, ()):
            for t in group.findall("Template"):
                tname = (t.findtext("Name") or "").strip()
                props_el = t.find("Properties")
                props = element_to_data(props_el) if props_el is not None else {}
                yield Template(tname, path, props if isinstance(props, dict) else {}, source)


def iter_properties(root: ET.Element) -> Iterator[PropertyDef]:
    for top in _top_groups(root):
        for group, path in _walk_groups(top, ()):
            for p in group.findall("Property"):
                pname = (p.findtext("Name") or "").strip()
                values: list[ValueDef] = []
                extras: dict[str, Any] = {}
                for child in p:
                    if child.tag == "ValueDefinition":
                        vd = element_to_data(child)
                        if isinstance(vd, dict):
                            values.append(
                                ValueDef(
                                    name=str(vd.get("Name", "")),
                                    data_type=str(vd.get("DataType", "")),
                                    extras={k: v for k, v in vd.items() if k not in ("Name", "DataType")},
                                )
                            )
                    elif child.tag != "Name":
                        extras[child.tag] = element_to_data(child)
                yield PropertyDef(pname, path, values, extras)


def last_definitions(assets: list) -> list:
    """One asset per GUID, the later file's definition at the first one's place: the add-on redefines four base assets
    (the two shipyards, the submarine base, the geyser), and the game reads the file loaded last, as ``by_guid`` does;
    a list over ``assets`` must not see the stale one as a second asset."""
    last = {a.guid: a for a in assets if a.guid is not None}
    seen: set[int] = set()
    out = []
    for asset in assets:
        if asset.guid is None:
            out.append(asset)
        elif asset.guid not in seen:
            seen.add(asset.guid)
            out.append(last[asset.guid])
    return out


def load_database(tree: SourceTree) -> AssetDatabase:
    """Load every asset list, the templates and the property schema from a tree."""
    db = AssetDatabase()
    seen: set[str] = set()
    for pattern in ASSET_FILE_PATTERNS:
        for rel in tree.glob(pattern):
            if rel in seen:
                continue
            seen.add(rel)
            physical = tree.resolve(rel)
            assert physical is not None
            db.assets.extend(load_assets_file(physical, source=rel))
            db.sources.append(rel)
    db.assets = last_definitions(db.assets)

    tpath = tree.resolve(TEMPLATE_FILE)
    if tpath is not None:
        for t in iter_templates(ET.parse(tpath).getroot(), TEMPLATE_FILE):
            db.templates[t.name] = t
    ppath = tree.resolve(PROPERTY_FILE)
    if ppath is not None:
        for p in iter_properties(ET.parse(ppath).getroot()):
            db.properties[p.name] = p
    db.reindex()
    db.problems.extend(resolve_inheritance(db))
    apply_template_defaults(db)
    return db
