"""The edits that change what a bank's objects say: properties, containers, switches, events and actions, music playlists.

Each ``@edit`` function takes the open ``BankDoc`` and plain arguments (ids, numbers, lists), so a list of them can be
made again on another copy of the bank; it raises ``EditError`` when the bank's layout is not one it writes. What each
bank version allows: docs/sound-editor.md "What can be edited". The edits that touch audio are in ``audioedits``.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from ..formats import bnkgraph, bnkmusic, bnksfx
from ..formats.bnk import HircObject
from ..infra.i18n import N_, tr
from . import containers, nodebase
from .registry import EditError, check_index, edit

if TYPE_CHECKING:
    from .bankdoc import BankDoc

DEFAULT_WEIGHT = 50  # what a new playlist entry weighs (the shipped ones: 1 to 100 in the tool, more in the files)
RAN_PARAMS = 24  # bytes before the child list of a Random/Sequence container in 117's banks
LOOPS, AVOID, SHUFFLE, SEQUENCE = 0, 18, 21, 22  # their offsets
SWITCH_HEAD = 10  # bytes before a Switch container's child list: kind u8, group u32, default u32, continuous u8
MUSIC_FIELDS = ("weight", "loop", "loop_min", "loop_max", "avoid_repeat", "use_weight", "shuffle")


def _layout(doc: BankDoc, container: int) -> tuple[HircObject, containers.Layout]:
    obj, node = doc.obj(container), doc.graph.get(container)
    if node is None:
        raise EditError(tr("sound.type_name_container_not", type_name=obj.type_name, container=container))
    try:
        return obj, containers.read_layout(obj, node, doc.version)
    except containers.LayoutError as exc:
        raise EditError(str(exc)) from exc


def _props(doc: BankDoc, obj_id: int) -> tuple[HircObject, nodebase.PropBlock]:
    obj = doc.obj(obj_id)
    try:
        return obj, nodebase.read_props(obj, doc.version)
    except nodebase.PropError as exc:
        raise EditError(str(exc)) from exc


# properties


@edit(N_("sound.change_settings"))
def set_props(doc: BankDoc, id: int, props: dict | None = None, ranges: dict | None = None) -> None:
    """Set (or, with None, remove) single properties and random ranges of an object by property id."""
    obj, block = _props(doc, id)
    for prop, value in (props or {}).items():
        block.set_float(int(prop), value)
    for prop, span in (ranges or {}).items():
        block.set_range(int(prop), None if span is None else (float(span[0]), float(span[1])))
    doc.put(nodebase.with_props(obj, block))


# containers


@edit(N_("sound.change_random_sequence_settings"))
def set_ranseq(
    doc: BankDoc,
    container: int,
    loops: int | None = None,
    avoid_repeat: int | None = None,
    sequence: bool | None = None,
    shuffle: bool | None = None,
) -> None:
    """A Random/Sequence container's loop count (0: endless), how many of the last plays it avoids repeating, whether
    it plays its list in order (sequence) and whether the random pick is a shuffle (117's banks)."""
    obj, lay = _layout(doc, container)
    if obj.type != bnkgraph.RANSEQ or bnkgraph.layout(doc.version).child_gap[bnkgraph.RANSEQ] is None:
        raise EditError(tr("sound.loop_settings_read_random_sequence"))
    pay = bytearray(obj.payload)
    base = lay.at - RAN_PARAMS
    for at, value, fmt in (
        (LOOPS, loops, "<H"),
        (AVOID, avoid_repeat, "<H"),
        (SEQUENCE, None if sequence is None else int(sequence), "<B"),
        (SHUFFLE, None if shuffle is None else int(shuffle), "<B"),
    ):
        if value is not None:
            struct.pack_into(fmt, pay, base + at, value)
    doc.put(HircObject(obj.type, obj.id, bytes(pay)))


@edit(N_("sound.change_playlist"))
def set_playlist(doc: BankDoc, container: int, entries: list) -> None:
    """A Random/Sequence container's playlist: (child id, weight) entries in order; a child may stand several times."""
    obj, lay = _layout(doc, container)
    if obj.type != bnkgraph.RANSEQ:
        raise EditError(tr("sound.type_name_container_no_playlist", type_name=obj.type_name, container=container))
    pairs = [(int(c), int(w)) for c, w in entries]
    if not pairs or any(c not in lay.children for c, _w in pairs) or any(not 0 <= w < 2**32 for _c, w in pairs):
        raise EditError(tr("sound.playlist_names_children_container_weight"))
    lay.playlist = pairs
    doc.put(containers.render(obj, lay))


@edit(N_("sound.change_weight"))
def set_weight(doc: BankDoc, container: int, child: int, weight: int) -> None:
    """The weight of a child (every entry of it) in a Random/Sequence container's playlist."""
    obj, lay = _layout(doc, container)
    if child not in lay.children or obj.type != bnkgraph.RANSEQ:
        raise EditError(tr("sound.not_child_random_sequence_container", child=child, container=container))
    set_playlist(doc, container, [(c, weight if c == child else w) for c, w in lay.playlist])


def _refuse_cycle(doc: BankDoc, container: int, child: int) -> None:
    """A child may not be the container itself or one of the container's ancestors (the bank would loop)."""
    seen, at = set(), container
    while at and at not in seen:
        if at == child:
            raise EditError(
                tr(
                    "sound.container_above_bank_loop",
                    child=child,
                    container=container,
                )
            )
        seen.add(at)
        at = bnkgraph.parent_of(doc.obj(at), doc.version) if at in doc.graph else 0


def _own(doc: BankDoc, container: int, child: int) -> HircObject:
    """The child made to hang under the container (it must hang under no other)."""
    obj = doc.obj(child)
    _refuse_cycle(doc, container, child)
    parent = bnkgraph.parent_of(obj, doc.version)
    if parent not in (0, container):
        raise EditError(
            tr(
                "sound.hangs_under_already_take_out",
                type_name=obj.type_name,
                child=child,
                parent=parent,
            )
        )
    return bnkgraph.with_parent(obj, doc.version, container)


@edit(N_("sound.add_child"))
def add_child(doc: BankDoc, container: int, child: int, weight: int = DEFAULT_WEIGHT, value: int | None = None) -> None:
    """Put an object of the bank under a container: its playlist (Random/Sequence), its branch ``value`` (Switch)."""
    obj, lay = _layout(doc, container)
    if child in lay.children:
        raise EditError(tr("sound.child_container_already", child=child, container=container))
    doc.put(_own(doc, container, child))
    lay.children.append(child)
    if obj.type == bnkgraph.RANSEQ:
        lay.playlist.append((child, weight))
    elif obj.type == bnkgraph.SWITCH:
        if value not in lay.table:
            raise EditError(
                tr(
                    "sound.switch_container_no_value_pick",
                    container=container,
                    value=value,
                )
            )
        lay.table[value].append(child)
    doc.put(containers.render(obj, lay))


@edit(N_("sound.remove_child"))
def remove_child(doc: BankDoc, container: int, child: int) -> None:
    """Take a child out of a Random/Sequence or Actor-Mixer container; the object stays in the bank, under none."""
    obj, lay = _layout(doc, container)
    if obj.type == bnkgraph.SWITCH:
        raise EditError(tr("sound.take_node_out_branch_switch"))
    if child not in lay.children:
        raise EditError(tr("sound.child_not_container", child=child, container=container))
    lay.children.remove(child)
    lay.playlist = [(c, w) for c, w in lay.playlist if c != child]
    doc.put(containers.render(obj, lay))
    doc.put(bnkgraph.with_parent(doc.obj(child), doc.version, 0))


# switch containers


@edit(N_("sound.change_switch"))
def set_switch(
    doc: BankDoc, container: int, default: int | None = None, group: int | None = None, state_group: bool | None = None
) -> None:
    """The group a Switch container follows (a switch group, or a state group) and the value it starts with."""
    obj, lay = _layout(doc, container)
    if obj.type != bnkgraph.SWITCH:
        raise EditError(tr("sound.not_switch_container", type_name=obj.type_name, container=container))
    pay = bytearray(obj.payload)
    at = lay.at - SWITCH_HEAD
    if state_group is not None:
        pay[at] = int(state_group)
    if group is not None:
        struct.pack_into("<I", pay, at + 1, group)
    if default is not None:
        struct.pack_into("<I", pay, at + 5, default)
    doc.put(HircObject(obj.type, obj.id, bytes(pay)))


@edit(N_("sound.change_branch"))
def set_branch(doc: BankDoc, container: int, value: int, nodes: list | None) -> None:
    """The nodes a switch value plays (children of the container; None removes the value's row)."""
    obj, lay = _layout(doc, container)
    if obj.type != bnkgraph.SWITCH:
        raise EditError(tr("sound.not_switch_container", type_name=obj.type_name, container=container))
    if nodes is None:
        lay.table.pop(value, None)
    else:
        if any(n not in lay.children for n in nodes):
            raise EditError(tr("sound.branch_plays_children_container_add"))
        lay.table[value] = [int(n) for n in nodes]
    doc.put(containers.render(obj, lay))


# events and actions


def _event_payload(version: int, actions: list[int]) -> bytes:
    ids = b"".join(struct.pack("<I", a) for a in actions)
    rec = bnkgraph.layout(version)
    if rec.event_head:  # the layout we write events in (117's: a head before the count)
        return bnksfx.event(0, actions).payload
    return struct.pack(rec.action_count, len(actions)) + ids


def _play_action(doc: BankDoc, action: int, target: int) -> HircObject:
    """A Play action: ours in 117's banks, in the others a copy of one the bank has (their action layout is not
    written from scratch)."""
    if bnkgraph.layout(doc.version).event_head:
        return bnksfx.play_action(action, target, doc.bank.bank_id)
    for o in doc.objects_of(bnkgraph.ACTION):
        if bnkgraph.read_action(o).type == bnkgraph.PLAY:
            return HircObject(o.type, action, o.payload[:2] + struct.pack("<I", target) + o.payload[6:])
    raise EditError(tr("sound.bank_no_play_action_make"))


def _check_target(doc: BankDoc, target: int) -> None:
    if doc.obj(target).type not in (2, 5, 6, 7, 9, 10, 11, 12, 13):
        raise EditError(tr("sound.object_not_something_event_play", target=target))


@edit(N_("sound.add_event"))
def add_event(doc: BankDoc, event: int, action: int, target: int) -> None:
    """A new Event that plays ``target`` (a sound, a container or a music object of the bank), with its Play action."""
    _check_target(doc, target)
    doc.add(_play_action(doc, action, target))
    doc.add(HircObject(bnkgraph.EVENT, event, _event_payload(doc.version, [action])))


@edit(N_("sound.change_events_actions"))
def set_event_actions(doc: BankDoc, event: int, actions: list) -> None:
    """The actions an event runs, by id, in order."""
    obj = doc.obj(event)
    if obj.type != bnkgraph.EVENT:
        raise EditError(tr("sound.object_not_event", event=event))
    for a in actions:
        if doc.obj(a).type != bnkgraph.ACTION:
            raise EditError(tr("sound.object_not_action", a=a))
    doc.put(HircObject(obj.type, obj.id, _event_payload(doc.version, [int(a) for a in actions])))


@edit(N_("sound.add_play_action"))
def add_play(doc: BankDoc, event: int, action: int, target: int) -> None:
    """Another Play action on an existing event."""
    _check_target(doc, target)
    held = bnkgraph.read_event(doc.obj(event), doc.version)
    doc.add(_play_action(doc, action, target))
    set_event_actions(doc, event, [*held, action])


@edit(N_("sound.change_action"))
def set_action(
    doc: BankDoc, action: int, target: int | None = None, group: int | None = None, value: int | None = None
) -> None:
    """What an action acts on: a play action's ``target``; a set-switch or set-state action's ``group`` and ``value``."""
    obj = doc.obj(action)
    read = bnkgraph.read_action(obj)
    pay = bytearray(obj.payload)
    if read.type in (bnkgraph.SET_STATE, bnkgraph.SET_SWITCH):
        if target is not None:
            raise EditError(tr("sound.set_switch_state_action_names"))
        struct.pack_into(
            "<II", pay, read.at, read.group if group is None else group, read.value if value is None else value
        )
        struct.pack_into("<I", pay, 2, read.value if value is None else value)  # the target is the value's id again
    else:
        if group is not None or value is not None:
            raise EditError(tr("sound.only_set_switch_state_action"))
        if target is not None:
            _check_target(doc, target)
            struct.pack_into("<I", pay, 2, target)
    doc.put(HircObject(obj.type, obj.id, bytes(pay)))


# music


@edit(N_("sound.change_music_node"))
def set_music_node(doc: BankDoc, container: int, index: int, fields: dict) -> None:
    """Fields of one entry of a music playlist container (117's banks): weight, loop, loop_min, loop_max,
    avoid_repeat, use_weight, shuffle."""
    obj = doc.obj(container)
    if not bnkgraph.layout(doc.version).music_readable or obj.type != bnkmusic.MUSIC_RANSEQ_CNTR:
        raise EditError(tr("sound.music_playlists_edited_playlist"))
    playlist = bnkmusic.parse_playlist(obj)
    check_index(playlist.nodes, index, tr("sound.playlist_entries"))
    bad = [k for k in fields if k not in MUSIC_FIELDS]
    if bad:
        raise EditError(tr("sound.playlist_entry_no_field", value=bad[0], value2=", ".join(MUSIC_FIELDS)))
    for key, value in fields.items():
        setattr(playlist.nodes[index], key, int(value))
    doc.put(bnkmusic.render_playlist(playlist))
