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
from ..islandprops import SField, SSection
from . import desktopslots


class CFieldEditor:
    """The widget of one ``SField``: ``get`` and ``set`` the value as the edit file keeps it; ``changed(callback)``.
    A value the widget cannot show as it is (a number past the field's range, a list entry that is no choice) stays
    the island's until the field is changed: OK alone must not write the widget's nearest value as an edit."""

    def __init__(self, field: SField, value) -> None:
        self.m_field = field
        self.m_widget = QWidget()
        row_layout = QHBoxLayout(self.m_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        self.m_parts: list = []
        kind = field.kind
        if kind == "readonly":
            row_layout.addWidget(QLabel(str(value)))
        elif kind == "int":
            self.m_parts = [self._spin(row_layout, int(field.lo), int(field.hi))]
        elif kind == "angle":
            self.m_has_value = QCheckBox(tr("islands.set"))
            self.m_has_value.setChecked(value is not None)
            self.m_parts = [QDoubleSpinBox(minimum=field.lo, maximum=field.hi - 0.01, decimals=2, singleStep=5)]
            self.m_has_value.toggled.connect(self._angle_on)
            self._angle_on(value is not None)
            row_layout.addWidget(self.m_has_value)
            row_layout.addWidget(self.m_parts[0])
        elif kind == "rect":
            self.m_parts = [self._spin(row_layout, 0, 4096) for _ in range(4)]
        elif kind == "choice":
            box = QComboBox()
            box.addItems([choice_name or tr("common.none_in_brackets") for choice_name in field.choices])
            self.m_parts = [box]
            row_layout.addWidget(box)
        elif kind == "choices":
            for choice in field.choices:
                box = QCheckBox(choice)
                self.m_parts.append(box)
                row_layout.addWidget(box)
        self.set(value)

    def _angle_on(self, is_on: bool) -> None:
        """The angle box follows the "set" tick and says what it waits for while it is off."""
        self.m_parts[0].setEnabled(is_on)
        self.m_parts[0].setToolTip("" if is_on else tr("islands.tip.angle_tick_set"))

    @staticmethod
    def _spin(layout, minimum: int, maximum: int) -> QSpinBox:
        spin = QSpinBox(minimum=minimum, maximum=maximum)
        layout.addWidget(spin)
        return spin

    def set(self, value) -> None:
        self._put(value)
        self._m_given, self._m_shown = value, self._read()

    def get(self):
        now = self._read()  # (None for a field that only shows)
        return self._m_given if now is not None and now == self._m_shown else now

    def _put(self, value) -> None:
        kind = self.m_field.kind
        if kind == "int":
            self.m_parts[0].setValue(int(value or 0))  # (a setting the island lacks shows 0 and stays unset)
        elif kind == "angle":
            self.m_parts[0].setValue(float(value) if value is not None else 0.0)
        elif kind == "rect":
            for spin, item in zip(self.m_parts, value or (0, 0, 0, 0), strict=True):
                spin.setValue(int(item))
        elif kind == "choice":
            if value not in self.m_field.choices:  # a value the game's own islands do not use is kept, not lost
                self.m_parts[0].addItem(value or tr("common.none_in_brackets"))
                self.m_field = SField(**{**self.m_field.__dict__, "choices": (*self.m_field.choices, value)})
            self.m_parts[0].setCurrentIndex(self.m_field.choices.index(value))
        elif kind == "choices":
            chosen = {chosen_name for chosen_name in (value or "").split(";") if chosen_name}
            for box in self.m_parts:
                box.setChecked(box.text() in chosen)

    def _read(self):
        """The value the widget shows now."""
        kind = self.m_field.kind
        if kind == "int":
            return self.m_parts[0].value()
        if kind == "angle":
            return round(self.m_parts[0].value(), 2) if self.m_has_value.isChecked() else None
        if kind == "rect":
            return [spin.value() for spin in self.m_parts]
        if kind == "choice":
            return self.m_field.choices[self.m_parts[0].currentIndex()]
        if kind == "choices":
            return ";".join(box.text() for box in self.m_parts if box.isChecked())
        return None

    def changed(self, callback) -> None:
        for part in self.m_parts:
            signal = getattr(part, "valueChanged", None) or getattr(part, "currentIndexChanged", None)
            (signal or part.toggled).connect(lambda *_: callback())
        if self.m_field.kind == "angle":
            self.m_has_value.toggled.connect(lambda *_: callback())


class CSectionPage(QWidget):
    """One section: its fields, and the problems of what is entered."""

    def __init__(self, section: SSection, edits: dict) -> None:
        super().__init__()
        self.m_section = section
        layout = QVBoxLayout(self)
        if section.note:
            note = QLabel(section.note)
            note.setWordWrap(True)
            layout.addWidget(note)
        form = QFormLayout()
        layout.addLayout(form)
        values = section.values(edits)
        self.m_editors = {field.key: CFieldEditor(field, values.get(field.key)) for field in section.fields}
        for field in section.fields:
            editor = self.m_editors[field.key]
            why = ""
            if not section.enabled:
                why = tr("islands.tip.section_not_editable")
            elif field.kind == "readonly":
                why = tr("islands.tip.field_shown_only")
            widgets.enable_unless(editor.m_widget, " ".join(filter(None, (why, field.tip))) if why else "", field.tip)
            form.addRow(field.label, editor.m_widget)
        self.m_problems = QLabel()
        self.m_problems.setWordWrap(True)
        self.m_problems.setStyleSheet("color: #e0523c")
        layout.addWidget(self.m_problems)
        layout.addStretch(1)
        for editor in self.m_editors.values():
            editor.changed(self.refresh)
        self.refresh()

    def values(self) -> dict:
        return {key: editor.get() for key, editor in self.m_editors.items() if editor.m_field.kind != "readonly"}

    def refresh(self) -> None:
        shown = {key: value for key, value in self.values().items() if value != self.m_section.base.get(key)}
        self.m_problems.setText("\n".join(self.m_section.check(shown)) if self.m_section.enabled else "")


class CIslandPropertiesDialog(QDialog):
    def __init__(self, parent, sections: list[SSection], edits: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("islands.island_properties"))
        self.m_pages = [CSectionPage(section, edits) for section in sections]
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        for page in self.m_pages:
            tabs.addTab(page, page.m_section.title)
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
        for page in self.m_pages:
            if page.m_section.enabled:
                out += islandprops.set_values(session, page.m_section, page.values())
        return out


def edit_menu(main, menu) -> None:
    """The Island properties item of the window's Edit menu."""
    menu.addSeparator()
    menu.addAction(tr("islands.island_properties_dots"), lambda: show(main))
    menu.addAction(tr("islands.slot_list"), lambda: desktopslots.show(main))


def show(main) -> None:
    doc = main.m_doc
    if doc is None:
        return
    sections = islandprops.sections_for(doc.m_editor, doc.m_session)
    if not sections:
        main.say(tr("islands.island_no_settings_change"))
        return
    dialog = CIslandPropertiesDialog(main, sections, doc.m_session.m_edits)
    if dialog.exec():
        problems = dialog.write(doc.m_session)
        main.say("; ".join(problems) if problems else tr("islands.island_properties_set"))
        main.sync()
