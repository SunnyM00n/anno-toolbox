"""A drag of a handle, no Qt: it writes the fields as the mouse moves (so the view can show the change), puts them
back at its end and hands over the edits that make the change as one step.

``CPartDrag`` moves, turns or scales a part's orientation transformer (``objbuild.handles``), ``CVertexDrag`` moves one
point of an info-file polygon (``objbuild.areaedit``). ``move`` follows the mouse, ``end`` returns the edits (None when
the mouse never moved). docs/tool-suite.md section 23, "Handles in the view".
"""

from __future__ import annotations

import math

from ..infra.i18n import tr
from ..models import cfgdoc, infofile
from ..models.cfgdoc import SEdit, SNode
from ..render import picking
from . import areaedit, handles

SNAP_DEGREES = 5.0


def _restore(saved: list[tuple[SNode, str | None, bytes]]) -> None:
    for leaf, text, trail in saved:
        leaf.text, leaf.trail = text, trail


class CVertexDrag:
    """One point of a polygon dragged over the ground."""

    m_role = "ifo"

    def __init__(self, area: areaedit.SArea, index: int) -> None:
        self.m_area, self.m_index, self.m_last = area, index, None
        leaves = (infofile.coordinate(area.positions[index], axis) for axis in "xz")
        self.m_saved = [(leaf, leaf.text, leaf.trail) for leaf in leaves if leaf is not None]

    def move(self, ground: tuple[float, float] | None) -> None:
        """The point at ``ground`` (x, z), as shown while the mouse moves."""
        if ground is not None:
            self.m_last = ground
            for edit in areaedit.move_point(self.m_area, self.m_index, *ground):
                edit.do()

    def end(self) -> list[SEdit] | None:
        _restore(self.m_saved)
        return areaedit.move_point(self.m_area, self.m_index, *self.m_last) if self.m_last is not None else None


class CPartDrag:
    """A part's orientation transformer dragged by a handle: ``axis0..2`` and ``plane`` move it, ``ring0..2`` turn
    it, ``scale`` scales it. ``ray`` is the mouse's ray and ``screen`` its distance in pixels from the pivot's."""

    def __init__(self, target: handles.SPartTarget, name: str, ray, screen: float) -> None:
        self.m_target, self.m_name, self.m_now, self.m_had = target, name, None, target.orient
        names = (
            handles.ROTATION if name.startswith("ring") else (handles.SCALE,) if name == "scale" else handles.POSITION
        )
        self.m_write = handles.field_writer(target, names)
        for edit in self.m_write.setup:
            edit.do()
        self.m_role, self.m_start, self.m_pivot = target.role, target.placed, target.pivot
        self.m_saved = [(leaf, leaf.text, leaf.trail) for leaf in self.m_write.leaves.values()]
        self.m_anchor = self._anchor(ray, screen)

    @property
    def axis(self) -> int:
        return int(self.m_name[-1]) if self.m_name[-1].isdigit() else 1

    def _anchor(self, ray, screen: float):
        if self.m_name.startswith("axis"):
            return picking.along_axis(ray, self.m_pivot, handles.AXES[self.axis])
        if self.m_name == "plane":
            return picking.ray_plane(ray, self.m_pivot, handles.AXES[1])
        if self.m_name.startswith("ring"):
            return handles.ring_angle(ray, self.m_pivot, self.axis)
        return screen

    def move(self, ray, screen: float, snap_enabled: bool = False) -> None:
        new = self._placed(ray, screen, snap_enabled)
        if new is not None:
            self.m_now = new
            for name, text in handles.values(new, tuple(self.m_write.leaves)).items():
                cfgdoc.set_value(self.m_write.leaves[name], text).do()

    def _placed(self, ray, screen: float, snap_enabled: bool) -> handles.SPlaced | None:
        if self.m_anchor is None:
            return None
        if self.m_name.startswith("axis"):
            now = picking.along_axis(ray, self.m_pivot, handles.AXES[self.axis])
            return (
                None
                if now is None
                else handles.moved(self.m_target, self.m_start, handles.AXES[self.axis] * (now - self.m_anchor))
            )
        if self.m_name == "plane":
            hit = picking.ray_plane(ray, self.m_pivot, handles.AXES[1])
            return None if hit is None else handles.moved(self.m_target, self.m_start, hit - self.m_anchor)
        if self.m_name.startswith("ring"):
            angle_rad = handles.ring_angle(ray, self.m_pivot, self.axis)
            if angle_rad is None:
                return None
            delta = angle_rad - self.m_anchor
            step = math.radians(SNAP_DEGREES)
            return handles.turned(
                self.m_target, self.m_start, self.axis, round(delta / step) * step if snap_enabled else delta
            )
        return handles.scaled(self.m_start, screen / self.m_anchor)

    def label(self) -> str:
        if self.m_name.startswith("ring"):
            return tr("common.turn_path", path=self.m_target.path)
        if self.m_name == "scale":
            return tr("common.scale_path", path=self.m_target.path)
        return tr("common.move_path", path=self.m_target.path)

    def end(self) -> list[SEdit] | None:
        """The preview put back, then the edits of the final values and the ones that made what the part lacked."""
        _restore(self.m_saved)
        for edit in reversed(self.m_write.setup):
            edit.undo()
        self.m_target.orient = self.m_had
        if self.m_now is None:
            return None
        edits = [*handles.field_edits(self.m_write, self.m_now, self.label()), *self.m_write.setup]
        return edits or None
