"""The object builder's share of a project's mod (``suite.project.Part``): the model configs, info and feedback files
and the ``.cfg.xml`` / ``.fc.xml`` patches it saved, kept under ``object-edits/<game>/<game path>``, each written into
the mod at its game path.

``PART`` is what ``suite.apps.parts`` lists; the window saves with ``EDITS.save_announced`` (it also tells the running game
through the live link). docs/tool-suite.md section 23, "Writing back".
"""

from __future__ import annotations

from ..infra.i18n import N_
from ..suite.project import ALL_GAMES, EditsFolder

EDITS = EditsFolder("object-edits", ALL_GAMES)  # the builder's work in a project (the viewer's imports are model-edits)
PART = EDITS.part("objects", N_("Object builder"), "object builder")
