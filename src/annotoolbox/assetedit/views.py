"""Saved balancing views: a name and the value paths of a SBalancingView's columns, kept per game in a small JSON file
(``<project>/asset-views/<game>.json``), so a table of "Building/Upkeep, Product/Price" comes back with one pick.
Stdlib only.

``CViewStore.load`` reads the file (a missing or damaged one is an empty store), ``put`` and ``remove`` change it,
``save`` writes it. ToolOne keeps its views as XML files of its own; those are not read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic

ASSET_VIEWS = "asset-views"  # the folder of a project (or workspace) that holds the files
FORMAT = 1


@dataclass
class SBalancingView:
    name: str
    columns: list[str]
    template: str = ""  # the template of the assets it was made for (a hint, not a filter)

    def label(self) -> str:
        return f"{self.name} ({self.template})" if self.template else self.name


@dataclass
class CViewStore:
    m_path: Path | None = None
    m_views: dict[str, SBalancingView] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> CViewStore:
        store = cls(path)
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            for row in (raw for raw in data.get("views", []) if isinstance(raw, dict)):
                columns = [str(column) for column in row.get("columns", []) if str(column).strip()]
                if str(row.get("name", "")).strip() and columns:
                    store.m_views[row["name"]] = SBalancingView(row["name"], columns, str(row.get("template", "")))
        except (OSError, ValueError, AttributeError, TypeError):
            store.m_views.clear()
        return store

    @property
    def names(self) -> list[str]:
        return sorted(self.m_views, key=str.lower)

    def put(self, name: str, columns: list[str], template: str = "") -> SBalancingView:
        """Save ``columns`` as view ``name`` (an existing view of that name is replaced)."""
        name = name.strip()
        columns = [column.strip() for column in columns if column.strip()]
        if not name or not columns:
            raise ValueError("a view needs a name and at least one value path")
        self.m_views[name] = SBalancingView(name, columns, template)
        return self.m_views[name]

    def remove(self, name: str) -> bool:
        return self.m_views.pop(name, None) is not None

    def save(self) -> None:
        if self.m_path is None:
            raise ValueError("no file to save the views to")
        rows = [
            {"name": view.name, "columns": view.columns, "template": view.template}
            for view in map(self.m_views.get, self.names)
        ]
        text = json.dumps({"format": FORMAT, "views": rows}, indent=1) + "\n"
        write_atomic(self.m_path, text.encode("utf-8"))
