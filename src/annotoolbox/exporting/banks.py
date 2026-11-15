"""The bank reader without Qt that the viewer, the sound editor and the export share: a Wwise bank listed (its
sounds, their media embedded or streamed, the objects by kind, each event's sounds, the music tracks), a ``.wem``
described and made playable (PCM and Wwise ADPCM as a WAV, Wwise Vorbis back to Ogg Vorbis through
``formats/wwvorbis``), and the older games' plain audio files as they are.

``bank_listing(data)``, ``walk_event(listing, event, choices)`` (``event_sounds`` without the choices),
``music_tracks(listing)`` and ``playable(data, name)`` are the entries; ``Unplayable`` says why a clip cannot be
played. Banks: docs/sound/ and ``formats/bnk``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..formats import bnk, bnkgraph, bnkmusic, bnkmusicold, riff, wemadpcm

# Wwise's codecs (codec << 16 | 1) and the source plugin that makes silence (it has no media)
PLUGINS = {0x00010001: "PCM", 0x00020001: "ADPCM", 0x00040001: "Vorbis", 0x00650002: "silence"}
INIT_BANK = 1355168291  # the id of every game's init bank (its info file names the groups)
STREAMS = {0: "embedded", 1: "streamed", 2: "streamed, prefetched"}
WAVE_TAGS = {0x0001: "PCM", 0x0002: "ADPCM", 0x0011: "IMA ADPCM", 0xFFFE: "PCM", 0xFFFF: "Wwise Vorbis", 0x0055: "MP3"}
PLAIN = (".wav", ".mp3", ".ogg")  # files a media player takes as they are
SOUND, ACTION, EVENT = 2, 3, 4
PLAYABLE = (2, 5, 6, 7, 9, 10, 11, 12, 13)  # the objects an event can reach
EXACT_VERSION = 172  # the bank version whose music objects are read exactly (the others' containers: ``bnkgraph``)


class Unplayable(ValueError):
    """A clip the viewer has no decoder for (and why)."""


@dataclass(frozen=True)
class BankSound:
    """A Sound object of a bank: its id, codec, whether its media is embedded or streamed, and the media's id (a
    streamed one is the file ``media/<id>.wem`` beside the banks)."""

    id: int
    codec: str
    stream: str
    media: int


@dataclass
class BankListing:
    version: int
    bank_id: int
    objects: dict[str, int] = field(default_factory=dict)  # kind -> count
    sounds: list[BankSound] = field(default_factory=list)
    media: dict[int, tuple[int, int]] = field(default_factory=dict)  # embedded media id -> (offset in DATA, size)
    data: bytes = b""  # the DATA chunk
    hirc: dict[int, bnk.HircObject] = field(default_factory=dict)  # every object by id
    graph: dict[int, bnkgraph.Node] = field(default_factory=dict)  # the sounds and containers, read by ``bnkgraph``
    events: list[int] = field(default_factory=list)

    def embedded(self, media: int) -> bytes:
        offset, size = self.media[media]
        return self.data[offset : offset + size]


def bank_listing(data: bytes) -> BankListing:
    """A bank's header, its objects counted by kind, its sounds and its embedded media."""
    bank = bnk.parse(data)
    out = BankListing(bank.version, bank.bank_id)
    for o in bank.hirc:
        out.objects[o.type_name] = out.objects.get(o.type_name, 0) + 1
        if o.type == 2 and len(o.payload) >= 9:  # plugin u32, stream type u8, source id u32 (banks 112 to 172)
            plugin, stream, media = struct.unpack_from("<IBI", o.payload, 0)
            out.sounds.append(
                BankSound(o.id, PLUGINS.get(plugin, f"{plugin:#x}"), STREAMS.get(stream, str(stream)), media)
            )
    out.media = {m.id: (m.offset, m.size) for m in bank.media}
    out.data = next((c.data for c in bank.chunks if c.tag == b"DATA"), b"")
    out.hirc = {o.id: o for o in bank.hirc}
    out.graph = bnkgraph.read_graph(bank.hirc, bank.version)
    out.events = [o.id for o in bank.hirc if o.type == EVENT]
    return out


