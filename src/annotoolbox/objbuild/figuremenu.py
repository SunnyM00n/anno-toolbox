"""View > Figures' state and the Walking figures switch of the object builder: the actors of the object's feedback
file (``viewer.figures``) put into the view's scene each time it is built, and what they play merged into the
view's animations.

``FigureMenu(window, menu)`` is the entry; the window calls ``graft`` when it builds the scene and ``animations``
when its clock moves. docs/engine-cloth.md "The figures in the view".
"""

from __future__ import annotations

from PySide6.QtGui import QActionGroup

from ..infra.i18n import N_, tr
from ..viewer import figures

STATES = ((0, N_("viewer.not_working")), (1, N_("viewer.working")))


class FigureMenu:
    """The figures' switch and state; ``figures`` the ones in the scene now (None: off, or no feedback file)."""

    def __init__(self, window, menu) -> None:
        self.window, self.shown, self.state = window, False, 1
        self.figures: figures.Figures | None = None
        self._units: dict[int, object] = {}
        group = QActionGroup(window)
        for state, text in STATES:
            action = menu.addAction(tr(text), lambda s=state: self.set_state(s))
            action.setCheckable(True)
            action.setChecked(state == self.state)
            group.addAction(action)

    def set_shown(self, on: bool) -> None:
        self.shown = on
        self.window._redraw()

    def set_state(self, state: int) -> None:
        self.state = state
        if self.shown:
            self.window._redraw()

    def units(self, obj):
        """The game's asset list, read once per game files."""
        key = id(obj.files)
        if key not in self._units:
            self._units[key] = figures.units_of(obj.files, obj.game)
        return self._units[key]

    def graft(self, scene, obj) -> str:
        """Put the figures into ``scene``; what the status line says ("" when off)."""
        self.figures = None
        feedback = obj.doc("fc") if obj is not None else None
        if not self.shown or feedback is None:
            return ""
        self.figures = figures.Figures.of(obj.files, obj.game, feedback.root, self.state, self.units(obj))
        self.figures.graft(scene)
        found = sum(1 for f in self.figures.figures if f.unit is not None)
        return tr("viewer.walking_figures_unit", n=len(self.figures.figures), found=found)

    def animations(self, seconds: float) -> dict:
        return self.figures.animations(seconds) if self.figures is not None else {}
