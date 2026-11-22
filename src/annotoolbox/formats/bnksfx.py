"""Wwise objects for sound effects (bank version 172): the pieces of a bank of our own.

``bnkmusic`` models the music objects; this module builds the few that a building or unit sound needs - Sound,
Random/Sequence container, Actor-Mixer, Attenuation, Event and its Play/Stop actions - by taking a plain vanilla
object of each kind as the donor, read from the installed game's ``BNK_PREPARED`` at run time, and patching only
ids, sources and the properties we mean (sounds.md "What 117 does", "The building sounds": Donors).
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

from . import bnk, bnkgraph, bnkmusic
from .bnkgraph import ACTION, ATTENUATION, EVENT, MIXER, RANSEQ, SOUND

BANK_VERSION = 172  # the bank version the builders write
IN_MEMORY, PREFETCHED, STREAMED = 0, 1, 2  # media in the bank's DATA / its start there and the rest loose / all loose
HAS_SOURCE = 8  # source flag bit of a source whose start is in the bank (the shipped streamed voices carry 9)
PREFETCH = 3000  # "in memory" size the music mod gives its streamed PCM tracks
PLAY, STOP = 0x0403, 0x0103  # action types (scope: object, not bus)
STOP_TAIL = b"\x07\x06\x00"  # curve, flags, empty exception list: as the shipped stop actions
# property ids of bank version 172 (1 is pitch, 2 the low-pass filter: read from the shipped banks, where 1 is
# random in +-100 cents and 2 runs 0 to 20); loop 0 = endless
PROP_VOLUME, PROP_PITCH, PROP_LOOP, PROP_ATTENUATION = 0, 1, 0x4A, 0x4B
BKHD_LANGUAGE = 0x17705D3E  # the language id a bank without a language carries
MEDIA_ALIGN = 16  # the shipped banks' media offsets are multiples of this (the BKHD alignment)

# plain objects of BNK_PREPARED: a looping Sound, an Actor-Mixer with 3D positioning and an attenuation,
# a Random container of Sounds that plays once; found by shape when a game update renumbers them
DONOR_SOUND, DONOR_MIXER, DONOR_RANSEQ = 351248216, 1069446043, 565123793


class CDonorError(ValueError):
    """The donor bank lacks an object the builders clone."""


@dataclass
class CNodeBase:
    """The node base parameters up to the property lists; positioning, aux, advanced settings, state
    and RTPC stay opaque in ``tail``."""

    m_head: bytes  # effects and metadata overrides: none in the objects we clone
    m_bus: int
    m_parent: int
    m_bits: int
    m_props: dict[int, bytes] = field(default_factory=dict)  # id -> 4 bytes
    m_ranged: dict[int, bytes] = field(default_factory=dict)  # id -> min and max as two floats
    m_tail: bytes = b""

    def render(self) -> bytes:
        out = self.m_head + struct.pack("<IIB", self.m_bus, self.m_parent, self.m_bits)
        ids = sorted(self.m_props)
        out += bytes([len(ids)]) + bytes(ids) + b"".join(self.m_props[property_id] for property_id in ids)
        ids = sorted(self.m_ranged)
        out += bytes([len(ids)]) + bytes(ids) + b"".join(self.m_ranged[property_id] for property_id in ids)
        return out + self.m_tail

    def set_float(self, prop: int, value: float) -> None:
        self.m_props[prop] = struct.pack("<f", value)

    def set_uint(self, prop: int, value: int) -> None:
        self.m_props[prop] = struct.pack("<I", value)

    def set_range(self, prop: int, low: float, high: float) -> None:
        self.m_ranged[prop] = struct.pack("<ff", low, high)


def parse_node_base(payload: bytes, start_bytes: int = 0) -> tuple[CNodeBase, int]:
    """The node base starting at ``pos`` and the offset where it ends."""
    reader = bnkmusic.CReader(payload)
    reader.m_pos = start_bytes
    end = start_bytes + len(bnkmusic.skip_node_base(reader))
    reader = bnkmusic.CReader(payload)
    reader.m_pos = start_bytes
    head = reader.take(4)
    if head != b"\0\0\0\0":
        raise CDonorError("donor object has effects or metadata overrides")
    bus, parent, bits = reader.u32(), reader.u32(), reader.u8()
    props, ranged = bnkgraph.read_prop_lists(reader)
    return CNodeBase(head, bus, parent, bits, props, ranged, payload[reader.m_pos : end]), end


def _positioning(node_base: CNodeBase) -> int:
    return node_base.m_tail[0] if node_base.m_tail else 0


@dataclass(frozen=True)
class SLimit:
    """How many instances an Actor-Mixer lets play at once (all emitters together) and what a sound does
    when it falls below the volume threshold: 0 play on, 1 kill, 2 virtual, 3 kill when finite else virtual."""

    max_instances: int
    below_threshold: int = 2
    kill_newest: bool = True  # at the limit the new sound is not started, as in the shipped root mixers
    queue: int = field(
        default=1, compare=False
    )  # a voice made virtual resumes: 0 from the beginning, 1 from elapsed time


ADV_KILL_NEWEST, ADV_VIRTUAL, ADV_IGNORE_PARENT = 1, 2, 4  # advanced settings bit vector
PRIORITY_BY_DISTANCE = 2  # node base bit: the distance lowers a sound's priority (the shipped roots set it)


def _adv_offset(tail: bytes) -> int:
    """Where the six advanced-settings bytes start inside a node base ``tail`` (positioning and aux first)."""
    reader = bnkmusic.CReader(tail)
    bits = reader.u8()
    if bits & 1 and bits & 2 and (reader.u8() >> 5) & 3:
        raise CDonorError("positioning with an automated path is not handled")
    if reader.u8() & 8:  # AuxParams: bHasAux
        reader.take(16)
    reader.u32()  # reflections aux bus
    return reader.m_pos


def set_limit(node_base: CNodeBase, limit: SLimit, ignore_parent: bool = True) -> None:
    """Write the instance limit and virtual-voice behaviour into the advanced settings of ``node_base``;
    ``ignore_parent`` False keeps the parent's limit counting too (a Sound under a limited mixer)."""
    offset_bytes = _adv_offset(node_base.m_tail)
    bits = ADV_VIRTUAL | (ADV_IGNORE_PARENT if ignore_parent else 0) | (ADV_KILL_NEWEST if limit.kill_newest else 0)
    advanced = struct.pack("<BBHB", bits, limit.queue, limit.max_instances, limit.below_threshold)
    node_base.m_tail = node_base.m_tail[:offset_bytes] + advanced + node_base.m_tail[offset_bytes + 5 :]
    node_base.m_bits |= PRIORITY_BY_DISTANCE


