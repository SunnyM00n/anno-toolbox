"""The sound graph of a Wwise bank of any version the Anno games ship: which object leads to which.

``read_graph(objects, version)`` reads a bank's Sounds and containers (Random/Sequence, Switch, Actor-Mixer, Layer)
into ``Node``s - parent, children, a sound's source, a switch container's group and the nodes each of its values
plays - and ``read_action`` / ``read_event`` read an action (kind, target, and a set-switch or set-state action's
group and value) and an event's action list. Versions: 112 (Anno 2205), 132 to 135 (Anno 1800), 172 (Anno 117);
the layouts and what is exact for each are in ``docs/sound/bank-objects.md``. The music objects stay with
``bnkmusic``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import bnkmusic
from .bnk import EVENT_HEADER, HircObject
from .bnkmusic import Reader
from .wwvorbis import CHANNEL_CONFIG

SOUND, ACTION, EVENT, RANSEQ, SWITCH, MIXER, LAYER = 2, 3, 4, 5, 6, 7, 9
ATTENUATION = 14  # an Attenuation object's type id
CONTAINERS = (RANSEQ, SWITCH, MIXER, LAYER)
PLAY, SET_STATE, SET_SWITCH = 4, 0x12, 0x19  # an action's type (the high byte of its 16-bit kind)
OLD, MID, NEW = "old", "mid", "new"  # the families of layouts: 112; 132 to 135; 172
SOURCE_PLUGIN = 2  # a plugin id whose low nibble is 2 is a source plugin (silence) with parameters of its own
MUSIC_BASE = (bnkmusic.MUSIC_SEGMENT, bnkmusic.MUSIC_SWITCH_CNTR, bnkmusic.MUSIC_RANSEQ_CNTR)  # a flag byte first (117)


def family(version: int) -> str:
    """The layout family of a bank version."""
    return OLD if version < 128 else MID if version < 140 else NEW


@dataclass(frozen=True)
class BankLayout:
    """What differs between the families of banks (``docs/sound/bank-objects.md``), so the code asks the record, not
    the family's name. ``child_gap``: the bytes between a container's node base and its child list (a random or
    sequence container's loop and transition settings, a switch container's group), None where the layout is not
    known and the list is found by the objects that name the container."""

    family: str
    prop_ids: dict[str, int]  # the ids of the volume, pitch and low-pass properties
    prop_names: dict[int, str]
    child_gap: dict[int, int | None]
    event_head: int  # the bytes before an event's action count
    action_count: str  # its struct format
    stream0_embedded: bool  # audio stored in the bank (stream 0) is in it; the oldest banks keep it in a package
    media_high_word: bool  # a source's media id is 64 bits and the bank's own media are found by the high word
    prefetch: bool  # a streamed source keeps the start of its clip in the bank
    vorbis_known: bool  # the Wwise Vorbis layout is known (a clip can be written)
    music_readable: bool  # the music objects are read and edited
    loose_folder: str  # where a loose clip goes under the bank's root
    attenuation_height: bool = False  # an Attenuation starts with the height-spread byte
    attenuation_slots: int = 7  # the curve-to-use bytes of an Attenuation (volume dry ... focus; 172 has more)
    vorbis_channels: dict[int, int] = field(default_factory=lambda: dict(CHANNEL_CONFIG))  # the u32 of a Vorbis .wem's
    # format chunk by channel count (the speaker mask alone in the oldest banks)
    stmg_head: int = field(kw_only=True)  # the bytes before the state groups of the Init bank's state manager
    stmg_tail: int = field(kw_only=True)  # the bytes after its game parameters


_NAMES_OLD = {0: "volume (dB)", 1: "LFE (dB)", 2: "pitch (cents)", 3: "low-pass filter"}
_GAP_UNKNOWN: dict[int, int | None] = dict.fromkeys((RANSEQ, SWITCH, MIXER, LAYER))
CHILD_GAP = {
    RANSEQ: 24,
    SWITCH: 10,
    MIXER: 0,
    LAYER: 0,
}  # bytes from a node base's end to the child list, every version
# fmt: off
LAYOUTS = {
    OLD: BankLayout(
        OLD, {"volume": 0, "pitch": 2, "low_pass": 3}, _NAMES_OLD, _GAP_UNKNOWN, 0, "<I",
        False, False, False, True, False, "", vorbis_channels={1: 0x4, 2: 0x3},
        stmg_head=6, stmg_tail=0,
    ),
    MID: BankLayout(
        MID, {"volume": 0, "pitch": 2, "low_pass": 3}, _NAMES_OLD, _GAP_UNKNOWN, 0, "<B",
        True, False, False, True, False, "",
        stmg_head=8, stmg_tail=4,
    ),
    NEW: BankLayout(
        NEW,
        {"volume": 0, "pitch": 1, "low_pass": 2},
        {0: "volume (dB)", 1: "pitch (cents)", 2: "low-pass filter", 3: "high-pass filter"},
        CHILD_GAP,
        EVENT_HEADER, "<B", True, True, True, True, True, "media/", attenuation_height=True, attenuation_slots=24,
        stmg_head=14, stmg_tail=4,
    ),
}
# fmt: on
EMPTY_TAIL = {RANSEQ: 6, MIXER: 4}  # the zero bytes an emptied container's payload ends in
SWITCH_GROUP_GAP = 10  # a switch container's group sits this far before its child list, in every family


def layout(version: int) -> BankLayout:
    """The layout record of a bank version."""
    return LAYOUTS[family(version)]


@dataclass
class Source:
    """A Sound's media: the codec plugin, how it is stored (0 in the bank, 1 prefetched, 2 streamed), the media id
    and, in the oldest banks, the file id and the offset in a file package."""

    plugin: int
    stream: int
    media: int
    file_id: int = 0
    offset: int = 0
    size: int = 0
    flags: int = 0
    high: int = 0  # 117: the upper half of the 64-bit media id (the bank's own media are found by it)
    params: bytes = b""  # a source plugin's parameters (silence)


@dataclass
class Switch:
    """A switch container: the group it follows (a switch group, or a state group), the value it starts with and the
    nodes each value plays."""

    state_group: bool
    group: int
    default: int
    branches: dict[int, list[int]] = field(default_factory=dict)


@dataclass
class Node:
    id: int
    type: int
    parent: int = 0
    children: list[int] = field(default_factory=list)
    source: Source | None = None
    switch: Switch | None = None
    exact: bool = True  # whether the child list (and a switch's table) was found where the layout says


@dataclass(frozen=True)
class Action:
    """An action: its kind (the low byte the scope, the high byte the type), the object it acts on and, for a
    set-switch or set-state action, the group and the value it sets."""

    kind: int
    target: int
    group: int = 0
    value: int = 0
    at: int = field(default=0, compare=False)  # where the group and value sit in the payload (set-switch, set-state)

    @property
    def type(self) -> int:
        return self.kind >> 8


def read_action(obj: HircObject) -> Action:
    """An action object: ids, props and ranged props, then what its type adds (the group and value of a set-switch
    or set-state action)."""
    r = Reader(obj.payload)
    kind, target = r.u16(), r.u32()
    if kind >> 8 not in (SET_STATE, SET_SWITCH):
        return Action(kind, target)
    r.u8()
    r.take(5 * r.u8())
    r.take(9 * r.u8())
    at = r.pos
    return Action(kind, target, r.u32(), r.u32(), at)


def read_event(obj: HircObject, version: int) -> list[int]:
    """The actions an event runs (a bank of 117: after seven bytes of header; 132 to 135 a one-byte count; 112 four
    bytes)."""
    lay = layout(version)
    count = struct.unpack_from(lay.action_count, obj.payload, lay.event_head)[0]
    at = lay.event_head + struct.calcsize(lay.action_count)
    return list(struct.unpack_from(f"<{count}I", obj.payload, at))


# a node base's head up to the parent id: the override flag, the effect list, the metadata list (117), the bus


def skip_head(r: Reader, fam: str) -> int:
    """Read a node base up to and including its parent id (returned); the bit vector and the property lists follow."""
    if fam == NEW:
        return bnkmusic.skip_head(r)
    r.u8()
    n = r.u8()
    if n:
        r.u8()
        r.take(7 * n)
    r.u8()
    r.u32()
    return r.u32()


def base_start(obj: HircObject, version: int) -> int:
    """Where an object's node base starts in its payload (after a Sound's source record, after a music object's flag
    byte in 117's banks). ``ValueError`` for an object kind that has none this module reads."""
    fam = family(version)
    if obj.type == SOUND:
        r = Reader(obj.payload)
        read_source(r, fam)
        return r.pos
    if obj.type in CONTAINERS:
        return 0
    if obj.type in MUSIC_BASE and fam == NEW:
        return 1
    raise ValueError(f"{obj.type_name} {obj.id} has no node base read in a bank of version {version}")


