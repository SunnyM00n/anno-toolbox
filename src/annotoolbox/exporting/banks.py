"""The bank reader without Qt that the viewer, the sound editor and the export share: a Wwise bank listed (its
sounds, their media embedded or streamed, the objects by kind, each event's sounds, the music tracks), a ``.wem``
described and made playable (PCM and Wwise ADPCM as a WAV, Wwise Vorbis back to Ogg Vorbis through
``formats/wwvorbis``), and the older games' plain audio files as they are.

``bank_listing(data)``, ``walk_event(listing, event, choices)`` (``event_sounds`` without the choices),
``music_tracks(listing)`` and ``playable(data, name)`` are the entries; ``CUnplayable`` says why a clip cannot be
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


class CUnplayable(ValueError):
    """A clip the viewer has no decoder for (and why)."""


@dataclass(frozen=True)
class SBankSound:
    """A Sound object of a bank: its id, codec, whether its media is embedded or streamed, and the media's id (a
    streamed one is the file ``media/<id>.wem`` beside the banks)."""

    id: int
    codec: str
    stream: str
    media: int


@dataclass
class SBankListing:
    version: int
    bank_id: int
    objects: dict[str, int] = field(default_factory=dict)  # kind -> count
    sounds: list[SBankSound] = field(default_factory=list)
    media: dict[int, tuple[int, int]] = field(default_factory=dict)  # embedded media id -> (offset in DATA, size)
    data: bytes = b""  # the DATA chunk
    hirc: dict[int, bnk.SHircObject] = field(default_factory=dict)  # every object by id
    graph: dict[int, bnkgraph.SNode] = field(default_factory=dict)  # the sounds and containers, read by ``bnkgraph``
    events: list[int] = field(default_factory=list)

    def embedded(self, media: int) -> bytes:
        offset, size = self.media[media]
        return self.data[offset : offset + size]


def bank_listing(data: bytes) -> SBankListing:
    """A bank's header, its objects counted by kind, its sounds and its embedded media."""
    bank = bnk.parse(data)
    out = SBankListing(bank.version, bank.bank_id)
    for hirc_object in bank.hirc:
        out.objects[hirc_object.type_name] = out.objects.get(hirc_object.type_name, 0) + 1
        if (
            hirc_object.type == 2 and len(hirc_object.payload) >= 9
        ):  # plugin u32, stream type u8, source id u32 (banks 112 to 172)
            plugin, stream, media = struct.unpack_from("<IBI", hirc_object.payload, 0)
            out.sounds.append(
                SBankSound(hirc_object.id, PLUGINS.get(plugin, f"{plugin:#x}"), STREAMS.get(stream, str(stream)), media)
            )
    out.media = {media_entry.id: (media_entry.offset, media_entry.size) for media_entry in bank.media}
    out.data = next((chunk.data for chunk in bank.chunks if chunk.tag == b"DATA"), b"")
    out.hirc = {hirc_object.id: hirc_object for hirc_object in bank.hirc}
    out.graph = bnkgraph.read_graph(bank.hirc, bank.version)
    out.events = [hirc_object.id for hirc_object in bank.hirc if hirc_object.type == EVENT]
    return out


def _children(listing: SBankListing, hirc_object: bnk.SHircObject) -> tuple[list[int], bool]:
    """(the ids an object leads to, whether they were read exactly): an event's actions, a play action's target, a
    container's children (``bnkgraph``); a music object by its layout (``bnkmusic``, ``bnkmusicold``); otherwise the ids found in the
    object's bytes (a guess)."""
    try:
        if hirc_object.type == EVENT:
            return bnkgraph.read_event(hirc_object, listing.version), True
        if hirc_object.type == ACTION:  # only a play action leads to sounds (stop, pause, states do not)
            action = bnkgraph.read_action(hirc_object)
            return ([action.target] if action.type == bnkgraph.PLAY else []), True
        node = listing.graph.get(hirc_object.id)
        if node is not None and node.exact:
            return node.children, True
        music = (bnkmusic.MUSIC_SEGMENT, bnkmusic.MUSIC_SWITCH_CNTR, bnkmusic.MUSIC_RANSEQ_CNTR)
        if hirc_object.type in music:
            if listing.version == EXACT_VERSION:
                return bnkmusic.children_of(hirc_object), True
            return bnkmusicold.children(hirc_object, listing.version), True
    except (ValueError, struct.error):
        pass
    parent = _parent(hirc_object, listing.version)
    found = []
    for offset in range(0, max(0, len(hirc_object.payload) - 3)):
        ref = struct.unpack_from("<I", hirc_object.payload, offset)[0]
        target = listing.hirc.get(ref)
        if ref not in (hirc_object.id, parent) and target is not None and target.type in PLAYABLE and target.type != 7:
            found += [] if ref in found else [ref]
    return found, False


