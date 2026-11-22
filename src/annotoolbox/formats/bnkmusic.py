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

from .bnk import SHircObject

MUSIC_SEGMENT = 10
MUSIC_TRACK = 11
MUSIC_SWITCH_CNTR = 12
MUSIC_RANSEQ_CNTR = 13


class CReader:
    """A little-endian cursor over one object's bytes; a read past the end raises ``ValueError``."""

    def __init__(self, data: bytes) -> None:
        self.m_data = data
        self.m_pos = 0

    def take(self, byte_count: int) -> bytes:
        if self.m_pos + byte_count > len(self.m_data):
            raise ValueError(f"read past end at {self.m_pos}+{byte_count} of {len(self.m_data)}")
        out = self.m_data[self.m_pos : self.m_pos + byte_count]
        self.m_pos += byte_count
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
        end = self.m_data.find(b"\0", self.m_pos)
        if end < 0:
            raise ValueError(f"string without its end at {self.m_pos} of {len(self.m_data)}")
        out = self.m_data[self.m_pos : end]
        self.m_pos = end + 1
        return out

    def done(self) -> bool:
        return self.m_pos == len(self.m_data)


# NodeBaseParams: skipped structurally, kept as raw bytes


def skip_node_base(reader: CReader) -> bytes:
    """Read past the node base parameters of a sound, container or music object; returns their raw bytes."""
    start = reader.m_pos
    skip_before_rtpc(reader)
    skip_rtpc_list(reader)
    return reader.m_data[start : reader.m_pos]


def skip_head(reader: CReader) -> int:
    """Read a 117 node base's effects, metadata and bus; returns the direct parent's id."""
    reader.u8()  # bIsOverrideParentFX
    num_fx = reader.u8()
    if num_fx:
        reader.u8()  # bitsFXBypass
        reader.take(7 * num_fx)
    reader.u8()  # bOverrideParentMetadata
    reader.take(6 * reader.u8())
    reader.u32()  # OverrideBusId
    return reader.u32()  # DirectParentID


def skip_before_rtpc(reader: CReader) -> None:
    """Read a node base up to its RTPC list (the last block of it): effects, metadata, bus, parent, properties,
    positioning, aux sends, advanced settings and the state chunk."""
    skip_head(reader)
    reader.u8()  # byBitVector
    # NodeInitialParams: props + ranged props
    count = reader.u8()
    reader.take(count)
    reader.take(4 * count)
    count = reader.u8()
    reader.take(count)
    reader.take(8 * count)
    # PositioningParams
    bits = reader.u8()
    if bits & 0x01:  # override parent
        has_3d = bool(bits & 0x02)  # HasListenerRelativeRouting
        if has_3d:
            bits3d = reader.u8()
            has_automation = (bits3d >> 5) & 0x03 != 0  # e3DPositionType != emitter
            if has_automation:
                reader.u8()  # ePathMode
                reader.s32()  # TransitionTime
                vertex_count = reader.u32()
                reader.take(16 * vertex_count)
                playlist_item_count = reader.u32()
                reader.take(20 * playlist_item_count)
                reader.take(12 * playlist_item_count)  # Ak3DAutomationParams
    # AuxParams
    aux = reader.u8()
    if aux & 0x08:  # bHasAux
        reader.take(16)
    reader.u32()  # reflectionsAuxBus
    # AdvSettingsParams
    reader.u8()
    reader.u8()
    reader.u16()
    reader.u8()
    reader.u8()
    # StateChunk
    count = reader.u8()
    reader.take(3 * count)
    groups = reader.u8()
    for _ in range(groups):
        reader.take(9)  # group id, sync type, instance id (exact split unverified; kept opaque)
        states = reader.u8()
        reader.take(12 * states)  # state id + inline property value


def skip_rtpc_list(reader: CReader) -> None:
    """Read past an InitialRTPC list: count u16, per entry the game parameter u32, type u8, accumulation u8, the driven
    property u8, the curve id u32, scaling u8, point count u16 and 12 bytes a point."""
    for _ in range(reader.u16()):
        reader.u32()
        reader.u8()
        reader.u8()
        reader.u8()
        reader.u32()
        reader.u8()
        reader.take(12 * reader.u16())


