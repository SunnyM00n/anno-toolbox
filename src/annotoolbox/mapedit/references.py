"""The report of a map's growth: every absolute reference the scan found, moved or kept with the reason
(docs/map-editor.md "Growing a map on any side").
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

MOVED, KEPT = "moved", "kept"


@dataclass(frozen=True)
class Reference:
    file: str  # the file the reference is in (a game path, or "assets" / "lua")
    where: str  # its place inside the file
    status: str  # MOVED or KEPT
    detail: str = ""  # old -> new, or why it was kept
    count: int = 1  # how many values of this kind at this place


@dataclass
class Report:
    grow: tuple[int, int, int, int]  # west, south, east, north (tiles)
    size: tuple[int, int] = (0, 0)  # the map's new size
    references: list[Reference] = field(default_factory=list)

    def add(self, file: str, where: str, status: str, detail: str = "", count: int = 1) -> None:
        self.references.append(Reference(file, where, status, detail, count))

    def moved(self) -> list[Reference]:
        return [r for r in self.references if r.status != KEPT]

    def kept(self) -> list[Reference]:
        return [r for r in self.references if r.status == KEPT]

    def lines(self) -> list[str]:
        """One line per reference, the kept ones last."""
        out = []
        for r in [*self.moved(), *self.kept()]:
            n = f" x{r.count}" if r.count != 1 else ""
            out.append(f"{r.status}: {r.file} {r.where}{n}" + (f" ({r.detail})" if r.detail else ""))
        return out

    def to_json(self) -> str:
        data = {"grow": list(self.grow), "size": list(self.size), "references": [asdict(r) for r in self.references]}
        return json.dumps(data, indent=1)