@dataclass
class SGroupChoice:
    """A switch or state group an event's containers follow: the value they start with and the values they branch on."""

    state_group: bool
    default: int
    values: list[int] = field(default_factory=list)


@dataclass
class SEventWalk:
    """What an event plays: its sounds (``SBankSound``s, a music track's media ids as numbers), whether every step was
    read exactly, the groups its switch containers follow and the set-switch and set-state actions it runs."""

    sounds: list[SBankSound | int] = field(default_factory=list)
    exact: bool = True
    groups: dict[int, SGroupChoice] = field(default_factory=dict)
    sets: list[bnkgraph.SAction] = field(default_factory=list)


def walk_event(
    listing: SBankListing, event: int, choices: dict[int, int] | None = None, limit: int = 2000
) -> SEventWalk:
    """The sounds an event plays: its actions' targets with the containers below them walked. A switch container whose
    group is in ``choices`` (group id -> value id) plays only that value's branch, any other plays all of its
    children."""
    choices = choices or {}
    sound_of = {sound.id: sound for sound in listing.sounds}
    out, seen, todo = SEventWalk(), set(), [event]
    while todo and len(seen) < limit:
        oid = todo.pop(0)
        hirc_object = listing.hirc.get(oid)
        if hirc_object is None or oid in seen:
            continue
        seen.add(oid)
        if hirc_object.type == SOUND:
            if oid in sound_of:
                out.sounds.append(sound_of[oid])
            continue
        if hirc_object.type == bnkmusic.MUSIC_TRACK:
            out.sounds += [media_id for media_id, _stream in _track_sources(listing, hirc_object)]
            continue
        if hirc_object.type == ACTION:
            _note_set(hirc_object, out)
        found, read = _children(listing, hirc_object)
        out.exact = out.exact and read
        todo += _branch(listing.graph.get(oid), found, choices, out)
    return out


def _note_set(hirc_object: bnk.SHircObject, walk: SEventWalk) -> None:
    try:
        action = bnkgraph.read_action(hirc_object)
    except (ValueError, struct.error):
        return
    if action.type in (bnkgraph.SET_STATE, bnkgraph.SET_SWITCH):
        walk.sets.append(action)


def _branch(node: bnkgraph.SNode | None, children: list[int], choices: dict[int, int], walk: SEventWalk) -> list[int]:
    """The children to walk below a node: a switch container's chosen branch when its group has a choice."""
    switch = node.switch if node is not None else None
    if switch is None:
        return children
    group = walk.groups.setdefault(switch.group, SGroupChoice(switch.state_group, switch.default))
    group.values += [value for value in switch.branches if value not in group.values]
    if switch.group in choices:
        return switch.branches.get(choices[switch.group], [])
    return children


def event_sounds(listing: SBankListing, event: int, limit: int = 2000) -> tuple[list[SBankSound | int], bool]:
    """The sounds an event plays (every branch of its switch containers) and whether every step was read exactly."""
    walk = walk_event(listing, event, None, limit)
    return walk.sounds, walk.exact


@dataclass(frozen=True)
class SMusicTrackInfo:
    """A music track: its id, its sources (media id, how it is stored), the segment it plays in and that segment's
    length in seconds (0 when not known)."""

    id: int
    sources: tuple[tuple[int, str], ...]
    segment: int = 0
    seconds: float = 0.0


