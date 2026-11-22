"""The edits of a music bank's timing (Anno 117's banks): a segment's markers, a container's transition rules, and the
fit of a segment to a track's clip of another length (``audioedits.replace_track_audio``).

Each ``@edit`` takes plain arguments like the others (``edits``); the layouts and the rule the fit follows are in
docs/sound/bank-objects.md "Music: segments, tracks, markers and transition rules", the user's side in
docs/sound-editor.md "Music".
"""

from __future__ import annotations

import math
import struct
from typing import TYPE_CHECKING

from ..formats import bnkgraph, bnkmusic
from ..formats.bnk import SHircObject
from ..infra.i18n import N_, tr
from .registry import CEditError, check_index, edit, reading

if TYPE_CHECKING:
    from .bankdoc import CBankDoc

CUE_ENTRY, CUE_EXIT = 43573010, 1539036744  # the ids every segment's entry and exit cue has
SCALE, KEEP = "scale", "keep"  # how a clip of another length moves the trims and the markers
RULE_FIELDS = (
    "src_fade_time", "src_fade_curve", "src_fade_offset", "sync_type", "cue_filter", "play_post_exit",
    "dst_fade_time", "dst_fade_curve", "dst_fade_offset", "dst_cue_filter", "jump_to_id", "jump_to_type",
    "entry_type", "play_pre_entry", "dst_match_cue",
)  # fmt: skip
RULE_LISTS = ("source_ids", "destination_ids")
TOLERANCE = 1.0  # ms: a segment is its track's visible part when they differ by less than this


def _readable(doc: CBankDoc) -> None:
    if not bnkgraph.layout(doc.m_version).music_readable:
        raise CEditError(tr("common.music_edited_banks_anno_117"))


def _segment(doc: CBankDoc, segment: int) -> bnkmusic.CMusicSegment:
    _readable(doc)
    hirc_object = doc.hirc_object(segment)
    if hirc_object.type != bnkmusic.MUSIC_SEGMENT:
        raise CEditError(tr("common.not_music_segment", type_name=hirc_object.type_name, segment=segment))
    with reading(tr("sound.segment", segment=segment)):
        return bnkmusic.parse_segment(hirc_object)


def _name(name: str | bytes) -> bytes:
    raw = name.encode("utf-8") if isinstance(name, str) else bytes(name)
    if b"\0" in raw:
        raise CEditError(tr("common.markers_name_no_zero_byte"))
    return raw


def _checked(seg: bnkmusic.CMusicSegment, markers: list[bnkmusic.SMarker]) -> None:
    ids = [marker.id for marker in markers]
    if len(set(ids)) != len(ids):
        raise CEditError(tr("common.two_markers_same_id"))
    if CUE_ENTRY not in ids or CUE_EXIT not in ids:
        raise CEditError(tr("common.segment_keeps_entry_exit_cues"))
    for marker in markers:
        if not (math.isfinite(marker.position) and 0 <= marker.position <= seg.m_duration):
            raise CEditError(
                tr(
                    "common.marker_lies_ms_outside_segments",
                    marker=marker.id,
                    position=marker.position,
                    duration=seg.m_duration,
                )
            )


# markers


@edit(N_("sound.change_markers"))
def set_markers(doc: CBankDoc, segment: int, markers: list) -> None:
    """A segment's markers as (id, position in ms, name) entries in order; the entry and exit cues stay, every
    position lies within the segment."""
    seg = _segment(doc, segment)
    try:
        seg.m_markers = [bnkmusic.SMarker(int(i), float(position), _name(name)) for i, position, name in markers]
    except (TypeError, ValueError) as exc:
        raise CEditError(tr("common.write_markers_id_position_name")) from exc
    _checked(seg, seg.m_markers)
    doc.put(bnkmusic.render_segment(seg))


def _marker_list(doc: CBankDoc, segment: int) -> list[tuple[int, float, bytes]]:
    return [(marker.id, marker.position, marker.name) for marker in _segment(doc, segment).m_markers]


@edit(N_("sound.change_marker"))
def set_marker(
    doc: CBankDoc, segment: int, marker: int, position: float | None = None, label: str | None = None
) -> None:
    """One marker of a segment: its position (ms) and its name (``label``)."""
    rows = _marker_list(doc, segment)
    if marker not in [row[0] for row in rows]:
        raise CEditError(tr("common.segment_no_marker", segment=segment, marker=marker))
    set_markers(
        doc,
        segment,
        [
            (
                i,
                row_position if i != marker or position is None else position,
                name if i != marker or label is None else label,
            )
            for i, row_position, name in rows
        ],
    )


