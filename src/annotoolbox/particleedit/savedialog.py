"""The particle editor's save dialog: the three ways (``saving.ways``) with their reasons, a path for a new file, and
what the chosen way writes; ``result_plan`` is the ``saving.SPlan`` chosen. The dialog itself is the object builder's
``writeback.CWaysDialog``. docs/particle-editor.md, "The app".
"""

from __future__ import annotations

from ..infra.i18n import tr
from ..objbuild.writeback import CWaysDialog
from .effectdoc import CEffectDoc
from .saving import TITLES, SPlan, plan, ways


class CSaveDialog(CWaysDialog):
    def __init__(self, effect: CEffectDoc, parent=None) -> None:
        self.m_effect = effect
        stem, extension = effect.m_path.rsplit(".", 1)
        super().__init__(tr("particles.title", title=effect.title), ways(effect), TITLES, f"{stem}_mod.{extension}",
                         (680, 480), parent)  # fmt: skip

    def _plan(self, way: str, path: str) -> SPlan:
        return plan(self.m_effect, way, path)
