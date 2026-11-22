"""The sound graph of a Wwise bank of any version the Anno games ship: which object leads to which.

``read_graph(objects, version)`` reads a bank's Sounds and containers (Random/Sequence, Switch, Actor-Mixer, Layer)
into ``SNode``s - parent, children, a sound's source, a switch container's group and the nodes each of its values
plays - and ``read_action`` / ``read_event`` read an action (kind, target, and a set-switch or set-state action's
group and value) and an event's action list. Versions: 112 (Anno 2205), 132 to 135 (Anno 1800), 172 (Anno 117);
the layouts and what is exact for each are in ``docs/sound/bank-objects.md``. The music objects stay with
``bnkmusic``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import bnkmusic
from .bnk import EVENT_HEADER, SHircObject
from .bnkmusic import CReader
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
class SBankLayout:
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
    OLD: SBankLayout(
        OLD, {"volume": 0, "pitch": 2, "low_pass": 3}, _NAMES_OLD, _GAP_UNKNOWN, 0, "<I",
        False, False, False, True, False, "", vorbis_channels={1: 0x4, 2: 0x3},
        stmg_head=6, stmg_tail=0,
    ),
    MID: SBankLayout(
        MID, {"volume": 0, "pitch": 2, "low_pass": 3}, _NAMES_OLD, _GAP_UNKNOWN, 0, "<B",
        True, False, False, True, False, "",
        stmg_head=8, stmg_tail=4,
    ),
    NEW: SBankLayout(
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


def layout(version: int) -> SBankLayout:
    """The layout record of a bank version."""
    return LAYOUTS[family(version)]


@dataclass
class SSource:
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
class SSwitch:
    """A switch container: the group it follows (a switch group, or a state group), the value it starts with and the
    nodes each value plays."""

    state_group: bool
    group: int
    default: int
    branches: dict[int, list[int]] = field(default_factory=dict)


@dataclass
class SNode:
    id: int
    type: int
    parent: int = 0
    children: list[int] = field(default_factory=list)
    source: SSource | None = None
    switch: SSwitch | None = None
    exact: bool = True  # whether the child list (and a switch's table) was found where the layout says


@dataclass(frozen=True)
class SAction:
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


def read_action(hirc_object: SHircObject) -> SAction:
    """An action object: ids, props and ranged props, then what its type adds (the group and value of a set-switch
    or set-state action)."""
    reader = CReader(hirc_object.payload)
    kind, target = reader.u16(), reader.u32()
    if kind >> 8 not in (SET_STATE, SET_SWITCH):
        return SAction(kind, target)
    reader.u8()
    reader.take(5 * reader.u8())
    reader.take(9 * reader.u8())
    at = reader.m_pos
    return SAction(kind, target, reader.u32(), reader.u32(), at)


def read_event(hirc_object: SHircObject, version: int) -> list[int]:
    """The actions an event runs (a bank of 117: after seven bytes of header; 132 to 135 a one-byte count; 112 four
    bytes). ``ValueError`` for a payload shorter than its count says."""
    bank_layout = layout(version)
    try:
        count = struct.unpack_from(bank_layout.action_count, hirc_object.payload, bank_layout.event_head)[0]
        at = bank_layout.event_head + struct.calcsize(bank_layout.action_count)
        return list(struct.unpack_from(f"<{count}I", hirc_object.payload, at))
    except struct.error as error:
        raise ValueError(f"event {hirc_object.id}: its action list is cut short") from error


# a node base's head up to the parent id: the override flag, the effect list, the metadata list (117), the bus


def skip_head(reader: CReader, bank_family: str) -> int:
    """Read a node base up to and including its parent id (returned); the bit vector and the property lists follow."""
    if bank_family == NEW:
        return bnkmusic.skip_head(reader)
    reader.u8()
    effect_count = reader.u8()
    if effect_count:
        reader.u8()
        reader.take(7 * effect_count)
    reader.u8()
    reader.u32()
    return reader.u32()


def base_start(hirc_object: SHircObject, version: int) -> int:
    """Where an object's node base starts in its payload (after a Sound's source record, after a music object's flag
    byte in 117's banks). ``ValueError`` for an object kind that has none this module reads."""
    bank_family = family(version)
    if hirc_object.type == SOUND:
        reader = CReader(hirc_object.payload)
        read_source(reader, bank_family)
        return reader.m_pos
    if hirc_object.type in CONTAINERS:
        return 0
    if hirc_object.type in MUSIC_BASE and bank_family == NEW:
        return 1
    raise ValueError(f"{hirc_object.type_name} {hirc_object.id} has no node base read in a bank of version {version}")


