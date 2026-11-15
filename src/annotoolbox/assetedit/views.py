"""Saved balancing views: a name and the value paths of a BalancingView's columns, kept per game in a small JSON file
(``<project>/asset-views/<game>.json``), so a table of "Building/Upkeep, Product/Price" comes back with one pick.
Stdlib only.

``ViewStore.load`` reads the file (a missing or damaged one is an empty store), ``put`` and ``remove`` change it,
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
class BalancingView:
    name: str
    columns: list[str]
    template: str = ""  # the template of the assets it was made for (a hint, not a filter)

    def label(self) -> str:
        return f"{self.name} ({self.template})" if self.template else self.name


@dataclass
class ViewStore:
    path: Path | None = None
    views: dict[str, BalancingView] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ViewStore:
        store = cls(path)
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            for row in (r for r in data.get("views", []) if isinstance(r, dict)):
                columns = [str(c) for c in row.get("columns", []) if str(c).strip()]
                if str(row.get("name", "")).strip() and columns:
                    store.views[row["name"]] = BalancingView(row["name"], columns, str(row.get("template", "")))
        except (OSError, ValueError, AttributeError, TypeError):
            store.views.clear()
        return store

    @property
    def names(self) -> list[str]:
        return sorted(self.views, key=str.lower)

    def put(self, name: str, columns: list[str], template: str = "") -> BalancingView:
        """Save ``columns`` as view ``name`` (an existing view of that name is replaced)."""
        name = name.strip()
        columns = [c.strip() for c in columns if c.strip()]
        if not name or not columns:
            raise ValueError("a view needs a name and at least one value path")
        self.views[name] = BalancingView(name, columns, template)
        return self.views[name]

    def remove(self, name: str) -> bool:
        return self.views.pop(name, None) is not None

    def save(self) -> None:
        if self.path is None:
            raise ValueError("no file to save the views to")
        rows = [{"name": v.name, "columns": v.columns, "template": v.template} for v in map(self.views.get, self.names)]
        text = json.dumps({"format": FORMAT, "views": rows}, indent=1) + "\n"
        write_atomic(self.path, text.encode("utf-8"))