def parent_of(obj: HircObject, version: int) -> int:
    """The container an object hangs under (0: none)."""
    r = Reader(obj.payload)
    r.pos = base_start(obj, version)
    return skip_head(r, family(version))


def with_parent(obj: HircObject, version: int, parent: int) -> HircObject:
    """``obj`` under another container: only the parent id changes."""
    r = Reader(obj.payload)
    r.pos = base_start(obj, version)
    skip_head(r, family(version))
    at = r.pos - 4
    return HircObject(obj.type, obj.id, obj.payload[:at] + struct.pack("<I", parent) + obj.payload[at + 4 :])


def read_prop_lists(r: Reader) -> tuple[dict[int, bytes], dict[int, bytes]]:
    """A node base's two property lists, after its bit vector: single values (4 bytes) and random ranges (two
    floats), each by id. ``ValueError`` for an id twice (the head before it was not read right)."""
    ids = list(r.take(r.u8()))
    props = {i: r.take(4) for i in ids}
    ranged_ids = list(r.take(r.u8()))
    ranged = {i: r.take(8) for i in ranged_ids}
    if len(props) != len(ids) or len(ranged) != len(ranged_ids):
        raise ValueError("a property twice in the node base")
    return props, ranged


def read_source(r: Reader, fam: str) -> Source:
    """A Sound's source record (the layouts per version: ``docs/sound/bank-objects.md``)."""
    plugin, stream = r.u32(), r.u8()
    wide = r.u64() if fam == NEW else r.u32()
    source = Source(plugin, stream, wide & 0xFFFFFFFF, high=wide >> 32)  # 117's id has the media id in its low half
    if fam == OLD:
        source.file_id = r.u32()
        if stream != 2:
            source.offset = r.u32()
    source.size = r.u32()
    source.flags = r.u8()
    if plugin & 0xF == SOURCE_PLUGIN:
        source.params = r.take(r.u32())
    return source