def get_limit(node_base: CNodeBase) -> SLimit:
    """The instance limit written in the advanced settings of ``node_base`` (:func:`set_limit` reads back)."""
    bits, queue, count, below_threshold = struct.unpack_from("<BBHB", node_base.m_tail, _adv_offset(node_base.m_tail))
    return SLimit(count, below_threshold, bool(bits & ADV_KILL_NEWEST), queue)


# game parameter curves (InitialRTPC) and attenuations

RTPC_GAME_PARAMETER, RTPC_ADDITIVE, RTPC_VOLUME, SCALE_DB = 0, 2, 0, 2  # as the shipped volume curves
LINEAR = 4  # curve interpolation
SILENT = -1.0  # a dB-scaled curve stores 10^(dB/20) - 1: 0 is 0 dB, -1 no sound


@dataclass(frozen=True)
class SCurve:
    """A volume curve over a game parameter: (parameter value, stored volume) points, linear between them."""

    parameter: int  # the game parameter's id (fnv1_32 of its name)
    curve_id: int
    points: tuple[tuple[float, float], ...]

    def render(self) -> bytes:
        head = struct.pack("<IBBBIBH", self.parameter, RTPC_GAME_PARAMETER, RTPC_ADDITIVE, RTPC_VOLUME, self.curve_id,
                           SCALE_DB, len(self.points))  # fmt: skip
        return head + b"".join(struct.pack("<ffI", x, y, LINEAR) for x, y in self.points)


def _rtpc_offset(tail: bytes) -> int:
    """Where the InitialRTPC block starts inside a node base ``tail``: after the advanced settings and states."""
    reader = bnkmusic.CReader(tail)
    reader.m_pos = _adv_offset(tail) + 6
    reader.take(3 * reader.u8())  # state properties
    for _ in range(reader.u8()):  # state groups
        reader.take(9)
        reader.take(12 * reader.u8())
    return reader.m_pos


