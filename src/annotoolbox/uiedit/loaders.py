"""Loaders show one child: an enum loader the children whose ``EnumEntry`` is its current value (plus those with no
entry), a game object loader the child of one class of game object.

``loader_kind(files, root)`` tells a loader file; ``chosen(kind, children, inputs, design)`` the children to place.
docs/ui/117/lists-grids-tabs-pages.md "Pages: loaders that pick a widget by value"; what the program does with a
value no child matches is inferred.
"""

from __future__ import annotations

from . import fields, listlayout

ENUM_LOADER, OBJECT_LOADERS = "c0222ddb", ("f521d20f", "374d2a35")  # widget types by their GUID's first part
CURRENT_ENUM_VALUE = 0x30000004


def loader_kind(files, root: dict) -> str | None:
    """ "enum" or "object" for a widget file of a loader type, else None."""
    for t in listlayout.type_chain(files, root):
        if t == ENUM_LOADER:
            return "enum"
        if t in OBJECT_LOADERS:
            return "object"
    return None


def _entry(el) -> int:
    return int(fields.get(el.node, "EnumEntry", 0) or 0)


def chosen(kind: str, children: list, inputs: dict, design: bool) -> list:
    """The children a loader instantiates. An enum loader keeps the children with no entry and those whose entry is
    the current value; when the value is not known the design view shows the first entry (the file as an author
    sees it). An object loader shows its first child: the class of the game object is not known to the preview."""
    if not children:
        return []
    if kind == "object":
        classed = [c for c in children if fields.get(c.node, "GameObjectTemplate")]
        first = classed[:1] if design else []
        return [c for c in children if c not in classed or c in first]
    value = inputs.get(CURRENT_ENUM_VALUE)
    entries = [_entry(c) for c in children if _entry(c)]
    if not isinstance(value, int) or isinstance(value, bool) or value not in entries:
        value = entries[0] if design and entries else None
    return [c for c in children if not _entry(c) or _entry(c) == value]
