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

from . import bnk, bnkgraph
from . import bnkmusic as bm
from .bnkgraph import ACTION, ATTENUATION, EVENT, MIXER, RANSEQ, SOUND

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


class DonorError(ValueError):
    """The donor bank lacks an object the builders clone."""


@dataclass
class NodeBase:
    """The node base parameters up to the property lists; positioning, aux, advanced settings, state
    and RTPC stay opaque in ``tail``."""

    head: bytes  # effects and metadata overrides: none in the objects we clone
    bus: int
    parent: int
    bits: int
    props: dict[int, bytes] = field(default_factory=dict)  # id -> 4 bytes
    ranged: dict[int, bytes] = field(default_factory=dict)  # id -> min and max as two floats
    tail: bytes = b""

    def render(self) -> bytes:
        out = self.head + struct.pack("<IIB", self.bus, self.parent, self.bits)
        ids = sorted(self.props)
        out += bytes([len(ids)]) + bytes(ids) + b"".join(self.props[i] for i in ids)
        ids = sorted(self.ranged)
        out += bytes([len(ids)]) + bytes(ids) + b"".join(self.ranged[i] for i in ids)
        return out + self.tail

    def set_float(self, prop: int, value: float) -> None:
        self.props[prop] = struct.pack("<f", value)

    def set_uint(self, prop: int, value: int) -> None:
        self.props[prop] = struct.pack("<I", value)

    def set_range(self, prop: int, low: float, high: float) -> None:
        self.ranged[prop] = struct.pack("<ff", low, high)


def parse_node_base(payload: bytes, pos: int = 0) -> tuple[NodeBase, int]:
    """The node base starting at ``pos`` and the offset where it ends."""
    r = bm.Reader(payload)
    r.pos = pos
    end = pos + len(bm.skip_node_base(r))
    r = bm.Reader(payload)
    r.pos = pos
    head = r.take(4)
    if head != b"\0\0\0\0":
        raise DonorError("donor object has effects or metadata overrides")
    bus, parent, bits = r.u32(), r.u32(), r.u8()
    props, ranged = bnkgraph.read_prop_lists(r)
    return NodeBase(head, bus, parent, bits, props, ranged, payload[r.pos : end]), end


def _positioning(nb: NodeBase) -> int:
    return nb.tail[0] if nb.tail else 0


@dataclass(frozen=True)
class Limit:
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
    r = bm.Reader(tail)
    bits = r.u8()
    if bits & 1 and bits & 2 and (r.u8() >> 5) & 3:
        raise DonorError("positioning with an automated path is not handled")
    if r.u8() & 8:  # AuxParams: bHasAux
        r.take(16)
    r.u32()  # reflections aux bus
    return r.pos


def set_limit(nb: NodeBase, limit: Limit, ignore_parent: bool = True) -> None:
    """Write the instance limit and virtual-voice behaviour into the advanced settings of ``nb``;
    ``ignore_parent`` False keeps the parent's limit counting too (a Sound under a limited mixer)."""
    at = _adv_offset(nb.tail)
    bits = ADV_VIRTUAL | (ADV_IGNORE_PARENT if ignore_parent else 0) | (ADV_KILL_NEWEST if limit.kill_newest else 0)
    adv = struct.pack("<BBHB", bits, limit.queue, limit.max_instances, limit.below_threshold)
    nb.tail = nb.tail[:at] + adv + nb.tail[at + 5 :]
    nb.bits |= PRIORITY_BY_DISTANCE


def get_limit(nb: NodeBase) -> Limit:
    """The instance limit written in the advanced settings of ``nb`` (:func:`set_limit` reads back)."""
    bits, queue, count, below = struct.unpack_from("<BBHB", nb.tail, _adv_offset(nb.tail))
    return Limit(count, below, bool(bits & ADV_KILL_NEWEST), queue)


# game parameter curves (InitialRTPC) and attenuations