def set_curves(node_base: CNodeBase, curves: list[SCurve]) -> None:
    """Replace the game parameter curves of ``node_base`` with ``curves`` (the tail ends with them)."""
    offset_bytes = _rtpc_offset(node_base.m_tail)
    node_base.m_tail = (
        node_base.m_tail[:offset_bytes] + struct.pack("<H", len(curves)) + b"".join(curve.render() for curve in curves)
    )


def _attenuation_curves(payload: bytes) -> tuple[int, list[tuple[int, int]]] | None:
    """Where an Attenuation's curve list starts and the (offset, count) of each curve's points, or None. The list
    is found where it ends exactly at the trailing game parameter block, as the layout before it varies."""
    for start in range(0, min(len(payload), 48)):
        reader = bnkmusic.CReader(payload)
        reader.m_pos = start
        try:
            curve_count = reader.u8()
            if not 0 < curve_count <= 19:
                continue
            spans = []
            for _ in range(curve_count):
                if reader.u8() > 4:  # scaling
                    raise ValueError
                count = reader.u16()
                spans.append((reader.m_pos, count))
                reader.take(12 * count)
            if reader.u16() == 0 and reader.m_pos == len(payload):
                return start, spans
        except (ValueError, struct.error, IndexError):
            continue
    return None


def attenuation_radius(payload: bytes) -> float:
    """The distance at which the attenuation's first (volume) curve ends."""
    found = _attenuation_curves(payload)
    if found is None:
        raise CDonorError("the donor attenuation's curves were not found")
    offset_bytes, count = found[1][0]
    return struct.unpack_from("<f", payload, offset_bytes + 12 * (count - 1))[0]


def shaped_attenuation(payload: bytes, far: float, gains: tuple[tuple[float, float], ...]) -> bytes:
    """The attenuation with a volume curve through ``gains`` ((distance, linear gain) points, the last at ``far``).
    The curve stores 10^(dB/20) - 1, so a gain is stored as gain - 1 and the line between two points is a gain
    falling linearly; the other curves keep their shape stretched to ``far``."""
    found = _attenuation_curves(payload)
    if found is None:
        raise CDonorError("the donor attenuation's curves were not found")
    spans = found[1]
    factor = far / attenuation_radius(payload)
    out = bytearray(payload)
    for offset_bytes, point_count in spans:
        for i in range(point_count):
            struct.pack_into(
                "<f", out, offset_bytes + 12 * i, struct.unpack_from("<f", out, offset_bytes + 12 * i)[0] * factor
            )
    offset_bytes, point_count = spans[0]
    line = b"".join(struct.pack("<ffI", distance, gain - 1.0, LINEAR) for distance, gain in gains)
    return (
        bytes(out[: offset_bytes - 2])
        + struct.pack("<H", len(gains))
        + line
        + bytes(out[offset_bytes + 12 * point_count :])
    )


def linear_attenuation(payload: bytes, far: float) -> bytes:
    """The attenuation with a straight gain fall: full at the source, silent at ``far``."""
    return shaped_attenuation(payload, far, ((0.0, 1.0), (far, 0.0)))


def attenuation_gains(payload: bytes) -> list[tuple[float, float]]:
    """The (distance, linear gain) points of the attenuation's volume curve."""
    found = _attenuation_curves(payload)
    if found is None:
        raise CDonorError("the donor attenuation's curves were not found")
    offset_bytes, point_count = found[1][0]
    return [
        (distance, stored_gain + 1.0)
        for distance, stored_gain, _ in (
            struct.unpack_from("<ffI", payload, offset_bytes + 12 * k) for k in range(point_count)
        )
    ]


# donors


@dataclass
class SDonors:
    """The plain vanilla objects the builders clone, found in the installed game's ``BNK_PREPARED``."""

    sound: CNodeBase
    mixer: CNodeBase
    ranseq: CNodeBase
    attenuation: bytes  # the Attenuation object the mixer points at
    mixer_attenuation_id: int

    @classmethod
    def find(cls, bank: bnk.SSoundBank) -> SDonors:
        """The donors of a bank: a Sound, a 3D Actor-Mixer and a Random container, and the mixer's attenuation."""
        sound = _donor(bank, DONOR_SOUND, SOUND, _plain_sound)
        mixer = _donor(bank, DONOR_MIXER, MIXER, _3d_mixer)
        ranseq = _donor(bank, DONOR_RANSEQ, RANSEQ, _plain_ranseq)
        sound_base, _ = parse_node_base(sound.payload, 18)
        mixer_base, _ = parse_node_base(mixer.payload)
        ranseq_base, _ = parse_node_base(ranseq.payload)
        attenuation_id = struct.unpack("<I", mixer_base.m_props[PROP_ATTENUATION])[0]
        attenuation_object = bank.find(attenuation_id)
        if attenuation_object is None or attenuation_object.type != ATTENUATION:
            raise CDonorError(f"the donor mixer's attenuation {attenuation_id} is not in the bank")
        return cls(sound_base, mixer_base, ranseq_base, attenuation_object.payload, attenuation_id)


