"""wwvorbis: a synthetic tone encoded as Wwise Vorbis and parsed back; skipped without ffmpeg (libvorbis) or the codebooks."""

import array
import math
import struct
import subprocess

import pytest

from annotoolbox.formats import bnkgraph, wwvorbis
from annotoolbox.infra.tools import find_tool

FFMPEG = find_tool("ffmpeg")
LIBRARY = wwvorbis.Library.find()
CONFIG = bnkgraph.LAYOUTS[bnkgraph.NEW].vorbis_channels


def has_libvorbis() -> bool:
    if FFMPEG is None:
        return False
    done = subprocess.run([str(FFMPEG), "-hide_banner", "-encoders"], capture_output=True, check=False)
    return b"libvorbis" in done.stdout


pytestmark = pytest.mark.skipif(
    not (has_libvorbis() and LIBRARY), reason="needs ffmpeg with libvorbis and packed_codebooks_aoTuV_603.bin"
)


def tone(seconds: float, rate: int, channels: int) -> bytes:
    n = int(seconds * rate)
    samples = array.array("h")
    for i in range(n):
        v = int(9000 * math.sin(i * 2 * math.pi * 440 / rate) + 3000 * math.sin(i * 2 * math.pi * 1250 / rate))
        samples.extend([v] * channels)
    return samples.tobytes()


def rms(pcm: bytes) -> float:
    s = array.array("h", pcm)
    return math.sqrt(sum(v * v for v in s) / max(len(s), 1))


@pytest.mark.parametrize("channels, rate", [(1, 44100), (1, 22050), (2, 48000)])
def test_a_tone_becomes_a_wwise_vorbis_file_that_decodes_to_the_same_tone(channels, rate):
    pcm = tone(0.6, rate, channels)
    wem = wwvorbis.encode(pcm, channels, rate, LIBRARY, FFMPEG, CONFIG)
    assert wem[:4] == b"RIFF" and wem[8:16] == b"WAVEfmt "
    fmt_size = struct.unpack_from("<I", wem, 16)[0]
    tag, ch, hz = struct.unpack_from("<HHI", wem, 20)
    assert (fmt_size, tag, ch, hz) == (0x42, wwvorbis.FMT_TAG, channels, wwvorbis.RATE)  # everything is 48 kHz
    parsed = wwvorbis.parse_wem(wem)
    assert (parsed.channels, parsed.rate) == (channels, wwvorbis.RATE)
    assert abs(parsed.samples - 0.6 * wwvorbis.RATE) < 1200  # the length, to the encoder's padding
    assert parsed.packets and len(wem) < len(pcm) // 4  # the point of it: much smaller than PCM
    vorb = wem[44:]  # fmt chunk data at 20, the vorb block 24 bytes in
    samples, setup_size, data_size = struct.unpack_from("<3I", vorb, 0)
    assert samples == parsed.samples and setup_size < data_size
    assert data_size == len(wem) - 20 - fmt_size - 8 - (data_size & 1)  # the data chunk holds setup and packets

    # the setup packet names its codebooks by library id and writes back to the same bytes
    setup = wwvorbis.parse_wwise_setup(parsed.setup, channels, LIBRARY)
    assert wwvorbis.write_wwise_setup(setup, LIBRARY) == parsed.setup

    decoded, ch, hz = wwvorbis.decode(wem, LIBRARY, FFMPEG)
    assert (ch, hz) == (channels, wwvorbis.RATE)
    assert abs(len(decoded) // (2 * channels) - parsed.samples) < 1024  # to a block: the Ogg way back has no trim
    assert 0.75 < rms(decoded) / rms(pcm) < 1.25  # the same tone, lossy


def test_unsupported_channel_counts_are_refused():
    with pytest.raises(wwvorbis.WemError, match="channels"):
        wwvorbis.encode(bytes(8), 4, 48000, LIBRARY, FFMPEG, CONFIG)


def test_a_file_that_is_not_wwise_vorbis_is_refused():
    with pytest.raises(wwvorbis.WemError):
        wwvorbis.parse_wem(b"not a riff file at all")


def test_a_file_that_is_no_riff_says_so_from_both_readers():
    for read in (wwvorbis.parse_wem, lambda data: wwvorbis.prefetch_size(data, 100)):
        with pytest.raises(wwvorbis.WemError, match="not a RIFF/WAVE file"):
            read(b"OggS" + bytes(40))