@edit(N_("sound.add_marker"))
def add_marker(doc: CBankDoc, segment: int, marker: int, position: float, label: str = "") -> None:
    """A custom marker, before the exit cue."""
    rows = _marker_list(doc, segment)
    at = next((k for k, row in enumerate(rows) if row[0] == CUE_EXIT), len(rows))
    set_markers(doc, segment, [*rows[:at], (marker, position, label), *rows[at:]])


@edit(N_("sound.remove_marker"))
def remove_marker(doc: CBankDoc, segment: int, marker: int) -> None:
    """A custom marker out of a segment (the entry and exit cues stay)."""
    rows = _marker_list(doc, segment)
    if marker not in [row[0] for row in rows]:
        raise CEditError(tr("common.segment_no_marker", segment=segment, marker=marker))
    if marker in (CUE_ENTRY, CUE_EXIT):
        raise CEditError(tr("common.segment_keeps_entry_exit_cues"))
    set_markers(doc, segment, [row for row in rows if row[0] != marker])


# transition rules


def _container(doc: CBankDoc, container: int) -> tuple[SHircObject, int, int, list[bnkmusic.STransRule]]:
    _readable(doc)
    hirc_object = doc.hirc_object(container)
    if hirc_object.type not in (bnkmusic.MUSIC_RANSEQ_CNTR, bnkmusic.MUSIC_SWITCH_CNTR):
        raise CEditError(
            tr(
                "common.not_music_playlist_switch_container",
                type_name=hirc_object.type_name,
                container=container,
            )
        )
    with reading(tr("sound.rule_list_container", container=container)):
        start, end = bnkmusic.locate_trans_rules(hirc_object.payload)
        return hirc_object, start, end, bnkmusic.parse_trans_rules(bnkmusic.CReader(hirc_object.payload[start:end]))


def _put_rules(doc: CBankDoc, hirc_object: SHircObject, start: int, end: int, rules: list[bnkmusic.STransRule]) -> None:
    try:
        body = bnkmusic.render_trans_rules(rules)
    except (struct.error, TypeError) as exc:
        raise CEditError(tr("common.rules_value_out_range", exc=exc)) from exc
    doc.splice(hirc_object, start, end, body)


def _fill(rule: bnkmusic.STransRule, fields: dict) -> None:
    bad = [k for k in fields if k not in (*RULE_FIELDS, *RULE_LISTS)]
    if bad:
        raise CEditError(tr("common.rule_no_field", value=bad[0], value2=", ".join(RULE_FIELDS)))
    for key, value in fields.items():
        setattr(rule, key, [int(item) for item in value] if key in RULE_LISTS else int(value))


@edit(N_("sound.change_transition_rule"))
def set_transition(doc: CBankDoc, container: int, index: int, fields: dict) -> None:
    """Fields of one transition rule of a Music Playlist or Music Switch container (``RULE_FIELDS``, and the node id
    lists ``source_ids`` and ``destination_ids``)."""
    hirc_object, start, end, rules = _container(doc, container)
    _fill(rules[check_index(rules, index, tr("islands.rules"))], fields)
    _put_rules(doc, hirc_object, start, end, rules)


@edit(N_("sound.add_transition_rule"))
def add_transition(doc: CBankDoc, container: int, copy: int = 0, fields: dict | None = None) -> None:
    """A new rule at the end: a copy of rule ``copy`` with ``fields`` changed."""
    hirc_object, start, end, rules = _container(doc, container)
    source = rules[check_index(rules, copy, tr("islands.rules"))]
    rule = bnkmusic.STransRule(
        **{**vars(source), "source_ids": list(source.source_ids), "destination_ids": list(source.destination_ids)}
    )
    _fill(rule, fields or {})
    _put_rules(doc, hirc_object, start, end, [*rules, rule])


@edit(N_("sound.remove_transition_rule"))
def remove_transition(doc: CBankDoc, container: int, index: int) -> None:
    """A rule out of a container."""
    hirc_object, start, end, rules = _container(doc, container)
    check_index(rules, index, tr("islands.rules"))
    _put_rules(doc, hirc_object, start, end, [rule for i, rule in enumerate(rules) if i != index])


