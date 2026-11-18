"""The child list and what follows it in a container's payload, read and written.

``read_layout(obj, node, version)`` finds where a Random/Sequence, Actor-Mixer or Switch container keeps its children
and the table behind them (a Random container's weights, a Switch container's value table); ``render`` puts a changed
layout back. A container whose tail does not read to its exact end is refused (``LayoutError``) rather than guessed:
the older banks' containers are found by the objects that name them (``formats/bnkgraph.child_block``) and about one in
a hundred has a tail of another shape. ``docs/sound/bank-objects.md`` has the layouts.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..formats import bnkgraph
from ..formats.bnk import HircObject
from ..formats.bnkmusic import Reader
from ..infra.i18n import tr


class LayoutError(ValueError):
    """The container's tail is not one of the layouts this editor writes."""


@dataclass
class Layout:
    """A container's child list at ``at`` in its payload and what follows it. ``playlist`` (Random/Sequence): the
    entries (child id, weight) in the file's order, a child once or several times; ``table`` (Switch): per value the
    nodes it plays, ``rest`` the bytes after the table."""

    at: int
    children: list[int]
    playlist: list[tuple[int, int]] = field(default_factory=list)
    table: dict[int, list[int]] = field(default_factory=dict)
    rest: bytes = b""


def child_span(obj: HircObject, node: bnkgraph.Node, version: int) -> tuple[int, int]:
    """Where a container's child list starts and where it ends in the payload (``LayoutError``: not found exactly)."""
    if not node.exact:
        raise LayoutError(tr("sound.child_list_not_found_exactly", type_name=obj.type_name, id=obj.id))
    at = bnkgraph.child_block(obj, bnkgraph.family(version), set(node.children))
    if at is None:
        raise LayoutError(tr("sound.child_list_not_found", type_name=obj.type_name, id=obj.id))
    return at, at + 4 + 4 * len(node.children)


def read_layout(obj: HircObject, node: bnkgraph.Node, version: int) -> Layout:
    """The layout of a container whose graph ``node`` was read exactly."""
    if obj.type not in (bnkgraph.RANSEQ, bnkgraph.MIXER, bnkgraph.SWITCH):
        raise LayoutError(
            tr(
                "sound.only_random_sequence_actor_mixer",
                type_name=obj.type_name,
                id=obj.id,
            )
        )
    at, after = child_span(obj, node, version)
    pay = obj.payload
    out = Layout(at, list(node.children))
    try:
        if obj.type == bnkgraph.RANSEQ:
            out.playlist = _read_playlist(pay, after, node.children)
        elif obj.type == bnkgraph.MIXER:
            if len(pay) != after:
                raise LayoutError(
                    tr(
                        "sound.bytes_follow_children",
                        type_name=obj.type_name,
                        id=obj.id,
                        value=len(pay) - after,
                    )
                )
        else:
            out.table, out.rest = _read_table(pay, after)
    except struct.error as exc:
        raise LayoutError(tr("sound.tail_not_read", type_name=obj.type_name, id=obj.id, exc=exc)) from exc
    return out


def _read_playlist(pay: bytes, after: int, children: list[int]) -> list[tuple[int, int]]:
    count = struct.unpack_from("<H", pay, after)[0]
    if len(pay) != after + 2 + 8 * count:
        raise LayoutError(tr("sound.playlist_not_end_container"))
    pairs = [struct.unpack_from("<II", pay, after + 2 + 8 * i) for i in range(count)]
    if not {i for i, _w in pairs} <= set(children):
        raise LayoutError(tr("sound.playlist_names_objects_not_children"))
    return pairs


def _read_table(pay: bytes, after: int) -> tuple[dict[int, list[int]], bytes]:
    r = Reader(pay)
    r.pos = after
    table = {}
    for _ in range(r.u32()):
        value = r.u32()
        table[value] = [r.u32() for _ in range(r.u32())]
    return table, pay[r.pos :]


def render(obj: HircObject, lay: Layout) -> HircObject:
    """``obj`` with the child list, playlist and value table of ``lay`` (the bytes before the list are kept)."""
    out = obj.payload[: lay.at] + struct.pack("<I", len(lay.children))
    out += b"".join(struct.pack("<I", c) for c in lay.children)
    if obj.type == bnkgraph.RANSEQ:
        out += struct.pack("<H", len(lay.playlist))
        out += b"".join(struct.pack("<II", c, w) for c, w in lay.playlist)
    elif obj.type == bnkgraph.SWITCH:
        out += struct.pack("<I", len(lay.table))
        for value, nodes in lay.table.items():
            out += struct.pack("<II", value, len(nodes)) + b"".join(struct.pack("<I", n) for n in nodes)
        out += lay.rest
    return HircObject(obj.type, obj.id, out)
