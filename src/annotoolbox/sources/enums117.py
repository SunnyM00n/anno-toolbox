"""Anno 117's enums as the game ships them: the data enums of ``datasets.xml`` (each item's data id), their Lua
stubs (each item's Lua index, which differs from the data id), and the engine's own enums exposed to Lua.

``read_datasets`` and ``read_lua_enum`` parse the files; ``collect`` reads all three from an install;
``render_markdown`` and ``to_json`` write the reference (``scripts/enums117.py``, ``docs/formats/enums-117.md``).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

DATASETS_XML = "data/base/config/game/datasets.xml"
LUA_DATASETS = "data/script/types/generated/datasets/"
LUA_ENGINE = "data/script/types/generated/halo/enums/"
# Enums the engine sizes and branches on: new values by data are not possible (engine-notes "Engine enums cannot grow")
FIXED = frozenset({"Region", "CultureType", "NeedAttributeType"})

_ENUM_HEAD = re.compile(r"^(\w+)\.(\w+)\s*=\s*\{", re.M)
_ENUM_ITEM = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(-?0x[0-9A-Fa-f]+|-?\d+)\s*,?\s*$", re.M)


@dataclass
class SItem:
    name: str
    id: int  # the data id in datasets.xml
    lua: int | None = None  # the index in the Lua stub
    description: str = ""
    hidden_in: str = ""  # SkipXmlExportInVersion: the game versions that do not export the item


@dataclass
class SDataset:
    group: str
    name: str
    id: int
    description: str = ""
    items: list[SItem] = field(default_factory=list)


@dataclass
class SLuaEnum:
    space: str  # ``datasets`` or ``halo``
    name: str
    values: list[tuple[str, int]]


def _int(text: str | None) -> int:
    return int(text.strip()) if text and text.strip().lstrip("-").isdigit() else 0


def read_datasets(text: str) -> list[SDataset]:
    """Every ``DataSet`` of a ``datasets.xml`` in file order, with its group (a group name may repeat)."""
    root = ET.fromstring(text)
    out = []
    for group in root.iter("Group"):
        gname = group.findtext("Name", "")
        for dataset in group.findall("DataSet"):
            items = [
                SItem(
                    item.findtext("Name", ""),
                    _int(item.findtext("Id")),
                    description=(item.findtext("Description") or "").strip(),
                    hidden_in=(item.findtext("SkipXmlExportInVersion") or "").strip(),
                )
                for item in dataset.findall("Items/Item")
            ]
            out.append(
                SDataset(
                    gname,
                    dataset.findtext("Name", ""),
                    _int(dataset.findtext("Id")),
                    (dataset.findtext("Description") or "").strip(),
                    items,
                )
            )
    return out


def read_lua_enum(text: str) -> SLuaEnum | None:
    """The enum table of one generated Lua stub (``datasets.X = {...}`` or ``halo.X = {...}``); ``Invalid`` and
    ``Count`` kept as they stand."""
    head = _ENUM_HEAD.search(text)
    if head is None:
        return None
    body = text[head.end() : text.find("}", head.end())]
    values = [(k, int(value, 0)) for k, value in _ENUM_ITEM.findall(body)]
    return SLuaEnum(head.group(1), head.group(2), values)


def attach_lua(datasets: list[SDataset], stubs: list[SLuaEnum]) -> list[SLuaEnum]:
    """Each item's Lua index from the dataset's stub (matched by name); returns the stubs with no dataset."""
    by_name = {lua_enum.name: lua_enum for lua_enum in stubs}
    for dataset in datasets:
        stub = by_name.pop(dataset.name, None)
        if stub is None:
            continue
        index = dict(stub.values)
        for item in dataset.items:
            item.lua = index.get(item.name)
    return sorted(by_name.values(), key=lambda lua_enum: lua_enum.name.lower())


def collect(files) -> tuple[list[SDataset], list[SLuaEnum], list[SLuaEnum]]:
    """(datasets with their Lua indexes, Lua-only dataset stubs, engine enums) of an install (``Anno117Files``)."""
    datasets = read_datasets(files.read(DATASETS_XML).decode("utf-8", errors="replace"))
    stubs, engine = [], []
    for prefix, out in ((LUA_DATASETS, stubs), (LUA_ENGINE, engine)):
        for path in files.iter_paths(prefix):
            if path.endswith(".lua"):
                enum = read_lua_enum(files.read(path).decode("utf-8", errors="replace"))
                if enum is not None:
                    out.append(enum)
    lua_only = attach_lua(datasets, stubs)
    return datasets, lua_only, sorted(engine, key=lambda entry: entry.name.lower())


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _dataset_md(dataset: SDataset) -> list[str]:
    fixed = " **Fixed by the engine: cannot grow by data.**" if dataset.name in FIXED else ""
    lines = [f"### {dataset.name}", "", f"Group {dataset.group}, dataset id {dataset.id}.{fixed}"]
    if dataset.description:
        lines += ["", _cell(dataset.description)]
    lines += ["", "| Item | Data id | Lua | Note |", "| --- | ---: | ---: | --- |"]
    for item in dataset.items:
        note = "; ".join(
            x for x in (_cell(item.description), f"not exported in {item.hidden_in}" if item.hidden_in else "") if x
        )
        lua = "" if item.lua is None else str(item.lua)
        lines.append(f"| `{item.name}` | {item.id} | {lua} | {note} |")
    return lines + [""]


def _enum_md(enum: SLuaEnum, hexed: bool) -> list[str]:
    rows = [f"| `{k}` | {f'0x{value & 0xFFFFFFFF:08X}' if hexed else value} |" for k, value in enum.values]
    return [f"### {enum.space}.{enum.name}", "", "| Value | Number |", "| --- | ---: |", *rows, ""]


def render_markdown(datasets: list[SDataset], lua_only: list[SLuaEnum], engine: list[SLuaEnum], build: str) -> str:
    """The whole reference as Markdown: datasets by group, Lua-only datasets, engine enums."""
    groups: dict[str, list[SDataset]] = {}
    for dataset in datasets:
        groups.setdefault(dataset.group, []).append(dataset)
    items = sum(len(dataset.items) for dataset in datasets)
    lines = [
        "# Anno 117's enums",
        "",
        f"Generated by `scripts/enums117.py` from the installed game ({build}); do not edit, regenerate. "
        f"{len(datasets)} data enums with {items} items in {len(groups)} groups (`{DATASETS_XML}`), "
        f"{len(lua_only)} enums only in the Lua stubs, {len(engine)} engine enums exposed to Lua.",
        "",
        "- **Data id**: the item's `Id` in `datasets.xml`. Asset files name an item by its name; the id is what the "
        "data stores.",
        "- **Lua**: the item's value in the generated Lua stub (`datasets.<Name>.<Item>`), an index that often differs "
        "from the data id (`Region`: `Roman` is data id 7, Lua 1). Empty: the stub lacks the item.",
        "- **Not exported in**: the game versions whose data leaves the item out (DLC groundwork and test values).",
        "- **Fixed by the engine**: enums the engine sizes its tables by and branches on; a mod cannot add values "
        '([`../engine-notes.md`](../engine-notes.md) "Engine enums cannot grow").',
        "- **Engine enums** (`halo.*`): C++ enums the game exports to Lua; their numbers are name hashes, not indexes.",
        "",
        "## Groups",
        "",
    ]
    lines += [
        f"- {group}: " + ", ".join(dataset.name for dataset in sorted(dss, key=lambda dataset: dataset.name.lower()))
        for group, dss in groups.items()
    ]
    for group, dss in groups.items():
        lines += ["", f"## {group}", ""]
        for dataset in sorted(dss, key=lambda dataset: dataset.name.lower()):
            lines += _dataset_md(dataset)
    lines += ["## Only in the Lua stubs", ""]
    for enum in lua_only:
        lines += _enum_md(enum, hexed=False)
    lines += ["## Engine enums", ""]
    for enum in engine:
        lines += _enum_md(enum, hexed=True)
    return "\n".join(lines).rstrip() + "\n"


def datasets_from_json(document: dict) -> list[SDataset]:
    """The datasets of a ``to_json`` document (``docs/formats/enums-117.json``), for code that needs the ids offline."""
    return [
        SDataset(
            item["group"],
            item["name"],
            item["id"],
            item.get("description", ""),
            [
                SItem(i["name"], i["id"], i.get("lua"), i.get("description", ""), i.get("hidden_in", ""))
                for i in item["items"]
            ],
        )
        for item in document["datasets"]
    ]


def to_json(datasets: list[SDataset], lua_only: list[SLuaEnum], engine: list[SLuaEnum], build: str) -> dict:
    """The same reference as plain data."""

    def enum(entry: SLuaEnum) -> dict:
        return {"name": entry.name, "space": entry.space, "values": dict(entry.values)}

    return {
        "build": build,
        "datasets": [
            {
                "group": dataset.group,
                "name": dataset.name,
                "id": dataset.id,
                "fixed": dataset.name in FIXED,
                "description": dataset.description,
                "items": [
                    {
                        "name": item.name,
                        "id": item.id,
                        "lua": item.lua,
                        "description": item.description,
                        "hidden_in": item.hidden_in,
                    }
                    for item in dataset.items
                ],
            }
            for dataset in datasets
        ],
        "lua_only": [enum(entry) for entry in lua_only],
        "engine": [enum(entry) for entry in engine],
    }
