"""Typed model of the ToolOne asset database used by Anno 2070."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


def as_list(value: Any) -> list:
    """ToolOne XML collapses single-item lists; normalise to a list."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def entries(node: Any) -> list[dict]:
    """The ``Item`` entries of a ToolOne list (``<X><Item>..</Item><Item>..</Item></X>``), each a dict; a
    single entry collapses to a dict in the data and an empty list to ``""``."""
    if not isinstance(node, dict):
        return []
    return [item for item in as_list(node.get("Item")) if isinstance(item, dict)]


def entry_values(node: Any, key: str) -> list:
    """The ``key`` field of every entry of a ToolOne list that sets it."""
    return [item.get(key) for item in entries(node) if item.get(key) not in (None, "")]


def optional_int(value: Any, default: int | None = None) -> int | None:
    """The whole number a value holds (a GUID, a text), ``default`` when it holds none: an absent value stays absent."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def number(value: Any, default: int = 0) -> int:
    """The whole number a ToolOne value holds (``"4096"``, ``"1.0"``); ``default`` when it is absent, empty or
    no number."""
    try:
        return int(float(value)) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def goods_tons(kilos: int) -> int:
    """A goods amount 2070 stores in kilos (1000 a ton) as 117's whole tons, rounded up: the player never pays less
    than 2070 asks and a cost never vanishes (2070 charges the exact kilos; every installed building and unit cost is
    a whole number of tons already). Python's ``round`` would send halves to the even ton and a few kilos to 0."""
    return max(0, -(-kilos // 1000))


def decimal(value: Any, default: float = 0.0) -> float:
    """The number a ToolOne value holds (``"0.5"``); ``default`` when it is absent, empty or no number."""
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def dig(data: Any, path: str) -> Any:
    """Follow a slash-separated path through nested dicts; None if absent."""
    current = data
    for part in path.split("/"):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def deep_merge(base: Any, override: Any) -> Any:
    """Merge ToolOne value trees: ``override`` wins, recursing into dicts.

    An empty element in the override (parsed as ``""``) means "component
    present, nothing changed", so it must not wipe a populated base value.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            out[key] = deep_merge(base.get(key), value) if key in base else value
        return out
    if override == "" and base not in (None, ""):
        return base
    return override


@dataclass
class SAsset:
    guid: int | None
    name: str
    template: str
    group: tuple[str, ...]
    values: dict[str, Any]
    source: str
    base_asset: int | None = None
    own_values: dict[str, Any] | None = None

    @property
    def group_path(self) -> str:
        return "/".join(self.group)

    @property
    def inherits(self) -> bool:
        return self.base_asset is not None

    def component(self, name: str) -> Any:
        return self.values.get(name)

    def has_component(self, name: str) -> bool:
        return name in self.values

    def get(self, path: str) -> Any:
        return dig(self.values, path)

    def number(self, path: str, default: int = 0) -> int:
        """The whole number at ``path``; ``default`` when it is absent, empty or no number."""
        return number(self.get(path), default)

    def variation_files(self) -> list[str]:
        """Model config files referenced by Object/Variations/Item/Filename."""
        out = []
        for item in as_list(self.get("Object/Variations/Item")):
            if isinstance(item, dict):
                file_name = item.get("Filename")
                if file_name:
                    out.append(file_name)
            elif isinstance(item, str) and item:
                out.append(item)
        return out


@dataclass
class STemplate:
    name: str
    group: tuple[str, ...]
    properties: dict[str, Any]
    source: str


@dataclass
class SValueDef:
    name: str
    data_type: str
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class SPropertyDef:
    """A component ("Property" in ToolOne terms) and its value schema."""

    name: str
    group: tuple[str, ...]
    values: list[SValueDef]
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class CAssetDatabase:
    m_assets: list[SAsset] = field(default_factory=list)
    m_templates: dict[str, STemplate] = field(default_factory=dict)
    m_properties: dict[str, SPropertyDef] = field(default_factory=dict)
    m_sources: list[str] = field(default_factory=list)
    m_problems: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._m_by_guid: dict[int, SAsset] = {}
        self.reindex()

    def reindex(self) -> None:
        self._m_by_guid = {asset.guid: asset for asset in self.m_assets if asset.guid is not None}

    def by_guid(self, guid: int) -> SAsset | None:
        return self._m_by_guid.get(guid)

    def by_template(self, template: str) -> Iterator[SAsset]:
        return (asset for asset in self.m_assets if asset.template == template)

    def template_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for asset in self.m_assets:
            counts[asset.template] = counts.get(asset.template, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
