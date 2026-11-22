"""The edits that change what a bank's objects say: properties, containers, switches, events and actions, music playlists.

Each ``@edit`` function takes the open ``CBankDoc`` and plain arguments (ids, numbers, lists), so a list of them can be
made again on another copy of the bank; it raises ``CEditError`` when the bank's layout is not one it writes. What each
bank version allows: docs/sound-editor.md "What can be edited". The edits that touch audio are in ``audioedits``.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from ..formats import bnkgraph, bnkmusic, bnksfx
from ..formats.bnk import SHircObject
from ..infra.i18n import N_, tr
from . import containers, nodebase
from .registry import CEditError, check_index, edit

if TYPE_CHECKING:
    from .bankdoc import CBankDoc

DEFAULT_WEIGHT = 50  # what a new playlist entry weighs (the shipped ones: 1 to 100 in the tool, more in the files)
RAN_PARAMS = 24  # bytes before the child list of a Random/Sequence container in 117's banks
LOOPS, AVOID, SHUFFLE, SEQUENCE = 0, 18, 21, 22  # their offsets
SWITCH_HEAD = 10  # bytes before a Switch container's child list: kind u8, group u32, default u32, continuous u8
MUSIC_FIELDS = ("weight", "loop", "min_loop", "max_loop", "avoid_repeat", "use_weight", "shuffle")


def _layout(doc: CBankDoc, container: int) -> tuple[SHircObject, containers.SLayout]:
    hirc_object, node = doc.hirc_object(container), doc.graph.get(container)
    if node is None:
        raise CEditError(tr("sound.type_name_container_not", type_name=hirc_object.type_name, container=container))
    try:
        return hirc_object, containers.read_layout(hirc_object, node, doc.m_version)
    except containers.CLayoutError as exc:
        raise CEditError(str(exc)) from exc


def _props(doc: CBankDoc, obj_id: int) -> tuple[SHircObject, nodebase.CPropBlock]:
    hirc_object = doc.hirc_object(obj_id)
    try:
        return hirc_object, nodebase.read_props(hirc_object, doc.m_version)
    except nodebase.CPropError as exc:
        raise CEditError(str(exc)) from exc


# properties


@edit(N_("sound.change_settings"))
def set_props(doc: CBankDoc, id: int, props: dict | None = None, ranges: dict | None = None) -> None:
    """Set (or, with None, remove) single properties and random ranges of an object by property id."""
    hirc_object, block = _props(doc, id)
    for prop, value in (props or {}).items():
        block.set_float(int(prop), value)
    for prop, span in (ranges or {}).items():
        block.set_range(int(prop), None if span is None else (float(span[0]), float(span[1])))
    doc.put(nodebase.with_props(hirc_object, block))


# containers


@edit(N_("sound.change_random_sequence_settings"))
def set_ranseq(
    doc: CBankDoc,
    container: int,
    loops: int | None = None,
    avoid_repeat: int | None = None,
    sequence: bool | None = None,
    shuffle: bool | None = None,
) -> None:
    """A Random/Sequence container's loop count (0: endless), how many of the last plays it avoids repeating, whether
    it plays its list in order (sequence) and whether the random pick is a shuffle (117's banks)."""
    hirc_object, lay = _layout(doc, container)
    if hirc_object.type != bnkgraph.RANSEQ or bnkgraph.layout(doc.m_version).child_gap[bnkgraph.RANSEQ] is None:
        raise CEditError(tr("sound.loop_settings_read_random_sequence"))
    pay = bytearray(hirc_object.payload)
    base = lay.at - RAN_PARAMS
    for at, value, fmt in (
        (LOOPS, loops, "<H"),
        (AVOID, avoid_repeat, "<H"),
        (SEQUENCE, None if sequence is None else int(sequence), "<B"),
        (SHUFFLE, None if shuffle is None else int(shuffle), "<B"),
    ):
        if value is not None:
            struct.pack_into(fmt, pay, base + at, value)
    doc.put(SHircObject(hirc_object.type, hirc_object.id, bytes(pay)))


@edit(N_("sound.change_playlist"))
def set_playlist(doc: CBankDoc, container: int, entries: list) -> None:
    """A Random/Sequence container's playlist: (child id, weight) entries in order; a child may stand several times."""
    hirc_object, lay = _layout(doc, container)
    if hirc_object.type != bnkgraph.RANSEQ:
        raise CEditError(
            tr("sound.type_name_container_no_playlist", type_name=hirc_object.type_name, container=container)
        )
    pairs = [(int(member), int(member_weight)) for member, member_weight in entries]
    if (
        not pairs
        or any(member not in lay.children for member, _w in pairs)
        or any(not 0 <= member_weight < 2**32 for _c, member_weight in pairs)
    ):
        raise CEditError(tr("sound.playlist_names_children_container_weight"))
    lay.playlist = pairs
    doc.put(containers.render(hirc_object, lay))


@edit(N_("sound.change_weight"))
def set_weight(doc: CBankDoc, container: int, child: int, weight: int) -> None:
    """The weight of a child (every entry of it) in a Random/Sequence container's playlist."""
    hirc_object, lay = _layout(doc, container)
    if child not in lay.children or hirc_object.type != bnkgraph.RANSEQ:
        raise CEditError(tr("sound.not_child_random_sequence_container", child=child, container=container))
    set_playlist(
        doc,
        container,
        [(member, weight if member == child else member_weight) for member, member_weight in lay.playlist],
    )


def _refuse_cycle(doc: CBankDoc, container: int, child: int) -> None:
    """A child may not be the container itself or one of the container's ancestors (the bank would loop)."""
    seen, at = set(), container
    while at and at not in seen:
        if at == child:
            raise CEditError(
                tr(
                    "sound.container_above_bank_loop",
                    child=child,
                    container=container,
                )
            )
        seen.add(at)
        at = bnkgraph.parent_of(doc.hirc_object(at), doc.m_version) if at in doc.graph else 0


def _own(doc: CBankDoc, container: int, child: int) -> SHircObject:
    """The child made to hang under the container (it must hang under no other)."""
    hirc_object = doc.hirc_object(child)
    _refuse_cycle(doc, container, child)
    parent = bnkgraph.parent_of(hirc_object, doc.m_version)
    if parent not in (0, container):
        raise CEditError(
            tr(
                "sound.hangs_under_already_take_out",
                type_name=hirc_object.type_name,
                child=child,
                parent=parent,
            )
        )
    return bnkgraph.with_parent(hirc_object, doc.m_version, container)


@edit(N_("sound.add_child"))
def add_child(
    doc: CBankDoc, container: int, child: int, weight: int = DEFAULT_WEIGHT, value: int | None = None
) -> None:
    """Put an object of the bank under a container: its playlist (Random/Sequence), its branch ``value`` (Switch)."""
    hirc_object, lay = _layout(doc, container)
    if child in lay.children:
        raise CEditError(tr("sound.child_container_already", child=child, container=container))
    doc.put(_own(doc, container, child))
    lay.children.append(child)
    if hirc_object.type == bnkgraph.RANSEQ:
        lay.playlist.append((child, weight))
    elif hirc_object.type == bnkgraph.SWITCH:
        if value not in lay.table:
            raise CEditError(
                tr(
                    "sound.switch_container_no_value_pick",
                    container=container,
                    value=value,
                )
            )
        lay.table[value].append(child)
    doc.put(containers.render(hirc_object, lay))


@edit(N_("sound.remove_child"))
def remove_child(doc: CBankDoc, container: int, child: int) -> None:
    """Take a child out of a Random/Sequence or Actor-Mixer container; the object stays in the bank, under none."""
    hirc_object, lay = _layout(doc, container)
    if hirc_object.type == bnkgraph.SWITCH:
        raise CEditError(tr("sound.take_node_out_branch_switch"))
    if child not in lay.children:
        raise CEditError(tr("sound.child_not_container", child=child, container=container))
    lay.children.remove(child)
    lay.playlist = [(member, member_weight) for member, member_weight in lay.playlist if member != child]
    doc.put(containers.render(hirc_object, lay))
    doc.put(bnkgraph.with_parent(doc.hirc_object(child), doc.m_version, 0))


# switch containers


@edit(N_("sound.change_switch"))
def set_switch(
    doc: CBankDoc, container: int, default: int | None = None, group: int | None = None, state_group: bool | None = None
) -> None:
    """The group a Switch container follows (a switch group, or a state group) and the value it starts with."""
    hirc_object, lay = _layout(doc, container)
    if hirc_object.type != bnkgraph.SWITCH:
        raise CEditError(tr("sound.not_switch_container", type_name=hirc_object.type_name, container=container))
    pay = bytearray(hirc_object.payload)
    at = lay.at - SWITCH_HEAD
    if state_group is not None:
        pay[at] = int(state_group)
    if group is not None:
        struct.pack_into("<I", pay, at + 1, group)
    if default is not None:
        struct.pack_into("<I", pay, at + 5, default)
    doc.put(SHircObject(hirc_object.type, hirc_object.id, bytes(pay)))


@edit(N_("sound.change_branch"))
def set_branch(doc: CBankDoc, container: int, value: int, nodes: list | None) -> None:
    """The nodes a switch value plays (children of the container; None removes the value's row)."""
    hirc_object, lay = _layout(doc, container)
    if hirc_object.type != bnkgraph.SWITCH:
        raise CEditError(tr("sound.not_switch_container", type_name=hirc_object.type_name, container=container))
    if nodes is None:
        lay.table.pop(value, None)
    else:
        if any(node not in lay.children for node in nodes):
            raise CEditError(tr("sound.branch_plays_children_container_add"))
        lay.table[value] = [int(node) for node in nodes]
    doc.put(containers.render(hirc_object, lay))


# events and actions


def _event_payload(version: int, actions: list[int]) -> bytes:
    ids = b"".join(struct.pack("<I", action) for action in actions)
    rec = bnkgraph.layout(version)
    if rec.event_head:  # the layout we write events in (117's: a head before the count)
        return bnksfx.event(0, actions).payload
    return struct.pack(rec.action_count, len(actions)) + ids


def _play_action(doc: CBankDoc, action: int, target: int) -> SHircObject:
    """A Play action: ours in 117's banks, in the others a copy of one the bank has (their action layout is not
    written from scratch)."""
    if bnkgraph.layout(doc.m_version).event_head:
        return bnksfx.play_action(action, target, doc.m_bank.bank_id)
    for action_object in doc.objects_of(bnkgraph.ACTION):
        if bnkgraph.read_action(action_object).type == bnkgraph.PLAY:
            return SHircObject(
                action_object.type,
                action,
                action_object.payload[:2] + struct.pack("<I", target) + action_object.payload[6:],
            )
    raise CEditError(tr("sound.bank_no_play_action_make"))


def _check_target(doc: CBankDoc, target: int) -> None:
    if doc.hirc_object(target).type not in (2, 5, 6, 7, 9, 10, 11, 12, 13):
        raise CEditError(tr("sound.object_not_something_event_play", target=target))


@edit(N_("sound.add_event"))
def add_event(doc: CBankDoc, event: int, action: int, target: int) -> None:
    """A new Event that plays ``target`` (a sound, a container or a music object of the bank), with its Play action."""
    _check_target(doc, target)
    doc.add(_play_action(doc, action, target))
    doc.add(SHircObject(bnkgraph.EVENT, event, _event_payload(doc.m_version, [action])))


@edit(N_("sound.change_events_actions"))
def set_event_actions(doc: CBankDoc, event: int, actions: list) -> None:
    """The actions an event runs, by id, in order."""
    hirc_object = doc.hirc_object(event)
    if hirc_object.type != bnkgraph.EVENT:
        raise CEditError(tr("sound.object_not_event", event=event))
    for action in actions:
        if doc.hirc_object(action).type != bnkgraph.ACTION:
            raise CEditError(tr("sound.object_not_action", a=action))
    doc.put(
        SHircObject(
            hirc_object.type, hirc_object.id, _event_payload(doc.m_version, [int(action) for action in actions])
        )
    )


@edit(N_("sound.add_play_action"))
def add_play(doc: CBankDoc, event: int, action: int, target: int) -> None:
    """Another Play action on an existing event."""
    _check_target(doc, target)
    held = bnkgraph.read_event(doc.hirc_object(event), doc.m_version)
    doc.add(_play_action(doc, action, target))
    set_event_actions(doc, event, [*held, action])


@edit(N_("sound.change_action"))
def set_action(
    doc: CBankDoc, action: int, target: int | None = None, group: int | None = None, value: int | None = None
) -> None:
    """What an action acts on: a play action's ``target``; a set-switch or set-state action's ``group`` and ``value``."""
    hirc_object = doc.hirc_object(action)
    read = bnkgraph.read_action(hirc_object)
    pay = bytearray(hirc_object.payload)
    if read.type in (bnkgraph.SET_STATE, bnkgraph.SET_SWITCH):
        if target is not None:
            raise CEditError(tr("sound.set_switch_state_action_names"))
        struct.pack_into(
            "<II", pay, read.at, read.group if group is None else group, read.value if value is None else value
        )
        struct.pack_into("<I", pay, 2, read.value if value is None else value)  # the target is the value's id again
    else:
        if group is not None or value is not None:
            raise CEditError(tr("sound.only_set_switch_state_action"))
        if target is not None:
            _check_target(doc, target)
            struct.pack_into("<I", pay, 2, target)
    doc.put(SHircObject(hirc_object.type, hirc_object.id, bytes(pay)))


# music


@edit(N_("sound.change_music_node"))
def set_music_node(doc: CBankDoc, container: int, index: int, fields: dict) -> None:
    """Fields of one entry of a music playlist container (117's banks): weight, loop, min_loop, max_loop,
    avoid_repeat, use_weight, shuffle."""
    hirc_object = doc.hirc_object(container)
    if not bnkgraph.layout(doc.m_version).music_readable or hirc_object.type != bnkmusic.MUSIC_RANSEQ_CNTR:
        raise CEditError(tr("sound.music_playlists_edited_playlist"))
    playlist = bnkmusic.parse_playlist(hirc_object)
    check_index(playlist.nodes, index, tr("sound.playlist_entries"))
    bad = [k for k in fields if k not in MUSIC_FIELDS]
    if bad:
        raise CEditError(tr("sound.playlist_entry_no_field", value=bad[0], value2=", ".join(MUSIC_FIELDS)))
    for key, value in fields.items():
        setattr(playlist.nodes[index], key, int(value))
    doc.put(bnkmusic.render_playlist(playlist))
