"""The child list and what follows it in a container's payload, read and written.

``read_layout(obj, node, version)`` finds where a Random/Sequence, Actor-Mixer or Switch container keeps its children
and the table behind them (a Random container's weights, a Switch container's value table); ``render`` puts a changed
layout back. A container whose tail does not read to its exact end is refused (``CLayoutError``) rather than guessed:
the older banks' containers are found by the objects that name them (``formats/bnkgraph.child_block``) and about one in
a hundred has a tail of another shape. ``docs/sound/bank-objects.md`` has the layouts.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..formats import bnkgraph
from ..formats.bnk import SHircObject
from ..formats.bnkmusic import CReader
from ..infra.i18n import tr


class CLayoutError(ValueError):
    """The container's tail is not one of the layouts this editor writes."""


@dataclass
class SLayout:
    """A container's child list at ``at`` in its payload and what follows it. ``playlist`` (Random/Sequence): the
    entries (child id, weight) in the file's order, a child once or several times; ``table`` (Switch): per value the
    nodes it plays, ``rest`` the bytes after the table."""

    at: int
    children: list[int]
    playlist: list[tuple[int, int]] = field(default_factory=list)
    table: dict[int, list[int]] = field(default_factory=dict)
    rest: bytes = b""


def child_span(hirc_object: SHircObject, node: bnkgraph.SNode, version: int) -> tuple[int, int]:
    """Where a container's child list starts and where it ends in the payload (``CLayoutError``: not found exactly)."""
    if not node.exact:
        raise CLayoutError(tr("sound.child_list_not_found_exactly", type_name=hirc_object.type_name, id=hirc_object.id))
    at = bnkgraph.child_block(hirc_object, bnkgraph.family(version), set(node.children))
    if at is None:
        raise CLayoutError(tr("sound.child_list_not_found", type_name=hirc_object.type_name, id=hirc_object.id))
    return at, at + 4 + 4 * len(node.children)


def read_layout(hirc_object: SHircObject, node: bnkgraph.SNode, version: int) -> SLayout:
    """The layout of a container whose graph ``node`` was read exactly."""
    if hirc_object.type not in (bnkgraph.RANSEQ, bnkgraph.MIXER, bnkgraph.SWITCH):
        raise CLayoutError(
            tr(
                "sound.only_random_sequence_actor_mixer",
                type_name=hirc_object.type_name,
                id=hirc_object.id,
            )
        )
    at, after = child_span(hirc_object, node, version)
    pay = hirc_object.payload
    out = SLayout(at, list(node.children))
    try:
        if hirc_object.type == bnkgraph.RANSEQ:
            out.playlist = _read_playlist(pay, after, node.children)
        elif hirc_object.type == bnkgraph.MIXER:
            if len(pay) != after:
                raise CLayoutError(
                    tr(
                        "sound.bytes_follow_children",
                        type_name=hirc_object.type_name,
                        id=hirc_object.id,
                        value=len(pay) - after,
                    )
                )
        else:
            out.table, out.rest = _read_table(pay, after)
    except struct.error as exc:
        raise CLayoutError(
            tr("sound.tail_not_read", type_name=hirc_object.type_name, id=hirc_object.id, exc=exc)
        ) from exc
    return out


def _read_playlist(pay: bytes, after: int, children: list[int]) -> list[tuple[int, int]]:
    count = struct.unpack_from("<H", pay, after)[0]
    if len(pay) != after + 2 + 8 * count:
        raise CLayoutError(tr("sound.playlist_not_end_container"))
    pairs = [struct.unpack_from("<II", pay, after + 2 + 8 * i) for i in range(count)]
    if not {i for i, _w in pairs} <= set(children):
        raise CLayoutError(tr("sound.playlist_names_objects_not_children"))
    return pairs


def _read_table(pay: bytes, after: int) -> tuple[dict[int, list[int]], bytes]:
    reader = CReader(pay)
    reader.m_pos = after
    table = {}
    for _ in range(reader.u32()):
        value = reader.u32()
        table[value] = [reader.u32() for _ in range(reader.u32())]
    return table, pay[reader.m_pos :]


def render(hirc_object: SHircObject, lay: SLayout) -> SHircObject:
    """``obj`` with the child list, playlist and value table of ``lay`` (the bytes before the list are kept)."""
    out = hirc_object.payload[: lay.at] + struct.pack("<I", len(lay.children))
    out += b"".join(struct.pack("<I", child) for child in lay.children)
    if hirc_object.type == bnkgraph.RANSEQ:
        out += struct.pack("<H", len(lay.playlist))
        out += b"".join(struct.pack("<II", child, weight) for child, weight in lay.playlist)
    elif hirc_object.type == bnkgraph.SWITCH:
        out += struct.pack("<I", len(lay.table))
        for value, nodes in lay.table.items():
            out += struct.pack("<II", value, len(nodes)) + b"".join(struct.pack("<I", node) for node in nodes)
        out += lay.rest
    return SHircObject(hirc_object.type, hirc_object.id, out)
