"""Several objects selected at once, what the tools do to them together, and a clipboard of plain data.

``Selection`` holds the entries of one ``EditSession`` (add, toggle, a box, every entry of one model) and moves,
turns, scales, deletes or copies them as one change of the undo stack (``one_change``). ``copy`` turns entries
into a clipboard (a dict that survives JSON, so it can go to another island) and ``paste`` places it again.
Positions and turns are Anno's, as in the edit document (island-editor "Full spec").
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager

import numpy as np

from ..infra.i18n import tr
from ..models import meshxform
from ..suite.undo import Command
from .editchange import copy_json, yaw_quat
from .editsession import CARRIED, EditSession, Entry, label_of

CLIPBOARD_VERSION = 1


@contextmanager
def one_change(session: EditSession, label: str) -> Iterator[None]:
    """Every change pushed inside the block becomes one step of the undo stack, named ``label``."""
    stack = session.undo
    start = stack.index
    try:
        yield
    finally:
        cmds = stack.commands[start : stack.index]
        if len(cmds) > 1:
            del stack.commands[start:]
            stack.index = start

            def undo():
                for c in reversed(cmds):
                    c.undo()

            def redo():
                for c in cmds:
                    c.redo()

            stack.push(Command(label, undo, redo))
        elif cmds:
            cmds[0].label = label


class Selection:
    """The selected entries of a session, in the order they were picked (the first one is the primary)."""

    def __init__(self, session: EditSession) -> None:
        self.session = session
        self._entries: list[Entry] = []

    @property
    def entries(self) -> list[Entry]:
        """What is selected and still on the island (an undone placement drops out)."""
        self._entries = [e for e in self._entries if e.alive and e.visible]
        return list(self._entries)

    @property
    def primary(self) -> Entry | None:
        rows = self.entries
        return rows[0] if rows else None

    def __len__(self) -> int:
        return len(self.entries)

    def set(self, entries: Iterable[Entry | None]) -> None:
        self._entries = []
        for e in entries:
            if e is not None and e not in self._entries:
                self._entries.append(e)

    def add(self, e: Entry) -> None:
        if e not in self._entries:
            self._entries.append(e)

    def toggle(self, e: Entry) -> None:
        if e in self._entries:
            self._entries.remove(e)
        else:
            self._entries.append(e)

    def clear(self) -> None:
        self._entries = []

    def box(self, x0: float, z0: float, x1: float, z1: float, layers: Iterable[str] | None = None) -> int:
        """Select every shown entry whose place lies in the box (Anno x and z, either corner first); ``layers``
        limits it to those layers. Returns how many."""
        xa, xb, za, zb = min(x0, x1), max(x0, x1), min(z0, z1), max(z0, z1)
        keep = set(layers) if layers is not None else None
        self.set(
            e
            for e in self.session.entries
            if e.shown and (keep is None or e.layer in keep) and xa <= e.position[0] <= xb and za <= e.position[2] <= zb
        )
        return len(self._entries)

    def same_model(self, e: Entry) -> int:
        """Select every shown entry of ``e``'s model (or editor-only asset). Returns how many."""
        self.set(x for x in self.session.entries if x.shown and x.batch == e.batch)
        return len(self._entries)

    def centre(self) -> list[float]:
        rows = self.entries
        if not rows:
            return [0.0, 0.0, 0.0]
        return np.mean([e.position for e in rows], axis=0).tolist()

    def _editable(self) -> list[Entry]:
        return [] if self.session.readonly else [e for e in self.entries if e.kind != "tree"]

    def move(self, delta) -> bool:
        """Move everything by ``delta`` (x, y, z) as one change."""
        rows = self._editable()
        start = self.session.begin_transform(rows)
        for e in rows:
            self.session.set_transform(e, position=np.add(e.position, delta).tolist())
        return self.session.end_transform(start, self._label(tr("move")))

    def turn(self, yaw: float, about=None) -> bool:
        """Turn everything by ``yaw`` (radians, about the vertical) round ``about`` (default: the centre)."""
        rows = self._editable()
        q = yaw_quat(yaw)
        pivot = np.asarray(self.centre() if about is None else about, float)
        start = self.session.begin_transform(rows)
        for e in rows:
            pos = pivot + meshxform.qrotate(tuple(q), tuple(np.asarray(e.position) - pivot))
            self.session.set_transform(
                e, position=pos.tolist(), rotation=list(meshxform.qmul(tuple(q), tuple(e.rotation)))
            )
        return self.session.end_transform(start, self._label(tr("turn")))

    def scale(self, factor: float, about=None) -> bool:
        """Scale everything and the distances between them by ``factor`` round ``about`` (default: the centre)."""
        rows = self._editable()
        pivot = np.asarray(self.centre() if about is None else about, float)
        start = self.session.begin_transform(rows)
        for e in rows:
            pos = pivot + (np.asarray(e.position) - pivot) * factor
            s = [v * factor for v in e.scale] if isinstance(e.scale, list) else e.scale * factor
            self.session.set_transform(e, position=pos.tolist(), scale=s)
        return self.session.end_transform(start, self._label(tr("scale")))

    def delete(self) -> int:
        rows = [e for e in self.entries if not self.session.readonly]
        with one_change(self.session, self._label(tr("delete"))):
            for e in rows:
                self.session.delete(e)
        self.clear()
        return len(rows)

    def duplicate(self) -> list[Entry]:
        """A copy of everything two tiles off; the copies become the selection."""
        with one_change(self.session, self._label(tr("duplicate"))):
            copies = [c for c in (self.session.duplicate(e) for e in self._editable()) if c is not None]
        self.set(copies)
        return copies

    def _label(self, verb: str) -> str:
        n = len(self._entries)
        return verb if n == 1 else tr("{verb} {n}", verb=verb, n=n)


