"""A clip from a WAV or an Ogg file made into the audio of a Sound: read, then encoded in the bank's codec.

``read_clip(data, name)`` gives 16-bit samples (a WAV of 8, 16, 24 or 32 bits or floats is read here; Ogg, MP3 and
FLAC through ffmpeg, else miniaudio, whichever is installed); ``encode(clip, codec, version)`` writes the ``.wem`` of
``codec`` (``pcm``, ``adpcm`` or ``vorbis``); ``availability(version)`` says per codec why it cannot be written.
What each codec needs and what was verified is in ``docs/sound-editor.md`` ("Audio").
"""

from __future__ import annotations

import array
import math
import struct
from dataclasses import dataclass

from ..exporting import banks
from ..formats import bnk, bnkgraph, riff, wemadpcm, wwvorbis
from ..infra import tools
from ..infra.i18n import tr
from ..infra.tools import find_tool

ADPCM_PLUGIN = 0x00020001
CODECS = {"pcm": bnk.PCM_PLUGIN, "adpcm": ADPCM_PLUGIN, "vorbis": bnk.VORBIS_PLUGIN}
MAX_CHANNELS = 2


class ClipError(ValueError):
    """A clip that cannot be read, or cannot be written in the asked codec (the sentence says why)."""


class _NotReadHere(ClipError):
    """A WAV whose layout or codec this reader does not read; a decoder (ffmpeg, miniaudio) may."""


@dataclass
class Clip:
    """Interleaved 16-bit samples."""

    samples: array.array
    rate: int
    channels: int

    @property
    def seconds(self) -> float:
        return len(self.samples) / max(self.channels, 1) / max(self.rate, 1)


def availability(version: int) -> dict[str, str]:
    """Per codec: "" when ``encode`` can write it for a bank of ``version``, else what is missing."""
    out = {"pcm": "", "adpcm": ""}
    if not bnkgraph.layout(version).vorbis_known:
        out["vorbis"] = tr("the Wwise Vorbis layout of this bank version is not known")
    elif find_tool("ffmpeg") is None:
        out["vorbis"] = tr("ffmpeg is not installed (put it on the PATH or into the tools folder)")
    elif wwvorbis.Library.find() is None:
        out["vorbis"] = tr("{file} is not in the tools folder", file=wwvorbis.LIBRARY_FILE)
    else:
        out["vorbis"] = ""
    return out


# reading