def _donor(bank: bnk.SSoundBank, wanted: int, type_id: int, shape) -> bnk.SHircObject:
    wanted_object = bank.find(wanted)
    if wanted_object is not None and wanted_object.type == type_id:
        return wanted_object
    for hirc_object in bank.hirc:
        if hirc_object.type == type_id:
            try:
                if shape(hirc_object):
                    return hirc_object
            except (ValueError, struct.error, CDonorError):
                continue
    raise CDonorError(f"no {bnk.HIRC_TYPE_NAMES[type_id]} in the donor bank to clone")


def _plain_sound(hirc_object: bnk.SHircObject) -> bool:
    node_base, end = parse_node_base(hirc_object.payload, 18)
    return (
        end == len(hirc_object.payload)
        and not node_base.m_ranged
        and _positioning(node_base) == 0
        and node_base.m_bus == 0
    )


def _3d_mixer(hirc_object: bnk.SHircObject) -> bool:
    node_base, end = parse_node_base(hirc_object.payload)
    return (
        PROP_ATTENUATION in node_base.m_props
        and _positioning(node_base) != 0
        and node_base.m_bus != 0
        and len(node_base.m_props) == 1
    )


def _plain_ranseq(hirc_object: bnk.SHircObject) -> bool:
    node_base, end = parse_node_base(hirc_object.payload)
    return _positioning(node_base) == 0 and node_base.m_bus == 0 and not node_base.m_props


# object builders


def attenuation(donors: SDonors, object_id: int) -> bnk.SHircObject:
    """An Attenuation object with the donor's curves."""
    return bnk.SHircObject(ATTENUATION, object_id, donors.attenuation)


def mixer(
    donors: SDonors, object_id: int, attenuation_id: int, children: list[int], limit: SLimit | None = None
) -> bnk.SHircObject:
    """An Actor-Mixer over ``children``, cloned from the donor's 3D mixer."""
    node_base = CNodeBase(**vars(donors.mixer))
    node_base.m_props = dict(node_base.m_props)
    node_base.m_parent = 0
    node_base.set_uint(PROP_ATTENUATION, attenuation_id)
    if limit is not None:
        set_limit(node_base, limit)
    payload = (
        node_base.render()
        + struct.pack("<I", len(children))
        + b"".join(struct.pack("<I", child_id) for child_id in children)
    )
    return bnk.SHircObject(MIXER, object_id, payload)


def clone_mixer(
    base: CNodeBase, object_id: int, children: list[int], bus: int | None = None, parent: int = 0
) -> bnk.SHircObject:
    """An Actor-Mixer with the node base of a shipped one (its bus, positioning and instance limit stay the
    game's own) over ``children``; ``bus`` replaces the output bus."""
    node_base = CNodeBase(**vars(base))
    node_base.m_props, node_base.m_ranged = dict(node_base.m_props), dict(node_base.m_ranged)
    node_base.m_parent = parent
    if bus is not None:
        node_base.m_bus = bus
    payload = (
        node_base.render()
        + struct.pack("<I", len(children))
        + b"".join(struct.pack("<I", child_id) for child_id in children)
    )
    return bnk.SHircObject(MIXER, object_id, payload)