RTPC_GAME_PARAMETER, RTPC_ADDITIVE, RTPC_VOLUME, SCALE_DB = 0, 2, 0, 2  # as the shipped volume curves
LINEAR = 4  # curve interpolation
SILENT = -1.0  # a dB-scaled curve stores 10^(dB/20) - 1: 0 is 0 dB, -1 no sound


@dataclass(frozen=True)
class Curve:
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
    r = bm.Reader(tail)
    r.pos = _adv_offset(tail) + 6
    r.take(3 * r.u8())  # state properties
    for _ in range(r.u8()):  # state groups
        r.take(9)
        r.take(12 * r.u8())
    return r.pos


def set_curves(nb: NodeBase, curves: list[Curve]) -> None:
    """Replace the game parameter curves of ``nb`` with ``curves`` (the tail ends with them)."""
    at = _rtpc_offset(nb.tail)
    nb.tail = nb.tail[:at] + struct.pack("<H", len(curves)) + b"".join(c.render() for c in curves)


def _attenuation_curves(payload: bytes) -> tuple[int, list[tuple[int, int]]] | None:
    """Where an Attenuation's curve list starts and the (offset, count) of each curve's points, or None. The list
    is found where it ends exactly at the trailing game parameter block, as the layout before it varies."""
    for start in range(0, min(len(payload), 48)):
        r = bm.Reader(payload)
        r.pos = start
        try:
            n = r.u8()
            if not 0 < n <= 19:
                continue
            spans = []
            for _ in range(n):
                if r.u8() > 4:  # scaling
                    raise ValueError
                count = r.u16()
                spans.append((r.pos, count))
                r.take(12 * count)
            if r.u16() == 0 and r.pos == len(payload):
                return start, spans
        except (ValueError, struct.error, IndexError):
            continue
    return None


def attenuation_radius(payload: bytes) -> float:
    """The distance at which the attenuation's first (volume) curve ends."""
    found = _attenuation_curves(payload)
    if found is None:
        raise DonorError("the donor attenuation's curves were not found")
    at, count = found[1][0]
    return struct.unpack_from("<f", payload, at + 12 * (count - 1))[0]


def shaped_attenuation(payload: bytes, far: float, gains: tuple[tuple[float, float], ...]) -> bytes:
    """The attenuation with a volume curve through ``gains`` ((distance, linear gain) points, the last at ``far``).
    The curve stores 10^(dB/20) - 1, so a gain is stored as gain - 1 and the line between two points is a gain
    falling linearly; the other curves keep their shape stretched to ``far``."""
    found = _attenuation_curves(payload)
    if found is None:
        raise DonorError("the donor attenuation's curves were not found")
    spans = found[1]
    factor = far / attenuation_radius(payload)
    out = bytearray(payload)
    for at, n in spans:
        for i in range(n):
            struct.pack_into("<f", out, at + 12 * i, struct.unpack_from("<f", out, at + 12 * i)[0] * factor)
    at, n = spans[0]
    line = b"".join(struct.pack("<ffI", d, g - 1.0, LINEAR) for d, g in gains)
    return bytes(out[: at - 2]) + struct.pack("<H", len(gains)) + line + bytes(out[at + 12 * n :])


def linear_attenuation(payload: bytes, far: float) -> bytes:
    """The attenuation with a straight gain fall: full at the source, silent at ``far``."""
    return shaped_attenuation(payload, far, ((0.0, 1.0), (far, 0.0)))


def attenuation_gains(payload: bytes) -> list[tuple[float, float]]:
    """The (distance, linear gain) points of the attenuation's volume curve."""
    found = _attenuation_curves(payload)
    if found is None:
        raise DonorError("the donor attenuation's curves were not found")
    at, n = found[1][0]
    return [(d, g + 1.0) for d, g, _i in (struct.unpack_from("<ffI", payload, at + 12 * k) for k in range(n))]


# donors


