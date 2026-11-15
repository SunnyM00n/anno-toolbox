"""The music forms of the sound editor's window (Qt): a segment's markers, a container's transition rules and how a
replaced track clip moves them.

``MusicPanel(edit)`` shows the rows that fit the picked object (``load``); ``edit(name, **args)`` is the window's edit
call. The text forms are ``parse_markers`` / ``markers_text`` and ``parse_fields`` (no Qt use, tested alone). The edits
are ``musicedits``; docs/sound-editor.md "Music".
"""

from __future__ import annotations

import struct
from collections.abc import Callable

from PySide6.QtWidgets import QComboBox, QFormLayout, QLineEdit, QSpinBox, QWidget

from ..formats import bnkmusic
from ..formats.bnk import fnv1_32
from ..infra.i18n import N_, tr
from . import formrows, musicedits
from .bankdoc import BankDoc


def markers_text(markers: list[bnkmusic.Marker]) -> str:
    """``id:ms`` per marker, ``id:ms:name`` for a named one."""
    return ", ".join(
        f"{m.id}:{m.position:g}" + (f":{m.name.decode('utf-8', 'replace')}" if m.name else "") for m in markers
    )


def parse_markers(text: str) -> list[tuple[int, float, str]]:
    """(id, ms, name) of ``id:ms[:name]`` entries; an entry that starts with a name instead of an id (``Verse:4000``)
    gets the id Wwise gives a cue of that name (the FNV-1 hash of the lower-case name). ``ValueError`` for the rest."""
    out = []
    for part in formrows.entries(text):
        head, _, rest = part.partition(":")
        position, _, name = rest.partition(":")
        if head.strip().isdigit():
            out.append((int(head), float(position), name.strip()))
        else:
            out.append((fnv1_32(head.strip()), float(position), head.strip()))
    return out


def parse_fields(text: str) -> dict:
    """``field=value`` entries (``sync_type=7, src=30;31``) as an edit's fields: numbers, and ``;`` lists for the node
    ids ``src`` and ``dst``. ``ValueError`` for an entry that is not one."""
    out: dict = {}
    for part in formrows.entries(text):
        key, sep, value = part.partition("=")
        if not sep:
            raise ValueError(part)
        out[key.strip()] = (
            [int(v) for v in value.split(";") if v.strip()] if key.strip() in musicedits.RULE_LISTS else int(value)
        )
    return out


class MusicPanel(QWidget):
    def __init__(self, edit: Callable[..., bool], say: Callable[[str], None]) -> None:
        super().__init__()
        self.edit, self.say = edit, say
        self.doc: BankDoc | None = None
        self.picked = 0
        self.markers = QLineEdit(placeholderText=tr("id:ms, id:ms:name ... (or name:ms)"))
        self.rule = QSpinBox()
        self.fields = QLineEdit(placeholderText=tr("sync_type=7, src_fade_time=3000 ..."))
        self.fit = QComboBox()
        self.fit.addItems([musicedits.SCALE, musicedits.KEEP])
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.rows = {
            "markers": (tr("markers (ms)"), self.markers, ((N_("Apply the markers"), self._apply_markers),)),
            "rules": (
                tr("transition rule"),
                self.rule,
                (
                    (N_("Apply to the rule"), self._apply_rule),
                    (N_("Add a rule (copy)"), self._add_rule),
                    (N_("Remove the rule"), self._remove_rule),
                ),
            ),
            "fit": (tr("markers follow a new clip by"), self.fit, ()),
        }
        self.widgets: dict[str, list[QWidget]] = {}
        for key, (label, widget, buttons) in self.rows.items():
            row = [widget]
            form.addRow(label, widget)
            if key == "rules":
                form.addRow(tr("fields of the rule"), self.fields)
                row.append(self.fields)
            row += formrows.add_buttons(form, buttons)
            self.widgets[key] = row
        self.load(None, 0)

    def load(self, doc: BankDoc | None, obj_id: int) -> None:
        """Show the rows of the picked object: markers for a segment, rules for a playlist or switch container, the
        fit for a track."""
        self.doc, self.picked = doc, obj_id
        kind = doc.kind_of(obj_id) if doc is not None and obj_id else 0
        show = {
            "markers": kind == bnkmusic.MUSIC_SEGMENT,
            "rules": kind in (bnkmusic.MUSIC_RANSEQ_CNTR, bnkmusic.MUSIC_SWITCH_CNTR),
            "fit": kind == bnkmusic.MUSIC_TRACK,
        }
        for key, widgets in self.widgets.items():
            for w in widgets:
                w.setVisible(show[key])
        for label_key in ("markers", "rules", "fit"):
            label = self.layout().labelForField(self.rows[label_key][1])
            if label is not None:
                label.setVisible(show[label_key])
        if show["markers"]:
            try:
                self.markers.setText(markers_text(bnkmusic.parse_segment(doc.obj(obj_id)).markers))
            except (ValueError, struct.error):  # a bank whose music is not read: the edit says so
                self.markers.setText("")
        self.setVisible(any(show.values()))

    def _apply_markers(self) -> None:
        try:
            entries = parse_markers(self.markers.text())
        except ValueError:
            self.say(tr("write the markers as id:ms or name:ms, separated by commas"))
            return
        self.edit("set_markers", segment=self.picked, markers=entries)

    def _fields(self) -> dict | None:
        try:
            return parse_fields(self.fields.text())
        except ValueError:
            self.say(tr("write the fields as name=number, separated by commas"))
            return None

    def _apply_rule(self) -> None:
        if (fields := self._fields()) is not None:
            self.edit("set_transition", container=self.picked, index=self.rule.value(), fields=fields)

    def _add_rule(self) -> None:
        if (fields := self._fields()) is not None:
            self.edit("add_transition", container=self.picked, copy=self.rule.value(), fields=fields)

    def _remove_rule(self) -> None:
        self.edit("remove_transition", container=self.picked, index=self.rule.value())