# a track's clip of another length


def visible(item: bnkmusic.SPlaylistItem) -> float:
    """The part of its clip (ms) a track's playlist item plays."""
    return item.source_duration_ms - item.begin_trim + item.end_trim


def retime(item: bnkmusic.SPlaylistItem, new_ms: float, mode: str) -> None:
    """A playlist item whose clip is now ``new_ms`` long. ``scale``: the trims and the start follow the clip's length;
    ``keep``: they stay (an ``CEditError`` when they would cut away the whole clip)."""
    if mode not in (SCALE, KEEP):
        raise CEditError(tr("common.markers_go_clip", value=SCALE, value2=KEEP))
    old = item.source_duration_ms
    if mode == SCALE and old > 0:
        ratio = new_ms / old
        item.begin_trim, item.end_trim, item.play_at = (
            item.begin_trim * ratio,
            item.end_trim * ratio,
            item.play_at * ratio,
        )
    item.source_duration_ms = new_ms
    if visible(item) <= 0:
        raise CEditError(tr("common.trims_cut_away_whole_new"))


def _moved(position: float, old: float, new: float, mode: str) -> float:
    """A marker's new position when the segment goes from ``old`` to ``new`` ms long."""
    if abs(position - old) < TOLERANCE / 2:
        return new  # whatever stood at the end stays at the end
    return min(position, new) if mode == KEEP else position * new / old


def fit_segment(doc: CBankDoc, track: int, old: float, new: float, mode: str) -> str:
    """The segment of ``track`` made as long as the track's playing part now is, its markers moved (``retime``); the
    sentence for the status line ("" when the track has no segment)."""
    for hirc_object in doc.objects_of(bnkmusic.MUSIC_SEGMENT):
        seg = bnkmusic.parse_segment(hirc_object)
        if track not in seg.m_children:
            continue
        if abs(seg.m_duration - old) > TOLERANCE or old <= 0:
            return tr("common.segment_not_tracks_length_markers", segment=hirc_object.id)
        for marker in seg.m_markers:
            marker.position = _moved(marker.position, seg.m_duration, new, mode)
        seg.m_duration = new
        doc.put(bnkmusic.render_segment(seg))
        return tr(
            "common.segment_now_ms_long_markers",
            segment=hirc_object.id,
            length=round(new),
            count=len(seg.m_markers),
        )
    return ""


# what the window shows


def cue_name(marker: bnkmusic.SMarker) -> str:
    """A marker's name: the cues' own, the name in the bank, else its id."""
    known = {CUE_ENTRY: "Entry Cue", CUE_EXIT: "Exit Cue"}
    return known.get(marker.id) or marker.name.decode("utf-8", "replace") or str(marker.id)


def track_marks(doc: CBankDoc, track: int) -> list[tuple[float, str]]:
    """The markers of the segment a music track plays in, as (seconds into the track's clip, name): the clip starts
    ``play_at`` ms into the segment, so a marker at segment time t sits at t - play_at in the clip."""
    if not bnkgraph.layout(doc.m_version).music_readable or doc.hirc_object(track).type != bnkmusic.MUSIC_TRACK:
        return []
    try:
        item = bnkmusic.parse_track(doc.hirc_object(track)).m_playlist[0]
        for hirc_object in doc.objects_of(bnkmusic.MUSIC_SEGMENT):
            seg = bnkmusic.parse_segment(hirc_object)
            if track in seg.m_children:
                return [((marker.position - item.play_at) / 1000.0, cue_name(marker)) for marker in seg.m_markers]
    except (ValueError, struct.error, IndexError):
        pass
    return []


def rule_lines(doc: CBankDoc, container: int) -> list[str]:
    """One line per transition rule of a music container (what the detail panel lists)."""
    try:
        *_head, rules = _container(doc, container)
    except CEditError:
        return []
    return [
        tr(
            "common.rule_sync_fade_ms_curve",
            index=i,
            src=_nodes(rule.source_ids),
            dst=_nodes(rule.destination_ids),
            sync=rule.sync_type,
            fade=rule.src_fade_time,
            curve=rule.src_fade_curve,
            entry=rule.entry_type,
        )
        for i, rule in enumerate(rules)
    ]


def _nodes(ids: list[int]) -> str:
    return ";".join(str(i) for i in ids[:3]) + ("..." if len(ids) > 3 else "")
