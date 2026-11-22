"""What the sound editor lists, without Qt: a bank's events, its hierarchy of containers and sounds, its music; the
title of an object (its name from the bank's info file, else its kind and id) and the facts the detail panel shows.

``outline(doc)`` is the entry: the three lists as ``SRow`` trees; ``describe(doc, obj_id)`` the lines of one object.
The window that shows them and the guide: ``soundwindow``, docs/sound-editor.md.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..exporting.banks import PLUGINS, STREAMS
from ..formats import bnkgraph, bnkmusic
from ..formats.bnk import SHircObject
from ..infra.i18n import N_, tr
from . import curveedits, groupedits, musicedits, nodebase
from .bankdoc import CBankDoc

MUSIC = (bnkmusic.MUSIC_SEGMENT, bnkmusic.MUSIC_TRACK, bnkmusic.MUSIC_SWITCH_CNTR, bnkmusic.MUSIC_RANSEQ_CNTR)


@dataclass
class SRow:
    id: int
    title: str
    children: list[SRow] = field(default_factory=list)


def title_of(doc: CBankDoc, hirc_object: SHircObject) -> str:
    name = doc.m_bank_info.events.get(hirc_object.id, "") if hirc_object.type == bnkgraph.EVENT else ""
    return f"{name or hirc_object.type_name} {hirc_object.id}" if not name else f"{name} ({hirc_object.id})"


def _tree(doc: CBankDoc, hirc_object: SHircObject, seen: set[int]) -> SRow:
    node = doc.graph.get(hirc_object.id)
    row = SRow(hirc_object.id, title_of(doc, hirc_object))
    seen.add(hirc_object.id)
    for child in node.children if node is not None else []:
        if child not in seen and doc.kind_of(child):  # by the index: a walk of the list per child froze big banks
            row.children.append(_tree(doc, doc.hirc_object(child), seen))
    return row


def outline(doc: CBankDoc) -> dict[str, list[SRow]]:
    events = [SRow(hirc_object.id, title_of(doc, hirc_object)) for hirc_object in doc.objects_of(bnkgraph.EVENT)]
    events.sort(key=lambda row: row.title)
    seen: set[int] = set()
    roots = []
    for hirc_object in doc.m_bank.hirc:
        node = doc.graph.get(hirc_object.id)
        if node is not None and (node.parent == 0 or node.parent not in doc.graph):
            roots.append(_tree(doc, hirc_object, seen))
    rest = [
        _tree(doc, hirc_object, seen)
        for hirc_object in doc.m_bank.hirc
        if hirc_object.id in doc.graph and hirc_object.id not in seen
    ]
    music = [
        SRow(hirc_object.id, title_of(doc, hirc_object)) for hirc_object in doc.m_bank.hirc if hirc_object.type in MUSIC
    ]
    out = {N_("common.events"): events, N_("sound.sounds_containers"): roots + rest, N_("common.music"): music}
    if groups := groupedits.group_rows(doc):  # the Init bank's state manager
        out[N_("assets.groups")] = [SRow(ident, title) for ident, title in groups]
    return out


def music_lines(doc: CBankDoc, hirc_object: SHircObject) -> list[str]:
    """What a music segment (length, markers), track (its playlist items) or container (its transition rules) holds."""
    if not bnkgraph.layout(doc.m_version).music_readable:
        return []
    try:
        if hirc_object.type == bnkmusic.MUSIC_SEGMENT:
            seg = bnkmusic.parse_segment(hirc_object)
            return [tr("sound.ms_long_markers", length=round(seg.m_duration, 1), count=len(seg.m_markers))] + [
                tr(
                    "sound.marker_name_id_position_ms",
                    name=musicedits.cue_name(marker),
                    id=marker.id,
                    position=round(marker.position, 1),
                )
                for marker in seg.m_markers
            ]
        if hirc_object.type == bnkmusic.MUSIC_TRACK:
            return [
                tr(
                    "sound.plays_media_ms_trims_clip",
                    media=i.source_id & 0xFFFFFFFF,
                    play_at=round(i.play_at, 1),
                    begin=round(i.begin_trim, 1),
                    end=round(i.end_trim, 1),
                    clip=round(i.source_duration_ms, 1),
                )
                for i in bnkmusic.parse_track(hirc_object).m_playlist
            ]
    except (ValueError, struct.error):
        return []
    return (
        musicedits.rule_lines(doc, hirc_object.id)
        if hirc_object.type in (bnkmusic.MUSIC_RANSEQ_CNTR, bnkmusic.MUSIC_SWITCH_CNTR)
        else []
    )


def _graph_lines(doc: CBankDoc, node: bnkgraph.SNode) -> list[str]:
    """Parent, children, source and switch group of an object the bank's graph has."""
    parent, count = node.parent or tr("common.none"), len(node.children)
    if node.exact:
        lines = [tr("sound.parent_count_children", parent=parent, count=count)]
    else:
        lines = [tr("sound.parent_children_not_read_exactly", parent=parent, count=count)]
    if node.source is not None:
        source = node.source
        lines.append(
            tr(
                "sound.plugin_stream_media_size_bytes",
                plugin=PLUGINS.get(source.plugin, hex(source.plugin)),
                stream=STREAMS.get(source.stream, source.stream),
                media=source.media,
                size=source.size,
            )
        )
    if node.switch is not None:
        group = doc.m_bank_info.group_label(node.switch.group)
        start = doc.m_bank_info.group_label(node.switch.group, node.switch.default)
        if node.switch.state_group:
            lines.append(tr("sound.state_group_starts", group=group, start=start))
        else:
            lines.append(tr("sound.switch_group_starts", group=group, start=start))
    return lines


