"""The curve form of the sound editor's window (Qt): the picked object's curves (an Attenuation's, a Layer container's, an
object's RTPC entries) in a list, the picked curve's points as text, and the edits that change, add or remove one.

``CurvePanel(edit, say)`` shows itself when the picked object has curves (``load``); ``edit(name, **args)`` is the
window's edit call. ``parse_points`` / ``points_text`` are the text form (no Qt use). The edits are ``curveedits``;
docs/sound-editor.md "Curves".
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QComboBox, QFormLayout, QLineEdit, QSpinBox, QWidget

from ..formats import bnkgraph
from ..formats.bnkcurves import Point
from ..infra.i18n import N_, tr
from . import curveedits, formrows, nodebase
from .bankdoc import BankDoc

NODE_KINDS = (bnkgraph.SOUND, *bnkgraph.CONTAINERS)  # the objects whose node base ends in an RTPC list we find
PROPERTIES = (("volume", N_("volume")), ("pitch", N_("pitch")), ("low_pass", N_("low-pass filter")))  # what a new RTPC
# entry can drive: the ``nodebase.prop_id`` name and its label


def points_text(points: list[Point]) -> str:
    """``x:y:interpolation`` per point, separated by commas."""
    return ", ".join(f"{p.x:g}:{p.y:g}:{p.interp}" for p in points)


def parse_points(text: str) -> list[tuple[float, float, int]]:
    """Points from ``x:y[:interpolation]`` entries (linear when it is left out); ``ValueError`` for anything else."""
    out = []
    for part in formrows.entries(text):
        fields = part.split(":")
        if not 2 <= len(fields) <= 3:
            raise ValueError(part)
        out.append((float(fields[0]), float(fields[1]), int(fields[2]) if len(fields) == 3 else 4))
    return out


class CurvePanel(QWidget):
    def __init__(self, edit: Callable[..., bool], say: Callable[[str], None]) -> None:
        super().__init__()
        self.edit, self.say = edit, say
        self.doc: BankDoc | None = None
        self.picked = 0
        self.refs: list[curveedits.CurveRef] = []
        self.which = QComboBox()
        self.points = QLineEdit(placeholderText=tr("x:y:interpolation, x:y ... (4 linear, 9 constant)"))
        self.scaling = QSpinBox()
        self.scaling.setRange(0, 255)
        self.parameter = QLineEdit(placeholderText=tr("game parameter id"))
        self.driven = QComboBox()
        self.driven.addItems([tr(label) for _name, label in PROPERTIES])
        self.slot = QSpinBox()
        self.slot.setRange(0, 23)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow(tr("curve"), self.which)
        form.addRow(tr("points"), self.points)
        form.addRow(tr("scaling"), self.scaling)
        form.addRow(tr("new RTPC: game parameter"), self.parameter)
        form.addRow(tr("new RTPC drives"), self.driven)
        form.addRow(tr("attenuation slot of a new curve"), self.slot)
        formrows.add_buttons(
            form,
            (
                (N_("Apply the curve"), self._apply),
                (N_("Add an RTPC to this object"), self._add_rtpc),
                (N_("Remove the RTPC"), self._remove_rtpc),
                (N_("Add a curve to this attenuation"), self._add_curve),
                (N_("Remove the curve"), self._remove_curve),
            ),
        )
        self.which.currentIndexChanged.connect(self._pick)
        self.load(None, 0)

    def load(self, doc: BankDoc | None, obj_id: int) -> None:
        """List the picked object's curves; the form is hidden when it has none and cannot be given one."""
        self.doc, self.picked = doc, obj_id
        self.refs = curveedits.curve_refs(doc, obj_id) if doc is not None and obj_id else []
        self.which.blockSignals(True)
        self.which.clear()
        self.which.addItems([r.label for r in self.refs])
        self.which.blockSignals(False)
        self._pick()
        node = doc is not None and bool(obj_id) and doc.kind_of(obj_id) in NODE_KINDS
        self.setVisible(bool(self.refs) or node or self._is_attenuation())

    def _is_attenuation(self) -> bool:
        return self.doc is not None and bool(self.picked) and self.doc.kind_of(self.picked) == bnkgraph.ATTENUATION

    def _current(self) -> curveedits.CurveRef | None:
        i = self.which.currentIndex()
        return self.refs[i] if 0 <= i < len(self.refs) else None

    def _pick(self) -> None:
        ref = self._current()
        self.points.setText(points_text(ref.points) if ref else "")
        self.scaling.setValue(ref.scaling or 0 if ref else 0)
        self.scaling.setEnabled(ref is not None and ref.scaling is not None)

    def _points(self) -> list[tuple[float, float, int]] | None:
        try:
            return parse_points(self.points.text())
        except ValueError:
            self.say(tr("write the points as x:y:interpolation, separated by commas"))
            return None

    def _apply(self) -> None:
        ref, points = self._current(), self._points()
        if ref is None or points is None:
            return
        extra = {"scaling": self.scaling.value()} if ref.scaling is not None else {}
        self.edit(ref.edit, **ref.args, points=points, **extra)

    def _add_rtpc(self) -> None:
        points = self._points()
        try:
            parameter = int(self.parameter.text())
        except ValueError:
            self.say(tr("write the game parameter as a number"))
            return
        if points is not None and self.doc is not None:
            driven = nodebase.prop_id(self.doc.version, PROPERTIES[self.driven.currentIndex()][0])
            self.edit("add_rtpc", id=self.picked, parameter=parameter, driven=driven, points=points)

    def _remove_rtpc(self) -> None:
        ref = self._current()
        if ref is None or ref.edit != "set_rtpc":
            self.say(tr("pick an RTPC entry of this object"))
            return
        self.edit("remove_rtpc", **ref.args)

    def _add_curve(self) -> None:
        points = self._points()
        if points is not None and self._is_attenuation():
            self.edit("add_attenuation_curve", attenuation=self.picked, points=points, slot=self.slot.value())

    def _remove_curve(self) -> None:
        ref = self._current()
        if ref is None or ref.edit != "set_attenuation_curve":
            self.say(tr("pick a curve of this attenuation"))
            return
        self.edit("remove_attenuation_curve", **ref.args)
