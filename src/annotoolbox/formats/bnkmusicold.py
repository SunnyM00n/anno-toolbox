"""The music objects of the older banks (112 of Anno 2205, 132 to 135 of Anno 1800), read, not written: a segment's
children, length and cues, a track's sources, a container's rules, playlist nodes and decision tree.

``children``, ``segment``, ``track_sources``, ``rules``, ``playlist`` and ``switch_tree`` take ``(obj, version)``
and raise ``ValueError`` for an object that does not read to its last byte: a misread layout is refused, not
guessed. 117's banks (172) are ``bnkmusic``. Layouts and counts: ``docs/sound/bank-objects.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import bnkbase, bnkcurves, bnkgraph, bnkmusic
from .bnk import SHircObject
from .bnkmusic import CReader, SPlaylistNode, SSwitchTree, STransRule

SUB_TRACKS_FROM = 128  # the versions whose track names its sub-tracks after the playlist (read in 135)


@dataclass(frozen=True)
class SOldSegment:
    children: list[int]  # the tracks the segment plays
    duration: float  # ms
    markers: list[tuple[int, float, bytes]]  # id, position in ms, name


def _head(reader: CReader, version: int) -> list[int]:
    """Past the flag byte and the node base (with its RTPC list) of a music object, then its child ids."""
    reader.u8()
    bnkbase.skip_before_rtpc(reader, version)
    bnkcurves.read_rtpcs(reader)
    return [reader.u32() for _ in range(reader.u32())]


def _check(hirc_object: SHircObject, version: int) -> None:
    if bnkgraph.family(version) == bnkgraph.NEW:
        raise ValueError(f"{hirc_object.type_name} {hirc_object.id}: a bank of version {version} is bnkmusic's")


def children(hirc_object: SHircObject, version: int) -> list[int]:
    """The objects a music segment, switch container or playlist container leads to (its child list)."""
    _check(hirc_object, version)
    if hirc_object.type not in (bnkmusic.MUSIC_SEGMENT, bnkmusic.MUSIC_SWITCH_CNTR, bnkmusic.MUSIC_RANSEQ_CNTR):
        raise ValueError(f"{hirc_object.type_name} {hirc_object.id} is no music container")
    return _head(CReader(hirc_object.payload), version)


def segment(hirc_object: SHircObject, version: int) -> SOldSegment:
    """A music segment read to its last byte (its markers' names are length-prefixed here, a zero-ended string in 172)."""
    _check(hirc_object, version)
    if hirc_object.type != bnkmusic.MUSIC_SEGMENT:
        raise ValueError(f"{hirc_object.type_name} {hirc_object.id} is no music segment")
    reader = CReader(hirc_object.payload)
    child_ids = _head(reader, version)
    reader.take(8 + 8 + 4 + 3)  # the meter: grid period and offset, tempo, beats per bar, beat value, flag
    reader.take(24 * reader.u32())  # stingers
    duration = reader.f64()
    marks = [(reader.u32(), reader.f64(), reader.take(reader.u32())) for _ in range(reader.u32())]
    if not reader.done():
        raise ValueError(f"segment {hirc_object.id}: {len(hirc_object.payload) - reader.m_pos} unparsed bytes")
    return SOldSegment(child_ids, duration, marks)


def track_sources(hirc_object: SHircObject, version: int) -> list[bnkgraph.SSource]:
    """A music track's sources read to the track's last byte (flag byte, sources, playlist items, node base)."""
    _check(hirc_object, version)
    if hirc_object.type != bnkmusic.MUSIC_TRACK:
        raise ValueError(f"{hirc_object.type_name} {hirc_object.id} is no music track")
    fam = bnkgraph.family(version)
    reader = CReader(hirc_object.payload)
    reader.u8()
    sources = [bnkgraph.read_source(reader, fam) for _ in range(reader.u32())]
    items = reader.u32()
    reader.take(items * (4 + 4 + 4 + 4 * 8))  # track, source and event ids, then play at, trims and duration (f64)
    if items and version >= SUB_TRACKS_FROM:
        reader.u32()
    for _ in range(reader.u32()):  # clip automation: clip, type, then the points
        reader.take(8)
        reader.take(12 * reader.u32())
    bnkbase.skip_before_rtpc(reader, version)
    bnkcurves.read_rtpcs(reader)
    kind = reader.u8()
    if kind == 3:  # a switch track: group type, group, default, the switch table, the transition
        reader.take(1 + 4 + 4)
        reader.take(4 * reader.u32())
        reader.take(32)
    reader.take(4)  # look-ahead time
    if not reader.done():
        raise ValueError(f"track {hirc_object.id}: {len(hirc_object.payload) - reader.m_pos} unparsed bytes")
    return sources


def _container(hirc_object: SHircObject, version: int, kind: int) -> CReader:
    """A reader past the head a playlist and a switch container share: flag, node base, children, the meter and the
    stingers (the segment's own head up to its duration)."""
    _check(hirc_object, version)
    if hirc_object.type != kind:
        raise ValueError(
            f"{hirc_object.type_name} {hirc_object.id} is no {'music switch' if kind == bnkmusic.MUSIC_SWITCH_CNTR else 'playlist'}"
        )
    reader = CReader(hirc_object.payload)
    _head(reader, version)
    reader.take(23)
    reader.take(24 * reader.u32())
    return reader


def _rules(reader: CReader, version: int) -> list[STransRule]:
    """The transition rules: as 172's, but 112's destination side ends after the entry type (24 bytes, not 26)."""
    old = bnkgraph.family(version) == bnkgraph.OLD
    out = []
    for _ in range(reader.u32()):
        source_ids = [reader.u32() for _ in range(reader.u32())]
        destination_ids = [reader.u32() for _ in range(reader.u32())]
        source_side = (reader.s32(), reader.u32(), reader.s32(), reader.u32(), reader.u32(), reader.u8())
        destination_side = (
            reader.s32(),
            reader.u32(),
            reader.s32(),
            reader.u32(),
            reader.u32(),
            reader.u16(),
            reader.u16(),
            0 if old else reader.u8(),
            0 if old else reader.u8(),
        )
        out.append(
            STransRule(
                source_ids, destination_ids, *source_side, *destination_side, reader.take(30) if reader.u8() else b""
            )
        )
    return out


def rules(hirc_object: SHircObject, version: int) -> list[STransRule]:
    """The transition rules of a playlist container or a music switch."""
    if hirc_object.type not in (bnkmusic.MUSIC_SWITCH_CNTR, bnkmusic.MUSIC_RANSEQ_CNTR):
        raise ValueError(f"{hirc_object.type_name} {hirc_object.id} is no music container")
    return _rules(_container(hirc_object, version, hirc_object.type), version)


def playlist(hirc_object: SHircObject, version: int) -> list[SPlaylistNode]:
    """A playlist container's nodes (the layout of 172's, read to the object's last byte)."""
    reader = _container(hirc_object, version, bnkmusic.MUSIC_RANSEQ_CNTR)
    _rules(reader, version)
    nodes = [
        SPlaylistNode(
            reader.u32(),
            reader.u32(),
            reader.u32(),
            reader.u32(),
            reader.s16(),
            reader.s16(),
            reader.s16(),
            reader.u32(),
            reader.u16(),
            reader.u8(),
            reader.u8(),
        )
        for _ in range(reader.u32())
    ]
    if not reader.done():
        raise ValueError(f"playlist {hirc_object.id}: {len(hirc_object.payload) - reader.m_pos} unparsed bytes")
    return nodes


def switch_tree(hirc_object: SHircObject, version: int) -> SSwitchTree:
    """A music switch's decision tree: the state or switch groups it decides by and the tree of 12-byte nodes whose
    leaves name the objects to play (``SSwitchTree.leaves``)."""
    reader = _container(hirc_object, version, bnkmusic.MUSIC_SWITCH_CNTR)
    _rules(reader, version)
    reader.u8()  # continue playback
    depth = reader.u32()
    group_ids = [reader.u32() for _ in range(depth)]
    reader.take(depth)  # the groups' types
    size = reader.u32()
    reader.u8()  # mode
    start = reader.m_pos
    tree = reader.take(size)
    if not reader.done():
        raise ValueError(f"switch {hirc_object.id}: {len(hirc_object.payload) - reader.m_pos} unparsed bytes")
    return SSwitchTree(group_ids, start, tree)
