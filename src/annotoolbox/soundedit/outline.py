"""What the sound editor lists, without Qt: a bank's events, its hierarchy of containers and sounds, its music; the
title of an object (its name from the bank's info file, else its kind and id) and the facts the detail panel shows.

``outline(doc)`` is the entry: the three lists as ``Row`` trees; ``describe(doc, obj_id)`` the lines of one object.
The window that shows them and the guide: ``soundwindow``, docs/sound-editor.md.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..exporting.banks import PLUGINS, STREAMS
from ..formats import bnkgraph, bnkmusic
from ..formats.bnk import HircObject
from ..infra.i18n import N_, tr
from . import curveedits, groupedits, musicedits, nodebase
from .bankdoc import BankDoc

MUSIC = (bnkmusic.MUSIC_SEGMENT, bnkmusic.MUSIC_TRACK, bnkmusic.MUSIC_SWITCH_CNTR, bnkmusic.MUSIC_RANSEQ_CNTR)


@dataclass
class Row:
    id: int
    title: str
    children: list[Row] = field(default_factory=list)


def title_of(doc: BankDoc, obj: HircObject) -> str:
    name = doc.info.events.get(obj.id, "") if obj.type == bnkgraph.EVENT else ""
    return f"{name or obj.type_name} {obj.id}" if not name else f"{name} ({obj.id})"


def _tree(doc: BankDoc, obj: HircObject, seen: set[int]) -> Row:
    node = doc.graph.get(obj.id)
    row = Row(obj.id, title_of(doc, obj))
    seen.add(obj.id)
    for child in node.children if node is not None else []:
        if child not in seen and any(o.id == child for o in doc.bank.hirc):
            row.children.append(_tree(doc, doc.obj(child), seen))
    return row


def outline(doc: BankDoc) -> dict[str, list[Row]]:
    events = [Row(o.id, title_of(doc, o)) for o in doc.objects_of(bnkgraph.EVENT)]
    events.sort(key=lambda r: r.title)
    seen: set[int] = set()
    roots = []
    for o in doc.bank.hirc:
        node = doc.graph.get(o.id)
        if node is not None and (node.parent == 0 or node.parent not in doc.graph):
            roots.append(_tree(doc, o, seen))
    rest = [_tree(doc, o, seen) for o in doc.bank.hirc if o.id in doc.graph and o.id not in seen]
    music = [Row(o.id, title_of(doc, o)) for o in doc.bank.hirc if o.type in MUSIC]
    out = {N_("Events"): events, N_("Sounds and containers"): roots + rest, N_("Music"): music}
    if groups := groupedits.group_rows(doc):  # the Init bank's state manager
        out[N_("Groups")] = [Row(ident, title) for ident, title in groups]
    return out


def music_lines(doc: BankDoc, obj: HircObject) -> list[str]:
    """What a music segment (length, markers), track (its playlist items) or container (its transition rules) holds."""
    if not bnkgraph.layout(doc.version).music_readable:
        return []
    try:
        if obj.type == bnkmusic.MUSIC_SEGMENT:
            seg = bnkmusic.parse_segment(obj)
            return [tr("{length} ms long, {count} markers", length=round(seg.duration, 1), count=len(seg.markers))] + [
                tr(
                    "marker {name} ({id}) at {position} ms",
                    name=musicedits.cue_name(m),
                    id=m.id,
                    position=round(m.position, 1),
                )
                for m in seg.markers
            ]
        if obj.type == bnkmusic.MUSIC_TRACK:
            return [
                tr(
                    "plays media {media} from {play_at} ms, trims {begin} and {end} ms, clip {clip} ms",
                    media=i.source_id & 0xFFFFFFFF,
                    play_at=round(i.play_at, 1),
                    begin=round(i.begin_trim, 1),
                    end=round(i.end_trim, 1),
                    clip=round(i.src_duration, 1),
                )
                for i in bnkmusic.parse_track(obj).playlist
            ]
    except (ValueError, struct.error):
        return []
    return (
        musicedits.rule_lines(doc, obj.id)
        if obj.type in (bnkmusic.MUSIC_RANSEQ_CNTR, bnkmusic.MUSIC_SWITCH_CNTR)
        else []
    )


def _graph_lines(doc: BankDoc, node: bnkgraph.Node) -> list[str]:
    """Parent, children, source and switch group of an object the bank's graph has."""
    parent, count = node.parent or tr("none"), len(node.children)
    if node.exact:
        lines = [tr("parent {parent}; {count} children", parent=parent, count=count)]
    else:
        lines = [tr("parent {parent}; {count} children (not read exactly)", parent=parent, count=count)]
    if node.source is not None:
        s = node.source
        lines.append(
            tr(
                "{plugin}, {stream}, media {media}, {size} bytes",
                plugin=PLUGINS.get(s.plugin, hex(s.plugin)),
                stream=STREAMS.get(s.stream, s.stream),
                media=s.media,
                size=s.size,
            )
        )
    if node.switch is not None:
        group = doc.info.group_label(node.switch.group)
        start = doc.info.group_label(node.switch.group, node.switch.default)
        if node.switch.state_group:
            lines.append(tr("state group {group}, starts at {start}", group=group, start=start))
        else:
            lines.append(tr("switch group {group}, starts at {start}", group=group, start=start))
    return lines


def _event_lines(doc: BankDoc, obj: HircObject) -> list[str]:
    return [tr("actions {actions}", actions=bnkgraph.read_event(obj, doc.version))]


def _action_lines(doc: BankDoc, obj: HircObject) -> list[str]:
    a = bnkgraph.read_action(obj)
    kind = f"{a.kind:#06x}"
    if a.at:
        text = tr(
            "action {kind}, target {target}, group {group} value {value}",
            kind=kind,
            target=a.target,
            group=a.group,
            value=a.value,
        )
        return [text]
    return [tr("action {kind}, target {target}", kind=kind, target=a.target)]


def _property_lines(doc: BankDoc, obj: HircObject) -> list[str]:
    try:
        block = nodebase.read_props(obj, doc.version)
    except nodebase.PropError:
        return []
    lines = [f"{nodebase.name_of(doc.version, p)}: {struct.unpack('<f', v)[0]:g}" for p, v in block.props.items()]
    return lines + [
        tr("{name} random: {range}", name=nodebase.name_of(doc.version, p), range=struct.unpack("<ff", v))
        for p, v in block.ranged.items()
    ]


KIND_LINES = {bnkgraph.EVENT: _event_lines, bnkgraph.ACTION: _action_lines}  # what an object kind adds of its own


def describe(doc: BankDoc, obj_id: int) -> list[str]:
    """The facts of one object: kind, parent, children, source, what its kind adds, curves, properties."""
    if not any(o.id == obj_id for o in doc.bank.hirc) and (group := groupedits.group_lines(doc, obj_id)):
        return group
    obj = doc.obj(obj_id)
    lines = [f"{obj.type_name} {obj.id}"]
    node = doc.graph.get(obj_id)
    if node is not None:
        lines += _graph_lines(doc, node)
    if obj.type in KIND_LINES:
        lines += KIND_LINES[obj.type](doc, obj)
    lines += music_lines(doc, obj)
    lines += [
        tr("{label}: {count} points", label=r.label, count=len(r.points)) for r in curveedits.curve_refs(doc, obj_id)
    ]
    return lines + _property_lines(doc, obj)
