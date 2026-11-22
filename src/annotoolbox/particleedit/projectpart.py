"""The particle editor's share of a project's mod (``suite.project.SPart``): the effect files and the model configs (or
their ModOps patches) it saved, kept under ``particle-edits/<game>/<game path>``, each written into the mod at its game
path.

``PART`` is what ``suite.apps.parts`` lists; the window saves with ``EDITS.save_announced`` (it also tells the running game
through the live link). docs/particle-editor.md, "The app".
"""

from __future__ import annotations

from ..infra.i18n import N_
from ..suite.project import ALL_GAMES, SEditsFolder

EDITS = SEditsFolder("particle-edits", ALL_GAMES)
PART = EDITS.part("particles", N_("islands.particles"), "particle editor")