def _event_lines(doc: CBankDoc, hirc_object: SHircObject) -> list[str]:
    return [tr("sound.actions", actions=bnkgraph.read_event(hirc_object, doc.m_version))]


def _action_lines(doc: CBankDoc, hirc_object: SHircObject) -> list[str]:
    action = bnkgraph.read_action(hirc_object)
    kind = f"{action.kind:#06x}"
    if action.at:
        text = tr(
            "sound.action_target_group_value",
            kind=kind,
            target=action.target,
            group=action.group,
            value=action.value,
        )
        return [text]
    return [tr("sound.action_kind_target", kind=kind, target=action.target)]


def _property_lines(doc: CBankDoc, hirc_object: SHircObject) -> list[str]:
    try:
        block = nodebase.read_props(hirc_object, doc.m_version)
    except nodebase.CPropError:
        return []
    lines = [
        f"{nodebase.name_of(doc.m_version, property_id)}: {struct.unpack('<f', raw)[0]:g}"
        for property_id, raw in block.m_props.items()
    ]
    return lines + [
        tr(
            "sound.name_random_range",
            name=nodebase.name_of(doc.m_version, property_id),
            range=struct.unpack("<ff", raw),
        )
        for property_id, raw in block.m_ranged.items()
    ]


KIND_LINES = {bnkgraph.EVENT: _event_lines, bnkgraph.ACTION: _action_lines}  # what an object kind adds of its own


def describe(doc: CBankDoc, obj_id: int) -> list[str]:
    """The facts of one object: kind, parent, children, source, what its kind adds, curves, properties."""
    if not doc.kind_of(obj_id) and (group := groupedits.group_lines(doc, obj_id)):
        return group
    hirc_object = doc.hirc_object(obj_id)
    lines = [f"{hirc_object.type_name} {hirc_object.id}"]
    node = doc.graph.get(obj_id)
    if node is not None:
        lines += _graph_lines(doc, node)
    if hirc_object.type in KIND_LINES:
        lines += KIND_LINES[hirc_object.type](doc, hirc_object)
    lines += music_lines(doc, hirc_object)
    lines += [
        tr("sound.label_count_points", label=ref.label, count=len(ref.points))
        for ref in curveedits.curve_refs(doc, obj_id)
    ]
    return lines + _property_lines(doc, hirc_object)
