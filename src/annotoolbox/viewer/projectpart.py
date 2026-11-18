"""The viewer's share of a project's mod (``suite.project.Part``): the models imported from glTF, kept under
``model-edits/<game>/<game path>`` and put into the mod at the path they replace or add.

``PART`` is what ``suite.apps.parts`` lists; ``EDITS.save_files`` is the import dialog's way to keep a model in the
project, ``EDITS.saved_files`` lists what the project holds. The build copies the files as they are: an imported
``.rdm`` goes into the mod where the game's own file stands, so the loader reads it in its place (docs/rdm-gltf.md).
"""

from __future__ import annotations

from ..infra.i18n import N_
from ..suite.project import MODEL_EDITS, EditsFolder

EDITS = EditsFolder(MODEL_EDITS)
PART = EDITS.part("models", N_("viewer.imported_models"), "Imported models")
