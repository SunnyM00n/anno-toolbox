"""The sound editor's share of a project's mod (``suite.project.SPart``): the banks and loose clips the edits made, kept
whole under ``sound-edits/<game>/<game path>`` (the mod's own paths), and the one announcement to the game link.

``PART`` is what ``suite.apps.parts`` lists; ``save`` is the window's. docs/sound-editor.md "Files".
"""

from __future__ import annotations

from ..infra.i18n import N_
from ..suite import linkannounce
from ..suite.livelink import BANKS
from ..suite.project import CProject, SEditsFolder

EDITS = SEditsFolder("sound-edits", ("anno117", "anno1800", "anno2205", "anno2070", "anno1404", "anno1701"))
PART = EDITS.part("sound", N_("sound.sound"), "Sound")


def drop(project: CProject, game: str, paths: set[str]) -> list[str]:
    """Take files an earlier save made out of the project again (an edit was undone); the game paths removed."""
    root, gone = EDITS.folder(project, game), []
    for path in sorted(paths):
        target = root.joinpath(*path.split("/"))
        if target.is_file():
            target.unlink()
            gone.append(path)
    return gone


def save(
    project: CProject, game: str, files: dict[str, bytes], earlier: set[str] = frozenset()
) -> linkannounce.SNotice:
    """Keep ``files`` (game path -> bytes) in the project and tell the game link; the text is for the status line.
    ``earlier``: what a previous save of the same bank made; what it holds that ``files`` lacks is removed."""
    drop(project, game, set(earlier) - set(files))
    EDITS.save_files(project, game, files)
    return linkannounce.announce((BANKS,), project.mod_id, sorted(files), is_deployed=False)
