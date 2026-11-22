"""The curve form of the sound editor's window (Qt): the picked object's curves (an Attenuation's, a Layer container's, an
object's RTPC entries) in a list, the picked curve's points as text, and the edits that change, add or remove one.

``CCurvePanel(edit, say)`` shows itself when the picked object has curves (``load``); ``edit(name, **args)`` is the
window's edit call. ``parse_points`` / ``points_text`` are the text form (no Qt use). The edits are ``curveedits``;
docs/sound-editor.md "Curves".
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtWidgets import QComboBox, QFormLayout, QLineEdit, QSpinBox, QWidget

from ..formats import bnkgraph
from ..formats.bnkcurves import SPoint
from ..infra.i18n import N_, tr
from . import curveedits, formrows, nodebase
from .bankdoc import CBankDoc

NODE_KINDS = (bnkgraph.SOUND, *bnkgraph.CONTAINERS)  # the objects whose node base ends in an RTPC list we find
PROPERTIES = (
    ("volume", N_("common.volume")),
    ("pitch", N_("common.pitch")),
    ("low_pass", N_("common.low_pass_filter")),
)  # what a new RTPC
# entry can drive: the ``nodebase.prop_id`` name and its label


def points_text(points: list[SPoint]) -> str:
    """``x:y:interpolation`` per point, separated by commas, each number as the 32-bit float the bank holds."""
    return ", ".join(
        f"{formrows.number_text(point.x, True)}:{formrows.number_text(point.y, True)}:{point.interp}"
        for point in points
    )


def parse_points(text: str) -> list[tuple[float, float, int]]:
    """Points from ``x:y[:interpolation]`` entries (linear when it is left out); ``ValueError`` for anything else."""
    out = []
    for part in formrows.entries(text):
        fields = part.split(":")
        if not 2 <= len(fields) <= 3:
            raise ValueError(part)
        x, y = float(fields[0]), float(fields[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(part)
        out.append((x, y, int(fields[2]) if len(fields) == 3 else 4))
    return out


class CCurvePanel(QWidget):
    def __init__(self, edit: Callable[..., bool], say: Callable[[str], None]) -> None:
        super().__init__()
        self.m_edit, self.m_say = edit, say
        self.m_doc: CBankDoc | None = None
        self.m_picked = 0
        self.m_refs: list[curveedits.SCurveRef] = []
        self.m_which = QComboBox()
        self.m_points = QLineEdit(placeholderText=tr("common.x_y_interpolation_4_linear"))
        self.m_scaling = QSpinBox()
        self.m_scaling.setRange(0, 255)
        self.m_parameter = QLineEdit(placeholderText=tr("common.game_parameter_id"))
        self.m_driven = QComboBox()
        self.m_driven.addItems([tr(label) for _name, label in PROPERTIES])
        self.m_slot = QSpinBox()
        self.m_slot.setRange(0, 23)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow(tr("common.curve"), self.m_which)
        form.addRow(tr("common.points"), self.m_points)
        form.addRow(tr("common.scaling"), self.m_scaling)
        form.addRow(tr("common.new_rtpc_game_parameter"), self.m_parameter)
        form.addRow(tr("common.new_rtpc_drives"), self.m_driven)
        form.addRow(tr("common.attenuation_slot_new_curve"), self.m_slot)
        formrows.add_buttons(
            form,
            (
                (N_("common.apply_curve"), self._apply),
                (N_("common.add_rtpc_object"), self._add_rtpc),
                (N_("common.remove_rtpc"), self._remove_rtpc),
                (N_("common.add_curve_attenuation"), self._add_curve),
                (N_("common.remove_curve"), self._remove_curve),
            ),
        )
        self.m_which.currentIndexChanged.connect(self._pick)
        self.load(None, 0)

    def load(self, doc: CBankDoc | None, obj_id: int) -> None:
        """List the picked object's curves; the form is hidden when it has none and cannot be given one."""
        self.m_doc, self.m_picked = doc, obj_id
        self.m_refs = curveedits.curve_refs(doc, obj_id) if doc is not None and obj_id else []
        self.m_which.blockSignals(True)
        self.m_which.clear()
        self.m_which.addItems([ref.label for ref in self.m_refs])
        self.m_which.blockSignals(False)
        self._pick()
        node = doc is not None and bool(obj_id) and doc.kind_of(obj_id) in NODE_KINDS
        self.setVisible(bool(self.m_refs) or node or self._is_attenuation())

    def _is_attenuation(self) -> bool:
        return (
            self.m_doc is not None and bool(self.m_picked) and self.m_doc.kind_of(self.m_picked) == bnkgraph.ATTENUATION
        )

    def _current(self) -> curveedits.SCurveRef | None:
        i = self.m_which.currentIndex()
        return self.m_refs[i] if 0 <= i < len(self.m_refs) else None

    def _pick(self) -> None:
        ref = self._current()
        self.m_points.setText(points_text(ref.points) if ref else "")
        self.m_scaling.setValue(ref.scaling or 0 if ref else 0)
        self.m_scaling.setEnabled(ref is not None and ref.scaling is not None)

    def _points(self) -> list[tuple[float, float, int]] | None:
        try:
            return parse_points(self.m_points.text())
        except ValueError:
            self.m_say(tr("common.write_points_x_y_interpolation"))
            return None

    def _apply(self) -> None:
        ref, points = self._current(), self._points()
        if ref is None or points is None:
            return
        extra = {"scaling": self.m_scaling.value()} if ref.scaling is not None else {}
        self.m_edit(ref.edit, **ref.args, points=points, **extra)

    def _add_rtpc(self) -> None:
        points = self._points()
        try:
            parameter = int(self.m_parameter.text())
        except ValueError:
            self.m_say(tr("common.write_game_parameter_number"))
            return
        if points is not None and self.m_doc is not None:
            driven = nodebase.prop_id(self.m_doc.m_version, PROPERTIES[self.m_driven.currentIndex()][0])
            self.m_edit("add_rtpc", id=self.m_picked, parameter=parameter, driven=driven, points=points)

    def _remove_rtpc(self) -> None:
        ref = self._current()
        if ref is None or ref.edit != "set_rtpc":
            self.m_say(tr("common.pick_rtpc_entry_object"))
            return
        self.m_edit("remove_rtpc", **ref.args)

    def _add_curve(self) -> None:
        points = self._points()
        if points is not None and self._is_attenuation():
            self.m_edit("add_attenuation_curve", attenuation=self.m_picked, points=points, slot=self.m_slot.value())

    def _remove_curve(self) -> None:
        ref = self._current()
        if ref is None or ref.edit != "set_attenuation_curve":
            self.m_say(tr("common.pick_curve_attenuation"))
            return
        self.m_edit("remove_attenuation_curve", **ref.args)
