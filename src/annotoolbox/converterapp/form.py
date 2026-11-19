"""The options of an entry's command as form fields (``commandform.fields``): a check box for a switch, a list for a
choice, a line for a number, a text or several values, a line with a picker for a path.

``OptionsForm.show_fields(change, parser)`` fills the box, ``values()`` reads it back by the options' names, and
``changed`` says when a value changed (the window shows the command line again). The option's own help from the
command line is shown under it. docs/converter-app.md, "The form".
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from . import commandform

STATE_APP = "converter"  # the settings' ``apps`` entry the window keeps its own state in
# options the form shows under their own name and remembers between runs (dest -> its label and tooltip texts)
REMEMBERED = {"reskin": (N_("convert.reskin"), N_("convert.reskin_tip"))}


class OptionsForm(QObject):
    """The form in ``box`` (a group box); one row per field, made again for each entry."""

    changed = Signal()

    def __init__(self, box: QWidget, settings) -> None:
        super().__init__(box)
        self.box, self.settings = box, settings
        self.layout = QFormLayout(box)
        self.widgets: dict[str, tuple[commandform.Field, QWidget]] = {}

    def show_fields(self, change, parser) -> None:
        while self.layout.rowCount():
            self.layout.removeRow(0)
        self.widgets.clear()
        if parser is None:
            self.layout.addRow(QLabel(tr("convert.no_options")))
            return
        found = commandform.fields(change, parser)
        if not found:
            self.layout.addRow(QLabel(tr("convert.no_options")))
        for f in found:
            widget = self._widget(f, change)
            self.widgets[f.dest] = (f, widget)
            # an option the entry narrowed to its own list: the command's help names choices the list does not offer
            tip = tr(REMEMBERED[f.dest][1]) if f.dest in REMEMBERED else "" if f.forced else f.help
            self.layout.addRow(_label(f), _with_help(widget, tip))

    def _widget(self, f: commandform.Field, change) -> QWidget:
        if f.kind == "flag":
            box = QCheckBox()
            box.setChecked(self._remembered(f))
            box.toggled.connect(lambda on, dest=f.dest: self._remember(dest, on))
            return box
        if f.kind == "choice":
            combo = QComboBox()
            if not f.required and (f.default is None or str(f.default) not in f.choices):
                combo.addItem(tr("convert.default"), "")
            for c in f.choices:
                combo.addItem(c, c)
            combo.setCurrentIndex(max(0, combo.findData(str(f.default)) if f.default is not None else 0))
            combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
            return combo
        line = QLineEdit()
        start = "" if f.default in (None, [], False) else str(f.default)
        folder = change.folders.get(f.dest)
        line.setText(getattr(self.settings, folder, "") if folder else start if not f.many else "")
        line.setPlaceholderText(tr("convert.several_values") if f.many else start)
        if f.kind == "int":
            line.setValidator(QIntValidator(line))
        elif f.kind == "float":
            line.setValidator(QDoubleValidator(line))
        line.textChanged.connect(lambda _t: self.changed.emit())
        return _with_picker(line, f) if f.kind == "path" else line

    def _kept(self) -> dict:
        return self.settings.apps.setdefault(STATE_APP, {}).setdefault("options", {})

    def _remembered(self, f: commandform.Field) -> bool:
        return bool(self._kept().get(f.dest, f.default)) if f.dest in REMEMBERED else bool(f.default)

    def _remember(self, dest: str, on: bool) -> None:
        if dest in REMEMBERED:
            self._kept()[dest] = bool(on)
        self.changed.emit()

    def values(self) -> dict:
        """The form's values by option name: a bool for a switch, else the text (a choice's own word)."""
        out = {}
        for dest, (_f, widget) in self.widgets.items():
            if isinstance(widget, QCheckBox):
                out[dest] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                out[dest] = widget.currentData() or ""
            else:
                line = widget if isinstance(widget, QLineEdit) else widget.findChild(QLineEdit)
                out[dest] = line.text().strip()
        return out

    def set_value(self, dest: str, value) -> None:
        """Set one field (a test, a remembered value)."""
        _f, widget = self.widgets[dest]
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QComboBox):
            widget.setCurrentIndex(max(0, widget.findData(str(value))))
        else:
            (widget if isinstance(widget, QLineEdit) else widget.findChild(QLineEdit)).setText(str(value))


def _label(f: commandform.Field) -> QLabel:
    if f.dest in REMEMBERED:
        return QLabel(tr(REMEMBERED[f.dest][0]))
    name = f.flag or f.dest
    return QLabel(f"{name} *" if f.required else name)


def _with_help(widget: QWidget, text: str) -> QWidget:
    if not text:
        return widget
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget)
    hint = QLabel(text, wordWrap=True)
    hint.setTextFormat(Qt.TextFormat.PlainText)
    hint.setEnabled(False)  # the help of the command line: read, not set
    lay.addWidget(hint)
    widget.setToolTip(text)
    return holder


def _with_picker(line: QLineEdit, f: commandform.Field) -> QWidget:
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(line, 1)
    button = QPushButton(tr("convert.pick"))

    def pick() -> None:
        if f.folder:
            name = QFileDialog.getExistingDirectory(holder, f.flag or f.dest, line.text())
        else:
            name = QFileDialog.getOpenFileName(holder, f.flag or f.dest, line.text())[0]
        if name:
            line.setText(name)

    button.clicked.connect(lambda _c=False: pick())
    lay.addWidget(button)
    return holder
