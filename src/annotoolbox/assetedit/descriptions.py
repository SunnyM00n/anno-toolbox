"""The editor's own descriptions of a game's properties and values, kept as data beside the code
(``data/descriptions-<game>.json``): written by hand from what the properties do, for those that the game's schema
files and the donor game's descriptions leave empty. Nothing in them is game text. Stdlib only.

``load(key)`` reads the table of a game (empty where there is none), ``apply`` fills the schema's empty descriptions
from it and marks them ``written`` (the form says so in the tooltip), ``unresolved`` lists keys that name nothing in
a schema (a test keeps the files honest against the real schema files).
"""

from __future__ import annotations

import json
from pathlib import Path

from .schema import SPropertySpec, SSchema, SValueSpec

DATA = Path(__file__).parent / "data"


def load(key: str, folder: Path = DATA) -> dict[str, str]:
    """The descriptions of game ``key`` by property (``Building``) or value path (``Building/Costs/Amount``)."""
    try:
        data = json.loads((folder / f"descriptions-{key}.json").read_text(encoding="utf-8"))
        return {
            k: description
            for k, description in data.get("descriptions", {}).items()
            if isinstance(k, str) and isinstance(description, str) and description
        }
    except (OSError, ValueError, AttributeError):
        return {}


def _spec(schema: SSchema, key: str) -> SPropertySpec | SValueSpec | None:
    property_name, _, rest = key.partition("/")
    node = schema.properties.get(property_name)
    for name in rest.split("/") if rest else ():
        node = node.child(name) if node is not None else None
    return node


def apply(schema: SSchema, table: dict[str, str]) -> int:
    """Fill the empty descriptions of ``schema`` from ``table``; returns how many were filled."""
    gained = 0
    for key, text in table.items():
        node = _spec(schema, key)
        if node is not None and not node.description:
            node.description = text
            node.written = True
            gained += 1
    return gained


def unresolved(schema: SSchema, table: dict[str, str]) -> list[str]:
    """The keys of ``table`` that name no property or value of ``schema``."""
    return [key for key in table if _spec(schema, key) is None]
