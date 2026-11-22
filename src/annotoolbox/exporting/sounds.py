"""Sounds and sound banks exported as WAV and Ogg Vorbis.

A ``.wem`` of PCM or Wwise ADPCM becomes a WAV with no tool; Wwise Vorbis becomes Ogg Vorbis with the codebook file
of the ww2ogg tool (optional, never shipped), and its WAV or any Ogg from a PCM file needs ffmpeg as well. 2070's and
1404's plain ``.wav``, ``.mp3`` and ``.ogg`` files are as stored or, for another form, go through ffmpeg. A bank
exports every sound it embeds (and the streamed ``.wem`` files beside it when the source has them) under
``<bank>/media/``, each event's sounds under ``<bank>/events/<event>/`` where the bank's walk resolves them, and with
``json`` a listing. Reading a bank is ``exporting.banks``. docs/exporting.md.
"""

from __future__ import annotations

import array
import io
import json
import re
import wave
from pathlib import Path

from ..formats import riff, wwvorbis
from ..infra import tools
from . import banks
from .run import SJob, SMade

CODEBOOKS_HELP = (
    "Wwise Vorbis needs the codebook file packed_codebooks_aoTuV_603.bin of the ww2ogg tool ("
    + tools.WHERE_TO_GET["wwcodebooks"]
    + "; optional)"
)
FFMPEG_HELP = "this form needs ffmpeg (" + tools.WHERE_TO_GET["ffmpeg"] + "; optional)"
EVENT_LIMIT = 4000  # events written at most for one bank


class CToolMissing(Exception):
    """An optional tool the form needs is not there."""


def missing() -> list[str]:
    """Lines for the optional tools the Ogg forms may need and the machine lacks."""
    lines = []
    if tools.find_tool("wwcodebooks") is None:
        lines.append("ogg of Wwise Vorbis: " + CODEBOOKS_HELP)
    if tools.find_tool("ffmpeg") is None:
        lines.append("ogg or wav of a clip that is not already one: " + FFMPEG_HELP)
    return lines


def _ffmpeg(data: bytes, suffix: str, ffmpeg_arguments: list[str], out_suffix: str) -> bytes:
    """``data`` through ffmpeg; ``CToolMissing`` without it, ``banks.CUnplayable`` with ffmpeg's last line on failure."""
    if tools.find_tool("ffmpeg") is None:
        raise CToolMissing(FFMPEG_HELP)
    try:
        return tools.ffmpeg_bytes(data, ffmpeg_arguments, in_suffix=suffix, out_suffix=out_suffix)
    except tools.CToolError as exc:
        raise banks.CUnplayable(str(exc)) from exc


def to_wav(data: bytes, suffix: str) -> bytes:
    """A clip (``.wem`` or a plain file) as a WAV. ``CToolMissing`` or ``banks.CUnplayable`` say why not."""
    if suffix in (".ogg", ".mp3"):
        return _ffmpeg(data, suffix, [], ".wav")
    wave_fields = banks.wave_info(data)
    if suffix == ".wav" and wave_fields["format"] == "PCM":
        return data
    if suffix == ".wav":
        return _ffmpeg(data, suffix, [], ".wav")
    if wave_fields["format"] == "Wwise Vorbis":
        library, ffmpeg = wwvorbis.CLibrary.find(), tools.find_tool("ffmpeg")
        if library is None:
            raise CToolMissing(CODEBOOKS_HELP)
        if ffmpeg is None:
            raise CToolMissing(FFMPEG_HELP)
        pcm, channels, rate = wwvorbis.decode(data, library, ffmpeg)
        return riff.wav_bytes(pcm, rate, channels)
    return banks.playable(data, "x.wem")[0]


def to_ogg(data: bytes, suffix: str) -> bytes:
    """A clip as Ogg Vorbis: a Wwise Vorbis ``.wem`` rewrapped (codebook file), anything else through ffmpeg."""
    if suffix == ".ogg":
        return data
    if suffix == ".wem" and banks.wave_info(data)["format"] == "Wwise Vorbis":
        library = wwvorbis.CLibrary.find()
        if library is None:
            raise CToolMissing(CODEBOOKS_HELP)
        try:
            return wwvorbis.to_ogg(data, library)
        except wwvorbis.CWemError as exc:
            raise banks.CUnplayable(f"Wwise Vorbis: {exc}") from exc
    wav = data if suffix == ".wav" else to_wav(data, suffix)
    return _ffmpeg(wav, ".wav", ["-c:a", "libvorbis"], ".ogg")


