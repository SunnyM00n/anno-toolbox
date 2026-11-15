"""Edit layers: named groups of objects a mapper makes (a town, the cliffs of one bay), hidden or locked together.

``EditLayers`` keeps them in the edit file under ``layers`` (``{name: {"hidden": bool, "locked": bool, "members":
[keys]}}``); an object placed in the editor carries its layer in its own record (``"layer"``), as its key is not
stable across a save. The island build reads neither. Every change is one step of the undo stack; ``hides`` and
``locked`` are what the view and the selection ask (island-editor "Full spec").
"""

from __future__ import annotations

from collections.abc import Iterable

from ..infra.i18n import tr
from ..suite.undo import Command
from .editchange import copy_json
from .editsession import EditSession, Entry


class EditLayers:
    def __init__(self, session: EditSession) -> None:
        self.session = session
        self.edits = session.edits

    @property
    def table(self) -> dict:
        """The layers; an island without any keeps its edit file free of the key until one is made."""
        if "layers" not in self.edits:
            return {}
        return self.edits["layers"]

    def names(self) -> list[str]:
        return list(self.table)

    def layer_of(self, e: Entry) -> str | None:
        if e.added is not None:
            return e.added.get("layer")
        return next((n for n, row in self.table.items() if e.key in row["members"]), None)

    def members(self, name: str) -> list[Entry]:
        return [e for e in self.session.entries if e.alive and self.layer_of(e) == name]

    def hides(self, e: Entry) -> bool:
        name = self.layer_of(e)
        return bool(name and self.table.get(name, {}).get("hidden"))

    def locked(self, e: Entry) -> bool:
        name = self.layer_of(e)
        return bool(name and self.table.get(name, {}).get("locked"))

    # changes, each one step of the undo stack
    def _snapshot(self) -> tuple:
        added = [(e.added, e.added.get("layer")) for e in self.session.entries if e.added is not None]
        return copy_json(self.table), added

    def _put(self, snap: tuple) -> None:
        table, added = snap
        self.table.clear()
        self.table.update(copy_json(table))
        for rec, layer in added:
            if layer is None:
                rec.pop("layer", None)
            else:
                rec["layer"] = layer

    def _change(self, label: str, do) -> None:
        if self.session.readonly:
            return
        self.edits.setdefault("layers", {})
        before = self._snapshot()
        do()
        after = self._snapshot()
        self.session.undo.push(Command(label, lambda: self._put(before), lambda: self._put(after)))

    def create(self, name: str) -> None:
        if not name or name in self.table:
            raise ValueError(tr("a layer needs a new name: {name}", name=repr(name)))
        self._change(tr("new layer {name}", name=name), lambda: self.table.update({name: _row()}))

    def remove(self, name: str) -> None:
        """Take the layer away; its objects stay on the island without a layer."""

        def do():
            self.table.pop(name, None)
            for e in self.session.entries:
                if e.added is not None and e.added.get("layer") == name:
                    e.added.pop("layer")

        self._change(tr("remove layer {name}", name=name), do)

    def assign(self, entries: Iterable[Entry], name: str | None) -> None:
        """Put ``entries`` on the layer ``name`` (made when new), or on none."""
        rows = [e for e in entries if e.kind != "tree"]

        def do():
            if name is not None:
                self.table.setdefault(name, _row())
            for e in rows:
                for row in self.table.values():
                    if e.key in row["members"]:
                        row["members"].remove(e.key)
                if e.added is not None:
                    if name is None:
                        e.added.pop("layer", None)
                    else:
                        e.added["layer"] = name
                elif name is not None:
                    self.table[name]["members"].append(e.key)

        self._change(tr("to layer {name}", name=name) if name else tr("off its layer"), do)

    def set_flag(self, name: str, flag: str, on: bool) -> None:
        """Hide (``hidden``) or lock (``locked``) a layer."""
        if flag not in ("hidden", "locked"):
            raise ValueError(flag)
        if on:
            label = tr("hide {name}", name=name) if flag == "hidden" else tr("lock {name}", name=name)
        else:
            label = tr("hidden off: {name}", name=name) if flag == "hidden" else tr("locked off: {name}", name=name)
        self._change(label, lambda: self.table[name].update({flag: on}))


def _row() -> dict:
    return {"hidden": False, "locked": False, "members": []}