@dataclass(frozen=True)
class SSoundSpec:
    """What a Sound plays: ``<media_id>.wem`` of ``size_bytes`` bytes, and how. ``is_in_bank``: the file is in the
    bank's own media (in memory, as the shipped sound effects are), else a loose streamed PCM file. ``flags``: the
    source flag byte (1: language-specific, as the shipped voices of a localised bank). ``prefetch_bytes``: the bank
    holds only this many leading bytes of the file as its media and the rest streams from the loose ``.wem`` (the
    shipped voices; flags gain the has-source bit 8)."""

    media_id: int
    size_bytes: int
    volume_db: float = 0.0
    pitch: tuple[float, float] = (0.0, 0.0)  # a random pitch in cents between these two per play
    is_looping: bool = False  # repeat endlessly
    max_count: int = 0  # at most this many instances of the Sound play at once, over all emitters (0: no limit)
    kill_newest: bool = True  # at that limit the new instance is dropped (else the lowest-priority one is stopped)
    plugin: int = bnk.PCM_PLUGIN
    is_in_bank: bool = False
    flags: int = 0
    prefetch_bytes: int = 0


def sound(
    base: CNodeBase, object_id: int, parent: int, spec: SSoundSpec, curves: list[SCurve] | None = None
) -> bnk.SHircObject:
    """A Sound over the node base of a plain shipped Sound (``SDonors.sound``, a voice, a UI sound) under ``parent``;
    ``curves``: its volume over game parameters."""
    node_base = CNodeBase(**vars(base))
    node_base.m_props, node_base.m_ranged = {}, {}
    node_base.m_parent = parent
    if curves:
        set_curves(node_base, curves)
    if spec.volume_db:
        node_base.set_float(PROP_VOLUME, spec.volume_db)
    if spec.is_looping:
        node_base.set_uint(PROP_LOOP, 0)
    if spec.max_count:
        set_limit(node_base, SLimit(spec.max_count, kill_newest=spec.kill_newest), ignore_parent=False)
    if spec.pitch != (0.0, 0.0):
        node_base.set_range(PROP_PITCH, *spec.pitch)
    kind, held = (IN_MEMORY, spec.size_bytes) if spec.is_in_bank else (STREAMED, min(spec.size_bytes, PREFETCH))
    flags = spec.flags
    if spec.prefetch_bytes:
        kind, held, flags = PREFETCHED, spec.prefetch_bytes, flags | HAS_SOURCE
    # the id word after the media id: a bank's own media are found by it
    file_id = spec.media_id if spec.is_in_bank or spec.prefetch_bytes else object_id
    source = struct.pack("<IBQIB", spec.plugin, kind, (file_id << 32) | spec.media_id, held, flags)
    return bnk.SHircObject(SOUND, object_id, source + node_base.render())


@dataclass
class SContainer:
    """A Random container: ``loops`` plays of one child (0 = endless), the pause before the next
    play drawn between ``min_pause_ms`` and ``max_pause_ms`` milliseconds."""

    loops: int = 1
    min_pause_ms: int = 0
    max_pause_ms: int = 0


def container(
    donors: SDonors,
    object_id: int,
    parent: int,
    children: list[tuple[int, int]],
    settings: SContainer,
    curves: list[SCurve] | None = None,
) -> bnk.SHircObject:
    """Children are (object id, weight in thousandths); the delay mode applies only with a pause; ``curves``: the
    container's volume over game parameters."""
    node_base = CNodeBase(**vars(donors.ranseq))
    node_base.m_props, node_base.m_ranged = {}, {}
    node_base.m_parent = parent
    if curves:
        set_curves(node_base, curves)
    middle = (settings.min_pause_ms + settings.max_pause_ms) / 2
    spread = (settings.max_pause_ms - settings.min_pause_ms) / 2
    delay = settings.max_pause_ms > 0
    params = struct.pack(
        "<HHHfffHBBBB",
        settings.loops,
        0,
        0,
        middle if delay else 1000.0,
        -spread if delay else 0.0,
        spread if delay else 0.0,
        1 if len(children) > 1 and not delay else 0,  # avoid repeating the last pick
        3 if delay else 0,  # transition: delay
        0,  # standard random
        0,  # random, not sequence
        26 if delay else 18,
    )
    payload = node_base.render() + params
    payload += struct.pack("<I", len(children)) + b"".join(struct.pack("<I", child_id) for child_id, _ in children)
    payload += struct.pack("<H", len(children)) + b"".join(
        struct.pack("<II", child_id, weight) for child_id, weight in children
    )
    return bnk.SHircObject(RANSEQ, object_id, payload)


def play_action(object_id: int, target: int, bank_id: int) -> bnk.SHircObject:
    """A Play action on ``target``, with the id of its bank."""
    payload = struct.pack("<HIB", PLAY, target, 0) + b"\0\0" + struct.pack("<BII", 4, bank_id, 0)
    return bnk.SHircObject(ACTION, object_id, payload)