@dataclass
class Donors:
    sound: NodeBase
    mixer: NodeBase
    ranseq: NodeBase
    attenuation: bytes  # the Attenuation object the mixer points at
    mixer_attenuation_id: int

    @classmethod
    def find(cls, bank: bnk.SoundBank) -> Donors:
        sound = _donor(bank, DONOR_SOUND, SOUND, _plain_sound)
        mixer = _donor(bank, DONOR_MIXER, MIXER, _3d_mixer)
        ranseq = _donor(bank, DONOR_RANSEQ, RANSEQ, _plain_ranseq)
        s, _ = parse_node_base(sound.payload, 18)
        m, _ = parse_node_base(mixer.payload)
        q, _ = parse_node_base(ranseq.payload)
        att_id = struct.unpack("<I", m.props[PROP_ATTENUATION])[0]
        att = bank.find(att_id)
        if att is None or att.type != ATTENUATION:
            raise DonorError(f"the donor mixer's attenuation {att_id} is not in the bank")
        return cls(s, m, q, att.payload, att_id)


def _donor(bank: bnk.SoundBank, wanted: int, type_id: int, shape) -> bnk.HircObject:
    obj = bank.find(wanted)
    if obj is not None and obj.type == type_id:
        return obj
    for o in bank.hirc:
        if o.type == type_id:
            try:
                if shape(o):
                    return o
            except (ValueError, struct.error, DonorError):
                continue
    raise DonorError(f"no {bnk.HIRC_TYPE_NAMES[type_id]} in the donor bank to clone")


def _plain_sound(o: bnk.HircObject) -> bool:
    nb, end = parse_node_base(o.payload, 18)
    return end == len(o.payload) and not nb.ranged and _positioning(nb) == 0 and nb.bus == 0


def _3d_mixer(o: bnk.HircObject) -> bool:
    nb, end = parse_node_base(o.payload)
    return PROP_ATTENUATION in nb.props and _positioning(nb) != 0 and nb.bus != 0 and len(nb.props) == 1


def _plain_ranseq(o: bnk.HircObject) -> bool:
    nb, end = parse_node_base(o.payload)
    return _positioning(nb) == 0 and nb.bus == 0 and not nb.props


# object builders


def attenuation(donors: Donors, obj_id: int) -> bnk.HircObject:
    return bnk.HircObject(ATTENUATION, obj_id, donors.attenuation)


def mixer(
    donors: Donors, obj_id: int, attenuation_id: int, children: list[int], limit: Limit | None = None
) -> bnk.HircObject:
    nb = NodeBase(**vars(donors.mixer))
    nb.props = dict(nb.props)
    nb.parent = 0
    nb.set_uint(PROP_ATTENUATION, attenuation_id)
    if limit is not None:
        set_limit(nb, limit)
    payload = nb.render() + struct.pack("<I", len(children)) + b"".join(struct.pack("<I", c) for c in children)
    return bnk.HircObject(MIXER, obj_id, payload)


def clone_mixer(
    base: NodeBase, obj_id: int, children: list[int], bus: int | None = None, parent: int = 0
) -> bnk.HircObject:
    """An Actor-Mixer with the node base of a shipped one (its bus, positioning and instance limit stay the
    game's own) over ``children``; ``bus`` replaces the output bus."""
    nb = NodeBase(**vars(base))
    nb.props, nb.ranged = dict(nb.props), dict(nb.ranged)
    nb.parent = parent
    if bus is not None:
        nb.bus = bus
    payload = nb.render() + struct.pack("<I", len(children)) + b"".join(struct.pack("<I", c) for c in children)
    return bnk.HircObject(MIXER, obj_id, payload)


