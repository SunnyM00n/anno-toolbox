"""The island's settings as a dialog (Edit > Island properties): one tab per section of ``islandprops`` - the island's
info, its pool registration, the properties of a 2070-format island - with a field per setting and the problems the
game would find named as you type. OK writes the changes as one undoable step each.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...infra.i18n import tr
from ...suite import widgets
from .. import islandprops
from ..islandprops import Field, Section
from . import desktopslots


class FieldEditor:
    """The widget of one ``Field``: ``get`` and ``set`` the value as the edit file keeps it; ``changed(callback)``.
    A value the widget cannot show as it is (a number past the field's range, a list entry that is no choice) stays
    the island's until the field is changed: OK alone must not write the widget's nearest value as an edit."""

    def __init__(self, field: Field, value) -> None:
        self.field = field
        self.widget = QWidget()
        row = QHBoxLayout(self.widget)
        row.setContentsMargins(0, 0, 0, 0)
        self.parts: list = []
        kind = field.kind
        if kind == "readonly":
            row.addWidget(QLabel(str(value)))
        elif kind == "int":
            self.parts = [self._spin(row, int(field.lo), int(field.hi))]
        elif kind == "angle":
            self.has_value = QCheckBox(tr("islands.set"))
            self.has_value.setChecked(value is not None)
            self.parts = [QDoubleSpinBox(minimum=field.lo, maximum=field.hi - 0.01, decimals=2, singleStep=5)]
            self.has_value.toggled.connect(self._angle_on)
            self._angle_on(value is not None)
            row.addWidget(self.has_value)
            row.addWidget(self.parts[0])
        elif kind == "rect":
            self.parts = [self._spin(row, 0, 4096) for _ in range(4)]
        elif kind == "choice":
            box = QComboBox()
            box.addItems([c or tr("common.none_in_brackets") for c in field.choices])
            self.parts = [box]
            row.addWidget(box)
        elif kind == "choices":
            for choice in field.choices:
                box = QCheckBox(choice)
                self.parts.append(box)
                row.addWidget(box)
        self.set(value)

    def _angle_on(self, on: bool) -> None:
        """The angle box follows the "set" tick and says what it waits for while it is off."""
        self.parts[0].setEnabled(on)
        self.parts[0].setToolTip("" if on else tr("islands.tip.angle_tick_set"))

    @staticmethod
    def _spin(row, lo: int, hi: int) -> QSpinBox:
        spin = QSpinBox(minimum=lo, maximum=hi)
        row.addWidget(spin)
        return spin

    def set(self, value) -> None:
        self._put(value)
        self._given, self._shown = value, self._read()

    def get(self):
        now = self._read()  # (None for a field that only shows)
        return self._given if now is not None and now == self._shown else now

    def _put(self, value) -> None:
        kind = self.field.kind
        if kind == "int":
            self.parts[0].setValue(int(value))
        elif kind == "angle":
            self.parts[0].setValue(float(value) if value is not None else 0.0)
        elif kind == "rect":
            for spin, v in zip(self.parts, value or (0, 0, 0, 0), strict=True):
                spin.setValue(int(v))
        elif kind == "choice":
            if value not in self.field.choices:  # a value the game's own islands do not use is kept, not lost
                self.parts[0].addItem(value or tr("common.none_in_brackets"))
                self.field = Field(**{**self.field.__dict__, "choices": (*self.field.choices, value)})
            self.parts[0].setCurrentIndex(self.field.choices.index(value))
        elif kind == "choices":
            chosen = {c for c in (value or "").split(";") if c}
            for box in self.parts:
                box.setChecked(box.text() in chosen)

    def _read(self):
        """The value the widget shows now."""
        kind = self.field.kind
        if kind == "int":
            return self.parts[0].value()
        if kind == "angle":
            return round(self.parts[0].value(), 2) if self.has_value.isChecked() else None
        if kind == "rect":
            return [spin.value() for spin in self.parts]
        if kind == "choice":
            return self.field.choices[self.parts[0].currentIndex()]
        if kind == "choices":
            return ";".join(box.text() for box in self.parts if box.isChecked())
        return None

    def changed(self, callback) -> None:
        for part in self.parts:
            signal = getattr(part, "valueChanged", None) or getattr(part, "currentIndexChanged", None)
            (signal or part.toggled).connect(lambda *_: callback())
        if self.field.kind == "angle":
            self.has_value.toggled.connect(lambda *_: callback())


class SectionPage(QWidget):
    """One section: its fields, and the problems of what is entered."""

    def __init__(self, section: Section, edits: dict) -> None:
        super().__init__()
        self.section = section
        layout = QVBoxLayout(self)
        if section.note:
            note = QLabel(section.note)
            note.setWordWrap(True)
            layout.addWidget(note)
        form = QFormLayout()
        layout.addLayout(form)
        values = section.values(edits)
        self.editors = {f.key: FieldEditor(f, values.get(f.key)) for f in section.fields}
        for f in section.fields:
            editor = self.editors[f.key]
            why = ""
            if not section.enabled:
                why = tr("islands.tip.section_not_editable")
            elif f.kind == "readonly":
                why = tr("islands.tip.field_shown_only")
            widgets.enable_unless(editor.widget, " ".join(filter(None, (why, f.tip))) if why else "", f.tip)
            form.addRow(f.label, editor.widget)
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("color: #e0523c")
        layout.addWidget(self.problems)
        layout.addStretch(1)
        for editor in self.editors.values():
            editor.changed(self.refresh)
        self.refresh()

    def values(self) -> dict:
        return {k: e.get() for k, e in self.editors.items() if e.field.kind != "readonly"}

    def refresh(self) -> None:
        shown = {k: v for k, v in self.values().items() if v != self.section.base.get(k)}
        self.problems.setText("\n".join(self.section.check(shown)) if self.section.enabled else "")


class IslandPropertiesDialog(QDialog):
    def __init__(self, parent, sections: list[Section], edits: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("islands.island_properties"))
        self.pages = [SectionPage(s, edits) for s in sections]
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        for page in self.pages:
            tabs.addTab(page, page.section.title)
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("islands.ok"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("common.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def write(self, session) -> list[str]:
        """Put every enabled page's values into the edit document; returns the problems named on the way."""
        out = []
        for page in self.pages:
            if page.section.enabled:
                out += islandprops.set_values(session, page.section, page.values())
        return out


def edit_menu(main, menu) -> None:
    """The Island properties item of the window's Edit menu."""
    menu.addSeparator()
    menu.addAction(tr("islands.island_properties_dots"), lambda: show(main))
    menu.addAction(tr("islands.slot_list"), lambda: desktopslots.show(main))


def show(main) -> None:
    doc = main.doc
    if doc is None:
        return
    sections = islandprops.sections_for(doc.editor, doc.session)
    if not sections:
        main.say(tr("islands.island_no_settings_change"))
        return
    dialog = IslandPropertiesDialog(main, sections, doc.session.edits)
    if dialog.exec():
        problems = dialog.write(doc.session)
        main.say("; ".join(problems) if problems else tr("islands.island_properties_set"))
        main.sync()