def read_clip(data: bytes, name: str = "") -> Clip:
    """The samples of a WAV, a PCM ``.wem`` or (with ffmpeg or miniaudio) any other audio file."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            return _read_wav(data)
        except ClipError as exc:
            # a Wwise file of another codec is not a clip; a WAV's channel count or NaN samples are final
            if not (name.lower().endswith(".wav") and isinstance(exc, _NotReadHere)):
                raise
            return _decoded(data, name)
    return _decoded(data, name)


def read_wem(wem: bytes) -> Clip:
    """The samples of a ``.wem`` of any codec the editor writes (PCM, Wwise ADPCM, Wwise Vorbis), decoded as the Play
    button decodes it; a ``ClipError`` says what is missing (the codebook file, ffmpeg or miniaudio)."""
    try:
        data, suffix = banks.playable(wem, "x.wem")
    except (banks.Unplayable, ValueError, struct.error) as exc:
        raise ClipError(str(exc)) from exc
    return read_clip(data, "x" + suffix)


def _wav_chunks(data: bytes) -> tuple[bytes, bytes]:
    found = riff.chunks(data)
    fmt, body = found.get(b"fmt ", b""), found.get(b"data", b"")
    if len(fmt) < 16 or not body:
        raise _NotReadHere(tr("the WAV has no format or no data chunk"))
    return fmt, body


def _read_wav(data: bytes) -> Clip:
    fmt, body = _wav_chunks(data)
    tag, channels, rate, _avg, _align, bits = struct.unpack_from("<HHIIHH", fmt)
    if tag == 0xFFFE and len(fmt) >= 26:  # extensible: the real tag opens the sub-format
        tag = struct.unpack_from("<H", fmt, 24)[0]
    if not 1 <= channels <= MAX_CHANNELS:
        raise ClipError(tr("{channels} channels: only mono and stereo clips are used", channels=channels))
    return Clip(_to_16(body, tag, bits, channels), rate, channels)


def _to_16(body: bytes, tag: int, bits: int, channels: int = 1) -> array.array:
    width = bits // 8
    frame = width * channels  # a cut last frame would leave a channel short
    body = body[: len(body) // frame * frame] if frame else body
    out = array.array("h")
    if tag == 1 and bits == 16:
        out.frombytes(body)
    elif tag == 1 and bits == 8:
        out.extend((b - 128) << 8 for b in body)
    elif tag == 1 and bits in (24, 32):
        top = bytearray(len(body) // width * 2)  # the upper 16 bits of each sample
        top[0::2], top[1::2] = body[width - 2 :: width], body[width - 1 :: width]
        out.frombytes(bytes(top))
    elif tag == 3 and bits in (32, 64):
        floats = array.array("f" if bits == 32 else "d")
        floats.frombytes(body)
        if not all(map(math.isfinite, floats)):
            raise ClipError(tr("the WAV holds samples that are not numbers (NaN or infinity)"))
        out.extend(max(-32768, min(32767, round(v * 32767))) for v in floats)
    else:
        raise _NotReadHere(
            tr("a WAV of format {tag} with {bits} bits is not read (PCM and float WAVs are)", tag=tag, bits=bits)
        )
    return out


def _decoded(data: bytes, name: str) -> Clip:
    """Any other audio file: ffmpeg writes it as a WAV, else miniaudio decodes it (stereo, 48 kHz)."""
    if find_tool("ffmpeg") is not None:
        try:
            return _read_wav(tools.ffmpeg_bytes(data, ["-c:a", "pcm_s16le"]))
        except tools.ToolError as exc:
            raise ClipError(tr("ffmpeg cannot read the file: {error}", error=str(exc)[:200])) from exc
    try:
        import miniaudio
    except ImportError:
        raise ClipError(
            tr("{value} is not a WAV: install ffmpeg or miniaudio to read other formats", value=name or "the file")
        ) from None
    try:
        decoded = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=2, sample_rate=48000)
    except miniaudio.DecodeError as exc:
        raise ClipError(tr("miniaudio cannot read the file: {exc}", exc=exc)) from exc
    return Clip(array.array("h", decoded.samples), 48000, 2)


# encoding


def encode(clip: Clip, codec: str, version: int) -> bytes:
    """The ``.wem`` of a clip in ``codec`` for a bank of ``version``; a ``ClipError`` says what is missing."""
    if codec not in CODECS:
        raise ClipError(tr("no codec {codec}: {value}", codec=repr(codec), value=", ".join(CODECS)))
    missing = availability(version).get(codec, "")
    if missing:
        raise ClipError(tr("{codec} cannot be written: {missing}", codec=codec, missing=missing))
    if codec == "pcm":
        return bnk.pcm_wem(clip.samples, clip.rate, clip.channels)
    if codec == "adpcm":
        return adpcm_wem(clip)
    library, ffmpeg = wwvorbis.Library.find(), find_tool("ffmpeg")
    try:
        config = bnkgraph.layout(version).vorbis_channels
        return wwvorbis.encode(clip.samples.tobytes(), clip.channels, clip.rate, library, ffmpeg, config=config)
    except wwvorbis.WemError as exc:
        raise ClipError(tr("Wwise Vorbis: {exc}", exc=exc)) from exc


def adpcm_wem(clip: Clip) -> bytes:
    """A Wwise ADPCM ``.wem`` of a clip (``formats/wemadpcm``)."""
    return wemadpcm.write(clip.samples, clip.rate, clip.channels)