def render_source(s: Source, fam: str) -> bytes:
    """The bytes ``read_source`` read (a source made by hand, or one read and changed)."""
    out = struct.pack("<IB", s.plugin, s.stream)
    out += struct.pack("<Q", s.high << 32 | s.media) if fam == NEW else struct.pack("<I", s.media)
    if fam == OLD:
        out += struct.pack("<I", s.file_id) + (b"" if s.stream == 2 else struct.pack("<I", s.offset))
    out += struct.pack("<IB", s.size, s.flags)
    if s.plugin & 0xF == SOURCE_PLUGIN:
        out += struct.pack("<I", len(s.params)) + s.params
    return out


def _find_children(payload: bytes, wanted: set[int], start: int) -> int | None:
    """Where the child list (count, ids) of a container sits: the one block after ``start`` whose ids are exactly the
    objects that name the container as their parent."""
    count = len(wanted)
    for at in range(start, len(payload) - 4 * count - 3):
        if struct.unpack_from("<I", payload, at)[0] == count:
            if set(struct.unpack_from(f"<{count}I", payload, at + 4)) == wanted:
                return at
    return None


def _switch_table(payload: bytes, at: int, children: list[int]) -> Switch | None:
    """A switch container's group (ten bytes before its child list) and the table after the list: per value the nodes
    it plays."""
    try:
        r = Reader(payload)
        r.pos = at - SWITCH_GROUP_GAP
        kind, group, default, _continuous = r.u8(), r.u32(), r.u32(), r.u8()
        if kind > 1:
            return None
        r.pos = at + 4 + 4 * len(children)
        out = Switch(bool(kind), group, default)
        known = set(children)
        for _ in range(r.u32()):
            value = r.u32()
            nodes = [r.u32() for _ in range(r.u32())]
            if not set(nodes) <= known:
                return None
            out.branches[value] = nodes
        return out
    except (ValueError, struct.error):
        return None