def parent_of(hirc_object: SHircObject, version: int) -> int:
    """The container an object hangs under (0: none)."""
    reader = CReader(hirc_object.payload)
    reader.m_pos = base_start(hirc_object, version)
    return skip_head(reader, family(version))


def with_parent(hirc_object: SHircObject, version: int, parent: int) -> SHircObject:
    """``obj`` under another container: only the parent id changes."""
    reader = CReader(hirc_object.payload)
    reader.m_pos = base_start(hirc_object, version)
    skip_head(reader, family(version))
    at = reader.m_pos - 4
    return SHircObject(
        hirc_object.type,
        hirc_object.id,
        hirc_object.payload[:at] + struct.pack("<I", parent) + hirc_object.payload[at + 4 :],
    )


def read_prop_lists(reader: CReader) -> tuple[dict[int, bytes], dict[int, bytes]]:
    """A node base's two property lists, after its bit vector: single values (4 bytes) and random ranges (two
    floats), each by id. ``ValueError`` for an id twice (the head before it was not read right)."""
    ids = list(reader.take(reader.u8()))
    singles = {i: reader.take(4) for i in ids}
    ranged_ids = list(reader.take(reader.u8()))
    ranged = {i: reader.take(8) for i in ranged_ids}
    if len(singles) != len(ids) or len(ranged) != len(ranged_ids):
        raise ValueError("a property twice in the node base")
    return singles, ranged


def read_source(reader: CReader, bank_family: str) -> SSource:
    """A Sound's source record (the layouts per version: ``docs/sound/bank-objects.md``)."""
    plugin, stream = reader.u32(), reader.u8()
    media_id = reader.u64() if bank_family == NEW else reader.u32()
    source = SSource(
        plugin, stream, media_id & 0xFFFFFFFF, high=media_id >> 32
    )  # 117's id has the media id in its low half
    if bank_family == OLD:
        source.file_id = reader.u32()
        if stream != 2:
            source.offset = reader.u32()
    source.size = reader.u32()
    source.flags = reader.u8()
    if plugin & 0xF == SOURCE_PLUGIN:
        source.params = reader.take(reader.u32())
    return source


def render_source(source: SSource, bank_family: str) -> bytes:
    """The bytes ``read_source`` read (a source made by hand, or one read and changed)."""
    out = struct.pack("<IB", source.plugin, source.stream)
    out += (
        struct.pack("<Q", source.high << 32 | source.media) if bank_family == NEW else struct.pack("<I", source.media)
    )
    if bank_family == OLD:
        out += struct.pack("<I", source.file_id) + (b"" if source.stream == 2 else struct.pack("<I", source.offset))
    out += struct.pack("<IB", source.size, source.flags)
    if source.plugin & 0xF == SOURCE_PLUGIN:
        out += struct.pack("<I", len(source.params)) + source.params
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