def _children(listing: BankListing, obj: bnk.HircObject) -> tuple[list[int], bool]:
    """(the ids an object leads to, whether they were read exactly): an event's actions, a play action's target, a
    container's children (``bnkgraph``); a music object by its layout (``bnkmusic``, ``bnkmusicold``); otherwise the ids found in the
    object's bytes (a guess)."""
    try:
        if obj.type == EVENT:
            return bnkgraph.read_event(obj, listing.version), True
        if obj.type == ACTION:  # only a play action leads to sounds (stop, pause, states do not)
            action = bnkgraph.read_action(obj)
            return ([action.target] if action.type == bnkgraph.PLAY else []), True
        node = listing.graph.get(obj.id)
        if node is not None and node.exact:
            return node.children, True
        music = (bnkmusic.MUSIC_SEGMENT, bnkmusic.MUSIC_SWITCH_CNTR, bnkmusic.MUSIC_RANSEQ_CNTR)
        if obj.type in music:
            if listing.version == EXACT_VERSION:
                return bnkmusic.children_of(obj), True
            return bnkmusicold.children(obj, listing.version), True
    except (ValueError, struct.error):
        pass
    parent = _parent(obj, listing.version)
    found = []
    for at in range(0, max(0, len(obj.payload) - 3)):
        ref = struct.unpack_from("<I", obj.payload, at)[0]
        target = listing.hirc.get(ref)
        if ref not in (obj.id, parent) and target is not None and target.type in PLAYABLE and target.type != 7:
            found += [] if ref in found else [ref]
    return found, False


@dataclass
class GroupChoice:
    """A switch or state group an event's containers follow: the value they start with and the values they branch on."""

    state_group: bool
    default: int
    values: list[int] = field(default_factory=list)


@dataclass
class EventWalk:
    """What an event plays: its sounds (``BankSound``s, a music track's media ids as numbers), whether every step was
    read exactly, the groups its switch containers follow and the set-switch and set-state actions it runs."""

    sounds: list[BankSound | int] = field(default_factory=list)
    exact: bool = True
    groups: dict[int, GroupChoice] = field(default_factory=dict)
    sets: list[bnkgraph.Action] = field(default_factory=list)


def walk_event(listing: BankListing, event: int, choices: dict[int, int] | None = None, limit: int = 2000) -> EventWalk:
    """The sounds an event plays: its actions' targets with the containers below them walked. A switch container whose
    group is in ``choices`` (group id -> value id) plays only that value's branch, any other plays all of its
    children."""
    choices = choices or {}
    sound_of = {s.id: s for s in listing.sounds}
    out, seen, todo = EventWalk(), set(), [event]
    while todo and len(seen) < limit:
        oid = todo.pop(0)
        obj = listing.hirc.get(oid)
        if obj is None or oid in seen:
            continue
        seen.add(oid)
        if obj.type == SOUND:
            if oid in sound_of:
                out.sounds.append(sound_of[oid])
            continue
        if obj.type == bnkmusic.MUSIC_TRACK:
            out.sounds += [m for m, _stream in _track_sources(listing, obj)]
            continue
        if obj.type == ACTION:
            _note_set(obj, out)
        found, read = _children(listing, obj)
        out.exact = out.exact and read
        todo += _branch(listing.graph.get(oid), found, choices, out)
    return out


def _note_set(obj: bnk.HircObject, walk: EventWalk) -> None:
    try:
        action = bnkgraph.read_action(obj)
    except (ValueError, struct.error):
        return
    if action.type in (bnkgraph.SET_STATE, bnkgraph.SET_SWITCH):
        walk.sets.append(action)


def _branch(node: bnkgraph.Node | None, children: list[int], choices: dict[int, int], walk: EventWalk) -> list[int]:
    """The children to walk below a node: a switch container's chosen branch when its group has a choice."""
    switch = node.switch if node is not None else None
    if switch is None:
        return children
    group = walk.groups.setdefault(switch.group, GroupChoice(switch.state_group, switch.default))
    group.values += [v for v in switch.branches if v not in group.values]
    if switch.group in choices:
        return switch.branches.get(choices[switch.group], [])
    return children


