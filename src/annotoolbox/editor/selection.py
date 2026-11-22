"""Several objects selected at once, what the tools do to them together, and a clipboard of plain data.

``CSelection`` holds the entries of one ``CEditSession`` (add, toggle, a box, every entry of one model) and moves,
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
from ..suite.undo import SCommand
from .editchange import copy_json, yaw_quat
from .editsession import CARRIED, CEditSession, SEntry, label_of

CLIPBOARD_VERSION = 1


@contextmanager
def one_change(session: CEditSession, label: str) -> Iterator[None]:
    """Every change pushed inside the block becomes one step of the undo stack, named ``label``."""
    stack = session.m_undo
    start = stack.m_index
    try:
        yield
    finally:
        cmds = stack.m_commands[start : stack.m_index]
        if len(cmds) > 1:
            del stack.m_commands[start:]
            stack.m_index = start

            def undo():
                for command in reversed(cmds):
                    command.undo()

            def redo():
                for command in cmds:
                    command.redo()

            stack.push(SCommand(label, undo, redo))
        elif cmds:
            cmds[0].label = label


class CSelection:
    """The selected entries of a session, in the order they were picked (the first one is the primary)."""

    def __init__(self, session: CEditSession) -> None:
        self.m_session = session
        self._m_entries: list[SEntry] = []

    @property
    def entries(self) -> list[SEntry]:
        """What is selected and still on the island (an undone placement drops out)."""
        self._m_entries = [entry for entry in self._m_entries if entry.alive and entry.visible]
        return list(self._m_entries)

    @property
    def primary(self) -> SEntry | None:
        rows = self.entries
        return rows[0] if rows else None

    def __len__(self) -> int:
        return len(self.entries)

    def set(self, entries: Iterable[SEntry | None]) -> None:
        self._m_entries = []
        for entry in entries:
            if entry is not None and entry not in self._m_entries:
                self._m_entries.append(entry)

    def add(self, entry: SEntry) -> None:
        if entry not in self._m_entries:
            self._m_entries.append(entry)

    def toggle(self, entry: SEntry) -> None:
        if entry in self._m_entries:
            self._m_entries.remove(entry)
        else:
            self._m_entries.append(entry)

    def clear(self) -> None:
        self._m_entries = []

    def box(self, x0: float, z0: float, x1: float, z1: float, layers: Iterable[str] | None = None) -> int:
        """Select every shown entry whose place lies in the box (Anno x and z, either corner first); ``layers``
        limits it to those layers. Returns how many."""
        xa, xb, za, zb = min(x0, x1), max(x0, x1), min(z0, z1), max(z0, z1)
        keep = set(layers) if layers is not None else None
        self.set(
            entry
            for entry in self.m_session.m_entries
            if entry.shown
            and (keep is None or entry.layer in keep)
            and xa <= entry.position[0] <= xb
            and za <= entry.position[2] <= zb
        )
        return len(self._m_entries)

    def same_model(self, entry: SEntry) -> int:
        """Select every shown entry of ``entry``'s model (or editor-only asset). Returns how many."""
        self.set(x for x in self.m_session.m_entries if x.shown and x.batch == entry.batch)
        return len(self._m_entries)

    def centre(self) -> list[float]:
        rows = self.entries
        if not rows:
            return [0.0, 0.0, 0.0]
        return np.mean([entry.position for entry in rows], axis=0).tolist()

    def _editable(self) -> list[SEntry]:
        return [] if self.m_session.m_readonly else [entry for entry in self.entries if entry.kind != "tree"]

    def move(self, delta) -> bool:
        """Move everything by ``delta`` (x, y, z) as one change."""
        rows = self._editable()
        start = self.m_session.begin_transform(rows)
        for entry in rows:
            self.m_session.set_transform(entry, position=np.add(entry.position, delta).tolist())
        return self.m_session.end_transform(start, self._label(tr("islands.move_lower")))

    def turn(self, yaw_rad: float, about=None) -> bool:
        """Turn everything by ``yaw`` (radians, about the vertical) round ``about`` (default: the centre)."""
        rows = self._editable()
        quaternion = yaw_quat(yaw_rad)
        pivot = np.asarray(self.centre() if about is None else about, float)
        start = self.m_session.begin_transform(rows)
        for entry in rows:
            position = pivot + meshxform.qrotate(tuple(quaternion), tuple(np.asarray(entry.position) - pivot))
            self.m_session.set_transform(
                entry,
                position=position.tolist(),
                rotation=list(meshxform.qmul(tuple(quaternion), tuple(entry.rotation))),
            )
        return self.m_session.end_transform(start, self._label(tr("islands.turn_lower")))

    def scale(self, factor: float, about=None) -> bool:
        """Scale everything and the distances between them by ``factor`` round ``about`` (default: the centre)."""
        rows = self._editable()
        pivot = np.asarray(self.centre() if about is None else about, float)
        start = self.m_session.begin_transform(rows)
        for entry in rows:
            position = pivot + (np.asarray(entry.position) - pivot) * factor
            new_scale = (
                [axis_scale * factor for axis_scale in entry.scale]
                if isinstance(entry.scale, list)
                else entry.scale * factor
            )
            self.m_session.set_transform(entry, position=position.tolist(), scale=new_scale)
        return self.m_session.end_transform(start, self._label(tr("common.scale")))

    def delete(self) -> int:
        rows = [entry for entry in self.entries if not self.m_session.m_readonly]
        with one_change(self.m_session, self._label(tr("islands.delete"))):
            for entry in rows:
                self.m_session.delete(entry)
        self.clear()
        return len(rows)

    def duplicate(self) -> list[SEntry]:
        """A copy of everything two tiles off; the copies become the selection."""
        with one_change(self.m_session, self._label(tr("islands.duplicate_lower"))):
            copies = [
                copy_entry
                for copy_entry in (self.m_session.duplicate(entry) for entry in self._editable())
                if copy_entry is not None
            ]
        self.set(copies)
        return copies

    def _label(self, verb: str) -> str:
        count = len(self._m_entries)
        return verb if count == 1 else tr("islands.verb_n", verb=verb, n=count)


