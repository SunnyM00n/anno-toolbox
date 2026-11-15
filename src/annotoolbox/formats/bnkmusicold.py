"""The music objects of the older banks (112 of Anno 2205, 132 to 135 of Anno 1800), read, not written: a segment's
children, length and cues, a track's sources, a container's rules, playlist nodes and decision tree.

``children``, ``segment``, ``track_sources``, ``rules``, ``playlist`` and ``switch_tree`` take ``(obj, version)``
and raise ``ValueError`` for an object that does not read to its last byte: a misread layout is refused, not
guessed. 117's banks (172) are ``bnkmusic``. Layouts and counts: ``docs/sound/bank-objects.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import bnkbase, bnkcurves, bnkgraph, bnkmusic
from .bnk import HircObject
from .bnkmusic import PlaylistNode, Reader, SwitchTree, TransRule

SUB_TRACKS_FROM = 128  # the versions whose track names its sub-tracks after the playlist (read in 135)


@dataclass(frozen=True)
class OldSegment:
    children: list[int]  # the tracks the segment plays
    duration: float  # ms
    markers: list[tuple[int, float, bytes]]  # id, position in ms, name


def _head(r: Reader, version: int) -> list[int]:
    """Past the flag byte and the node base (with its RTPC list) of a music object, then its child ids."""
    r.u8()
    bnkbase.skip_before_rtpc(r, version)
    bnkcurves.read_rtpcs(r)
    return [r.u32() for _ in range(r.u32())]


def _check(obj: HircObject, version: int) -> None:
    if bnkgraph.family(version) == bnkgraph.NEW:
        raise ValueError(f"{obj.type_name} {obj.id}: a bank of version {version} is bnkmusic's")


def children(obj: HircObject, version: int) -> list[int]:
    """The objects a music segment, switch container or playlist container leads to (its child list)."""
    _check(obj, version)
    if obj.type not in (bnkmusic.MUSIC_SEGMENT, bnkmusic.MUSIC_SWITCH_CNTR, bnkmusic.MUSIC_RANSEQ_CNTR):
        raise ValueError(f"{obj.type_name} {obj.id} is no music container")
    return _head(Reader(obj.payload), version)


def segment(obj: HircObject, version: int) -> OldSegment:
    """A music segment read to its last byte (its markers' names are length-prefixed here, a zero-ended string in 172)."""
    _check(obj, version)
    if obj.type != bnkmusic.MUSIC_SEGMENT:
        raise ValueError(f"{obj.type_name} {obj.id} is no music segment")
    r = Reader(obj.payload)
    kids = _head(r, version)
    r.take(8 + 8 + 4 + 3)  # the meter: grid period and offset, tempo, beats per bar, beat value, flag
    r.take(24 * r.u32())  # stingers
    duration = r.f64()
    marks = [(r.u32(), r.f64(), r.take(r.u32())) for _ in range(r.u32())]
    if not r.done():
        raise ValueError(f"segment {obj.id}: {len(obj.payload) - r.pos} unparsed bytes")
    return OldSegment(kids, duration, marks)


def track_sources(obj: HircObject, version: int) -> list[bnkgraph.Source]:
    """A music track's sources read to the track's last byte (flag byte, sources, playlist items, node base)."""
    _check(obj, version)
    if obj.type != bnkmusic.MUSIC_TRACK:
        raise ValueError(f"{obj.type_name} {obj.id} is no music track")
    fam = bnkgraph.family(version)
    r = Reader(obj.payload)
    r.u8()
    sources = [bnkgraph.read_source(r, fam) for _ in range(r.u32())]
    items = r.u32()
    r.take(items * (4 + 4 + 4 + 4 * 8))  # track, source and event ids, then play at, trims and duration (f64)
    if items and version >= SUB_TRACKS_FROM:
        r.u32()
    for _ in range(r.u32()):  # clip automation: clip, type, then the points
        r.take(8)
        r.take(12 * r.u32())
    bnkbase.skip_before_rtpc(r, version)
    bnkcurves.read_rtpcs(r)
    kind = r.u8()
    if kind == 3:  # a switch track: group type, group, default, the switch table, the transition
        r.take(1 + 4 + 4)
        r.take(4 * r.u32())
        r.take(32)
    r.take(4)  # look-ahead time
    if not r.done():
        raise ValueError(f"track {obj.id}: {len(obj.payload) - r.pos} unparsed bytes")
    return sources


def _container(obj: HircObject, version: int, kind: int) -> Reader:
    """A reader past the head a playlist and a switch container share: flag, node base, children, the meter and the
    stingers (the segment's own head up to its duration)."""
    _check(obj, version)
    if obj.type != kind:
        raise ValueError(
            f"{obj.type_name} {obj.id} is no {'music switch' if kind == bnkmusic.MUSIC_SWITCH_CNTR else 'playlist'}"
        )
    r = Reader(obj.payload)
    _head(r, version)
    r.take(23)
    r.take(24 * r.u32())
    return r


def _rules(r: Reader, version: int) -> list[TransRule]:
    """The transition rules: as 172's, but 112's destination side ends after the entry type (24 bytes, not 26)."""
    old = bnkgraph.family(version) == bnkgraph.OLD
    out = []
    for _ in range(r.u32()):
        src = [r.u32() for _ in range(r.u32())]
        dst = [r.u32() for _ in range(r.u32())]
        a = (r.s32(), r.u32(), r.s32(), r.u32(), r.u32(), r.u8())
        b = (r.s32(), r.u32(), r.s32(), r.u32(), r.u32(), r.u16(), r.u16(), 0 if old else r.u8(), 0 if old else r.u8())
        out.append(TransRule(src, dst, *a, *b, r.take(30) if r.u8() else b""))
    return out


def rules(obj: HircObject, version: int) -> list[TransRule]:
    """The transition rules of a playlist container or a music switch."""
    if obj.type not in (bnkmusic.MUSIC_SWITCH_CNTR, bnkmusic.MUSIC_RANSEQ_CNTR):
        raise ValueError(f"{obj.type_name} {obj.id} is no music container")
    return _rules(_container(obj, version, obj.type), version)


def playlist(obj: HircObject, version: int) -> list[PlaylistNode]:
    """A playlist container's nodes (the layout of 172's, read to the object's last byte)."""
    r = _container(obj, version, bnkmusic.MUSIC_RANSEQ_CNTR)
    _rules(r, version)
    nodes = [
        PlaylistNode(r.u32(), r.u32(), r.u32(), r.u32(), r.s16(), r.s16(), r.s16(), r.u32(), r.u16(), r.u8(), r.u8())
        for _ in range(r.u32())
    ]
    if not r.done():
        raise ValueError(f"playlist {obj.id}: {len(obj.payload) - r.pos} unparsed bytes")
    return nodes


def switch_tree(obj: HircObject, version: int) -> SwitchTree:
    """A music switch's decision tree: the state or switch groups it decides by and the tree of 12-byte nodes whose
    leaves name the objects to play (``SwitchTree.leaves``)."""
    r = _container(obj, version, bnkmusic.MUSIC_SWITCH_CNTR)
    _rules(r, version)
    r.u8()  # continue playback
    depth = r.u32()
    args = [r.u32() for _ in range(depth)]
    r.take(depth)  # the groups' types
    size = r.u32()
    r.u8()  # mode
    start = r.pos
    tree = r.take(size)
    if not r.done():
        raise ValueError(f"switch {obj.id}: {len(obj.payload) - r.pos} unparsed bytes")
    return SwitchTree(args, start, tree)