def _switch_table(payload: bytes, at: int, children: list[int]) -> SSwitch | None:
    """A switch container's group (ten bytes before its child list) and the table after the list: per value the nodes
    it plays."""
    try:
        reader = CReader(payload)
        reader.m_pos = at - SWITCH_GROUP_GAP
        kind, group, default, _continuous = reader.u8(), reader.u32(), reader.u32(), reader.u8()
        if kind > 1:
            return None
        reader.m_pos = at + 4 + 4 * len(children)
        out = SSwitch(bool(kind), group, default)
        known = set(children)
        for _ in range(reader.u32()):
            value = reader.u32()
            nodes = [reader.u32() for _ in range(reader.u32())]
            if not set(nodes) <= known:
                return None
            out.branches[value] = nodes
        return out
    except (ValueError, struct.error):
        return None


def read_graph(objects: list[SHircObject], version: int) -> dict[int, SNode]:
    """Every Sound and container of a bank by id. A child list is read by the layout of a bank of 117's node bases;
    where that fails, and in the older banks, it is the block of the payload that holds exactly the objects naming the
    container as their parent (``SNode.exact`` is False where no block was found)."""
    bank_family = family(version)
    nodes = {
        hirc_object.id: SNode(hirc_object.id, hirc_object.type)
        for hirc_object in objects
        if hirc_object.type == SOUND or hirc_object.type in CONTAINERS
    }
    for hirc_object in objects:
        if hirc_object.id in nodes:
            _read_head(hirc_object, nodes[hirc_object.id], bank_family)
    child_ids: dict[int, set[int]] = {}
    for child in nodes.values():
        child_ids.setdefault(child.parent, set()).add(child.id)
    for hirc_object in objects:
        node = nodes.get(hirc_object.id)
        if node is not None and hirc_object.type != SOUND:
            _read_children(hirc_object, node, bank_family, child_ids.get(hirc_object.id, set()))
    return nodes


def _read_head(hirc_object: SHircObject, node: SNode, bank_family: str) -> None:
    """A node's parent, and a Sound's source."""
    try:
        reader = CReader(hirc_object.payload)
        if hirc_object.type == SOUND:
            node.source = read_source(reader, bank_family)
            if bank_family == NEW:  # the node base follows the source record
                reader = CReader(hirc_object.payload[reader.m_pos :])
        node.parent = skip_head(reader, bank_family)
    except (ValueError, struct.error):
        node.exact = False


def child_block(hirc_object: SHircObject, bank_family: str, wanted: set[int]) -> int | None:
    """Where a container's child list (count, ids) starts in its payload: by the layout of a bank of 117's node bases,
    else (and in the older banks) the block that holds exactly the objects naming the container as their parent."""
    gap = LAYOUTS[bank_family].child_gap.get(hirc_object.type)
    if gap is not None:
        try:
            reader = CReader(hirc_object.payload)
            bnkmusic.skip_node_base(reader)
            reader.take(gap)
            at = reader.m_pos
            reader.take(4 * reader.u32())
            return at
        except (ValueError, struct.error):
            pass
    return _find_children(hirc_object.payload, wanted, 0) if wanted else _empty_block(hirc_object)


def _empty_block(hirc_object: SHircObject) -> int | None:
    """Where the child list of a container nothing names sits: an empty list ends a Random/Sequence container (child
    count u32, playlist count u16) or an Actor-Mixer (child count u32) in zero bytes (``docs/sound/bank-objects.md``
    "A container emptied of its last child")."""
    tail = EMPTY_TAIL.get(hirc_object.type)
    if tail is None or len(hirc_object.payload) < tail or any(hirc_object.payload[-tail:]):
        return None
    return len(hirc_object.payload) - tail


def _read_children(hirc_object: SHircObject, node: SNode, bank_family: str, wanted: set[int]) -> None:
    """A container's child list (and a switch container's table) by layout, else by the objects that name it."""
    at = child_block(hirc_object, bank_family, wanted)
    if at is None:
        if wanted:
            node.exact = False
        return
    count = struct.unpack_from("<I", hirc_object.payload, at)[0]
    node.children = list(struct.unpack_from(f"<{count}I", hirc_object.payload, at + 4))
    if hirc_object.type == SWITCH:
        node.switch = _switch_table(hirc_object.payload, at, node.children)
        node.exact = node.switch is not None
