"""The loops every island format shares when it applies an edit file to its objects: ``apply_changes`` matches the
``objects`` by key and deletes or moves them, ``add_each`` places the ``added_objects`` one by one. Each format brings
its ``SObjectOps`` (how an object moves, how deleted ones go, what still names them) and its own placing of a new one
(``editor/island*edit``; docs/island-editor.md "Placed objects")."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SObjectOps:
    """What a format does with an object an edit names: ``move(handle, change)`` sets its place, turn and scale,
    ``remove(handles)`` takes the deleted ones out (after all were matched), ``referenced(handle)`` says whether
    something still names it (2205's labels and groups: the report lists it)."""

    move: Callable[[object, dict], None]
    remove: Callable[[list], None]
    referenced: Callable[[object], bool] = field(default=lambda _h: False)


def apply_changes(
    objects: Iterable[tuple[str, object]], changes: dict, object_operations: SObjectOps, report: dict
) -> None:
    """Apply an edit file's ``objects``: each ``(key, handle)`` of ``objects`` the edits name is deleted or moved;
    counts into ``report`` (``objects changed`` / ``objects deleted``, ``still referenced``), a key nothing matched into
    ``unmatched`` (sorted)."""
    wanted, gone = dict(changes), []
    for key, handle in objects:
        change = wanted.pop(key, None)
        if change is None:
            continue
        if change.get("deleted"):
            gone.append(handle)
            if object_operations.referenced(handle):
                report["still referenced"].append(key)
        else:
            object_operations.move(handle, change)
            report["objects changed"] += 1
    object_operations.remove(gone)
    report["objects deleted"] += len(gone)
    report["unmatched"] += sorted(wanted)


def add_each(
    entries: Iterable[dict], add: Callable[[dict], bool], missing: Callable[[dict], str], report: dict
) -> None:
    """Add every entry of ``added_objects`` with ``add`` (True when it was placed); one it could not is named by
    ``missing`` in ``unmatched``."""
    for entry in entries:
        if add(entry):
            report["objects added"] += 1
        else:
            report["unmatched"].append(missing(entry))
