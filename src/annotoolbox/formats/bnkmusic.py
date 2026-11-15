"""Structured views of the Wwise music objects (bank version 172).

Layouts were derived from the Anno 117 banks and validated by re-serialising
every object byte for byte (``tests/test_bnk.py``).  Only what a music
bank's rebuild needs is modelled explicitly (children, meter, duration, markers,
sources, playlist items); the node base parameters (fx, props, positioning,
state chunk, RTPC) are parsed just far enough to find their end and are kept
as opaque bytes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .bnk import HircObject

MUSIC_SEGMENT = 10
MUSIC_TRACK = 11
MUSIC_SWITCH_CNTR = 12
MUSIC_RANSEQ_CNTR = 13


class Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError(f"read past end at {self.pos}+{n} of {len(self.data)}")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def s16(self) -> int:
        return struct.unpack("<h", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def s32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self.take(8))[0]

    def cstring(self) -> bytes:
        end = self.data.index(b"\0", self.pos)
        out = self.data[self.pos : end]
        self.pos = end + 1
        return out

    def done(self) -> bool:
        return self.pos == len(self.data)


# NodeBaseParams: skipped structurally, kept as raw bytes


def skip_node_base(r: Reader) -> bytes:
    """Read past the node base parameters of a sound, container or music object; returns their raw bytes."""
    start = r.pos
    skip_before_rtpc(r)
    skip_rtpc_list(r)
    return r.data[start : r.pos]


def skip_head(r: Reader) -> int:
    """Read a 117 node base's effects, metadata and bus; returns the direct parent's id."""
    r.u8()  # bIsOverrideParentFX
    num_fx = r.u8()
    if num_fx:
        r.u8()  # bitsFXBypass
        r.take(7 * num_fx)
    r.u8()  # bOverrideParentMetadata
    r.take(6 * r.u8())
    r.u32()  # OverrideBusId
    return r.u32()  # DirectParentID


def skip_before_rtpc(r: Reader) -> None:
    """Read a node base up to its RTPC list (the last block of it): effects, metadata, bus, parent, properties,
    positioning, aux sends, advanced settings and the state chunk."""
    skip_head(r)
    r.u8()  # byBitVector
    # NodeInitialParams: props + ranged props
    n = r.u8()
    r.take(n)
    r.take(4 * n)
    n = r.u8()
    r.take(n)
    r.take(8 * n)
    # PositioningParams
    bits = r.u8()
    if bits & 0x01:  # override parent
        has_3d = bool(bits & 0x02)  # HasListenerRelativeRouting
        if has_3d:
            bits3d = r.u8()
            has_automation = (bits3d >> 5) & 0x03 != 0  # e3DPositionType != emitter
            if has_automation:
                r.u8()  # ePathMode
                r.s32()  # TransitionTime
                nv = r.u32()
                r.take(16 * nv)
                npl = r.u32()
                r.take(20 * npl)
                r.take(12 * npl)  # Ak3DAutomationParams
    # AuxParams
    aux = r.u8()
    if aux & 0x08:  # bHasAux
        r.take(16)
    r.u32()  # reflectionsAuxBus
    # AdvSettingsParams
    r.u8()
    r.u8()
    r.u16()
    r.u8()
    r.u8()
    # StateChunk
    n = r.u8()
    r.take(3 * n)
    groups = r.u8()
    for _ in range(groups):
        r.take(9)  # group id, sync type, instance id (exact split unverified; kept opaque)
        states = r.u8()
        r.take(12 * states)  # state id + inline property value


def skip_rtpc_list(r: Reader) -> None:
    """Read past an InitialRTPC list: count u16, per entry the game parameter u32, type u8, accumulation u8, the driven
    property u8, the curve id u32, scaling u8, point count u16 and 12 bytes a point."""
    for _ in range(r.u16()):
        r.u32()
        r.u8()
        r.u8()
        r.u8()
        r.u32()
        r.u8()
        r.take(12 * r.u16())


# MusicSegment


@dataclass
class Marker:
    id: int
    position: float  # ms
    name: bytes = b""


@dataclass
class MeterInfo:
    grid_period: float = 1000.0
    grid_offset: float = 0.0
    tempo: float = 120.0
    beats_per_bar: int = 4
    beat_value: int = 4
    flag: int = 0


@dataclass
class MusicSegment:
    id: int
    flags: int
    base_params: bytes
    children: list[int]
    meter: MeterInfo
    stingers: bytes  # raw (u32 count + 24 bytes each)
    duration: float  # ms
    markers: list[Marker]

    def parent_id(self) -> int:
        return _parent_of(self.base_params)

    def set_parent(self, parent: int) -> None:
        self.base_params = _with_parent(self.base_params, parent)


def parent_offset(base: bytes) -> int:
    """Where DirectParentID sits in a node base: after the fx and metadata lists and the override bus."""
    r = Reader(base)
    r.u8()  # bIsOverrideParentFX
    n = r.u8()
    if n:
        r.u8()
        r.take(7 * n)
    r.u8()  # bOverrideParentMetadata
    n = r.u8()
    r.take(6 * n)
    r.u32()  # OverrideBusId
    return r.pos


def _parent_of(base: bytes) -> int:
    return struct.unpack_from("<I", base, parent_offset(base))[0]


def _with_parent(base: bytes, parent: int) -> bytes:
    off = parent_offset(base)
    return base[:off] + struct.pack("<I", parent) + base[off + 4 :]


def _read_meter(r: Reader) -> MeterInfo:
    return MeterInfo(r.f64(), r.f64(), r.f32(), r.u8(), r.u8(), r.u8())


def _write_meter(m: MeterInfo) -> bytes:
    return struct.pack("<ddfBBB", m.grid_period, m.grid_offset, m.tempo, m.beats_per_bar, m.beat_value, m.flag)


def parse_segment(obj: HircObject) -> MusicSegment:
    if obj.type != MUSIC_SEGMENT:
        raise ValueError("not a MusicSegment")
    r = Reader(obj.payload)
    flags = r.u8()
    base = skip_node_base(r)
    children = [r.u32() for _ in range(r.u32())]
    meter = _read_meter(r)
    n_stingers = r.u32()
    stingers = struct.pack("<I", n_stingers) + r.take(24 * n_stingers)
    duration = r.f64()
    markers = [Marker(r.u32(), r.f64(), r.cstring()) for _ in range(r.u32())]
    if not r.done():
        raise ValueError(f"segment {obj.id}: {len(obj.payload) - r.pos} unparsed bytes")
    return MusicSegment(obj.id, flags, base, children, meter, stingers, duration, markers)


def render_segment(s: MusicSegment) -> HircObject:
    out = struct.pack("<B", s.flags) + s.base_params
    out += struct.pack("<I", len(s.children)) + b"".join(struct.pack("<I", c) for c in s.children)
    out += _write_meter(s.meter) + s.stingers + struct.pack("<d", s.duration)
    out += struct.pack("<I", len(s.markers))
    for m in s.markers:
        out += struct.pack("<Id", m.id, m.position) + m.name + b"\0"
    return HircObject(MUSIC_SEGMENT, s.id, out)


# MusicTrack


@dataclass
class TrackSource:
    plugin_id: int  # 0x00040001 = Wwise Vorbis
    stream_type: int  # 0 data, 1 prefetch+stream, 2 stream
    source_id: int  # 64-bit media id (low 32 bits = wem file name)
    in_memory_size: int
    source_bits: int


@dataclass
class PlaylistItem:
    track_id: int
    source_id: int
    event_id: int
    play_at: float
    begin_trim: float
    end_trim: float
    src_duration: float


@dataclass
class MusicTrack:
    id: int
    sources: list[TrackSource]
    sources_tail: int  # one byte after the source list (purpose unknown, always 0 in Anno 117)
    playlist: list[PlaylistItem]
    num_sub_track: int
    clip_automation: bytes  # raw (u32 count + items)
    base_params: bytes
    track_type: int
    switch_params: bytes  # raw, only for track_type 3
    look_ahead: int

    def parent_id(self) -> int:
        return _parent_of(self.base_params)

    def set_parent(self, parent: int) -> None:
        self.base_params = _with_parent(self.base_params, parent)


def parse_track(obj: HircObject) -> MusicTrack:
    if obj.type != MUSIC_TRACK:
        raise ValueError("not a MusicTrack")
    r = Reader(obj.payload)
    sources = []
    for _ in range(r.u32()):
        sources.append(TrackSource(r.u32(), r.u8(), r.u64(), r.u32(), r.u8()))
    sources_tail = r.u8()
    playlist = []
    for _ in range(r.u32()):
        playlist.append(PlaylistItem(r.u32(), r.u64(), r.u32(), r.f64(), r.f64(), r.f64(), r.f64()))
    num_sub_track = r.u32() if playlist else 0
    start = r.pos
    n_auto = r.u32()
    for _ in range(n_auto):
        r.u32()
        r.u32()
        pts = r.u32()
        r.take(12 * pts)
    clip_automation = obj.payload[start : r.pos]
    base = skip_node_base(r)
    track_type = r.u8()
    switch_params = b""
    if track_type == 3:
        s0 = r.pos
        r.u8()  # eGroupType
        r.u32()  # uGroupID
        r.u32()  # uDefaultSwitch
        r.take(4 * r.u32())  # switch assoc
        r.take(4 * 8)  # TransParams
        switch_params = obj.payload[s0 : r.pos]
    look_ahead = r.s32()
    if not r.done():
        raise ValueError(f"track {obj.id}: {len(obj.payload) - r.pos} unparsed bytes")
    return MusicTrack(
        obj.id,
        sources,
        sources_tail,
        playlist,
        num_sub_track,
        clip_automation,
        base,
        track_type,
        switch_params,
        look_ahead,
    )


def render_track(t: MusicTrack) -> HircObject:
    out = struct.pack("<I", len(t.sources))
    for s in t.sources:
        out += struct.pack("<IBQIB", s.plugin_id, s.stream_type, s.source_id, s.in_memory_size, s.source_bits)
    out += struct.pack("<B", t.sources_tail) + struct.pack("<I", len(t.playlist))
    for p in t.playlist:
        out += struct.pack(
            "<IQIdddd", p.track_id, p.source_id, p.event_id, p.play_at, p.begin_trim, p.end_trim, p.src_duration
        )
    if t.playlist:
        out += struct.pack("<I", t.num_sub_track)
    out += (
        t.clip_automation
        + t.base_params
        + struct.pack("<B", t.track_type)
        + t.switch_params
        + struct.pack("<i", t.look_ahead)
    )
    return HircObject(MUSIC_TRACK, t.id, out)


# MusicRanSeqCntr (playlist container)


@dataclass
class PlaylistNode:
    segment_id: int
    item_id: int
    num_children: int
    rs_type: int  # 0xFFFFFFFF leaf; else sequence/random mode
    loop: int
    loop_min: int
    loop_max: int
    weight: int
    avoid_repeat: int
    use_weight: int
    shuffle: int


@dataclass
class MusicPlaylist:
    id: int
    flags: int
    base_params: bytes
    children: list[int]
    meter: MeterInfo
    stingers: bytes
    rules: bytes  # raw transition rules
    nodes: list[PlaylistNode]

    def parent_id(self) -> int:
        return _parent_of(self.base_params)


SYNC_IMMEDIATE = 0
SYNC_EXIT_CUE = 7


@dataclass
class TransRule:
    """One music transition rule (source nodes -> destination nodes)."""

    src: list[int]
    dst: list[int]
    src_fade_time: int
    src_fade_curve: int
    src_fade_offset: int
    sync_type: int
    cue_filter: int
    play_post_exit: int
    dst_fade_time: int
    dst_fade_curve: int
    dst_fade_offset: int
    dst_cue_filter: int
    jump_to_id: int
    jump_to_type: int
    entry_type: int
    play_pre_entry: int
    dst_match_cue: int
    trans_object: bytes  # raw 30-byte transition object, or empty


def parse_trans_rules(r: Reader) -> list[TransRule]:
    out = []
    for _ in range(r.u32()):
        src = [r.u32() for _ in range(r.u32())]
        dst = [r.u32() for _ in range(r.u32())]
        a = (r.s32(), r.u32(), r.s32(), r.u32(), r.u32(), r.u8())
        b = (r.s32(), r.u32(), r.s32(), r.u32(), r.u32(), r.u16(), r.u16(), r.u8(), r.u8())
        tobj = r.take(30) if r.u8() else b""
        out.append(TransRule(src, dst, *a, *b, tobj))
    return out


def render_trans_rules(rules: list[TransRule]) -> bytes:
    out = struct.pack("<I", len(rules))
    for t in rules:
        out += struct.pack("<I", len(t.src)) + b"".join(struct.pack("<I", i) for i in t.src)
        out += struct.pack("<I", len(t.dst)) + b"".join(struct.pack("<I", i) for i in t.dst)
        out += struct.pack(
            "<iIiIIB", t.src_fade_time, t.src_fade_curve, t.src_fade_offset, t.sync_type, t.cue_filter, t.play_post_exit
        )
        out += struct.pack(
            "<iIiIIHHBB",
            t.dst_fade_time,
            t.dst_fade_curve,
            t.dst_fade_offset,
            t.dst_cue_filter,
            t.jump_to_id,
            t.jump_to_type,
            t.entry_type,
            t.play_pre_entry,
            t.dst_match_cue,
        )
        out += struct.pack("<B", 1) + t.trans_object if t.trans_object else struct.pack("<B", 0)
    return out


def _skip_container_head(r: Reader) -> None:
    """Read past flags, node base, children, meter and stingers: the part a playlist and a switch container share."""
    r.u8()
    skip_node_base(r)
    r.take(4 * r.u32())
    _read_meter(r)
    r.take(24 * r.u32())


def locate_trans_rules(payload: bytes) -> tuple[int, int]:
    """Byte range of the transition rules inside a playlist or switch container payload."""
    r = Reader(payload)
    _skip_container_head(r)
    start = r.pos
    parse_trans_rules(r)
    return start, r.pos


def rewrite_trans_rules(obj: HircObject, edit) -> int:
    """Apply ``edit(rule) -> bool`` to every rule of a playlist/switch container; returns the number changed."""
    start, end = locate_trans_rules(obj.payload)
    rules = parse_trans_rules(Reader(obj.payload[start:end]))
    changed = sum(1 for t in rules if edit(t))
    if changed:
        obj.payload = obj.payload[:start] + render_trans_rules(rules) + obj.payload[end:]
    return changed


def parse_playlist(obj: HircObject) -> MusicPlaylist:
    if obj.type != MUSIC_RANSEQ_CNTR:
        raise ValueError("not a MusicRanSeqCntr")
    r = Reader(obj.payload)
    flags = r.u8()
    base = skip_node_base(r)
    children = [r.u32() for _ in range(r.u32())]
    meter = _read_meter(r)
    n_stingers = r.u32()
    stingers = struct.pack("<I", n_stingers) + r.take(24 * n_stingers)
    rules_at = r.pos
    parse_trans_rules(r)
    rules = obj.payload[rules_at : r.pos]
    nodes = []
    for _ in range(r.u32()):
        nodes.append(
            PlaylistNode(
                r.u32(), r.u32(), r.u32(), r.u32(), r.s16(), r.s16(), r.s16(), r.u32(), r.u16(), r.u8(), r.u8()
            )
        )
    if not r.done():
        raise ValueError(f"playlist {obj.id}: {len(obj.payload) - r.pos} unparsed bytes")
    return MusicPlaylist(obj.id, flags, base, children, meter, stingers, rules, nodes)


def render_playlist(p: MusicPlaylist) -> HircObject:
    out = struct.pack("<B", p.flags) + p.base_params
    out += struct.pack("<I", len(p.children)) + b"".join(struct.pack("<I", c) for c in p.children)
    out += _write_meter(p.meter) + p.stingers + p.rules
    out += struct.pack("<I", len(p.nodes))
    for n in p.nodes:
        out += struct.pack(
            "<IIIIhhhIHBB",
            n.segment_id,
            n.item_id,
            n.num_children,
            n.rs_type,
            n.loop,
            n.loop_min,
            n.loop_max,
            n.weight,
            n.avoid_repeat,
            n.use_weight,
            n.shuffle,
        )
    return HircObject(MUSIC_RANSEQ_CNTR, p.id, out)


# Shared node header helpers (playlist and switch containers)


def locate_children(payload: bytes) -> tuple[int, int]:
    """Byte range of the child id list (count + ids) after flags and base params."""
    r = Reader(payload)
    r.u8()
    skip_node_base(r)
    start = r.pos
    r.take(4 * r.u32())
    return start, r.pos


def rewrite_children(obj: HircObject, children: list[int]) -> None:
    start, end = locate_children(obj.payload)
    body = struct.pack("<I", len(children)) + b"".join(struct.pack("<I", c) for c in children)
    obj.payload = obj.payload[:start] + body + obj.payload[end:]


def children_of(obj: HircObject) -> list[int]:
    start, end = locate_children(obj.payload)
    r = Reader(obj.payload[start:end])
    return [r.u32() for _ in range(r.u32())]


@dataclass
class SwitchTree:
    args: list[int]  # state/switch group ids, one per tree level
    tree_offset: int  # offset of the decision tree inside the payload
    tree: bytes

    def leaves(self) -> list[tuple[list[int], int, int]]:
        """(state ids along the path, audio node id, byte offset of that id in the payload)."""
        out: list[tuple[list[int], int, int]] = []

        def walk(idx: int, level: int, path: list[int]) -> None:
            if level == len(self.args):
                node = struct.unpack_from("<I", self.tree, idx * 12 + 4)[0]
                out.append((path, node, self.tree_offset + idx * 12 + 4))
                return
            _key, children_idx, count, _w, _p = struct.unpack_from("<IHHHH", self.tree, idx * 12)
            for i in range(count):
                k = struct.unpack_from("<I", self.tree, (children_idx + i) * 12)[0]
                walk(children_idx + i, level + 1, path + [k])

        walk(0, 0, [])
        return out


def parse_switch_tree(payload: bytes) -> SwitchTree:
    r = Reader(payload)
    _skip_container_head(r)
    parse_trans_rules(r)
    r.u8()  # bIsContinuePlayback
    depth = r.u32()
    args = [r.u32() for _ in range(depth)]
    r.take(depth)  # group types
    size = r.u32()
    r.u8()  # mode
    off = r.pos
    return SwitchTree(args, off, r.take(size))


def retarget_switch_leaves(obj: HircObject, predicate, new_node: int) -> int:
    """Point every decision tree leaf for which ``predicate(path, node)`` holds at ``new_node``."""
    tree = parse_switch_tree(obj.payload)
    payload = bytearray(obj.payload)
    changed = 0
    for path, node, off in tree.leaves():
        if predicate(path, node):
            struct.pack_into("<I", payload, off, new_node)
            changed += 1
    obj.payload = bytes(payload)
    return changed