def _track_sources(listing: SBankListing, hirc_object: bnk.SHircObject) -> list[tuple[int, str]]:
    """A music track's sources (media id, how stored); none for a track that does not read."""
    try:
        if listing.version == EXACT_VERSION:
            sources = [
                (source.source_id & 0xFFFFFFFF, source.stream_type)
                for source in bnkmusic.parse_track(hirc_object).m_sources
            ]
        else:
            sources = [
                (source.media, source.stream) for source in bnkmusicold.track_sources(hirc_object, listing.version)
            ]
    except (ValueError, struct.error):
        return []
    return [(media, STREAMS.get(stream, str(stream))) for media, stream in sources]


def music_tracks(listing: SBankListing) -> list[SMusicTrackInfo]:
    """Every music track of a bank with its sources, the segment each plays in and its length."""
    segments: dict[int, tuple[int, float]] = {}
    for hirc_object in listing.hirc.values():
        if hirc_object.type != bnkmusic.MUSIC_SEGMENT:
            continue
        try:
            if listing.version == EXACT_VERSION:
                seg = bnkmusic.parse_segment(hirc_object)
                children, duration = seg.m_children, seg.m_duration
            else:
                old = bnkmusicold.segment(hirc_object, listing.version)
                children, duration = old.children, old.duration
        except (ValueError, struct.error):
            continue
        for child in children:
            segments[child] = (hirc_object.id, duration / 1000.0)
    return [
        SMusicTrackInfo(
            hirc_object.id, tuple(_track_sources(listing, hirc_object)), *segments.get(hirc_object.id, (0, 0.0))
        )
        for hirc_object in listing.hirc.values()
        if hirc_object.type == bnkmusic.MUSIC_TRACK
    ]


@dataclass
class SGroupNames:
    """A switch or state group's name and its values' names (by id)."""

    name: str
    values: dict[int, str] = field(default_factory=dict)


@dataclass
class SBankInfo:
    """What the ``.json`` the sound tool wrote beside a bank says: the bank's name, its media's names and paths
    (relative to the banks' folder), its events by id."""

    name: str = ""
    media: dict[int, tuple[str, str]] = field(default_factory=dict)  # id -> (name, path)
    events: dict[int, str] = field(default_factory=dict)
    groups: dict[int, SGroupNames] = field(default_factory=dict)  # switch and state groups by id

    def group_label(self, group: int, value: int | None = None) -> str:
        """A group (or one of its values) by name where the info file has one, else its number."""
        names = self.groups.get(group)
        if names is None:
            return str(group if value is None else value)
        return (names.name or str(group)) if value is None else names.values.get(value) or str(value)


def bank_info(text: str, more: SBankInfo | None = None) -> SBankInfo:
    """The bank info file's names; an empty record for a file that does not read. ``more``: a record to add the
    names to (the switch and state groups live in the init bank's file, not in each bank's)."""
    import json

    try:
        banks = json.loads(text)["SoundBanksInfo"]["SoundBanks"]
    except (ValueError, KeyError, TypeError):
        return more or SBankInfo()
    out = more or SBankInfo(banks[0].get("ShortName", "") if banks else "")
    for bank in banks:
        for key, values in (("SwitchGroups", "Switches"), ("StateGroups", "States")):
            for group in bank.get(key, []):
                names = out.groups.setdefault(int(group["Id"]), SGroupNames(group.get("Name", "")))
                names.values.update({int(value["Id"]): value.get("Name", "") for value in group.get(values, [])})
        for media in (
            *bank.get("Media", []),
            *bank.get("ReferencedStreamedFiles", []),
        ):  # 2205 lists its voices as streamed
            out.media[int(media["Id"])] = (media.get("ShortName", ""), media.get("Path", "").replace("\\", "/").lower())
        for event in bank.get("Events", []):
            out.events[int(event["Id"])] = event.get("Name", "")
    return out


def read_info(bank: str, exists, read) -> SBankInfo:
    """A bank's info file with the names of the init bank's file added (its switch and state groups): ``exists(path)``
    and ``read(path)`` (text) look in the game's files."""
    folder = bank.rsplit("/", 1)[0] + "/"
    root = folder[: folder.index("/windows/") + len("/windows/")] if "/windows/" in folder else folder
    own = bank[: -len(".bnk")] + ".json"
    bank_data = bank_info(read(own)) if exists(own) else SBankInfo()
    init = f"{root}{INIT_BANK}.json"
    return bank_info(read(init), bank_data) if init != own and exists(init) else bank_data


