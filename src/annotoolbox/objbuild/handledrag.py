"""A drag of a handle, no Qt: it writes the fields as the mouse moves (so the view can show the change), puts them
back at its end and hands over the edits that make the change as one step.

``PartDrag`` moves, turns or scales a part's orientation transformer (``objbuild.handles``), ``VertexDrag`` moves one
point of an info-file polygon (``objbuild.areaedit``). ``move`` follows the mouse, ``end`` returns the edits (None when
the mouse never moved). docs/tool-suite.md section 23, "Handles in the view".
"""

from __future__ import annotations

import math

from ..infra.i18n import tr
from ..models import cfgdoc, infofile
from ..models.cfgdoc import Edit, Node
from ..render import picking
from . import areaedit, handles

SNAP_DEGREES = 5.0


def _restore(saved: list[tuple[Node, str | None, bytes]]) -> None:
    for leaf, text, trail in saved:
        leaf.text, leaf.trail = text, trail


class VertexDrag:
    """One point of a polygon dragged over the ground."""

    role = "ifo"

    def __init__(self, area: areaedit.Area, index: int) -> None:
        self.area, self.index, self.last = area, index, None
        leaves = (infofile.coordinate(area.positions[index], axis) for axis in "xz")
        self.saved = [(leaf, leaf.text, leaf.trail) for leaf in leaves if leaf is not None]

    def move(self, ground: tuple[float, float] | None) -> None:
        """The point at ``ground`` (x, z), as shown while the mouse moves."""
        if ground is not None:
            self.last = ground
            for edit in areaedit.move_point(self.area, self.index, *ground):
                edit.do()

    def end(self) -> list[Edit] | None:
        _restore(self.saved)
        return areaedit.move_point(self.area, self.index, *self.last) if self.last is not None else None


class PartDrag:
    """A part's orientation transformer dragged by a handle: ``axis0..2`` and ``plane`` move it, ``ring0..2`` turn
    it, ``scale`` scales it. ``ray`` is the mouse's ray and ``screen`` its distance in pixels from the pivot's."""

    def __init__(self, target: handles.PartTarget, name: str, ray, screen: float) -> None:
        self.target, self.name, self.now, self.had = target, name, None, target.orient
        names = (
            handles.ROTATION if name.startswith("ring") else (handles.SCALE,) if name == "scale" else handles.POSITION
        )
        self.write = handles.field_writer(target, names)
        for edit in self.write.setup:
            edit.do()
        self.role, self.start, self.pivot = target.role, target.placed, target.pivot
        self.saved = [(leaf, leaf.text, leaf.trail) for leaf in self.write.leaves.values()]
        self.anchor = self._anchor(ray, screen)

    @property
    def axis(self) -> int:
        return int(self.name[-1]) if self.name[-1].isdigit() else 1

    def _anchor(self, ray, screen: float):
        if self.name.startswith("axis"):
            return picking.along_axis(ray, self.pivot, handles.AXES[self.axis])
        if self.name == "plane":
            return picking.ray_plane(ray, self.pivot, handles.AXES[1])
        if self.name.startswith("ring"):
            return handles.ring_angle(ray, self.pivot, self.axis)
        return screen

    def move(self, ray, screen: float, snap: bool = False) -> None:
        new = self._placed(ray, screen, snap)
        if new is not None:
            self.now = new
            for name, text in handles.values(new, tuple(self.write.leaves)).items():
                cfgdoc.set_value(self.write.leaves[name], text).do()

    def _placed(self, ray, screen: float, snap: bool) -> handles.Placed | None:
        if self.anchor is None:
            return None
        if self.name.startswith("axis"):
            now = picking.along_axis(ray, self.pivot, handles.AXES[self.axis])
            return (
                None
                if now is None
                else handles.moved(self.target, self.start, handles.AXES[self.axis] * (now - self.anchor))
            )
        if self.name == "plane":
            hit = picking.ray_plane(ray, self.pivot, handles.AXES[1])
            return None if hit is None else handles.moved(self.target, self.start, hit - self.anchor)
        if self.name.startswith("ring"):
            angle = handles.ring_angle(ray, self.pivot, self.axis)
            if angle is None:
                return None
            delta = angle - self.anchor
            step = math.radians(SNAP_DEGREES)
            return handles.turned(self.target, self.start, self.axis, round(delta / step) * step if snap else delta)
        return handles.scaled(self.start, screen / self.anchor)

    def label(self) -> str:
        if self.name.startswith("ring"):
            return tr("turn {path}", path=self.target.path)
        if self.name == "scale":
            return tr("scale {path}", path=self.target.path)
        return tr("move {path}", path=self.target.path)

    def end(self) -> list[Edit] | None:
        """The preview put back, then the edits of the final values and the ones that made what the part lacked."""
        _restore(self.saved)
        for edit in reversed(self.write.setup):
            edit.undo()
        self.target.orient = self.had
        if self.now is None:
            return None
        edits = [*handles.field_edits(self.write, self.now, self.label()), *self.write.setup]
        return edits or None
