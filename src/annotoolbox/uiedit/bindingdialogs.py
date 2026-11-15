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

TITLE = N_("UI editor")
PROPERTY_KINDS = ["Boolean", "Int32", "Float32", "String", "Vector2", "Vector3", "Event", "Action"]


def ask_property(parent: QWidget) -> tuple[str, int, int, object] | None:
    """A custom property's name and type: (name, property type, value type code, default value)."""
    name, ok = QInputDialog.getText(parent, tr(TITLE), tr("The custom property's name:"))
    if not ok or not name:
        return None
    kind, ok = QInputDialog.getItem(parent, tr(TITLE), tr("Its type:"), PROPERTY_KINDS, 2, False)
    if not ok:
        return None
    ptype = {v: k for k, v in fields.ENUMS["PropertyType"].items()}[kind]
    code = TYPE_CODES.get(ptype, 0)
    default = {fields.BOOL: False, fields.I32: 0, fields.FLOAT: 0.0, fields.STRING: "", fields.VEC2: (0.0, 0.0),
               fields.VEC3: (0.0, 0.0, 0.0)}.get(code)  # fmt: skip
    return name, ptype, code, default


def ask_binding(parent: QWidget, own: dict[int, tuple[str, int]], top: elements.Element | None):
    """A binding from a source to a target through an operator: (source, operator hash, target)."""
    source = choose_end(parent, tr("The source:"), own, top)
    if source is None:
        return None
    ops = [f"{o.name} ({tr(o.family)}: {tr(o.doc)})" for o in operators.OPERATORS.values()]
    op, ok = QInputDialog.getItem(parent, tr(TITLE), tr("The operator:"), ops, 0, False)
    if not ok:
        return None
    target = choose_end(parent, tr("The target:"), own, top, targets=True)
    if target is None:
        return None
    return source, list(operators.OPERATORS)[ops.index(op)], target


def choose_end(parent: QWidget, what: str, own: dict[int, tuple[str, int]], top: elements.Element | None,
               targets: bool = False) -> bindings.EndSpec | None:  # fmt: skip
    """One end of a binding, picked from the file's own properties, its elements' built-in properties (actions only
    as targets, events only as sources) and a typed constant (sources only)."""
    choices: list[tuple[str, bindings.EndSpec]] = [
        (f"own.{n}", bindings.EndSpec("own", name=n)) for n, _t in own.values()
    ]
    for el in top.walk() if top else []:
        if el.kind == "template" or not el.guid:
            continue
        for b in fields.builtins_of(el.kind):
            if b.kind == "property" or (b.kind == "action" and targets) or (b.kind == "event" and not targets):
                choices.append((f"{el.name}.{b.name}", bindings.EndSpec("path", el.guid, (b.uid,))))
    if not targets:
        choices.append((tr("a constant..."), bindings.EndSpec("constant")))
    text, ok = QInputDialog.getItem(parent, tr(TITLE), what, [c[0] for c in choices], 0, True)
    if not ok:
        return None
    spec = dict(choices).get(text)
    if spec is not None and spec.kind == "constant":
        value, ok = QInputDialog.getText(parent, tr(TITLE), tr("The constant (a number, or text):"))
        if not ok:
            return None
        try:
            return bindings.EndSpec("constant", value=(fields.FLOAT, float(value)), ptype=3)
        except ValueError:
            return bindings.EndSpec("constant", value=(fields.STRING, value), ptype=4)
    return spec


def ask_bind_read(parent: QWidget, element: elements.Element):
    """The built-in property of ``element`` a field the file reads is bound into, None when cancelled."""
    props = [b for b in fields.builtins_of(element.kind) if b.kind == "property"]
    name, ok = QInputDialog.getItem(
        parent, tr(TITLE), tr("Bind into {name}'s:", name=element.name), [b.name for b in props], 0, False
    )
    return next(b for b in props if b.name == name) if ok else None
