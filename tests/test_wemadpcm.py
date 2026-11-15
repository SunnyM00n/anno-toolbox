"""``formats.wemadpcm``: IMA ADPCM in Wwise's block layout, written and read back."""

import array
import math

import pytest

from annotoolbox.formats import riff, wemadpcm

np = pytest.importorskip("numpy")


def _wave(frames: int, channels: int) -> array.array:
    samples = array.array("h")
    for i in range(frames):
        for c in range(channels):
            samples.append(round(3000 * math.sin(i / 40 + c)))
    return samples


@pytest.mark.parametrize("channels", [1, 2])
def test_written_audio_reads_back_close_to_what_it_was(channels: int):
    wave = _wave(200, channels)
    decoded, rate, got = wemadpcm.read(wemadpcm.write(wave, 24000, channels))
    assert (rate, got) == (24000, channels)
    assert len(decoded) >= len(wave)  # the last block is filled up
    warm = 100 * channels  # the step size needs a few samples to grow to the signal
    assert np.abs(decoded[warm : len(wave)].astype(int) - np.asarray(wave, int)[warm:]).max() < 300


def test_the_blocks_are_36_bytes_a_channel_and_the_format_says_so():
    data = riff.chunks(wemadpcm.write(_wave(130, 2), 48000, 2))
    assert len(data[b"data"]) == 3 * 36 * 2  # 130 frames: three blocks of 64
    assert data[b"fmt "][12:14] == (72).to_bytes(2, "little")


def test_a_file_without_a_format_is_refused():
    with pytest.raises(ValueError, match="no format"):
        wemadpcm.read(riff.build([(b"data", bytes(36))]))
