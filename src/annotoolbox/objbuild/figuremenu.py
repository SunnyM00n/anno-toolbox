"""View > Figures' state and the Walking figures switch of the object builder: the actors of the object's feedback
file (``viewer.figures``) put into the view's scene each time it is built, and what they play merged into the
view's animations.

``CFigureMenu(window, menu)`` is the entry; the window calls ``graft`` when it builds the scene and ``animations``
when its clock moves. docs/engine-cloth.md "The figures in the view".
"""

from __future__ import annotations

from PySide6.QtGui import QActionGroup

from ..infra.i18n import N_, tr
from ..viewer import figures

STATES = ((0, N_("viewer.not_working")), (1, N_("viewer.working")))


class CFigureMenu:
    """The figures' switch and state; ``figures`` the ones in the scene now (None: off, or no feedback file)."""

    def __init__(self, window, menu) -> None:
        self.m_window, self.m_shown, self.m_state = window, False, 1
        self.m_figures: figures.CFigures | None = None
        self._m_units: tuple[object, str, object] | None = None  # (the game's files, its key, its asset list)
        group = QActionGroup(window)
        for state, text in STATES:
            action = menu.addAction(tr(text), lambda bound_state=state: self.set_state(bound_state))
            action.setCheckable(True)
            action.setChecked(state == self.m_state)
            group.addAction(action)

    def set_shown(self, is_shown: bool) -> None:
        self.m_shown = is_shown
        self.m_window._redraw()

    def set_state(self, state: int) -> None:
        self.m_state = state
        if self.m_shown:
            self.m_window._redraw()

    def units(self, object_doc):
        """The game's asset list, read once per game's files: the one of the last game kept (an object's own
        ``CEditedFiles`` is new at every open, the files under it are the game's)."""
        base = getattr(object_doc.m_files, "m_base", object_doc.m_files)
        if self._m_units is None or self._m_units[0] is not base or self._m_units[1] != object_doc.m_game:
            self._m_units = (base, object_doc.m_game, figures.units_of(object_doc.m_files, object_doc.m_game))
        return self._m_units[2]

    def graft(self, scene, object_doc) -> str:
        """Put the figures into ``scene``; what the status line says ("" when off)."""
        self.m_figures = None
        feedback = object_doc.document("fc") if object_doc is not None else None
        if not self.m_shown or feedback is None:
            return ""
        self.m_figures = figures.CFigures.of(
            object_doc.m_files, object_doc.m_game, feedback.root, self.m_state, self.units(object_doc)
        )
        self.m_figures.graft(scene)
        found = sum(1 for figure in self.m_figures.m_figures if figure.m_unit is not None)
        return tr("viewer.walking_figures_unit", n=len(self.m_figures.m_figures), found=found)

    def animations(self, seconds: float) -> dict:
        return self.m_figures.animations(seconds) if self.m_figures is not None else {}
