"""The map editor's share of a project's mod (``suite.project.Part``): the files an edited map becomes, kept whole
under ``map-edits/<game>/<game path>``, and the growth reports under ``map-edits/reports/`` (not part of the mod).

``PART`` is what ``suite.apps.parts`` lists; ``save`` is the window's. docs/map-editor.md "Writing it".
"""

from __future__ import annotations

from pathlib import Path

from ..infra.i18n import N_
from ..suite.project import EditsFolder, Project

EDITS = EditsFolder("map-edits", ("anno117", "anno1800", "anno2070", "anno1404", "anno1701"))
PART = EDITS.part("maps", N_("Maps"), "Maps")
REPORTS = "reports"


def save(
    project: Project, game: str, files: dict[str, bytes], earlier: set[str] = frozenset(), report: str = ""
) -> list[Path]:
    """Keep ``files`` (game path -> bytes) in the project; what an earlier save of the same map made and ``files``
    lacks is taken out again. ``report``: a growth's report, kept beside (``reports/<template>.grow.json``)."""
    root = EDITS.folder(project, game)
    for path in sorted(set(earlier) - set(files)):
        target = root.joinpath(*path.split("/"))
        if target.is_file():
            target.unlink()
    saved = EDITS.save_files(project, game, files)
    if report:
        name = next((p for p in files if p.endswith((".a7tinfo", ".www"))), "map").rsplit("/", 1)[-1]
        folder = project.folder / EDITS.edits / REPORTS
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name.rsplit('.', 1)[0]}.grow.json").write_text(report, encoding="utf-8", newline="\n")
    return saved