# MusicSegment


@dataclass
class SMarker:
    id: int
    position: float  # ms
    name: bytes = b""


@dataclass
class SMeterInfo:
    grid_period: float = 1000.0
    grid_offset: float = 0.0
    tempo: float = 120.0
    beats_per_bar: int = 4
    beat_value: int = 4
    flag: int = 0


@dataclass
class CMusicSegment:
    m_id: int
    m_flags: int
    m_base_params: bytes
    m_children: list[int]
    m_meter: SMeterInfo
    m_stingers: bytes  # raw (u32 count + 24 bytes each)
    m_duration: float  # ms
    m_markers: list[SMarker]

    def parent_id(self) -> int:
        return _parent_of(self.m_base_params)

    def set_parent(self, parent: int) -> None:
        self.m_base_params = _with_parent(self.m_base_params, parent)


def parent_offset(base: bytes) -> int:
    """Where DirectParentID sits in a node base: after the fx and metadata lists and the override bus."""
    reader = CReader(base)
    skip_head(reader)
    return reader.m_pos - 4


def _parent_of(base: bytes) -> int:
    return struct.unpack_from("<I", base, parent_offset(base))[0]


def _with_parent(base: bytes, parent: int) -> bytes:
    offset = parent_offset(base)
    return base[:offset] + struct.pack("<I", parent) + base[offset + 4 :]


def _read_meter(reader: CReader) -> SMeterInfo:
    return SMeterInfo(reader.f64(), reader.f64(), reader.f32(), reader.u8(), reader.u8(), reader.u8())


def _write_meter(meter: SMeterInfo) -> bytes:
    return struct.pack(
        "<ddfBBB", meter.grid_period, meter.grid_offset, meter.tempo, meter.beats_per_bar, meter.beat_value, meter.flag
    )


def parse_segment(hirc_object: SHircObject) -> CMusicSegment:
    if hirc_object.type != MUSIC_SEGMENT:
        raise ValueError("not a MusicSegment")
    reader = CReader(hirc_object.payload)
    flags = reader.u8()
    base = skip_node_base(reader)
    children = [reader.u32() for _ in range(reader.u32())]
    meter = _read_meter(reader)
    n_stingers = reader.u32()
    stingers = struct.pack("<I", n_stingers) + reader.take(24 * n_stingers)
    duration = reader.f64()
    markers = [SMarker(reader.u32(), reader.f64(), reader.cstring()) for _ in range(reader.u32())]
    if not reader.done():
        raise ValueError(f"segment {hirc_object.id}: {len(hirc_object.payload) - reader.m_pos} unparsed bytes")
    return CMusicSegment(hirc_object.id, flags, base, children, meter, stingers, duration, markers)


def render_segment(segment: CMusicSegment) -> SHircObject:
    out = struct.pack("<B", segment.m_flags) + segment.m_base_params
    out += struct.pack("<I", len(segment.m_children)) + b"".join(
        struct.pack("<I", child) for child in segment.m_children
    )
    out += _write_meter(segment.m_meter) + segment.m_stingers + struct.pack("<d", segment.m_duration)
    out += struct.pack("<I", len(segment.m_markers))
    for marker in segment.m_markers:
        out += struct.pack("<Id", marker.id, marker.position) + marker.name + b"\0"
    return SHircObject(MUSIC_SEGMENT, segment.m_id, out)


# MusicTrack


@dataclass
class STrackSource:
    plugin_id: int  # 0x00040001 = Wwise Vorbis
    stream_type: int  # 0 data, 1 prefetch+stream, 2 stream
    source_id: int  # 64-bit media id (low 32 bits = wem file name)
    in_memory_size: int
    source_bits: int


@dataclass
class SPlaylistItem:
    track_id: int
    source_id: int
    event_id: int
    play_at: float
    begin_trim: float
    end_trim: float
    source_duration_ms: float