def _item_of(entry: SEntry) -> dict:
    """What a pasted copy of ``entry`` needs to be placed again: its model or editor-only asset."""
    base = entry.base
    item = {"kind": entry.kind}
    if entry.cfg:
        item["cfg"] = entry.cfg
    for k in ("guid", "template", *CARRIED):
        if base.get(k) is not None:
            item[k] = base[k]
    if entry.marker and not base.get("clone"):  # a clone's marker is "NoModel", its template stays
        item["template"] = entry.marker
    item["name"] = label_of(base)
    item["clone"] = bool(base.get("clone"))
    return item


def copy(entries: Iterable[SEntry]) -> dict:
    """A clipboard of ``entries`` (no forest trees): each item with its model, its offset from their centre, its
    turn and scale."""
    rows = [entry for entry in entries if entry.kind != "tree" and entry.alive and entry.visible]
    if not rows:
        return {"version": CLIPBOARD_VERSION, "items": []}
    centre = np.mean([entry.position for entry in rows], axis=0)
    items = []
    for entry in rows:
        item = _item_of(entry)
        item["offset"] = (np.asarray(entry.position) - centre).tolist()
        item["rotation"] = list(entry.rotation)
        item["scale"] = copy_json(entry.scale)
        if entry.color is not None:
            item["color"] = list(entry.color)
        items.append(item)
    return {"version": CLIPBOARD_VERSION, "items": items}


def _pick_of(item: dict) -> dict:
    """The library pick ``CEditSession.place`` takes for a clipboard item: an editor-only asset or a copy of an
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


def paste(session: CEditSession, clipboard: dict, at, yaw_rad: float = 0.0, on_ground: bool = False) -> list[SEntry]:
    """Place the clipboard's items round ``at`` (x, y, z), the whole turned by ``yaw``, as one change; with
    ``on_ground`` each lands on the ground under it. Props come back as props. Returns the new entries."""
    if session.m_readonly or clipboard.get("version") != CLIPBOARD_VERSION:
        return []
    quaternion = yaw_quat(yaw_rad)
    out = []
    count = len(clipboard["items"])
    with one_change(session, tr("islands.paste_lower") if count == 1 else tr("islands.paste_n", n=count)):
        for item in clipboard["items"]:
            position = np.asarray(at, float) + meshxform.qrotate(tuple(quaternion), tuple(item["offset"]))
            if on_ground:
                position[1] = session.height_at(position[0], position[2])
            rot = list(meshxform.qmul(tuple(quaternion), tuple(item["rotation"])))
            if item["kind"] == "prop":
                record = {"cfg": item["cfg"], "position": position.tolist(), "rotation": rot}
                if item.get("color"):
                    record["color"] = list(item["color"])
                entry = session.add_record("prop", record)
            else:
                entry = session.place(_pick_of(item), position.tolist())
            if entry is None:
                continue
            session.set_transform(entry, rotation=rot, scale=item.get("scale", 1.0))
            out.append(entry)
    return out