@dataclass(frozen=True)
class SoundSpec:
    """What a Sound plays: ``<media_id>.wem`` of ``size`` bytes, and how. ``in_bank``: the file is in the bank's own
    media (in memory, as the shipped sound effects are), else a loose streamed PCM file. ``flags``: the source flag
    byte (1: language-specific, as the shipped voices of a localised bank). ``prefetch``: the bank holds only this many
    leading bytes of the file as its media and the rest streams from the loose ``.wem`` (the shipped voices; flags
    gain the has-source bit 8)."""

    media_id: int
    size: int
    volume_db: float = 0.0
    pitch: tuple[float, float] = (0.0, 0.0)  # a random pitch in cents between these two per play
    loop: bool = False  # repeat endlessly
    max_count: int = 0  # at most this many instances of the Sound play at once, over all emitters (0: no limit)
    kill_newest: bool = True  # at that limit the new instance is dropped (else the lowest-priority one is stopped)
    plugin: int = bnk.PCM_PLUGIN
    in_bank: bool = False
    flags: int = 0
    prefetch: int = 0


def sound(
    base: NodeBase, obj_id: int, parent: int, spec: SoundSpec, curves: list[Curve] | None = None
) -> bnk.HircObject:
    """A Sound over the node base of a plain shipped Sound (``Donors.sound``, a voice, a UI sound) under ``parent``;
    ``curves``: its volume over game parameters."""
    nb = NodeBase(**vars(base))
    nb.props, nb.ranged = {}, {}
    nb.parent = parent
    if curves:
        set_curves(nb, curves)
    if spec.volume_db:
        nb.set_float(PROP_VOLUME, spec.volume_db)
    if spec.loop:
        nb.set_uint(PROP_LOOP, 0)
    if spec.max_count:
        set_limit(nb, Limit(spec.max_count, kill_newest=spec.kill_newest), ignore_parent=False)
    if spec.pitch != (0.0, 0.0):
        nb.set_range(PROP_PITCH, *spec.pitch)
    kind, held = (IN_MEMORY, spec.size) if spec.in_bank else (STREAMED, min(spec.size, PREFETCH))
    flags = spec.flags
    if spec.prefetch:
        kind, held, flags = PREFETCHED, spec.prefetch, flags | HAS_SOURCE
    # the id word after the media id: a bank's own media are found by it
    file_id = spec.media_id if spec.in_bank or spec.prefetch else obj_id
    source = struct.pack("<IBQIB", spec.plugin, kind, (file_id << 32) | spec.media_id, held, flags)
    return bnk.HircObject(SOUND, obj_id, source + nb.render())


@dataclass
class Container:
    """A Random container: ``loops`` plays of one child (0 = endless), the pause before the next
    play drawn between ``pause_min`` and ``pause_max`` milliseconds."""

    loops: int = 1
    pause_min: int = 0
    pause_max: int = 0


