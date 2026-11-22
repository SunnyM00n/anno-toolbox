"""The music forms of the sound editor's window (Qt): a segment's markers, a container's transition rules and how a
replaced track clip moves them.

``CMusicPanel(edit)`` shows the rows that fit the picked object (``load``); ``edit(name, **args)`` is the window's edit
call. The text forms are ``parse_markers`` / ``markers_text`` and ``parse_fields`` (no Qt use, tested alone). The edits
are ``musicedits``; docs/sound-editor.md "Music".
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable

from PySide6.QtWidgets import QComboBox, QFormLayout, QLineEdit, QSpinBox, QWidget

from ..formats import bnkmusic
from ..formats.bnk import fnv1_32
from ..infra.i18n import N_, tr
from . import formrows, musicedits
from .bankdoc import CBankDoc

RULES_MOST = (
    65535  # the spin box's own 99 is five past what 117 ships (95 rules in one container): added ones were out of reach
)


def markers_text(markers: list[bnkmusic.SMarker]) -> str:
    """``id:ms`` per marker, ``id:ms:name`` for a named one."""
    return ", ".join(
        f"{marker.id}:{formrows.number_text(marker.position)}"
        + (f":{marker.name.decode('utf-8', 'replace')}" if marker.name else "")
        for marker in markers
    )


def parse_markers(text: str) -> list[tuple[int, float, str]]:
    """(id, ms, name) of ``id:ms[:name]`` entries; an entry that starts with a name instead of an id (``Verse:4000``)
    gets the id Wwise gives a cue of that name (the FNV-1 hash of the lower-case name). ``ValueError`` for the rest."""
    out = []
    for part in formrows.entries(text):
        head, _, rest = part.partition(":")
        position, _, name = rest.partition(":")
        ms = float(position)
        if not (math.isfinite(ms) and head.strip()):
            raise ValueError(part)
        if head.strip().isdigit():
            out.append((int(head), ms, name.strip()))
        else:
            out.append((fnv1_32(head.strip()), ms, head.strip()))
    return out


def parse_fields(text: str) -> dict:
    """``field=value`` entries (``sync_type=7, source_ids=30;31``) as an edit's fields: numbers, and ``;`` lists for the node
    ids ``source_ids`` and ``destination_ids``. ``ValueError`` for an entry that is not one."""
    out: dict = {}
    for part in formrows.entries(text):
        key, sep, value = part.partition("=")
        if not sep:
            raise ValueError(part)
        out[key.strip()] = (
            [int(item) for item in value.split(";") if item.strip()]
            if key.strip() in musicedits.RULE_LISTS
            else int(value)
        )
    return out


class CMusicPanel(QWidget):
    def __init__(self, edit: Callable[..., bool], say: Callable[[str], None]) -> None:
        super().__init__()
        self.m_edit, self.m_say = edit, say
        self.m_doc: CBankDoc | None = None
        self.m_picked = 0
        self.m_markers = QLineEdit(placeholderText=tr("common.id_ms_name"))
        self.m_rule = QSpinBox()
        self.m_rule.setMaximum(RULES_MOST)
        self.m_fields = QLineEdit(placeholderText=tr("common.sync_type_eq_7_src"))
        self.m_fit = QComboBox()
        self.m_fit.addItems([musicedits.SCALE, musicedits.KEEP])
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.m_rows = {
            "markers": (tr("common.markers_ms"), self.m_markers, ((N_("common.apply_markers"), self._apply_markers),)),
            "rules": (
                tr("common.transition_rule"),
                self.m_rule,
                (
                    (N_("common.apply_to_rule"), self._apply_rule),
                    (N_("common.add_rule_copy"), self._add_rule),
                    (N_("common.remove_rule"), self._remove_rule),
                ),
            ),
            "fit": (tr("common.markers_follow_new_clip"), self.m_fit, ()),
        }
        self.m_widgets: dict[str, list[QWidget]] = {}
        for key, (label, widget, buttons) in self.m_rows.items():
            row = [widget]
            form.addRow(label, widget)
            if key == "rules":
                form.addRow(tr("common.fields_rule"), self.m_fields)
                row.append(self.m_fields)
            row += formrows.add_buttons(form, buttons)
            self.m_widgets[key] = row
        self.load(None, 0)

    def load(self, doc: CBankDoc | None, obj_id: int) -> None:
        """Show the rows of the picked object: markers for a segment, rules for a playlist or switch container, the
        fit for a track."""
        self.m_doc, self.m_picked = doc, obj_id
        kind = doc.kind_of(obj_id) if doc is not None and obj_id else 0
        show = {
            "markers": kind == bnkmusic.MUSIC_SEGMENT,
            "rules": kind in (bnkmusic.MUSIC_RANSEQ_CNTR, bnkmusic.MUSIC_SWITCH_CNTR),
            "fit": kind == bnkmusic.MUSIC_TRACK,
        }
        for key, widgets in self.m_widgets.items():
            for widget in widgets:
                widget.setVisible(show[key])
        for label_key in ("markers", "rules", "fit"):
            label = self.layout().labelForField(self.m_rows[label_key][1])
            if label is not None:
                label.setVisible(show[label_key])
        if show["markers"]:
            try:
                self.m_markers.setText(markers_text(bnkmusic.parse_segment(doc.hirc_object(obj_id)).m_markers))
            except (ValueError, struct.error):  # a bank whose music is not read: the edit says so
                self.m_markers.setText("")
        self.setVisible(any(show.values()))

    def _apply_markers(self) -> None:
        try:
            entries = parse_markers(self.m_markers.text())
        except ValueError:
            self.m_say(tr("common.write_markers_id_ms_name"))
            return
        self.m_edit("set_markers", segment=self.m_picked, markers=entries)

    def _fields(self) -> dict | None:
        try:
            return parse_fields(self.m_fields.text())
        except ValueError:
            self.m_say(tr("common.write_fields_name_eq_number"))
            return None

    def _apply_rule(self) -> None:
        if (fields := self._fields()) is not None:
            self.m_edit("set_transition", container=self.m_picked, index=self.m_rule.value(), fields=fields)

    def _add_rule(self) -> None:
        if (fields := self._fields()) is not None:
            self.m_edit("add_transition", container=self.m_picked, copy=self.m_rule.value(), fields=fields)

    def _remove_rule(self) -> None:
        self.m_edit("remove_transition", container=self.m_picked, index=self.m_rule.value())