def media_file(bank: str, media: int, bank_data: SBankInfo, exists) -> str:
    """The game path of a streamed sound's ``.wem``: the bank info's path, else ``media/<id>.wem`` beside the banks or
    in the bank's language folder; "" when none is there."""
    folder = bank.rsplit("/", 1)[0] + "/"
    root = folder[: folder.index("/windows/") + len("/windows/")] if "/windows/" in folder else folder
    language = folder[len(root) :]
    found = [root + bank_data.media[media][1]] if media in bank_data.media else []
    found += [f"{root}media/{media}.wem", f"{root}media/{language}{media}.wem"]
    return next((path for path in found if exists(path)), "")


def _parent(hirc_object: bnk.SHircObject, version: int) -> int | None:
    """The container an object hangs under (its node base's parent), so a guessed walk does not climb to it."""
    try:
        return bnkgraph.parent_of(hirc_object, version)
    except (ValueError, struct.error):
        return None


def wave_info(data: bytes) -> dict:
    """A RIFF/WAVE (a ``.wem`` or a ``.wav``): format, channels, sample rate, length in seconds when it can be told."""
    try:
        chunks = riff.chunks(data)
    except ValueError as exc:
        raise CUnplayable(str(exc)) from exc
    format_chunk = chunks.get(b"fmt ", b"")
    if len(format_chunk) < 16:
        raise CUnplayable("no format chunk")
    tag, channels, rate, per_second = struct.unpack_from("<HHII", format_chunk)
    fields = {"format": WAVE_TAGS.get(tag, f"{tag:#x}"), "channels": channels, "rate": rate}
    align = struct.unpack_from("<H", format_chunk, 12)[0]
    if tag == 0x0002 and align and channels:  # Wwise ADPCM: 64 samples a channel in a block of 36 bytes a channel
        fields["seconds"] = len(chunks.get(b"data", b"")) // align * 64 / max(rate, 1)
    elif tag == 0xFFFF and len(format_chunk) >= 28:
        fields["seconds"] = struct.unpack_from("<I", format_chunk, 24)[0] / max(
            rate, 1
        )  # the vorb block's sample count
    elif per_second:
        fields["seconds"] = len(chunks.get(b"data", b"")) / per_second
    return fields


def playable(data: bytes, name: str, codebooks=None) -> tuple[bytes, str]:
    """``(file bytes, suffix)`` a media player plays: a plain file as it is, a PCM ``.wem`` as WAV, a Wwise Vorbis one
    as Ogg Vorbis (``codebooks``: ``wwvorbis.CLibrary``, found in the tools folder when None)."""
    lower = name.lower()
    if lower.endswith(PLAIN):
        return data, lower[lower.rindex(".") :]
    wave_fields = wave_info(data)
    if wave_fields["format"] == "PCM":
        samples, rate, channels = bnk.read_pcm_wem(data)
        return riff.wav_bytes(samples.tobytes(), rate, channels), ".wav"
    if wave_fields["format"] == "ADPCM":
        try:
            samples, rate, channels = wemadpcm.read(data)
        except ValueError as exc:
            raise CUnplayable(str(exc)) from exc
        return riff.wav_bytes(samples.astype("<i2").tobytes(), rate, channels), ".wav"
    if wave_fields["format"] == "Wwise Vorbis":
        from ..formats import wwvorbis

        library = codebooks or wwvorbis.CLibrary.find()
        if library is None:
            raise CUnplayable(f"Wwise Vorbis needs the codebook file {wwvorbis.LIBRARY_FILE} in the tools folder")
        try:
            return wwvorbis.to_ogg(data, library), ".ogg"
        except wwvorbis.CWemError as exc:
            raise CUnplayable(f"Wwise Vorbis: {exc}") from exc
    raise CUnplayable(f"no decoder for {wave_fields['format']}")
