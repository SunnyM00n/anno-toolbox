"""The questions the UI editor asks to make a custom property or a binding: a name and a type, a source, an operator
and a target each chosen from what the open file offers.

``ask_property(parent)`` gives ``(name, type, code, default)``; ``ask_binding(parent, own, top)`` gives
``(source, operator hash, target)``; ``ask_bind_read(parent, element)`` the built-in property to bind a read path
into. Each answers None when the user cancels. docs/ui-editor.md "Bindings".
"""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QWidget

from ..infra.i18n import N_, tr
from . import bindings, elements, fields, operators
from .panels import TYPE_CODES

TITLE = N_("ui.ui_editor")
PROPERTY_KINDS = ["Boolean", "Int32", "Float32", "String", "Vector2", "Vector3", "Event", "Action"]


def ask_property(parent: QWidget) -> tuple[str, int, int, object] | None:
    """A custom property's name and type: (name, property type, value type code, default value)."""
    name, accepted = QInputDialog.getText(parent, tr(TITLE), tr("ui.custom_propertys_name"))
    if not accepted or not name:
        return None
    kind, accepted = QInputDialog.getItem(parent, tr(TITLE), tr("ui.its_type"), PROPERTY_KINDS, 2, False)
    if not accepted:
        return None
    property_type = {type_name: type_id for type_id, type_name in fields.ENUMS["PropertyType"].items()}[kind]
    code = TYPE_CODES.get(property_type, 0)
    default = {fields.BOOL: False, fields.I32: 0, fields.FLOAT: 0.0, fields.STRING: "", fields.VEC2: (0.0, 0.0),
               fields.VEC3: (0.0, 0.0, 0.0)}.get(code)  # fmt: skip
    return name, property_type, code, default


def ask_binding(parent: QWidget, own: dict[int, tuple[str, int]], top: elements.SElement | None):
    """A binding from a source to a target through an operator: (source, operator hash, target)."""
    source = choose_end(parent, tr("ui.the_source"), own, top)
    if source is None:
        return None
    ops = [
        f"{operator_entry.name} ({tr(operators.FAMILY_LABELS[operator_entry.family])}: {tr(operator_entry.description)})"
        for operator_entry in operators.OPERATORS.values()
    ]
    chosen_operator, accepted = QInputDialog.getItem(parent, tr(TITLE), tr("ui.the_operator"), ops, 0, False)
    if not accepted:
        return None
    target = choose_end(parent, tr("ui.the_target"), own, top, targets=True)
    if target is None:
        return None
    return source, list(operators.OPERATORS)[ops.index(chosen_operator)], target


def choose_end(parent: QWidget, what: str, own: dict[int, tuple[str, int]], top: elements.SElement | None,
               targets: bool = False) -> bindings.SEndSpec | None:  # fmt: skip
    """One end of a binding, picked from the file's own properties, its elements' built-in properties (actions only
    as targets, events only as sources) and a typed constant (sources only)."""
    choices: list[tuple[str, bindings.SEndSpec]] = [
        (f"own.{name}", bindings.SEndSpec("own", name=name)) for name, _t in own.values()
    ]
    for element in top.walk() if top else []:
        if element.kind == "template" or not element.guid:
            continue
        for builtin in fields.builtins_of(element.kind):
            if (
                builtin.kind == "property"
                or (builtin.kind == "action" and targets)
                or (builtin.kind == "event" and not targets)
            ):
                choices.append(
                    (f"{element.name}.{builtin.name}", bindings.SEndSpec("path", element.guid, (builtin.uid,)))
                )
    if not targets:
        choices.append((tr("ui.a_constant"), bindings.SEndSpec("constant")))
    text, accepted = QInputDialog.getItem(parent, tr(TITLE), what, [choice[0] for choice in choices], 0, True)
    if not accepted:
        return None
    spec = dict(choices).get(text)
    if spec is not None and spec.kind == "constant":
        value, accepted = QInputDialog.getText(parent, tr(TITLE), tr("ui.constant_number_text"))
        if not accepted:
            return None
        try:
            return bindings.SEndSpec("constant", value=(fields.FLOAT, float(value)), ptype=3)
        except ValueError:
            return bindings.SEndSpec("constant", value=(fields.STRING, value), ptype=4)
    return spec


def ask_bind_read(parent: QWidget, element: elements.SElement):
    """The built-in property of ``element`` a field the file reads is bound into, None when cancelled."""
    properties = [builtin for builtin in fields.builtins_of(element.kind) if builtin.kind == "property"]
    name, accepted = QInputDialog.getItem(
        parent,
        tr(TITLE),
        tr("ui.bind_into_names", name=element.name),
        [builtin.name for builtin in properties],
        0,
        False,
    )
    return next(builtin for builtin in properties if builtin.name == name) if accepted else None
