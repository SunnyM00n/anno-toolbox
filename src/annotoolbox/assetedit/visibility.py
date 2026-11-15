"""When a form shows a value: the visibility conditions of Anno 1800's schema and the values a template calls
secondary. Stdlib only.

1800's ``properties-toolone.xml`` has a ``ValueVisibilityConditions`` block: per property and value a list of
conditions on the *other values of the same property* (``Condition`` items with the value it looks at, a constraint
type and its operand); a value is shown when all of them hold. A template may also list ``HiddenValues``, the
values an asset of it rarely sets; ToolOne's View menu calls them "secondary template values". Neither changes an
asset: a hidden value is only left out of the form until the view is told to show it (``docs/asset-editor.md``).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .xmltree import text

if TYPE_CHECKING:
    from .schema import Schema
    from .values import Layers

EQUALS, MIN, FLAGSET, OVERRIDDEN, DEFAULT = "equals", "min", "flagset", "overridden", "default"
KINDS = {
    "CONSTRAINT_EQUALS": EQUALS,
    "CONSTRAINT_MIN": MIN,
    "CONSTRAINT_CHOICE_FLAGSET": FLAGSET,
    "CONSTRAINT_OVERRIDDEN": OVERRIDDEN,
    "CONSTRAINT_DEFAULTVALUE": DEFAULT,
}
FALSE = ("", "0", "false", "False")


@dataclass(frozen=True)
class Condition:
    """One condition on a sibling value: ``value`` of ``watches`` must stand in ``kind`` to ``operand``."""

    watches: str
    kind: str
    operand: str = ""


Conditions = dict[tuple[str, str], tuple[Condition, ...]]  # (property, value) -> its conditions, all to hold


def read_conditions(root: ET.Element) -> Conditions:
    """The ``ValueVisibilityConditions`` of a properties file (empty where it has none)."""
    out: Conditions = {}
    block = root.find("ValueVisibilityConditions")
    for prop in block if block is not None else ():
        for value in prop:
            items = []
            for item in value.findall("Item"):
                kind = KINDS.get(text(item, "ValueConstraintType"))
                if kind is not None and text(item, "ValueDefinition"):
                    items.append(Condition(text(item, "ValueDefinition"), kind, text(item, "ValueConstraint")))
            if items:
                out[(prop.tag, value.tag)] = tuple(items)
    return out


def read_hidden(template: ET.Element) -> tuple[str, ...]:
    """The value names a template's ``HiddenValues`` lists."""
    hidden = template.find("HiddenValues")
    return tuple(c.tag for c in hidden) if hidden is not None else ()


def _number(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        return None


def holds(cond: Condition, current: str, overridden: bool, first_choice: str = "") -> bool:
    """Whether ``cond`` is met by the watched value: its ``current`` text (empty: nothing is set anywhere), whether
    the asset or a base asset sets it, and the first item of its data set (a choice nobody set is that item)."""
    shown = current or first_choice
    if cond.kind == OVERRIDDEN:
        return overridden
    if cond.kind == DEFAULT:
        return not overridden
    if cond.kind == MIN:
        return (_number(current) or 0.0) >= (_number(cond.operand) or 0.0)
    if cond.kind == FLAGSET:
        return bool(set(shown.split(";")) & set(cond.operand.split(";")))
    if not cond.operand:  # no operand: the value is off
        return shown in FALSE
    if shown in FALSE and cond.operand in FALSE:
        return True
    a, b = _number(shown), _number(cond.operand)
    return shown == cond.operand if a is None or b is None else a == b


def visible(conditions: tuple[Condition, ...], watched: Callable[[str], tuple[str, bool, str]]) -> bool:
    """All of ``conditions`` hold; ``watched(name)`` answers (text, overridden, first choice) of a sibling value."""
    return all(holds(c, *watched(c.watches)) for c in conditions)


def watcher(schema: Schema, layers: Layers, prop: str) -> Callable[[str], tuple[str, bool, str]]:
    """The sibling lookup of ``visible`` for the values of ``prop`` in one asset: the value is looked for in ``prop``
    first, then in the asset's other properties (a few conditions watch a value that lives elsewhere)."""

    def watched(name: str) -> tuple[str, bool, str]:
        for owner in [prop, *(p for p in layers.property_names() if p != prop)]:
            spec = schema.properties[owner].child(name) if owner in schema.properties else None
            if spec is None:
                continue
            found = layers.lookup((owner, name))
            data_set = schema.data_sets.get(spec.data_set)
            first = data_set.names[0] if data_set is not None and data_set.names else ""
            return found.text, found.origin in ("own", "base"), first
        return "", False, ""

    return watched


def is_shown(schema: Schema, layers: Layers, prop: str, name: str) -> bool:
    """Whether the form shows value ``name`` of ``prop`` for this asset: no condition, or every one holds."""
    conditions = schema.conditions.get((prop, name))
    return not conditions or visible(conditions, watcher(schema, layers, prop))