def event_sounds(listing: BankListing, event: int, limit: int = 2000) -> tuple[list[BankSound | int], bool]:
    """The sounds an event plays (every branch of its switch containers) and whether every step was read exactly."""
    walk = walk_event(listing, event, None, limit)
    return walk.sounds, walk.exact


@dataclass(frozen=True)
class MusicTrackInfo:
    """A music track: its id, its sources (media id, how it is stored), the segment it plays in and that segment's
    length in seconds (0 when not known)."""

    id: int
    sources: tuple[tuple[int, str], ...]
    segment: int = 0
    seconds: float = 0.0


def _track_sources(listing: BankListing, obj: bnk.HircObject) -> list[tuple[int, str]]:
    """A music track's sources (media id, how stored); none for a track that does not read."""
    try:
        if listing.version == EXACT_VERSION:
            sources = [(s.source_id & 0xFFFFFFFF, s.stream_type) for s in bnkmusic.parse_track(obj).sources]
        else:
            sources = [(s.media, s.stream) for s in bnkmusicold.track_sources(obj, listing.version)]
    except (ValueError, struct.error):
        return []
    return [(media, STREAMS.get(stream, str(stream))) for media, stream in sources]


def music_tracks(listing: BankListing) -> list[MusicTrackInfo]:
    """Every music track of a bank with its sources, the segment each plays in and its length."""
    segments: dict[int, tuple[int, float]] = {}
    for obj in listing.hirc.values():
        if obj.type != bnkmusic.MUSIC_SEGMENT:
            continue
        try:
            seg = (
                bnkmusic.parse_segment(obj)
                if listing.version == EXACT_VERSION
                else bnkmusicold.segment(obj, listing.version)
            )
        except (ValueError, struct.error):
            continue
        for child in seg.children:
            segments[child] = (obj.id, seg.duration / 1000.0)
    return [
        MusicTrackInfo(o.id, tuple(_track_sources(listing, o)), *segments.get(o.id, (0, 0.0)))
        for o in listing.hirc.values()
        if o.type == bnkmusic.MUSIC_TRACK
    ]


@dataclass
class GroupNames:
    """A switch or state group's name and its values' names (by id)."""

    name: str
    values: dict[int, str] = field(default_factory=dict)


@dataclass
class BankInfo:
    """What the ``.json`` the sound tool wrote beside a bank says: the bank's name, its media's names and paths
    (relative to the banks' folder), its events by id."""

    name: str = ""
    media: dict[int, tuple[str, str]] = field(default_factory=dict)  # id -> (name, path)
    events: dict[int, str] = field(default_factory=dict)
    groups: dict[int, GroupNames] = field(default_factory=dict)  # switch and state groups by id

    def group_label(self, group: int, value: int | None = None) -> str:
        """A group (or one of its values) by name where the info file has one, else its number."""
        names = self.groups.get(group)
        if names is None:
            return str(group if value is None else value)
        return (names.name or str(group)) if value is None else names.values.get(value) or str(value)


def bank_info(text: str, more: BankInfo | None = None) -> BankInfo:
    """The bank info file's names; an empty record for a file that does not read. ``more``: a record to add the
    names to (the switch and state groups live in the init bank's file, not in each bank's)."""
    import json

    try:
        banks = json.loads(text)["SoundBanksInfo"]["SoundBanks"]
    except (ValueError, KeyError, TypeError):
        return more or BankInfo()
    out = more or BankInfo(banks[0].get("ShortName", "") if banks else "")
    for b in banks:
        for key, values in (("SwitchGroups", "Switches"), ("StateGroups", "States")):
            for g in b.get(key, []):
                names = out.groups.setdefault(int(g["Id"]), GroupNames(g.get("Name", "")))
                names.values.update({int(v["Id"]): v.get("Name", "") for v in g.get(values, [])})
        for m in (*b.get("Media", []), *b.get("ReferencedStreamedFiles", [])):  # 2205 lists its voices as streamed
            out.media[int(m["Id"])] = (m.get("ShortName", ""), m.get("Path", "").replace("\\", "/").lower())
        for e in b.get("Events", []):
            out.events[int(e["Id"])] = e.get("Name", "")
    return out