def _item_of(e: Entry) -> dict:
    """What a pasted copy of ``e`` needs to be placed again: its model or editor-only asset."""
    base = e.base
    item = {"kind": e.kind}
    if e.cfg:
        item["cfg"] = e.cfg
    for k in ("guid", "template", *CARRIED):
        if base.get(k) is not None:
            item[k] = base[k]
    if e.marker and not base.get("clone"):  # a clone's marker is "NoModel", its template stays
        item["template"] = e.marker
    item["name"] = label_of(base)
    item["clone"] = bool(base.get("clone"))
    return item


def copy(entries: Iterable[Entry]) -> dict:
    """A clipboard of ``entries`` (no forest trees): each item with its model, its offset from their centre, its
    turn and scale."""
    rows = [e for e in entries if e.kind != "tree" and e.alive and e.visible]
    if not rows:
        return {"version": CLIPBOARD_VERSION, "items": []}
    centre = np.mean([e.position for e in rows], axis=0)
    items = []
    for e in rows:
        item = _item_of(e)
        item["offset"] = (np.asarray(e.position) - centre).tolist()
        item["rotation"] = list(e.rotation)
        item["scale"] = copy_json(e.scale)
        if e.color is not None:
            item["color"] = list(e.color)
        items.append(item)
    return {"version": CLIPBOARD_VERSION, "items": items}


def _pick_of(item: dict) -> dict:
    """The library pick ``EditSession.place`` takes for a clipboard item: an editor-only asset or a copy of an
    object of the island (``guid``), else a model (``cfg``)."""
    if item.get("guid") and (item.get("clone") or not item.get("cfg")):
        pick = {"guid": item["guid"], "template": item.get("template"), "name": item.get("name")}
        if item.get("cfg"):
            pick["cfg"] = item["cfg"]
        if item.get("clone"):
            pick |= {"clone": True, "variation": item.get("variation", 0)}
            pick |= {"plain": True} if item.get("plain") else {}
        return pick
    return {k: item[k] for k in ("cfg", "source", "asset", "variation", "template") if item.get(k) is not None}


def paste(session: EditSession, clipboard: dict, at, yaw: float = 0.0, on_ground: bool = False) -> list[Entry]:
    """Place the clipboard's items round ``at`` (x, y, z), the whole turned by ``yaw``, as one change; with
    ``on_ground`` each lands on the ground under it. Props come back as props. Returns the new entries."""
    if session.readonly or clipboard.get("version") != CLIPBOARD_VERSION:
        return []
    q = yaw_quat(yaw)
    out = []
    n = len(clipboard["items"])
    with one_change(session, tr("paste") if n == 1 else tr("paste {n}", n=n)):
        for item in clipboard["items"]:
            pos = np.asarray(at, float) + meshxform.qrotate(tuple(q), tuple(item["offset"]))
            if on_ground:
                pos[1] = session.height_at(pos[0], pos[2])
            rot = list(meshxform.qmul(tuple(q), tuple(item["rotation"])))
            if item["kind"] == "prop":
                record = {"cfg": item["cfg"], "position": pos.tolist(), "rotation": rot}
                if item.get("color"):
                    record["color"] = list(item["color"])
                e = session.add_record("prop", record)
            else:
                e = session.place(_pick_of(item), pos.tolist())
            if e is None:
                continue
            session.set_transform(e, rotation=rot, scale=item.get("scale", 1.0))
            out.append(e)
    return out