def stop_action(object_id: int, target: int, fade_ms: int = 100) -> bnk.SHircObject:
    """A Stop action of ``target`` that fades out over ``fade_ms``."""
    payload = struct.pack("<HIB", STOP, target, 0) + b"\1\x3b" + struct.pack("<I", fade_ms) + b"\0" + STOP_TAIL
    return bnk.SHircObject(ACTION, object_id, payload)


def event(object_id: int, actions: list[int]) -> bnk.SHircObject:
    """An Event that runs ``actions``."""
    payload = (
        b"\0" * bnk.EVENT_HEADER
        + bytes([len(actions)])
        + b"".join(struct.pack("<I", action_id) for action_id in actions)
    )
    return bnk.SHircObject(EVENT, object_id, payload)


def action_target(hirc_object: bnk.SHircObject) -> int:
    """The object an action acts on (``ValueError`` for a cut payload)."""
    return bnkgraph.read_action(hirc_object).target


def event_actions(hirc_object: bnk.SHircObject) -> list[int]:
    """The action ids of an event of a bank of version 172 (``ValueError`` for a cut payload)."""
    return bnkgraph.read_event(hirc_object, BANK_VERSION)


# a bank of our own

_RANK = {ATTENUATION: 0, SOUND: 1, RANSEQ: 2, MIXER: 3, ACTION: 4, EVENT: 5}  # children first, as the shipped banks


def header(donor: bnk.SSoundBank, name: str, bank_id: int, language: int = BKHD_LANGUAGE) -> bytes:
    """BKHD of a bank: the donor's first 24 bytes with our id and language (the id of a language folder name,
    ``bnk.fnv1_32("en_us")``; none for a bank without one) and a project hash from the name."""
    head = bytearray(donor.header.data[:24] + bytes(16))
    struct.pack_into("<I", head, 4, bank_id)
    head[8:12] = struct.pack("<I", language)
    head[24:] = hashlib.md5(name.encode("utf-8")).digest()
    return bytes(head)


def new_bank(
    donor: bnk.SSoundBank,
    name: str,
    bank_id: int,
    objects: list[bnk.SHircObject],
    media: dict[int, bytes] | None = None,
    language: int = BKHD_LANGUAGE,
) -> bnk.SSoundBank:
    """A bank holding ``objects``. Children first, then actions and events, the order the shipped banks
    keep. ``media`` (id -> .wem file) goes into the bank's own DIDX and DATA, 16-byte aligned as the
    shipped banks' are; without it the .wem files are loose. ``language``: the BKHD language id."""
    ordered = sorted(objects, key=lambda hirc_object: _RANK[hirc_object.type])
    chunks = [bnk.SChunk(b"BKHD", header(donor, name, bank_id, language))]
    entries: list[bnk.SMediaEntry] = []
    if media:
        blob = bytearray()
        for media_id, data in media.items():
            blob += bytes(-len(blob) % MEDIA_ALIGN)
            entries.append(bnk.SMediaEntry(media_id, len(blob), len(data)))
            blob += data
        chunks += [bnk.SChunk(b"DIDX", b""), bnk.SChunk(b"DATA", bytes(blob))]
    chunks.append(bnk.SChunk(b"HIRC", b""))
    return bnk.SSoundBank(chunks=chunks, hirc=ordered, media=entries)


def append_to(bank: bnk.SSoundBank, objects: list[bnk.SHircObject]) -> int:
    """Add ``objects`` to an existing bank (the fallback when the engine takes no bank of ours): the same
    ordering, new ids only. Returns how many were added."""
    have = {hirc_object.id for hirc_object in bank.hirc}
    new = [hirc_object for hirc_object in objects if hirc_object.id not in have]
    first_late = next(
        (i for i, hirc_object in enumerate(bank.hirc) if hirc_object.type in (ACTION, EVENT)), len(bank.hirc)
    )
    new.sort(key=lambda hirc_object: _RANK[hirc_object.type])
    early = [hirc_object for hirc_object in new if hirc_object.type not in (ACTION, EVENT)]
    late = [hirc_object for hirc_object in new if hirc_object.type in (ACTION, EVENT)]
    bank.hirc[first_late:first_late] = early
    bank.hirc.extend(late)
    return len(new)
