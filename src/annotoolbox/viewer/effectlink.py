"""Which effect the viewer hands to the particle editor: the one the selected part of the open model places, else the
only effect there is. No Qt.

``effect_to_edit(scene, node)`` gives the effect's file and the model config that places it (the link ``edit_effect``
of ``suite.links``). docs/particle-editor.md, "The app".
"""

from __future__ import annotations

from ..sources.overlay import normalize
from .modelscene import Scene


def effect_to_edit(scene: Scene | None, node: str) -> tuple[str, str] | None:
    """(effect file, model config or "" when the scene is the effect alone) for the part ``node``; None when the
    part places no effect and the scene has not exactly one."""
    if scene is None or not scene.effects:
        return None
    found = [e for e in scene.effects if node and e.node == node] or (scene.effects if len(scene.effects) == 1 else [])
    if not found:
        return None
    effect = normalize(found[0].file)
    return effect, "" if normalize(scene.config) == effect else normalize(scene.config)