def read_info(bank: str, exists, read) -> BankInfo:
    """A bank's info file with the names of the init bank's file added (its switch and state groups): ``exists(path)``
    and ``read(path)`` (text) look in the game's files."""
    folder = bank.rsplit("/", 1)[0] + "/"
    root = folder[: folder.index("/windows/") + len("/windows/")] if "/windows/" in folder else folder
    own = bank[: -len(".bnk")] + ".json"
    info = bank_info(read(own)) if exists(own) else BankInfo()
    init = f"{root}{INIT_BANK}.json"
    return bank_info(read(init), info) if init != own and exists(init) else info


def media_file(bank: str, media: int, info: BankInfo, exists) -> str:
    """The game path of a streamed sound's ``.wem``: the bank info's path, else ``media/<id>.wem`` beside the banks or
    in the bank's language folder; "" when none is there."""
    folder = bank.rsplit("/", 1)[0] + "/"
    root = folder[: folder.index("/windows/") + len("/windows/")] if "/windows/" in folder else folder
    language = folder[len(root) :]
    found = [root + info.media[media][1]] if media in info.media else []
    found += [f"{root}media/{media}.wem", f"{root}media/{language}{media}.wem"]
    return next((p for p in found if exists(p)), "")


def _parent(obj: bnk.HircObject, version: int) -> int | None:
    """The container an object hangs under (its node base's parent), so a guessed walk does not climb to it."""
    try:
        return bnkgraph.parent_of(obj, version)
    except (ValueError, struct.error):
        return None


def wave_info(data: bytes) -> dict:
    """A RIFF/WAVE (a ``.wem`` or a ``.wav``): format, channels, sample rate, length in seconds when it can be told."""
    try:
        chunks = riff.chunks(data)
    except ValueError as exc:
        raise Unplayable(str(exc)) from exc
    fmt = chunks.get(b"fmt ", b"")
    if len(fmt) < 16:
        raise Unplayable("no format chunk")
    tag, channels, rate, per_second = struct.unpack_from("<HHII", fmt)
    info = {"format": WAVE_TAGS.get(tag, f"{tag:#x}"), "channels": channels, "rate": rate}
    align = struct.unpack_from("<H", fmt, 12)[0]
    if tag == 0x0002 and align and channels:  # Wwise ADPCM: 64 samples a channel in a block of 36 bytes a channel
        info["seconds"] = len(chunks.get(b"data", b"")) // align * 64 / max(rate, 1)
    elif tag == 0xFFFF and len(fmt) >= 28:
        info["seconds"] = struct.unpack_from("<I", fmt, 24)[0] / max(rate, 1)  # the vorb block's sample count
    elif per_second:
        info["seconds"] = len(chunks.get(b"data", b"")) / per_second
    return info


def playable(data: bytes, name: str, codebooks=None) -> tuple[bytes, str]:
    """``(file bytes, suffix)`` a media player plays: a plain file as it is, a PCM ``.wem`` as WAV, a Wwise Vorbis one
    as Ogg Vorbis (``codebooks``: ``wwvorbis.Library``, found in the tools folder when None)."""
    lower = name.lower()
    if lower.endswith(PLAIN):
        return data, lower[lower.rindex(".") :]
    info = wave_info(data)
    if info["format"] == "PCM":
        samples, rate, channels = bnk.read_pcm_wem(data)
        return riff.wav_bytes(samples.tobytes(), rate, channels), ".wav"
    if info["format"] == "ADPCM":
        try:
            samples, rate, channels = wemadpcm.read(data)
        except ValueError as exc:
            raise Unplayable(str(exc)) from exc
        return riff.wav_bytes(samples.astype("<i2").tobytes(), rate, channels), ".wav"
    if info["format"] == "Wwise Vorbis":
        from ..formats import wwvorbis

        library = codebooks or wwvorbis.Library.find()
        if library is None:
            raise Unplayable(f"Wwise Vorbis needs the codebook file {wwvorbis.LIBRARY_FILE} in the tools folder")
        try:
            return wwvorbis.to_ogg(data, library), ".ogg"
        except wwvorbis.WemError as exc:
            raise Unplayable(f"Wwise Vorbis: {exc}") from exc
    raise Unplayable(f"no decoder for {info['format']}")
