"""The report of a map's growth: every absolute reference the scan found, moved or kept with the reason
(docs/map-editor.md "Growing a map on any side").
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

MOVED, KEPT = "moved", "kept"


@dataclass(frozen=True)
class SReference:
    file: str  # the file the reference is in (a game path, or "assets" / "lua")
    where: str  # its place inside the file
    status: str  # MOVED or KEPT
    detail: str = ""  # old -> new, or why it was kept
    count: int = 1  # how many values of this kind at this place


@dataclass
class CReport:
    m_grow: tuple[int, int, int, int]  # west, south, east, north (tiles)
    m_size: tuple[int, int] = (0, 0)  # the map's new size
    m_references: list[SReference] = field(default_factory=list)

    def add(self, file: str, where: str, status: str, detail: str = "", count: int = 1) -> None:
        self.m_references.append(SReference(file, where, status, detail, count))

    def moved(self) -> list[SReference]:
        return [reference for reference in self.m_references if reference.status != KEPT]

    def kept(self) -> list[SReference]:
        return [reference for reference in self.m_references if reference.status == KEPT]

    def lines(self) -> list[str]:
        """One line per reference, the kept ones last."""
        out = []
        for reference in [*self.moved(), *self.kept()]:
            count_text = f" x{reference.count}" if reference.count != 1 else ""
            out.append(
                f"{reference.status}: {reference.file} {reference.where}{count_text}"
                + (f" ({reference.detail})" if reference.detail else "")
            )
        return out

    def to_json(self) -> str:
        data = {
            "grow": list(self.m_grow),
            "size": list(self.m_size),
            "references": [asdict(reference) for reference in self.m_references],
        }
        return json.dumps(data, indent=1)