@dataclass
class CMusicTrack:
    m_id: int
    m_sources: list[STrackSource]
    m_sources_tail: int  # one byte after the source list (purpose unknown, always 0 in Anno 117)
    m_playlist: list[SPlaylistItem]
    m_num_sub_track: int
    m_clip_automation: bytes  # raw (u32 count + items)
    m_base_params: bytes
    m_track_type: int
    m_switch_params: bytes  # raw, only for track_type 3
    m_look_ahead: int

    def parent_id(self) -> int:
        return _parent_of(self.m_base_params)

    def set_parent(self, parent: int) -> None:
        self.m_base_params = _with_parent(self.m_base_params, parent)


def parse_track(hirc_object: SHircObject) -> CMusicTrack:
    if hirc_object.type != MUSIC_TRACK:
        raise ValueError("not a MusicTrack")
    reader = CReader(hirc_object.payload)
    sources = []
    for _ in range(reader.u32()):
        sources.append(STrackSource(reader.u32(), reader.u8(), reader.u64(), reader.u32(), reader.u8()))
    sources_tail = reader.u8()
    playlist = []
    for _ in range(reader.u32()):
        playlist.append(
            SPlaylistItem(
                reader.u32(), reader.u64(), reader.u32(), reader.f64(), reader.f64(), reader.f64(), reader.f64()
            )
        )
    num_sub_track = reader.u32() if playlist else 0
    start = reader.m_pos
    n_auto = reader.u32()
    for _ in range(n_auto):
        reader.u32()
        reader.u32()
        point_count = reader.u32()
        reader.take(12 * point_count)
    clip_automation = hirc_object.payload[start : reader.m_pos]
    base = skip_node_base(reader)
    track_type = reader.u8()
    switch_params = b""
    if track_type == 3:
        switch_start = reader.m_pos
        reader.u8()  # eGroupType
        reader.u32()  # uGroupID
        reader.u32()  # uDefaultSwitch
        reader.take(4 * reader.u32())  # switch assoc
        reader.take(4 * 8)  # TransParams
        switch_params = hirc_object.payload[switch_start : reader.m_pos]
    look_ahead = reader.s32()
    if not reader.done():
        raise ValueError(f"track {hirc_object.id}: {len(hirc_object.payload) - reader.m_pos} unparsed bytes")
    return CMusicTrack(
        hirc_object.id,
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


def render_track(track: CMusicTrack) -> SHircObject:
    out = struct.pack("<I", len(track.m_sources))
    for source in track.m_sources:
        out += struct.pack(
            "<IBQIB", source.plugin_id, source.stream_type, source.source_id, source.in_memory_size, source.source_bits
        )
    out += struct.pack("<B", track.m_sources_tail) + struct.pack("<I", len(track.m_playlist))
    for item in track.m_playlist:
        out += struct.pack(
            "<IQIdddd",
            item.track_id,
            item.source_id,
            item.event_id,
            item.play_at,
            item.begin_trim,
            item.end_trim,
            item.source_duration_ms,
        )
    if track.m_playlist:
        out += struct.pack("<I", track.m_num_sub_track)
    out += (
        track.m_clip_automation
        + track.m_base_params
        + struct.pack("<B", track.m_track_type)
        + track.m_switch_params
        + struct.pack("<i", track.m_look_ahead)
    )
    return SHircObject(MUSIC_TRACK, track.m_id, out)


# MusicRanSeqCntr (playlist container)


@dataclass
class SPlaylistNode:
    segment_id: int
    item_id: int
    num_children: int
    rs_type: int  # 0xFFFFFFFF leaf; else sequence/random mode
    loop: int
    min_loop: int
    max_loop: int
    weight: int
    avoid_repeat: int
    use_weight: int
    shuffle: int


@dataclass
class SMusicPlaylist:
    id: int
    flags: int
    base_params: bytes
    children: list[int]
    meter: SMeterInfo
    stingers: bytes
    rules: bytes  # raw transition rules
    nodes: list[SPlaylistNode]

    def parent_id(self) -> int:
        return _parent_of(self.base_params)


SYNC_IMMEDIATE = 0
SYNC_EXIT_CUE = 7


@dataclass
class STransRule:
    """One music transition rule (source nodes -> destination nodes)."""

    source_ids: list[int]
    destination_ids: list[int]
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


def parse_trans_rules(reader: CReader) -> list[STransRule]:
    out = []
    for _ in range(reader.u32()):
        source_ids = [reader.u32() for _ in range(reader.u32())]
        destination_ids = [reader.u32() for _ in range(reader.u32())]
        source_part = (reader.s32(), reader.u32(), reader.s32(), reader.u32(), reader.u32(), reader.u8())
        destination_part = (
            reader.s32(),
            reader.u32(),
            reader.s32(),
            reader.u32(),
            reader.u32(),
            reader.u16(),
            reader.u16(),
            reader.u8(),
            reader.u8(),
        )
        transition_object = reader.take(30) if reader.u8() else b""
        out.append(STransRule(source_ids, destination_ids, *source_part, *destination_part, transition_object))
    return out


def render_trans_rules(rules: list[STransRule]) -> bytes:
    out = struct.pack("<I", len(rules))
    for rule in rules:
        out += struct.pack("<I", len(rule.source_ids)) + b"".join(
            struct.pack("<I", node_id) for node_id in rule.source_ids
        )
        out += struct.pack("<I", len(rule.destination_ids)) + b"".join(
            struct.pack("<I", node_id) for node_id in rule.destination_ids
        )
        out += struct.pack(
            "<iIiIIB",
            rule.src_fade_time,
            rule.src_fade_curve,
            rule.src_fade_offset,
            rule.sync_type,
            rule.cue_filter,
            rule.play_post_exit,
        )
        out += struct.pack(
            "<iIiIIHHBB",
            rule.dst_fade_time,
            rule.dst_fade_curve,
            rule.dst_fade_offset,
            rule.dst_cue_filter,
            rule.jump_to_id,
            rule.jump_to_type,
            rule.entry_type,
            rule.play_pre_entry,
            rule.dst_match_cue,
        )
        out += struct.pack("<B", 1) + rule.trans_object if rule.trans_object else struct.pack("<B", 0)
    return out


def _skip_container_head(reader: CReader) -> None:
    """Read past flags, node base, children, meter and stingers: the part a playlist and a switch container share."""
    reader.u8()
    skip_node_base(reader)
    reader.take(4 * reader.u32())
    _read_meter(reader)
    reader.take(24 * reader.u32())


def locate_trans_rules(payload: bytes) -> tuple[int, int]:
    """Byte range of the transition rules inside a playlist or switch container payload."""
    reader = CReader(payload)
    _skip_container_head(reader)
    start = reader.m_pos
    parse_trans_rules(reader)
    return start, reader.m_pos


def rewrite_trans_rules(hirc_object: SHircObject, edit) -> int:
    """Apply ``edit(rule) -> bool`` to every rule of a playlist/switch container; returns the number changed."""
    start, end = locate_trans_rules(hirc_object.payload)
    rules = parse_trans_rules(CReader(hirc_object.payload[start:end]))
    changed = sum(1 for rule in rules if edit(rule))
    if changed:
        hirc_object.payload = hirc_object.payload[:start] + render_trans_rules(rules) + hirc_object.payload[end:]
    return changed


def parse_playlist(hirc_object: SHircObject) -> SMusicPlaylist:
    if hirc_object.type != MUSIC_RANSEQ_CNTR:
        raise ValueError("not a MusicRanSeqCntr")
    reader = CReader(hirc_object.payload)
    flags = reader.u8()
    base = skip_node_base(reader)
    children = [reader.u32() for _ in range(reader.u32())]
    meter = _read_meter(reader)
    n_stingers = reader.u32()
    stingers = struct.pack("<I", n_stingers) + reader.take(24 * n_stingers)
    rules_at = reader.m_pos
    parse_trans_rules(reader)
    rules = hirc_object.payload[rules_at : reader.m_pos]
    nodes = []
    for _ in range(reader.u32()):
        nodes.append(
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
        )
    if not reader.done():
        raise ValueError(f"playlist {hirc_object.id}: {len(hirc_object.payload) - reader.m_pos} unparsed bytes")
    return SMusicPlaylist(hirc_object.id, flags, base, children, meter, stingers, rules, nodes)


def render_playlist(playlist: SMusicPlaylist) -> SHircObject:
    out = struct.pack("<B", playlist.flags) + playlist.base_params
    out += struct.pack("<I", len(playlist.children)) + b"".join(struct.pack("<I", child) for child in playlist.children)
    out += _write_meter(playlist.meter) + playlist.stingers + playlist.rules
    out += struct.pack("<I", len(playlist.nodes))
    for node in playlist.nodes:
        out += struct.pack(
            "<IIIIhhhIHBB",
            node.segment_id,
            node.item_id,
            node.num_children,
            node.rs_type,
            node.loop,
            node.min_loop,
            node.max_loop,
            node.weight,
            node.avoid_repeat,
            node.use_weight,
            node.shuffle,
        )
    return SHircObject(MUSIC_RANSEQ_CNTR, playlist.id, out)


# Shared node header helpers (playlist and switch containers)


def locate_children(payload: bytes) -> tuple[int, int]:
    """Byte range of the child id list (count + ids) after flags and base params."""
    reader = CReader(payload)
    reader.u8()
    skip_node_base(reader)
    start = reader.m_pos
    reader.take(4 * reader.u32())
    return start, reader.m_pos


def rewrite_children(hirc_object: SHircObject, children: list[int]) -> None:
    start, end = locate_children(hirc_object.payload)
    body = struct.pack("<I", len(children)) + b"".join(struct.pack("<I", child) for child in children)
    hirc_object.payload = hirc_object.payload[:start] + body + hirc_object.payload[end:]


def children_of(hirc_object: SHircObject) -> list[int]:
    start, end = locate_children(hirc_object.payload)
    reader = CReader(hirc_object.payload[start:end])
    return [reader.u32() for _ in range(reader.u32())]


@dataclass
class SSwitchTree:
    args: list[int]  # state/switch group ids, one per tree level
    tree_offset: int  # offset of the decision tree inside the payload
    tree: bytes

    def _node(self, index: int) -> int:
        """The byte offset of decision tree node ``index``, which must lie inside the tree."""
        if index < 0 or (index + 1) * 12 > len(self.tree):
            raise ValueError(f"decision tree node {index} lies outside its {len(self.tree)} bytes")
        return index * 12

    def leaves(self) -> list[tuple[list[int], int, int]]:
        """(state ids along the path, audio node id, byte offset of that id in the payload)."""
        out: list[tuple[list[int], int, int]] = []

        def walk(index: int, level: int, path: list[int]) -> None:
            offset = self._node(index)
            if level == len(self.args):
                out.append((path, struct.unpack_from("<I", self.tree, offset + 4)[0], self.tree_offset + offset + 4))
                return
            _key, children_index, count, _weight, _probability = struct.unpack_from("<IHHHH", self.tree, offset)
            for i in range(count):
                key = struct.unpack_from("<I", self.tree, self._node(children_index + i))[0]
                walk(children_index + i, level + 1, [*path, key])

        walk(0, 0, [])
        return out


def parse_switch_tree(payload: bytes) -> SSwitchTree:
    reader = CReader(payload)
    _skip_container_head(reader)
    parse_trans_rules(reader)
    reader.u8()  # bIsContinuePlayback
    depth = reader.u32()
    group_ids = [reader.u32() for _ in range(depth)]
    reader.take(depth)  # group types
    tree_size = reader.u32()
    reader.u8()  # mode
    tree_offset = reader.m_pos
    return SSwitchTree(group_ids, tree_offset, reader.take(tree_size))


def retarget_switch_leaves(hirc_object: SHircObject, predicate, new_node: int) -> int:
    """Point every decision tree leaf for which ``predicate(path, node)`` holds at ``new_node``."""
    tree = parse_switch_tree(hirc_object.payload)
    payload = bytearray(hirc_object.payload)
    changed = 0
    for path, node, offset in tree.leaves():
        if predicate(path, node):
            struct.pack_into("<I", payload, offset, new_node)
            changed += 1
    hirc_object.payload = bytes(payload)
    return changed
