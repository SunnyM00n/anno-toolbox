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
class Item:
    name: str
    id: int  # the data id in datasets.xml
    lua: int | None = None  # the index in the Lua stub
    description: str = ""
    hidden_in: str = ""  # SkipXmlExportInVersion: the game versions that do not export the item


@dataclass
class Dataset:
    group: str
    name: str
    id: int
    description: str = ""
    items: list[Item] = field(default_factory=list)


@dataclass
class LuaEnum:
    space: str  # ``datasets`` or ``halo``
    name: str
    values: list[tuple[str, int]]


def _int(text: str | None) -> int:
    return int(text.strip()) if text and text.strip().lstrip("-").isdigit() else 0


def read_datasets(text: str) -> list[Dataset]:
    """Every ``DataSet`` of a ``datasets.xml`` in file order, with its group (a group name may repeat)."""
    root = ET.fromstring(text)
    out = []
    for group in root.iter("Group"):
        gname = group.findtext("Name", "")
        for ds in group.findall("DataSet"):
            items = [
                Item(
                    it.findtext("Name", ""),
                    _int(it.findtext("Id")),
                    description=(it.findtext("Description") or "").strip(),
                    hidden_in=(it.findtext("SkipXmlExportInVersion") or "").strip(),
                )
                for it in ds.findall("Items/Item")
            ]
            out.append(
                Dataset(
                    gname,
                    ds.findtext("Name", ""),
                    _int(ds.findtext("Id")),
                    (ds.findtext("Description") or "").strip(),
                    items,
                )
            )
    return out


def read_lua_enum(text: str) -> LuaEnum | None:
    """The enum table of one generated Lua stub (``datasets.X = {...}`` or ``halo.X = {...}``); ``Invalid`` and
    ``Count`` kept as they stand."""
    head = _ENUM_HEAD.search(text)
    if head is None:
        return None
    body = text[head.end() : text.find("}", head.end())]
    values = [(k, int(v, 0)) for k, v in _ENUM_ITEM.findall(body)]
    return LuaEnum(head.group(1), head.group(2), values)


def attach_lua(datasets: list[Dataset], stubs: list[LuaEnum]) -> list[LuaEnum]:
    """Each item's Lua index from the dataset's stub (matched by name); returns the stubs with no dataset."""
    by_name = {s.name: s for s in stubs}
    for ds in datasets:
        stub = by_name.pop(ds.name, None)
        if stub is None:
            continue
        index = dict(stub.values)
        for item in ds.items:
            item.lua = index.get(item.name)
    return sorted(by_name.values(), key=lambda s: s.name.lower())


def collect(files) -> tuple[list[Dataset], list[LuaEnum], list[LuaEnum]]:
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
    return datasets, lua_only, sorted(engine, key=lambda e: e.name.lower())


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _dataset_md(ds: Dataset) -> list[str]:
    fixed = " **Fixed by the engine: cannot grow by data.**" if ds.name in FIXED else ""
    lines = [f"### {ds.name}", "", f"Group {ds.group}, dataset id {ds.id}.{fixed}"]
    if ds.description:
        lines += ["", _cell(ds.description)]
    lines += ["", "| Item | Data id | Lua | Note |", "| --- | ---: | ---: | --- |"]
    for it in ds.items:
        note = "; ".join(
            x for x in (_cell(it.description), f"not exported in {it.hidden_in}" if it.hidden_in else "") if x
        )
        lua = "" if it.lua is None else str(it.lua)
        lines.append(f"| `{it.name}` | {it.id} | {lua} | {note} |")
    return lines + [""]


def _enum_md(enum: LuaEnum, hexed: bool) -> list[str]:
    rows = [f"| `{k}` | {f'0x{v & 0xFFFFFFFF:08X}' if hexed else v} |" for k, v in enum.values]
    return [f"### {enum.space}.{enum.name}", "", "| Value | Number |", "| --- | ---: |", *rows, ""]


def render_markdown(datasets: list[Dataset], lua_only: list[LuaEnum], engine: list[LuaEnum], build: str) -> str:
    """The whole reference as Markdown: datasets by group, Lua-only datasets, engine enums."""
    groups: dict[str, list[Dataset]] = {}
    for ds in datasets:
        groups.setdefault(ds.group, []).append(ds)
    items = sum(len(ds.items) for ds in datasets)
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
        f"- {g}: " + ", ".join(ds.name for ds in sorted(dss, key=lambda d: d.name.lower())) for g, dss in groups.items()
    ]
    for g, dss in groups.items():
        lines += ["", f"## {g}", ""]
        for ds in sorted(dss, key=lambda d: d.name.lower()):
            lines += _dataset_md(ds)
    lines += ["## Only in the Lua stubs", ""]
    for enum in lua_only:
        lines += _enum_md(enum, hexed=False)
    lines += ["## Engine enums", ""]
    for enum in engine:
        lines += _enum_md(enum, hexed=True)
    return "\n".join(lines).rstrip() + "\n"


def datasets_from_json(doc: dict) -> list[Dataset]:
    """The datasets of a ``to_json`` document (``docs/formats/enums-117.json``), for code that needs the ids offline."""
    return [
        Dataset(
            d["group"],
            d["name"],
            d["id"],
            d.get("description", ""),
            [
                Item(i["name"], i["id"], i.get("lua"), i.get("description", ""), i.get("hidden_in", ""))
                for i in d["items"]
            ],
        )
        for d in doc["datasets"]
    ]


def to_json(datasets: list[Dataset], lua_only: list[LuaEnum], engine: list[LuaEnum], build: str) -> dict:
    """The same reference as plain data."""

    def enum(e: LuaEnum) -> dict:
        return {"name": e.name, "space": e.space, "values": dict(e.values)}

    return {
        "build": build,
        "datasets": [
            {
                "group": ds.group,
                "name": ds.name,
                "id": ds.id,
                "fixed": ds.name in FIXED,
                "description": ds.description,
                "items": [
                    {
                        "name": it.name,
                        "id": it.id,
                        "lua": it.lua,
                        "description": it.description,
                        "hidden_in": it.hidden_in,
                    }
                    for it in ds.items
                ],
            }
            for ds in datasets
        ],
        "lua_only": [enum(e) for e in lua_only],
        "engine": [enum(e) for e in engine],
    }