def container(
    donors: Donors,
    obj_id: int,
    parent: int,
    children: list[tuple[int, int]],
    c: Container,
    curves: list[Curve] | None = None,
) -> bnk.HircObject:
    """Children are (object id, weight in thousandths); the delay mode applies only with a pause; ``curves``: the
    container's volume over game parameters."""
    nb = NodeBase(**vars(donors.ranseq))
    nb.props, nb.ranged = {}, {}
    nb.parent = parent
    if curves:
        set_curves(nb, curves)
    middle = (c.pause_min + c.pause_max) / 2
    spread = (c.pause_max - c.pause_min) / 2
    delay = c.pause_max > 0
    params = struct.pack(
        "<HHHfffHBBBB",
        c.loops,
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
    payload = nb.render() + params
    payload += struct.pack("<I", len(children)) + b"".join(struct.pack("<I", i) for i, _ in children)
    payload += struct.pack("<H", len(children)) + b"".join(struct.pack("<II", i, w) for i, w in children)
    return bnk.HircObject(RANSEQ, obj_id, payload)


def play_action(obj_id: int, target: int, bank_id: int) -> bnk.HircObject:
    payload = struct.pack("<HIB", PLAY, target, 0) + b"\0\0" + struct.pack("<BII", 4, bank_id, 0)
    return bnk.HircObject(ACTION, obj_id, payload)


def stop_action(obj_id: int, target: int, fade_ms: int = 100) -> bnk.HircObject:
    payload = struct.pack("<HIB", STOP, target, 0) + b"\1\x3b" + struct.pack("<I", fade_ms) + b"\0" + STOP_TAIL
    return bnk.HircObject(ACTION, obj_id, payload)


def event(obj_id: int, actions: list[int]) -> bnk.HircObject:
    payload = b"\0" * bnk.EVENT_HEADER + bytes([len(actions)]) + b"".join(struct.pack("<I", a) for a in actions)
    return bnk.HircObject(EVENT, obj_id, payload)


def action_target(obj: bnk.HircObject) -> int:
    return struct.unpack_from("<I", obj.payload, 2)[0]


def event_actions(obj: bnk.HircObject) -> list[int]:
    n = obj.payload[bnk.EVENT_HEADER]
    return list(struct.unpack_from(f"<{n}I", obj.payload, bnk.EVENT_HEADER + 1))


# a bank of our own

_RANK = {ATTENUATION: 0, SOUND: 1, RANSEQ: 2, MIXER: 3, ACTION: 4, EVENT: 5}  # children first, as the shipped banks


def header(donor: bnk.SoundBank, name: str, bank_id: int, language: int = BKHD_LANGUAGE) -> bytes:
    """BKHD of a bank: the donor's first 24 bytes with our id and language (the id of a language folder name,
    ``bnk.fnv1_32("en_us")``; none for a bank without one) and a project hash from the name."""
    head = bytearray(donor.header.data[:24] + bytes(16))
    struct.pack_into("<I", head, 4, bank_id)
    head[8:12] = struct.pack("<I", language)
    head[24:] = hashlib.md5(name.encode("utf-8")).digest()
    return bytes(head)


def new_bank(
    donor: bnk.SoundBank,
    name: str,
    bank_id: int,
    objects: list[bnk.HircObject],
    media: dict[int, bytes] | None = None,
    language: int = BKHD_LANGUAGE,
) -> bnk.SoundBank:
    """A bank holding ``objects``. Children first, then actions and events, the order the shipped banks
    keep. ``media`` (id -> .wem file) goes into the bank's own DIDX and DATA, 16-byte aligned as the
    shipped banks' are; without it the .wem files are loose. ``language``: the BKHD language id."""
    ordered = sorted(objects, key=lambda o: _RANK[o.type])
    chunks = [bnk.Chunk(b"BKHD", header(donor, name, bank_id, language))]
    entries: list[bnk.MediaEntry] = []
    if media:
        blob = bytearray()
        for media_id, data in media.items():
            blob += bytes(-len(blob) % MEDIA_ALIGN)
            entries.append(bnk.MediaEntry(media_id, len(blob), len(data)))
            blob += data
        chunks += [bnk.Chunk(b"DIDX", b""), bnk.Chunk(b"DATA", bytes(blob))]
    chunks.append(bnk.Chunk(b"HIRC", b""))
    return bnk.SoundBank(chunks=chunks, hirc=ordered, media=entries)


def append_to(bank: bnk.SoundBank, objects: list[bnk.HircObject]) -> int:
    """Add ``objects`` to an existing bank (the fallback when the engine takes no bank of ours): the same
    ordering, new ids only. Returns how many were added."""
    have = {o.id for o in bank.hirc}
    new = [o for o in objects if o.id not in have]
    first_late = next((i for i, o in enumerate(bank.hirc) if o.type in (ACTION, EVENT)), len(bank.hirc))
    new.sort(key=lambda o: _RANK[o.type])
    early = [o for o in new if o.type not in (ACTION, EVENT)]
    late = [o for o in new if o.type in (ACTION, EVENT)]
    bank.hirc[first_late:first_late] = early
    bank.hirc.extend(late)
    return len(new)