def to_mp3(data: bytes, suffix: str) -> bytes:
    """A clip as MP3 (the form of the old games' music and speech): an MP3 as it is, anything else through ffmpeg."""
    if suffix == ".mp3":
        return data
    wav = data if suffix == ".wav" and banks.wave_info(data)["format"] == "PCM" else to_wav(data, suffix)
    return _ffmpeg(wav, ".wav", ["-c:a", "libmp3lame", "-q:a", "4"], ".mp3")


def mono_samples(data: bytes, suffix: str) -> tuple[list[int], int]:
    """A clip's samples mixed to one channel (signed 16 bit values) and its sample rate; ``CToolMissing`` or
    ``banks.CUnplayable`` when it cannot be decoded."""
    try:
        with wave.open(io.BytesIO(to_wav(data, suffix))) as wav_file:
            channels, rate, width = wav_file.getnchannels(), wav_file.getframerate(), wav_file.getsampwidth()
            raw = wav_file.readframes(wav_file.getnframes())
    except (wave.Error, EOFError) as exc:
        raise banks.CUnplayable(f"not a PCM WAV: {exc}") from exc
    if width != 2:
        raise banks.CUnplayable(f"{8 * width} bit samples: only 16 bit is read")
    values = list(array.array("h", raw[: len(raw) // 2 * 2]))
    if channels > 1:
        values = [sum(values[i : i + channels]) // channels for i in range(0, len(values) - channels + 1, channels)]
    return values, rate


def _convert(data: bytes, suffix: str, key: str) -> bytes:
    return to_wav(data, suffix) if key == "wav" else to_ogg(data, suffix)


def export_sound(job: SJob) -> SMade:
    """The WAV and Ogg forms of one clip."""
    made, suffix = SMade(), Path(job.path).suffix.lower()
    for key in (k for k in ("wav", "ogg") if k in job.formats):
        if suffix == "." + key:
            made.notes.append(f"already {key}: the stored file is the {key}")
            continue
        try:
            made.files[job.name("." + key)] = _convert(job.data, suffix, key)
        except CToolMissing as exc:
            made.notes.append(str(exc))
            made.status = "tool-missing"
        except (banks.CUnplayable, ValueError) as exc:
            made.notes.append(f"{key}: {exc}")
            made.status = "failed"
    return made


RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
)  # Windows device names: a file of that name opens the device


def _safe(name: str) -> str:
    """A bank's name for a sound or an event as one file or folder name."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .") or "unnamed"
    return f"_{clean}" if clean.split(".")[0].upper() in RESERVED else clean


def _info(job: SJob) -> banks.SBankInfo:
    read = lambda p: job.source.read(p).decode("utf-8", "replace")  # noqa: E731
    try:
        return banks.read_info(job.path, job.source.exists, read)
    except (OSError, ValueError, KeyError):
        return banks.SBankInfo()


class _CBank:
    """One bank being exported: its listing and info, the forms asked, the converted clips kept (a clip many events
    play is converted once), what went wrong, and the file names given so far (the bank's names are not unique)."""

    def __init__(self, job: SJob) -> None:
        self.job, self.listing, self.info = job, banks.bank_listing(job.data), _info(job)
        self.keys = [k for k in ("wav", "ogg") if k in job.formats]
        self.root = job.folder()
        self.cache: dict[tuple[int, str], bytes | None] = {}
        self.problems: dict[str, int] = {}
        self.missing_tool = False
        self._media_names: dict[int, str] = {}
        self._taken: dict[str, int] = {}

    def media(self, sound: banks.SBankSound) -> bytes | None:
        if sound.media in self.listing.media:
            return self.listing.embedded(sound.media)
        where = banks.media_file(self.job.path, sound.media, self.info, self.job.source.exists)
        return self.job.source.read(where) if where else None

    def name(self, sound: banks.SBankSound) -> str:
        """The file stem of a sound's media; two media of one name get their id added to it."""
        if sound.media not in self._media_names:
            named = self.info.media.get(sound.media)
            stem = _safe((named[0] if named else "") or str(sound.media))
            if self._taken.setdefault(stem.lower(), sound.media) != sound.media:
                stem = f"{stem}_{sound.media}"
            self._media_names[sound.media] = stem
        return self._media_names[sound.media]

    def problem(self, why: str) -> None:
        self.problems[why] = self.problems.get(why, 0) + 1

    def converted(self, sound: banks.SBankSound, key: str) -> bytes | None:
        if (sound.media, key) in self.cache:
            return self.cache[sound.media, key]
        out = None
        try:
            media = self.media(sound)
            if media is None:
                self.problem("media file not found")
            else:
                out = _convert(media, ".wem", key)
        except CToolMissing as exc:
            self.missing_tool = True
            self.problem(str(exc))
        except (banks.CUnplayable, ValueError, OSError) as exc:
            self.problem(str(exc))
        self.cache[sound.media, key] = out
        return out


def export_bank(job: SJob) -> SMade:
    """Every sound of a bank in the forms asked (wav, ogg), per event where the walk resolves them, and a listing."""
    made, bank = SMade(), _CBank(job)
    if bank.keys:
        _plain_sounds(bank, made)
        made.notes += _events(bank, made)
    if "json" in job.formats:
        made.files[f"{bank.root}.json"] = _listing_json(bank)
    if bank.problems:
        made.notes += [f"{count} sound(s): {why}" for why, count in bank.problems.items()]
        made.status = "tool-missing" if bank.missing_tool else "failed"
    return made


def _plain_sounds(bank: _CBank, made: SMade) -> None:
    """Each sound's stored clip and its converted forms under ``<bank>/media/``."""
    for sound in bank.listing.sounds:
        if sound.codec == "silence":
            continue
        name = bank.name(sound)
        raw = bank.media(sound)
        if raw is not None:
            made.files[f"{bank.root}/media/{name}.wem"] = raw
        for key in bank.keys:
            if (body := bank.converted(sound, key)) is not None:
                made.files[f"{bank.root}/media/{name}.{key}"] = body


def _walked(walk: banks.SEventWalk) -> list[banks.SBankSound]:
    """An event's sounds as ``SBankSound``s: a music track's media ids (plain numbers in the walk) get a stand-in."""
    found = [sound if not isinstance(sound, int) else banks.SBankSound(0, "music", "", sound) for sound in walk.sounds]
    return [sound for sound in found if sound.codec != "silence"]


def _events(bank: _CBank, made: SMade) -> list[str]:
    """The files of each event's sounds under ``<bank>/events/<event>/``; the notes (how many, whether exact)."""
    written, inexact, folders = 0, 0, {}
    for event in bank.listing.events[:EVENT_LIMIT]:
        walk = banks.walk_event(bank.listing, event)
        sounds = _walked(walk)
        if not sounds:
            continue
        inexact += not walk.exact
        stem = _safe(bank.info.events.get(event, str(event)))
        if folders.setdefault(stem.lower(), event) != event:  # two events of one name
            stem = f"{stem}_{event}"
        for number, sound in enumerate(sounds):
            for key in bank.keys:
                if (body := bank.converted(sound, key)) is not None:
                    made.files[f"{bank.root}/events/{stem}/{number:02d}_{bank.name(sound)}.{key}"] = body
                    written += 1
    notes = [f"{written} event file(s) written under {bank.root}/events/"] if written else []
    if inexact:
        notes.append(f"{inexact} event(s) were walked by a guess (the bank's containers are not read exactly)")
    return notes


def _listing_json(bank: _CBank) -> bytes:
    listing, bank_data = bank.listing, bank.info
    events = []
    for event in listing.events[:EVENT_LIMIT]:
        walk = banks.walk_event(listing, event)
        ids = [sound.media for sound in _walked(walk)]
        events.append({"id": event, "name": bank_data.events.get(event, ""), "media": ids, "exact": walk.exact})
    doc = {
        "version": listing.version,
        "bank_id": listing.bank_id,
        "name": bank_data.name,
        "objects": listing.objects,
        "sounds": [
            {
                "id": sound.id,
                "codec": sound.codec,
                "stream": sound.stream,
                "media": sound.media,
                "name": bank.name(sound),
            }
            for sound in listing.sounds
        ],
        "events": events,
        "music": [
            {
                "id": track.id,
                "sources": [list(x) for x in track.sources],
                "segment": track.segment,
                "seconds": track.seconds,
            }
            for track in banks.music_tracks(listing)
        ],
    }
    return (json.dumps(doc, indent=1) + "\n").encode("utf-8")