def read_graph(objects: list[HircObject], version: int) -> dict[int, Node]:
    """Every Sound and container of a bank by id. A child list is read by the layout of a bank of 117's node bases;
    where that fails, and in the older banks, it is the block of the payload that holds exactly the objects naming the
    container as their parent (``Node.exact`` is False where no block was found)."""
    fam = family(version)
    nodes = {o.id: Node(o.id, o.type) for o in objects if o.type == SOUND or o.type in CONTAINERS}
    for o in objects:
        if o.id in nodes:
            _read_head(o, nodes[o.id], fam)
    kids: dict[int, set[int]] = {}
    for n in nodes.values():
        kids.setdefault(n.parent, set()).add(n.id)
    for o in objects:
        node = nodes.get(o.id)
        if node is not None and o.type != SOUND:
            _read_children(o, node, fam, kids.get(o.id, set()))
    return nodes


def _read_head(obj: HircObject, node: Node, fam: str) -> None:
    """A node's parent, and a Sound's source."""
    try:
        r = Reader(obj.payload)
        if obj.type == SOUND:
            node.source = read_source(r, fam)
            if fam == NEW:  # the node base follows the source record
                r = Reader(obj.payload[r.pos :])
        node.parent = skip_head(r, fam)
    except (ValueError, struct.error):
        node.exact = False


def child_block(obj: HircObject, fam: str, wanted: set[int]) -> int | None:
    """Where a container's child list (count, ids) starts in its payload: by the layout of a bank of 117's node bases,
    else (and in the older banks) the block that holds exactly the objects naming the container as their parent."""
    gap = LAYOUTS[fam].child_gap.get(obj.type)
    if gap is not None:
        try:
            r = Reader(obj.payload)
            bnkmusic.skip_node_base(r)
            r.take(gap)
            at = r.pos
            r.take(4 * r.u32())
            return at
        except (ValueError, struct.error):
            pass
    return _find_children(obj.payload, wanted, 0) if wanted else _empty_block(obj)


def _empty_block(obj: HircObject) -> int | None:
    """Where the child list of a container nothing names sits: an empty list ends a Random/Sequence container (child
    count u32, playlist count u16) or an Actor-Mixer (child count u32) in zero bytes (``docs/sound/bank-objects.md``
    "A container emptied of its last child")."""
    tail = EMPTY_TAIL.get(obj.type)
    if tail is None or len(obj.payload) < tail or any(obj.payload[-tail:]):
        return None
    return len(obj.payload) - tail


def _read_children(obj: HircObject, node: Node, fam: str, wanted: set[int]) -> None:
    """A container's child list (and a switch container's table) by layout, else by the objects that name it."""
    at = child_block(obj, fam, wanted)
    if at is None:
        if wanted:
            node.exact = False
        return
    count = struct.unpack_from("<I", obj.payload, at)[0]
    node.children = list(struct.unpack_from(f"<{count}I", obj.payload, at + 4))
    if obj.type == SWITCH:
        node.switch = _switch_table(obj.payload, at, node.children)
        node.exact = node.switch is not None
